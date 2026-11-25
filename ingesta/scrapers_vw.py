"""Carga diaria a Postgres de las capturas de los scrapers de Volkswagen (VO, VN y VN otros).

Lee las bases SQLite de los scrapers SIEMPRE en solo lectura (mode=ro) y copia a Postgres:

- cada pasada success que aún no esté en scraper_runs, con todas sus observaciones
  (stock_externo_snapshot) y sus eventos (eventos_externos), en una sola transacción por pasada;
- después recalcula anuncios_externos para esa fuente a partir de snapshots y eventos.

Es idempotente: una pasada ya cargada no se vuelve a cargar, y las claves únicas impiden
duplicados aunque dos cargas coincidan. Desaparecer no es vender: los estados de un anuncio son
presente, ausente y retirado, tal como los deciden los eventos del scraper.

Órdenes (python -m ingesta.scrapers_vw ...):
  cargar            carga lo nuevo de las tres fuentes y, después, el color de los anuncios
                    (ingesta/colores.py; si falla, se anota y no cambia el resultado) (cron 08:30)
  comprobar         carga lo pendiente, anota en fuentes_estado si falta la captura del día y envía
                    el resumen del día por Telegram (cron 09:00)
  recuento          anuncios cargados por día y fuente, frente a lo que dijo el scraper
  concesionarios    dealer_id y nombres que coinciden con config/concesionario.yaml
  remarcar-propio   vuelve a calcular es_propio tras cambiar config/concesionario.yaml

Una fuente en fuentes_en_pausa (config/concesionario.yaml) no se carga ni se comprueba: se anota
«en_pausa» (con desde y motivo) y el resumen la cuenta como en pausa, no como problema. Al quitarla de
la lista, la carga siguiente trae todas las pasadas que el scraper tenga pendientes.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import psycopg
import yaml
from psycopg.types.json import Jsonb

from ingesta import telegram
from ingesta.concesionario import Pausa, fuentes_en_pausa
from ingesta.db import conectar
from ingesta.provincias import normalizar_cp, provincia

LOG = logging.getLogger("ingesta.scrapers_vw")
MADRID = ZoneInfo("Europe/Madrid")
RAIZ = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Fuente:
    nombre: str
    tipo_stock: str
    fichero: str
    scope: str


FUENTES = {
    "vo": Fuente("vo", "VO", "vo/vw_stock.sqlite3", "volkswagen-es-used-BQ-U-passenger-v1"),
    "vn": Fuente("vn", "VN", "vn/vw_vn_stock.sqlite3", "volkswagen-es-new-BQ-N-fuel-B-D-passenger-v1"),
    "vn_otros": Fuente("vn_otros", "VN", "vn_otros/vw_vn_stock.sqlite3",
                       "volkswagen-es-new-BQ-N-fuel-E-H-passenger-v1"),
}

# Campos de "business" que pasan a columnas; el resto se guarda en datos (jsonb).
CAMPOS_COLUMNA = {
    "vin", "dealer_id", "dealer_name", "dealer_city", "dealer_postcode", "model", "version", "fuel",
    "model_year", "first_registration_date", "mileage_km", "price_retail_cents", "price_basis_label",
    "delivery_status_code", "delivery_status_text",
}
# Campos del registro completo que no se copian: identificadores ya en columnas y las URL de las
# fotos, que solo ocupan sitio (siguen en la base del scraper y en los brutos del bucket).
CAMPOS_FUERA = {"vehicle_id", "business", "business_hash", "observed_at", "image_urls"}


class ErrorCarga(Exception):
    pass


# --------------------------------------------------------------------------- configuración

def dir_datos() -> Path:
    return Path(os.environ.get("CDF_SCRAPERS_DATA", "/opt/cdf/data"))


def ruta_concesionario() -> Path:
    return Path(os.environ.get("CDF_CONCESIONARIO", RAIZ / "config" / "concesionario.yaml"))


def dealers_propios(ruta: Path | None = None) -> set[str]:
    cfg = yaml.safe_load((ruta or ruta_concesionario()).read_text(encoding="utf-8")) or {}
    ids = ((cfg.get("propio") or {}).get("dealer_ids")) or []
    ids = {str(i).strip().upper() for i in ids if str(i).strip()}
    if not ids:
        raise ErrorCarga("config/concesionario.yaml no tiene propio.dealer_ids")
    return ids


def pausas() -> dict[str, Pausa]:
    return fuentes_en_pausa(ruta_concesionario())


def detalle_pausa(p: Pausa) -> dict:
    return {"desde": p.desde.isoformat(), "motivo": p.motivo}


def hoy_madrid() -> date:
    return datetime.now(MADRID).date()


# --------------------------------------------------------------------------- SQLite (solo lectura)

def abrir_sqlite(fuente: Fuente, base: Path | None = None) -> sqlite3.Connection:
    ruta = (base or dir_datos()) / fuente.fichero
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        # Como root, SQLite dejaría ficheros -shm/-wal de root que bloquearían al scraper (usuario cdf).
        raise ErrorCarga("No se abren las bases de los scrapers como root: ejecuta como el usuario cdf")
    if not ruta.is_file():
        raise ErrorCarga(f"No existe la base de {fuente.nombre}: {ruta}")
    # mode=ro: esta conexión no puede escribir en la base del scraper.
    db = sqlite3.connect(f"{ruta.resolve().as_uri()}?mode=ro", uri=True, timeout=30)
    db.row_factory = sqlite3.Row
    meta = dict(db.execute("SELECT key, value FROM metadata").fetchall())
    if meta.get("scope") != fuente.scope:
        db.close()
        raise ErrorCarga(f"{ruta} tiene el ámbito {meta.get('scope')!r}; se esperaba {fuente.scope!r}")
    if meta.get("schema_version") != "1":
        db.close()
        raise ErrorCarga(f"{ruta}: versión de esquema {meta.get('schema_version')!r} no prevista")
    return db


# --------------------------------------------------------------------------- transformación

def _entero(valor):
    if valor is None or isinstance(valor, bool):
        return None
    if isinstance(valor, (int, float)):
        return int(round(valor))
    texto = str(valor).strip()
    return int(texto) if texto.isdigit() else None


def _anio(valor):
    n = _entero(valor)
    return n if n is not None and 1900 <= n <= 2100 else None


def _fecha(valor):
    if not valor:
        return None
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        return None


def fila_snapshot(fuente: Fuente, run: sqlite3.Row, vehicle_id: str, observed_at: str,
                  data_json: str, propios: set[str]) -> dict:
    registro = json.loads(data_json)
    b = registro.get("business") or {}
    cp = normalizar_cp(b.get("dealer_postcode"))
    prov_codigo, prov_nombre = provincia(cp)
    dealer_id = (b.get("dealer_id") or None)
    datos = {k: v for k, v in registro.items() if k not in CAMPOS_FUERA}
    datos["business"] = {k: v for k, v in b.items() if k not in CAMPOS_COLUMNA}
    datos["n_imagenes"] = len(registro.get("image_urls") or [])
    return {
        "captured_at": observed_at,
        "snapshot_day": run["snapshot_day"],
        "run_id": run["run_id"],
        "fuente": fuente.nombre,
        "tipo_stock": fuente.tipo_stock,
        "id_anuncio": vehicle_id,
        "bastidor": b.get("vin"),
        "dealer_id": dealer_id,
        "concesionario": b.get("dealer_name"),
        "ciudad": b.get("dealer_city"),
        "codigo_postal": cp,
        "provincia_codigo": prov_codigo,
        "provincia": prov_nombre,
        "es_propio": bool(dealer_id) and dealer_id.strip().upper() in propios,
        "modelo": b.get("model"),
        "version": b.get("version"),
        "combustible": b.get("fuel"),
        "anio": _anio(b.get("model_year")),
        "fecha_matriculacion": _fecha(b.get("first_registration_date")),
        "km": _entero(b.get("mileage_km")),
        "precio_cents": _entero(b.get("price_retail_cents")),
        "etiqueta_precio": b.get("price_basis_label"),
        "estado_entrega_codigo": None if b.get("delivery_status_code") is None else str(b.get("delivery_status_code")),
        "estado_entrega": b.get("delivery_status_text"),
        "datos": Jsonb(datos),
    }


COLUMNAS_SNAPSHOT = [
    "captured_at", "snapshot_day", "run_id", "fuente", "tipo_stock", "id_anuncio", "bastidor",
    "dealer_id", "concesionario", "ciudad", "codigo_postal", "provincia_codigo", "provincia",
    "es_propio", "modelo", "version", "combustible", "anio", "fecha_matriculacion", "km",
    "precio_cents", "etiqueta_precio", "estado_entrega_codigo", "estado_entrega", "datos",
]
SQL_SNAPSHOT = (
    f"INSERT INTO stock_externo_snapshot ({', '.join(COLUMNAS_SNAPSHOT)}) "
    f"VALUES ({', '.join('%(' + c + ')s' for c in COLUMNAS_SNAPSHOT)}) "
    "ON CONFLICT (fuente, run_id, id_anuncio) DO NOTHING"
)
SQL_EVENTO = """
INSERT INTO eventos_externos (fuente, event_id, run_id, tipo_stock, id_anuncio, detected_at, tipo,
                              campo, valor_anterior, valor_nuevo)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::jsonb)
ON CONFLICT (fuente, event_id) DO NOTHING
"""

# Estado de cada anuncio a partir del último evento de presencia del scraper.
SQL_ANUNCIOS = """
INSERT INTO anuncios_externos AS a
       (fuente, id_anuncio, tipo_stock, first_seen, last_seen, ausente_desde, estado, reapariciones, actualizado_at)
SELECT s.fuente, s.id_anuncio, s.tipo_stock, s.first_seen, s.last_seen,
       CASE WHEN e.ultimo IN ('not_seen', 'removed') THEN coalesce(e.ultimo_not_seen, e.ultimo_at) END,
       CASE e.ultimo WHEN 'not_seen' THEN 'ausente' WHEN 'removed' THEN 'retirado' ELSE 'presente' END,
       coalesce(e.reapariciones, 0), now()
FROM (SELECT fuente, id_anuncio, min(tipo_stock) AS tipo_stock,
             min(captured_at) AS first_seen, max(captured_at) AS last_seen
      FROM stock_externo_snapshot WHERE fuente = %(fuente)s
      GROUP BY fuente, id_anuncio) s
LEFT JOIN (
      SELECT fuente, id_anuncio,
             (array_agg(tipo ORDER BY event_id DESC)
                 FILTER (WHERE tipo IN ('new', 'returned', 'not_seen', 'removed')))[1] AS ultimo,
             (array_agg(detected_at ORDER BY event_id DESC)
                 FILTER (WHERE tipo IN ('new', 'returned', 'not_seen', 'removed')))[1] AS ultimo_at,
             max(detected_at) FILTER (WHERE tipo = 'not_seen') AS ultimo_not_seen,
             count(*) FILTER (WHERE tipo = 'returned') AS reapariciones
      FROM eventos_externos WHERE fuente = %(fuente)s
      GROUP BY fuente, id_anuncio) e USING (fuente, id_anuncio)
ON CONFLICT (fuente, id_anuncio) DO UPDATE SET
       tipo_stock = EXCLUDED.tipo_stock, first_seen = EXCLUDED.first_seen, last_seen = EXCLUDED.last_seen,
       ausente_desde = EXCLUDED.ausente_desde, estado = EXCLUDED.estado,
       reapariciones = EXCLUDED.reapariciones, actualizado_at = EXCLUDED.actualizado_at
WHERE (a.tipo_stock, a.first_seen, a.last_seen, a.ausente_desde, a.estado, a.reapariciones)
      IS DISTINCT FROM
      (EXCLUDED.tipo_stock, EXCLUDED.first_seen, EXCLUDED.last_seen, EXCLUDED.ausente_desde,
       EXCLUDED.estado, EXCLUDED.reapariciones)
"""


# --------------------------------------------------------------------------- carga

def _cargar_run(pg: psycopg.Connection, sq: sqlite3.Connection, fuente: Fuente,
                run: sqlite3.Row, propios: set[str]) -> tuple[int, int]:
    """Copia una pasada success completa en una transacción. Devuelve (anuncios, eventos)."""
    with pg.transaction():
        cur = pg.execute(
            """INSERT INTO scraper_runs (fuente, run_id, tipo_stock, snapshot_day, started_at, finished_at,
                                         expected_count, observed_count, scope)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT DO NOTHING""",
            (fuente.nombre, run["run_id"], fuente.tipo_stock, run["snapshot_day"], run["started_at"],
             run["finished_at"], run["expected_count"], run["observed_count"], fuente.scope),
        )
        if cur.rowcount == 0:
            return 0, 0  # otra carga se adelantó

        filas = [fila_snapshot(fuente, run, r["vehicle_id"], r["observed_at"], r["data_json"], propios)
                 for r in sq.execute("SELECT vehicle_id, observed_at, data_json FROM observations "
                                     "WHERE run_id = ? ORDER BY vehicle_id", (run["run_id"],))]
        if len(filas) != run["observed_count"]:
            raise ErrorCarga(f"{fuente.nombre} {run['snapshot_day']}: el scraper dice {run['observed_count']} "
                             f"anuncios y hay {len(filas)} observaciones; no se carga")
        with pg.cursor() as c:
            c.executemany(SQL_SNAPSHOT, filas)

        eventos = [(fuente.nombre, e["event_id"], run["run_id"], fuente.tipo_stock, e["vehicle_id"],
                    e["detected_at"], e["kind"], e["field"], e["old_json"], e["new_json"])
                   for e in sq.execute("SELECT * FROM events WHERE run_id = ? ORDER BY event_id",
                                       (run["run_id"],))]
        with pg.cursor() as c:
            c.executemany(SQL_EVENTO, eventos)

        cargadas = pg.execute("SELECT count(*) FROM stock_externo_snapshot WHERE fuente = %s AND run_id = %s",
                              (fuente.nombre, run["run_id"])).fetchone()[0]
        if cargadas != run["observed_count"]:
            raise ErrorCarga(f"{fuente.nombre} {run['snapshot_day']}: {cargadas} filas en Postgres "
                             f"frente a {run['observed_count']} del scraper")
        return len(filas), len(eventos)


def cargar_fuente(pg: psycopg.Connection, fuente: Fuente, base: Path | None = None,
                  propios: set[str] | None = None) -> dict:
    propios = propios if propios is not None else dealers_propios()
    sq = abrir_sqlite(fuente, base)
    try:
        cargados = {r[0] for r in pg.execute("SELECT run_id FROM scraper_runs WHERE fuente = %s",
                                             (fuente.nombre,))}
        runs = sq.execute("SELECT * FROM runs WHERE status = 'success' ORDER BY snapshot_day").fetchall()
        nuevos = [r for r in runs if r["run_id"] not in cargados]
        resumen = {"runs_nuevos": 0, "anuncios": 0, "eventos": 0, "dias": []}
        for run in nuevos:
            anuncios, eventos = _cargar_run(pg, sq, fuente, run, propios)
            if anuncios:
                resumen["runs_nuevos"] += 1
                resumen["anuncios"] += anuncios
                resumen["eventos"] += eventos
                resumen["dias"].append(run["snapshot_day"])
                LOG.info("%s %s: %s anuncios, %s eventos", fuente.nombre, run["snapshot_day"], anuncios, eventos)
    finally:
        sq.close()
    if resumen["runs_nuevos"]:
        with pg.transaction():
            pg.execute(SQL_ANUNCIOS, {"fuente": fuente.nombre})
    return resumen


def _anotar(pg: psycopg.Connection, fuente: str, tipo: str, dia: date, estado: str, detalle: dict) -> None:
    with pg.transaction():
        pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) VALUES (%s, %s, %s, %s, %s)",
                   (fuente, tipo, dia, estado, Jsonb(detalle)))


def cargar(pg: psycopg.Connection, nombres=None, base: Path | None = None, dia: date | None = None) -> dict:
    """Carga todas las fuentes. Un fallo en una no impide cargar las otras."""
    dia = dia or hoy_madrid()
    propios = dealers_propios()
    en_pausa = pausas()
    resultado = {}
    for nombre in nombres or FUENTES:
        fuente = FUENTES[nombre]
        if nombre in en_pausa:
            resultado[nombre] = {"en_pausa": detalle_pausa(en_pausa[nombre])}
            _anotar(pg, nombre, "carga", dia, "en_pausa", detalle_pausa(en_pausa[nombre]))
            continue
        try:
            resumen = cargar_fuente(pg, fuente, base, propios)
            resultado[nombre] = resumen
            _anotar(pg, nombre, "carga", dia, "ok", resumen)
        except Exception as exc:  # se anota y se sigue con la siguiente fuente
            LOG.exception("Fallo al cargar %s", nombre)
            if pg.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
                pg.rollback()
            resultado[nombre] = {"error": str(exc)}
            _anotar(pg, nombre, "carga", dia, "error", {"error": str(exc)[:2000]})
    return resultado


def comprobar(pg: psycopg.Connection, base: Path | None = None, dia: date | None = None,
              cargar_antes: bool = True, avisar=None) -> dict:
    """Comprobación de las 09:00: ¿tiene cada scraper su captura success del día, y está cargada?

    Al terminar envía siempre el resumen del día por Telegram. `avisar` es la función de envío
    (por defecto telegram.enviar); False no envía nada.
    """
    dia = dia or hoy_madrid()
    if cargar_antes:
        cargar(pg, base=base, dia=dia)
    en_pausa = pausas()
    resultado = {}
    for nombre, fuente in FUENTES.items():
        detalle: dict = {}
        if nombre in en_pausa:
            detalle = detalle_pausa(en_pausa[nombre])
            _anotar(pg, nombre, "comprobacion", dia, "en_pausa", detalle)
            resultado[nombre] = {"estado": "en_pausa", **detalle}
            continue
        try:
            sq = abrir_sqlite(fuente, base)
            try:
                hoy = sq.execute("SELECT run_id, observed_count FROM runs WHERE status = 'success' "
                                 "AND snapshot_day = ?", (dia.isoformat(),)).fetchone()
                ultima = sq.execute("SELECT max(snapshot_day) FROM runs WHERE status = 'success'").fetchone()[0]
                intento = sq.execute("SELECT status, error, started_at FROM runs WHERE snapshot_day = ? "
                                     "ORDER BY started_at DESC LIMIT 1", (dia.isoformat(),)).fetchone()
            finally:
                sq.close()
            detalle["ultima_captura"] = ultima
            if intento is not None:
                detalle["ultimo_intento"] = {"estado": intento["status"], "inicio": intento["started_at"],
                                             "error": (intento["error"] or "")[:500]}
            if hoy is None:
                estado = "sin_captura"
            else:
                detalle["anuncios"] = hoy["observed_count"]
                en_pg = pg.execute("SELECT 1 FROM scraper_runs WHERE fuente = %s AND run_id = %s",
                                   (nombre, hoy["run_id"])).fetchone()
                estado = "ok" if en_pg else "sin_cargar"
        except Exception as exc:
            estado, detalle = "error", {"error": str(exc)[:2000]}
        _anotar(pg, nombre, "comprobacion", dia, estado, detalle)
        resultado[nombre] = {"estado": estado, **detalle}
    if avisar is not False:
        enviar_resumen(pg, dia, resultado, avisar or telegram.enviar)
    return resultado


# --------------------------------------------------------------------------- resumen por Telegram

ETIQUETAS = {"vo": "VO", "vn": "VN", "vn_otros": "VN eléctricos e híbridos"}


def _miles(n: int) -> str:
    return f"{n:,}".replace(",", ".")


def _dia_corto(valor) -> str:
    try:
        return date.fromisoformat(str(valor)[:10]).strftime("%d/%m")
    except ValueError:
        return str(valor)


def texto_resumen(dia: date, comprobacion: dict, filas: dict[str, int], errores_carga: dict[str, str],
                  cargadas: int | None = None) -> str:
    """Resumen del día en texto plano. Las cifras son las de Postgres y las del scraper.

    `filas`: anuncios de la captura del día por fuente. `cargadas`: filas cargadas ese día sumando
    todas las cargas anotadas en fuentes_estado (si no se da, la suma de `filas`).
    """
    lineas, problemas = [], 0
    for nombre, etiqueta in ETIQUETAS.items():
        r = comprobacion.get(nombre) or {"estado": "error", "error": "sin comprobar"}
        estado = r.get("estado")
        if estado == "ok":
            lineas.append(f"{etiqueta}: {_miles(filas.get(nombre, 0))} anuncios")
            continue
        if estado == "en_pausa":   # no es un problema: está en fuentes_en_pausa
            desde = f" desde el {_dia_corto(r['desde'])}" if r.get("desde") else ""
            motivo = f" ({r['motivo']})" if r.get("motivo") else ""
            lineas.append(f"{etiqueta}: en pausa{desde}{motivo}")
            continue
        problemas += 1
        if estado == "sin_captura":
            partes = ["sin captura de hoy"]
            if r.get("ultima_captura"):
                partes.append(f"última el {_dia_corto(r['ultima_captura'])}")
            intento = r.get("ultimo_intento") or {}
            if intento.get("estado"):
                motivo = f"último intento: {intento['estado']}"
                if intento.get("error"):
                    motivo += f" ({intento['error'][:120]})"
                partes.append(motivo)
            lineas.append(f"{etiqueta}: " + "; ".join(partes))
        elif estado == "sin_cargar":
            lineas.append(f"{etiqueta}: {_miles(r.get('anuncios') or 0)} anuncios capturados, sin cargar en Postgres")
        else:
            lineas.append(f"{etiqueta}: error al comprobar ({str(r.get('error', ''))[:150]})")
    total = _miles(sum(filas.values()) if cargadas is None else cargadas)
    if errores_carga:
        problemas += 1
        detalle = "; ".join(f"{ETIQUETAS.get(f, f)}: {e[:120]}" for f, e in sorted(errores_carga.items()))
        lineas.append(f"Carga a Postgres: con errores ({total} filas) · {detalle}")
    else:
        lineas.append(f"Carga a Postgres: correcta ({total} filas)")
    cabecera = "todo correcto" if problemas == 0 else f"{problemas} problema{'s' if problemas > 1 else ''}"
    return "\n".join([f"CDF {dia.strftime('%d/%m')} · {cabecera}", *lineas])


def datos_resumen(pg: psycopg.Connection, dia: date) -> tuple[dict[str, int], dict[str, str], int]:
    """Datos del resumen, sacados de Postgres.

    - anuncios de la captura del día por fuente (stock_externo_snapshot);
    - error de la última carga del día de cada fuente, si esa última carga falló;
    - filas cargadas ese día: la suma de los anuncios de TODAS las cargas anotadas con ese día (la de
      las 08:30, la que repite la comprobación de las 09:00 y cualquier «make cargar» a mano). Con
      solo la última, una recarga sin nada nuevo de las 09:00 dejaría la cifra en 0.
    """
    filas = dict(pg.execute("SELECT fuente, count(*) FROM stock_externo_snapshot WHERE snapshot_day = %s "
                            "GROUP BY fuente", (dia,)).fetchall())
    # Solo las cargas de los scrapers (la del color, fuente «colores», va aparte)
    cargadas = pg.execute(
        """SELECT coalesce(sum((detalle->>'anuncios')::bigint), 0) FROM fuentes_estado
           WHERE tipo = 'carga' AND dia = %s AND estado = 'ok' AND fuente = ANY(%s)""", (dia, list(FUENTES))).fetchone()[0]
    errores = {}
    for fuente, estado, detalle in pg.execute(
            """SELECT DISTINCT ON (fuente) fuente, estado, detalle FROM fuentes_estado
               WHERE tipo = 'carga' AND dia = %s AND fuente = ANY(%s) ORDER BY fuente, id DESC""", (dia, list(FUENTES))):
        if estado not in ("ok", "en_pausa"):
            errores[fuente] = str((detalle or {}).get("error", estado))
    return filas, errores, int(cargadas)


def enviar_resumen(pg: psycopg.Connection, dia: date, comprobacion: dict, avisar) -> bool:
    """Envía el resumen y lo anota en fuentes_estado. Nunca lanza: un fallo de Telegram no hace fallar el job."""
    texto = None
    try:
        texto = texto_resumen(dia, comprobacion, *datos_resumen(pg, dia))
        intentos = avisar(texto)
        _anotar(pg, "telegram", "aviso", dia, "ok", {"texto": texto, "intentos": intentos})
        LOG.info("Resumen enviado a Telegram")
        return True
    except Exception as exc:  # sin traza: podría llevar la URL con el token
        error = telegram.limpiar(f"{type(exc).__name__}: {exc}")[:500]
        LOG.warning("No se pudo enviar el resumen a Telegram: %s", error)
        try:
            _anotar(pg, "telegram", "aviso", dia, "error", {"error": error, "texto": texto})
        except Exception:
            LOG.warning("Tampoco se pudo anotar el fallo de Telegram en fuentes_estado")
        return False


def recuento(pg: psycopg.Connection, dias: int = 14) -> list[tuple]:
    return pg.execute(
        """SELECT r.snapshot_day, r.fuente, r.tipo_stock, r.observed_count,
                  count(s.id) AS cargados, count(s.id) FILTER (WHERE s.es_propio) AS propios
           FROM scraper_runs r
           LEFT JOIN stock_externo_snapshot s ON s.fuente = r.fuente AND s.run_id = r.run_id
           WHERE r.snapshot_day > (SELECT max(snapshot_day) FROM scraper_runs) - %s::int
           GROUP BY 1, 2, 3, 4 ORDER BY 1, 2""",
        (dias,),
    ).fetchall()


def concesionarios(pg: psycopg.Connection) -> list[tuple]:
    """dealer_id y nombres que casan con el concesionario propio de config/concesionario.yaml, para revisarlo."""
    cfg = yaml.safe_load(ruta_concesionario().read_text(encoding="utf-8")) or {}
    ids = sorted(dealers_propios())
    nombres = [n for n in (cfg.get("propio") or {}).get("nombres_en_scrapers") or []]
    patrones = ["%" + n + "%" for n in nombres] or ["%"]
    return pg.execute(
        """SELECT dealer_id, concesionario, ciudad, codigo_postal, tipo_stock, bool_or(es_propio),
                  count(*), max(snapshot_day)
           FROM stock_externo_snapshot
           WHERE dealer_id = ANY(%s) OR concesionario ILIKE ANY(%s)
           GROUP BY 1, 2, 3, 4, 5 ORDER BY 1, 5""",
        (ids, patrones),
    ).fetchall()


def remarcar_propio(pg: psycopg.Connection) -> int:
    ids = sorted(dealers_propios())
    with pg.transaction():
        cur = pg.execute(
            """UPDATE stock_externo_snapshot
               SET es_propio = coalesce(upper(trim(dealer_id)) = ANY(%s), false)
               WHERE es_propio IS DISTINCT FROM coalesce(upper(trim(dealer_id)) = ANY(%s), false)""",
            (ids, ids),
        )
        return cur.rowcount


# --------------------------------------------------------------------------- línea de órdenes

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="orden", required=True)
    p = sub.add_parser("cargar")
    p.add_argument("--fuente", choices=sorted(FUENTES), action="append")
    p = sub.add_parser("comprobar")
    p.add_argument("--dia", type=date.fromisoformat)
    p.add_argument("--sin-cargar", action="store_true", help="no intentar cargar antes de comprobar")
    p.add_argument("--sin-telegram", action="store_true", help="no enviar el resumen por Telegram")
    p = sub.add_parser("recuento")
    p.add_argument("--dias", type=int, default=14)
    sub.add_parser("concesionarios")
    sub.add_parser("remarcar-propio")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    with conectar(autocommit=True) as pg:
        if args.orden == "cargar":
            res = cargar(pg, args.fuente)
            if not args.fuente:   # el color, después del stock y sin cambiar su resultado
                from ingesta import colores
                res["colores"] = colores.ejecutar(pg, hoy_madrid())
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
            return 1 if any("error" in r for f, r in res.items() if f in FUENTES) else 0
        if args.orden == "comprobar":
            res = comprobar(pg, dia=args.dia, cargar_antes=not args.sin_cargar,
                            avisar=False if args.sin_telegram else None)
            print(json.dumps(res, ensure_ascii=False, indent=2, default=str))
            return 0 if all(r["estado"] in ("ok", "en_pausa") for r in res.values()) else 1
        if args.orden == "recuento":
            print(f"{'día':<12}{'fuente':<10}{'tipo':<6}{'scraper':>9}{'cargados':>10}{'propios':>9}")
            for dia, fuente, tipo, obs, carg, prop in recuento(pg, args.dias):
                aviso = "" if obs == carg else "  <- no cuadra"
                print(f"{dia!s:<12}{fuente:<10}{tipo:<6}{obs:>9}{carg:>10}{prop:>9}{aviso}")
            return 0
        if args.orden == "concesionarios":
            for fila in concesionarios(pg):
                print(" | ".join("" if v is None else str(v) for v in fila))
            return 0
        if args.orden == "remarcar-propio":
            print(f"Filas actualizadas: {remarcar_propio(pg)}")
            return 0
    return 2


if __name__ == "__main__":
    sys.exit(main())
