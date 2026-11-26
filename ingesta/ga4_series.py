"""Señales semanales de intención de compra de GA4 (tablas ga4_intencion_*, migración 0026).

Por semana (lunes ISO), tipo (VN, VO o sin_tipo), grupo de canal (organico, directo, pagado, otros) y marca
(propia o sin_marca), solo agregados:
- sesiones, sesiones con interacción y tiempo de interacción (segundos), por la página de destino;
- vistas de fichas (de nuevo, de ocasión y de km 0, y las landings de modelo), por la página vista;
- eventos de intención (config/ga4_intencion.yaml: configurador, form_start, form_submit, Solicitar información,
  Click llamada o email, Lead - Nuevos y Lead - Segunda mano), por la página del evento y solo de los dominios
  propios (ni compartidos ni sin dominio);
y, aparte, las vistas de fichas por modelo (ga4_fichas_modelo_semana).

Las páginas se clasifican con config/ga4_paginas.yaml (ingesta/ga4_paginas.py) por su dominio (hostName: un
subdominio por marca; solo cuentan los de la marca propia) y su ruta: las no comerciales, las de otras marcas del
grupo y las de dominios ajenos no cuentan. El canal, con config/canales.yaml (fuente, medio y agrupación de
canales de GA4), agrupado con config/ga4_intencion.yaml. Ninguna dimensión de usuario: de las páginas, solo el
dominio y la ruta.

Cada semana cargada queda en ga4_intencion_carga con la huella de su contenido: si al recargarla no ha cambiado
(ni config/ga4_paginas.yaml), no se añade nada; si ha cambiado, sus filas nuevas sustituyen a las anteriores (vistas
*_vigente) sin borrar nada. Las respuestas brutas van comprimidas al bucket (ga4_series/raw/<día>/) antes de
escribir en Postgres.

Cuándo:
- Carga inicial: make ga4-series DESDE=2023-06-05 (hasta la última semana terminada), por tramos de semanas.
- Cada noche, dentro de la descarga de GA4 (make ga4, 06:10): las últimas semanas terminadas y las que falten.

Uso:
  python -m ingesta.ga4_series --desde 2023-06-05 [--hasta 2026-11-16]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import sys
import time
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg
import yaml

from ingesta import canales as cn
from ingesta import conectores as cx
from ingesta import ga4
from ingesta import ga4_paginas as gp
from ingesta.campanas import RAIZ
from ingesta.concesionario import ErrorConfiguracion

LOG = logging.getLogger("ingesta.ga4_series")
FUENTE = "ga4_series"
ORIGEN = "ga4_data_api v1beta"
UNA_SEMANA = timedelta(days=7)
GRUPOS = ("organico", "directo", "pagado", "otros")
MARCAS = (gp.PROPIA, gp.SIN_MARCA)
SESION = ("sesiones", "sesiones_interaccion", "tiempo_interaccion")
EVENTOS = ("configurar", "color", "opcionales", "precio", "form_start", "form_submit", "solicitar_informacion",
           "click_llamada_email", "lead_nuevos", "lead_segunda_mano")
COLUMNAS = SESION + ("vistas_fichas",) + EVENTOS
CANAL_DIMS = ["sessionSource", "sessionMedium", "sessionDefaultChannelGroup"]


# --------------------------------------------------------------------------- configuración

@dataclass(frozen=True)
class Config:
    inicio: date
    semanas_recarga: int
    semanas_por_tramo: int
    grupos_canal: dict[str, str]
    pagado_ga4: frozenset[str]
    eventos: dict[str, tuple[str, str | None]]          # columna → (evento, tipo fijo)
    analisis: dict
    eventos_solo_explorar: tuple[str, ...] = ()

    def eventos_intencion(self) -> list[str]:
        """Los eventos de intención que enseña make ga4-explorar: los de las series y los solo de exploración."""
        series = [e for e, _ in self.eventos.values()]
        return series + [e for e in self.eventos_solo_explorar if e not in series]


def ruta() -> Path:
    return Path(os.environ.get("CDF_GA4_INTENCION", RAIZ / "config" / "ga4_intencion.yaml"))


def lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def cargar_config(fichero: Path | None = None) -> Config:
    cfg = yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {}

    def error(que: str) -> ErrorConfiguracion:
        return ErrorConfiguracion(f"config/ga4_intencion.yaml: {que}")

    inicio = cfg.get("inicio")
    if not isinstance(inicio, date) or inicio.weekday() != 0:
        raise error("«inicio» tiene que ser un lunes (AAAA-MM-DD)")
    enteros = {}
    for clave in ("semanas_recarga", "semanas_por_tramo"):
        v = cfg.get(clave)
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise error(f"«{clave}» tiene que ser un entero ≥ 1")
        enteros[clave] = v
    grupos = cfg.get("grupos_canal") or {}
    if not isinstance(grupos, dict) or any(g not in GRUPOS for g in grupos.values()):
        raise error(f"«grupos_canal» lleva cada canal a uno de {', '.join(GRUPOS)}")
    eventos = cfg.get("eventos") or {}
    if not isinstance(eventos, dict) or set(eventos) != set(EVENTOS):
        raise error("«eventos» tiene que tener exactamente las columnas " + ", ".join(EVENTOS))
    ev = {}
    for col, e in eventos.items():
        if not isinstance(e, dict) or not str(e.get("evento") or "").strip() or e.get("tipo") not in (None, "VN", "VO"):
            raise error(f"«eventos.{col}» necesita «evento» y, si lleva «tipo», VN o VO")
        ev[col] = (str(e["evento"]).strip(), e.get("tipo"))
    if len({e for e, _ in ev.values()}) != len(ev):
        raise error("un evento de GA4 solo puede ir a una columna")
    analisis = cfg.get("analisis") or {}
    if not isinstance(analisis, dict):
        raise error("«analisis» debe ser un diccionario")
    return Config(inicio, enteros["semanas_recarga"], enteros["semanas_por_tramo"],
                  {str(k): v for k, v in grupos.items()}, frozenset(str(x) for x in cfg.get("pagado_ga4") or []),
                  ev, analisis, tuple(str(e) for e in cfg.get("eventos_solo_explorar") or []))


# --------------------------------------------------------------------------- de las filas de GA4 a las de la tabla

def semana_iso(valor: str) -> date:
    """«202523» (isoYearIsoWeek) → lunes de esa semana."""
    return date.fromisocalendar(int(valor[:4]), int(valor[4:]), 1)


class Agregador:
    def __init__(self, cfg: Config, paginas: gp.Paginas, canales: cn.Canales):
        self.cfg, self.paginas, self.canales = cfg, paginas, canales
        self.filas: dict[tuple, dict[str, float]] = defaultdict(lambda: dict.fromkeys(COLUMNAS, 0))
        self.modelos: dict[tuple, int] = defaultdict(int)
        self.por_evento = {e: (col, tipo) for col, (e, tipo) in cfg.eventos.items()}
        self._clases: dict[tuple, gp.Pagina] = {}
        self._grupos: dict[tuple, str] = {}

    def pagina(self, ruta: str, host: str | None) -> gp.Pagina:
        k = (ruta, host)
        if k not in self._clases:
            self._clases[k] = self.paginas.clasificar(ruta, host)
        return self._clases[k]

    def grupo(self, fuente: str, medio: str, canal_ga4: str) -> str:
        k = (fuente, medio, canal_ga4)
        if k not in self._grupos:
            canal = self.canales.canal(fuente, medio, canal_ga4)
            self._grupos[k] = self.cfg.grupos_canal.get(canal) or (
                "pagado" if canal_ga4 in self.cfg.pagado_ga4 else "otros")
        return self._grupos[k]

    def _sumar(self, semana: date, tipo: str | None, grupo: str, marca: str, valores: Mapping[str, float]) -> None:
        f = self.filas[(semana, tipo or "sin_tipo", grupo, marca)]
        for c, v in valores.items():
            f[c] += v

    def sesiones(self, d: list[str], m: list[str], host: str | None) -> None:
        p = self.pagina(d[1], host)
        if p.cuenta:
            self._sumar(semana_iso(d[0]), p.tipo, self.grupo(*d[2:5]), p.marca,
                        {"sesiones": int(float(m[0])), "sesiones_interaccion": int(float(m[1])),
                         "tiempo_interaccion": float(m[2])})

    def vistas(self, d: list[str], m: list[str], host: str | None) -> None:
        p = self.pagina(d[1], host)
        if not (p.cuenta and p.ficha and p.tipo):
            return
        semana, n = semana_iso(d[0]), int(float(m[0]))
        self._sumar(semana, p.tipo, self.grupo(*d[2:5]), p.marca, {"vistas_fichas": n})
        if p.modelo:
            self.modelos[(semana, p.tipo, p.modelo)] += n

    def eventos(self, d: list[str], m: list[str], host: str | None) -> None:
        """Solo los eventos de los dominios propios (ni compartidos ni sin dominio): el tipo fijo del evento
        (configurador y «Lead - Nuevos», VN…) nunca lo saca del filtro de dominio."""
        col, tipo = self.por_evento.get(d[1], (None, None))
        if col is None or self.paginas.dominio(host) != "propio":
            return
        p = self.pagina(d[2], host)
        if not p.cuenta:
            return
        self._sumar(semana_iso(d[0]), tipo or p.tipo, self.grupo(*d[3:6]), p.marca, {col: int(float(m[0]))})

    def por_semana(self) -> dict[date, tuple[list[dict], list[dict]]]:
        salida: dict[date, tuple[list[dict], list[dict]]] = defaultdict(lambda: ([], []))
        for (semana, tipo, grupo, marca), v in sorted(self.filas.items()):
            if any(v.values()):
                salida[semana][0].append({"semana": semana, "tipo": tipo, "canal": grupo, "marca": marca,
                                          **{c: (round(x, 3) if c == "tiempo_interaccion" else int(x))
                                             for c, x in v.items()}})
        for (semana, tipo, modelo), n in sorted(self.modelos.items()):
            salida[semana][1].append({"semana": semana, "tipo": tipo, "modelo": modelo, "vistas": n})
        return salida


def huella(filas: list[dict], modelos: list[dict]) -> str:
    datos = json.dumps([filas, modelos], sort_keys=True, default=str, ensure_ascii=False)
    return hashlib.sha256(datos.encode()).hexdigest()


# --------------------------------------------------------------------------- descarga y escritura

def descargar_tramo(cli, desde: date, hasta: date, cfg: Config, paginas: gp.Paginas, canales: cn.Canales
                    ) -> dict[date, tuple[list[dict], list[dict]]]:
    """Semanas [desde, hasta] (lunes a domingo) → {semana: (filas, filas por modelo)}."""
    extra = ["hostName"]                     # la marca sale del subdominio
    ag = Agregador(cfg, paginas, canales)

    def host(d: list[str]) -> str:
        return d[-1]

    for d, m in cli.informe(desde, hasta, ["isoYearIsoWeek", "landingPage", *CANAL_DIMS, *extra],
                            ["sessions", "engagedSessions", "userEngagementDuration"]):
        ag.sesiones(d, m, host(d))
    for d, m in cli.informe(desde, hasta, ["isoYearIsoWeek", "pagePath", *CANAL_DIMS, *extra], ["screenPageViews"]):
        ag.vistas(d, m, host(d))
    filtro = {"filter": {"fieldName": "eventName",
                         "inListFilter": {"values": sorted(e for e, _ in cfg.eventos.values())}}}
    for d, m in cli.informe(desde, hasta, ["isoYearIsoWeek", "eventName", "pagePath", *CANAL_DIMS, *extra],
                            ["eventCount"], filtro=filtro):
        ag.eventos(d, m, host(d))
    return ag.por_semana()


def ultima_semana_terminada(hoy: date) -> date:
    """El lunes de la última semana cuyo domingo ya ha pasado (los datos de GA4 llegan hasta ayer)."""
    return lunes(hoy) - UNA_SEMANA


def escribir(pg: psycopg.Connection, semanas: list[date], datos: dict, run_id: str, momento, propiedad: str,
             version: str) -> tuple[int, int]:
    """Añade las semanas cuyo contenido ha cambiado. (semanas nuevas o cambiadas, semanas iguales)."""
    vigente = {s: (h, v) for s, h, v in pg.execute(
        "SELECT semana, huella, version_paginas FROM ga4_intencion_carga_vigente WHERE semana = ANY(%s)",
        (semanas,)).fetchall()}
    nuevas = iguales = 0
    cols = ["run_id", "semana", "tipo", "canal", "marca", *COLUMNAS]
    for s in semanas:
        filas, modelos = datos.get(s, ([], []))
        h = huella(filas, modelos)
        if vigente.get(s) == (h, version):
            iguales += 1
            continue
        nuevas += 1
        pg.execute("""INSERT INTO ga4_intencion_carga (run_id, cargado_at, origen, propiedad_id, semana, huella, filas,
                      version_paginas) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
                   (run_id, momento, ORIGEN, propiedad, s, h, len(filas), version))
        with pg.cursor() as cur:
            if filas:
                cur.executemany(f"INSERT INTO ga4_intencion_semana ({', '.join(cols)}) VALUES "
                                f"({', '.join(['%s'] * len(cols))})",
                                [(run_id, *[f[c] for c in cols[1:]]) for f in filas])
            if modelos:
                cur.executemany("INSERT INTO ga4_fichas_modelo_semana (run_id, semana, tipo, modelo, vistas) "
                                "VALUES (%s, %s, %s, %s, %s)",
                                [(run_id, f["semana"], f["tipo"], f["modelo"], f["vistas"]) for f in modelos])
    return nuevas, iguales


def cargar(pg: psycopg.Connection, cli, propiedad: str, desde: date, hasta: date, hoy: date, cfg: Config | None = None,
           paginas: gp.Paginas | None = None, canales: cn.Canales | None = None, subir=None) -> dict:
    """Carga las semanas de `desde` a `hasta` (lunes), por tramos: cada tramo, sus brutos al bucket y después su
    transacción."""
    cfg = cfg or cargar_config()
    paginas = paginas or gp.cargar()
    canales = canales or cn.cargar()
    desde, hasta = lunes(desde), min(lunes(hasta), ultima_semana_terminada(hoy))
    momento = cx.ahora()
    run_id = f"ga4s-{momento:%Y%m%dT%H%M%S%f}"
    resumen = {"desde": str(desde), "hasta": str(hasta), "semanas_nuevas": 0, "semanas_iguales": 0, "peticiones": 0}
    if hasta < desde:
        return resumen
    inicio = desde
    while inicio <= hasta:
        fin = min(inicio + (cfg.semanas_por_tramo - 1) * UNA_SEMANA, hasta)
        semanas = [inicio + i * UNA_SEMANA for i in range((fin - inicio).days // 7 + 1)]
        cli.brutos, antes = [], cli.peticiones
        datos = descargar_tramo(cli, inicio, fin + timedelta(days=6), cfg, paginas, canales)
        cx.guardar_brutos(FUENTE, f"{run_id}_{inicio:%Y%m%d}", hoy, cli.brutos, subir)
        resumen["peticiones"] += cli.peticiones - antes
        with pg.transaction():
            n, i = escribir(pg, semanas, datos, run_id, momento, propiedad, paginas.version)
        resumen["semanas_nuevas"] += n
        resumen["semanas_iguales"] += i
        inicio = fin + UNA_SEMANA
    return resumen


def semanas_diarias(pg: psycopg.Connection, hoy: date, cfg: Config) -> tuple[date, date] | None:
    """Lo que recarga la descarga diaria: las últimas semanas_recarga semanas terminadas y las que falten desde la
    última cargada. None si todavía no hay ninguna carga (la inicial es make ga4-series)."""
    ultima = pg.execute("SELECT max(semana) FROM ga4_intencion_carga").fetchone()[0]
    if ultima is None:
        return None
    hasta = ultima_semana_terminada(hoy)
    desde = min(ultima + UNA_SEMANA, hasta - (cfg.semanas_recarga - 1) * UNA_SEMANA)
    return max(desde, cfg.inicio), hasta


def actualizar_sin_romper(pg: psycopg.Connection, token: str, propiedad: str, secretos: list[str], hoy: date,
                          subir=None, dormir=time.sleep) -> dict:
    """Lo que llama la descarga diaria de GA4, ya confirmada: un fallo queda como aviso, no como fallo de la
    descarga."""
    from ingesta.ga4_explorar import Cliente
    try:
        cfg = cargar_config()
        rango = semanas_diarias(pg, hoy, cfg)
        if rango is None:
            return {"series": "sin carga inicial (make ga4-series DESDE=…)"}
        cli = Cliente(token, propiedad, secretos, dormir)
        return {"series": cargar(pg, cli, propiedad, *rango, hoy, cfg, subir=subir)}
    except Exception as exc:
        aviso = cx.limpiar(f"{type(exc).__name__}: {exc}", secretos)[:500]
        LOG.warning("La descarga de GA4 está guardada, pero no se han podido cargar las series semanales: %s", aviso)
        return {"aviso_series": aviso}


# --------------------------------------------------------------------------- línea de órdenes

def ejecutar(desde: date, hasta: date | None = None, entorno: Mapping[str, str] | None = None, subir=None,
             dormir=time.sleep, firmar=ga4.jwt_firmado) -> int:
    from ingesta.db import conectar
    from ingesta.ga4_explorar import Cliente
    from ingesta.scrapers_vw import hoy_madrid
    entorno = os.environ if entorno is None else entorno
    hoy = hoy_madrid()
    try:
        cfg = cargar_config()
        if desde < cfg.inicio:
            LOG.warning("DESDE es anterior a «inicio» (%s): se empieza en %s", cfg.inicio, cfg.inicio)
            desde = cfg.inicio
        propiedad, ruta_cred = ga4.credenciales(entorno)
        token, secretos = ga4.token_acceso(ruta_cred, dormir, firmar)
        cli = Cliente(token, propiedad, secretos, dormir)
        with conectar(autocommit=True) as pg:
            resumen = cargar(pg, cli, propiedad, desde, hasta or hoy, hoy, cfg, subir=subir)
    except cx.SinCredenciales as exc:
        LOG.error("GA4 sin credenciales: %s", exc)
        return 1
    except (cx.ErrorDescarga, gp.ErrorPaginas, ErrorConfiguracion, cn.ErrorCanales) as exc:
        LOG.error("Series de GA4: %s", exc)
        return 1
    LOG.info("Series de GA4: %s", resumen)
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--desde", type=date.fromisoformat, required=True, help="primera semana (AAAA-MM-DD)")
    parser.add_argument("--hasta", type=date.fromisoformat, help="última semana (por defecto, la última terminada)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return ejecutar(args.desde, args.hasta)


if __name__ == "__main__":
    sys.exit(main())
