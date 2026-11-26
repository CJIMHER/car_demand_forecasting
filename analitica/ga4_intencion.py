"""Informe de análisis de las señales de intención de compra de GA4 (make ga4-intencion-informe).

No toca la previsión ni el panel: lee las tablas y escribe un texto. Protocolo fijado de antemano en
config/ga4_intencion.yaml («analisis»); el informe lo repite al principio.

Para VN y VO, cada señal semanal (ga4_intencion_semana_vigente: sesiones, sesiones con interacción, tiempo de
interacción, vistas de fichas, configurador, form_start, form_submit, Solicitar información, Click llamada o
email y el lead de la web), en tres grupos de canal: todos, orgánico y directo, y pagado.

1. Correlación cruzada con tres objetivos semanales:
   - leads de Walcu de todos los orígenes (walcu_campana_semana_vigente, semanas con la carga completa; sin las
     campañas de otras marcas ni de otras empresas del grupo);
   - leads de Walcu de Facebook (los orígenes de Meta de config/campanas_walcu.yaml);
   - ventas por fecha de venta (walcu_ventas_semana), sin las últimas semanas, que Walcu cierra con retraso.
   La señal de la semana t−k frente al objetivo de la semana t, k = 0 … retardo_maximo. Las dos series, en
   diferencias semanales de log(1 + x), para que la tendencia común no fabrique correlación. Dos cifras: la
   correlación tal cual y la parcial, quitando el gasto de Meta de las semanas t y t−k (el mismo gasto por tipo
   que usa la previsión), porque la inversión en Meta mueve a la vez el tráfico pagado y los leads. Una
   correlación es distinta de 0 si pasa de ±1,96/√n (marcada con *). Con muchas señales y retardos, alguna
   pasará por azar (1 de cada 20): la correlación orienta, no decide.
2. Validación con origen móvil (la que decide), con PASA / NO PASA por separado para cada objetivo:
   a. Leads de Meta: la señal entra en el modelo de leads de la previsión (analitica/prevision.py, como Google
      Trends: log(1 + señal de la semana anterior a la previsión) y se compara el error absoluto medio de los
      leads del tipo con y sin ella, en los mismos orígenes, con el gasto que hubo y con el previsto. PASA si baja
      el error al menos mejora_minima_pct en los dos. El modelo ya lleva el gasto de Meta: lo que aporte la señal
      es lo que no explica la inversión.
   b. Leads de Walcu de todos los orígenes y ventas por fecha de venta (fuera de Meta): un modelo sencillo
      (validar_simple: la media de las últimas semanas corregida por cuánto se ha movido la señal) frente a esa
      misma media. Sin señal es la referencia; PASA si baja su error al menos mejora_minima_pct.

Uso:
  python -m analitica.ga4_intencion [--hoy AAAA-MM-DD]       (make ga4-intencion-informe, a un fichero)
"""

from __future__ import annotations

import argparse
import hashlib
import math
import sys
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import date, timedelta

import numpy as np
import psycopg

from analitica import prevision as pv
from ingesta import concesionario as cc
from ingesta import ga4_series as gs
from ingesta import referencias_meta as ref
from ingesta.concesionario import ErrorConfiguracion

TIPOS = ("VN", "VO")
UNA_SEMANA = timedelta(days=7)
OBJETIVOS = (("leads_todos", "leads de Walcu (todos los orígenes)"), ("leads_facebook", "leads de Walcu (Facebook)"),
             ("ventas", "ventas por fecha de venta"))
FUERA_META = ("leads_todos", "ventas")           # objetivos de la validación con el modelo sencillo


# --------------------------------------------------------------------------- protocolo

@dataclass(frozen=True)
class Protocolo:
    retardo_maximo: int
    marcas: tuple[str, ...]
    grupos_canal: dict[str, tuple[str, ...]]
    senales: dict[str, tuple[str, ...]]
    ventas_excluidas: int
    semanas_minimas: int
    mejora_minima: float           # fracción
    version: str
    fm_origenes: int = 26          # validación fuera de Meta
    fm_horizonte: int = 4
    fm_ventana: int = 4


def protocolo(cfg: gs.Config | None = None) -> Protocolo:
    cfg = cfg or gs.cargar_config()
    a = cfg.analisis

    def error(que: str) -> ErrorConfiguracion:
        return ErrorConfiguracion(f"config/ga4_intencion.yaml: analisis.{que}")

    def entero(clave: str, minimo: int) -> int:
        v = a.get(clave)
        if isinstance(v, bool) or not isinstance(v, int) or v < minimo:
            raise error(f"{clave} tiene que ser un entero ≥ {minimo}")
        return v

    marcas = tuple(a.get("marcas") or ())
    if not marcas or any(m not in gs.MARCAS for m in marcas):
        raise error(f"marcas: una lista de {', '.join(gs.MARCAS)}")
    grupos = {str(k): tuple(v or ()) for k, v in (a.get("grupos_canal") or {}).items()}
    if not grupos or any(not v or any(g not in gs.GRUPOS for g in v) for v in grupos.values()):
        raise error(f"grupos_canal: cada grupo, una lista de {', '.join(gs.GRUPOS)}")
    senales = {str(k): tuple(v or ()) for k, v in (a.get("senales") or {}).items()}
    if not senales or any(not v or any(c not in gs.COLUMNAS for c in v) for v in senales.values()):
        raise error("senales: cada señal, una lista de columnas de ga4_intencion_semana")
    mejora = a.get("mejora_minima_pct")
    if isinstance(mejora, bool) or not isinstance(mejora, (int, float)) or not 0 <= mejora < 100:
        raise error("mejora_minima_pct tiene que ser un número entre 0 y 100")
    texto = repr(sorted(a.items()))
    fm = a.get("fuera_meta") or {}
    if not isinstance(fm, dict):
        raise error("fuera_meta debe ser un diccionario (origenes, horizonte, semanas_referencia)")

    def entero_fm(clave: str) -> int:
        v = fm.get(clave)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise error(f"fuera_meta.{clave} tiene que ser un entero ≥ 1")
        return v

    return Protocolo(entero("retardo_maximo", 0), marcas, grupos, senales, entero("ventas_semanas_excluidas", 0),
                     entero("semanas_minimas", 3), mejora / 100, hashlib.sha256(texto.encode()).hexdigest()[:12],
                     entero_fm("origenes"), entero_fm("horizonte"), entero_fm("semanas_referencia"))


# --------------------------------------------------------------------------- datos

Serie = dict[date, float]


def leer_senales(pg: psycopg.Connection, p: Protocolo) -> tuple[dict[tuple[str, str, str], Serie], dict[str, date]]:
    """({(tipo, señal, grupo de canal): {semana: valor}}, primera semana con valor > 0 de cada señal). Cada serie tiene
    todas las semanas cargadas (0 si no hubo nada)."""
    semanas = [s for (s,) in pg.execute("SELECT semana FROM ga4_intencion_carga_vigente ORDER BY semana")]
    cols = sorted({c for v in p.senales.values() for c in v})
    sumas: dict[tuple[date, str, str], dict[str, float]] = defaultdict(lambda: defaultdict(float))
    for fila in pg.execute(f"SELECT semana, tipo, canal, {', '.join(cols)} FROM ga4_intencion_semana_vigente "
                           "WHERE tipo = ANY(%s) AND marca = ANY(%s)", (list(TIPOS), list(p.marcas))):
        s, tipo, canal, *vals = fila
        for c, v in zip(cols, vals):
            sumas[(s, tipo, canal)][c] += float(v)
    series: dict[tuple[str, str, str], Serie] = {}
    primera: dict[str, date] = {}
    for tipo in TIPOS:
        for senal, columnas in p.senales.items():
            for grupo, canales in p.grupos_canal.items():
                serie = {s: sum(sumas.get((s, tipo, c), {}).get(col, 0.0) for c in canales for col in columnas)
                         for s in semanas}
                series[(tipo, senal, grupo)] = serie
                con = [s for s, v in serie.items() if v > 0]
                if con:
                    primera[senal] = min(primera.get(senal, con[0]), min(con))
    return series, primera


def leer_leads(pg: psycopg.Connection, conc=None, cruce=None) -> dict[str, dict[str, Serie]]:
    """{leads_todos|leads_facebook: {tipo: {semana: leads}}}, solo semanas con la carga hecha después de acabar."""
    conc = conc or cc.cargar()
    cruce = cruce or ref.cargar()
    cargada = dict(pg.execute("SELECT semana, max((fecha_carga AT TIME ZONE 'Europe/Madrid')::date) "
                              "FROM walcu_campana_semana_vigente GROUP BY 1").fetchall())
    completas = {s for s, d in cargada.items() if s + timedelta(days=6) < d}
    salida = {"leads_todos": {t: dict.fromkeys(completas, 0.0) for t in TIPOS},
              "leads_facebook": {t: dict.fromkeys(completas, 0.0) for t in TIPOS}}
    for semana, origen, campana, tipo, leads in pg.execute(
            "SELECT semana, origen, campana, tipo, sum(leads) FROM walcu_campana_semana_vigente GROUP BY 1, 2, 3, 4"):
        if semana not in completas or conc.otra_marca(campana) or conc.otra_empresa(campana) \
                or conc.otra_empresa(origen):
            continue
        salida["leads_todos"][tipo][semana] += float(leads)
        if cruce.es_meta(origen):
            salida["leads_facebook"][tipo][semana] += float(leads)
    return salida


def leer_ventas(pg: psycopg.Connection, hoy: date, excluidas: int) -> dict[str, Serie]:
    from ingesta.walcu_ventas import vigentes
    ventas, cubiertas = vigentes(pg)
    limite = gs.lunes(hoy) - excluidas * UNA_SEMANA          # sin las últimas `excluidas` semanas terminadas
    validas = {s for s in cubiertas if s < limite}
    return {t: {s: float(ventas.get((s, t), 0)) for s in validas} for t in TIPOS}


def gasto_meta(pg: psycopg.Connection, datos: pv.Datos) -> dict[str, Serie]:
    """Gasto de Meta por tipo y semana (euros), el de la previsión. Desde la primera semana con datos de Meta, las
    semanas sin gasto valen 0; antes, no se sabe."""
    primera = pg.execute("SELECT min(semana) FROM panel_campana_semana WHERE origen = 'real'").fetchone()[0]
    salida: dict[str, Serie] = {t: {} for t in TIPOS}
    if primera is None or datos.dia_meta is None:
        return salida
    s = primera
    while s <= datos.dia_meta:
        for t in TIPOS:
            g = datos.grupos.get((t, None))
            salida[t][s] = g.semanas[s].gasto if g and s in g.semanas else 0.0
        s += UNA_SEMANA
    return salida


# --------------------------------------------------------------------------- correlación cruzada

def _dlog(serie: Serie, semanas: list[date]) -> np.ndarray:
    v = np.array([math.log1p(serie[s]) if s in serie else np.nan for s in semanas])
    return np.concatenate([[np.nan], np.diff(v)])


def _residuos(v: np.ndarray, Z: np.ndarray) -> np.ndarray:
    beta, *_ = np.linalg.lstsq(Z, v, rcond=None)
    return v - Z @ beta


def _corr(a: np.ndarray, b: np.ndarray) -> float | None:
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return None
    return float(np.corrcoef(a, b)[0, 1])


@dataclass(frozen=True)
class Correlacion:
    retardo: int
    n: int
    r: float | None
    n_parcial: int
    r_parcial: float | None

    def distinta(self, parcial: bool = True) -> bool:
        r, n = (self.r_parcial, self.n_parcial) if parcial else (self.r, self.n)
        return r is not None and n > 0 and abs(r) > 1.96 / math.sqrt(n)


def correlaciones(senal: Serie, objetivo: Serie, gasto: Serie, retardo_maximo: int, minimas: int) -> list[Correlacion]:
    todas = sorted(set(senal) | set(objetivo) | set(gasto))
    if not todas:
        return []
    semanas = [todas[0] + i * UNA_SEMANA for i in range((todas[-1] - todas[0]).days // 7 + 1)]
    x, y, g = _dlog(senal, semanas), _dlog(objetivo, semanas), _dlog(gasto, semanas)
    salida = []
    for k in range(retardo_maximo + 1):
        xk = np.concatenate([np.full(k, np.nan), x[:len(x) - k]]) if k else x
        gk = np.concatenate([np.full(k, np.nan), g[:len(g) - k]]) if k else g
        ok = ~np.isnan(xk) & ~np.isnan(y)
        n = int(ok.sum())
        r = _corr(xk[ok], y[ok]) if n >= minimas else None
        okp = ok & ~np.isnan(g) & ~np.isnan(gk)
        np_ = int(okp.sum())
        rp = None
        if np_ >= minimas:
            Z = np.column_stack([np.ones(np_), g[okp], gk[okp]])
            rp = _corr(_residuos(xk[okp], Z), _residuos(y[okp], Z))
        salida.append(Correlacion(k, n, r, np_, rp))
    return salida


# --------------------------------------------------------------------------- validación con origen móvil

@dataclass(frozen=True)
class Validacion:
    n: int
    mae_sin_real: float
    mae_con_real: float
    mae_sin_previsto: float
    mae_con_previsto: float
    comparable: bool

    def mejora(self, variante: str) -> float:
        sin, con = ((self.mae_sin_real, self.mae_con_real) if variante == "real"
                    else (self.mae_sin_previsto, self.mae_con_previsto))
        return (sin - con) / sin if sin > 0 else 0.0

    def pasa(self, minima: float) -> bool:
        return self.comparable and self.mejora("real") >= minima and self.mejora("previsto") >= minima


def _errores(filas: list[dict]) -> dict[str, tuple[int, float]]:
    return {f["variante"]: (f["n"], f["mae_modelo"]) for f in filas
            if f["nivel"] == "tipo" and f["medida"] == "leads"}


def validar_senal(datos: pv.Datos, cfg: pv.Config, tipo: str, serie: Serie,
                  base: dict[str, tuple[int, float]] | None = None) -> Validacion | None:
    g = datos.grupos.get((tipo, None))
    if g is None:
        return None
    solo = replace(datos, grupos={(tipo, None): replace(g, trends={})})
    base = base if base is not None else _errores(pv.validar(solo, cfg, {tipo: False})[0])
    con = replace(datos, grupos={(tipo, None): replace(g, trends=dict(serie))})
    err = _errores(pv.validar(con, cfg, {tipo: True})[0])
    if "gasto_real" not in base or "gasto_previsto" not in base or "gasto_real" not in err \
            or "gasto_previsto" not in err:
        return None
    comparable = base["gasto_real"][0] == err["gasto_real"][0] and base["gasto_previsto"][0] == err["gasto_previsto"][0]
    return Validacion(base["gasto_real"][0], base["gasto_real"][1], err["gasto_real"][1], base["gasto_previsto"][1],
                      err["gasto_previsto"][1], comparable)


def base_validacion(datos: pv.Datos, cfg: pv.Config, tipo: str) -> dict[str, tuple[int, float]]:
    g = datos.grupos.get((tipo, None))
    if g is None:
        return {}
    solo = replace(datos, grupos={(tipo, None): replace(g, trends={})})
    return _errores(pv.validar(solo, cfg, {tipo: False})[0])


# --------------------------------------------------------------------------- validación fuera de Meta

@dataclass(frozen=True)
class ValidacionSimple:
    n: int
    mae_referencia: float
    mae_modelo: float

    @property
    def mejora(self) -> float:
        return (self.mae_referencia - self.mae_modelo) / self.mae_referencia if self.mae_referencia > 0 else 0.0

    def pasa(self, minima: float) -> bool:
        return self.mejora >= minima


def validar_simple(senal: Serie, objetivo: Serie, origenes: int, horizonte: int, ventana: int, retraso: int,
                   minimas: int) -> ValidacionSimple | None:
    """Validación con origen móvil de un modelo sencillo frente a la referencia (la media de las últimas `ventana`
    semanas conocidas del objetivo).

    En el origen t solo se sabe el objetivo hasta t − retraso (las ventas se cierran con retraso) y la señal hasta t.
    Referencia R_t: media del objetivo en las `ventana` semanas que acaban en t − retraso. Cambio de la señal
    d_t = log(1 + señal_t) − log(1 + media de la señal en esas mismas semanas). Modelo, para cada horizonte h:
    log(1 + y_{t+h}) = log(1 + R_t) + β_h · d_t, con β_h por mínimos cuadrados sin término independiente, con los
    pares ya conocidos en t. Sin señal (β = 0) es la referencia: lo que mejora es lo que aporta la señal.
    Se prevén h = 1 … horizonte desde los últimos `origenes` orígenes con todo el horizonte conocido."""
    def ventana_de(t: date) -> list[date]:
        return [t - (retraso + j) * UNA_SEMANA for j in range(ventana)]

    def referencia(t: date) -> float | None:
        w = ventana_de(t)
        return sum(objetivo[s] for s in w) / ventana if all(s in objetivo for s in w) else None

    def cambio(t: date) -> float | None:
        w = ventana_de(t)
        if t not in senal or not all(s in senal for s in w):
            return None
        return math.log1p(senal[t]) - math.log1p(sum(senal[s] for s in w) / ventana)

    semanas = sorted(objetivo)
    utiles = {t: (referencia(t), cambio(t)) for t in semanas}
    utiles = {t: v for t, v in utiles.items() if v[0] is not None and v[1] is not None}
    candidatos = [t for t in sorted(utiles) if all(t + h * UNA_SEMANA in objetivo for h in range(1, horizonte + 1))]
    puntos = []
    for t in candidatos[-origenes:]:
        corte = t - retraso * UNA_SEMANA                      # lo último que se sabe del objetivo en t
        r_t, d_t = utiles[t]
        for h in range(1, horizonte + 1):
            pares = [(d, math.log1p(objetivo[u + h * UNA_SEMANA]) - math.log1p(r))
                     for u, (r, d) in utiles.items() if u + h * UNA_SEMANA <= corte]
            if len(pares) < minimas:
                continue
            dd = np.array([p[0] for p in pares])
            yy = np.array([p[1] for p in pares])
            beta = float(dd @ yy / (dd @ dd)) if dd @ dd > 0 else 0.0
            real = objetivo[t + h * UNA_SEMANA]
            prevista = max(0.0, math.expm1(math.log1p(r_t) + beta * d_t))
            puntos.append((abs(r_t - real), abs(prevista - real)))
    if not puntos:
        return None
    arr = np.array(puntos)
    return ValidacionSimple(len(puntos), float(arr[:, 0].mean()), float(arr[:, 1].mean()))


# --------------------------------------------------------------------------- informe

def _desde_primera(serie: Serie, primera: date | None) -> Serie:
    """La serie desde la primera semana con datos de la señal: antes, el evento no existía (no es un 0)."""
    return {s: v for s, v in serie.items() if primera and s >= primera}


def _f(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _r(r: float | None, marca: bool) -> str:
    if r is None:
        return "    —"
    return f"{r:+.2f}".replace(".", ",") + ("*" if marca else " ")


def _pct(x: float) -> str:
    return f"{100 * x:+.1f} %".replace(".", ",")


def _rango(serie: Serie) -> str:
    return f"{_f(min(serie))} a {_f(max(serie))} ({len(serie)} semanas)" if serie else "sin datos"


def informe(pg: psycopg.Connection, hoy: date, cfg: gs.Config | None = None, cfg_prev: pv.Config | None = None) -> str:
    p = protocolo(cfg)
    cfg_prev = cfg_prev or pv.cargar_config()
    L: list[str] = []
    w = L.append
    w(f"Señales de intención de compra de GA4: análisis del {_f(hoy)}")
    w("=" * 78)
    w("Protocolo fijado de antemano (config/ga4_intencion.yaml, analisis; versión " + p.version + "):")
    w(f"  retardos de 0 a {p.retardo_maximo} semanas (la señal de la semana t−k frente al objetivo de la semana t);")
    w("  series en diferencias semanales de log(1 + x); «sin Meta»: correlación parcial quitando el gasto de Meta de")
    w("  las semanas t y t−k; * = distinta de 0 (|r| > 1,96/√n); con menos de "
      f"{p.semanas_minimas} semanas en común, «—»;")
    w(f"  marcas: {', '.join(p.marcas)}; ventas sin las últimas {p.ventas_excluidas} semanas terminadas;")
    w(f"  una señal PASA si en la validación con origen móvil baja el error al menos un "
      f"{_pct(p.mejora_minima).lstrip('+')}, por separado para cada objetivo:")
    w("    · leads de Meta: en el modelo de leads de la previsión, con el gasto que hubo y con el previsto;")
    w("    · leads de Walcu (todos los orígenes) y ventas por fecha de venta: un modelo sencillo frente a la media de")
    w(f"      las últimas {p.fm_ventana} semanas conocidas, {p.fm_origenes} orígenes × {p.fm_horizonte} semanas.")

    series, primera = leer_senales(pg, p)
    datos = pv.leer(pg, hoy, cfg_prev)
    objetivos = {**leer_leads(pg), "ventas": leer_ventas(pg, hoy, p.ventas_excluidas)}
    gasto = gasto_meta(pg, datos)
    semanas_ga4 = sorted({s for v in series.values() for s in v})
    w("")
    w("Datos")
    w(f"  GA4: {_f(semanas_ga4[0]) if semanas_ga4 else '—'} a {_f(semanas_ga4[-1]) if semanas_ga4 else '—'} "
      f"({len(semanas_ga4)} semanas cargadas)")
    for t in TIPOS:
        for clave, nombre in OBJETIVOS:
            w(f"  {t} · {nombre}: {_rango(objetivos[clave][t])}")
        w(f"  {t} · gasto de Meta: {_rango(gasto[t])}")
    w("  Primera semana con datos de cada señal: " + ", ".join(f"{s} {_f(primera.get(s))}" for s in p.senales))
    if not semanas_ga4:
        w("\nSin semanas de GA4 cargadas: make ga4-series DESDE=… antes del informe.")
        return "\n".join(L) + "\n"

    # 1. Correlación cruzada
    adelanta: dict[tuple[str, str, str], list[str]] = defaultdict(list)
    for t in TIPOS:
        for clave, nombre in OBJETIVOS:
            w("")
            w(f"1. Correlación cruzada · {t} · {nombre}")
            cab = " ".join(f"{'k=' + str(k):>6}" for k in range(p.retardo_maximo + 1))
            w(f"  {'señal':<22} {'canal':<17} {'':<8} {cab}   n")
            for senal in p.senales:
                for grupo in p.grupos_canal:
                    serie = _desde_primera(series[(t, senal, grupo)], primera.get(senal))
                    if not any(serie.values()):
                        continue
                    cs = correlaciones(serie, objetivos[clave][t], gasto[t], p.retardo_maximo, p.semanas_minimas)
                    if not cs:
                        continue
                    w(f"  {senal:<22} {grupo:<17} {'tal cual':<8} "
                      + " ".join(f"{_r(c.r, c.distinta(False)):>6}" for c in cs) + f"  {max(c.n for c in cs)}")
                    w(f"  {'':<22} {'':<17} {'sin Meta':<8} "
                      + " ".join(f"{_r(c.r_parcial, c.distinta()):>6}" for c in cs)
                      + f"  {max(c.n_parcial for c in cs)}")
                    lags = [c.retardo for c in cs if c.retardo > 0 and c.distinta() and (c.r_parcial or 0) > 0]
                    if lags:
                        adelanta[(t, senal, grupo)].append(f"{nombre} (k = {', '.join(map(str, lags))})")

    # 2. Validación
    resultados: dict[tuple[str, str, str], Validacion | None] = {}
    sin_validar: set[str] = set()
    for t in TIPOS:
        w("")
        w(f"2. Validación con origen móvil · {t} · error absoluto medio de los leads de Meta por semana "
          f"({cfg_prev.val_semanas} orígenes × {cfg_prev.semanas} semanas)")
        base = base_validacion(datos, cfg_prev, t)
        if not base:
            w("  Sin semanas de Meta con leads de Walcu para este tipo: no se puede validar.")
            sin_validar.add(t)
            continue
        w(f"  Sin señal: con el gasto que hubo {base['gasto_real'][1]:.2f}; con el previsto "
          f"{base['gasto_previsto'][1]:.2f} (n = {base['gasto_real'][0]})".replace(".", ","))
        w(f"  {'señal':<22} {'canal':<17} {'gasto que hubo':>20} {'gasto previsto':>20}  veredicto")
        for senal in p.senales:
            for grupo in p.grupos_canal:
                serie = _desde_primera(series[(t, senal, grupo)], primera.get(senal))
                v = validar_senal(datos, cfg_prev, t, serie, base) if any(serie.values()) else None
                resultados[(t, senal, grupo)] = v
                if v is None:
                    w(f"  {senal:<22} {grupo:<17} {'—':>20} {'—':>20}  NO PASA (sin datos de la señal)")
                    continue
                real = f"{v.mae_con_real:.2f} ({_pct(v.mejora('real'))})".replace(".", ",", 1)
                prev = f"{v.mae_con_previsto:.2f} ({_pct(v.mejora('previsto'))})".replace(".", ",", 1)
                veredicto = "PASA" if v.pasa(p.mejora_minima) else (
                    "NO PASA (distintas semanas)" if not v.comparable else "NO PASA")
                w(f"  {senal:<22} {grupo:<17} {real:>20} {prev:>20}  {veredicto}")

    # 2b. Validación fuera de Meta: leads de Walcu de todos los orígenes y ventas
    simples: dict[tuple[str, str, str, str], ValidacionSimple | None] = {}
    retrasos = {"leads_todos": 0, "ventas": p.ventas_excluidas}
    nombres = dict(OBJETIVOS)
    for t in TIPOS:
        for clave in FUERA_META:
            w("")
            w(f"2b. Validación fuera de Meta · {t} · {nombres[clave]} · modelo sencillo frente a la media de "
              f"{p.fm_ventana} semanas")
            w(f"  {'señal':<22} {'canal':<17} {'n':>4} {'referencia':>11} {'con la señal':>22}  veredicto")
            for senal in p.senales:
                for grupo in p.grupos_canal:
                    serie = _desde_primera(series[(t, senal, grupo)], primera.get(senal))
                    v = (validar_simple(serie, objetivos[clave][t], p.fm_origenes, p.fm_horizonte, p.fm_ventana,
                                        retrasos[clave], p.semanas_minimas) if any(serie.values()) else None)
                    simples[(t, clave, senal, grupo)] = v
                    if v is None:
                        w(f"  {senal:<22} {grupo:<17} {'—':>4} {'—':>11} {'—':>22}  NO PASA (sin datos bastantes)")
                        continue
                    con = f"{v.mae_modelo:.2f} ({_pct(v.mejora)})".replace(".", ",", 1)
                    w(f"  {senal:<22} {grupo:<17} {v.n:>4} {f'{v.mae_referencia:.2f}'.replace('.', ','):>11} "
                      f"{con:>22}  {'PASA' if v.pasa(p.mejora_minima) else 'NO PASA'}")

    # 3. Resumen
    w("")
    w("3. Resumen: PASA / NO PASA por objetivo")

    def listar(t: str, objetivo: str, veredictos: dict[tuple[str, str], bool | None]) -> None:
        pasan = [k for k, v in veredictos.items() if v]
        no = [k for k, v in veredictos.items() if v is False]
        sin = [k for k, v in veredictos.items() if v is None]
        for nombre, lista in (("PASAN", pasan), ("NO PASAN", no), ("Sin datos de la señal (no pasan)", sin)):
            w(f"  {t} · {objetivo} · {nombre} ({len(lista)}): "
              + ("; ".join(f"{s} ({g})" for s, g in lista) if lista else "ninguna"))

    for t in TIPOS:
        if t in sin_validar:
            w(f"  {t} · leads de Meta: no se ha podido validar ninguna señal: sin semanas de Meta con leads de "
              f"Walcu de {t}.")
        else:
            listar(t, "leads de Meta", {(s, g): (v.pasa(p.mejora_minima) if v else None)
                                        for (tt, s, g), v in resultados.items() if tt == t})
        for clave in FUERA_META:
            listar(t, nombres[clave], {(s, g): (v.pasa(p.mejora_minima) if v else None)
                                       for (tt, c, s, g), v in simples.items() if tt == t and c == clave})
        corr = [f"{s} ({g}): {' · '.join(v)}" for (tt, s, g), v in sorted(adelanta.items()) if tt == t]
        w(f"  {t} · Adelantan con correlación parcial positiva distinta de 0 (orienta, no decide):")
        for c in corr or ["ninguna"]:
            w(f"    {c}")
    w("")
    w("Lectura: una señal del canal pagado que no pasa sin Meta ni en la validación solo repite la inversión en Meta;")
    w("las de orgánico y directo son las que pueden adelantar demanda que no compra Meta. Nada de esto cambia todavía")
    w("la previsión ni el panel.")
    return "\n".join(L) + "\n"


def main(argv=None) -> int:
    from ingesta.db import conectar
    from ingesta.scrapers_vw import hoy_madrid
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hoy", type=date.fromisoformat, help="día del análisis (por defecto, hoy)")
    args = parser.parse_args(argv)
    with conectar(autocommit=True) as pg:
        sys.stdout.write(informe(pg, args.hoy or hoy_madrid()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
