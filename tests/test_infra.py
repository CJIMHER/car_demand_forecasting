"""Comprobaciones estáticas de la infraestructura (no hace falta Docker) y del entorno de cron."""

import os
import re
import stat
import subprocess

import pytest
import yaml

from ingesta import scrapers_vw as sv
from sinteticos import RAIZ, capturar, flota

COMPOSE = yaml.safe_load((RAIZ / "infra/docker-compose.yml").read_text())
DOCKERFILE = (RAIZ / "infra/jobs/Dockerfile").read_text()
CRONTAB = (RAIZ / "infra/jobs/crontab").read_text()


# --- B4: Postgres no publica puertos hacia fuera

def test_postgres_sin_puertos_publicos():
    for nombre, servicio in COMPOSE["services"].items():
        for p in servicio.get("ports", []):
            assert str(p).startswith("127.0.0.1:"), f"{nombre} publica {p} fuera de 127.0.0.1"
    assert "ports" not in COMPOSE["services"]["postgres"]
    assert "network_mode" not in COMPOSE["services"]["postgres"]


# --- B1: rclone por variables RCLONE_CONFIG_HZ_*, sin fichero

def test_rclone_sin_fichero_de_configuracion():
    jobs = COMPOSE["services"]["jobs"]
    texto = (RAIZ / "infra/docker-compose.yml").read_text()
    assert "rclone.conf" not in "".join(jobs.get("volumes", []))
    assert "RCLONE_CONFIG" not in (jobs.get("environment") or {})  # nada de RCLONE_CONFIG=fichero
    assert "RCLONE_CONF_HOST" not in texto
    assert jobs["env_file"] == ["${CDF_ENV_FILE:-/opt/cdf/.env}"]


def test_entorno_de_cron_filtrado(tmp_path):
    destino = tmp_path / "jobs.env"
    env = {"PATH": os.environ["PATH"], "PGHOST": "postgres", "BUCKET": "cdf-bucket",
           "RCLONE_CONFIG_HZ_TYPE": "s3", "RCLONE_CONFIG_HZ_SECRET_ACCESS_KEY": "abc$def 'x'",
           "TELEGRAM_BOT_TOKEN": "1:tok", "TELEGRAM_CHAT_ID": "-100", "OTRA_COSA_SECRETA": "no",
           "POSTGRES_PASSWORD": "no-hace-falta"}
    subprocess.run(["bash", str(RAIZ / "infra/jobs/exportar_entorno.sh"), str(destino)], env=env, check=True)
    assert stat.S_IMODE(destino.stat().st_mode) == 0o600
    texto = destino.read_text()
    assert "OTRA_COSA_SECRETA" not in texto and "POSTGRES_PASSWORD" not in texto
    # Lo que lee una tarea de cron al cargar el fichero
    leido = subprocess.run(["bash", "-c", f". {destino}; env"], env={"PATH": os.environ["PATH"]},
                           capture_output=True, text=True, check=True).stdout
    for clave in ("PGHOST", "BUCKET", "RCLONE_CONFIG_HZ_TYPE", "RCLONE_CONFIG_HZ_SECRET_ACCESS_KEY",
                  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"):
        assert f"{clave}={env[clave]}\n" in leido


# --- B2: cron en hora de Madrid

def test_imagen_en_hora_de_madrid():
    assert re.search(r"apt-get install[^\n]*\btzdata\b", DOCKERFILE)
    assert "ENV TZ=Europe/Madrid" in DOCKERFILE
    assert "ln -snf /usr/share/zoneinfo/$TZ /etc/localtime" in DOCKERFILE
    assert COMPOSE["services"]["jobs"]["environment"]["TZ"] == "Europe/Madrid"
    entrada = (RAIZ / "infra/jobs/entrypoint.sh").read_text()
    assert "/usr/share/zoneinfo/Europe/Madrid" in entrada and "CET|CEST" in entrada


def test_crontab_a_las_horas_previstas_y_como_cdf():
    lineas = [l.split() for l in CRONTAB.splitlines() if re.match(r"^\d", l)]
    assert [(l[0], l[1], l[5], l[-1]) for l in lineas] == [
        ("30", "8", "cdf", "cargar"), ("0", "9", "cdf", "comprobar"), ("30", "9", "cdf", "copia")]
    assert "CRON_TZ" not in CRONTAB  # cron usa la hora local del sistema, que es Madrid


# --- B3: mismo UID/GID que cdf, parametrizado, y nada como root sobre las bases

def test_uid_gid_parametrizados_sin_valor_fijo():
    args = COMPOSE["services"]["jobs"]["build"]["args"]
    assert args["CDF_UID"].startswith("${CDF_UID:?") and args["CDF_GID"].startswith("${CDF_GID:?")
    assert "1000" not in DOCKERFILE
    assert re.search(r"^ARG CDF_UID$", DOCKERFILE, re.M) and re.search(r"^ARG CDF_GID$", DOCKERFILE, re.M)
    makefile = (RAIZ / "Makefile").read_text()
    assert "id -u cdf" in makefile and "id -g cdf" in makefile


def test_carga_se_niega_a_correr_como_root(datos, monkeypatch):
    capturar(datos, "vo", "2026-11-10", flota("VO", 3))
    monkeypatch.setattr(sv.os, "geteuid", lambda: 0)
    with pytest.raises(sv.ErrorCarga, match="root"):
        sv.abrir_sqlite(sv.FUENTES["vo"])
    assert not list(datos.rglob("*-shm"))
