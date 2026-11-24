"""Piezas comunes de los conectores de Meta (ingesta/meta.py) y GA4 (ingesta/ga4.py).

- Peticiones HTTPS con reintentos: ante un límite de la API (429, o lo que cada API diga que es un
  límite) o un 5xx o un fallo de red, espera (Retry-After si viene; si no, esperas crecientes) y
  vuelve a intentarlo. Cualquier otro error no se reintenta. Ningún error lleva la URL, el token ni
  el texto de la excepción original.
- Brutos: las respuestas, tal cual, comprimidas con gzip y subidas al bucket ANTES de leerlas
  (<remoto>:<BUCKET>/<fuente>/raw/<día>/<run>_<nnn>.json.gz, como los scrapers). Si no se pueden
  subir, la descarga no escribe nada en Postgres (la de la noche siguiente vuelve a recargar 7 días).
- Inserción versionada en tablas de hechos de solo añadir: cada fila lleva una huella de su
  contenido; solo se añade si la clave es nueva o su contenido ha cambiado (Meta y GA4 corrigen los
  días pasados). Las vistas *_vigente dan la última versión de cada clave.
- Anotación en fuentes_estado (tipo 'descarga'): ok, error o sin_credenciales (sin credenciales la
  tarea no hace ninguna petición y no es un error: no salta ninguna alerta).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
import os
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable, Iterable, Mapping
from datetime import date, datetime, timezone
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

LOG = logging.getLogger("ingesta.conectores")
ESPERAS = (5, 15, 45, 90, 180)
MAX_RETRY_AFTER = 300
USER_AGENT = "cdf-conectores/1.0"


class ErrorDescarga(Exception):
    pass


class SinCredenciales(Exception):
    pass


def _urlopen(peticion, timeout):
    # Punto único de salida a la red: las pruebas lo sustituyen y ninguna llega a Internet.
    return urllib.request.urlopen(peticion, timeout=timeout)


def limpiar(texto: str, secretos: Iterable[str | None]) -> str:
    for s in secretos:
        if s:
            texto = texto.replace(s, "***")
    return texto


def peticion_json(url: str, *, metodo: str = "GET", cabeceras: Mapping[str, str] | None = None,
                  cuerpo: bytes | None = None, es_limite: Callable[[int, dict], bool] | None = None,
                  secretos: Iterable[str | None] = (), dormir=time.sleep, intentos: int = 6,
                  timeout: float = 60, al_responder: Callable[[Mapping[str, str]], None] | None = None
                  ) -> tuple[dict, bytes]:
    """(respuesta como JSON, cuerpo en bruto). Lanza ErrorDescarga si no puede. `al_responder` recibe las
    cabeceras (en minúsculas) de cada respuesta correcta: Meta dice en ellas cuánto lleva gastado de su
    límite de uso."""
    secretos = list(secretos)
    ultimo = "sin intentos"
    for n in range(1, intentos + 1):
        espera = ESPERAS[min(n - 1, len(ESPERAS) - 1)]
        pet = urllib.request.Request(url, data=cuerpo, method=metodo,
                                     headers={"User-Agent": USER_AGENT, "Accept": "application/json", **(cabeceras or {})})
        try:
            with _urlopen(pet, timeout) as r:
                bruto = r.read()
                cabeceras_resp = {str(k).lower(): str(v) for k, v in dict(getattr(r, "headers", None) or {}).items()}
            if al_responder:
                al_responder(cabeceras_resp)
            try:
                return json.loads(bruto.decode("utf-8")), bruto
            except (ValueError, UnicodeDecodeError):
                raise ErrorDescarga("respuesta que no es JSON") from None
        except urllib.error.HTTPError as exc:
            try:
                datos = json.loads(exc.read().decode("utf-8"))
            except Exception:
                datos = {}
            error = datos.get("error") if isinstance(datos.get("error"), dict) else {}
            mensaje = str(error.get("message") or error.get("status") or "")[:200]
            ultimo = limpiar(f"HTTP {exc.code} {mensaje}".strip(), secretos)
            limite = exc.code == 429 or bool(es_limite and es_limite(exc.code, datos))
            if limite:
                try:
                    espera = min(int(exc.headers.get("Retry-After") or espera), MAX_RETRY_AFTER)
                except (TypeError, ValueError):
                    pass
            elif exc.code < 500:
                raise ErrorDescarga(f"la API rechazó la petición: {ultimo}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
            ultimo = f"error de red ({type(exc).__name__})"
        if n < intentos:
            LOG.info("Reintento %s tras %s s: %s", n, espera, ultimo)
            dormir(espera)
    raise ErrorDescarga(f"sin respuesta tras {intentos} intentos: {ultimo}") from None


# --------------------------------------------------------------------------- brutos al bucket

def subir_rclone(carpeta: Path, destino: str) -> None:
    """Sube la carpeta al bucket con rclone copy (nunca sync: lo que ya hay no se borra)."""
    bucket = os.environ.get("BUCKET", "")
    if not bucket or not shutil.which("rclone"):
        raise ErrorDescarga("falta BUCKET o rclone: no se pueden guardar los brutos")
    remoto = os.environ.get("RCLONE_REMOTE", "hz")
    r = subprocess.run(["rclone", "copy", str(carpeta), f"{remoto}:{bucket}/{destino}"], capture_output=True,
                       text=True, timeout=600)
    if r.returncode != 0:
        raise ErrorDescarga(f"rclone terminó con código {r.returncode} al subir los brutos")


def guardar_brutos(fuente: str, run_id: str, dia: date, respuestas: list[bytes],
                   subir: Callable[[Path, str], None] | None = None) -> str:
    """Comprime cada respuesta y las sube al bucket. Devuelve la ruta en el bucket."""
    destino = f"{fuente}/raw/{dia.isoformat()}"
    with tempfile.TemporaryDirectory(prefix=f"cdf_{fuente}_") as tmp:
        for i, bruto in enumerate(respuestas, 1):
            with gzip.open(Path(tmp) / f"{run_id}_{i:03d}.json.gz", "wb", compresslevel=6) as g:
                g.write(bruto)
        (subir or subir_rclone)(Path(tmp), destino)
    return destino


# --------------------------------------------------------------------------- inserción versionada

def huella(fila: Mapping, columnas: Iterable[str]) -> str:
    datos = {c: fila[c] for c in columnas}
    return hashlib.sha256(json.dumps(datos, sort_keys=True, default=str, ensure_ascii=False).encode()).hexdigest()


def insertar_versionado(pg: psycopg.Connection, tabla: str, vista: str, clave: tuple[str, ...],
                        valores: tuple[str, ...], filas: list[dict], comunes: dict) -> tuple[int, int]:
    """Añade las filas cuya clave es nueva o cuyo contenido (huella) ha cambiado. (nuevas, iguales)."""
    if not filas:
        return 0, 0
    dias = sorted({f["dia"] for f in filas})
    vigentes = {tuple(r[:-1]): r[-1] for r in pg.execute(
        sql.SQL("SELECT {c}, huella FROM {v} WHERE dia BETWEEN %s AND %s").format(
            c=sql.SQL(", ").join(map(sql.Identifier, clave)), v=sql.Identifier(vista)), (dias[0], dias[-1]))}
    nuevas = []
    for f in filas:
        f["huella"] = huella(f, valores)
        if vigentes.get(tuple(f[c] for c in clave)) != f["huella"]:
            nuevas.append(f)
    if nuevas:
        cols = list(comunes) + list(dict.fromkeys(clave + valores)) + ["huella"]
        with pg.cursor() as cur:
            cur.executemany(
                sql.SQL("INSERT INTO {t} ({c}) VALUES ({p})").format(
                    t=sql.Identifier(tabla), c=sql.SQL(", ").join(map(sql.Identifier, cols)),
                    p=sql.SQL(", ").join(sql.Placeholder() * len(cols))),
                [tuple(Jsonb(v) if isinstance(v, dict) else v
                       for v in ([comunes[c] for c in comunes] + [f[c] for c in cols[len(comunes):]])) for f in nuevas])
    return len(nuevas), len(filas) - len(nuevas)


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def anotar(pg: psycopg.Connection, fuente: str, dia: date, estado: str, detalle: dict) -> None:
    anotar_tipo(pg, fuente, dia, "descarga", estado, detalle)


def anotar_tipo(pg: psycopg.Connection, fuente: str, dia: date, tipo: str, estado: str, detalle: dict) -> None:
    pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) VALUES (%s, %s, %s, %s, %s)",
               (fuente, tipo, dia, estado, Jsonb(detalle)))
