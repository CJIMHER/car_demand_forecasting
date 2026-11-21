"""Las pruebas no dependen de la configuración del host.

En el servidor, «make test» corre en cdf_jobs, que recibe /opt/cdf/.env como env_file: con
PANEL_EJEMPLO_VISIBLE_PARA=tecnico,direccion, el caso «sin variable» de las pruebas del panel dejaba
de serlo. conftest.py (entorno_limpio) borra esa configuración al empezar cada prueba.
"""

from __future__ import annotations

import os
import subprocess
import sys

from conftest import RAIZ, del_host

# Lo que hay en el .env de producción (valores inventados) y lo que añade el compose a cdf_jobs.
ENTORNO_DE_PRODUCCION = {
    "PANEL_PERMITE_EJEMPLO": "1", "PANEL_EJEMPLO_VISIBLE_PARA": "tecnico,direccion", "PANEL_SESION_DIAS": "7",
    "PANEL_DOMINIO": "panel.invalid", "PANEL_DB_PASSWORD": "clave", "PANEL_PROXY": "10.9.9.9",
    "PANEL_DATABASE_URL": "dbname=no_existe", "TELEGRAM_BOT_TOKEN": "123:abc", "TELEGRAM_CHAT_ID": "-1",
    "BUCKET": "bucket-de-produccion", "BACKUP_PASSPHRASE": "frase", "RCLONE_REMOTE": "hz",
    "RCLONE_CONFIG_HZ_ENDPOINT": "https://bucket.invalid", "POSTGRES_PASSWORD": "pw",
    "CDF_SCRAPERS_DATA": "/datos/scrapers", "CDF_CONCESIONARIO": "/no/existe/concesionario.yaml",
    "CDF_VERSIONES": "/no/existe/versiones.yaml",
}

# Las que fallaban (o fallarían) con ese entorno antes del arreglo.
SENSIBLES = [
    "tests/test_contrato_panel.py::test_con_datos_de_ejemplo_cada_rol_ve_segun_la_variable",
    "tests/test_contrato_panel.py::test_al_sustituir_por_filas_reales_el_bloque_pierde_la_etiqueta",
    "tests/test_panel.py::test_caducidad_por_defecto_30_dias",
    "tests/test_panel.py::test_cookies_solo_del_host",
    "tests/test_segmentos.py::test_textos_reales_del_encargo",
]


def test_ninguna_prueba_ve_la_configuracion_del_host():
    assert [n for n in os.environ if del_host(n)] == []


def test_cubre_todo_el_env_de_ejemplo():
    """Cada variable de .env.example se limpia, salvo las de Postgres de pruebas (PG*)."""
    nombres = [l.split("=")[0] for l in (RAIZ / ".env.example").read_text().splitlines()
               if l and not l.startswith("#") and "=" in l]
    assert nombres and [n for n in nombres if not del_host(n)] == []


def test_con_el_entorno_de_produccion_las_pruebas_sensibles_pasan():
    env = {**os.environ, **ENTORNO_DE_PRODUCCION}
    r = subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *SENSIBLES],
                       cwd=RAIZ, env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-4000:] + r.stderr[-2000:]
