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


@pytest.fixture(autouse=True)
def sin_telegram(monkeypatch):
    """Ninguna prueba llama a Telegram: sin credenciales y con la salida a la red bloqueada."""
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

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
