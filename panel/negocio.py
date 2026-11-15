"""Utilidades comunes de las vistas Dirección, Ventas y Marketing: lectura por bloque y textos.

Cada bloque se lee con el origen que le da panel/contrato.py (nunca mezcla real y ejemplo) y del
último día que tiene ese origen en su tabla. Sin filas, el bloque dice «Sin datos todavía».
"""

from __future__ import annotations

from datetime import date

import psycopg
from psycopg import sql

from panel import formato as fmt

TEMAS = {"ventas": "Ventas", "stock": "Stock", "campanas": "Campañas", "mercado": "Mercado", "datos": "Datos"}
DESTINATARIOS = {"direccion": "dirección", "ventas": "jefe de ventas", "marketing": "marketing", "tecnico": "técnico"}
TIPOS = {"todo": "Todo", "VN": "Nuevos (VN)", "VO": "Ocasión (VO)"}


def ultimo_dia(pg: psycopg.Connection, tabla: str, origen: str | None) -> date | None:
    if origen is None:
        return None
    return pg.execute(sql.SQL("SELECT max(dia) FROM {} WHERE origen = %s").format(sql.Identifier(tabla)),
                      (origen,)).fetchone()[0]


def bloque(origen: str | None, **datos) -> dict:
    """Un bloque de la vista: su origen ('real', 'ejemplo' o None) y, si hay filas, sus datos."""
    return {"origen": origen, "hay": bool(datos.pop("hay", origen is not None)), **datos}


def lista_y(textos: list[str]) -> str:
    if len(textos) <= 1:
        return "".join(textos)
    return ", ".join(textos[:-1]) + " y " + textos[-1]


def destinatarios(roles: list[str]) -> str:
    return lista_y([DESTINATARIOS.get(r, r) for r in roles])


def selector_tipo(ruta: str, actual: str, objetivo: str, extra: str = "") -> list[dict]:
    return [{"id": t, "etiqueta": e, "activa": t == actual, "url": f"{ruta}?tipo={t}{extra}", "objetivo": objetivo}
            for t, e in TIPOS.items()]


def signo_pct(x: float | None, decimales: int = 1) -> str:
    """+4,8 % / −1,0 % / — (sin dato)."""
    return fmt.porcentaje(x, decimales, signo=True) if x is not None else fmt.GUION


def intervalo_pct(p10: float, p90: float) -> str:
    return f"{round(p10 * 100)}–{round(p90 * 100)} %"


def cambio(actual: int, previo: int | None) -> str:
    """«un 20 % menos», «un 12 % más», «estables»; sin base, nada."""
    if not previo:
        return ""
    d = (actual - previo) / previo * 100
    if abs(d) < 5:
        return "estables"
    return f"un {fmt.decimal(abs(d), 0)} % {'más' if d > 0 else 'menos'}"
