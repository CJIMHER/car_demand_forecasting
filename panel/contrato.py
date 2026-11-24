"""Contrato de datos del panel (docs/contrato_panel.md): qué tabla lee cada bloque y de qué origen.

Regla «un bloque no mezcla orígenes»: el origen de un bloque es 'real' en cuanto cualquiera de sus
tablas tiene una fila real; si no, 'ejemplo' si alguna tiene filas de ejemplo; si no, ninguno
(«Sin datos todavía»). Todas las tablas del bloque se leen después SOLO con ese origen. Así, cuando
entra la primera fila real de una tabla, sus filas de ejemplo (y las de las demás tablas del mismo
bloque) dejan de mostrarse, y conectar una fuente es sustituir filas sin tocar las vistas.

Dirección no mezcla nunca: fuera del modo ejemplo explícito (PANEL_MODO_EJEMPLO=1), sus bloques solo
leen filas reales y, sin ellas, dicen «Pendiente de conectar: <fuente>» o «Sin datos todavía»
(VISTAS_SOLO_REAL; panel/direccion.py).

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
    "panel_campana_resultado", "panel_trafico_canal_dia", "panel_campana_semana", "panel_referencia_meta",
    "panel_campana_otra_marca", "panel_precio_vo", "panel_salidas_stock",
    "panel_prevision_modelo", "panel_prevision_semana", "panel_ventas_crm",
)

# Tablas del motor de alertas (migración 0009) con el mismo formato (dia, origen, calculado_at). No
# son del contrato de datos de ejemplo (make ejemplo-cargar no las toca): las escribe alertas/motor.py.
# alertas_evaluacion dice qué reglas se han evaluado y con qué origen; con una fila real, el bloque
# de alertas es real aunque no salte ninguna, y las alertas se leen de la tabla alertas.
# panel_campana_decision: la decisión de cada campaña según los criterios de Meta (alertas/criterios.py).
MOTOR = ("alertas_evaluacion", "panel_campana_decision")
ALERTAS = ("alertas_evaluacion", "panel_alertas")

# Bloques de cada vista y las tablas que leen
BLOQUES: dict[str, dict[str, tuple[str, ...]]] = {
    "direccion": {
        "stock": ("panel_stock_vehiculo",),
        "antiguedad": ("panel_stock_vehiculo",),
        # Ventas del mes: las del CRM (ingesta/walcu_crm.py) o, sin ellas, las salidas de nuestro stock (estimación)
        "ventas_kpi": ("panel_ventas_crm", "panel_salidas_stock"),
        "ventas_crm": ("panel_ventas_crm",),        # ventas del CRM por mes, tipo y modelo
        "alertas_kpi": ALERTAS,
        "ventas_mes": ("panel_ventas_mes",),
        "alertas": ALERTAS,
        "edad": ("panel_stock_vehiculo",),
        "salidas": ("panel_salidas_stock",),        # salidas por mes, tipo y modelo (analitica/salidas.py)
        "precio": ("panel_precio_vo",),             # precio provisional de nuestros VO (el mismo de Ventas)
        "marketing": ("panel_campana_semana", "panel_campana_decision"),   # Meta y Walcu, últimas 4 semanas
        "referencias": ("panel_referencia_meta",),  # 2025 frente a 2026
        "trafico": ("panel_trafico_canal_dia",),    # GA4 por canal
        "prevision": ("panel_prevision_modelo", "panel_prevision_semana"),   # ventas de Meta, próximas 4 semanas
    },
    "ventas": {
        "resumen": ("panel_riesgo_vehiculo",),
        "lista": ("panel_riesgo_vehiculo",),
        "detalle": ("panel_riesgo_vehiculo", "panel_curva_grupo"),
        "alertas": ALERTAS,
        "precio": ("panel_precio_vo",),             # precio provisional de nuestros VO (analitica/precio.py)
        "salidas": ("panel_salidas_stock",),        # salidas de las últimas 4 semanas, un coche por fila
        "stock_crm": ("panel_stock_vehiculo",),     # stock VO completo del CRM, con su fecha de entrada real
    },
    "marketing": {
        "kpis": ("panel_campana_dia", "panel_leads_dia"),
        "recomendacion": ("panel_recomendacion_modelo",),
        "campanas": ("panel_campana_dia", "panel_campana_semana", "panel_campana_salud", "panel_campana_decision",
                     "panel_campana_otra_marca"),     # otras marcas: solo la línea de debajo
        "atencion": ("panel_atencion_relativa",),
        "ocasion": ("panel_riesgo_vehiculo",),      # solo en VO: stock de ocasión que mover
        "semanas": ("panel_semana_campana",),
        "pasadas": ("panel_campana_resultado",),
        "trafico": ("panel_trafico_canal_dia",),    # tráfico de la web por canal (GA4)
        "mes": ("panel_campana_semana", "panel_campana_decision"),   # últimas 4 semanas: Meta y Walcu por campaña
        "referencias": ("panel_referencia_meta",),  # referencias históricas de coste (por año)
        "prevision": ("panel_prevision_modelo",),   # previsión 4 semanas, con escenarios de gasto (analitica/prevision.py)
        "alertas": ALERTAS,
    },
}
VISTAS = tuple(BLOQUES)
# Vistas que, fuera del modo ejemplo explícito, nunca muestran filas de ejemplo
VISTAS_SOLO_REAL = ("direccion",)


@dataclass(frozen=True)
class Modo:
    real: bool
    ejemplo: bool


def modos(pg: psycopg.Connection) -> dict[str, Modo]:
    """Para cada tabla del contrato (y alertas_evaluacion), si tiene filas reales y si tiene filas de
    ejemplo (una consulta)."""
    partes = [sql.SQL("SELECT {nombre}, EXISTS (SELECT 1 FROM {t} WHERE origen = 'real'), "
                      "EXISTS (SELECT 1 FROM {t} WHERE origen = 'ejemplo')").format(
                          nombre=sql.Literal(t), t=sql.Identifier(t)) for t in TABLAS + MOTOR]
    return {t: Modo(r, e) for t, r, e in pg.execute(sql.SQL(" UNION ALL ").join(partes))}


def origen_bloque(m: dict[str, Modo], tablas: tuple[str, ...], ejemplo: bool = True) -> str | None:
    """`ejemplo`=False: solo real o nada (nunca filas de ejemplo)."""
    if any(m[t].real for t in tablas):
        return REAL
    if ejemplo and any(m[t].ejemplo for t in tablas):
        return EJEMPLO
    return None


def origenes(m: dict[str, Modo], vista: str, ejemplo: bool = True) -> dict[str, str | None]:
    return {bloque: origen_bloque(m, tablas, ejemplo) for bloque, tablas in BLOQUES[vista].items()}


def estado_vista(m: dict[str, Modo], vista: str, ejemplo: bool = True) -> str:
    o = set(origenes(m, vista, ejemplo).values())
    if EJEMPLO in o:
        return "ejemplo"
    return "real" if REAL in o else "vacia"


def estados(m: dict[str, Modo], modo_ejemplo: bool = True) -> dict[str, str]:
    """Sin el modo ejemplo, las VISTAS_SOLO_REAL se juzgan solo por sus filas reales."""
    return {v: estado_vista(m, v, modo_ejemplo or v not in VISTAS_SOLO_REAL) for v in VISTAS}
