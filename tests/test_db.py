"""Conversión de la conexión a URL de SQLAlchemy/Alembic, en los dos formatos que se dan."""

from sqlalchemy.engine import make_url

from ingesta import db

CLAVE = "p@ss:w/rd#?%&=x y"
PG = {"PGHOST": "cdf_postgres_test", "PGPORT": "5433", "PGUSER": "postgres", "PGPASSWORD": CLAVE,
      "PGDATABASE": "postgres"}


def _partes(url):
    u = make_url(url)
    return u.drivername, u.username, u.password, u.host, u.port, u.database, dict(u.query)


def test_libpq_con_variables_pg_como_en_el_contenedor():
    url = db.url_desde_libpq("dbname=cdf_test_1106a1b2b6", PG)
    assert url.startswith("postgresql+psycopg://")
    assert CLAVE not in url  # va escapada
    assert _partes(url) == ("postgresql+psycopg", "postgres", CLAVE, "cdf_postgres_test", 5433,
                            "cdf_test_1106a1b2b6", {})


def test_libpq_completa_con_valores_propios_y_parametros_extra():
    url = db.url_desde_libpq(f"host=10.0.0.5 port=5432 user=cdf password='{CLAVE}' dbname=cdf sslmode=require", PG)
    assert _partes(url) == ("postgresql+psycopg", "cdf", CLAVE, "10.0.0.5", 5432, "cdf", {"sslmode": "require"})


def test_libpq_con_socket_local():
    url = db.url_desde_libpq("dbname=cdf_test_x", {"PGHOST": "/tmp/sock dir", "PGPORT": "6543", "PGUSER": "postgres"})
    assert _partes(url) == ("postgresql+psycopg", "postgres", None, None, None, "cdf_test_x",
                            {"host": "/tmp/sock dir", "port": "6543"})


def test_url_sqlalchemy_con_los_dos_formatos(monkeypatch):
    for k, v in PG.items():
        monkeypatch.setenv(k, v)
    # Formato libpq (contenedor)
    monkeypatch.setenv("CDF_DATABASE_URL", "dbname=cdf_test_1106a1b2b6")
    assert _partes(db.url_sqlalchemy()) == ("postgresql+psycopg", "postgres", CLAVE, "cdf_postgres_test", 5433,
                                            "cdf_test_1106a1b2b6", {})
    # Formato URL (Mac con pgserver)
    monkeypatch.setenv("CDF_DATABASE_URL", "postgresql://postgres:@/cdf_test_x?host=/tmp/abc")
    assert db.url_sqlalchemy() == "postgresql+psycopg://postgres:@/cdf_test_x?host=/tmp/abc"


def test_produccion_no_cambia(monkeypatch):
    """Sin CDF_DATABASE_URL (producción) sigue igual: psycopg toma todo de las variables PG*."""
    for k, v in PG.items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("CDF_DATABASE_URL", raising=False)
    assert db.url_sqlalchemy() == "postgresql+psycopg://"
    assert db.conninfo() == ""
