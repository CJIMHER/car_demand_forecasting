"""make panel-dev: el panel en el Mac, en http://localhost:8000, con datos sintéticos.

Levanta un Postgres 16 local y desechable (paquete pgserver, el mismo de las pruebas), aplica las
migraciones, escribe 60 días de capturas sintéticas con el código real de los scrapers (huecos y
una fuente que empieza más tarde, como en tests/escenario_mercado.py) y una exportación de colores,
las carga, precalcula,
carga los datos de ejemplo de Dirección, Ventas y Marketing (panel/ejemplo.py) y crea un usuario
de prueba por rol. Con datos de ejemplo, esas tres vistas solo las ve el rol de
PANEL_EJEMPLO_VISIBLE_PARA (por defecto, tecnico). El panel se conecta como cdf_panel, igual que en producción.

Solo para ver el panel antes de desplegar: nada de esto toca el servidor ni datos reales. Las
cookies del panel son Secure: Chrome y Firefox las aceptan en http://localhost.
"""

from __future__ import annotations

import os
import secrets
import sys
import tempfile
from datetime import date
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
USUARIOS = {
    "direccion": ("Prueba Dirección", "prueba-direccion-local"),
    "ventas": ("Prueba Ventas", "prueba-ventas-local"),
    "marketing": ("Prueba Marketing", "prueba-marketing-local"),
    "tecnico": ("Prueba Técnico", "prueba-tecnico-local"),
}


def preparar(base: Path, fin: date | None = None, dias: int = 60, escala: float = 4.0) -> str:
    """Postgres local con datos sintéticos y usuarios de prueba. Devuelve la URL de cdf_panel.
    `escala`: flotas cuatro veces mayores que en las pruebas, para que los segmentos del detalle de un
    modelo tengan muestra."""
    try:
        import pgserver
    except ImportError:
        sys.exit("Falta pgserver (solo Mac): .venv/bin/pip install pgserver==0.1.4")
    import psycopg
    from alembic import command
    from alembic.config import Config

    sys.path.insert(0, str(RAIZ / "tests"))
    from escenario_mercado import escenario

    from analitica import mercado as am
    from ingesta import colores, scrapers_vw as sv
    from ingesta.concesionario import cargar as cargar_concesionario
    from panel import ejemplo, rol_bd, usuarios

    srv = pgserver.get_server(str(base / "pg"), cleanup_mode="stop")
    admin = srv.get_uri()
    with psycopg.connect(admin, autocommit=True) as c:
        c.execute("DROP DATABASE IF EXISTS cdf_dev WITH (FORCE)")
        c.execute("CREATE DATABASE cdf_dev")
    raiz, _, query = admin.partition("?")
    url = raiz.rsplit("/", 1)[0] + "/cdf_dev" + ("?" + query if query else "")
    os.environ["CDF_DATABASE_URL"] = url
    os.environ["CDF_SCRAPERS_DATA"] = str(base / "scrapers")
    command.upgrade(Config(str(RAIZ / "alembic.ini")), "head")

    conc = cargar_concesionario()
    fin = fin or sv.hoy_madrid()
    print(f"Escribiendo {dias} días de capturas sintéticas hasta el {fin:%d/%m/%Y}…")
    escenario(base / "scrapers", fin, conc.provincia_codigo, conc.provincias_comunidad, conc.dealer_ids[0], dias=dias,
              escala=escala)
    with psycopg.connect(url, autocommit=True) as pg:
        sv.cargar(pg, dia=fin)
        colores.ejecutar(pg, fin)
        am.precalcular(pg, hasta=fin, conc=conc)
        ejemplo.cargar(pg, fin, conc)
        for rol, (nombre, clave) in USUARIOS.items():
            usuarios.alta(pg, rol, nombre, rol, clave)
        pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
        clave_rol = secrets.token_hex(32)
        rol_bd.activar(pg, clave_rol)
    return psycopg.conninfo.make_conninfo(url, user="cdf_panel", password=clave_rol)


def main() -> None:
    import uvicorn

    from panel.app import crear_app

    base = Path(tempfile.mkdtemp(prefix="cdf_panel_dev_"))
    dsn = preparar(base)
    print("\nUsuarios de prueba (solo en este Postgres local):")
    for rol, (_, clave) in USUARIOS.items():
        print(f"  {rol:<10} contraseña: {clave}")
    print("\nPanel en http://localhost:8000  (Ctrl+C para parar)\n")
    uvicorn.run(crear_app(dsn=dsn), host="127.0.0.1", port=8000, proxy_headers=False, access_log=False,
                server_header=False)


if __name__ == "__main__":
    main()
