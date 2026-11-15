"""Números y fechas en formato español, horas en Madrid."""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

MADRID = ZoneInfo("Europe/Madrid")
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre",
         "octubre", "noviembre", "diciembre")
GUION = "—"


def miles(n) -> str:
    """3136 → «3.136» (como el prototipo: punto de millar también con cuatro cifras)."""
    if n is None:
        return GUION
    entero = round(n)
    texto = f"{abs(entero):,}".replace(",", ".")
    return ("−" if entero < 0 else "") + texto


def decimal(x: float, decimales: int = 1) -> str:
    texto = f"{abs(x):,.{decimales}f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("−" if x < 0 and round(abs(x), decimales) else "") + texto


def euros(cents) -> str:
    return GUION if cents is None else f"{miles(round(cents / 100))} €"


def porcentaje(x: float | None, decimales: int = 0, signo: bool = False) -> str:
    if x is None:
        return GUION
    texto = decimal(x, decimales)
    if signo and round(x, decimales) > 0:
        texto = "+" + texto
    return f"{texto} %"


def fecha_corta(d: date | None) -> str:
    return GUION if d is None else d.strftime("%d/%m")


def fecha(d: date | None) -> str:
    return GUION if d is None else d.strftime("%d/%m/%Y")


def fecha_larga(d: date) -> str:
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


def hora(t: datetime | None) -> str:
    return GUION if t is None else t.astimezone(MADRID).strftime("%H:%M")


def fecha_hora(t: datetime | None) -> str:
    return GUION if t is None else t.astimezone(MADRID).strftime("%d/%m/%Y %H:%M")


def hoy() -> date:
    return datetime.now(MADRID).date()
