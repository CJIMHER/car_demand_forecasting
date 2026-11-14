"""Conexión a Postgres.

Sin URL en el código ni en el repositorio: se usa CDF_DATABASE_URL si existe y, si no, las
variables estándar de libpq (PGHOST, PGUSER, PGPASSWORD, PGDATABASE), que el compose pasa al
contenedor jobs desde /opt/cdf/.env.
"""

from __future__ import annotations

import os
from collections.abc import Mapping

import psycopg
from psycopg.conninfo import conninfo_to_dict
from sqlalchemy.engine import URL

# Parámetros de libpq que tienen sitio propio en la URL; el resto va en la query (sslmode...).
_VARIABLES_PG = (("host", "PGHOST"), ("port", "PGPORT"), ("user", "PGUSER"),
                 ("password", "PGPASSWORD"), ("dbname", "PGDATABASE"))


def conninfo() -> str:
    return os.environ.get("CDF_DATABASE_URL", "")


def conectar(**kwargs) -> psycopg.Connection:
    return psycopg.connect(conninfo(), **kwargs)


def url_desde_libpq(cadena: str, entorno: Mapping[str, str] | None = None) -> str:
    """URL postgresql+psycopg:// a partir de una cadena libpq «clave=valor» (puede ser vacía).

    Lo que no venga en la cadena se completa con las variables PG* del entorno, como haría libpq.
    La contraseña y el resto de valores quedan escapados. Un host que empieza por «/» es un
    directorio de socket y va en la query (?host=...), que es como lo entiende psycopg.
    """
    entorno = os.environ if entorno is None else entorno
    datos = {k: str(v) for k, v in conninfo_to_dict(cadena).items() if v not in (None, "")}
    for clave, variable in _VARIABLES_PG:
        if clave not in datos and entorno.get(variable):
            datos[clave] = entorno[variable]
    host = datos.pop("host", None)
    puerto = datos.pop("port", None)
    query = {}
    if host and (host.startswith("/") or "," in host):  # socket o varios hosts: los resuelve libpq
        query["host"] = host
        host = None
        if puerto:
            query["port"] = puerto
            puerto = None
    usuario, clave_pg, base = datos.pop("user", None), datos.pop("password", None), datos.pop("dbname", None)
    query.update(datos)
    return URL.create("postgresql+psycopg", username=usuario, password=clave_pg, host=host,
                      port=int(puerto) if puerto else None, database=base,
                      query=query).render_as_string(hide_password=False)


def url_sqlalchemy() -> str:
    url = conninfo()
    if not url:
        return "postgresql+psycopg://"  # producción: psycopg completa con las variables PG*
    for prefijo in ("postgresql://", "postgres://"):
        if url.startswith(prefijo):
            return "postgresql+psycopg://" + url[len(prefijo):]
    if "://" in url:
        return url
    return url_desde_libpq(url)  # cadena libpq «clave=valor» (p. ej. las pruebas en el contenedor)
