"""Postgres para las pruebas.

Por orden de preferencia:
1. CDF_TEST_PG=1 (lo pone «make test» en el contenedor jobs): se usa el Postgres del compose con las
   variables PG* y se crea una base temporal por prueba, que se borra al terminar.
2. El paquete pgserver (Postgres 16 embebido, solo para el Mac de desarrollo).
3. Si no hay ninguno, las pruebas que necesitan Postgres se saltan (las demás se ejecutan).
"""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

import psycopg
import pytest

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(Path(__file__).resolve().parent))


# Configuración que el host puede traer y que cambia el comportamiento de la aplicación. En el
# servidor, «make test» corre en cdf_jobs, que recibe /opt/cdf/.env entero como env_file (y
# CDF_SCRAPERS_DATA del compose): sin esto, las pruebas verían la configuración de producción.
# Se quedan PG* y CDF_TEST_PG, que dicen a las pruebas dónde está el Postgres de pruebas.
PREFIJOS_DEL_HOST = ("PANEL_", "TELEGRAM_", "RCLONE_", "POSTGRES_", "META_", "GA4_")
VARIABLES_DEL_HOST = ("BUCKET", "BACKUP_PASSPHRASE", "CDF_DATABASE_URL", "CDF_SCRAPERS_DATA", "CDF_CONCESIONARIO",
                      "CDF_VERSIONES", "CDF_CAMPANAS", "CDF_ENV_FILE", "CDF_PYTHON")


def del_host(nombre: str) -> bool:
    return nombre.startswith(PREFIJOS_DEL_HOST) or nombre in VARIABLES_DEL_HOST


def _limpiar(mp: pytest.MonkeyPatch) -> None:
    for nombre in [n for n in os.environ if del_host(n)]:
        mp.delenv(nombre)


@pytest.fixture(scope="session", autouse=True)
def entorno_limpio_sesion():
    """Antes que cualquier fixture de módulo o de sesión (p. ej. el catálogo de test_segmentos)."""
    with pytest.MonkeyPatch.context() as mp:
        _limpiar(mp)
        yield


@pytest.fixture(autouse=True)
def entorno_limpio(entorno_limpio_sesion, monkeypatch):
    """Cada prueba empieza sin la configuración del host; la que necesite una variable la fija ella
    con monkeypatch (y se deshace al terminar)."""
    _limpiar(monkeypatch)


@pytest.fixture(autouse=True)
def sin_telegram(entorno_limpio, monkeypatch):
    """Ninguna prueba llama a Telegram: sin credenciales (entorno_limpio) y con la salida a la red bloqueada."""

    def prohibido(*args, **kwargs):
        raise AssertionError("Una prueba ha intentado llamar a Telegram")

    from ingesta import telegram
    monkeypatch.setattr(telegram, "_urlopen", prohibido)


@pytest.fixture(scope="session")
def pg_admin():
    """(conninfo de administración, directorio de binarios de Postgres o None)."""
    if os.environ.get("CDF_TEST_PG") == "1":
        yield "dbname=postgres", None
        return
    try:
        import pgserver
    except ImportError:
        pytest.skip("Sin Postgres para pruebas (ni CDF_TEST_PG=1 ni pgserver)")
    datos = tempfile.mkdtemp(prefix="cdf_pg_")
    srv = pgserver.get_server(datos, cleanup_mode="stop")
    yield srv.get_uri(), str(Path(pgserver.__file__).parent / "pginstall" / "bin")
    shutil.rmtree(datos, ignore_errors=True)


def _url_con_base(admin: str, nombre: str) -> str:
    if admin.startswith("postgresql://"):
        base, _, query = admin.partition("?")
        base = base.rsplit("/", 1)[0] + "/" + nombre
        return base + ("?" + query if query else "")
    return f"{admin.replace('dbname=postgres', '')} dbname={nombre}".strip()


@pytest.fixture
def pg_url(pg_admin, monkeypatch):
    """Base vacía y migrada con Alembic; CDF_DATABASE_URL apunta a ella durante la prueba."""
    admin, _ = pg_admin
    nombre = "cdf_test_" + uuid.uuid4().hex[:10]
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'CREATE DATABASE "{nombre}"')
    url = _url_con_base(admin, nombre)
    monkeypatch.setenv("CDF_DATABASE_URL", url)
    from alembic import command
    from alembic.config import Config
    command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")
    yield url
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute(f'DROP DATABASE "{nombre}" WITH (FORCE)')


@pytest.fixture
def pg(pg_url):
    with psycopg.connect(pg_url, autocommit=True) as c:
        yield c


@pytest.fixture
def datos(tmp_path, monkeypatch):
    """Carpeta /opt/cdf/data sintética."""
    d = tmp_path / "data"
    d.mkdir()
    monkeypatch.setenv("CDF_SCRAPERS_DATA", str(d))
    return d


CLAVE_ROL_PANEL = "clave-del-rol-panel-solo-pruebas-0123456789"


@pytest.fixture
def panel_url(pg_url, pg):
    """Conexión como cdf_panel (el usuario de Postgres del panel), con la contraseña puesta como en
    producción (verificador SCRAM)."""
    from panel import rol_bd
    rol_bd.activar(pg, CLAVE_ROL_PANEL)
    return psycopg.conninfo.make_conninfo(pg_url, user="cdf_panel", password=CLAVE_ROL_PANEL)
