"""Despliegue del panel (sin Docker): compose, imagen, Caddy, cron, Makefile y .env.example."""

import os
import re
import shutil
import subprocess

import pytest
import yaml

from sinteticos import RAIZ

COMPOSE = yaml.safe_load((RAIZ / "infra/docker-compose.yml").read_text())
PANEL = COMPOSE["services"]["panel"]
CADDY = COMPOSE["services"]["caddy"]

# El Makefile se ejecuta en el host y .env.example es una plantilla del host: en la imagen de
# cdf_jobs (donde corre «make test») no hay make y .dockerignore excluye .env*. Ahí se saltan.
sin_make = pytest.mark.skipif(shutil.which("make") is None,
                              reason="sin make (contenedor cdf_jobs): el Makefile se prueba en el host")
sin_env_example = pytest.mark.skipif(not (RAIZ / ".env.example").exists(),
                                     reason="sin .env.example (lo excluye .dockerignore): se prueba en el host")


def _make(tmp_path, objetivo, env_extra: str):
    """Lanza un objetivo del Makefile con un «docker» falso que apunta lo que se le pide."""
    falsos = tmp_path / "bin"
    falsos.mkdir()
    registro = tmp_path / "docker.txt"
    (falsos / "docker").write_text(f'#!/usr/bin/env bash\necho "$*" >> "{registro}"\nexit 0\n')
    (falsos / "docker").chmod(0o755)
    env_file = tmp_path / ".env"
    env_file.write_text("POSTGRES_USER=cdf\nPOSTGRES_PASSWORD=abc\nPOSTGRES_DB=cdf\n" + env_extra)
    env = {"PATH": f"{falsos}{os.pathsep}{os.environ['PATH']}", "HOME": str(tmp_path)}
    r = subprocess.run(["make", "--no-print-directory", "-C", str(RAIZ), objetivo, f"ENV_FILE={env_file}",
                        "CDF_UID=1001", "CDF_GID=1001"], env=env, capture_output=True, text=True, timeout=60)
    return r, (registro.read_text() if registro.exists() else "")


@sin_make
def test_sin_panel_dominio_no_arrancan_ni_panel_ni_caddy(tmp_path):
    for i, extra in enumerate(("", "PANEL_DOMINIO=\nPANEL_DB_PASSWORD=" + "a" * 64 + "\n")):
        d = tmp_path / f"caso{i}"
        d.mkdir()
        r, docker = _make(d, "panel-deploy", extra)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "no se arrancan" in r.stdout
        assert " up " not in f" {docker} " and " build " not in f" {docker} " and "rol_bd" not in docker


@sin_make
def test_con_panel_dominio_rol_imagen_y_arranque(tmp_path):
    r, docker = _make(tmp_path, "panel-deploy", "PANEL_DOMINIO=panel.example.invalid\nPANEL_DB_PASSWORD=" + "a" * 64 + "\n")
    assert r.returncode == 0, r.stdout + r.stderr
    lineas = docker.splitlines()
    assert any("run --rm --no-deps -u cdf jobs python -m panel.rol_bd" in l for l in lineas)
    assert any(l.endswith("--profile panel build panel") for l in lineas)
    assert any("--profile panel up -d --wait --force-recreate panel caddy" in l for l in lineas)
    assert "a" * 64 not in r.stdout + r.stderr + docker          # la contraseña no sale en la orden


@sin_make
def test_con_panel_dominio_sin_contrasena_del_rol_falla(tmp_path):
    r, docker = _make(tmp_path, "panel-deploy", "PANEL_DOMINIO=panel.example.invalid\n")
    assert r.returncode != 0 and "PANEL_DB_PASSWORD" in r.stdout + r.stderr
    assert " up " not in f" {docker} "


@sin_make
@pytest.mark.parametrize("orden", ["cargar", "borrar"])
def test_ejemplo_se_niega_sin_panel_permite_ejemplo(tmp_path, orden):
    for i, extra in enumerate(("", "PANEL_PERMITE_EJEMPLO=\n", "PANEL_PERMITE_EJEMPLO=0\n", "PANEL_PERMITE_EJEMPLO=si\n")):
        d = tmp_path / f"caso{i}"
        d.mkdir()
        r, docker = _make(d, f"ejemplo-{orden}", extra)
        assert r.returncode != 0 and "PANEL_PERMITE_EJEMPLO=1" in r.stdout + r.stderr
        assert docker == ""                                         # ni se llega a llamar a docker
    r, docker = _make(tmp_path, f"ejemplo-{orden}", "PANEL_PERMITE_EJEMPLO=1\n")
    assert r.returncode == 0, r.stdout + r.stderr
    assert f"exec -T -u cdf -e PANEL_PERMITE_EJEMPLO=1 jobs python -m panel.ejemplo {orden}" in docker


def test_deploy_llama_a_panel_deploy():
    makefile = (RAIZ / "Makefile").read_text()
    deploy = makefile.split("\ndeploy:", 1)[1].split("\n\n", 1)[0]
    assert "panel-deploy" in deploy


def test_panel_y_caddy_solo_con_el_perfil_panel():
    assert PANEL["profiles"] == ["panel"] and CADDY["profiles"] == ["panel"]
    for nombre, s in COMPOSE["services"].items():
        if nombre not in ("panel", "caddy"):
            assert "profiles" not in s, nombre
    # Una variable vacía no puede romper el resto del compose (se interpola aunque el perfil no esté activo)
    texto = (RAIZ / "infra/docker-compose.yml").read_text()
    assert "${PANEL_DB_PASSWORD:-}" in texto and "${PANEL_DOMINIO:-}" in texto
    assert "PANEL_DB_PASSWORD:?" not in texto and "PANEL_DOMINIO:?" not in texto


def test_panel_sin_privilegios_y_sin_secretos_ajenos():
    assert "ports" not in PANEL
    assert PANEL["read_only"] is True and PANEL["cap_drop"] == ["ALL"]
    assert "no-new-privileges:true" in PANEL["security_opt"]
    assert "env_file" not in PANEL                              # ni Telegram, ni bucket, ni frase de copias
    env = PANEL["environment"]
    assert env["PGUSER"] == "cdf_panel" and env["PANEL_PROXY"] == "caddy"
    assert env["PANEL_EJEMPLO_VISIBLE_PARA"] == "${PANEL_EJEMPLO_VISIBLE_PARA:-tecnico}"
    assert "PANEL_PERMITE_EJEMPLO" not in env                    # el panel nunca escribe datos de ejemplo
    assert set(PANEL["networks"]) == {"cdf_net", "cdf_web"} and CADDY["networks"] == ["cdf_web"]
    dockerfile = (RAIZ / "panel/Dockerfile").read_text()
    assert re.search(r"^USER panel$", dockerfile, re.M)
    assert "--no-proxy-headers" in dockerfile and "--no-access-log" in dockerfile
    assert "HEALTHCHECK" in dockerfile and "/salud" in dockerfile


def test_caddy_https_para_panel_dominio():
    caddyfile = (RAIZ / "infra/caddy/Caddyfile").read_text()
    assert "{$PANEL_DOMINIO}" in caddyfile and "reverse_proxy panel:8000" in caddyfile
    assert "header_up X-Forwarded-For {remote_host}" in caddyfile
    assert "admin off" in caddyfile
    assert re.match(r"caddy:\d+\.\d+\.\d+$", CADDY["image"])     # versión fija
    assert "./caddy/Caddyfile:/etc/caddy/Caddyfile:ro" in CADDY["volumes"]


@sin_env_example
def test_variables_nuevas_en_env_example_sin_valor():
    lineas = (RAIZ / ".env.example").read_text().splitlines()
    variables = dict(l.split("=", 1) for l in lineas if re.match(r"^[A-Z_]+=", l))
    for v in ("PANEL_DOMINIO", "PANEL_DB_PASSWORD", "PANEL_SESION_DIAS", "PANEL_PERMITE_EJEMPLO",
              "PANEL_EJEMPLO_VISIBLE_PARA"):
        assert v in variables and variables[v] == "", v


def test_precalculo_en_tarea_y_cron():
    tarea = (RAIZ / "infra/jobs/tarea.sh").read_text()
    assert "precalcular) python -m analitica.mercado --ultimos 8" in tarea
    makefile = (RAIZ / "Makefile").read_text()
    assert "python -m analitica.mercado $${DIA:+--dia $$DIA}" in makefile


@pytest.mark.parametrize("ruta", ["panel/static/vendor/htmx", "panel/static/vendor/echarts",
                                  "panel/static/fonts/inter", "panel/static/fonts/space-grotesk"])
def test_recursos_de_terceros_en_el_repositorio_con_licencia(ruta):
    assert (RAIZ / ruta / "LICENSE").stat().st_size > 500 or (RAIZ / ruta / "LICENSE").read_text().strip()
    versiones = (RAIZ / "panel/static/VERSIONES.md").read_text()
    assert ruta.split("/")[-1] in versiones
