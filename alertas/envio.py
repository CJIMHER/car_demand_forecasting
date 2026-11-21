"""Envío por Telegram de las alertas (un mensaje por rol y día) y de los avisos de los botones de Ventas.

Con ingesta/telegram.py: texto plano, reintentos y el token nunca en logs ni en mensajes.

Destinos (del .env): TELEGRAM_CHAT_ID es el técnico; TELEGRAM_CHAT_DIRECCION, TELEGRAM_CHAT_VENTAS y
TELEGRAM_CHAT_MARKETING, los de cada rol. Si falta el chat de un rol, su mensaje va al técnico con el
rol delante («[Para ventas]»).

Qué se envía:
- Alertas: las abiertas hoy para el rol que nunca se le han enviado desde que se abrieron. Es decir,
  las nuevas de hoy y las que no pudieron salir un día anterior por un fallo de Telegram. Una alerta
  abierta que se repite al día siguiente no se reenvía.
- Acciones de Ventas («Pedir campaña a marketing», «Revisar precio»): las que aún no tienen salida.
  Las pide el panel (tabla acciones) y las envía cdf_jobs cada minuto: el panel no tiene el token.
  Con datos de ejemplo, solo al técnico y con «[EJEMPLO]» delante.

Las alertas calculadas sobre filas de ejemplo (solo para el técnico, con «[EJEMPLO]») no se envían
salvo con ALERTAS_ENVIAR_EJEMPLO=1 en el .env; se guardan igual y se ven en el panel.

Cada envío queda en alertas_salida (enviado o error). Un fallo se anota también en fuentes_estado
(fuente 'telegram', tipo 'aviso') y no rompe la tarea.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from datetime import date

import psycopg
from psycopg.types.json import Jsonb

from ingesta import telegram
from panel import formato as fmt

LOG = logging.getLogger("alertas.envio")
ROLES = ("tecnico", "direccion", "ventas", "marketing")
NOMBRES = {"direccion": "dirección", "ventas": "ventas", "marketing": "marketing", "tecnico": "técnico"}
VARIABLES = {"tecnico": "TELEGRAM_CHAT_ID", "direccion": "TELEGRAM_CHAT_DIRECCION",
             "ventas": "TELEGRAM_CHAT_VENTAS", "marketing": "TELEGRAM_CHAT_MARKETING"}
ACCIONES = {"campana": ("Pedir campaña a marketing", "marketing"), "revisar_precio": ("Revisar precio", "ventas")}
PREFIJO_EJEMPLO = "[EJEMPLO] "
LIMITE = telegram.LIMITE_TEXTO - 100


def destino(rol: str, entorno: Mapping[str, str]) -> tuple[str, str]:
    """(rol cuyo chat se usa, chat). Sin chat propio, el del técnico."""
    chat = (entorno.get(VARIABLES[rol]) or "").strip()
    if chat:
        return rol, chat
    return "tecnico", (entorno.get(VARIABLES["tecnico"]) or "").strip()


def mensaje_alertas(dia: date, rol: str, filas: list[dict], para_otro: bool) -> str:
    n = len(filas)
    cabecera = (f"CDF {dia.strftime('%d/%m')} · {n} alerta{'s' if n != 1 else ''} "
                f"nueva{'s' if n != 1 else ''} para {NOMBRES[rol]}")
    if para_otro:
        cabecera = f"[Para {NOMBRES[rol]}] {cabecera}"
    lineas, usado = [cabecera, ""], len(cabecera) + 2
    for i, f in enumerate(filas):
        linea = f"• {f['texto']}"
        if usado + len(linea) + 1 > LIMITE:
            lineas.append(f"… y {n - i} más en el panel.")
            break
        lineas.append(linea)
        usado += len(linea) + 1
    return "\n".join(lineas)


def _registrar(pg, dia: date, que: str, rol: str, dest: str, texto: str, estado: str, *, alertas=(),
               accion_id=None, error=None, intentos=None) -> None:
    pg.execute("""INSERT INTO alertas_salida (fecha, que, rol, destino, texto, alertas, accion_id, estado, error, intentos)
                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
               (dia, que, rol, dest, texto, Jsonb(list(alertas)), accion_id, estado, error, intentos))
    if estado == "error":
        try:
            pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) VALUES ('telegram', 'aviso', "
                       "%s, 'error', %s)", (dia, Jsonb({"aviso": que, "rol": rol, "destino": dest, "error": error})))
        except Exception:
            LOG.warning("No se pudo anotar el fallo de Telegram en fuentes_estado")


def _intentar(enviar, texto: str, chat: str) -> tuple[str, int | None, str | None]:
    """(estado, intentos, error). Nunca lanza; el error va sin token."""
    try:
        if not chat:
            raise telegram.ErrorTelegram("falta TELEGRAM_CHAT_ID (chat del técnico) en el entorno")
        return "enviado", enviar(texto, chat_id=chat), None
    except Exception as exc:  # sin traza: podría llevar la URL con el token
        return "error", None, telegram.limpiar(f"{type(exc).__name__}: {exc}")[:500]


def pendientes(pg: psycopg.Connection, dia: date, rol: str) -> list[dict]:
    """Alertas abiertas hoy para el rol que no se le han enviado desde que se abrieron."""
    cols = ("regla", "clave", "origen", "texto", "nivel", "tema", "abierta_desde")
    filas = [dict(zip(cols, f)) for f in pg.execute(
        f"""SELECT {', '.join(cols)} FROM alertas WHERE fecha = %s AND rol = %s AND estado IN ('nueva', 'abierta')
            ORDER BY (nivel = 'aviso') DESC, tema, regla, clave""", (dia, rol))]
    if not filas:
        return []
    desde = min(f["abierta_desde"] for f in filas)
    enviadas: dict[tuple, date] = {}
    for fecha, alertas in pg.execute(
            "SELECT fecha, alertas FROM alertas_salida WHERE que = 'alertas' AND rol = %s AND estado = 'enviado' "
            "AND fecha >= %s", (rol, desde)):
        for a in alertas:
            k = (a["regla"], a["clave"], a["origen"])
            enviadas[k] = max(enviadas.get(k, fecha), fecha)
    return [f for f in filas if enviadas.get((f["regla"], f["clave"], f["origen"]), date.min) < f["abierta_desde"]]


def enviar_dia(pg: psycopg.Connection, dia: date, enviar=None, entorno: Mapping[str, str] | None = None,
               sin_telegram: bool = False) -> dict:
    """Un mensaje por rol con sus alertas pendientes. Devuelve {rol: resultado}."""
    entorno = os.environ if entorno is None else entorno
    enviar = enviar or telegram.enviar
    resultado = {}
    con_ejemplo = (entorno.get("ALERTAS_ENVIAR_EJEMPLO") or "").strip() == "1"
    for rol in ROLES:
        # Las calculadas sobre filas de ejemplo se guardan y se ven en el panel, pero no se envían
        # salvo con ALERTAS_ENVIAR_EJEMPLO=1 en el .env
        filas = [f for f in pendientes(pg, dia, rol) if con_ejemplo or f["origen"] != "ejemplo"]
        if not filas:
            continue
        dest, chat = destino(rol, entorno)
        texto = mensaje_alertas(dia, rol, filas, para_otro=dest != rol)
        if sin_telegram:
            print(f"--- {rol} (iría al chat de {dest})\n{texto}\n")
            resultado[rol] = f"sin enviar (SIN_TELEGRAM): {len(filas)}"
            continue
        estado, intentos, error = _intentar(enviar, texto, chat)
        _registrar(pg, dia, "alertas", rol, dest, texto, estado, intentos=intentos, error=error,
                   alertas=[{"regla": f["regla"], "clave": f["clave"], "origen": f["origen"]} for f in filas])
        resultado[rol] = f"{estado}: {len(filas)}" + (f" ({error})" if error else "")
        if error:
            LOG.warning("Alertas de %s sin enviar: %s", rol, error)
    return resultado


# --------------------------------------------------------------------------- botones de Ventas

def mensaje_accion(a: dict, para_otro: str | None) -> str:
    titulo, _ = ACCIONES[a["accion"]]
    lineas = [f"{titulo} · {a['vehiculo']} (stock {a['stock_id']}, {a['tipo']})",
              f"{fmt.miles(a['dias_exposicion'])} días en stock · {fmt.decimal(float(a['p_venta90_p50']) * 100, 0)} % "
              f"de venderse en 90 días (entre {fmt.decimal(float(a['p_venta90_p10']) * 100, 0)} y "
              f"{fmt.decimal(float(a['p_venta90_p90']) * 100, 0)} %)",
              f"Lo pide {a['usuario']} desde el panel, {fmt.fecha_hora(a['pedido_at'])}."]
    texto = "\n".join(lineas)
    if para_otro:
        texto = f"[Para {NOMBRES[para_otro]}] {texto}"
    if a["origen"] == "ejemplo":
        texto = PREFIJO_EJEMPLO + texto
    return texto


def enviar_acciones(pg: psycopg.Connection, enviar=None, entorno: Mapping[str, str] | None = None,
                    hoy: date | None = None) -> int:
    """Envía los avisos de las acciones que aún no tienen salida. Devuelve cuántas se han intentado."""
    entorno = os.environ if entorno is None else entorno
    enviar = enviar or telegram.enviar
    cols = ("id", "pedido_at", "stock_id", "accion", "usuario", "origen", "tipo", "vehiculo", "dias_exposicion",
            "p_venta90_p10", "p_venta90_p50", "p_venta90_p90")
    acciones = [dict(zip(cols, f)) for f in pg.execute(
        f"""SELECT {', '.join(f'a.{c}' for c in cols)} FROM acciones a
            WHERE NOT EXISTS (SELECT 1 FROM alertas_salida s WHERE s.accion_id = a.id) ORDER BY a.id""")]
    for a in acciones:
        rol = ACCIONES[a["accion"]][1]
        if a["origen"] == "ejemplo":          # con datos de ejemplo, solo al técnico
            rol = "tecnico"
            dest, chat = "tecnico", (entorno.get(VARIABLES["tecnico"]) or "").strip()
        else:
            dest, chat = destino(rol, entorno)
        texto = mensaje_accion(a, ACCIONES[a["accion"]][1] if dest != ACCIONES[a["accion"]][1] else None)
        estado, intentos, error = _intentar(enviar, texto, chat)
        dia = hoy or a["pedido_at"].astimezone(fmt.MADRID).date()
        _registrar(pg, dia, "accion", rol, dest, texto, estado, accion_id=a["id"], intentos=intentos, error=error)
    return len(acciones)


def main(argv=None) -> int:
    """python -m alertas.envio acciones: el cron de cada minuto (sin salida si no hay nada)."""
    import argparse
    import sys

    from ingesta.db import conectar

    parser = argparse.ArgumentParser(description="Avisos de las acciones de Ventas por Telegram")
    parser.add_argument("orden", choices=["acciones"])
    parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        with conectar(autocommit=True, connect_timeout=10) as pg:
            n = enviar_acciones(pg)
    except Exception as exc:
        LOG.error("Avisos de acciones fallidos: %s", telegram.limpiar(f"{type(exc).__name__}: {exc}")[:300])
        return 1
    if n:
        LOG.info("Avisos de acciones: %s", n)
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
