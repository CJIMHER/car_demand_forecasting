"""Google Trends: enlaces, comprobación, subida y carga de las descargas manuales.

Google Trends se descarga A MANO, una vez al mes, desde el perfil de Chrome del proyecto. Este
módulo nunca accede a Google (ni pytrends, ni navegadores automáticos, ni peticiones a
trends.google.com): automatiza todo lo demás.

1. enlaces (en el Mac; make gtrends-enlaces): un HTML local con un enlace por comparación, a partir de
   data/competidores.csv. Cada comparación: la consulta del modelo propio y hasta 4 de sus competidores
   (5 términos, el límite de Trends); con más competidores, varias comparaciones que repiten la
   consulta del modelo como ancla. Zona, periodo y categoría, de config/gtrends.yaml.
2. subir (en el Mac; make gtrends-subir CARPETA=…): lee los CSV descargados (multiTimeline.csv,
   multiTimeline (1).csv…), los identifica por los términos de su cabecera (no por el nombre) y
   comprueba categoría, zona, frecuencia semanal y unas 52 semanas. Si todo cuadra, sube los
   ficheros originales y un manifiesto (fecha de descarga, comparaciones y huellas) a
   /opt/cdf/entrada/gtrends/<fecha>/ en el servidor, con scp. No borra nada de la carpeta local.
3. cargar (en el servidor; make gtrends y el cron diario): importa la última entrega a gtrends_semana
   (hechos, solo añadir: repetir la carga de la misma entrega no duplica), sube los originales
   comprimidos al bucket (gtrends/raw/<fecha>/) y calcula la atención relativa de Marketing
   (panel_atencion_relativa, origen real).

«<1» en Trends se guarda como 0,5 con menor_que_1 = verdadero.
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import html
import json
import logging
import os
import re
import subprocess
import sys
import tempfile
import urllib.parse
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from fractions import Fraction
from pathlib import Path

import yaml

from ingesta.campanas import normalizar, plegar

RAIZ = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("ingesta.gtrends")
FUENTE = "gtrends"
URL = "https://trends.google.com/trends/explore"
DESTINO = "/opt/cdf/entrada/gtrends"
MANIFIESTO = "manifiesto.json"
CLAVE_SSH = Path("~/.ssh/cdf_hetzner")
MENOR_QUE_1 = 0.5
FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}$")


class ErrorGtrends(Exception):
    pass


# --------------------------------------------------------------------------- configuración

@dataclass(frozen=True)
class Config:
    geo: str
    zona: str
    periodo: str
    categoria: int
    categoria_nombres: tuple[str, ...]   # nombres equivalentes de la categoría; el primero es el que se muestra
    idioma: str
    frecuencia: str
    semanas_min: int
    semanas_max: int
    max_terminos: int
    semanas_atencion: int

    @property
    def categoria_nombre(self) -> str:
        return self.categoria_nombres[0]

    def es_categoria(self, nombre: str) -> bool:
        """La categoría del CSV es la del YAML: por su código si lo trae («47» o «… (47)») y, si no, por
        cualquiera de sus nombres equivalentes (Trends la ha llamado de más de una forma)."""
        codigo = re.fullmatch(r"(?:.*\()?\s*(\d+)\s*\)?", nombre.strip())
        if codigo:
            return int(codigo.group(1)) == self.categoria
        return plegar(nombre) in {plegar(n) for n in self.categoria_nombres}


def ruta_config() -> Path:
    return Path(os.environ.get("CDF_GTRENDS", RAIZ / "config" / "gtrends.yaml"))


def cargar_config(fichero: Path | None = None) -> Config:
    c = yaml.safe_load((fichero or ruta_config()).read_text(encoding="utf-8")) or {}
    try:
        nombres = c.get("categoria_nombres") or c["categoria_nombre"]
        nombres = tuple(str(n) for n in ([nombres] if isinstance(nombres, str) else nombres))
        if not nombres:
            raise ValueError("categoria_nombres vacío")
        cfg = Config(str(c["geo"]), str(c["zona"]), str(c["periodo"]), int(c["categoria"]),
                     nombres, str(c.get("idioma") or "es"), str(c["frecuencia"]),
                     int(c["semanas"]["minimo"]), int(c["semanas"]["maximo"]), int(c.get("max_terminos") or 5),
                     int((c.get("atencion") or {}).get("semanas") or 4))
    except (KeyError, TypeError, ValueError) as exc:
        raise ErrorGtrends(f"config/gtrends.yaml incompleto o no válido ({exc})") from None
    if cfg.max_terminos < 2:
        raise ErrorGtrends("config/gtrends.yaml: max_terminos debe ser al menos 2")
    return cfg


# --------------------------------------------------------------------------- comparaciones

@dataclass(frozen=True)
class Comparacion:
    id: str
    modelo: str
    consulta: str                       # la del modelo propio: el ancla
    competidores: tuple[str, ...]       # sus consultas

    @property
    def terminos(self) -> tuple[str, ...]:
        return (self.consulta, *self.competidores)

    def url(self, cfg: Config) -> str:
        parametros = [("cat", str(cfg.categoria)), ("date", cfg.periodo), ("geo", cfg.geo), ("hl", cfg.idioma),
                      ("q", ",".join(self.terminos))]
        return URL + "?" + "&".join(f"{k}={urllib.parse.quote(v, safe=',')}" for k, v in parametros)


def clave_termino(t: str) -> str:
    """Cómo se comparan los términos: sin mayúsculas, tildes ni espacios de más."""
    return plegar(t)


def comparaciones(cfg: Config, fichero: Path | None = None) -> list[Comparacion]:
    """Por modelo (en el orden de data/competidores.csv), su consulta y la de sus competidores, en grupos
    de max_terminos − 1 con la consulta del modelo como ancla en cada uno."""
    ruta = fichero or RAIZ / "data" / "competidores.csv"
    modelos: dict[str, tuple[str, list[str]]] = {}
    with ruta.open(encoding="utf-8", newline="") as f:
        for fila in csv.DictReader(f):
            modelo, consulta = (fila.get("modelo_vw") or "").strip(), (fila.get("consulta_vw") or "").strip()
            rival = (fila.get("consulta_competidor") or "").strip()
            if not modelo or not consulta or not rival:
                continue
            ancla, rivales = modelos.setdefault(modelo, (consulta, []))
            if clave_termino(consulta) != clave_termino(ancla):
                raise ErrorGtrends(f"data/competidores.csv: el modelo {modelo} tiene dos consultas («{ancla}» y «{consulta}»)")
            if clave_termino(rival) not in {clave_termino(r) for r in rivales} | {clave_termino(ancla)}:
                rivales.append(rival)
    salida, por = [], cfg.max_terminos - 1
    for modelo, (ancla, rivales) in modelos.items():
        grupos = [rivales[i:i + por] for i in range(0, len(rivales), por)]
        slug = normalizar(modelo).replace("_", "-")
        for i, grupo in enumerate(grupos, 1):
            salida.append(Comparacion(f"{slug}-{i}", modelo, ancla, tuple(grupo)))
    if len({c.id for c in salida}) != len(salida):
        raise ErrorGtrends("data/competidores.csv: dos modelos dan el mismo identificador de comparación")
    return salida


# --------------------------------------------------------------------------- enlaces (Mac)

def html_enlaces(comps: list[Comparacion], cfg: Config, hoy: date) -> str:
    """Página local: un enlace por comparación, con casilla de «descargada» (se guarda en el navegador)."""
    e = html.escape
    filas = "\n".join(
        f'<tr data-id="{e(c.id)}"><td><input type="checkbox" id="c-{e(c.id)}" aria-label="Descargada {e(c.id)}"></td>'
        f'<th scope="row"><label for="c-{e(c.id)}">{e(c.id)}</label></th><td>{e(c.modelo)}</td>'
        f'<td>{" · ".join(e(t) for t in c.terminos)}</td>'
        f'<td><a href="{e(c.url(cfg))}" target="_blank" rel="noopener noreferrer">Abrir en Google Trends</a></td></tr>'
        for c in comps)
    clave = "cdf-gtrends-" + hashlib.sha256("|".join(c.id + ":" + ",".join(c.terminos) for c in comps).encode()
                                            ).hexdigest()[:12]
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Google Trends: comparaciones para descargar</title>
<style>
body {{ font: 15px/1.45 system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; color: #1d1d1f; }}
table {{ border-collapse: collapse; width: 100%; }} th, td {{ text-align: left; padding: .4rem .6rem; border-bottom: 1px solid #ddd; }}
tr.hecha {{ color: #888; }} tr.hecha a {{ color: #888; }} .faltan {{ font-size: 1.2rem; font-weight: 600; }}
ol li {{ margin: .2rem 0; }} code {{ background: #f2f2f2; padding: 0 .2rem; }}
</style></head><body>
<h1>Google Trends: comparaciones para descargar</h1>
<p>Generada el {e(hoy.isoformat())} · {len(comps)} comparaciones · zona {e(cfg.zona)} ({e(cfg.geo)}),
periodo «{e(cfg.periodo)}», categoría «{e(cfg.categoria_nombre)}» ({cfg.categoria}).</p>
<p class="faltan" id="faltan" aria-live="polite"></p>
<ol>
<li>Abre esta página en el perfil de Chrome del proyecto, con la sesión de Google iniciada.</li>
<li>Para cada comparación: «Abrir en Google Trends», y en «Interés a lo largo del tiempo», el botón de
descarga (flecha hacia abajo). Guarda el CSV en una misma carpeta; el nombre da igual
(<code>multiTimeline.csv</code>, <code>multiTimeline (1).csv</code>…): se identifica por sus términos.</li>
<li>Marca la casilla de cada una descargada. Si Trends muestra otra zona, periodo o categoría, vuelve a abrir el enlace.</li>
<li>Al terminar: <code>make gtrends-subir CARPETA=&lt;carpeta&gt;</code>.</li>
</ol>
<table><thead><tr><th scope="col">Hecha</th><th scope="col">Comparación</th><th scope="col">Modelo</th>
<th scope="col">Términos</th><th scope="col">Enlace</th></tr></thead>
<tbody>
{filas}
</tbody></table>
<p><button type="button" id="limpiar">Desmarcar todas</button></p>
<script>
(function () {{
  var clave = {json.dumps(clave)}, total = {len(comps)};
  var hechas = {{}};
  try {{ hechas = JSON.parse(localStorage.getItem(clave) || "{{}}"); }} catch (e) {{ hechas = {{}}; }}
  function pintar() {{
    var n = 0;
    document.querySelectorAll("tr[data-id]").forEach(function (tr) {{
      var id = tr.dataset.id, caja = tr.querySelector("input");
      caja.checked = !!hechas[id]; tr.classList.toggle("hecha", caja.checked); if (caja.checked) n++;
    }});
    var faltan = total - n;
    document.getElementById("faltan").textContent = faltan === 0
      ? "Todas descargadas (" + total + "). Siguiente paso: make gtrends-subir CARPETA=<carpeta>."
      : "Faltan " + faltan + " de " + total + ".";
  }}
  document.querySelectorAll("tr[data-id] input").forEach(function (caja) {{
    caja.addEventListener("change", function () {{
      var id = caja.closest("tr").dataset.id;
      if (caja.checked) {{ hechas[id] = true; }} else {{ delete hechas[id]; }}
      localStorage.setItem(clave, JSON.stringify(hechas)); pintar();
    }});
  }});
  document.getElementById("limpiar").addEventListener("click", function () {{
    hechas = {{}}; localStorage.removeItem(clave); pintar();
  }});
  pintar();
}})();
</script>
</body></html>
"""


# --------------------------------------------------------------------------- lectura de un CSV de Trends

@dataclass
class Serie:
    """Un CSV de «Interés a lo largo del tiempo»."""
    categoria: str | None
    frecuencia: str
    terminos: list[str]                     # tal como vienen
    zonas: list[str]
    semanas: list[date]
    valores: dict[str, list[tuple[float, bool]]]   # término → [(valor, menor que 1)]


def leer_csv(datos: bytes) -> Serie:
    """Formato real de Trends: «Categoría: …», línea vacía, cabecera «Semana,término: (España),…» y
    una fila por semana. Error si no lo es."""
    try:
        texto = datos.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ErrorGtrends("no es un fichero de texto UTF-8") from None
    lineas = [l for l in texto.splitlines()]
    categoria = None
    i = 0
    while i < len(lineas) and not lineas[i].strip():
        i += 1
    if i < len(lineas) and ":" in lineas[i] and "," not in lineas[i]:
        etiqueta, _, valor = lineas[i].partition(":")
        if plegar(etiqueta) in ("categoria", "category"):
            categoria = valor.strip()
            i += 1
    while i < len(lineas) and not lineas[i].strip():
        i += 1
    filas = list(csv.reader(lineas[i:]))
    if not filas or len(filas[0]) < 2:
        raise ErrorGtrends("no tiene la cabecera de Google Trends («Semana,término: (zona),…»)")
    cabecera, cuerpo = filas[0], [f for f in filas[1:] if any(x.strip() for x in f)]
    terminos, zonas = [], []
    for col in cabecera[1:]:
        m = re.match(r"^(.*):\s*\((.*)\)\s*$", col.strip())
        if not m:
            raise ErrorGtrends(f"columna «{col}» sin el formato «término: (zona)»")
        terminos.append(m.group(1).strip())
        zonas.append(m.group(2).strip())
    semanas, valores = [], {t: [] for t in terminos}
    for f in cuerpo:
        if len(f) != len(cabecera):
            raise ErrorGtrends(f"fila con {len(f)} columnas en lugar de {len(cabecera)}")
        try:
            semanas.append(date.fromisoformat(f[0].strip()))
        except ValueError:
            raise ErrorGtrends(f"fecha no válida «{f[0]}»") from None
        for t, v in zip(terminos, f[1:]):
            v = v.strip()
            if v == "<1":
                valores[t].append((MENOR_QUE_1, True))
                continue
            try:
                n = float(v)
            except ValueError:
                raise ErrorGtrends(f"valor no válido «{v}» de «{t}» el {f[0]}") from None
            if not 0 <= n <= 100:
                raise ErrorGtrends(f"valor fuera de 0–100 «{v}» de «{t}» el {f[0]}")
            valores[t].append((n, False))
    return Serie(categoria, cabecera[0].strip(), terminos, zonas, semanas, valores)


def errores_serie(s: Serie, cfg: Config) -> list[str]:
    """Lo que no cuadra con config/gtrends.yaml: categoría, zona, frecuencia y número de semanas."""
    errores = []
    if s.categoria is None:
        errores.append(f"sin la línea «Categoría: {cfg.categoria_nombre}»: se ha descargado sin categoría")
    elif not cfg.es_categoria(s.categoria):
        errores.append(f"categoría «{s.categoria}» en lugar de "
                       + " o ".join(f"«{n}»" for n in cfg.categoria_nombres) + f" ({cfg.categoria})")
    otras = sorted({z for z in s.zonas if plegar(z) != plegar(cfg.zona)})
    if otras:
        errores.append(f"zona {', '.join(f'«{z}»' for z in otras)} en lugar de «{cfg.zona}»")
    if plegar(s.frecuencia) != plegar(cfg.frecuencia):
        errores.append(f"datos por «{s.frecuencia}» en lugar de por «{cfg.frecuencia}» (periodo distinto de «{cfg.periodo}»)")
    elif not cfg.semanas_min <= len(s.semanas) <= cfg.semanas_max:
        errores.append(f"{len(s.semanas)} semanas en lugar de unas 52 ({cfg.semanas_min}–{cfg.semanas_max})")
    elif any((b - a).days != 7 for a, b in zip(s.semanas, s.semanas[1:])):
        errores.append("semanas no consecutivas")
    return errores


# --------------------------------------------------------------------------- comprobación (Mac)

@dataclass
class Fichero:
    nombre: str
    datos: bytes
    huella: str
    serie: Serie


@dataclass
class Informe:
    asignados: dict[str, Fichero] = field(default_factory=dict)
    faltan: list[Comparacion] = field(default_factory=list)
    repetidas: dict[str, list[str]] = field(default_factory=dict)
    desconocidos: list[tuple[str, str]] = field(default_factory=list)
    errores: list[str] = field(default_factory=list)
    avisos: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errores and not self.faltan

    def texto(self, total: int) -> str:
        lineas = [f"Comparaciones con su CSV: {len(self.asignados)} de {total}."]
        if self.faltan:
            lineas.append(f"Faltan {len(self.faltan)}:")
            lineas += [f"  - {c.id} ({', '.join(c.terminos)})" for c in self.faltan]
        if self.errores:
            lineas.append("Errores:")
            lineas += [f"  - {e}" for e in self.errores]
        if self.desconocidos:
            lineas.append("Ficheros que no casan con ninguna comparación (no se suben):")
            lineas += [f"  - {n}: {m}" for n, m in self.desconocidos]
        if self.avisos:
            lineas.append("Avisos:")
            lineas += [f"  - {a}" for a in self.avisos]
        lineas.append("Todo cuadra." if self.ok else "No se sube nada: corrige lo anterior y vuelve a ejecutarlo.")
        return "\n".join(lineas)


def comprobar(carpeta: Path, comps: list[Comparacion], cfg: Config) -> Informe:
    """Asigna cada CSV de la carpeta a su comparación por los términos de su cabecera y lo comprueba."""
    if not carpeta.is_dir():
        raise ErrorGtrends(f"no existe la carpeta {carpeta}")
    por_terminos = {frozenset(clave_termino(t) for t in c.terminos): c for c in comps}
    inf = Informe()
    vistos: dict[str, list[Fichero]] = defaultdict(list)
    for ruta in sorted(carpeta.iterdir(), key=lambda p: p.name):
        if not ruta.is_file() or ruta.suffix.lower() != ".csv":
            continue
        datos = ruta.read_bytes()
        try:
            serie = leer_csv(datos)
        except ErrorGtrends as exc:
            inf.desconocidos.append((ruta.name, f"no es un CSV de Google Trends ({exc})"))
            continue
        comp = por_terminos.get(frozenset(clave_termino(t) for t in serie.terminos))
        if comp is None:
            inf.desconocidos.append((ruta.name, "sus términos (" + ", ".join(serie.terminos)
                                     + ") no son los de ninguna comparación"))
            continue
        fichero = Fichero(ruta.name, datos, hashlib.sha256(datos).hexdigest(), serie)
        vistos[comp.id].append(fichero)
        for e in errores_serie(serie, cfg):
            inf.errores.append(f"{comp.id} ({ruta.name}): {e}. Vuelve a descargar {comp.id}: {comp.url(cfg)}")
    for comp in comps:
        ficheros = vistos.get(comp.id, [])
        if not ficheros:
            inf.faltan.append(comp)
            continue
        if len(ficheros) > 1:
            nombres = [f.nombre for f in ficheros]
            inf.repetidas[comp.id] = nombres
            if len({f.huella for f in ficheros}) == 1:
                inf.avisos.append(f"{comp.id} está repetida con el mismo contenido ({', '.join(nombres)}): "
                                  f"se usa {nombres[0]}")
            else:
                inf.errores.append(f"{comp.id} está repetida con contenidos distintos ({', '.join(nombres)}): "
                                   "deja en la carpeta solo la buena, o vuelve a descargarla")
        inf.asignados[comp.id] = ficheros[0]
    return inf


def manifiesto(inf: Informe, comps: list[Comparacion], cfg: Config, fecha: date) -> dict:
    por_id = {c.id: c for c in comps}
    return {
        "fecha_descarga": fecha.isoformat(),
        "generado_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "geo": cfg.geo, "zona": cfg.zona, "periodo": cfg.periodo, "categoria": cfg.categoria,
        "categoria_nombre": cfg.categoria_nombre, "categoria_nombres": list(cfg.categoria_nombres),
        "comparaciones": [{
            "id": cid, "modelo": por_id[cid].modelo, "consulta": por_id[cid].consulta,
            "terminos": list(por_id[cid].terminos), "fichero": f"{cid}.csv", "fichero_original": f.nombre,
            "huella": f.huella, "semanas": len(f.serie.semanas),
            "primera": f.serie.semanas[0].isoformat(), "ultima": f.serie.semanas[-1].isoformat(),
        } for cid, f in sorted(inf.asignados.items())],
    }


def ip_servidor(ip: str | None, entorno=os.environ, fichero_env: Path | None = None) -> str:
    """La IP del parámetro o, si no, CDF_SERVIDOR_IP del entorno o del .env de desarrollo (del Mac)."""
    if ip:
        return ip
    if entorno.get("CDF_SERVIDOR_IP"):
        return entorno["CDF_SERVIDOR_IP"].strip()
    ruta = fichero_env or RAIZ / ".env"
    if ruta.is_file():
        for linea in ruta.read_text(encoding="utf-8").splitlines():
            clave, _, valor = linea.partition("=")
            if clave.strip() == "CDF_SERVIDOR_IP" and valor.strip():
                return valor.strip().strip('"').strip("'")
    raise ErrorGtrends("falta la IP del servidor: IP=<ip> en make, o CDF_SERVIDOR_IP en el .env del Mac")


def _ejecutar(orden: list[str]) -> None:
    r = subprocess.run(orden, capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise ErrorGtrends(f"«{' '.join(orden[:1])}» terminó con código {r.returncode}: {r.stderr.strip()[:300]}")


def subir(inf: Informe, comps: list[Comparacion], cfg: Config, fecha: date, ip: str, clave: Path = CLAVE_SSH,
          ejecutar=_ejecutar) -> str:
    """Sube los CSV originales (como <comparación>.csv) y el manifiesto a /opt/cdf/entrada/gtrends/<fecha>/.
    Primero a <fecha>.subiendo y después se renombra: la carga nunca ve una entrega a medias."""
    destino = f"{DESTINO}/{fecha.isoformat()}"
    remoto = f"cdf@{ip}"
    ssh = ["ssh", "-i", str(clave.expanduser()), "-o", "BatchMode=yes", remoto]
    with tempfile.TemporaryDirectory(prefix="cdf_gtrends_") as tmp:
        carpeta = Path(tmp)
        for cid, f in inf.asignados.items():
            (carpeta / f"{cid}.csv").write_bytes(f.datos)
        (carpeta / MANIFIESTO).write_text(json.dumps(manifiesto(inf, comps, cfg, fecha), ensure_ascii=False,
                                                     indent=2), encoding="utf-8")
        ejecutar(ssh + [f"test ! -e {destino} || {{ echo 'ya hay una entrega del {fecha.isoformat()} en el servidor' >&2; "
                        f"exit 1; }}; mkdir -p {destino}.subiendo"])
        ejecutar(["scp", "-i", str(clave.expanduser()), "-o", "BatchMode=yes", "-q",
                  *sorted(str(p) for p in carpeta.iterdir()), f"{remoto}:{destino}.subiendo/"])
        ejecutar(ssh + [f"mv {destino}.subiendo {destino}"])
    return destino


# --------------------------------------------------------------------------- carga (servidor)

def entrada() -> Path:
    return Path(os.environ.get("CDF_GTRENDS_ENTRADA", "/entrada/gtrends"))


def ultima_entrega(raiz: Path) -> Path | None:
    entregas = sorted(p for p in raiz.iterdir() if p.is_dir() and FECHA.match(p.name)) if raiz.is_dir() else []
    return entregas[-1] if entregas else None


def _subir_bucket(carpeta: Path, fecha: str, subir) -> str:
    """Los originales (y el manifiesto), comprimidos, a gtrends/raw/<fecha>/."""
    from ingesta import conectores as cx
    destino = f"{FUENTE}/raw/{fecha}"
    with tempfile.TemporaryDirectory(prefix="cdf_gtrends_") as tmp:
        for p in sorted(carpeta.iterdir()):
            if p.is_file():
                # mtime=0: el mismo original da siempre el mismo .gz
                with open(Path(tmp) / f"{p.name}.gz", "wb") as bruto, \
                        gzip.GzipFile(fileobj=bruto, mode="wb", compresslevel=6, mtime=0) as g:
                    g.write(p.read_bytes())
        (subir or cx.subir_rclone)(Path(tmp), destino)
    return destino


def cargar(pg, carpeta: Path, cfg: Config, subir=None) -> dict:
    """Importa una entrega a gtrends_entrega y gtrends_semana (sin duplicar) y rehace la atención relativa."""
    ruta_m = carpeta / MANIFIESTO
    if not ruta_m.is_file():
        raise ErrorGtrends(f"la entrega {carpeta.name} no tiene {MANIFIESTO}")
    man = json.loads(ruta_m.read_text(encoding="utf-8"))
    fecha = date.fromisoformat(man["fecha_descarga"])
    huella_m = hashlib.sha256(ruta_m.read_bytes()).hexdigest()
    ya = pg.execute("SELECT 1 FROM gtrends_entrega WHERE fecha_descarga = %s AND huella_manifiesto = %s",
                    (fecha, huella_m)).fetchone()
    if ya:   # ya cargada: no se sube ni se inserta otra vez; la atención se rehace (es determinista)
        return {"entrega": fecha.isoformat(), "comparaciones": len(man["comparaciones"]), "filas": 0,
                "filas_nuevas": 0, "brutos": None, "atencion_modelos": escribir_atencion(pg, fecha, cfg),
                "ya_cargada": True}
    if "categoria" in man and int(man["categoria"]) != cfg.categoria:
        raise ErrorGtrends(f"la entrega {carpeta.name} es de la categoría {man['categoria']}, no de la "
                           f"{cfg.categoria} de config/gtrends.yaml")
    for c in man["comparaciones"]:
        if hashlib.sha256((carpeta / c["fichero"]).read_bytes()).hexdigest() != c["huella"]:
            raise ErrorGtrends(f"{c['fichero']}: su huella no es la del manifiesto (fichero cambiado o incompleto)")
    # Los originales, al bucket antes de leerlos
    bucket = _subir_bucket(carpeta, fecha.isoformat(), subir)
    filas = []
    for c in man["comparaciones"]:
        serie = leer_csv((carpeta / c["fichero"]).read_bytes())
        errores = errores_serie(serie, cfg)
        if errores:
            raise ErrorGtrends(f"{c['fichero']}: {'; '.join(errores)}")
        ancla = clave_termino(c["consulta"])
        for t in serie.terminos:
            for semana, (valor, menor) in zip(serie.semanas, serie.valores[t]):
                filas.append((fecha, c["id"], c["modelo"], t, clave_termino(t) == ancla, semana, valor, menor,
                              c["huella"]))
    with pg.transaction():
        pg.execute("""INSERT INTO gtrends_entrega (fecha_descarga, huella_manifiesto, carpeta, manifiesto)
                      VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                   (fecha, huella_m, carpeta.name, json.dumps(man, ensure_ascii=False)))
        with pg.cursor() as cur:
            antes = pg.execute("SELECT count(*) FROM gtrends_semana WHERE fecha_descarga = %s", (fecha,)).fetchone()[0]
            cur.executemany(
                """INSERT INTO gtrends_semana (fecha_descarga, comparacion, modelo, termino, es_modelo, semana, valor,
                   menor_que_1, huella_fichero) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING""",
                filas)
            despues = pg.execute("SELECT count(*) FROM gtrends_semana WHERE fecha_descarga = %s", (fecha,)).fetchone()[0]
    n_atencion = escribir_atencion(pg, fecha, cfg)
    return {"entrega": fecha.isoformat(), "comparaciones": len(man["comparaciones"]), "filas": len(filas),
            "filas_nuevas": despues - antes, "brutos": bucket, "atencion_modelos": n_atencion, "ya_cargada": False}


# --------------------------------------------------------------------------- atención relativa

def atencion(pg, fecha: date, cfg: Config) -> list[dict]:
    """Por modelo: su cuota de búsquedas en cada comparación (su valor / la suma de los términos) en las
    últimas `semanas` semanas completas frente a las `semanas` anteriores; con varias comparaciones, la
    media de las cuotas (todas pesan igual). En puntos: 100 × (cuota reciente − cuota anterior)."""
    n = cfg.semanas_atencion
    datos: dict[tuple[str, str], dict[date, list]] = defaultdict(lambda: defaultdict(lambda: [Fraction(0), Fraction(0)]))
    rivales: dict[str, set[str]] = defaultdict(set)
    for comp, modelo, termino, es_modelo, semana, valor in pg.execute(
            """SELECT DISTINCT ON (comparacion, termino, semana) comparacion, modelo, termino, es_modelo, semana, valor
               FROM gtrends_semana WHERE fecha_descarga = %s ORDER BY comparacion, termino, semana, id DESC""", (fecha,)):
        # Solo semanas completas antes de la descarga (la de la descarga puede ir a medias)
        if semana + timedelta(days=7) > fecha:
            continue
        v = Fraction(str(valor))
        d = datos[(modelo, comp)][semana]
        d[1] += v
        if es_modelo:
            d[0] += v
        else:
            rivales[modelo].add(clave_termino(termino))
    cuotas: dict[str, list[tuple[Fraction, Fraction]]] = defaultdict(list)
    fin: dict[str, date] = {}
    for (modelo, _), semanas in datos.items():
        orden = sorted(semanas)
        if len(orden) < 2 * n:
            continue
        rec, ant = orden[-n:], orden[-2 * n:-n]
        tot_r, tot_a = sum(semanas[s][1] for s in rec), sum(semanas[s][1] for s in ant)
        if not tot_r or not tot_a:
            continue
        cuotas[modelo].append((sum(semanas[s][0] for s in rec) / tot_r, sum(semanas[s][0] for s in ant) / tot_a))
        fin[modelo] = max(fin.get(modelo, rec[-1]), rec[-1])
    filas = []
    for modelo, cs in sorted(cuotas.items()):
        if len(rivales[modelo]) < 2:
            continue
        rec = sum(c[0] for c in cs) / len(cs)
        ant = sum(c[1] for c in cs) / len(cs)
        filas.append({"modelo": modelo, "mes": fin[modelo], "relativo_puntos": float((rec - ant) * 100),
                      "n_competidores": len(rivales[modelo]), "entrega": fecha, "semana_fin": fin[modelo],
                      "cuota_pct": float(rec * 100), "cuota_anterior_pct": float(ant * 100),
                      "n_comparaciones": len(cs)})
    return filas


def escribir_atencion(pg, fecha: date, cfg: Config) -> int:
    filas = atencion(pg, fecha, cfg)
    cols = ("modelo", "mes", "relativo_puntos", "n_competidores", "entrega", "semana_fin", "cuota_pct",
            "cuota_anterior_pct", "n_comparaciones")
    with pg.transaction():
        pg.execute("DELETE FROM panel_atencion_relativa WHERE origen = 'real' AND dia = %s", (fecha,))
        with pg.cursor() as cur:
            cur.executemany(f"INSERT INTO panel_atencion_relativa (dia, origen, {', '.join(cols)}) VALUES "
                            f"(%s, 'real', {', '.join(['%s'] * len(cols))})", [(fecha, *(f[c] for c in cols)) for f in filas])
    return len(filas)


# --------------------------------------------------------------------------- órdenes

def orden_enlaces(salida: Path, hoy: date | None = None) -> int:
    cfg = cargar_config()
    comps = comparaciones(cfg)
    salida.parent.mkdir(parents=True, exist_ok=True)
    salida.write_text(html_enlaces(comps, cfg, hoy or date.today()), encoding="utf-8")
    print(f"{len(comps)} comparaciones en {salida}")
    return 0


def orden_subir(carpeta: Path, ip: str | None, fecha: date | None, solo_comprobar: bool, ejecutar=_ejecutar) -> int:
    cfg = cargar_config()
    comps = comparaciones(cfg)
    inf = comprobar(carpeta, comps, cfg)
    print(inf.texto(len(comps)))
    if not inf.ok:
        return 1
    if solo_comprobar:
        return 0
    destino = subir(inf, comps, cfg, fecha or date.today(), ip_servidor(ip), ejecutar=ejecutar)
    print(f"Subida a {destino}. En el servidor: make gtrends (o esperar a la carga automática de las 07:00).")
    return 0


def orden_cargar(subir_bucket=None) -> int:
    from ingesta import conectores as cx
    from ingesta.db import conectar
    from ingesta.scrapers_vw import hoy_madrid
    hoy = hoy_madrid()
    with conectar(autocommit=True) as pg:
        try:
            cfg = cargar_config()
            carpeta = ultima_entrega(entrada())
            if carpeta is None:
                LOG.info("Google Trends: ninguna entrega en %s", entrada())
                return 0
            resumen = cargar(pg, carpeta, cfg, subir_bucket)
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"[:500]
            cx.anotar(pg, FUENTE, hoy, "error", {"error": error})
            LOG.error("Carga de Google Trends fallida: %s", error)
            return 1
        if resumen["filas_nuevas"]:
            cx.anotar(pg, FUENTE, hoy, "ok", resumen)
        LOG.info("Google Trends: %s", resumen)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="orden", required=True)
    e = sub.add_parser("enlaces", help="HTML local con un enlace por comparación (Mac)")
    e.add_argument("--salida", type=Path, default=RAIZ / "salidas" / "gtrends_enlaces.html")
    s = sub.add_parser("subir", help="Comprueba los CSV de una carpeta y los sube al servidor (Mac)")
    s.add_argument("--carpeta", type=Path, required=True)
    s.add_argument("--ip")
    s.add_argument("--fecha", type=date.fromisoformat, help="fecha de la descarga (por defecto, hoy)")
    s.add_argument("--solo-comprobar", action="store_true")
    sub.add_parser("cargar", help="Importa la última entrega (servidor)")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        if args.orden == "enlaces":
            return orden_enlaces(args.salida)
        if args.orden == "subir":
            return orden_subir(args.carpeta.expanduser(), args.ip, args.fecha, args.solo_comprobar)
        return orden_cargar()
    except ErrorGtrends as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
