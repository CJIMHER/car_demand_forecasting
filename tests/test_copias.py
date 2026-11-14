"""La copia diaria: pg_dump cifrado con gpg, subido con rclone (simulado aquí) y restaurable.

El rclone de esta prueba es un sustituto que copia a una carpeta local. La parte de
probar_restauracion.sh que usa Docker no se puede probar sin Docker; aquí se reproduce su núcleo
(descifrar + pg_restore en una base aparte + comparar recuentos).
"""

import os
import shutil
import subprocess
from pathlib import Path

import psycopg
import pytest

from ingesta import scrapers_vw as sv
from sinteticos import RAIZ, capturar, flota

RCLONE_FALSO = """#!/usr/bin/env bash
set -euo pipefail
orden="$1"; shift
case "$orden" in
  copyto) dst="$FAKE_BUCKET/${2#*:}"; mkdir -p "$(dirname "$dst")"; cp "$1" "$dst" ;;
  delete) echo "delete $*" >> "$FAKE_BUCKET/borrados.txt" ;;
  *) echo "orden no simulada: $orden" >&2; exit 1 ;;
esac
"""


def _binarios(pg_admin):
    _, bindir = pg_admin
    for b in ("pg_dump", "pg_restore", "psql"):
        if not (bindir and Path(bindir, b).exists()) and not shutil.which(b):
            pytest.skip(f"falta {b}")
    if not shutil.which("gpg"):
        pytest.skip("falta gpg")
    return bindir


def test_copia_cifrada_y_restaurable(pg_admin, pg_url, pg, datos, tmp_path):
    bindir = _binarios(pg_admin)
    capturar(datos, "vo", "2026-11-10", flota("VO", 12))
    capturar(datos, "vn", "2026-11-10", flota("VN", 7, fuente="vn"))
    capturar(datos, "vn_otros", "2026-11-10", flota("EH", 3, fuente="vn_otros"))
    sv.cargar(pg)

    falsos = tmp_path / "bin"
    falsos.mkdir()
    (falsos / "rclone").write_text(RCLONE_FALSO)
    (falsos / "rclone").chmod(0o755)
    bucket = tmp_path / "bucket"
    bucket.mkdir()

    # En el Mac la conexión va en la URL de pgserver; en el contenedor, en las variables PG*.
    info = psycopg.conninfo.conninfo_to_dict(pg_url)
    env = {**os.environ,
           "PATH": os.pathsep.join(p for p in (str(falsos), bindir, os.environ["PATH"]) if p),
           "BUCKET": "cdf-prueba", "RCLONE_REMOTE": "hz",
           "BACKUP_PASSPHRASE": "frase de prueba", "FAKE_BUCKET": str(bucket)}
    for clave, var in (("host", "PGHOST"), ("user", "PGUSER"), ("dbname", "PGDATABASE"), ("password", "PGPASSWORD")):
        if info.get(clave):
            env[var] = str(info[clave])
    r = subprocess.run(["bash", str(RAIZ / "infra/backup/pg_backup.sh")], env=env, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr

    copias = list((bucket / "cdf-prueba/backups/postgres").glob("cdf_*.dump.gpg"))
    assert len(copias) == 1
    cifrada = copias[0].read_bytes()
    assert not cifrada.startswith(b"PGDMP")  # no es un volcado en claro
    assert (copias[0].parent / (copias[0].name + ".sha256")).exists()
    assert "--min-age 30d" in (bucket / "borrados.txt").read_text()
    assert pg.execute("SELECT estado FROM fuentes_estado WHERE tipo = 'copia'").fetchone() == ("ok",)

    # Con otra frase no se descifra
    mala = subprocess.run(["gpg", "--batch", "--quiet", "--pinentry-mode", "loopback", "--passphrase", "otra",
                           "--decrypt", "-o", str(tmp_path / "x.dump"), str(copias[0])], capture_output=True)
    assert mala.returncode != 0

    # Núcleo de probar_restauracion.sh: descifrar, restaurar en una base aparte y comparar recuentos
    volcado = tmp_path / "cdf.dump"
    subprocess.run(["gpg", "--batch", "--quiet", "--pinentry-mode", "loopback", "--passphrase", "frase de prueba",
                    "--decrypt", "-o", str(volcado), str(copias[0])], check=True)
    with psycopg.connect(pg_url, autocommit=True) as c:
        c.execute("CREATE DATABASE cdf_restaurada_prueba")
    try:
        subprocess.run(["pg_restore", "-d", "cdf_restaurada_prueba", "--no-owner", "--no-privileges",
                        "--exit-on-error", str(volcado)], env=env, check=True, capture_output=True)
        consulta = ("SELECT snapshot_day, fuente, count(*) FROM stock_externo_snapshot GROUP BY 1, 2 ORDER BY 1, 2")
        destino = psycopg.conninfo.make_conninfo(pg_url, dbname="cdf_restaurada_prueba")
        with psycopg.connect(destino) as r2:
            assert r2.execute(consulta).fetchall() == pg.execute(consulta).fetchall()
            assert r2.execute("SELECT count(*) FROM eventos_externos").fetchone()[0] == 22
            # Los disparadores de «solo añadir» también vuelven con la copia
            with pytest.raises(psycopg.errors.RaiseException):
                r2.execute("DELETE FROM stock_externo_snapshot")
    finally:
        with psycopg.connect(pg_url, autocommit=True) as c:
            c.execute("DROP DATABASE cdf_restaurada_prueba WITH (FORCE)")


def test_copia_falla_si_falla_pg_dump(tmp_path, pg_url, pg):
    falsos = tmp_path / "bin"
    falsos.mkdir()
    (falsos / "rclone").write_text(RCLONE_FALSO)
    (falsos / "pg_dump").write_text("#!/usr/bin/env bash\necho roto >&2\nexit 1\n")
    for f in falsos.iterdir():
        f.chmod(0o755)
    if not shutil.which("gpg"):
        pytest.skip("falta gpg")
    env = {**os.environ, "PATH": f"{falsos}{os.pathsep}{os.environ['PATH']}", "BUCKET": "b",
           "BACKUP_PASSPHRASE": "x", "FAKE_BUCKET": str(tmp_path)}
    r = subprocess.run(["bash", str(RAIZ / "infra/backup/pg_backup.sh")], env=env, capture_output=True, text=True)
    assert r.returncode != 0
    assert not list(tmp_path.rglob("cdf_*.dump.gpg"))
