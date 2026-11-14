"""Envío de mensajes de texto plano a Telegram con sendMessage de la Bot API.

TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID salen del entorno (del /opt/cdf/.env del servidor).
El token va en la URL de la Bot API, así que:
- ningún error que salga de aquí incluye la URL ni el texto de la excepción original
  (se relanza siempre con «from None» y un mensaje propio);
- limpiar() borra el token de cualquier texto antes de anotarlo o escribirlo en un log.

Reintenta con espera si Telegram responde 429 (respetando retry_after), 5xx o falla la red.
Cualquier otra respuesta (400 chat no encontrado, 401 token inválido...) no se reintenta.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

API = "https://api.telegram.org"
LIMITE_TEXTO = 4000          # Telegram admite 4096 caracteres por mensaje
ESPERAS = (5, 15, 45)        # segundos entre intentos por red o 5xx
MAX_RETRY_AFTER = 120


class ErrorTelegram(Exception):
    pass


def _urlopen(peticion, timeout):
    # Punto único de salida a la red; las pruebas lo sustituyen para que nunca se llame a Telegram.
    return urllib.request.urlopen(peticion, timeout=timeout)


def limpiar(texto: str, token: str | None = None) -> str:
    """Quita el token (el del argumento y el del entorno) de un texto."""
    for t in {token, os.environ.get("TELEGRAM_BOT_TOKEN")}:
        if t:
            texto = texto.replace(t, "***")
    return texto


def _descripcion(cuerpo: bytes) -> tuple[str, int | None]:
    try:
        datos = json.loads(cuerpo.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return "", None
    retry = (datos.get("parameters") or {}).get("retry_after")
    return str(datos.get("description") or ""), retry if isinstance(retry, int) else None


def enviar(texto: str, *, token: str | None = None, chat_id: str | None = None,
           intentos: int = 4, dormir=time.sleep, timeout: float = 20) -> int:
    """Envía `texto` y devuelve el número de intentos usados. Lanza ErrorTelegram si no puede."""
    token = token or os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = chat_id or os.environ.get("TELEGRAM_CHAT_ID", "")
    if not token or not chat_id:
        raise ErrorTelegram("faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID en el entorno")
    cuerpo = urllib.parse.urlencode({"chat_id": chat_id, "text": texto[:LIMITE_TEXTO],
                                     "disable_web_page_preview": "true"}).encode()
    ultimo = "sin intentos"
    for n in range(1, intentos + 1):
        espera = ESPERAS[min(n - 1, len(ESPERAS) - 1)]
        peticion = urllib.request.Request(f"{API}/bot{token}/sendMessage", data=cuerpo, method="POST",
                                          headers={"Content-Type": "application/x-www-form-urlencoded"})
        try:
            with _urlopen(peticion, timeout) as r:
                r.read()
            return n
        except urllib.error.HTTPError as exc:
            try:
                leido = exc.read()
            except Exception:
                leido = b""
            descripcion, retry_after = _descripcion(leido)
            ultimo = limpiar(f"HTTP {exc.code} {descripcion}".strip(), token)
            if exc.code == 429:
                espera = min(retry_after or espera, MAX_RETRY_AFTER)
            elif exc.code < 500:
                raise ErrorTelegram(f"Telegram rechazó el mensaje: {ultimo}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            ultimo = f"error de red ({type(exc).__name__})"
        if n < intentos:
            dormir(espera)
    raise ErrorTelegram(f"no se pudo enviar tras {intentos} intentos: {ultimo}") from None
