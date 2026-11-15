"""Aviso por Telegram si falla la copia de las 09:30. Envío siempre simulado: nunca se llama a Telegram."""

import logging
import os
import subprocess
import sys
import urllib.error
from datetime import date

import pytest

from ingesta import aviso_copia, telegram
from sinteticos import RAIZ

TOKEN = "123456:SECRETO-de-prueba-AAAA"
DIA = date(2026, 11, 15)
SCRIPT = RAIZ / "infra/backup/pg_backup.sh"


def test_texto_del_aviso():
    assert aviso_copia.texto_aviso(DIA, 1, "subida al bucket") == (
        "CDF 15/11 · la copia de Postgres ha fallado\n"
        "Paso: subida al bucket (código 1)\n"
        "Detalle: registro de la copia de hoy (make logs)")


def test_aviso_enviado_y_anotado(pg):
    enviados = []
    assert aviso_copia.avisar(DIA, 1, "pg_dump y cifrado", enviar=lambda t: enviados.append(t) or 1)
    assert enviados == [aviso_copia.texto_aviso(DIA, 1, "pg_dump y cifrado")]
    fila = pg.execute("SELECT fuente, tipo, estado, detalle->>'aviso', detalle->>'texto' FROM fuentes_estado").fetchone()
    assert fila == ("telegram", "aviso", "ok", "copia", enviados[0])


def test_aviso_llega_aunque_postgres_no_responda(monkeypatch):
    # La copia puede fallar justamente porque Postgres está caído: el aviso se envía igual.
    monkeypatch.setenv("CDF_DATABASE_URL", "host=127.0.0.1 port=1 dbname=nada connect_timeout=2")
    enviados = []
    assert aviso_copia.avisar(DIA, 1, "pg_dump y cifrado", enviar=lambda t: enviados.append(t) or 1)
    assert len(enviados) == 1


def test_fallo_de_telegram_sin_token_y_anotado(pg, monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", TOKEN)
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "-100123")
    peticiones = []

    def red_caida(peticion, timeout):
        peticiones.append(peticion)
        raise urllib.error.URLError(f"fallo en https://api.telegram.org/bot{TOKEN}/sendMessage")

    monkeypatch.setattr(telegram, "_urlopen", red_caida)
    monkeypatch.setattr(telegram, "ESPERAS", (0, 0, 0))
    caplog.set_level(logging.DEBUG)
    assert aviso_copia.avisar(DIA, 1, "subida al bucket") is False   # mismo envío que el resumen: con reintentos
    assert len(peticiones) == 4
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE tipo = 'aviso'").fetchone()
    assert estado == "error" and "4 intentos" in detalle["error"]
    assert TOKEN not in str(detalle) + caplog.text


# ------------------------------------------------------------------ desde pg_backup.sh

def _entorno(tmp_path, python, pg_dump_falla=True):
    """pg_backup.sh con pg_dump roto (o bueno) y el «python» del aviso sustituido."""
    falsos = tmp_path / "bin"
    falsos.mkdir()
    (falsos / "rclone").write_text("#!/usr/bin/env bash\nexit 0\n")
    if pg_dump_falla:
        (falsos / "pg_dump").write_text("#!/usr/bin/env bash\necho roto >&2\nexit 1\n")
    (falsos / "psql").write_text("#!/usr/bin/env bash\ncat >/dev/null\nexit 0\n")
    for f in falsos.iterdir():
        f.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith(("TELEGRAM_", "PG"))}
    env.update({"PATH": f"{falsos}{os.pathsep}{os.environ['PATH']}", "BUCKET": "b", "BACKUP_PASSPHRASE": "x",
                "CDF_PYTHON": str(python)})
    return env


def _python_falso(tmp_path, codigo):
    registro = tmp_path / "aviso.txt"
    falso = tmp_path / "python_falso"
    falso.write_text(f'#!/usr/bin/env bash\nprintf "%s|" "$@" > "{registro}"\nexit {codigo}\n')
    falso.chmod(0o755)
    return falso, registro


@pytest.mark.parametrize("codigo_aviso", [0, 1])
def test_copia_fallida_avisa_y_sale_con_su_codigo(tmp_path, codigo_aviso):
    if not __import__("shutil").which("gpg"):
        pytest.skip("falta gpg")
    falso, registro = _python_falso(tmp_path, codigo_aviso)
    r = subprocess.run(["bash", str(SCRIPT)], env=_entorno(tmp_path, falso), capture_output=True, text=True,
                       timeout=60)
    assert r.returncode == 1, r.stdout + r.stderr       # el de pg_dump, haya ido bien o mal el aviso
    assert registro.read_text() == "-m|ingesta.aviso_copia|--codigo|1|--paso|pg_dump y cifrado|"
    if codigo_aviso:
        assert "el aviso a Telegram no se pudo enviar" in r.stdout


def test_falta_una_variable_tambien_avisa(tmp_path):
    falso, registro = _python_falso(tmp_path, 0)
    env = _entorno(tmp_path, falso)
    del env["BUCKET"]
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert "--paso|variables de entorno|" in registro.read_text()


def test_copia_fallida_con_el_aviso_real_sin_credenciales(tmp_path):
    # El módulo real en un proceso aparte, sin TELEGRAM_* en el entorno: no hay petición posible.
    if not __import__("shutil").which("gpg"):
        pytest.skip("falta gpg")
    env = _entorno(tmp_path, sys.executable)
    env["CDF_DATABASE_URL"] = "host=127.0.0.1 port=1 dbname=nada connect_timeout=2"
    r = subprocess.run(["bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)
    assert r.returncode == 1
    assert "faltan TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID" in r.stderr
    assert "el aviso a Telegram no se pudo enviar" in r.stdout
