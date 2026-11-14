"""Resumen diario por Telegram. Envío siempre simulado: nunca se llama a Telegram."""

import io
import json
import logging
import urllib.error
from datetime import date

import pytest

from ingesta import scrapers_vw as sv
from ingesta import telegram
from sinteticos import capturar, fallar, flota

TOKEN = "123456:SECRETO-de-prueba-AAAA"
DIA = date(2026, 11, 14)


class Respuesta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http_error(codigo, cuerpo):
    return urllib.error.HTTPError(f"https://api.telegram.org/bot{TOKEN}/sendMessage", codigo, "x", {},
                                  io.BytesIO(json.dumps(cuerpo).encode()))


class TelegramFalso:
    """Sustituye a telegram._urlopen: guarda las peticiones y responde lo que se le diga."""

    def __init__(self, respuestas):
        self.respuestas, self.peticiones = list(respuestas), []

    def __call__(self, peticion, timeout):
        self.peticiones.append(peticion)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return Respuesta(json.dumps({"ok": True, "result": {}}).encode())


# ------------------------------------------------------------------ texto

def test_texto_todo_correcto():
    comp = {"vo": {"estado": "ok"}, "vn": {"estado": "ok"}, "vn_otros": {"estado": "ok"}}
    texto = sv.texto_resumen(DIA, comp, {"vo": 3076, "vn": 4798, "vn_otros": 419}, {})
    assert texto == ("CDF 14/11 · todo correcto\n"
                     "VO: 3.076 anuncios\n"
                     "VN: 4.798 anuncios\n"
                     "VN eléctricos e híbridos: 419 anuncios\n"
                     "Carga a Postgres: correcta (8.293 filas)")


def test_texto_con_un_problema():
    comp = {"vo": {"estado": "ok"},
            "vn": {"estado": "sin_captura", "ultima_captura": "2026-11-13",
                   "ultimo_intento": {"estado": "failed", "error": "El total cambió durante la captura"}},
            "vn_otros": {"estado": "ok"}}
    lineas = sv.texto_resumen(DIA, comp, {"vo": 3076, "vn_otros": 419}, {}).splitlines()
    assert lineas[0] == "CDF 14/11 · 1 problema"
    assert lineas[2] == ("VN: sin captura de hoy; última el 13/11; "
                         "último intento: failed (El total cambió durante la captura)")
    assert lineas[4] == "Carga a Postgres: correcta (3.495 filas)"


def test_texto_con_varios_problemas():
    comp = {"vo": {"estado": "sin_cargar", "anuncios": 3076}, "vn": {"estado": "ok"},
            "vn_otros": {"estado": "error", "error": "No existe la base de vn_otros"}}
    lineas = sv.texto_resumen(DIA, comp, {"vn": 4798}, {"vo": "el scraper dice 3076 y hay 3075"}).splitlines()
    assert lineas[0] == "CDF 14/11 · 3 problemas"
    assert lineas[1] == "VO: 3.076 anuncios capturados, sin cargar en Postgres"
    assert lineas[3] == "VN eléctricos e híbridos: error al comprobar (No existe la base de vn_otros)"
    assert lineas[4] == "Carga a Postgres: con errores (4.798 filas) · VO: el scraper dice 3076 y hay 3075"


# ------------------------------------------------------------------ envío

@pytest.fixture
def credenciales(monkeypatch):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100200300")


def test_envio_texto_plano(credenciales, monkeypatch):
    falso = TelegramFalso([None])
    monkeypatch.setattr(telegram, "_urlopen", falso)
    assert telegram.enviar("CDF 14/11 · todo correcto") == 1
    p = falso.peticiones[0]
    assert p.full_url.endswith("/sendMessage") and p.get_method() == "POST"
    cuerpo = dict(x.split("=", 1) for x in p.data.decode().split("&"))
    assert cuerpo["chat_id"] == "-100200300"
    assert "parse_mode" not in cuerpo  # texto plano


def test_reintenta_429_con_retry_after_y_red(credenciales, monkeypatch):
    falso = TelegramFalso([http_error(429, {"ok": False, "parameters": {"retry_after": 7}}),
                           urllib.error.URLError("sin red"), None])
    monkeypatch.setattr(telegram, "_urlopen", falso)
    esperas = []
    assert telegram.enviar("hola", dormir=esperas.append) == 3
    assert esperas == [7, 15]


def test_no_reintenta_400_y_el_error_no_lleva_token(credenciales, monkeypatch):
    falso = TelegramFalso([http_error(400, {"ok": False, "description": "Bad Request: chat not found"})])
    monkeypatch.setattr(telegram, "_urlopen", falso)
    with pytest.raises(telegram.ErrorTelegram) as e:
        telegram.enviar("hola", dormir=lambda s: None)
    assert len(falso.peticiones) == 1
    assert "chat not found" in str(e.value)
    assert TOKEN not in str(e.value) and e.value.__cause__ is None and e.value.__suppress_context__


def test_sin_credenciales_no_hay_peticion():
    with pytest.raises(telegram.ErrorTelegram, match="faltan"):
        telegram.enviar("hola")


# ------------------------------------------------------------------ dentro de la comprobación

def test_comprobacion_envia_resumen_y_lo_anota(pg, datos):
    capturar(datos, "vo", "2026-11-14", flota("VO", 9))
    capturar(datos, "vn", "2026-11-14", flota("VN", 7, fuente="vn"))
    capturar(datos, "vn_otros", "2026-11-13", flota("EH", 4, fuente="vn_otros"))
    fallar(datos, "vn_otros", "2026-11-14", "El total cambió durante la captura")
    enviados = []
    sv.comprobar(pg, dia=DIA, avisar=lambda t: enviados.append(t) or 1)
    assert enviados == ["CDF 14/11 · 1 problema\n"
                        "VO: 9 anuncios\n"
                        "VN: 7 anuncios\n"
                        "VN eléctricos e híbridos: sin captura de hoy; última el 13/11; "
                        "último intento: failed (El total cambió durante la captura)\n"
                        "Carga a Postgres: correcta (16 filas)"]
    fila = pg.execute("SELECT fuente, estado, detalle->>'texto' FROM fuentes_estado WHERE tipo = 'aviso'").fetchone()
    assert fila == ("telegram", "ok", enviados[0])


def test_fallo_de_telegram_se_anota_sin_token_y_no_rompe_el_job(pg, datos, credenciales, monkeypatch, caplog):
    capturar(datos, "vo", "2026-11-14", flota("VO", 9))
    capturar(datos, "vn", "2026-11-14", flota("VN", 7, fuente="vn"))
    capturar(datos, "vn_otros", "2026-11-14", flota("EH", 4, fuente="vn_otros"))
    falso = TelegramFalso([urllib.error.URLError(f"fallo en https://api.telegram.org/bot{TOKEN}/x")] * 4)
    monkeypatch.setattr(telegram, "_urlopen", falso)
    monkeypatch.setattr(telegram.time, "sleep", lambda s: None)
    monkeypatch.setattr(telegram, "ESPERAS", (0, 0, 0))
    caplog.set_level(logging.DEBUG)
    res = sv.comprobar(pg, dia=DIA)  # envío real del módulo, con la red simulada
    assert {r["estado"] for r in res.values()} == {"ok"}
    assert len(falso.peticiones) == 4
    fila = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE tipo = 'aviso'").fetchone()
    assert fila[0] == "error" and "4 intentos" in fila[1]["error"]
    assert fila[1]["texto"].startswith("CDF 14/11 · todo correcto")
    todo = json.dumps(fila[1]) + caplog.text
    assert TOKEN not in todo


def test_limpiar_quita_el_token(credenciales):
    assert telegram.limpiar(f"https://api.telegram.org/bot{TOKEN}/sendMessage") == \
        "https://api.telegram.org/bot***/sendMessage"


def test_comprobar_cli_sin_telegram(pg_url, datos):
    capturar(datos, "vo", "2026-11-14", flota("VO", 3))
    assert sv.main(["comprobar", "--dia", "2026-11-14", "--sin-telegram"]) == 1  # faltan vn y vn_otros
