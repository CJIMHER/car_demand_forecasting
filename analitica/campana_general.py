"""Cuándo lanzar una campaña general, por separado para VN y VO (config/campana_general.yaml).

Tres reglas; cada una basta sola para recomendar lanzar y, si se cumplen varias, se dicen todas:
1. Caída de ventas: las de las últimas N semanas completas (por fecha de venta, walcu_ventas_semana:
   ingesta/walcu_ventas.py) son más de un X % inferiores a las de las N anteriores (y la diferencia llega a la
   diferencia mínima de coches). Sin las 2·N semanas cubiertas, o con ventas demasiado viejas, queda pendiente.
2. Subida del stock: el stock propio de hoy frente al de hace D días ha subido más de un Y %. Stock del CRM
   (panel_stock_vehiculo real) si cubre esos días; si no, nuestros anuncios en la web de la marca
   (panel_mercado_dia, toda España). Sin D días de datos, pendiente.
3. Temporada: el mes de hoy está en una temporada del tipo.

Escribe panel_campana_general (origen real) para el día del cálculo, dentro de make precalcular. Los textos (sin
umbrales) salen de la configuración; las cifras van en `cifras`, que solo enseña el rol técnico.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import date, timedelta
from fractions import Fraction
from pathlib import Path

import psycopg
import yaml
from psycopg.types.json import Jsonb

RAIZ = Path(__file__).resolve().parent.parent
TIPOS = ("VN", "VO")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")
UNA_SEMANA = timedelta(days=7)


class ErrorCampanaGeneral(Exception):
    pass


@dataclass(frozen=True)
class Temporada:
    meses: tuple[int, ...]
    nombre: str

    @property
    def texto_meses(self) -> str:
        return "-".join(MESES[m - 1] for m in self.meses)


@dataclass(frozen=True)
class Config:
    version: str
    semanas: int
    caida_pct: Fraction
    diferencia_minima: int
    retraso_max_semanas: int
    semanas_recientes_excluidas: int
    dias_stock: int
    subida_pct: Fraction
    temporadas: dict[str, tuple[Temporada, ...]]
    textos: dict[str, str]


def ruta() -> Path:
    return Path(os.environ.get("CDF_CAMPANA_GENERAL") or RAIZ / "config" / "campana_general.yaml")


def cargar(fichero: Path | None = None) -> Config:
    d = yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {}
    try:
        v, s = d["ventas"], d["stock"]
        temporadas = {}
        for tipo in TIPOS:
            lista = []
            for t in d["temporadas"].get(tipo) or []:
                meses = tuple(int(m) for m in t["meses"])
                if not meses or any(not 1 <= m <= 12 for m in meses):
                    raise ErrorCampanaGeneral(f"temporadas.{tipo}: meses de 1 a 12")
                lista.append(Temporada(meses, str(t["nombre"])))
            temporadas[tipo] = tuple(lista)
        textos = {k: str(x) for k, x in d["textos"].items()}
        faltan = {"conviene", "no_hace_falta", "ventas", "stock", "temporada", "proxima", "ventas_pendiente",
                  "stock_pendiente"} - set(textos)
        if faltan:
            raise ErrorCampanaGeneral("textos: faltan " + ", ".join(sorted(faltan)))
        cfg = Config(str(d["version"]), int(v["semanas"]), Fraction(str(v["caida_pct"])),
                     int(v.get("diferencia_minima") or 0), int(v.get("retraso_max_semanas", 4)),
                     int(v.get("semanas_recientes_excluidas") or 0), int(s["dias"]),
                     Fraction(str(s["subida_pct"])), temporadas, textos)
    except (KeyError, TypeError, ValueError) as exc:
        raise ErrorCampanaGeneral(f"config/campana_general.yaml incompleto: {type(exc).__name__} {exc}") from None
    if cfg.semanas < 1 or cfg.dias_stock < 1 or cfg.semanas_recientes_excluidas < 0:
        raise ErrorCampanaGeneral("config/campana_general.yaml: semanas y dias tienen que ser al menos 1")
    return cfg


def lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


# --------------------------------------------------------------------------- reglas

def regla_ventas(ventas: dict[tuple[date, str], int], cubiertas: set[date], tipo: str, hoy: date,
                 cfg: Config) -> dict:
    """{'estado': si|no|pendiente, 'cifras': …}. Ventanas de semanas completas que terminan
    semanas_recientes_excluidas semanas antes de la última semana completa (o en la última con datos, como mucho
    retraso_max_semanas antes de esa)."""
    ultima = lunes(hoy) - UNA_SEMANA * (1 + cfg.semanas_recientes_excluidas)
    con_datos = [s for s in cubiertas if s <= ultima]
    fin = max(con_datos) if con_datos else None
    n = cfg.semanas
    cifras: dict = {"semanas": n, "caida_pct_umbral": float(cfg.caida_pct), "diferencia_minima": cfg.diferencia_minima,
                    "semanas_recientes_excluidas": cfg.semanas_recientes_excluidas}
    if fin is None or fin < ultima - cfg.retraso_max_semanas * UNA_SEMANA:
        return {"estado": "pendiente", "cifras": {**cifras, "motivo": "sin ventas recientes",
                                                   "ultima_semana_con_datos": str(fin) if fin else None}}
    semanas = [fin - i * UNA_SEMANA for i in range(2 * n)]
    if any(s not in cubiertas for s in semanas):
        return {"estado": "pendiente", "cifras": {**cifras, "motivo": f"faltan semanas de las {2 * n}",
                                                   "hasta": str(fin)}}
    actual = sum(ventas.get((s, tipo), 0) for s in semanas[:n])
    anterior = sum(ventas.get((s, tipo), 0) for s in semanas[n:])
    cambio = Fraction(actual - anterior, anterior) * 100 if anterior else None
    cae = (anterior > 0 and cambio < -cfg.caida_pct and anterior - actual >= cfg.diferencia_minima)
    cifras.update(actual=actual, anterior=anterior, cambio_pct=None if cambio is None else round(float(cambio), 1),
                  actual_desde=str(semanas[n - 1]), actual_hasta=str(fin), anterior_desde=str(semanas[-1]),
                  anterior_hasta=str(semanas[n]))
    return {"estado": "si" if cae else "no", "cifras": cifras}


def regla_stock(serie: dict[date, int], fuente: str | None, hoy: date, cfg: Config) -> dict:
    """Stock de hoy (último día con dato hasta hoy) frente al de hace `dias` días (último día con dato hasta
    entonces)."""
    cifras: dict = {"dias": cfg.dias_stock, "subida_pct_umbral": float(cfg.subida_pct), "fuente": fuente}
    hasta_hoy = [d for d in serie if d <= hoy]
    if not hasta_hoy:
        return {"estado": "pendiente", "cifras": {**cifras, "motivo": "sin datos del stock"}}
    dia_hoy = max(hasta_hoy)
    objetivo = dia_hoy - timedelta(days=cfg.dias_stock)
    antes = [d for d in serie if d <= objetivo]
    if not antes:
        return {"estado": "pendiente", "cifras": {**cifras, "motivo": f"menos de {cfg.dias_stock} días de datos",
                                                   "primer_dia": str(min(serie))}}
    dia_antes = max(antes)
    ahora, previo = serie[dia_hoy], serie[dia_antes]
    cambio = Fraction(ahora - previo, previo) * 100 if previo else None
    sube = cambio is not None and cambio > cfg.subida_pct
    cifras.update(hoy=ahora, dia_hoy=str(dia_hoy), antes=previo, dia_antes=str(dia_antes),
                  cambio_pct=None if cambio is None else round(float(cambio), 1))
    return {"estado": "si" if sube else "no", "cifras": cifras}


def temporada(hoy: date, tipo: str, cfg: Config) -> Temporada | None:
    return next((t for t in cfg.temporadas.get(tipo, ()) if hoy.month in t.meses), None)


def proxima(hoy: date, tipo: str, cfg: Config) -> Temporada | None:
    """La próxima temporada que empieza después de este mes (sin contar la de ahora)."""
    ahora = temporada(hoy, tipo, cfg)
    for k in range(1, 13):
        m = (hoy.month - 1 + k) % 12 + 1
        for t in cfg.temporadas.get(tipo, ()):
            if t.meses[0] == m and t is not ahora:
                return t
    return None


def evaluar(tipo: str, hoy: date, ventas: dict, cubiertas: set[date], serie_stock: dict[date, int],
            fuente_stock: str | None, cfg: Config) -> dict:
    """Una fila de panel_campana_general (sin dia ni origen)."""
    tx = cfg.textos
    rv = regla_ventas(ventas, cubiertas, tipo, hoy, cfg)
    rs = regla_stock(serie_stock, fuente_stock, hoy, cfg)
    t = temporada(hoy, tipo, cfg)
    motivos, pendientes = [], []
    if rv["estado"] == "si":
        motivos.append({"regla": "ventas", "texto": tx["ventas"].format(semanas=cfg.semanas)})
    elif rv["estado"] == "pendiente":
        pendientes.append({"regla": "ventas", "texto": tx["ventas_pendiente"].format(semanas=2 * cfg.semanas)})
    if rs["estado"] == "si":
        motivos.append({"regla": "stock", "texto": tx["stock"].format(dias=cfg.dias_stock)})
    elif rs["estado"] == "pendiente":
        pendientes.append({"regla": "stock", "texto": tx["stock_pendiente"].format(dias=cfg.dias_stock)})
    if t is not None:
        motivos.append({"regla": "temporada", "texto": tx["temporada"].format(nombre=t.nombre, meses=t.texto_meses)})
    sig = proxima(hoy, tipo, cfg)
    return {"tipo": tipo, "conviene": bool(motivos), "motivos": motivos, "pendientes": pendientes,
            "proxima": sig.texto_meses if sig else None,
            "cifras": {"ventas": {"estado": rv["estado"], **rv["cifras"]},
                       "stock": {"estado": rs["estado"], **rs["cifras"]},
                       "temporada": {"estado": "si" if t else "no", "nombre": t.nombre if t else None,
                                     "meses": t.texto_meses if t else None}},
            "version": cfg.version}


# --------------------------------------------------------------------------- datos

def series_stock(pg: psycopg.Connection, hoy: date, dias: int) -> dict[str, tuple[str | None, dict[date, int]]]:
    """Por tipo: (fuente, día → stock propio). El del CRM si tiene un día de hace `dias` días o antes; si no, el de
    nuestros anuncios en la web (scrapers)."""
    salida = {}
    objetivo = hoy - timedelta(days=dias)
    for tipo in TIPOS:
        crm = {d: int(n) for d, n in pg.execute(
            """SELECT dia, count(*) FROM panel_stock_vehiculo WHERE origen = 'real' AND tipo = %s AND dia <= %s
               GROUP BY dia""", (tipo, hoy))}
        if crm and min(crm) <= objetivo:
            salida[tipo] = ("crm", crm)
            continue
        web = {d: int(n) for d, n in pg.execute(
            "SELECT dia, propios FROM panel_mercado_dia WHERE zona = 'espana' AND tipo = %s AND dia <= %s",
            (tipo, hoy))}
        salida[tipo] = ("web", web) if web else ("crm", crm) if crm else (None, {})
    return salida


def calcular(pg: psycopg.Connection, hoy: date, cfg: Config | None = None) -> list[dict]:
    from ingesta import walcu_ventas
    cfg = cfg or cargar()
    ventas, cubiertas = walcu_ventas.vigentes(pg)
    stock = series_stock(pg, hoy, cfg.dias_stock)
    return [evaluar(t, hoy, ventas, cubiertas, stock[t][1], stock[t][0], cfg) for t in TIPOS]


def actualizar(pg: psycopg.Connection, hoy: date, cfg: Config | None = None) -> dict:
    """Rehace las filas reales del día. Resumen: si conviene por tipo."""
    filas = calcular(pg, hoy, cfg)
    with pg.transaction():
        pg.execute("DELETE FROM panel_campana_general WHERE origen = 'real' AND dia = %s", (hoy,))
        with pg.cursor() as cur:
            cur.executemany(
                """INSERT INTO panel_campana_general (dia, origen, tipo, conviene, motivos, pendientes, proxima,
                                                     cifras, version)
                   VALUES (%s, 'real', %s, %s, %s, %s, %s, %s, %s)""",
                [(hoy, f["tipo"], f["conviene"], Jsonb(f["motivos"]), Jsonb(f["pendientes"]), f["proxima"],
                  Jsonb(f["cifras"]), f["version"]) for f in filas])
    return {f["tipo"]: ("conviene: " + ", ".join(m["regla"] for m in f["motivos"]) if f["conviene"]
                        else "no hace falta") for f in filas}
