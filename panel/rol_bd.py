"""Activa el rol de Postgres del panel (cdf_panel) con la contraseña de PANEL_DB_PASSWORD.

Se lanza en cdf_jobs con el usuario administrador (make panel-rol, dentro de make deploy). La
contraseña sale del entorno (del .env) y no viaja nunca en claro a Postgres: se envía el
verificador SCRAM-SHA-256 calculado aquí, como hace \\password de psql. Así no puede acabar en el
log del servidor ni aparece en ninguna migración.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import sys
import unicodedata

import psycopg
from psycopg import sql

ROL = "cdf_panel"
ITERACIONES = 4096


def verificador_scram(clave: str, sal: bytes | None = None, iteraciones: int = ITERACIONES) -> str:
    sal = sal or os.urandom(16)
    normalizada = unicodedata.normalize("NFKC", clave).encode()
    salada = hashlib.pbkdf2_hmac("sha256", normalizada, sal, iteraciones)
    cliente = hmac.new(salada, b"Client Key", hashlib.sha256).digest()
    guardada = hashlib.sha256(cliente).digest()
    servidor = hmac.new(salada, b"Server Key", hashlib.sha256).digest()
    b64 = lambda b: base64.b64encode(b).decode()  # noqa: E731
    return f"SCRAM-SHA-256${iteraciones}:{b64(sal)}${b64(guardada)}:{b64(servidor)}"


def activar(pg: psycopg.Connection, clave: str) -> None:
    if len(clave) < 24:
        raise ValueError("PANEL_DB_PASSWORD debe tener al menos 24 caracteres (openssl rand -hex 32)")
    pg.execute(sql.SQL("ALTER ROLE {} WITH LOGIN PASSWORD {} CONNECTION LIMIT 20").format(
        sql.Identifier(ROL), sql.Literal(verificador_scram(clave))))
    pg.execute(sql.SQL("ALTER ROLE {} SET statement_timeout = '15s'").format(sql.Identifier(ROL)))


def main() -> int:
    clave = os.environ.get("PANEL_DB_PASSWORD", "")
    try:
        with psycopg.connect(os.environ.get("CDF_DATABASE_URL", ""), autocommit=True) as pg:
            activar(pg, clave)
    except (ValueError, psycopg.Error) as exc:
        print(f"No se pudo activar {ROL}: {type(exc).__name__}: {str(exc).splitlines()[0] if str(exc) else ''}",
              file=sys.stderr)
        return 1
    print(f"Rol {ROL} activo (contraseña de PANEL_DB_PASSWORD).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
