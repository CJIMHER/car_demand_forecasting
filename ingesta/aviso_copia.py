"""Aviso por Telegram cuando falla la copia diaria de Postgres (infra/backup/pg_backup.sh).

Lo llama pg_backup.sh desde su trap de salida, solo si la copia termina con error:

    python -m ingesta.aviso_copia --codigo 1 --paso "subida al bucket"

Mismo mecanismo que el resumen diario: texto plano con sendMessage (ingesta.telegram.enviar, con
sus reintentos), el token nunca en logs ni en mensajes. Primero se envía y después se intenta
anotar en fuentes_estado (fuente 'telegram', tipo 'aviso'): si la copia ha fallado porque
Postgres no responde, el aviso tiene que llegar igual. Nunca lanza; el código de salida (0 enviado,
1 no enviado) es informativo: pg_backup.sh sale siempre con el código de la copia.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date

from psycopg.types.json import Jsonb

from ingesta import telegram
from ingesta.db import conectar
from ingesta.scrapers_vw import hoy_madrid

LOG = logging.getLogger("cdf.aviso_copia")


def texto_aviso(dia: date, codigo: int, paso: str) -> str:
    return "\n".join([
        f"CDF {dia.strftime('%d/%m')} · la copia de Postgres ha fallado",
        f"Paso: {paso or 'desconocido'} (código {codigo})",
        "Detalle: registro de la copia de hoy (make logs)",
    ])


def _anotar(dia: date, estado: str, detalle: dict) -> None:
    try:
        with conectar(autocommit=True, connect_timeout=10) as pg:
            pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) "
                       "VALUES ('telegram', 'aviso', %s, %s, %s)", (dia, estado, Jsonb(detalle)))
    except Exception as exc:  # Postgres caído: el aviso ya se ha intentado, no se oculta nada
        LOG.warning("No se pudo anotar el aviso en fuentes_estado (%s)", type(exc).__name__)


def avisar_texto(dia: date, texto: str, aviso: str, enviar=None) -> bool:
    """Envía un aviso de fallo (texto plano) y lo anota con detalle.aviso = `aviso`. Nunca lanza.

    Lo usan la copia (aviso «copia») y el precálculo del panel (aviso «precalculo»).
    """
    try:
        intentos = (enviar or telegram.enviar)(texto)
    except Exception as exc:  # sin traza: podría llevar la URL con el token
        error = telegram.limpiar(f"{type(exc).__name__}: {exc}")[:500]
        LOG.warning("No se pudo enviar el aviso «%s» a Telegram: %s", aviso, error)
        _anotar(dia, "error", {"aviso": aviso, "error": error, "texto": texto})
        return False
    LOG.info("Aviso «%s» enviado a Telegram", aviso)
    _anotar(dia, "ok", {"aviso": aviso, "texto": texto, "intentos": intentos})
    return True


def avisar(dia: date, codigo: int, paso: str, enviar=None) -> bool:
    """Envía el aviso de copia fallida y lo anota. Nunca lanza."""
    return avisar_texto(dia, texto_aviso(dia, codigo, paso), "copia", enviar)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Aviso por Telegram de copia de Postgres fallida")
    parser.add_argument("--codigo", type=int, required=True, help="código de salida de pg_backup.sh")
    parser.add_argument("--paso", default="", help="paso de la copia en el que falló")
    parser.add_argument("--dia", type=date.fromisoformat)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        return 0 if avisar(args.dia or hoy_madrid(), args.codigo, args.paso) else 1
    except Exception as exc:  # cualquier cosa imprevista: sin traza, sin token
        LOG.warning("Aviso de copia fallida no enviado: %s", telegram.limpiar(type(exc).__name__))
        return 1


if __name__ == "__main__":
    sys.exit(main())
