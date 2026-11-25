"""Reparto de las alertas por destinatario: «nueva» y «ya enviada» se deciden por rol.

Cada alerta de campaña nueva va a marketing (texto limpio) y a técnico (detalle). Que una ya abierta para marketing
no se repita no depende de lo que haya recibido el técnico, ni al revés. Reproduce el 25-11: las reglas de campaña
pasaron de ir solo a marketing a ir también al técnico (con el detalle), y el cansancio (regla f) de una campaña
de Taigo era nuevo ese día.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

from alertas import envio, motor
from test_alertas import reglas
from test_criterios_meta import HOY, META, campana, conc  # noqa: F401

AYER = HOY - timedelta(days=1)
ENTORNO = {"TELEGRAM_CHAT_ID": "chat-tecnico", "TELEGRAM_CHAT_MARKETING": "chat-marketing"}


def _solo_marketing():
    """Las reglas de campaña como estaban antes del 25-11: solo para marketing."""
    return [dataclasses.replace(r, roles=("marketing",)) for r in reglas(*META)]


def _enviar(pg, dia):
    enviados: dict[str, str] = {}

    def enviar(texto, chat_id):
        enviados[chat_id] = texto
        return 1
    envio.enviar_dia(pg, dia, enviar=enviar, entorno=ENTORNO)
    return enviados


def _mala(pg):
    """Salta ayer y hoy: lead a cita (a) y lead caro (c)."""
    campana(pg, "MALA", dias=3, leads_por_dia=[0, 40, 0], citas=1, gasto=80_000, modelo="T-Roc",
            titulo="T-Roc noviembre")


def _taigo(pg):
    """Cansancio (regla f): 7 días a 10 leads y 3 a 7, con el lead de esos 3 días por encima de 10 €."""
    campana(pg, "TAIGO", dias=10, leads_por_dia=[10] * 7 + [7] * 3, gasto_por_dia=[100] * 7 + [7_007] * 3,
            citas=30, modelo="Taigo", titulo="VW [VN][2026] Taigo noviembre")


def _por(pg, dia, rol):
    return {(r, c): e for r, c, e in pg.execute(
        "SELECT regla, clave, estado FROM alertas WHERE fecha = %s AND rol = %s", (dia, rol))}


def test_una_alerta_nueva_va_a_marketing_y_a_tecnico_cada_uno_por_su_lado(pg, conc):
    # Ayer: reglas solo para marketing; la campaña MALA salta (a y c) y se le envía
    _mala(pg)
    motor.evaluar(pg, AYER, conc, _solo_marketing())
    ayer = _enviar(pg, AYER)
    assert set(ayer) == {"chat-marketing"} and "T-Roc noviembre" in ayer["chat-marketing"]
    # Hoy: las reglas van también al técnico, y el cansancio de Taigo es nuevo
    _taigo(pg)
    motor.evaluar(pg, HOY, conc, reglas(*META))
    mk, tec = _por(pg, HOY, "marketing"), _por(pg, HOY, "tecnico")
    assert mk[("meta_f", "TAIGO")] == "nueva" and mk[("meta_c", "MALA")] == "abierta"     # para marketing
    assert tec[("meta_f", "TAIGO")] == "nueva" and tec[("meta_c", "MALA")] == "nueva"      # para el técnico, todas
    hoy = _enviar(pg, HOY)
    # Marketing: solo lo nuevo para él (el cansancio de Taigo), con texto limpio; lo ya abierto no se repite
    assert "VW [VN][2026] Taigo noviembre" in hoy["chat-marketing"] and "T-Roc noviembre" not in hoy["chat-marketing"]
    assert "regla" not in hoy["chat-marketing"] and "umbral" not in hoy["chat-marketing"]
    assert "señales de cansancio" in hoy["chat-marketing"]
    # Técnico: todas, nuevas para él, con el detalle
    assert "T-Roc noviembre" in hoy["chat-tecnico"] and "Taigo noviembre" in hoy["chat-tecnico"]
    assert "(regla f)" in hoy["chat-tecnico"] and "umbral" in hoy["chat-tecnico"]
    # Al día siguiente, sin cambios, nadie recibe nada otra vez
    motor.evaluar(pg, HOY + timedelta(days=1), conc, reglas(*META))
    assert _enviar(pg, HOY + timedelta(days=1)) == {}


def test_lo_que_recibe_el_tecnico_no_cuenta_para_marketing_ni_al_reves(pg, conc):
    _mala(pg)
    motor.evaluar(pg, HOY, conc, reglas(*META))
    from ingesta import telegram

    def falla_tecnico(texto, chat_id):
        if chat_id == "chat-tecnico":
            raise telegram.ErrorTelegram("fallo")
        return 1
    envio.enviar_dia(pg, HOY, enviar=falla_tecnico, entorno=ENTORNO)
    # A marketing le llegó: no queda pendiente; al técnico no: sigue pendiente, aunque marketing ya la tenga
    assert envio.pendientes(pg, HOY, "marketing") == []
    assert {p["regla"] for p in envio.pendientes(pg, HOY, "tecnico")} >= {"meta_a", "meta_c"}
    # Y al revés, con una alerta nueva (el cansancio de Taigo): falla el envío a marketing y llega al técnico
    _taigo(pg)
    motor.evaluar(pg, HOY, conc, reglas(*META))

    def falla_marketing(texto, chat_id):
        if chat_id == "chat-marketing":
            raise telegram.ErrorTelegram("fallo")
        return 1
    envio.enviar_dia(pg, HOY, enviar=falla_marketing, entorno=ENTORNO)
    pendientes = {(p["regla"], p["clave"]) for p in envio.pendientes(pg, HOY, "marketing")}
    assert ("meta_f", "TAIGO") in pendientes and {c for _, c in pendientes} == {"TAIGO"}
    assert envio.pendientes(pg, HOY, "tecnico") == []


def test_reejecutar_el_mismo_dia_tras_cambiar_los_destinatarios(pg, conc):
    """Lo del 25-11: el envío de las 09:15 (reglas solo para marketing) ya le había mandado a marketing el
    cansancio de Taigo; al volver a ejecutar las alertas con las reglas nuevas, el técnico las recibe todas por
    primera vez y marketing solo lo que no tenía (la campaña general, regla nueva)."""
    _mala(pg)
    motor.evaluar(pg, AYER, conc, _solo_marketing())
    _enviar(pg, AYER)
    _taigo(pg)
    motor.evaluar(pg, HOY, conc, _solo_marketing())                 # 09:15, antes del despliegue
    temprano = _enviar(pg, HOY)
    assert "Taigo noviembre" in temprano["chat-marketing"] and "chat-tecnico" not in temprano
    motor.evaluar(pg, HOY, conc, reglas(*META))                     # después, a mano, con las reglas nuevas
    assert [p["clave"] for p in envio.pendientes(pg, HOY, "marketing")] == []
    assert {(p["regla"], p["clave"]) for p in envio.pendientes(pg, HOY, "tecnico")} >= {
        ("meta_f", "TAIGO"), ("meta_c", "MALA"), ("meta_a", "MALA")}


def test_sin_telegram_dice_que_no_se_repite_y_por_que(pg, conc, capsys):
    _mala(pg)
    motor.evaluar(pg, AYER, conc, _solo_marketing())
    _enviar(pg, AYER)
    _taigo(pg)
    motor.evaluar(pg, HOY, conc, reglas(*META))
    envio.enviar_dia(pg, HOY, entorno=ENTORNO, sin_telegram=True)
    salida = capsys.readouterr().out
    assert "--- marketing: " in salida and "meta_c MALA el 19/11" in salida      # abierta, ya enviada ayer
    assert "--- marketing (iría al chat de marketing)" in salida and "Taigo noviembre" in salida
