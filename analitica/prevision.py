"""Previsión bayesiana de leads, citas y ventas de las campañas de Meta (cada noche, tras el precálculo).

Lee panel_campana_semana (Meta y Walcu por semana y campaña, que rehace cada mañana la descarga de Meta) y
escribe:
- panel_prevision_modelo: la posterior de cada tipo (VN, VO) y de cada modelo con campaña;
- panel_prevision_semana: la previsión de las próximas semanas, por semana y en total, para cada escenario
  de gasto de config/prevision.yaml;
- panel_prevision_validacion: la validación hacia atrás (error absoluto medio frente a la media de las
  últimas semanas, y cobertura del intervalo).

Las tres piezas (config/prevision.yaml):
1. Leads por semana según el gasto: regresión bayesiana de log(leads + 1) sobre log(gasto + 1), el mes y el
   régimen, con posterior normal-gamma inversa conjugada (fórmula cerrada). Por tipo; cada modelo parte de la
   posterior de su tipo (agrupamiento parcial): con pocas semanas se queda cerca de ella.
2. Lead a cita: Beta-binomial. El tipo, con sus semanas desde regimen_desde con las citas completas; cada
   modelo, con la media de su tipo valiendo `peso_tipo` leads.
3. Cita a venta: igual, con las semanas con las ventas completas.

Qué entra:
- Las campañas de Meta que casan con leads de Walcu en alguna semana (las demás no tienen leads que medir).
- Las semanas terminadas con carga de Walcu hecha después de terminar la semana (leads completos) y con
  gasto. Una semana con carga de Walcu en que la campaña no casa con ningún lead cuenta 0 leads.
- El gasto de cada campaña se reparte entre VN y VO según sus leads de toda su vida (sin leads, según su
  ámbito; «ambos» o sin regla, a medias). Los leads, citas y ventas ya vienen por tipo.

La previsión cubre las `semanas` semanas desde la del día del cálculo, con el gasto previsto: la media de
las últimas `semanas_gasto` semanas terminadas de cada campaña activa (estado de Meta ACTIVE o, sin estado,
con gasto la última semana). Sin gasto, previsión 0.

Validación (obligatoria): cada una de las últimas `validacion.semanas` semanas con datos completos es un
origen t; se entrena con lo que se sabía al final de t (las citas y ventas de las semanas que ya estaban
completas entonces) y se prevén t+1 … t+semanas, con el gasto que hubo (gasto_real) y con el gasto previsto
como se calcula cada noche (gasto_previsto). Referencia: la media de las últimas `semanas_referencia`
semanas conocidas en t.

Google Trends: solo si hay entregas cargadas, y solo si con ese dato baja el error de los leads en la
validación del tipo. Si no, no se usa y se dice.

Uso:
  python -m analitica.prevision            (make prevision; cron, después del precálculo)
  python -m analitica.prevision --hoy 2026-11-24
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field, replace
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import psycopg
import yaml
from psycopg.types.json import Jsonb

from analitica import predictiva as pd
from ingesta import referencias_meta as ref
from ingesta.concesionario import ErrorConfiguracion

LOG = logging.getLogger("analitica.prevision")
RAIZ = Path(__file__).resolve().parent.parent
TIPOS = ("VN", "VO")
UNA_SEMANA = timedelta(days=7)
VERSION = "prevision-1"
MEDIDAS = ("leads", "citas", "ventas")


# --------------------------------------------------------------------------- configuración

@dataclass(frozen=True)
class Config:
    regimen_desde: date
    semanas: int
    semanas_gasto: int
    intervalo: float
    simulaciones: int
    simulaciones_validacion: int
    semilla: int
    escenarios: tuple[int, ...]
    objetivo_citas: float
    val_semanas: int
    val_referencia: int
    prior_media: dict[str, float]
    prior_sd: dict[str, float]
    a0: float
    b0: float
    inflar: float
    tasa_a: float
    tasa_b: float
    peso_tipo: float
    pocos_semanas: int
    pocos_leads_citas: int
    pocos_citas_ventas: int

    def prior(self, columnas: tuple[str, ...]) -> tuple[np.ndarray, np.ndarray]:
        """(media, V) del prior de β (diagonal)."""
        clave = lambda c: "mes" if c.startswith("mes_") else ("regimen" if c.startswith("regimen") else c)  # noqa: E731
        return (np.array([self.prior_media[clave(c)] for c in columnas]),
                np.diag([self.prior_sd[clave(c)] ** 2 for c in columnas]))


def ruta() -> Path:
    import os
    return Path(os.environ.get("CDF_PREVISION", RAIZ / "config" / "prevision.yaml"))


def cargar_config(fichero: Path | None = None) -> Config:
    f = fichero or ruta()
    cfg = yaml.safe_load(f.read_text(encoding="utf-8")) or {}

    def error(clave: str, que: str):
        return ErrorConfiguracion(f"config/prevision.yaml: {clave} {que}")

    def entero(d: dict, clave: str, nombre: str, minimo: int = 1) -> int:
        v = d.get(clave)
        if isinstance(v, bool) or not isinstance(v, int) or v < minimo:
            raise error(nombre, f"tiene que ser un entero ≥ {minimo}")
        return v

    def real(d: dict, clave: str, nombre: str, positivo: bool = True) -> float:
        v = d.get(clave)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or (positivo and v <= 0):
            raise error(nombre, "tiene que ser un número" + (" positivo" if positivo else ""))
        return float(v)

    regimen = cfg.get("regimen_desde")
    if not isinstance(regimen, date):
        raise error("regimen_desde", "tiene que ser una fecha (AAAA-MM-DD)")
    intervalo = real(cfg, "intervalo", "intervalo")
    if not 0 < intervalo < 1:
        raise error("intervalo", "tiene que estar entre 0 y 1")
    escenarios = cfg.get("escenarios_gasto")
    if not isinstance(escenarios, list) or 0 not in escenarios or any(
            isinstance(e, bool) or not isinstance(e, int) or e <= -100 for e in escenarios):
        raise error("escenarios_gasto", "tiene que ser una lista de % enteros mayores que −100, con el 0")
    val = cfg.get("validacion") or {}
    pr = cfg.get("priors") or {}
    leads = pr.get("leads") or {}
    medias, sds = {}, {}
    for c in ("intercepto", "gasto", "mes", "regimen", "trends"):
        d = leads.get(c) or {}
        medias[c] = real(d, "media", f"priors.leads.{c}.media", positivo=False)
        sds[c] = real(d, "sd", f"priors.leads.{c}.sd")
    tasa = pr.get("tasa_tipo") or {}
    pocos = cfg.get("pocos_datos") or {}
    return Config(
        regimen_desde=regimen, semanas=entero(cfg, "semanas", "semanas"),
        semanas_gasto=entero(cfg, "semanas_gasto", "semanas_gasto"), intervalo=intervalo,
        simulaciones=entero(cfg, "simulaciones", "simulaciones", 100),
        simulaciones_validacion=entero(cfg, "simulaciones_validacion", "simulaciones_validacion", 100),
        semilla=entero(cfg, "semilla", "semilla", 0), escenarios=tuple(sorted(escenarios)),
        objetivo_citas=real(cfg, "objetivo_citas", "objetivo_citas"),
        val_semanas=entero(val, "semanas", "validacion.semanas"),
        val_referencia=entero(val, "semanas_referencia", "validacion.semanas_referencia"),
        prior_media=medias, prior_sd=sds, a0=real(leads, "a", "priors.leads.a"), b0=real(leads, "b", "priors.leads.b"),
        inflar=real(pr, "modelo_inflar", "priors.modelo_inflar"), tasa_a=real(tasa, "a", "priors.tasa_tipo.a"),
        tasa_b=real(tasa, "b", "priors.tasa_tipo.b"), peso_tipo=real(pr, "peso_tipo", "priors.peso_tipo"),
        pocos_semanas=entero(pocos, "semanas", "pocos_datos.semanas"),
        pocos_leads_citas=entero(pocos, "leads_citas", "pocos_datos.leads_citas"),
        pocos_citas_ventas=entero(pocos, "citas_ventas", "pocos_datos.citas_ventas"))


# --------------------------------------------------------------------------- datos

@dataclass
class Semana:
    """Una semana de un grupo (tipo o modelo)."""
    gasto: float = 0.0                 # euros
    leads: int | None = None           # None: sin leads completos de Walcu esa semana
    citas: int | None = None           # None: citas aún incompletas
    ventas: int | None = None          # None: ventas aún incompletas


@dataclass
class Grupo:
    tipo: str
    modelo: str | None                 # None: el tipo entero
    semanas: dict[date, Semana] = field(default_factory=dict)
    gasto_previsto: float = 0.0        # euros por semana
    trends: dict[date, float] = field(default_factory=dict)

    @property
    def clave(self) -> tuple[str, str | None]:
        return self.tipo, self.modelo


@dataclass
class Datos:
    grupos: dict[tuple[str, str | None], Grupo]
    dia_meta: date | None
    ultima_semana: date | None          # última semana con leads completos de Walcu
    dias_cita: int
    dias_venta: int
    campanas: int = 0
    sin_cruce: int = 0
    trends_entregas: int = 0


def lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def leer(pg: psycopg.Connection, hoy: date, cfg: Config) -> Datos:
    cruce = ref.cargar()
    dia = pg.execute("SELECT max(dia) FROM panel_campana_semana WHERE origen = 'real'").fetchone()[0]
    datos = Datos({}, dia, None, cruce.dias_cita, cruce.dias_venta)
    if dia is None:
        return datos
    filas = pg.execute(
        """SELECT semana, campana_id, ambito, tipo_campana, modelo, gasto_cents, walcu, cruce, leads_vn, leads_vo,
                  citas_vn, citas_vo, coalesce(ventas_con_cita_vn, ventas_vn), coalesce(ventas_con_cita_vo, ventas_vo),
                  citas_completas, ventas_completas
           FROM panel_campana_semana WHERE origen = 'real' AND dia = %s AND campana_id IS NOT NULL""",
        (dia,)).fetchall()
    # Semanas con los leads completos: carga de Walcu hecha después de terminar la semana
    cargada = dict(pg.execute(
        "SELECT semana, max((fecha_carga AT TIME ZONE 'Europe/Madrid')::date) FROM walcu_campana_semana_vigente "
        "GROUP BY 1").fetchall())
    leads_ok = {s for s, d in cargada.items() if s + timedelta(days=6) < d and s + timedelta(days=6) <= dia}
    datos.ultima_semana = max(leads_ok) if leads_ok else None
    cruzadas = {f[1] for f in filas if f[7] is not None}
    todas = {f[1] for f in filas}
    datos.campanas, datos.sin_cruce = len(cruzadas), len(todas - cruzadas)
    # Reparto del gasto de cada campaña entre VN y VO
    vida: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    ambito: dict[str, tuple] = {}
    for f in filas:
        ambito[f[1]] = (f[2], f[3], f[4])
        if f[7] is not None:
            vida[f[1]][0] += f[8] or 0
            vida[f[1]][1] += f[9] or 0

    def reparto(cid: str) -> dict[str, float]:
        vn, vo = vida.get(cid, (0, 0))
        if vn + vo:
            return {"VN": vn / (vn + vo), "VO": vo / (vn + vo)}
        a = ambito[cid][0]
        return {"VN": 1.0, "VO": 0.0} if a == "VN" else {"VN": 0.0, "VO": 1.0} if a == "VO" else {"VN": .5, "VO": .5}

    # Campañas activas y su gasto de las últimas semanas terminadas
    estado = dict(pg.execute("SELECT campana_id, estado_efectivo FROM campanas_meta_estado_vigente").fetchall())
    terminadas = sorted({f[0] for f in filas if f[0] + timedelta(days=6) <= dia and f[0] < lunes(hoy)})
    recientes = terminadas[-cfg.semanas_gasto:]
    gasto_cid: dict[tuple[date, str], float] = defaultdict(float)
    for f in filas:
        gasto_cid[(f[0], f[1])] += f[5] / 100
    for f in filas:
        semana, cid, _, tipo_campana, modelo, gasto_c, walcu, _, *walcu_vals, citas_ok, ventas_ok = f
        if cid not in cruzadas:
            continue
        for t in TIPOS:
            sh = reparto(cid)[t]
            claves = [(t, None)] + ([(t, modelo)] if tipo_campana == "modelo" and modelo and sh > 0 else [])
            for clave in claves:
                g = datos.grupos.setdefault(clave, Grupo(*clave))
                s = g.semanas.setdefault(semana, Semana())
                s.gasto += sh * gasto_c / 100
                if semana in leads_ok:
                    i = 0 if t == "VN" else 1
                    lv, cv, vv = walcu_vals[i] or 0, walcu_vals[2 + i] or 0, walcu_vals[4 + i] or 0
                    s.leads = (s.leads or 0) + lv
                    s.citas = (s.citas or 0) + cv if citas_ok else None
                    s.ventas = (s.ventas or 0) + vv if ventas_ok else None
    # Gasto previsto: media de las últimas semanas terminadas de cada campaña activa, repartida
    for cid in cruzadas:
        activa = (estado[cid] == "ACTIVE") if estado.get(cid) else bool(recientes and gasto_cid.get((recientes[-1], cid)))
        if not activa or not recientes:
            continue
        media = sum(gasto_cid.get((s, cid), 0.0) for s in recientes) / len(recientes)
        _, tipo_campana, modelo = ambito[cid]
        for t in TIPOS:
            sh = reparto(cid)[t]
            for clave in [(t, None)] + ([(t, modelo)] if tipo_campana == "modelo" and modelo and sh > 0 else []):
                if clave in datos.grupos:
                    datos.grupos[clave].gasto_previsto += sh * media
    _leer_trends(pg, datos)
    return datos


def _leer_trends(pg, datos: Datos) -> None:
    """Índice semanal de Google Trends de cada grupo (la consulta del modelo; el tipo, la media de sus
    modelos), de la última entrega. Nada si no hay entregas."""
    datos.trends_entregas = pg.execute("SELECT count(*) FROM gtrends_entrega").fetchone()[0]
    if not datos.trends_entregas:
        return
    por_modelo: dict[str, dict[date, float]] = defaultdict(dict)
    for modelo, semana, valor in pg.execute(
            """SELECT modelo, semana, avg(valor) FROM gtrends_semana
               WHERE es_modelo AND fecha_descarga = (SELECT max(fecha_descarga) FROM gtrends_semana)
               GROUP BY 1, 2"""):
        por_modelo[modelo.casefold()][semana] = float(valor)
    for (t, modelo), g in datos.grupos.items():
        if modelo is not None:
            g.trends = dict(por_modelo.get(modelo.casefold(), {}))
    for t in TIPOS:
        if (t, None) not in datos.grupos:
            continue
        modelos = [m for (tt, m) in datos.grupos if tt == t and m and m.casefold() in por_modelo]
        semanas: dict[date, list[float]] = defaultdict(list)
        for m in modelos:
            for s, v in por_modelo[m.casefold()].items():
                semanas[s].append(v)
        datos.grupos[(t, None)].trends = {s: sum(v) / len(v) for s, v in semanas.items()}


# --------------------------------------------------------------------------- piezas del modelo

def ajustar_leads(X: np.ndarray, y: np.ndarray, m0: np.ndarray, V0: np.ndarray, a0: float,
                  b0: float) -> tuple[np.ndarray, np.ndarray, float, float]:
    """Posterior normal-gamma inversa: (media, V, a, b)."""
    V0inv = np.linalg.inv(V0)
    prec = V0inv + X.T @ X
    V = np.linalg.inv(prec)
    V = (V + V.T) / 2
    m = V @ (V0inv @ m0 + X.T @ y)
    a = a0 + len(y) / 2
    b = b0 + 0.5 * float(y @ y + m0 @ V0inv @ m0 - m @ prec @ m)
    return m, V, a, max(b, 1e-9)


@dataclass(frozen=True)
class Ajuste:
    post: pd.Posterior
    semanas: int
    leads_citas: int
    citas_ventas: int
    pocos_datos: bool
    motivos: tuple[str, ...]


def _trends_previo(g: Grupo, semana: date) -> float | None:
    """El índice de la semana anterior (lo que se sabe al empezar la semana); sin él, el último anterior."""
    anteriores = [s for s in g.trends if s < semana]
    return g.trends[max(anteriores)] if anteriores else None


def ajustar(g: Grupo, cfg: Config, hasta: date, dias_cita: int, dias_venta: int, base: Ajuste | None = None,
            usar_trends: bool = False) -> Ajuste | None:
    """Las tres piezas con lo que se sabía al terminar la semana `hasta`. `base`: el ajuste de su tipo (para un
    modelo). None si no hay ninguna semana con leads y gasto."""
    columnas = pd.COLUMNAS + ((pd.TRENDS,) if usar_trends else ())
    fin = hasta + timedelta(days=6)
    filas, y = [], []
    for s, w in sorted(g.semanas.items()):
        if s > hasta or w.leads is None or w.gasto <= 0:
            continue
        tr = _trends_previo(g, s) if usar_trends else None
        if usar_trends and tr is None:
            continue
        filas.append(pd.fila(s, w.gasto, cfg.regimen_desde, columnas, tr))
        y.append(math.log1p(w.leads))
    if not filas:
        return None
    X, y = np.array(filas), np.array(y)
    if base is None:
        m0, V0 = cfg.prior(columnas)
    else:
        m0, V0 = np.array(base.post.media), np.array(base.post.covarianza) * cfg.inflar
    m, V, a, b = ajustar_leads(X, y, m0, V0, cfg.a0, cfg.b0)

    def tasa(num: str, den: str, dias: int) -> tuple[tuple[float, float], int, bool]:
        """Beta de num/den con las semanas completas en `fin` (desde regimen_desde si hay)."""
        def suma(desde):
            n = d = 0
            for s, w in g.semanas.items():
                if s < desde or s + timedelta(days=6 + dias) > fin:
                    continue
                vn, vd = getattr(w, num), getattr(w, den)
                if vn is None or vd is None:
                    continue
                n, d = n + vn, d + vd
            return n, d
        n, d = suma(cfg.regimen_desde)
        solo_regimen = d > 0
        if not solo_regimen:
            n, d = suma(date.min)
        if base is None:
            return (cfg.tasa_a + n, cfg.tasa_b + max(d - n, 0)), d, solo_regimen
        pa, pb = base.post.cita if num == "citas" else base.post.venta
        mu = pa / (pa + pb)
        return (mu * cfg.peso_tipo + n, (1 - mu) * cfg.peso_tipo + max(d - n, 0)), d, solo_regimen

    cita, leads_citas, cita_reg = tasa("citas", "leads", dias_cita)
    venta, citas_ventas, venta_reg = tasa("ventas", "citas", dias_venta)
    trends = _trends_previo(g, hasta + UNA_SEMANA) if usar_trends else None
    post = pd.Posterior(columnas, tuple(m.tolist()), tuple(map(tuple, V.tolist())), a, b, cita, venta, trends)
    motivos = []
    if len(y) < cfg.pocos_semanas:
        motivos.append(f"{len(y)} semanas con leads")
    if leads_citas < cfg.pocos_leads_citas:
        motivos.append(f"{leads_citas} leads con las citas completas")
    if citas_ventas < cfg.pocos_citas_ventas:
        motivos.append(f"{citas_ventas} citas con las ventas completas")
    if not cita_reg or not venta_reg:
        motivos.append(f"sin semanas completas desde {cfg.regimen_desde:%d/%m/%Y}: tasas con todo el histórico")
    return Ajuste(post, len(y), leads_citas, citas_ventas, bool(motivos), tuple(motivos))


# --------------------------------------------------------------------------- validación

def _semanas_validas(g: Grupo, medida: str) -> list[date]:
    return sorted(s for s, w in g.semanas.items() if getattr(w, medida) is not None)


def _conocida(s: date, medida: str, t: date, datos: Datos) -> bool:
    """¿Se sabía en el final de la semana t la cifra de la semana s?"""
    dias = {"leads": 0, "citas": datos.dias_cita, "ventas": datos.dias_venta}[medida]
    return s + timedelta(days=6 + dias) <= t + timedelta(days=6)


def validar(datos: Datos, cfg: Config, usar_trends: dict[str, bool]) -> tuple[list[dict], dict]:
    """Filas de panel_prevision_validacion y el error de leads de cada tipo (para decidir Trends)."""
    if datos.ultima_semana is None:
        return [], {}
    h = cfg.semanas
    ultima = datos.ultima_semana
    origenes = [ultima - (h + k) * UNA_SEMANA for k in range(cfg.val_semanas)][::-1]
    puntos: dict[tuple, list[tuple[float, float, int, int, int]]] = defaultdict(list)
    for t in origenes:
        ajustes: dict[str, Ajuste | None] = {}
        for tipo in TIPOS:
            g = datos.grupos.get((tipo, None))
            ajustes[tipo] = ajustar(g, cfg, t, datos.dias_cita, datos.dias_venta,
                                    usar_trends=usar_trends.get(tipo, False)) if g else None
        for clave, g in sorted(datos.grupos.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
            tipo, modelo = clave
            base = ajustes.get(tipo)
            if base is None:
                continue
            aj = base if modelo is None else ajustar(g, cfg, t, datos.dias_cita, datos.dias_venta, base,
                                                     usar_trends.get(tipo, False))
            if aj is None:
                continue
            objetivos = [t + (j + 1) * UNA_SEMANA for j in range(h)]
            previas = sorted(s for s in g.semanas if s <= t and g.semanas[s].gasto > 0)[-cfg.semanas_gasto:]
            previsto = (sum(g.semanas[s].gasto for s in previas) / cfg.semanas_gasto) if previas else 0.0
            reales = [g.semanas[s].gasto if s in g.semanas else 0.0 for s in objetivos]
            for variante, gasto in (("gasto_real", reales), ("gasto_previsto", [previsto] * h)):
                post = aj.post
                if post.trends is not None:
                    post = replace(post, trends=_trends_previo(g, objetivos[0]))
                sim = pd.simular(post, objetivos, gasto, cfg.regimen_desde, cfg.simulaciones_validacion,
                                 cfg.semilla)
                for medida in MEDIDAS:
                    conocidas = [s for s in _semanas_validas(g, medida) if _conocida(s, medida, t, datos)]
                    ref_media = (sum(getattr(g.semanas[s], medida) for s in conocidas[-cfg.val_referencia:])
                                 / len(conocidas[-cfg.val_referencia:])) if conocidas else None
                    x = getattr(sim, medida)
                    for j, s in enumerate(objetivos):
                        real = getattr(g.semanas[s], medida) if s in g.semanas else (0 if s <= ultima and
                                                                                      medida == "leads" else None)
                        if real is None or ref_media is None:
                            continue
                        lo, med, hi = pd.cuantiles(x[:, j], cfg.intervalo)
                        nivel = "tipo" if modelo is None else "modelo"
                        puntos[(tipo, nivel, medida, variante)].append((med, ref_media, lo, hi, real))
    filas, error_leads = [], {}
    desde, hasta = origenes[0] + UNA_SEMANA, ultima
    for (tipo, nivel, medida, variante), p in sorted(puntos.items()):
        arr = np.array(p, float)
        mae_m = float(np.mean(np.abs(arr[:, 0] - arr[:, 4])))
        mae_r = float(np.mean(np.abs(arr[:, 1] - arr[:, 4])))
        cob = float(np.mean((arr[:, 2] <= arr[:, 4]) & (arr[:, 4] <= arr[:, 3])))
        filas.append({"tipo": tipo, "nivel": nivel, "medida": medida, "variante": variante, "n": len(p),
                      "mae_modelo": round(mae_m, 3), "mae_referencia": round(mae_r, 3), "cobertura": round(cob, 4),
                      "mejora": mae_m < mae_r, "desde_semana": desde, "hasta_semana": hasta})
        if nivel == "tipo" and medida == "leads" and variante == "gasto_real":
            error_leads[tipo] = mae_m
    return filas, error_leads


# --------------------------------------------------------------------------- precálculo

COLS_MODELO = ["dia", "origen", "tipo", "modelo", "columnas", "media", "covarianza", "a", "b", "cita_a", "cita_b",
               "venta_a", "venta_b", "trends", "semanas", "leads_citas", "citas_ventas", "pocos_datos", "motivo",
               "ultima_semana", "primera_semana", "gasto_semanal_cents", "regimen_desde", "simulaciones", "semilla",
               "intervalo", "validada", "validacion", "aviso_gasto"]
COLS_SEMANA = ["dia", "origen", "tipo", "modelo", "semana", "escenario", "gasto_cents", "leads_p10", "leads_p50",
               "leads_p90", "citas_p10", "citas_p50", "citas_p90", "ventas_p10", "ventas_p50", "ventas_p90",
               "modelo_version", "n", "pocos_datos"]
COLS_VALIDACION = ["dia", "origen", "tipo", "nivel", "medida", "variante", "n", "mae_modelo", "mae_referencia",
                   "cobertura", "mejora", "desde_semana", "hasta_semana", "trends"]


def semilla(cfg: Config, tipo: str, modelo: str | None) -> int:
    return pd.semilla_grupo(cfg.semilla, tipo, modelo)


def filas_prevision(clave, post: pd.Posterior, semanas: list[date], gasto: float, cfg: Config, dia: date,
                    n: int, pocos: bool, origen: str = "real") -> list[dict]:
    """Filas de panel_prevision_semana de un grupo: cada escenario, cada semana y el total (semana NULL)."""
    tipo, modelo = clave
    return [{"dia": dia, "origen": origen, "tipo": tipo, "modelo": modelo, **f, "modelo_version": VERSION, "n": n,
             "pocos_datos": pocos}
            for f in pd.filas_semana(post, semanas, gasto, cfg.escenarios, cfg.regimen_desde, cfg.simulaciones,
                                     semilla(cfg, tipo, modelo), cfg.intervalo)]


def _num(x: float) -> str:
    return f"{x:.1f}".replace(".", ",")


def publicacion(validacion: list[dict], tipo: str, nivel: str, intervalo: float) -> dict:
    """{validada, validacion, aviso_gasto} de un tipo o de sus modelos. Se publica si, en la validación, el error
    de los leads con el gasto que hubo es menor que el de la referencia (la media de las últimas semanas): es lo
    que mide el modelo (cuántos leads da un gasto). Con el gasto previsto, si no mejora, se dice aparte."""
    f = {x["variante"]: x for x in validacion if (x["tipo"], x["nivel"], x["medida"]) == (tipo, nivel, "leads")}
    real, previsto = f.get("gasto_real"), f.get("gasto_previsto")
    if real is None:
        return {"validada": False, "aviso_gasto": None,
                "validacion": "Sin validación todavía: hacen falta más semanas con leads de Walcu."}
    cifras = (f"error medio de leads {_num(real['mae_modelo'])} por semana frente a {_num(real['mae_referencia'])} "
              f"de la media de las últimas semanas, en {real['n']} semanas previstas; lo real cayó en el intervalo del "
              f"{round(intervalo * 100)} % el {round(real['cobertura'] * 100)} % de las veces)")
    aviso = None
    if previsto is not None and not previsto["mejora"]:
        aviso = (f"Con el gasto previsto (la media de las últimas semanas), la previsión no mejora a la media de "
                 f"leads ({_num(previsto['mae_modelo'])} frente a {_num(previsto['mae_referencia'])}): el gasto cambia "
                 "mucho de una semana a otra. Mejor mirarla con el escenario de gasto que se vaya a hacer.")
    if not real["mejora"]:
        return {"validada": False, "aviso_gasto": aviso,
                "validacion": f"No se publica: en la validación no mejora a la referencia ({cifras})."}
    return {"validada": True, "aviso_gasto": aviso, "validacion": f"Validación: {cifras}."}


def calcular(pg: psycopg.Connection, hoy: date, cfg: Config | None = None) -> dict:
    inicio = time.monotonic()
    cfg = cfg or cargar_config()
    datos = leer(pg, hoy, cfg)
    resumen: dict = {"dia": str(hoy), "campanas": datos.campanas, "campanas_sin_cruce": datos.sin_cruce,
                     "ultima_semana": str(datos.ultima_semana) if datos.ultima_semana else None}
    with pg.transaction():
        for t in ("panel_prevision_modelo", "panel_prevision_semana", "panel_prevision_validacion"):
            pg.execute(f"DELETE FROM {t} WHERE origen = 'real' AND dia = %s", (hoy,))
        if datos.ultima_semana is None or not datos.grupos:
            resumen["motivo"] = "sin semanas de Meta con leads de Walcu: no se prevé"
            return resumen
        # Google Trends: solo con entregas, y solo si baja el error de los leads en la validación
        sin = {t: False for t in TIPOS}
        validacion, error_sin = validar(datos, cfg, sin)
        usar = dict(sin)
        if datos.trends_entregas:
            con = {t: True for t in TIPOS}
            val_con, error_con = validar(datos, cfg, con)
            for t in TIPOS:
                usar[t] = t in error_con and t in error_sin and error_con[t] < error_sin[t]
            if any(usar.values()):
                validacion = [f for f in val_con if usar[f["tipo"]]] + [f for f in validacion if not usar[f["tipo"]]]
            resumen["trends"] = {t: ("se usa: baja el error de los leads" if usar[t] else "no se usa: no baja el error "
                                     "de los leads en la validación") for t in TIPOS}
        else:
            resumen["trends"] = "sin entregas de Google Trends cargadas: no se usa"
        for f in validacion:
            f.update(dia=hoy, origen="real", trends=usar[f["tipo"]])
        # Regla del proyecto: se publica (el panel la enseña) solo si bate a la referencia
        veredicto = {(t, n): publicacion(validacion, t, n, cfg.intervalo) for t in TIPOS for n in ("tipo", "modelo")}
        # Ajuste con todo y previsión de las próximas semanas
        semanas = [lunes(hoy) + j * UNA_SEMANA for j in range(cfg.semanas)]
        f_modelo, f_semana = [], []
        ajustes: dict[str, Ajuste] = {}
        for clave, g in sorted(datos.grupos.items(), key=lambda kv: (kv[0][0], kv[0][1] is not None, kv[0][1] or "")):
            tipo, modelo = clave
            base = ajustes.get(tipo)
            if modelo is not None and base is None:
                continue
            aj = ajustar(g, cfg, datos.ultima_semana, datos.dias_cita, datos.dias_venta, base, usar[tipo])
            if aj is None:
                continue
            if modelo is None:
                ajustes[tipo] = aj
            elif g.gasto_previsto <= 0 and not any(s >= datos.ultima_semana - 3 * UNA_SEMANA for s in g.semanas):
                continue                                    # modelo sin campaña reciente
            post = aj.post
            if post.trends is not None:
                post = replace(post, trends=_trends_previo(g, semanas[0]))
            f_modelo.append({
                "dia": hoy, "origen": "real", "tipo": tipo, "modelo": modelo, "columnas": Jsonb(list(post.columnas)),
                "media": Jsonb(list(post.media)), "covarianza": Jsonb([list(r) for r in post.covarianza]),
                "a": post.a, "b": post.b, "cita_a": post.cita[0], "cita_b": post.cita[1], "venta_a": post.venta[0],
                "venta_b": post.venta[1], "trends": post.trends, "semanas": aj.semanas,
                "leads_citas": aj.leads_citas, "citas_ventas": aj.citas_ventas, "pocos_datos": aj.pocos_datos,
                "motivo": "; ".join(aj.motivos) or None, "ultima_semana": datos.ultima_semana,
                "primera_semana": semanas[0], "gasto_semanal_cents": round(g.gasto_previsto * 100),
                "regimen_desde": cfg.regimen_desde, "simulaciones": cfg.simulaciones,
                "semilla": semilla(cfg, tipo, modelo), "intervalo": cfg.intervalo,
                **veredicto[(tipo, "tipo" if modelo is None else "modelo")]})
            f_semana += filas_prevision(clave, post, semanas, g.gasto_previsto, cfg, hoy, aj.semanas, aj.pocos_datos)
        for tabla, cols, filas in (("panel_prevision_modelo", COLS_MODELO, f_modelo),
                                   ("panel_prevision_semana", COLS_SEMANA, f_semana),
                                   ("panel_prevision_validacion", COLS_VALIDACION, validacion)):
            if filas:
                with pg.cursor() as cur:
                    cur.executemany(f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                                    [tuple(f[c] for c in cols) for f in filas])
    resumen.update(grupos=len(f_modelo), filas_semana=len(f_semana), filas_validacion=len(validacion),
                   publicada={f"{t} {n}": veredicto[(t, n)]["validada"] for t, n in veredicto},
                   leads_mejora={f"{f['tipo']} {f['nivel']} {f['variante']}": f["mejora"] for f in validacion
                                 if f["medida"] == "leads"},
                   segundos=round(time.monotonic() - inicio, 1))
    return resumen


# --------------------------------------------------------------------------- línea de órdenes

def ejecutar(hoy: date | None = None) -> int:
    from ingesta.db import conectar
    from ingesta.scrapers_vw import hoy_madrid
    hoy = hoy or hoy_madrid()
    try:
        with conectar(autocommit=True) as pg:
            try:
                resumen = calcular(pg, hoy)
                estado = "ok"
            except Exception as exc:
                resumen, estado = {"error": f"{type(exc).__name__}: {exc}"[:500]}, "error"
            pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) VALUES ('panel_prevision', "
                       "'precalculo', %s, %s, %s)", (hoy, estado, Jsonb(resumen)))
    except Exception as exc:
        LOG.error("Previsión fallida: %s", exc)
        return 1
    (LOG.info if estado == "ok" else LOG.error)("Previsión: %s", resumen)
    return 0 if estado == "ok" else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--hoy", type=date.fromisoformat, help="día del cálculo (por defecto, hoy)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return ejecutar(args.hoy)


if __name__ == "__main__":
    sys.exit(main())
