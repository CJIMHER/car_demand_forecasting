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

Histórico (make meta-historico DESDE=2025-01-01 [HASTA=…]; por defecto hasta ayer): solo el nivel de
campaña, por tramos de un mes, con las campañas en cualquier estado (también archivadas y borradas:
filtro por effective_status con todos los estados) y el nombre y el estado de cada campaña
(campanas_meta_estado). Espera cuando la cabecera x-business-use-case-usage dice que el uso se acerca al
límite. Cada tramo sube sus brutos y se guarda antes de pedir el siguiente, en la misma tabla que la carga
diaria y con la misma huella: relanzarlo no duplica filas (la clave es campaña y día).

Uso:
  python -m ingesta.meta                          últimos 7 días (cron; make meta)
  python -m ingesta.meta --desde 2026-11-01 --hasta 2026-11-20
  python -m ingesta.meta --historico --desde 2025-01-01 [--hasta 2026-11-23]
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


# --------------------------------------------------------------------------- histórico (make meta-historico)

# Todos los estados de una campaña: así salen también las archivadas y las borradas
ESTADOS = ["ACTIVE", "PAUSED", "DELETED", "ARCHIVED", "IN_PROCESS", "WITH_ISSUES", "CAMPAIGN_PAUSED", "ADSET_PAUSED",
           "PENDING_REVIEW", "DISAPPROVED", "PREAPPROVED", "PENDING_BILLING_INFO"]
# Uso del límite de la API (cabecera x-business-use-case-usage, en %): desde USO_FRENAR se espera
# ESPERA_FRENAR segundos antes de la petición siguiente; desde USO_PARAR, ESPERA_PARAR. Si Meta dice
# cuántos minutos faltan para recuperar el acceso (estimated_time_to_regain_access), se espera eso.
USO_FRENAR, ESPERA_FRENAR = 75, 60
USO_PARAR, ESPERA_PARAR = 90, 300
CLAVE_ESTADO = ("cuenta_id", "campana_id")
VALORES_ESTADO = ("campana_nombre", "estado", "estado_efectivo")


def pausa_por_uso(cabeceras: Mapping[str, str]) -> float:
    """Segundos que esperar antes de la petición siguiente según x-business-use-case-usage."""
    try:
        uso = json.loads(cabeceras.get("x-business-use-case-usage") or "{}")
    except ValueError:
        return 0
    pct, eta = 0.0, 0.0
    for lista in (uso.values() if isinstance(uso, dict) else []):
        for u in lista if isinstance(lista, list) else [lista]:
            if not isinstance(u, dict):
                continue
            for k in ("call_count", "total_cputime", "total_time"):
                try:
                    pct = max(pct, float(u.get(k) or 0))
                except (TypeError, ValueError):
                    pass
            try:
                eta = max(eta, float(u.get("estimated_time_to_regain_access") or 0))
            except (TypeError, ValueError):
                pass
    if eta > 0:
        return min(eta * 60, 3600)
    if pct >= USO_PARAR:
        return ESPERA_PARAR
    if pct >= USO_FRENAR:
        return ESPERA_FRENAR
    return 0


def tramos(desde: date, hasta: date) -> list[tuple[date, date]]:
    """Meses naturales entre desde y hasta (el primero y el último, recortados)."""
    salida, ini = [], desde
    while ini <= hasta:
        sig = (ini.replace(day=1) + timedelta(days=32)).replace(day=1)
        salida.append((ini, min(hasta, sig - timedelta(days=1))))
        ini = sig
    return salida


def _paginar(url: str, token: str, dormir, leer, espera: list[float] | None = None) -> tuple[list[dict], list[bytes]]:
    """Sigue la paginación respetando el límite de uso; `leer` convierte cada elemento de «data». `espera`
    (una lista con un número) se comparte entre llamadas: lo que dijo la última respuesta vale para la
    petición siguiente, sea del mismo tramo o del siguiente."""
    filas, brutos, paginas = [], [], 0
    cabeceras = {"Authorization": f"Bearer {token}"}
    espera = [0.0] if espera is None else espera

    def uso(c):
        espera[0] = pausa_por_uso(c)
    while url:
        if espera[0]:
            LOG.info("Límite de uso de Meta alto: espero %s s", int(espera[0]))
            dormir(espera[0])
        datos, bruto = cx.peticion_json(url, cabeceras=cabeceras, es_limite=es_limite, secretos=[token],
                                        dormir=dormir, al_responder=uso)
        brutos.append(bruto)
        paginas += 1
        filas += [leer(r) for r in datos.get("data") or []]
        url = (datos.get("paging") or {}).get("next")
        if url and not url.startswith(f"{BASE}/"):     # el token nunca sale hacia otro sitio
            raise cx.ErrorDescarga("paginación hacia un dominio que no es el de Meta")
        if paginas > 2000:
            raise cx.ErrorDescarga("demasiadas páginas")
    return filas, brutos


def descargar_tramo(token: str, cuenta: str, version: str, desde: date, hasta: date, dormir=time.sleep,
                    espera: list[float] | None = None) -> tuple[list[dict], list[bytes]]:
    """Insights por campaña y día de un tramo, de campañas en cualquier estado (también archivadas y borradas)."""
    params = {"level": "campaign", "time_increment": "1", "limit": "500",
              "time_range": json.dumps({"since": desde.isoformat(), "until": hasta.isoformat()}),
              "filtering": json.dumps([{"field": "campaign.effective_status", "operator": "IN", "value": ESTADOS}]),
              "fields": ",".join(CAMPOS)}
    url = f"{BASE}/{version}/act_{cuenta}/insights?{urllib.parse.urlencode(params)}"
    return _paginar(url, token, dormir, lambda r: leer_fila(r, "campana", cuenta), espera)


def descargar_estados(token: str, cuenta: str, version: str, dormir=time.sleep,
                      espera: list[float] | None = None) -> tuple[list[dict], list[bytes]]:
    """Nombre y estado de todas las campañas de la cuenta."""
    params = {"fields": "id,name,status,effective_status", "limit": "500",
              "filtering": json.dumps([{"field": "effective_status", "operator": "IN", "value": ESTADOS}])}
    url = f"{BASE}/{version}/act_{cuenta}/campaigns?{urllib.parse.urlencode(params)}"
    return _paginar(url, token, dormir, lambda r: {
        "cuenta_id": cuenta, "campana_id": str(r["id"]), "campana_nombre": r.get("name"), "estado": r.get("status"),
        "estado_efectivo": r.get("effective_status")}, espera)


def insertar_estados(pg: psycopg.Connection, filas: list[dict], comunes: dict) -> int:
    """Añade el estado de las campañas nuevas o que han cambiado. Devuelve cuántas filas."""
    vigentes = {(c, i): h for c, i, h in pg.execute(
        "SELECT cuenta_id, campana_id, huella FROM campanas_meta_estado_vigente")}
    nuevas = []
    for f in filas:
        f["huella"] = cx.huella(f, VALORES_ESTADO)
        if vigentes.get((f["cuenta_id"], f["campana_id"])) != f["huella"]:
            nuevas.append(f)
    cols = list(comunes) + list(CLAVE_ESTADO + VALORES_ESTADO) + ["huella"]
    with pg.cursor() as cur:
        cur.executemany(f"INSERT INTO campanas_meta_estado ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                        [tuple(comunes[c] for c in comunes) + tuple(f[c] for c in cols[len(comunes):]) for f in nuevas])
    return len(nuevas)


def ejecutar_historico_con(pg: psycopg.Connection, hoy: date, entorno: Mapping[str, str], desde: date,
                           hasta: date | None = None, subir=None, dormir=time.sleep) -> dict:
    """Histórico por meses: cada tramo se descarga, sube sus brutos y se guarda antes de pedir el siguiente,
    así que si se corta, relanzarlo no duplica nada (una fila solo se añade si es nueva o ha cambiado)."""
    token, cuenta = credenciales(entorno)
    config = cmp.cargar()
    hasta = hasta or hoy - timedelta(days=1)
    if desde > hasta:
        raise cx.ErrorDescarga(f"DESDE ({desde}) es posterior a HASTA ({hasta})")
    momento = cx.ahora()
    run_id = f"meta-hist-{momento:%Y%m%dT%H%M%S}"
    origen = f"meta_marketing_api {config.meta_version}"
    espera = [0.0]                                     # límite de uso, compartido por todas las peticiones
    estados, brutos = descargar_estados(token, cuenta, config.meta_version, dormir=dormir, espera=espera)
    cx.guardar_brutos(FUENTE, f"{run_id}_estados", hoy, brutos, subir)
    with pg.transaction():
        n_estados = insertar_estados(pg, estados, {"descargado_at": momento, "run_id": run_id})
    resumen = {"desde": str(desde), "hasta": str(hasta), "historico": True, "campanas": len(estados),
               "estados_nuevos": n_estados, "tramos": []}
    for ini, fin in tramos(desde, hasta):
        filas, brutos = descargar_tramo(token, cuenta, config.meta_version, ini, fin, dormir=dormir, espera=espera)
        cx.guardar_brutos(FUENTE, f"{run_id}_{ini:%Y%m}", hoy, brutos, subir)      # antes de escribir nada
        with pg.transaction():
            nuevas, iguales = cx.insertar_versionado(
                pg, "campanas_meta_dia", "campanas_meta_dia_vigente", CLAVE, VALORES, filas,
                {"descargado_at": momento, "run_id": run_id, "origen": origen})
        resumen["tramos"].append({"mes": f"{ini:%Y-%m}", "paginas": len(brutos), "filas": len(filas),
                                  "filas_nuevas": nuevas, "filas_iguales": iguales})
        LOG.info("Meta %s: %s filas (%s nuevas)", f"{ini:%Y-%m}", len(filas), nuevas)
    resumen.update(cmp.actualizar_sin_romper(pg, config))      # los tramos ya están confirmados
    return resumen


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
    campanas = cmp.actualizar_sin_romper(pg, config)  # la descarga ya está confirmada
    return {"desde": str(desde), "hasta": str(hasta), "paginas": len(brutos), "filas": len(filas),
            "filas_nuevas": nuevas, "filas_iguales": iguales, "brutos": ruta, **campanas}


def ejecutar(entorno: Mapping[str, str] | None = None, desde: date | None = None, hasta: date | None = None,
             subir=None, dormir=time.sleep, historico: bool = False) -> int:
    """Descarga diaria o, con historico=True, el histórico por meses desde `desde` (obligatorio)."""
    entorno = os.environ if entorno is None else entorno
    hoy = hoy_madrid()
    secretos = [entorno.get("META_ACCESS_TOKEN")]
    if historico and desde is None:
        LOG.error("El histórico necesita DESDE (make meta-historico DESDE=2025-01-01)")
        return 1
    try:
        with conectar(autocommit=True) as pg:
            try:
                if historico:
                    resumen = ejecutar_historico_con(pg, hoy, entorno, desde, hasta, subir, dormir)
                else:
                    resumen = ejecutar_con(pg, hoy, entorno, desde, hasta, subir, dormir)
            except cx.SinCredenciales as exc:
                if historico:    # lo pide alguien a mano: decirlo claro
                    LOG.error("Meta sin credenciales: %s", exc)
                    return 1
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
    parser.add_argument("--historico", action="store_true",
                        help="por meses, solo el nivel de campaña y con las campañas archivadas y borradas")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return ejecutar(desde=args.desde, hasta=args.hasta, historico=args.historico)


if __name__ == "__main__":
    sys.exit(main())
