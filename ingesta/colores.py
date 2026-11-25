"""Carga del color de los anuncios desde la exportación del componente de fichas (cron 08:30, tras
la carga del stock: python -m ingesta.scrapers_vw cargar).

El componente de fichas es independiente de los scrapers: lee la ficha de cada anuncio y deja en
/opt/cdf/data/vw_details/exports/colors_latest.jsonl.gz una línea JSON por anuncio (source_name,
vehicle_id, vin, status, exterior_color, interior_color, fetched_at…). Aquí:

- se lee esa exportación en solo lectura (nunca la caché colors.sqlite3 ni nada más de su carpeta);
- cada línea se guarda en color_anuncio (solo añadir) si su estado, VIN, colores o fetched_at no
  estaban ya guardados para ese anuncio; los colores, tal cual;
- se anota en fuentes_estado (fuente «colores», tipo «carga»): «ok», «sin_captura» si el fichero no
  existe o «error» si no se puede leer. Nunca lanza: la carga del stock sigue aunque esto falle.
- con «colores» en fuentes_en_pausa (config/concesionario.yaml) no se lee nada: se anota «en_pausa».

No toca stock_externo_snapshot, eventos_externos ni anuncios_externos: el color no es un «cambio»
del anuncio. La unión con el stock (fuente + vehicle_id, con el VIN como contraste) se hace en el
precálculo (analitica/mercado.py).
"""

from __future__ import annotations

import gzip
import hashlib
import json
import logging
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

LOG = logging.getLogger("ingesta.colores")
FUENTES = ("vo", "vn", "vn_otros")
FICHERO = Path("vw_details") / "exports" / "colors_latest.jsonl.gz"

SQL = """
INSERT INTO color_anuncio (run_id, fuente, id_anuncio, bastidor, color_exterior, color_interior, estado, fetched_at)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT DO NOTHING
"""


def ruta(base: Path | None = None) -> Path:
    from ingesta.scrapers_vw import dir_datos
    return (base or dir_datos()) / FICHERO


def _texto(valor) -> str | None:
    if valor is None:
        return None
    texto = str(valor)
    return texto if texto.strip() else None


def _instante(valor) -> datetime | None:
    if not valor:
        return None
    try:
        t = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else None   # sin zona no se sabe qué día es: como si no viniera


def leer(fichero: Path) -> tuple[str, list[tuple], Counter]:
    """(huella del fichero, filas para color_anuncio sin run_id, recuento de líneas descartadas por motivo)."""
    crudo = fichero.read_bytes()
    huella = hashlib.sha256(crudo).hexdigest()
    filas, descartes = [], Counter()
    for linea in gzip.decompress(crudo).decode("utf-8").splitlines():
        if not linea.strip():
            continue
        try:
            r = json.loads(linea)
        except json.JSONDecodeError:
            descartes["json"] += 1
            continue
        fuente, vid, estado = r.get("source_name"), _texto(r.get("vehicle_id")), _texto(r.get("status"))
        if fuente not in FUENTES:
            descartes["fuente"] += 1
            continue
        if vid is None or estado is None:
            descartes["sin_id_o_estado"] += 1
            continue
        filas.append((fuente, vid, _texto(r.get("vin")), _texto(r.get("exterior_color")),
                      _texto(r.get("interior_color")), estado, _instante(r.get("fetched_at"))))
    return huella, filas, descartes


def cargar(pg: psycopg.Connection, base: Path | None = None) -> dict:
    """Lee la exportación y añade lo nuevo a color_anuncio en una transacción. Lanza si no puede."""
    fichero = ruta(base)
    huella, filas, descartes = leer(fichero)
    run_id = f"colores-{huella[:16]}"
    with pg.transaction():
        antes = pg.execute("SELECT count(*) FROM color_anuncio").fetchone()[0]
        with pg.cursor() as cur:
            cur.executemany(SQL, [(run_id, *f) for f in filas])
        nuevas = pg.execute("SELECT count(*) FROM color_anuncio").fetchone()[0] - antes
    return {"fichero": str(fichero), "run_id": run_id, "lineas": len(filas), "filas_nuevas": nuevas,
            "por_estado": dict(Counter(f[5] for f in filas)),
            "con_color": sum(1 for f in filas if f[3] is not None), "descartadas": dict(descartes)}


def ejecutar(pg: psycopg.Connection, dia: date, base: Path | None = None) -> dict:
    """Carga y anota en fuentes_estado. Nunca lanza: un fallo aquí no para la carga del stock."""
    fichero = ruta(base)
    try:
        from ingesta.scrapers_vw import detalle_pausa, pausas
        pausa = pausas().get("colores")
        if pausa is not None:
            estado, detalle = "en_pausa", detalle_pausa(pausa)
        elif not fichero.is_file():
            estado, detalle = "sin_captura", {"error": f"No existe la exportación de colores: {fichero}"}
        else:
            estado, detalle = "ok", cargar(pg, base)
    except Exception as exc:
        LOG.exception("Fallo al cargar los colores")
        if pg.info.transaction_status != psycopg.pq.TransactionStatus.IDLE:
            pg.rollback()
        estado, detalle = "error", {"error": f"{type(exc).__name__}: {exc}"[:2000], "fichero": str(fichero)}
    try:
        with pg.transaction():
            pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) VALUES ('colores', 'carga', "
                       "%s, %s, %s)", (dia, estado, Jsonb(detalle)))
    except Exception:
        LOG.warning("No se pudo anotar la carga de colores en fuentes_estado")
    LOG.info("Colores: %s %s", estado, detalle)
    return {"estado": estado, **detalle}
