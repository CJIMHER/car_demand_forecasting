"""Contrato de datos del panel (docs/contrato_panel.md): qué tabla lee cada bloque y de qué origen.

Regla «un bloque no mezcla orígenes»: el origen de un bloque es 'real' en cuanto cualquiera de sus
tablas tiene una fila real; si no, 'ejemplo' si alguna tiene filas de ejemplo; si no, ninguno
(«Sin datos todavía»). Todas las tablas del bloque se leen después SOLO con ese origen. Así, cuando
entra la primera fila real de una tabla, sus filas de ejemplo (y las de las demás tablas del mismo
bloque) dejan de mostrarse, y conectar una fuente es sustituir filas sin tocar las vistas.

El estado de una vista sale de sus bloques: 'ejemplo' si alguno es de ejemplo, 'real' si alguno es
real y ninguno de ejemplo, 'vacia' si no hay filas. Quién ve cada estado lo decide permisos.py.
"""

from __future__ import annotations

from dataclasses import dataclass

import psycopg
from psycopg import sql

REAL, EJEMPLO = "real", "ejemplo"

# Tablas del contrato (migración 0005). Las panel_mercado_* no están: son siempre reales.
TABLAS = (
    "panel_stock_vehiculo", "panel_ventas_mes", "panel_alertas", "panel_riesgo_vehiculo",
    "panel_curva_grupo", "panel_leads_dia", "panel_campana_dia", "panel_campana_salud",
    "panel_recomendacion_modelo", "panel_atencion_relativa", "panel_semana_campana",
    "panel_campana_resultado",
)

# Bloques de cada vista y las tablas que leen
BLOQUES: dict[str, dict[str, tuple[str, ...]]] = {
    "direccion": {
        "stock": ("panel_stock_vehiculo",),
        "antiguedad": ("panel_stock_vehiculo",),
        "ventas_kpi": ("panel_ventas_mes",),
        "alertas_kpi": ("panel_alertas",),
        "ventas_mes": ("panel_ventas_mes",),
        "alertas": ("panel_alertas",),
        "edad": ("panel_stock_vehiculo",),
    },
    "ventas": {
        "resumen": ("panel_riesgo_vehiculo",),
        "lista": ("panel_riesgo_vehiculo",),
        "detalle": ("panel_riesgo_vehiculo", "panel_curva_grupo"),
    },
    "marketing": {
        "kpis": ("panel_campana_dia", "panel_leads_dia"),
        "recomendacion": ("panel_recomendacion_modelo",),
        "campanas": ("panel_campana_dia", "panel_leads_dia", "panel_campana_salud"),
        "atencion": ("panel_atencion_relativa",),
        "semanas": ("panel_semana_campana",),
        "pasadas": ("panel_campana_resultado",),
    },
}
VISTAS = tuple(BLOQUES)


@dataclass(frozen=True)
class Modo:
    real: bool
    ejemplo: bool


def modos(pg: psycopg.Connection) -> dict[str, Modo]:
    """Para cada tabla del contrato, si tiene filas reales y si tiene filas de ejemplo (una consulta)."""
    partes = [sql.SQL("SELECT {nombre}, EXISTS (SELECT 1 FROM {t} WHERE origen = 'real'), "
                      "EXISTS (SELECT 1 FROM {t} WHERE origen = 'ejemplo')").format(
                          nombre=sql.Literal(t), t=sql.Identifier(t)) for t in TABLAS]
    return {t: Modo(r, e) for t, r, e in pg.execute(sql.SQL(" UNION ALL ").join(partes))}


def origen_bloque(m: dict[str, Modo], tablas: tuple[str, ...]) -> str | None:
    if any(m[t].real for t in tablas):
        return REAL
    if any(m[t].ejemplo for t in tablas):
        return EJEMPLO
    return None


def origenes(m: dict[str, Modo], vista: str) -> dict[str, str | None]:
    return {bloque: origen_bloque(m, tablas) for bloque, tablas in BLOQUES[vista].items()}


def estado_vista(m: dict[str, Modo], vista: str) -> str:
    o = set(origenes(m, vista).values())
    if EJEMPLO in o:
        return "ejemplo"
    return "real" if REAL in o else "vacia"


def estados(m: dict[str, Modo]) -> dict[str, str]:
    return {v: estado_vista(m, v) for v in VISTAS}
