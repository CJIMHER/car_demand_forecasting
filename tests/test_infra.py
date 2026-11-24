"""Comprobaciones estáticas de la infraestructura (no hace falta Docker) y del entorno de cron."""

import os
import re
import stat
from pathlib import Path
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
    # Solo Caddy publica puertos, y solo el 80 y el 443
    publican = {n for n, s in COMPOSE["services"].items() if s.get("ports")}
    assert publican == {"caddy"}
    assert {str(p).split(":")[0] for p in COMPOSE["services"]["caddy"]["ports"]} == {"80", "443"}
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
           "POSTGRES_PASSWORD": "no-hace-falta", "TELEGRAM_CHAT_VENTAS": "-200", "TELEGRAM_CHAT_DIRECCION": "-300",
           "TELEGRAM_CHAT_MARKETING": "-400", "META_ACCESS_TOKEN": "EAAB-meta", "META_AD_ACCOUNT_ID": "123",
           "GA4_PROPERTY_ID": "456", "GA4_CREDENCIALES": "/secretos/ga4.json", "PANEL_DB_PASSWORD": "no-hace-falta"}
    subprocess.run(["bash", str(RAIZ / "infra/jobs/exportar_entorno.sh"), str(destino)], env=env, check=True)
    assert stat.S_IMODE(destino.stat().st_mode) == 0o600
    texto = destino.read_text()
    assert "OTRA_COSA_SECRETA" not in texto and "POSTGRES_PASSWORD" not in texto and "PANEL_DB_PASSWORD" not in texto
    # Lo que lee una tarea de cron al cargar el fichero
    leido = subprocess.run(["bash", "-c", f". {destino}; env"], env={"PATH": os.environ["PATH"]},
                           capture_output=True, text=True, check=True).stdout
    for clave in ("PGHOST", "BUCKET", "RCLONE_CONFIG_HZ_TYPE", "RCLONE_CONFIG_HZ_SECRET_ACCESS_KEY",
                  "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "TELEGRAM_CHAT_VENTAS", "TELEGRAM_CHAT_DIRECCION",
                  "TELEGRAM_CHAT_MARKETING", "META_ACCESS_TOKEN", "META_AD_ACCOUNT_ID", "GA4_PROPERTY_ID",
                  "GA4_CREDENCIALES"):
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
        ("0", "6", "cdf", "meta"), ("10", "6", "cdf", "ga4"), ("30", "6", "cdf", "walcu_api"), ("0", "7", "cdf", "gtrends"),
        ("30", "8", "cdf", "cargar"), ("0", "9", "cdf", "comprobar"), ("10", "9", "cdf", "precalcular"),
        ("15", "9", "cdf", "alertas"), ("30", "9", "cdf", "copia")]
    # Los avisos de los botones de Ventas, cada minuto y también como cdf
    assert "* * * * * cdf /app/infra/jobs/tarea.sh avisos" in CRONTAB.splitlines()
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


# --- La copia funciona con un HOME no escribible (en cdf_jobs, HOME no debe ser necesario para gpg)

def test_copia_con_home_de_solo_lectura(pg_admin, pg_url, pg, datos, tmp_path):
    import glob
    import shutil

    import psycopg

    _, bindir = pg_admin
    if not shutil.which("gpg") or not (shutil.which("pg_dump") or (bindir and (Path(bindir) / "pg_dump").exists())):
        pytest.skip("faltan gpg o pg_dump")
    capturar(datos, "vo", "2026-11-10", flota("VO", 6))
    sv.cargar(pg)

    falsos = tmp_path / "bin"
    falsos.mkdir()
    (falsos / "rclone").write_text(
        '#!/usr/bin/env bash\nset -e\necho "${RCLONE_CONFIG-SIN}" >> "$FAKE_BUCKET.config"\n'
        '[ "$1" = copyto ] && { d="$FAKE_BUCKET/${3#*:}"; mkdir -p "$(dirname "$d")"; cp "$2" "$d"; }\nexit 0\n')
    (falsos / "rclone").chmod(0o755)
    (falsos / "python_aviso").write_text('#!/usr/bin/env bash\ntouch "$FAKE_BUCKET.aviso"\n')
    (falsos / "python_aviso").chmod(0o755)
    home = tmp_path / "home_solo_lectura"
    home.mkdir()
    home.chmod(0o555)
    frase = "frase-secreta-de-prueba-4c9e"

    # Conexión SOLO por variables PG*, como en el contenedor (sin CDF_DATABASE_URL)
    info = psycopg.conninfo.conninfo_to_dict(pg_url)
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PG", "GNUPG")) and k not in ("CDF_DATABASE_URL", "HOME")}
    env.update({"HOME": str(home), "BUCKET": "b", "BACKUP_PASSPHRASE": frase, "FAKE_BUCKET": str(tmp_path / "bucket"), "CDF_PYTHON": str(falsos / "python_aviso"),
                "PATH": os.pathsep.join(p for p in (str(falsos), bindir, os.environ["PATH"]) if p)})
    for clave, var in (("host", "PGHOST"), ("port", "PGPORT"), ("user", "PGUSER"), ("password", "PGPASSWORD"),
                       ("dbname", "PGDATABASE")):
        valor = info.get(clave) or os.environ.get(var)
        if valor:
            env[var] = str(valor)

    antes = set(glob.glob("/tmp/cdf_gnupg.*"))
    try:
        r = subprocess.run(["bash", str(RAIZ / "infra/backup/pg_backup.sh")], env=env,
                           capture_output=True, text=True, timeout=120)
    finally:
        home.chmod(0o755)
    assert r.returncode == 0, r.stdout + r.stderr
    assert frase not in r.stdout + r.stderr
    assert list(home.iterdir()) == []                       # nada escrito en HOME
    assert set(glob.glob("/tmp/cdf_gnupg.*")) == antes      # el GNUPGHOME temporal se borra
    copia = next((tmp_path / "bucket").rglob("cdf_*.dump.gpg"))
    assert not copia.read_bytes().startswith(b"PGDMP")
    assert pg.execute("SELECT estado FROM fuentes_estado WHERE tipo = 'copia'").fetchone() == ("ok",)
    assert not (tmp_path / "bucket.aviso").exists()          # copia correcta: no se avisa a Telegram
    # rclone sin fichero de configuración (sin el aviso de rclone.conf); el remoto, por variables
    assert set((tmp_path / "bucket.config").read_text().split()) == {"/dev/null"}


def test_rclone_sin_aviso_de_rclone_conf():
    for script in ("infra/backup/pg_backup.sh", "infra/backup/probar_restauracion.sh", "infra/jobs/tarea.sh"):
        texto = (RAIZ / script).read_text()
        assert "export RCLONE_CONFIG=/dev/null" in texto, script
        # antes de la primera llamada a rclone
        primera = min(i for i, l in enumerate(texto.splitlines())
                      if re.match(r"\s*(\S+=\"\$\()?rclone ", l) or "/pg_backup.sh" in l and "copia)" in l)
        exporta = next(i for i, l in enumerate(texto.splitlines()) if "export RCLONE_CONFIG=/dev/null" in l)
        assert exporta < primera, script
    assert re.search(r"^\s+RCLONE_CONFIG=/dev/null$", DOCKERFILE, re.M)   # make copia / docker exec
    assert "RCLONE_CONFIG_HZ" not in DOCKERFILE                          # el remoto sigue en el .env


def test_gpg_sin_frase_en_la_linea_de_ordenes():
    for script in ("infra/backup/pg_backup.sh", "infra/backup/probar_restauracion.sh"):
        texto = (RAIZ / script).read_text()
        llamadas = [l for l in texto.splitlines() if re.search(r"\bgpg\b", l) and not l.lstrip().startswith("#")]
        assert llamadas, script
        assert "--passphrase " not in texto and '--passphrase "' not in texto
        assert "--batch" in texto and "--pinentry-mode loopback" in texto and "--passphrase-fd" in texto
        assert "GNUPGHOME" in texto and "--no-symkey-cache" in texto


def test_home_de_cdf_escribible_en_la_imagen():
    assert re.search(r"useradd [^\n]*-m -d /home/cdf", DOCKERFILE)
    assert "-d /app" not in DOCKERFILE
