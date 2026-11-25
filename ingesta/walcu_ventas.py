"""Ventas de Walcu por semana de la VENTA, tipo y modelo (make walcu-ventas-cargar FICHERO=/ruta.csv).

Para la regla «caída de ventas» de la campaña general (analitica/campana_general.py): las ventas por su fecha,
no por la semana del lead (con cohortes, las semanas recientes siempre parecerían peores).

Fuentes:
- CSV con exactamente las columnas  semana, tipo, modelo, ventas  (semana: el lunes de la semana de la venta;
  tipo VN o VO; ventas, entero no negativo). Lo genera en el Mac  scripts/walcu_agregar.py --negociaciones  a
  partir de la exportación de negociaciones de Walcu. Se rechaza ENTERO si no vale, con las mismas comprobaciones
  de datos personales que el CSV de leads (ingesta/walcu.py); los mensajes dicen fila y columna, nunca el valor.
- La API de Walcu (ingesta/walcu_crm.py), cuando hay mapeo: las ventas del CRM por su fecha. Manda sobre el CSV.

Cada carga dice qué semanas cubre (desde, hasta; por defecto, de la primera a la última semana del fichero): una
semana cubierta sin filas es una semana sin ventas. Para cada semana vale la carga de la API más reciente que la
cubre o, si no hay, la del CSV más reciente. Solo añadir (walcu_ventas_carga, walcu_ventas_semana); el mismo
contenido dos veces no se vuelve a cargar. El CSV va comprimido al bucket antes de escribir en Postgres.

Uso (en el servidor, el fichero llega por la entrada estándar):
  python -m ingesta.walcu_ventas cargar --nombre ventas.csv [--hasta AAAA-MM-DD] < ventas.csv
  python -m ingesta.walcu_ventas comprobar --nombre … < …        solo valida, no carga
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import logging
import sys
import tempfile
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg

from ingesta import conectores as cx
from ingesta.walcu import ErrorWalcu, lunes, personal

LOG = logging.getLogger("ingesta.walcu_ventas")
COLUMNAS = ("semana", "tipo", "modelo", "ventas")
TIPOS = ("VN", "VO")
UNA_SEMANA = timedelta(days=7)


@dataclass
class Validado:
    filas: list[dict]
    avisos: list[str]

    @property
    def desde(self) -> date:
        return min(f["semana"] for f in self.filas)

    @property
    def hasta(self) -> date:
        return max(f["semana"] for f in self.filas)


def validar(contenido: bytes) -> Validado:
    """Comprueba el CSV entero; ErrorWalcu con todos los problemas (fila y columna, sin valores)."""
    try:
        texto = contenido.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ErrorWalcu(["el fichero no está en UTF-8"]) from None
    lector = csv.reader(io.StringIO(texto))
    cabecera = [c.strip() for c in next(lector, [])]
    problemas: list[str] = []
    if tuple(cabecera) != COLUMNAS:
        raise ErrorWalcu(["columnas admitidas, exactamente y en este orden: " + ", ".join(COLUMNAS)])
    filas, claves = [], {}
    for n, valores in enumerate(lector, start=2):
        if not any(v.strip() for v in valores):
            continue
        if len(valores) != len(COLUMNAS):
            problemas.append(f"fila {n}: tiene {len(valores)} valores y la cabecera {len(COLUMNAS)}")
            continue
        crudo = dict(zip(COLUMNAS, (v.strip() for v in valores)))
        for col, v in crudo.items():
            tipo_dato = personal(v)
            if tipo_dato:
                problemas.append(f"fila {n}, columna {col}: parece {tipo_dato}")
        f: dict = {}
        semana = lunes(crudo["semana"])
        if isinstance(semana, str):
            problemas.append(f"fila {n}, columna semana: {semana.replace('del lead', 'de la venta')}")
        else:
            f["semana"] = semana
        if crudo["tipo"] not in TIPOS:
            problemas.append(f"fila {n}, columna tipo: debe ser VN o VO")
        f["tipo"] = crudo["tipo"]
        if not crudo["modelo"]:
            problemas.append(f"fila {n}, columna modelo: vacía")
        f["modelo"] = crudo["modelo"]
        if not crudo["ventas"].isdigit():
            problemas.append(f"fila {n}, columna ventas: no es un entero no negativo")
        else:
            f["ventas"] = int(crudo["ventas"])
        if len(f) != len(COLUMNAS):
            continue
        clave = (f["semana"], f["tipo"], f["modelo"])
        if clave in claves:
            problemas.append(f"fila {n}: repite semana, tipo y modelo de la fila {claves[clave]}")
            continue
        claves[clave] = n
        filas.append(f)
    if problemas:
        raise ErrorWalcu(problemas)
    if not filas:
        raise ErrorWalcu(["el fichero no tiene filas"])
    avisos = []
    ceros = sum(1 for f in filas if f["ventas"] == 0)
    if ceros:
        avisos.append(f"{ceros} filas con 0 ventas (no hacen falta: una semana cubierta sin filas ya es 0)")
    return Validado(filas, avisos)


def huella(contenido: bytes) -> str:
    return hashlib.sha256(contenido).hexdigest()


def _subir(contenido: bytes, run_id: str, dia: date, subir=None) -> str:
    destino = f"walcu/ventas/{dia.isoformat()}"
    with tempfile.TemporaryDirectory(prefix="cdf_walcu_ventas_") as tmp:
        with gzip.open(Path(tmp) / f"{run_id}.csv.gz", "wb", compresslevel=6) as g:
            g.write(contenido)
        (subir or cx.subir_rclone)(Path(tmp), destino)
    return destino


def escribir(pg: psycopg.Connection, filas: list[dict], fuente: str, desde: date, hasta: date, h: str,
             nombre: str | None = None) -> str:
    """Una carga con sus filas (las de 0 ventas no se guardan). Devuelve el run_id."""
    momento = cx.ahora()
    run_id = f"walcu-ventas-{fuente}-{momento:%Y%m%dT%H%M%S%f}-{h[:8]}"
    with pg.transaction():
        pg.execute("""INSERT INTO walcu_ventas_carga (run_id, fecha_carga, fuente, fichero_origen, huella, desde, hasta)
                      VALUES (%s, %s, %s, %s, %s, %s, %s)""", (run_id, momento, fuente, nombre, h, desde, hasta))
        with pg.cursor() as cur:
            cur.executemany("INSERT INTO walcu_ventas_semana (run_id, semana, tipo, modelo, ventas) "
                            "VALUES (%s, %s, %s, %s, %s)",
                            [(run_id, f["semana"], f["tipo"], f["modelo"], f["ventas"]) for f in filas if f["ventas"]])
    return run_id


def cargar_con(pg: psycopg.Connection, contenido: bytes, nombre: str, hoy: date, hasta: date | None = None,
               subir=None) -> dict:
    """Valida, sube al bucket y guarda (fuente csv). Lanza ErrorWalcu si no vale."""
    v = validar(contenido)
    nombre = Path(nombre or "ventas.csv").name
    if personal(nombre):
        nombre = "ventas.csv"
    if hasta is not None:
        if hasta.isoweekday() != 1:
            raise ErrorWalcu(["--hasta debe ser un lunes"])
        if hasta < v.hasta:
            raise ErrorWalcu(["--hasta es anterior a la última semana del fichero"])
    hasta = hasta or v.hasta
    h = huella(contenido + hasta.isoformat().encode())
    resumen = {"fichero": nombre, "filas": len(v.filas), "ventas": sum(f["ventas"] for f in v.filas),
               "desde": v.desde.isoformat(), "hasta": hasta.isoformat(), "avisos": v.avisos}
    previa = pg.execute("SELECT run_id FROM walcu_ventas_carga WHERE huella = %s AND fuente = 'csv'", (h,)).fetchone()
    if previa:
        return {**resumen, "ya_cargado": previa[0]}
    tmp_id = f"walcu-ventas-csv-{h[:12]}"
    resumen["bucket"] = _subir(contenido, tmp_id, hoy, subir)            # antes de escribir nada
    resumen["run_id"] = escribir(pg, v.filas, "csv", v.desde, hasta, h, nombre)
    return resumen


# --------------------------------------------------------------------------- lectura (las ventas vigentes)

def vigentes(pg: psycopg.Connection) -> tuple[dict[tuple[date, str], int], set[date]]:
    """((semana, tipo) → ventas, semanas cubiertas). Para cada semana, la carga de la API más reciente que la cubre
    o, si no hay, la del CSV más reciente."""
    cargas = pg.execute("""SELECT run_id, fuente, desde, hasta FROM walcu_ventas_carga
                           ORDER BY (fuente = 'api') DESC, fecha_carga DESC, id DESC""").fetchall()
    ganadora: dict[date, str] = {}
    for run_id, _, desde, hasta in cargas:
        s = desde
        while s <= hasta:
            ganadora.setdefault(s, run_id)
            s += UNA_SEMANA
    if not ganadora:
        return {}, set()
    ventas: dict[tuple[date, str], int] = defaultdict(int)
    for run_id, semana, tipo, n in pg.execute(
            "SELECT run_id, semana, tipo, sum(ventas) FROM walcu_ventas_semana WHERE run_id = ANY(%s) GROUP BY 1, 2, 3",
            (list(set(ganadora.values())),)):
        if ganadora.get(semana) == run_id:
            ventas[(semana, tipo)] += int(n)
    return dict(ventas), set(ganadora)


def main(argv=None, entrada=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("orden", choices=("cargar", "comprobar"))
    parser.add_argument("--nombre", default="ventas.csv", help="nombre del fichero (se guarda sin la ruta)")
    parser.add_argument("--hasta", type=date.fromisoformat,
                        help="última semana que cubre (lunes; por defecto, la última del fichero)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    contenido = (entrada or sys.stdin.buffer).read()
    try:
        if args.orden == "comprobar":
            v = validar(contenido)
            print(f"Fichero correcto: {len(v.filas)} filas, {sum(f['ventas'] for f in v.filas)} ventas, de la semana "
                  f"del {v.desde:%d-%m-%Y} a la del {v.hasta:%d-%m-%Y}." + "".join(f"\nAviso: {a}." for a in v.avisos))
            return 0
        from ingesta.db import conectar
        from ingesta.scrapers_vw import hoy_madrid
        with conectar(autocommit=True) as pg:
            resumen = cargar_con(pg, contenido, args.nombre, hoy_madrid(), args.hasta)
            if "ya_cargado" in resumen:
                print(f"Ese fichero ya estaba cargado ({resumen['ya_cargado']}): no se carga otra vez.")
                return 0
            cx.anotar_tipo(pg, "walcu_ventas", hoy_madrid(), "carga", "ok",
                           {k: resumen[k] for k in ("fichero", "filas", "ventas", "desde", "hasta", "run_id")})
    except ErrorWalcu as exc:
        print(exc, file=sys.stderr)
        return 2
    except cx.ErrorDescarga as exc:
        print(f"No se ha cargado nada: {exc}", file=sys.stderr)
        return 1
    print(f"Cargadas {resumen['filas']} filas ({resumen['ventas']} ventas), de la semana del {resumen['desde']} a la "
          f"del {resumen['hasta']} ({resumen['run_id']}). Cada semana sustituye a la que hubiera del CSV.")
    for a in resumen["avisos"]:
        print(f"Aviso: {a}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
