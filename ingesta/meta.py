"""Conector de Meta Marketing API (solo lectura, permiso ads_read): insights diarios por campaña,
conjunto de anuncios y anuncio. Inactivo hasta tener credenciales.

Variables del .env:
  META_ACCESS_TOKEN    token de acceso con ads_read (de un usuario del sistema del Business Manager)
  META_AD_ACCOUNT_ID   cuenta publicitaria (con o sin «act_» delante)
Sin cualquiera de las dos, la tarea no hace ninguna petición y anota «sin_credenciales» en
fuentes_estado (no es un error: no salta ninguna alerta).

Cada noche (06:00) recarga los últimos 7 días, porque Meta corrige sus cifras: una fila solo se
añade a campanas_meta_dia si su contenido ha cambiado (ingesta/conectores.py), así que una
corrección queda como versión nueva sin duplicar las demás. Las respuestas brutas van comprimidas al
bucket (meta/raw/<día>/) antes de leerlas.

Qué se guarda (lista blanca; nada más llega a la tabla): día, campaña, conjunto y anuncio (id y
nombre), moneda, gasto, impresiones, alcance, frecuencia, clics, CPM y las acciones de lead si las
hay. Los insights de Meta son agregados: no traen datos de personas, y no se piden.

El token va en la cabecera Authorization, nunca en la URL; ningún error ni registro lo lleva.

Uso:
  python -m ingesta.meta                          últimos 7 días (cron; make meta)
  python -m ingesta.meta --desde 2026-11-01 --hasta 2026-11-20
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.parse
from collections.abc import Mapping
from datetime import date, timedelta

import psycopg

from ingesta import campanas as cmp
from ingesta import conectores as cx
from ingesta.db import conectar
from ingesta.scrapers_vw import hoy_madrid

LOG = logging.getLogger("ingesta.meta")
FUENTE = "meta"
BASE = "https://graph.facebook.com"
NIVELES = {"campana": "campaign", "conjunto": "adset", "anuncio": "ad"}
CAMPOS = ["date_start", "account_currency", "campaign_id", "campaign_name", "spend", "impressions", "reach",
          "frequency", "clicks", "cpm", "actions"]
CAMPOS_NIVEL = {"campana": [], "conjunto": ["adset_id", "adset_name"],
                "anuncio": ["adset_id", "adset_name", "ad_id", "ad_name"]}
# Códigos de error de Meta que son límites de uso (además del HTTP 429)
LIMITES = {4, 17, 32, 613} | set(range(80000, 80015))
DIAS = 7
CLAVE = ("cuenta_id", "nivel", "dia", "campana_id", "conjunto_id", "anuncio_id")
VALORES = ("campana_nombre", "conjunto_nombre", "anuncio_nombre", "moneda", "gasto_cents", "impresiones", "alcance",
           "frecuencia", "clics", "cpm_cents", "leads", "acciones_lead")


def credenciales(entorno: Mapping[str, str]) -> tuple[str, str]:
    token = (entorno.get("META_ACCESS_TOKEN") or "").strip()
    cuenta = (entorno.get("META_AD_ACCOUNT_ID") or "").strip().removeprefix("act_")
    if not token or not cuenta:
        raise cx.SinCredenciales("faltan META_ACCESS_TOKEN o META_AD_ACCOUNT_ID en el .env")
    return token, cuenta


def es_limite(codigo: int, datos: dict) -> bool:
    error = datos.get("error") if isinstance(datos.get("error"), dict) else {}
    return error.get("code") in LIMITES


def _entero(v) -> int | None:
    return None if v in (None, "") else int(float(v))


def _decimal(v) -> float | None:
    return None if v in (None, "") else float(v)


def leer_fila(r: dict, nivel: str, cuenta: str) -> dict:
    """Una fila de insights → fila de campanas_meta_dia. Solo los campos de la lista blanca."""
    acciones = {a["action_type"]: _decimal(a.get("value")) for a in (r.get("actions") or [])
                if isinstance(a, dict) and "lead" in str(a.get("action_type", ""))}
    leads = acciones.get("lead", max(acciones.values()) if acciones else None)
    gasto = _decimal(r.get("spend")) or 0.0
    cpm = _decimal(r.get("cpm"))
    return {
        "cuenta_id": cuenta, "nivel": nivel, "dia": date.fromisoformat(r["date_start"]),
        "campana_id": str(r["campaign_id"]), "campana_nombre": r.get("campaign_name"),
        "conjunto_id": str(r["adset_id"]) if nivel != "campana" else None,
        "conjunto_nombre": r.get("adset_name") if nivel != "campana" else None,
        "anuncio_id": str(r["ad_id"]) if nivel == "anuncio" else None,
        "anuncio_nombre": r.get("ad_name") if nivel == "anuncio" else None,
        "moneda": r.get("account_currency"), "gasto_cents": round(gasto * 100),
        "impresiones": _entero(r.get("impressions")) or 0, "alcance": _entero(r.get("reach")),
        "frecuencia": _decimal(r.get("frequency")), "clics": _entero(r.get("clicks")) or 0,
        "cpm_cents": None if cpm is None else round(cpm * 100, 4),
        "leads": None if leads is None else int(leads), "acciones_lead": acciones,
    }


def descargar(token: str, cuenta: str, version: str, desde: date, hasta: date, dormir=time.sleep
              ) -> tuple[list[dict], list[bytes]]:
    """(filas leídas de los tres niveles, respuestas en bruto). Sigue la paginación de Meta."""
    filas, brutos = [], []
    cabeceras = {"Authorization": f"Bearer {token}"}
    for nivel, nivel_api in NIVELES.items():
        params = {"level": nivel_api, "time_increment": "1", "limit": "500",
                  "time_range": json.dumps({"since": desde.isoformat(), "until": hasta.isoformat()}),
                  "fields": ",".join(CAMPOS + CAMPOS_NIVEL[nivel])}
        url = f"{BASE}/{version}/act_{cuenta}/insights?{urllib.parse.urlencode(params)}"
        paginas = 0
        while url:
            datos, bruto = cx.peticion_json(url, cabeceras=cabeceras, es_limite=es_limite, secretos=[token],
                                            dormir=dormir)
            brutos.append(bruto)
            paginas += 1
            pendientes = [leer_fila(r, nivel, cuenta) for r in datos.get("data") or []]
            filas += pendientes
            url = (datos.get("paging") or {}).get("next")
            if url and not url.startswith(f"{BASE}/"):     # el token nunca sale hacia otro sitio
                raise cx.ErrorDescarga("paginación hacia un dominio que no es el de Meta")
            if paginas > 2000:
                raise cx.ErrorDescarga("demasiadas páginas")
    return filas, brutos


def ejecutar_con(pg: psycopg.Connection, hoy: date, entorno: Mapping[str, str], desde: date | None = None,
                 hasta: date | None = None, subir=None, dormir=time.sleep) -> dict:
    """Descarga, guarda los brutos, añade lo nuevo y anota. Lanza SinCredenciales o ErrorDescarga."""
    token, cuenta = credenciales(entorno)
    config = cmp.cargar()
    hasta = hasta or hoy - timedelta(days=1)
    desde = desde or hasta - timedelta(days=DIAS - 1)
    momento = cx.ahora()
    run_id = f"meta-{momento:%Y%m%dT%H%M%S}"
    filas, brutos = descargar(token, cuenta, config.meta_version, desde, hasta, dormir=dormir)
    ruta = cx.guardar_brutos(FUENTE, run_id, hoy, brutos, subir)        # antes de escribir nada
    with pg.transaction():
        nuevas, iguales = cx.insertar_versionado(
            pg, "campanas_meta_dia", "campanas_meta_dia_vigente", CLAVE, VALORES, filas,
            {"descargado_at": momento, "run_id": run_id, "origen": f"meta_marketing_api {config.meta_version}"})
    campanas = cmp.actualizar(pg, config)
    return {"desde": str(desde), "hasta": str(hasta), "paginas": len(brutos), "filas": len(filas),
            "filas_nuevas": nuevas, "filas_iguales": iguales, "brutos": ruta, **campanas}


def ejecutar(entorno: Mapping[str, str] | None = None, desde: date | None = None, hasta: date | None = None,
             subir=None, dormir=time.sleep) -> int:
    entorno = os.environ if entorno is None else entorno
    hoy = hoy_madrid()
    secretos = [entorno.get("META_ACCESS_TOKEN")]
    try:
        with conectar(autocommit=True) as pg:
            try:
                resumen = ejecutar_con(pg, hoy, entorno, desde, hasta, subir, dormir)
            except cx.SinCredenciales as exc:
                cx.anotar(pg, FUENTE, hoy, "sin_credenciales", {"motivo": str(exc)})
                LOG.info("Meta sin credenciales: no se descarga nada")
                return 0
            except Exception as exc:
                error = cx.limpiar(f"{type(exc).__name__}: {exc}", secretos)[:500]
                cx.anotar(pg, FUENTE, hoy, "error", {"error": error})
                LOG.error("Descarga de Meta fallida: %s", error)
                return 1
            cx.anotar(pg, FUENTE, hoy, "ok", resumen)
        LOG.info("Meta: %s", resumen)
        return 0
    except Exception as exc:   # Postgres caído
        LOG.error("Meta: %s", cx.limpiar(type(exc).__name__, secretos))
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
