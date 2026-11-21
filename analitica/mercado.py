"""Precálculo de la vista «Mercado» del panel (cron de cdf_jobs a las 09:10).

Lee las tablas de hechos (stock_externo_snapshot, eventos_externos, scraper_runs) y rellena las
tablas derivadas panel_mercado_dia, panel_mercado_modelo, panel_mercado_concesionario,
panel_mercado_capturas y, para el detalle de cada modelo, panel_mercado_segmento y
panel_mercado_sin_clasificar (analitica/segmentos.py). El panel solo lee estas tablas: nunca
calcula sobre las de hechos al servir una página. Recalcular un día sustituye ese día en las
derivadas; las tablas de hechos no se tocan.

Por concesionario (solo la provincia del concesionario propio): los concesionarios con anuncios ese
día, con el nombre que publica la web (el más reciente si un código ha tenido varios).

Reglas (cada una con su prueba en tests/test_mercado.py):

- Ausencias, nunca ventas. Una ausencia es baja solo si el scraper la ha marcado retirada (evento
  «removed», tras tres capturas seguidas sin verla).
- Un día cuenta para una fuente solo si tiene captura completa (pasada success en scraper_runs).
  Para VN, solo si la tienen vn y vn_otros.
- Alta observada: anuncio visto por primera vez el día D con captura completa de su fuente en D−1.
  Los de la primera captura de una fuente o los que aparecen tras un hueco no son altas.
- Altas y ausencias de hoy exigen captura de ayer; la variación a 7 días, captura de hace 7 días;
  altas y ausencias de 7 días, capturas en todos los días de D−7 a D. Si falta, NULL (el panel
  muestra «—» con el motivo), nunca un cero ni una cifra parcial.
- Ausencias de hoy: estaban ayer y hoy no. Ausencias de 7 días: vistos entre D−7 y D−1 y no
  publicados en D.
- Días publicado: mediana (inferior) de D − primer día visto de los publicados en D. Si algún
  anuncio en la mitad inferior (edad ≤ mediana) no tiene alta observada, su edad real puede ser
  mayor y la mediana es una cota inferior («≥ N»).
- Modelo: el del scraper sin espacios sobrantes ni la marca delante («T-Cross», no «<marca>
  T-Cross»), con la ortografía más frecuente entre las que solo cambian en mayúsculas.
- Precio mediano: de los publicados en D con precio (mediana estadística: media de los dos
  centrales si el número es par).

Uso:
  python -m analitica.mercado                 todo el histórico (make precalcular)
  python -m analitica.mercado --ultimos 8     los 8 últimos días hasta hoy (cron)
  python -m analitica.mercado --dia 2026-11-14
"""

from __future__ import annotations

import argparse
import bisect
import logging
import re
import statistics
import sys
import time
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb

from analitica import segmentos, versiones
from ingesta import aviso_copia, telegram
from ingesta.concesionario import Concesionario, cargar as cargar_concesionario
from ingesta.db import conectar
from ingesta.scrapers_vw import hoy_madrid

LOG = logging.getLogger("analitica.mercado")

TIPOS = {"VO": ("vo",), "VN": ("vn", "vn_otros")}
FUENTES = ("vo", "vn", "vn_otros")
ZONAS = ("provincia", "comunidad", "espana")
SIN_MODELO = "Sin modelo"
UN_DIA = timedelta(days=1)


@dataclass(frozen=True)
class Fila:
    provincia: str | None
    dealer_id: str | None
    es_propio: bool
    modelo: str
    precio_cents: int | None
    # Para los segmentos del detalle de un modelo (analitica/segmentos.py)
    version: str | None = None
    combustible: str | None = None
    potencia_cv: int | None = None
    matriculacion: date | None = None
    km: int | None = None
    color: str | None = None


Clave = tuple[str, str]  # (fuente, id_anuncio)


# --------------------------------------------------------------------------- utilidades

def normalizar_modelo(valor, marca: str | None = None) -> str:
    """Modelo tal como lo da el scraper, sin espacios sobrantes y sin la marca delante
    («<marca> T-Cross» → «T-Cross»; la marca sale de config/concesionario.yaml); vacío → «Sin modelo»."""
    if valor is None:
        return SIN_MODELO
    texto = re.sub(r"\s+", " ", str(valor)).strip()
    if marca:
        sin_marca = re.sub(rf"^{re.escape(marca.strip())}\s+", "", texto, flags=re.IGNORECASE).strip()
        texto = sin_marca or texto
    return texto or SIN_MODELO


def mediana_inferior(valores: list[int]) -> int:
    orden = sorted(valores)
    return orden[(len(orden) - 1) // 2]


def dias_publicado(edades: list[tuple[int, bool]]) -> tuple[int | None, bool]:
    """(mediana de días, es cota inferior). edades: (días, tiene alta observada)."""
    if not edades:
        return None, False
    med = mediana_inferior([e for e, _ in edades])
    cota = any(not observada and e <= med for e, observada in edades)
    return med, cota


def precio_mediano(precios: list[int]) -> int | None:
    return round(statistics.median(precios)) if precios else None


# --------------------------------------------------------------------------- lectura de hechos

class Hechos:
    """Lo que hace falta de las tablas de hechos, leído una vez por pasada (y los días, a demanda)."""

    def __init__(self, pg: psycopg.Connection, conc: Concesionario):
        self.pg = pg
        self.conc = conc
        self.completos: dict[str, set[date]] = {f: set() for f in FUENTES}
        self.runs: dict[tuple[str, date], tuple] = {}
        for fuente, dia, fin, observados in pg.execute(
                "SELECT fuente, snapshot_day, finished_at, observed_count FROM scraper_runs"):
            self.completos[fuente].add(dia)
            self.runs[(fuente, dia)] = (fin, observados)
        # Ortografía de cada modelo: la más frecuente entre las que solo cambian en mayúsculas.
        variantes: dict[str, Counter] = defaultdict(Counter)
        for modelo, n in pg.execute("SELECT modelo, count(*) FROM stock_externo_snapshot GROUP BY modelo"):
            m = normalizar_modelo(modelo, conc.marca)
            variantes[m.casefold()][m] += n
        self.modelos = {k: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for k, c in variantes.items()}
        self.primero: dict[Clave, date] = {
            (f, i): d for f, i, d in pg.execute(
                "SELECT fuente, id_anuncio, min(snapshot_day) FROM stock_externo_snapshot GROUP BY 1, 2")}
        self.retiradas: dict[Clave, list[date]] = defaultdict(list)
        for f, i, d in pg.execute(
                """SELECT e.fuente, e.id_anuncio, r.snapshot_day FROM eventos_externos e
                   JOIN scraper_runs r USING (fuente, run_id) WHERE e.tipo = 'removed'"""):
            self.retiradas[(f, i)].append(d)
        # Nombre de cada concesionario tal como lo publica la web, por día; vale el más reciente.
        por_dia: dict[str, dict[date, Counter]] = defaultdict(lambda: defaultdict(Counter))
        for dealer, dia, nombre, n in pg.execute(
                """SELECT dealer_id, snapshot_day, concesionario, count(*) FROM stock_externo_snapshot
                   WHERE dealer_id IS NOT NULL AND concesionario IS NOT NULL GROUP BY 1, 2, 3"""):
            nombre = re.sub(r"\s+", " ", nombre).strip()
            if nombre:
                por_dia[dealer][dia][nombre] += n
        self._nombres: dict[str, tuple[list[date], list[str]]] = {}
        for dealer, dias in por_dia.items():
            orden = sorted(dias)
            self._nombres[dealer] = (orden, [sorted(dias[d].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
                                             for d in orden])
        self._dias: dict[date, dict[Clave, Fila]] = {}

    def nombre_concesionario(self, dealer_id: str, dia: date) -> str:
        """El nombre más reciente con que la web ha publicado este código hasta `dia` (si no hay, el código)."""
        dias, nombres = self._nombres.get(dealer_id, ([], []))
        i = bisect.bisect_right(dias, dia)
        return nombres[i - 1] if i else dealer_id

    def modelo(self, valor) -> str:
        m = normalizar_modelo(valor, self.conc.marca)
        return self.modelos.get(m.casefold(), m)

    def valido(self, dia: date, tipo: str) -> bool:
        return all(dia in self.completos[f] for f in TIPOS[tipo])

    def alta_observada(self, clave: Clave) -> bool:
        return (self.primero[clave] - UN_DIA) in self.completos[clave[0]]

    def filas(self, dia: date, tipo: str) -> dict[Clave, Fila]:
        if dia not in self._dias:
            if len(self._dias) > 16:  # se recorre en orden: basta con los últimos días
                for viejo in sorted(self._dias)[:-9]:
                    del self._dias[viejo]
            self._dias[dia] = {
                (f, i): Fila(p, d, bool(pr), self.modelo(m), *resto)
                for f, i, p, d, pr, m, *resto in self.pg.execute(
                    """SELECT fuente, id_anuncio, provincia_codigo, dealer_id, es_propio, modelo, precio_cents,
                              version, combustible, potencia_cv, fecha_matriculacion, km, color_exterior
                       FROM stock_externo_snapshot WHERE snapshot_day = %s""", (dia,))}
        fuentes = TIPOS[tipo]
        return {k: v for k, v in self._dias[dia].items() if k[0] in fuentes}

    def zonas(self, provincia: str | None) -> tuple[str, ...]:
        if provincia == self.conc.provincia_codigo:
            return ZONAS
        if provincia in self.conc.provincias_comunidad:
            return ("comunidad", "espana")
        return ("espana",)


# --------------------------------------------------------------------------- cálculo de un día

def calcular_dia(h: Hechos, dia: date, tipo: str) -> tuple[list[dict], list[dict], list[dict]]:
    """Filas de panel_mercado_dia, panel_mercado_modelo y panel_mercado_concesionario (esta, solo de
    la provincia del concesionario propio) para un día válido del tipo."""
    hoy = h.filas(dia, tipo)
    ayer_ok = h.valido(dia - UN_DIA, tipo)
    hace7_ok = h.valido(dia - 7 * UN_DIA, tipo)
    ventana_ok = all(h.valido(dia - k * UN_DIA, tipo) for k in range(8))

    ayer = h.filas(dia - UN_DIA, tipo) if ayer_ok else {}
    hace7 = h.filas(dia - 7 * UN_DIA, tipo) if hace7_ok else {}
    ultima_vista: dict[Clave, tuple[date, Fila]] = {}
    altas7: dict[Clave, Fila] = {}
    if ventana_ok:
        for k in range(7, 0, -1):  # de D−7 a D−1: queda la última observación de cada anuncio
            d = dia - k * UN_DIA
            for clave, fila in h.filas(d, tipo).items():
                ultima_vista[clave] = (d, fila)
        for d in (dia - k * UN_DIA for k in range(6, -1, -1)):  # altas de D−6 a D
            for clave, fila in (hoy if d == dia else h.filas(d, tipo)).items():
                if h.primero[clave] == d:
                    altas7[clave] = fila

    nombres = {"provincia": h.conc.provincia, "comunidad": h.conc.comunidad, "espana": "España"}
    filas_dia, filas_modelo, filas_conc = [], [], []
    for zona in ZONAS:
        en_zona = lambda f: zona in h.zonas(f.provincia)  # noqa: E731
        hoy_z = {c: f for c, f in hoy.items() if en_zona(f)}
        por_modelo: dict[str, dict] = defaultdict(lambda: {"anuncios": 0, "propios": 0, "hace7": 0, "altas7": 0,
                                                           "ausencias7": 0, "precios": [], "edades": []})
        for clave, f in hoy_z.items():
            m = por_modelo[f.modelo]
            m["anuncios"] += 1
            m["propios"] += f.es_propio
            if f.precio_cents is not None:
                m["precios"].append(f.precio_cents)
            m["edades"].append(((dia - h.primero[clave]).days, h.alta_observada(clave)))
        for f in hace7.values():
            if en_zona(f):
                por_modelo[f.modelo]["hace7"] += 1

        altas_hoy = ausencias_hoy = bajas_7 = None
        if ayer_ok:
            altas_hoy = sum(1 for c in hoy_z if h.primero[c] == dia)
            ausencias_hoy = sum(1 for c, f in ayer.items() if c not in hoy and en_zona(f))
        if ventana_ok:
            bajas_7 = 0
            for clave, (visto, f) in ultima_vista.items():
                if clave in hoy or not en_zona(f):
                    continue
                por_modelo[f.modelo]["ausencias7"] += 1
                if any(visto < r <= dia for r in h.retiradas.get(clave, ())):
                    bajas_7 += 1
            for clave, f in altas7.items():
                if en_zona(f):
                    por_modelo[f.modelo]["altas7"] += 1

        filas_dia.append({
            "dia": dia, "tipo": tipo, "zona": zona, "zona_nombre": nombres[zona],
            "anuncios": len(hoy_z), "propios": sum(1 for f in hoy_z.values() if f.es_propio),
            "concesionarios": len({f.dealer_id for f in hoy_z.values() if f.dealer_id}),
            "altas_hoy": altas_hoy, "ausencias_hoy": ausencias_hoy, "bajas_7": bajas_7,
            "ayer_completo": ayer_ok, "hace7_completo": hace7_ok, "ventana_completa": ventana_ok,
        })
        for modelo, m in por_modelo.items():
            med, cota = dias_publicado(m["edades"])
            filas_modelo.append({
                "dia": dia, "tipo": tipo, "zona": zona, "modelo": modelo, "anuncios": m["anuncios"],
                "propios": m["propios"], "anuncios_hace7": m["hace7"] if hace7_ok else None,
                "altas_7": m["altas7"] if ventana_ok else None,
                "ausencias_7": m["ausencias7"] if ventana_ok else None,
                "precio_mediano_cents": precio_mediano(m["precios"]), "n_precio": len(m["precios"]),
                "dias_mediana": med, "dias_cota": cota,
            })
        if zona == "provincia":
            filas_conc = _por_concesionario(h, dia, tipo, hoy_z, [f for f in hace7.values() if en_zona(f)], hace7_ok)
    return filas_dia, filas_modelo, filas_conc


def _por_concesionario(h: Hechos, dia: date, tipo: str, hoy_z: dict[Clave, Fila], hace7_z: list[Fila],
                       hace7_ok: bool) -> list[dict]:
    """Una fila por concesionario con anuncios hoy en la provincia del concesionario propio."""
    conc: dict[str, dict] = defaultdict(lambda: {"anuncios": 0, "precios": [], "propio": False})
    for f in hoy_z.values():
        if f.dealer_id:
            c = conc[f.dealer_id]
            c["anuncios"] += 1
            c["propio"] = c["propio"] or f.es_propio
            if f.precio_cents is not None:
                c["precios"].append(f.precio_cents)
    hace7 = Counter(f.dealer_id for f in hace7_z if f.dealer_id)
    return [{
        "dia": dia, "tipo": tipo, "dealer_id": dealer, "concesionario": h.nombre_concesionario(dealer, dia),
        "es_propio": c["propio"], "anuncios": c["anuncios"],
        "anuncios_hace7": hace7.get(dealer, 0) if hace7_ok else None,
        "precio_mediano_cents": precio_mediano(c["precios"]), "n_precio": len(c["precios"]),
    } for dealer, c in conc.items()]


# --------------------------------------------------------------------------- escritura

COLS_DIA = ["dia", "tipo", "zona", "zona_nombre", "anuncios", "propios", "concesionarios", "altas_hoy",
            "ausencias_hoy", "bajas_7", "ayer_completo", "hace7_completo", "ventana_completa"]
COLS_MODELO = ["dia", "tipo", "zona", "modelo", "anuncios", "propios", "anuncios_hace7", "altas_7", "ausencias_7",
               "precio_mediano_cents", "n_precio", "dias_mediana", "dias_cota"]
COLS_CONCESIONARIO = ["dia", "tipo", "dealer_id", "concesionario", "es_propio", "anuncios", "anuncios_hace7",
                      "precio_mediano_cents", "n_precio"]


def _insertar(pg, tabla: str, cols: list[str], filas: list[dict]) -> None:
    if filas:
        with pg.cursor() as cur:
            cur.executemany(f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                            [tuple(f[c] for c in cols) for f in filas])


def _capturas(pg, h: Hechos, desde: date, hasta: date) -> int:
    sin_prov = {(f, d): n for f, d, n in pg.execute(
        """SELECT fuente, snapshot_day, count(*) FILTER (WHERE provincia_codigo IS NULL)
           FROM stock_externo_snapshot WHERE snapshot_day BETWEEN %s AND %s GROUP BY 1, 2""", (desde, hasta))}
    filas = []
    for fuente in FUENTES:
        if not h.completos[fuente]:
            continue
        d = max(desde, min(h.completos[fuente]))
        while d <= hasta:
            fin, observados = h.runs.get((fuente, d), (None, None))
            filas.append((fuente, d, (fuente, d) in h.runs, fin, observados, sin_prov.get((fuente, d))))
            d += UN_DIA
    pg.execute("DELETE FROM panel_mercado_capturas WHERE dia BETWEEN %s AND %s", (desde, hasta))
    with pg.cursor() as cur:
        cur.executemany("INSERT INTO panel_mercado_capturas (fuente, dia, completa, fin_at, anuncios, sin_provincia) "
                        "VALUES (%s, %s, %s, %s, %s, %s)", filas)
    return len(filas)


def precalcular(pg: psycopg.Connection, desde: date | None = None, hasta: date | None = None,
                conc: Concesionario | None = None) -> dict:
    """Recalcula los días de [desde, hasta] (por defecto, todo el histórico hasta hoy)."""
    conc = conc or cargar_concesionario()
    catalogo = versiones.cargar(conc.marca)
    h = Hechos(pg, conc)
    hasta = hasta or hoy_madrid()
    todos = set().union(*h.completos.values())
    if not todos:
        return {"desde": None, "hasta": str(hasta), "dias": 0, "filas_dia": 0, "filas_modelo": 0,
                "filas_concesionario": 0, "filas_segmento": 0}
    desde = max(desde or min(todos), min(todos))
    resumen = {"desde": str(desde), "hasta": str(hasta), "dias": 0, "filas_dia": 0, "filas_modelo": 0,
               "filas_concesionario": 0, "filas_segmento": 0}
    d = desde
    while d <= hasta:
        with pg.transaction():
            for tabla in ("panel_mercado_concesionario", "panel_mercado_modelo", "panel_mercado_dia",
                          "panel_mercado_segmento", "panel_mercado_sin_clasificar"):
                pg.execute(f"DELETE FROM {tabla} WHERE dia = %s", (d,))
            calculado = False
            for tipo in TIPOS:
                if h.valido(d, tipo):
                    filas_dia, filas_modelo, filas_conc = calcular_dia(h, d, tipo)
                    _insertar(pg, "panel_mercado_dia", COLS_DIA, filas_dia)
                    _insertar(pg, "panel_mercado_modelo", COLS_MODELO, filas_modelo)
                    _insertar(pg, "panel_mercado_concesionario", COLS_CONCESIONARIO, filas_conc)
                    filas_seg, sin_clasificar = segmentos.calcular(h, d, tipo, catalogo)
                    _insertar(pg, "panel_mercado_segmento", segmentos.COLS_SEGMENTO, filas_seg)
                    _insertar(pg, "panel_mercado_sin_clasificar", segmentos.COLS_SIN_CLASIFICAR, sin_clasificar)
                    resumen["filas_segmento"] += len(filas_seg)
                    resumen["filas_dia"] += len(filas_dia)
                    resumen["filas_modelo"] += len(filas_modelo)
                    resumen["filas_concesionario"] += len(filas_conc)
                    calculado = True
            resumen["dias"] += calculado
        d += UN_DIA
    with pg.transaction():
        resumen["filas_capturas"] = _capturas(pg, h, desde, hasta)
    return resumen


# --------------------------------------------------------------------------- línea de órdenes

def _anotar(pg, dia: date, estado: str, detalle: dict) -> None:
    pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) VALUES ('panel_mercado', "
               "'precalculo', %s, %s, %s)", (dia, estado, Jsonb(detalle)))


def ejecutar(desde: date | None, hasta: date | None, avisar=None) -> int:
    """Precálculo con anotación en fuentes_estado y aviso por Telegram si falla. Devuelve el código."""
    hoy = hoy_madrid()
    paso = "conexión a Postgres"
    inicio = time.monotonic()
    try:
        with conectar(autocommit=True) as pg:
            paso = "precálculo"
            resumen = precalcular(pg, desde, hasta)
            resumen["segundos"] = round(time.monotonic() - inicio, 1)
            paso = "anotación en fuentes_estado"
            _anotar(pg, hoy, "ok", resumen)
        LOG.info("Precálculo terminado: %s", resumen)
        return 0
    except Exception as exc:
        error = telegram.limpiar(f"{type(exc).__name__}: {exc}")[:500]
        LOG.error("Precálculo fallido en «%s»: %s", paso, error)
        try:
            with conectar(autocommit=True, connect_timeout=10) as pg:
                _anotar(pg, hoy, "error", {"paso": paso, "error": error})
        except Exception:
            LOG.warning("Tampoco se pudo anotar el fallo en fuentes_estado")
        texto = "\n".join([f"CDF {hoy.strftime('%d/%m')} · el precálculo del panel ha fallado",
                           f"Paso: {paso} ({type(exc).__name__})",
                           "Detalle: registro de precalcular de hoy (make logs)"])
        aviso_copia.avisar_texto(hoy, texto, "precalculo", enviar=avisar)
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    grupo = parser.add_mutually_exclusive_group()
    grupo.add_argument("--dia", type=date.fromisoformat, help="solo este día")
    grupo.add_argument("--ultimos", type=int, help="los N últimos días hasta hoy")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    desde = hasta = None
    if args.dia:
        desde = hasta = args.dia
    elif args.ultimos:
        hasta = hoy_madrid()
        desde = hasta - (args.ultimos - 1) * UN_DIA
    return ejecutar(desde, hasta)


if __name__ == "__main__":
    sys.exit(main())
