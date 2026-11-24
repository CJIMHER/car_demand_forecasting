"""Previsión de leads, citas y ventas a partir de una posterior ya entrenada (analitica/prevision.py).

Lo usan el precálculo (previsión guardada y validación) y el panel (escenarios de gasto y «para conseguir N
citas»), sin volver a entrenar: con la misma posterior, el mismo gasto y la misma semilla, salen las mismas
cifras. Solo numpy.

Leads: log(leads + 1) = x·β + ε, con β | σ² ~ N(media, σ²·V) y σ² ~ Gamma inversa(a, b) (posterior
normal-gamma inversa). Cada simulación saca σ², los x·β de las semanas (correlados: comparten β) y el ruido
de cada semana; leads = redondeo de exp(y) − 1. Una semana sin gasto, 0 leads. Citas ~ Binomial(leads, p) con
p ~ Beta (lead a cita) y ventas ~ Binomial(citas, q) con q ~ Beta (cita a venta), una tasa por simulación.

Columnas del diseño (en este orden; trends solo si se usa):
  intercepto, gasto (log(euros + 1)), mes_1 … mes_12, regimen (1 antes de regimen_desde),
  regimen_gasto (regimen · gasto), trends (log(1 + índice de Google Trends de la semana anterior)).
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import yaml

MESES = tuple(f"mes_{m}" for m in range(1, 13))
COLUMNAS = ("intercepto", "gasto", *MESES, "regimen", "regimen_gasto")
TRENDS = "trends"
LOG_MAX = math.log(1e6)          # tope de log(leads + 1): un millón de leads a la semana


def mes_de(semana: date) -> int:
    """El mes de una semana ISO: el de su jueves."""
    return (semana + timedelta(days=3)).month


def fila(semana: date, gasto_euros: float, regimen_desde: date, columnas: tuple[str, ...],
         trends: float | None = None) -> list[float]:
    lg = math.log1p(max(gasto_euros, 0.0))
    antes = 1.0 if semana < regimen_desde else 0.0
    valores = {"intercepto": 1.0, "gasto": lg, "regimen": antes, "regimen_gasto": antes * lg,
               TRENDS: math.log1p(trends) if trends is not None else 0.0}
    m = mes_de(semana)
    valores.update({f"mes_{i}": 1.0 if i == m else 0.0 for i in range(1, 13)})
    return [valores[c] for c in columnas]


@dataclass(frozen=True)
class Posterior:
    """Lo que se guarda de cada tipo o modelo (panel_prevision_modelo)."""
    columnas: tuple[str, ...]
    media: tuple[float, ...]
    covarianza: tuple[tuple[float, ...], ...]     # V: la de β es σ²·V
    a: float
    b: float
    cita: tuple[float, float]                      # Beta(α, β) de lead a cita
    venta: tuple[float, float]                     # Beta(α, β) de cita a venta
    trends: float | None = None                    # último índice de Trends (si se usa)
    extra: dict = field(default_factory=dict, compare=False)


@dataclass(frozen=True)
class Simulacion:
    leads: np.ndarray          # simulaciones × semanas
    citas: np.ndarray
    ventas: np.ndarray


def simular(post: Posterior, semanas: list[date], gasto: list[float], regimen_desde: date, sims: int,
            semilla: int) -> Simulacion:
    """Leads, citas y ventas simulados de cada semana con ese gasto (euros por semana)."""
    rng = np.random.default_rng(semilla)
    k = len(semanas)
    X = np.array([fila(s, g, regimen_desde, post.columnas, post.trends) for s, g in zip(semanas, gasto)], float)
    m, V = np.array(post.media, float), np.array(post.covarianza, float)
    mu = X @ m
    S = X @ V @ X.T
    L = np.linalg.cholesky(S + 1e-9 * np.eye(k))
    # Siempre los mismos números aleatorios, en el mismo orden: un escenario solo cambia el gasto
    sigma2 = post.b / rng.gamma(post.a, 1.0, sims)
    z = rng.standard_normal((sims, k))
    e = rng.standard_normal((sims, k))
    u_cita = rng.beta(*post.cita, sims)
    u_venta = rng.beta(*post.venta, sims)
    sd = np.sqrt(sigma2)[:, None]
    y = np.clip(mu + sd * (z @ L.T) + sd * e, None, LOG_MAX)
    leads = np.maximum(0, np.rint(np.expm1(y))).astype(np.int64)
    leads[:, np.array(gasto, float) <= 0] = 0
    citas = rng.binomial(leads, u_cita[:, None])
    ventas = rng.binomial(citas, u_venta[:, None])
    return Simulacion(leads, citas, ventas)


def cuantiles(x: np.ndarray, intervalo: float) -> tuple[int, int, int]:
    """(bajo, mediana, alto) del intervalo central; enteros (son recuentos)."""
    lo = (1 - intervalo) / 2
    q = np.quantile(x, [lo, 0.5, 1 - lo], method="inverted_cdf")
    return int(q[0]), int(q[1]), int(q[2])


def resumen(sim: Simulacion, intervalo: float) -> dict:
    """Por semana y el total: {medida: [(p10, p50, p90) de cada semana], medida_total: (p10, p50, p90)}."""
    salida = {}
    for medida in ("leads", "citas", "ventas"):
        x = getattr(sim, medida)
        salida[medida] = [cuantiles(x[:, j], intervalo) for j in range(x.shape[1])]
        salida[f"{medida}_total"] = cuantiles(x.sum(axis=1), intervalo)
    return salida


def gasto_para_citas(post: Posterior, semanas: list[date], objetivo: int, regimen_desde: date, sims: int,
                     semilla: int, intervalo: float, maximo: float) -> dict | None:
    """El gasto semanal (el mismo cada semana) con el que las citas de todas las semanas llegan a `objetivo`:
    en la mediana, y el intervalo (con suerte: el percentil alto llega; sin ella: el bajo llega). None si ni
    con `maximo` € a la semana la mediana llega."""
    lo = (1 - intervalo) / 2

    def citas(g: float) -> np.ndarray:
        return simular(post, semanas, [g] * len(semanas), regimen_desde, sims, semilla).citas.sum(axis=1)

    def buscar(q: float) -> float | None:
        if np.quantile(citas(maximo), q, method="inverted_cdf") < objetivo:
            return None
        a, b = 0.0, maximo
        if np.quantile(citas(a), q, method="inverted_cdf") >= objetivo:
            return 0.0
        for _ in range(40):                     # bisección sobre log(gasto + 1): precisión de céntimos
            c = math.expm1((math.log1p(a) + math.log1p(b)) / 2)
            if np.quantile(citas(c), q, method="inverted_cdf") >= objetivo:
                b = c
            else:
                a = c
            if b - a < 1:
                break
        return b

    mediana = buscar(0.5)
    if mediana is None:
        return None
    return {"mediana": mediana, "bajo": buscar(1 - lo), "alto": buscar(lo)}


def semilla_grupo(base: int, tipo: str, modelo: str | None) -> int:
    """Una semilla fija por grupo: la misma en el precálculo y en el panel."""
    texto = f"{tipo}|{modelo or ''}"
    return base * 1_000_003 + sum((i + 1) * ord(c) for i, c in enumerate(texto))


def filas_semana(post: Posterior, semanas: list[date], gasto: float, escenarios, regimen_desde: date, sims: int,
                 semilla: int, intervalo: float) -> list[dict]:
    """La previsión de un grupo para cada escenario (% sobre `gasto`, euros por semana), por semana y en total
    (semana None): gasto_cents y p10, p50 y p90 de leads, citas y ventas."""
    filas = []
    for esc in escenarios:
        g = gasto * (1 + esc / 100)
        r = resumen(simular(post, semanas, [g] * len(semanas), regimen_desde, sims, semilla), intervalo)
        for j, s in enumerate(list(semanas) + [None]):
            q = {m: (r[f"{m}_total"] if s is None else r[m][j]) for m in ("leads", "citas", "ventas")}
            filas.append({"semana": s, "escenario": esc,
                          "gasto_cents": round(g * 100 * (len(semanas) if s is None else 1)),
                          **{f"{m}_p{p}": q[m][i] for m in q for i, p in enumerate((10, 50, 90))}})
    return filas


@dataclass(frozen=True)
class Panel:
    """Lo que el panel necesita de config/prevision.yaml (el resto viaja con la posterior)."""
    escenarios: tuple[int, ...]
    objetivo_citas: float
    gasto_maximo: float          # la búsqueda del gasto para N citas llega hasta este múltiplo del previsto


def parametros_panel(fichero: Path | None = None) -> Panel:
    raiz = Path(__file__).resolve().parent.parent
    f = fichero or Path(os.environ.get("CDF_PREVISION", raiz / "config" / "prevision.yaml"))
    cfg = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
    return Panel(tuple(sorted(cfg.get("escenarios_gasto") or [0])), float(cfg.get("objetivo_citas") or 1.5),
                 float(cfg.get("objetivo_gasto_maximo") or 20))
