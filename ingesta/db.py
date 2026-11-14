"""Conexión a Postgres.

Sin URL en el código ni en el repositorio: se usa CDF_DATABASE_URL si existe y, si no, las
variables estándar de libpq (PGHOST, PGUSER, PGPASSWORD, PGDATABASE), que el compose pasa al
contenedor jobs desde /opt/cdf/.env.
"""

from __future__ import annotations

import os

import psycopg


def conninfo() -> str:
    return os.environ.get("CDF_DATABASE_URL", "")


def conectar(**kwargs) -> psycopg.Connection:
    return psycopg.connect(conninfo(), **kwargs)


def url_sqlalchemy() -> str:
    url = conninfo()
    if not url:
        return "postgresql+psycopg://"  # psycopg completa con las variables PG*
    for prefijo in ("postgresql://", "postgres://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo):]
    return url
