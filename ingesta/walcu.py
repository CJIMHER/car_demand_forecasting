"""Carga de Walcu agregada por mes, origen, campaña y tipo (make walcu-cargar FICHERO=/ruta.csv).

El fichero lo produce en el Mac scripts/walcu_agregar.py a partir de la exportación de leads de Walcu. Ya
viene agregado: ni nombres, ni teléfonos, ni correos, ni identificadores de clientes. Aun así, aquí se
comprueba antes de guardar nada y el fichero se rechaza ENTERO (no se carga ninguna fila) si:
- sus columnas no son exactamente COLUMNAS (falta alguna, sobra alguna o se repite);
- algún valor contiene «@», 9 dígitos seguidos (o un teléfono con espacios), o algo con forma de DNI o NIE;
- algún valor no tiene el formato esperado (mes AAAA-MM, tipo VN o VO, recuentos enteros no negativos,
  campaña y origen no vacíos) o se repite la clave (mes, origen, campaña, tipo).
Los mensajes dicen fila y columna, nunca el valor: un valor rechazado podría ser un dato personal.

Que leads_cercana + leads_fuera + leads_sin_zona no sume leads, o que haya más citas que leads, no rechaza el
fichero (pasa en la exportación de Walcu): se avisa con el número de filas.

Se guarda en walcu_campana_mes (solo añadir) con fecha_carga, run_id y el nombre del fichero. Recargar un
mes lo sustituye: walcu_campana_mes_vigente da, para cada mes, solo la última carga que lo trae. El mismo
fichero (misma huella) dos veces no se vuelve a cargar. Antes de escribir en Postgres, el CSV va
comprimido al bucket (walcu/agregado/<día>/). Después se rehacen el cruce con Meta y las referencias
(ingesta/referencias_meta.py).

Uso (en el servidor, el fichero llega por la entrada estándar):
  python -m ingesta.walcu cargar --nombre walcu_agregado.csv < walcu_agregado.csv
  python -m ingesta.walcu comprobar --nombre … < …        solo valida, no carga
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import logging
import re
import sys
import tempfile
import gzip
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg

from ingesta import conectores as cx

LOG = logging.getLogger("ingesta.walcu")
COLUMNAS = ("mes", "origen", "campana", "tipo", "leads", "leads_cercana", "leads_fuera", "leads_sin_zona", "citas",
            "ventas", "num_inexistente")
TEXTO = ("mes", "origen", "campana", "tipo")
NUMEROS = COLUMNAS[4:]
TIPOS = ("VN", "VO")
MES = re.compile(r"(20\d\d)-(0[1-9]|1[0-2])")
# Datos personales: cualquiera rechaza el fichero entero
PERSONALES = (
    ("un correo (@)", re.compile(r"@")),
    ("un teléfono (9 dígitos seguidos)", re.compile(r"\d{9}")),
    ("un teléfono", re.compile(r"(?<!\d)(\+34[\s.-]?)?[6789]\d{2}[\s.-]\d{3}[\s.-]\d{3}(?!\d)|(?<!\d)[6789]\d{2}([\s.-]\d{2}){3}(?!\d)")),
    ("un DNI", re.compile(r"(?<![0-9A-Za-z])\d{8}[\s-]?[A-Za-z](?![0-9A-Za-z])")),
    ("un NIE", re.compile(r"(?<![0-9A-Za-z])[XYZxyz][\s-]?\d{7}[\s-]?[A-Za-z](?![0-9A-Za-z])")),
)
MAX_PROBLEMAS = 20


class ErrorWalcu(Exception):
    def __init__(self, problemas: list[str]):
        self.problemas = problemas
        mas = f" (y {len(problemas) - MAX_PROBLEMAS} más)" if len(problemas) > MAX_PROBLEMAS else ""
        super().__init__("Fichero rechazado, no se ha cargado nada:\n  - "
                         + "\n  - ".join(problemas[:MAX_PROBLEMAS]) + mas)


def personal(valor: str) -> str | None:
    """Qué dato personal parece el valor, o None."""
    for nombre, patron in PERSONALES:
        if patron.search(valor):
            return nombre
    return None


@dataclass
class Validado:
    filas: list[dict]
    avisos: list[str]


def validar(contenido: bytes) -> Validado:
    """Filas del CSV si todo cuadra; si no, ErrorWalcu con todos los problemas (sin los valores)."""
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ErrorWalcu(["el fichero no está en UTF-8"]) from None
    primera = texto.split("\n", 1)[0]
    separador = ";" if primera.count(";") > primera.count(",") else ","
    lector = csv.reader(io.StringIO(texto, newline=""), delimiter=separador)
    cabecera = [c.strip() for c in next(lector, [])]
    problemas = []
    if not cabecera or cabecera == [""]:
        raise ErrorWalcu(["el fichero está vacío"])
    repetidas = sorted({c for c in cabecera if cabecera.count(c) > 1})
    sobran = [c for c in cabecera if c not in COLUMNAS]
    faltan = [c for c in COLUMNAS if c not in cabecera]
    if sobran:
        # El nombre de una columna también podría ser un dato personal: se comprueba antes de citarlo
        problemas.append("columnas que no están en la lista cerrada: "
                         + ", ".join(f"«{c}»" if not personal(c) else "(una con aspecto de dato personal)"
                                     for c in sobran))
    if faltan:
        problemas.append("faltan columnas: " + ", ".join(faltan))
    if repetidas:
        problemas.append("columnas repetidas: " + ", ".join(repetidas))
    if problemas:
        problemas.append("columnas admitidas, exactamente: " + ", ".join(COLUMNAS))
        raise ErrorWalcu(problemas)
    filas, claves = [], {}
    zonas = citas_de_mas = 0
    for n, valores in enumerate(lector, 2):
        if not any(v.strip() for v in valores):
            continue
        if len(valores) != len(cabecera):
            problemas.append(f"fila {n}: tiene {len(valores)} valores y la cabecera {len(cabecera)}")
            continue
        crudo = dict(zip(cabecera, (v.strip() for v in valores)))
        for col, v in crudo.items():
            tipo_dato = personal(v)
            if tipo_dato:
                problemas.append(f"fila {n}, columna {col}: parece {tipo_dato}")
        f: dict = {}
        m = MES.fullmatch(crudo["mes"])
        if not m:
            problemas.append(f"fila {n}, columna mes: no es AAAA-MM")
        else:
            f["mes"] = date(int(m.group(1)), int(m.group(2)), 1)
        for col in ("origen", "campana"):
            if not crudo[col]:
                problemas.append(f"fila {n}, columna {col}: vacía")
            f[col] = crudo[col]
        if crudo["tipo"] not in TIPOS:
            problemas.append(f"fila {n}, columna tipo: debe ser VN o VO")
        f["tipo"] = crudo["tipo"]
        for col in NUMEROS:
            if not re.fullmatch(r"\d+", crudo[col]):
                problemas.append(f"fila {n}, columna {col}: no es un entero no negativo")
            else:
                f[col] = int(crudo[col])
        if len(f) != len(COLUMNAS):
            continue
        clave = (f["mes"], f["origen"], f["campana"], f["tipo"])
        if clave in claves:
            problemas.append(f"fila {n}: repite mes, origen, campaña y tipo de la fila {claves[clave]}")
            continue
        claves[clave] = n
        zonas += f["leads_cercana"] + f["leads_fuera"] + f["leads_sin_zona"] != f["leads"]
        citas_de_mas += f["citas"] > f["leads"] or f["ventas"] > f["citas"] or f["num_inexistente"] > f["leads"]
        filas.append(f)
    if problemas:
        raise ErrorWalcu(problemas)
    if not filas:
        raise ErrorWalcu(["el fichero no tiene filas"])
    avisos = []
    if zonas:
        avisos.append(f"{zonas} filas en que leads_cercana + leads_fuera + leads_sin_zona no suma leads")
    if citas_de_mas:
        avisos.append(f"{citas_de_mas} filas con más citas que leads, más ventas que citas o más números "
                      "inexistentes que leads")
    return Validado(filas, avisos)


def huella(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def subir_al_bucket(contenido: bytes, run_id: str, dia: date, subir=None) -> str:
    destino = f"walcu/agregado/{dia.isoformat()}"
    with tempfile.TemporaryDirectory(prefix="cdf_walcu_") as tmp:
        with gzip.open(Path(tmp) / f"{run_id}.csv.gz", "wb", compresslevel=6) as g:
            g.write(contenido)
        (subir or cx.subir_rclone)(Path(tmp), destino)
    return destino


def cargar_con(pg: psycopg.Connection, contenido: bytes, nombre: str, hoy: date, subir=None) -> dict:
    """Valida, sube al bucket, guarda y rehace cruce y referencias. Lanza ErrorWalcu si no vale."""
    v = validar(contenido)
    nombre = Path(nombre or "walcu.csv").name           # solo el nombre: la ruta del Mac no se guarda
    if personal(nombre):
        nombre = "walcu.csv"
    h = huella(contenido)
    previa = pg.execute("SELECT run_id, min(fecha_carga) FROM walcu_campana_mes WHERE huella_fichero = %s "
                        "GROUP BY run_id LIMIT 1", (h,)).fetchone()
    meses = sorted({f["mes"] for f in v.filas})
    resumen = {"fichero": nombre, "filas": len(v.filas), "desde": f"{meses[0]:%Y-%m}", "hasta": f"{meses[-1]:%Y-%m}",
               "avisos": v.avisos}
    if previa:
        return {**resumen, "ya_cargado": previa[0]}
    momento = cx.ahora()
    run_id = f"walcu-{momento:%Y%m%dT%H%M%S%f}-{h[:8]}"     # una carga, un run_id (la vista vigente lo usa)
    resumen["bucket"] = subir_al_bucket(contenido, run_id, hoy, subir)       # antes de escribir nada
    with pg.transaction():
        with pg.cursor() as cur:
            cur.executemany(
                f"INSERT INTO walcu_campana_mes (fecha_carga, run_id, fichero_origen, huella_fichero, {', '.join(COLUMNAS)}) "
                f"VALUES (%s, %s, %s, %s, {', '.join(['%s'] * len(COLUMNAS))})",
                [(momento, run_id, nombre, h, *(f[c] for c in COLUMNAS)) for f in v.filas])
    from ingesta import referencias_meta as ref
    resumen["run_id"] = run_id
    resumen["referencias"] = ref.actualizar(pg)
    return resumen


def main(argv=None, entrada=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("orden", choices=("cargar", "comprobar"))
    parser.add_argument("--nombre", default="walcu.csv", help="nombre del fichero (se guarda sin la ruta)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    contenido = (entrada or sys.stdin.buffer).read()
    try:
        if args.orden == "comprobar":
            v = validar(contenido)
            print(f"Fichero correcto: {len(v.filas)} filas." + "".join(f"\nAviso: {a}." for a in v.avisos))
            return 0
        from ingesta.db import conectar
        from ingesta.scrapers_vw import hoy_madrid
        with conectar(autocommit=True) as pg:
            resumen = cargar_con(pg, contenido, args.nombre, hoy_madrid())
            if "ya_cargado" in resumen:
                print(f"Ese fichero ya estaba cargado ({resumen['ya_cargado']}): no se carga otra vez.")
                return 0
            cx.anotar_tipo(pg, "walcu", hoy_madrid(), "carga", "ok",
                           {k: resumen[k] for k in ("fichero", "filas", "desde", "hasta", "run_id")})
    except ErrorWalcu as exc:
        print(exc, file=sys.stderr)
        return 2
    except cx.ErrorDescarga as exc:
        print(f"No se ha cargado nada: {exc}", file=sys.stderr)
        return 1
    print(f"Cargadas {resumen['filas']} filas de {resumen['desde']} a {resumen['hasta']} ({resumen['run_id']}).")
    for a in resumen["avisos"]:
        print(f"Aviso: {a}.")
    r = resumen["referencias"]
    print(f"Cruce con Meta: {r['campanas_mes']} filas de campaña y mes; sin cruce: {r['sin_cruce']}. "
          "Detalle: make referencias-meta")
    return 0


if __name__ == "__main__":
    sys.exit(main())
