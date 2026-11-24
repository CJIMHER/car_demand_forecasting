"""Campañas: tipo, ámbito y modelo de cada una, y unión de Meta, GA4 y Walcu por utm_campaign.

Las reglas están documentadas en config/campanas.yaml. Este módulo:
- clasifica cada campaña por su nombre (o por su asignación a mano, que manda): tipo modelo o
  general; ámbito VN, VO o ambos; el modelo, normalizado con el catálogo de config/versiones.yaml, y
  si ese modelo no está en el catálogo, «sin datos de mercado»;
- rehace panel_campana_dia (origen real) con las campañas de Meta clasificadas: es la tabla del
  contrato del panel de la que Marketing y los criterios de Meta leen el tipo y el ámbito, con los
  leads que cuenta Meta (leads_meta) para las reglas que no necesitan Walcu;
- rehace panel_campanas_sin_regla con las campañas de Meta y GA4 que no casan, con el motivo, para
  «Estado de las fuentes». De GA4 solo cuentan las filas de Meta (canal Meta, o nombre o ID de una
  campaña descargada de Meta): los criterios de campañas son solo para Meta;
- con config/canales.yaml (ingesta/canales.py), rehace el tráfico de GA4 por canal y tipo de stock
  (panel_trafico_canal_dia, real), las fuentes y medios que caen en «otros» (panel_trafico_otros) y
  las campañas de Google Ads, con su nombre y sin reglas (panel_campanas_google_ads);
- al final, rehace el cruce con Walcu y las referencias de coste (ingesta/referencias_meta.py).

Uso: python -m ingesta.campanas (make campanas) tras cambiar config/campanas.yaml. Lo hacen también
cada descarga de Meta y de GA4.
"""

from __future__ import annotations

import logging
import os
from collections import defaultdict
import re
import sys
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

RAIZ = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("ingesta.campanas")
TIPOS = ("modelo", "general")
AMBITOS = ("VN", "VO", "ambos")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "setiembre",
         "octubre", "noviembre", "diciembre")
ETIQUETA = re.compile(r"\[\s*(vn|vo)\s*\]")
ANIO = re.compile(r"\[\s*(20\d\d)\s*\]|(?<![0-9])(20\d\d)(?![0-9])")
# Sin etiqueta, el ámbito sale de estas palabras enteras: «ocasión», «ocasion» o «VO» → VO; «nuevos» o «VN» → VN
PALABRA_VO = re.compile(r"(?<![a-z0-9])(ocasion|vo)(?![a-z0-9])")
PALABRA_VN = re.compile(r"(?<![a-z0-9])(nuevos|vn)(?![a-z0-9])")
MES = re.compile(r"(?<![a-z])(" + "|".join(MESES) + r")(?![a-z])")


class ErrorCampanas(Exception):
    pass


def normalizar(nombre: str | None) -> str:
    """«T-Roc Ocasión · Noviembre» → «t_roc_ocasion_noviembre»."""
    texto = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


def plegar(texto: str | None) -> str:
    """Sin tildes, en minúsculas y con los espacios reducidos a uno (conserva corchetes y guiones)."""
    t = unicodedata.normalize("NFKD", str(texto or ""))
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    return re.sub(r"\s+", " ", t).strip()


def compacto(texto: str | None) -> str:
    """Solo letras y números: «T-Roc», «t roc» y «TROC» → «troc»."""
    return re.sub(r"[^a-z0-9]", "", plegar(texto))


def catalogo_modelos(fichero: Path | None = None) -> dict[str, str]:
    """{forma compacta: nombre del modelo} de config/versiones.yaml, con sus nombres «en_texto»."""
    ruta_v = fichero or Path(os.environ.get("CDF_VERSIONES", RAIZ / "config" / "versiones.yaml"))
    cfg = yaml.safe_load(ruta_v.read_text(encoding="utf-8")) or {}
    salida = {}
    for modelo, datos in (cfg.get("modelos") or {}).items():
        for forma in [str(modelo)] + [str(n) for n in (datos or {}).get("en_texto") or []]:
            salida.setdefault(compacto(forma), str(modelo))
    return salida


@dataclass(frozen=True)
class Clasificacion:
    """Tipo, ámbito y modelo de una campaña; sin tipo, no casa con nada y «motivo» dice por qué."""
    tipo: str | None = None              # modelo, general
    ambito: str | None = None            # VN, VO, ambos
    modelo: str | None = None
    sin_mercado: bool = False            # modelo fuera del catálogo de turismos que capturamos
    origen: str | None = None            # regla, asignacion
    motivo: str | None = None

    @property
    def ok(self) -> bool:
        return self.tipo is not None


@dataclass(frozen=True)
class Campanas:
    generales: tuple[str, ...]
    asignaciones: dict[str, Clasificacion]
    equivalencias: dict[str, str]
    meta_version: str
    catalogo: dict[str, str]

    def modelo(self, texto: str) -> tuple[str, bool]:
        """(modelo normalizado con el catálogo, sin datos de mercado)."""
        nombre = self.catalogo.get(compacto(texto))
        return (nombre, False) if nombre else (re.sub(r"\s+", " ", texto).strip(), True)

    def clasificar(self, nombre: str | None) -> Clasificacion:
        manual = self.asignaciones.get(normalizar(nombre))
        if manual:
            return manual
        original = re.sub(r"\s+", " ", str(nombre or "")).strip()
        texto = plegar(original)
        if not texto:
            return Clasificacion(motivo="sin nombre")
        etiquetas = set(ETIQUETA.findall(texto))
        if len(etiquetas) > 1:
            return Clasificacion(motivo="lleva las etiquetas [VN] y [VO] a la vez")
        if etiquetas:
            ambito = etiquetas.pop().upper()
        else:
            vo, vn = bool(PALABRA_VO.search(texto)), bool(PALABRA_VN.search(texto))
            ambito = "VO" if vo and not vn else "VN" if vn and not vo else "ambos"
        junto = compacto(texto)
        if any(g in junto for g in self.generales):
            return Clasificacion("general", ambito, origen="regla")
        anio = ANIO.search(texto)
        if anio is None:
            return Clasificacion(motivo="no es general (" + ", ".join(self.generales) + ") ni lleva año "
                                        "(2026) seguido del modelo y el mes")
        mes = MES.search(texto, anio.end())
        if mes is None:
            return Clasificacion(motivo="no es general (" + ", ".join(self.generales) + ") ni lleva el mes "
                                        "(enero … diciembre) después del año y el modelo")
        # El modelo, tal como está escrito: plegar() no cambia la longitud salvo letras raras («ß»)
        tramo = (original if len(original) == len(texto) else texto)[anio.end():mes.start()]
        tramo = re.sub(r"\[\s*v[no]\s*\]|[\[\]]", " ", tramo, flags=re.I)
        tramo = re.sub(r"(?<![^\W\d_])(ocasi[oó]n|nuevos|v[no])(?![^\W\d_])", " ", tramo, flags=re.I).strip(" -_·|:/")
        if not compacto(tramo):
            return Clasificacion(motivo="no hay modelo entre el año y el mes")
        modelo, sin_mercado = self.modelo(tramo)
        return Clasificacion("modelo", ambito, modelo, sin_mercado, "regla")

    def clave_union(self, utm_campaign: str | None) -> str:
        """Clave con que se unen un lead de Walcu (o una sesión de GA4) y una campaña de Meta."""
        n = normalizar(utm_campaign)
        return normalizar(self.equivalencias.get(n, n))


def ruta() -> Path:
    return Path(os.environ.get("CDF_CAMPANAS", RAIZ / "config" / "campanas.yaml"))


def cargar(fichero: Path | None = None, catalogo: dict[str, str] | None = None) -> Campanas:
    cfg = yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {}
    reglas = cfg.get("reglas") or {}
    if not isinstance(reglas, dict):
        raise ErrorCampanas("config/campanas.yaml: «reglas» lleva la lista «generales» (las reglas campaña a "
                            "campaña son ahora «asignaciones»)")
    generales = tuple(dict.fromkeys(compacto(g) for g in reglas.get("generales") or [] if compacto(g)))
    if not generales:
        raise ErrorCampanas("config/campanas.yaml: falta la lista «reglas.generales»")
    catalogo = catalogo_modelos() if catalogo is None else catalogo
    base = Campanas(generales, {}, {}, "", catalogo)
    asignaciones = {}
    for i, a in enumerate(cfg.get("asignaciones") or [], 1):
        donde = f"config/campanas.yaml, asignación {i}"
        if not isinstance(a, dict) or not normalizar(a.get("campana")):
            raise ErrorCampanas(f"{donde}: falta «campana»")
        if a.get("tipo") not in TIPOS:
            raise ErrorCampanas(f"{donde}: «tipo» debe ser modelo o general")
        if a.get("ambito") not in AMBITOS:
            raise ErrorCampanas(f"{donde}: «ambito» debe ser VN, VO o ambos")
        if (a["tipo"] == "modelo") != bool(a.get("modelo")):
            raise ErrorCampanas(f"{donde}: «modelo» va en las de tipo modelo y solo en ellas")
        clave = normalizar(a["campana"])
        if clave in asignaciones:
            raise ErrorCampanas(f"{donde}: la campaña «{a['campana']}» ya tiene asignación")
        modelo, sin_mercado = base.modelo(str(a["modelo"])) if a.get("modelo") else (None, False)
        asignaciones[clave] = Clasificacion(a["tipo"], a["ambito"], modelo, sin_mercado, "asignacion")
    equivalencias = {normalizar(k): normalizar(v) for k, v in (cfg.get("equivalencias") or {}).items()}
    return Campanas(generales, asignaciones, equivalencias, str((cfg.get("meta") or {}).get("version_api") or "v24.0"),
                    catalogo)


# --------------------------------------------------------------------------- tablas del panel

def _ampliar(acum: dict, clave, dia, *valores) -> None:
    """[primer día, último día, sumas…] por clave."""
    a = acum.get(clave)
    if a is None:
        acum[clave] = [dia, dia, *valores]
        return
    a[0], a[1] = min(a[0], dia), max(a[1], dia)
    for i, v in enumerate(valores, 2):
        a[i] += v


def actualizar(pg: psycopg.Connection, campanas: Campanas | None = None, canales=None) -> dict:
    """Rehace panel_campana_dia (real), panel_campanas_sin_regla y las tablas de tráfico de GA4.
    Devuelve cuántas campañas de Meta se han clasificado y cuántas (Meta y GA4) han quedado sin regla."""
    from ingesta import canales as cn     # aquí: canales.py importa este módulo
    campanas = campanas or cargar()
    canales = canales or cn.cargar()
    filas = pg.execute(
        """SELECT dia, campana_id, campana_nombre, gasto_cents, impresiones, clics, leads FROM campanas_meta_dia_vigente
           WHERE nivel = 'campana' ORDER BY campana_id, dia""").fetchall()
    por_campana: dict[str, list] = {}
    for f in filas:
        por_campana.setdefault(f[1], []).append(f)
    panel, sin_regla, por_clave, por_id, clasificadas = [], [], {}, {}, 0
    for cid, dias in por_campana.items():
        # El nombre vigente (el del último día con nombre) clasifica todos los días de la campaña
        nombre = next((d[2] for d in reversed(dias) if d[2]), None) or "(sin nombre)"
        c = campanas.clasificar(nombre)
        por_clave.setdefault(normalizar(nombre), c)
        por_id[str(cid)] = c
        if not c.ok:
            sin_regla.append(("meta", nombre, dias[0][0], dias[-1][0], sum(d[3] for d in dias), None, c.motivo))
            continue
        clasificadas += 1
        inicio = dias[0][0]
        panel += [(d[0], cid, nombre, c.tipo, c.modelo if c.tipo == "modelo" else None, c.ambito, c.sin_mercado,
                   inicio, d[3], d[4], d[5], d[6]) for d in dias]
    # GA4: canal y tipo de cada fila; de las de Meta, las campañas que se intentan clasificar
    trafico: dict[tuple, list] = defaultdict(lambda: [0, 0, 0.0])
    otros: dict[tuple, list] = {}
    google: dict[tuple, list] = {}
    de_meta: dict[str, list] = {}
    for dia, fuente, medio, camp, camp_id, canal_ga4, pagina, s, si, ek in pg.execute(
            """SELECT dia, fuente, medio, campana, campana_id, canal_ga4, pagina_destino, sesiones,
                      sesiones_interaccion, eventos_clave FROM ga4_trafico_dia_vigente"""):
        s, si, ek = int(s), int(si), float(ek)
        reservado = canales.reservado(camp)
        canal = canales.canal(fuente, medio, canal_ga4)
        # Una campaña descargada de Meta (por nombre o por ID) es de Meta aunque llegue con otra fuente
        if not reservado and (camp_id and str(camp_id) in por_id or campanas.clave_union(camp) in por_clave):
            canal = cn.META
        t = trafico[(dia, canal, canales.tipo(pagina) or "sin_tipo")]
        t[0], t[1], t[2] = t[0] + s, t[1] + si, t[2] + ek
        if canal == cn.OTROS:
            _ampliar(otros, (fuente, medio, canal_ga4), dia, s)
        elif reservado:
            continue
        elif canal == cn.META:
            _ampliar(de_meta, (camp, camp_id), dia, s)
        elif canal == "google_ads":
            _ampliar(google, (camp, camp_id), dia, s, si, ek)
    for (camp, camp_id), (p, u, s) in de_meta.items():
        c = (por_id.get(str(camp_id)) if camp_id else None) or por_clave.get(campanas.clave_union(camp)) \
            or campanas.clasificar(camp)
        if not c.ok:
            sin_regla.append(("ga4", camp, p, u, None, s, c.motivo))
    with pg.transaction():
        pg.execute("DELETE FROM panel_campana_dia WHERE origen = 'real'")
        pg.execute("DELETE FROM panel_campanas_sin_regla")
        pg.execute("DELETE FROM panel_trafico_canal_dia WHERE origen = 'real'")
        pg.execute("DELETE FROM panel_trafico_otros")
        pg.execute("DELETE FROM panel_campanas_google_ads")
        with pg.cursor() as cur:
            cur.executemany(
                """INSERT INTO panel_campana_dia (origen, dia, campana_id, titulo, tipo_campana, modelo, ambito,
                   sin_mercado, inicio, gasto_cents, impresiones, clics, leads_meta)
                   VALUES ('real', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""", panel)
            cur.executemany("INSERT INTO panel_campanas_sin_regla (fuente, campana, primer_dia, ultimo_dia, "
                            "gasto_cents, sesiones, motivo) VALUES (%s, %s, %s, %s, %s, %s, %s)", sin_regla)
            cur.executemany("INSERT INTO panel_trafico_canal_dia (origen, dia, canal, tipo, sesiones, "
                            "sesiones_interaccion, eventos_clave) VALUES ('real', %s, %s, %s, %s, %s, %s)",
                            [(*k, *v) for k, v in trafico.items()])
            cur.executemany("INSERT INTO panel_trafico_otros (fuente, medio, canal_ga4, primer_dia, ultimo_dia, "
                            "sesiones) VALUES (%s, %s, %s, %s, %s, %s)", [(*k, *v) for k, v in otros.items()])
            cur.executemany("INSERT INTO panel_campanas_google_ads (campana, campana_id, primer_dia, ultimo_dia, "
                            "sesiones, sesiones_interaccion, eventos_clave) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                            [(*k, *v) for k, v in google.items()])
    # Cruce con Walcu y referencias de coste por año (ingesta/referencias_meta.py)
    from ingesta import referencias_meta as ref
    LOG.info("Referencias de Meta: %s", ref.actualizar(pg, campanas))
    return {"campanas_clasificadas": clasificadas, "campanas_sin_regla": len(sin_regla)}


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from ingesta import canales as cn
    from ingesta.db import conectar
    try:
        config, canales = cargar(), cn.cargar()
    except (ErrorCampanas, cn.ErrorCanales) as exc:
        LOG.error("%s", exc)
        return 1
    with conectar(autocommit=True) as pg:
        LOG.info("Campañas: %s", actualizar(pg, config, canales))
    return 0


if __name__ == "__main__":
    sys.exit(main())
