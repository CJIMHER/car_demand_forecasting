"""Conector de la GA4 Data API (solo lectura: cuenta de servicio con el rol Lector de la propiedad).
Inactivo hasta tener credenciales.

Variables del .env:
  GA4_PROPERTY_ID     id numérico de la propiedad de GA4
  GA4_CREDENCIALES    ruta (dentro del contenedor) del fichero JSON de la cuenta de servicio. El
                      fichero va FUERA del repositorio (en el servidor, /opt/cdf/secretos/, montado
                      en /secretos de cdf_jobs), es del usuario cdf y tiene permisos 600: con
                      permisos más abiertos la descarga se niega.
Sin las dos variables, la tarea no hace ninguna petición y anota «sin_credenciales» (no es un error).

Por día, fuente, medio, campaña (utm_campaign) y página de destino: sesiones, sesiones con
interacción y eventos clave, con el ID de la campaña y la agrupación de canales por defecto de GA4
(respaldo de config/canales.yaml). Ningún dato personal ni identificador de usuario: no se piden
dimensiones de usuario, y de la página de destino solo se guarda la ruta (sin parámetros, que
podrían llevar datos de un formulario).

Cada noche (06:10) recarga los últimos 7 días (GA4 también corrige): una fila solo se añade a
ga4_trafico_dia si su contenido ha cambiado. Las respuestas brutas van comprimidas al bucket
(ga4/raw/<día>/) antes de leerlas.

Acceso: OAuth 2.0 de cuenta de servicio (JWT firmado con RS256, alcance analytics.readonly), con la
librería cryptography, que solo se carga si hay credenciales.

Uso:
  python -m ingesta.ga4                           últimos 7 días (cron; make ga4)
  python -m ingesta.ga4 --desde 2026-11-01 --hasta 2026-11-20
"""

from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import stat
import sys
import time
import urllib.parse
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path

import psycopg

from ingesta import campanas as cmp
from ingesta import conectores as cx
from ingesta.db import conectar
from ingesta.scrapers_vw import hoy_madrid

LOG = logging.getLogger("ingesta.ga4")
FUENTE = "ga4"
API = "https://analyticsdata.googleapis.com/v1beta"
TOKEN_URI = "https://oauth2.googleapis.com/token"
ALCANCE = "https://www.googleapis.com/auth/analytics.readonly"
ORIGEN = "ga4_data_api v1beta"
DIMENSIONES = ["date", "sessionSource", "sessionMedium", "sessionCampaignName", "landingPage", "sessionCampaignId",
               "sessionDefaultChannelGroup"]
METRICAS = ["sessions", "engagedSessions", "keyEvents"]
POR_PAGINA = 10_000
DIAS = 7
CLAVE = ("propiedad_id", "dia", "fuente", "medio", "campana", "pagina_destino")
VALORES = ("sesiones", "sesiones_interaccion", "eventos_clave", "campana_id", "canal_ga4")
METRICAS_SUMA = ("sesiones", "sesiones_interaccion", "eventos_clave")


def credenciales(entorno: Mapping[str, str]) -> tuple[str, Path]:
    propiedad = (entorno.get("GA4_PROPERTY_ID") or "").strip().removeprefix("properties/")
    fichero = (entorno.get("GA4_CREDENCIALES") or "").strip()
    if not propiedad or not fichero:
        raise cx.SinCredenciales("faltan GA4_PROPERTY_ID o GA4_CREDENCIALES en el .env")
    ruta = Path(fichero)
    if not ruta.is_file():
        raise cx.ErrorDescarga("GA4_CREDENCIALES no apunta a un fichero")
    if stat.S_IMODE(ruta.stat().st_mode) & 0o077:
        raise cx.ErrorDescarga("el fichero de la cuenta de servicio debe tener permisos 600")
    return propiedad, ruta


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).rstrip(b"=").decode()


def jwt_firmado(cuenta: dict, ahora: int) -> str:
    """JWT de la cuenta de servicio para pedir el token de acceso (RS256)."""
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise cx.ErrorDescarga("falta la librería cryptography (requirements.txt)") from None
    cabecera = {"alg": "RS256", "typ": "JWT", "kid": cuenta.get("private_key_id")}
    carga = {"iss": cuenta["client_email"], "scope": ALCANCE, "aud": cuenta.get("token_uri") or TOKEN_URI,
             "iat": ahora, "exp": ahora + 3600}
    firmable = f"{_b64(json.dumps(cabecera).encode())}.{_b64(json.dumps(carga).encode())}"
    clave = serialization.load_pem_private_key(cuenta["private_key"].encode(), password=None)
    firma = clave.sign(firmable.encode(), padding.PKCS1v15(), hashes.SHA256())
    return f"{firmable}.{_b64(firma)}"


def token_acceso(ruta: Path, dormir=time.sleep, firmar=jwt_firmado) -> tuple[str, list[str]]:
    """(token de acceso, secretos que no deben salir en ningún error)."""
    try:
        cuenta = json.loads(ruta.read_text(encoding="utf-8"))
        cuenta["client_email"], cuenta["private_key"]
    except (OSError, ValueError, KeyError, TypeError):
        raise cx.ErrorDescarga("el fichero de la cuenta de servicio no es válido") from None
    asercion = firmar(cuenta, int(time.time()))
    cuerpo = urllib.parse.urlencode({"grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
                                     "assertion": asercion}).encode()
    datos, _ = cx.peticion_json(cuenta.get("token_uri") or TOKEN_URI, metodo="POST", cuerpo=cuerpo,
                                cabeceras={"Content-Type": "application/x-www-form-urlencoded"},
                                secretos=[asercion], dormir=dormir)
    token = datos.get("access_token")
    if not token:
        raise cx.ErrorDescarga("Google no ha devuelto un token de acceso")
    return token, [asercion, token]


def es_limite(codigo: int, datos: dict) -> bool:
    error = datos.get("error") if isinstance(datos.get("error"), dict) else {}
    return error.get("status") == "RESOURCE_EXHAUSTED"


def _opcional(valor: str) -> str | None:
    """«(not set)», «(other)» o vacío → None."""
    v = (valor or "").strip()
    return None if not v or (v.startswith("(") and v.endswith(")")) else v


def ruta_pagina(valor: str) -> str:
    """Solo la ruta: sin parámetros ni fragmento."""
    return valor.split("?", 1)[0].split("#", 1)[0] or "/"


def leer_fila(r: dict, propiedad: str) -> dict:
    """Una fila del informe → fila de ga4_trafico_dia (solo estas columnas)."""
    d = [x.get("value", "") for x in r.get("dimensionValues") or []]
    m = [x.get("value", "0") for x in r.get("metricValues") or []]
    if len(d) != len(DIMENSIONES) or len(m) != len(METRICAS):
        raise cx.ErrorDescarga("fila de GA4 con dimensiones o métricas inesperadas")
    return {"propiedad_id": propiedad, "dia": datetime.strptime(d[0], "%Y%m%d").date(), "fuente": d[1],
            "medio": d[2], "campana": d[3], "pagina_destino": ruta_pagina(d[4]), "sesiones": int(float(m[0])),
            "sesiones_interaccion": int(float(m[1])), "eventos_clave": float(m[2]), "campana_id": _opcional(d[5]),
            "canal_ga4": _opcional(d[6])}


def _agrupar(filas: list[dict]) -> list[dict]:
    """Al quitar los parámetros, dos páginas de destino pueden quedar iguales: se suman. El ID de campaña
    y la agrupación de canales, de la que tiene más sesiones (GA4 da uno por fuente, medio y campaña)."""
    juntas: dict[tuple, dict] = {}
    for f in filas:
        k = tuple(f[c] for c in CLAVE)
        if k in juntas:
            j = juntas[k]
            if f["sesiones"] > j["_max"]:
                j.update(campana_id=f["campana_id"] or j["campana_id"], canal_ga4=f["canal_ga4"] or j["canal_ga4"],
                         _max=f["sesiones"])
            for c in METRICAS_SUMA:
                j[c] += f[c]
        else:
            juntas[k] = dict(f, _max=f["sesiones"])
    for j in juntas.values():
        del j["_max"]
    return list(juntas.values())


def descargar(token: str, propiedad: str, desde: date, hasta: date, secretos: list[str], dormir=time.sleep
              ) -> tuple[list[dict], list[bytes]]:
    filas, brutos, offset = [], [], 0
    while True:
        cuerpo = {"dateRanges": [{"startDate": desde.isoformat(), "endDate": hasta.isoformat()}],
                  "dimensions": [{"name": n} for n in DIMENSIONES], "metrics": [{"name": n} for n in METRICAS],
                  "limit": POR_PAGINA, "offset": offset, "keepEmptyRows": False}
        datos, bruto = cx.peticion_json(f"{API}/properties/{propiedad}:runReport", metodo="POST",
                                        cuerpo=json.dumps(cuerpo).encode(), es_limite=es_limite, secretos=secretos,
                                        cabeceras={"Authorization": f"Bearer {token}",
                                                   "Content-Type": "application/json"}, dormir=dormir)
        brutos.append(bruto)
        pagina = datos.get("rows") or []
        filas += [leer_fila(r, propiedad) for r in pagina]
        offset += len(pagina)
        total = int(datos.get("rowCount") or 0)
        if not pagina or offset >= total:
            break
        if len(brutos) > 1000:
            raise cx.ErrorDescarga("demasiadas páginas")
    return _agrupar(filas), brutos


def ejecutar_con(pg: psycopg.Connection, hoy: date, entorno: Mapping[str, str], desde: date | None = None,
                 hasta: date | None = None, subir=None, dormir=time.sleep, firmar=jwt_firmado) -> dict:
    propiedad, ruta = credenciales(entorno)
    token, secretos = token_acceso(ruta, dormir, firmar)
    hasta = hasta or hoy - timedelta(days=1)
    desde = desde or hasta - timedelta(days=DIAS - 1)
    momento = cx.ahora()
    run_id = f"ga4-{momento:%Y%m%dT%H%M%S}"
    try:
        filas, brutos = descargar(token, propiedad, desde, hasta, secretos, dormir)
    except cx.ErrorDescarga as exc:
        raise cx.ErrorDescarga(cx.limpiar(str(exc), secretos)) from None
    ruta_bucket = cx.guardar_brutos(FUENTE, run_id, hoy, brutos, subir)
    with pg.transaction():
        nuevas, iguales = cx.insertar_versionado(pg, "ga4_trafico_dia", "ga4_trafico_dia_vigente", CLAVE, VALORES,
                                                 filas, {"descargado_at": momento, "run_id": run_id, "origen": ORIGEN})
    campanas = cmp.actualizar_sin_romper(pg)          # la descarga ya está confirmada
    return {"desde": str(desde), "hasta": str(hasta), "paginas": len(brutos), "filas": len(filas),
            "filas_nuevas": nuevas, "filas_iguales": iguales, "brutos": ruta_bucket, **campanas}


def ejecutar(entorno: Mapping[str, str] | None = None, desde: date | None = None, hasta: date | None = None,
             subir=None, dormir=time.sleep, firmar=jwt_firmado) -> int:
    entorno = os.environ if entorno is None else entorno
    hoy = hoy_madrid()
    try:
        with conectar(autocommit=True) as pg:
            try:
                resumen = ejecutar_con(pg, hoy, entorno, desde, hasta, subir, dormir, firmar)
            except cx.SinCredenciales as exc:
                cx.anotar(pg, FUENTE, hoy, "sin_credenciales", {"motivo": str(exc)})
                LOG.info("GA4 sin credenciales: no se descarga nada")
                return 0
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"[:500]
                cx.anotar(pg, FUENTE, hoy, "error", {"error": error})
                LOG.error("Descarga de GA4 fallida: %s", error)
                return 1
            cx.anotar(pg, FUENTE, hoy, "ok", resumen)
        LOG.info("GA4: %s", resumen)
        return 0
    except Exception as exc:
        LOG.error("GA4: %s", type(exc).__name__)
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--desde", type=date.fromisoformat)
    parser.add_argument("--hasta", type=date.fromisoformat)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return ejecutar(desde=args.desde, hasta=args.hasta)


if __name__ == "__main__":
    sys.exit(main())
