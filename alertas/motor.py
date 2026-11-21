"""Motor de alertas: cada mañana a las 09:15 (cron de cdf_jobs), después del precálculo de las 09:10.

Determinista y sin ningún modelo de lenguaje: evalúa las reglas de alertas/reglas.yaml (umbrales,
destinatarios y textos) con los cálculos de alertas/reglas.py y escribe:

- alertas: una fila por regla, rol y clave de lo que la dispara, con su texto (el motivo en una
  frase con sus números), sus cifras en JSON, su origen y su estado:
    nueva    salta hoy y no estaba abierta en la evaluación anterior de la regla;
    abierta  ya lo estaba (conserva abierta_desde): no se vuelve a enviar;
    cerrada  estaba abierta y hoy ya no se cumple (se escribe una vez, el día que se cierra).
  Si una regla no se puede evaluar hoy para un tipo (falta la captura de hoy o la de hace 7 días),
  sus alertas abiertas de ese tipo siguen abiertas: ni se cierran ni saltan otras nuevas.
- alertas_evaluacion: qué reglas se evaluaron y con qué origen (y por qué no, si no se pudo).

Origen de cada regla: las que dependen de fuentes_estado o de panel_mercado_* son siempre reales.
Las preparadas dependen de tablas del contrato del panel: con filas reales en todas sus tablas se
evalúan con ellas; si no, con filas de ejemplo en todas, sobre el ejemplo; si no, quedan inactivas.
Una alerta calculada sobre filas de ejemplo va SOLO al rol técnico, con «[EJEMPLO]» delante.

Después envía por Telegram un mensaje por rol con sus alertas nuevas (alertas/envio.py).

Recalcular un día sustituye sus filas de alertas y alertas_evaluacion; lo ya enviado no se repite.

Uso:
  python -m alertas.motor                    hoy (cron; make alertas)
  python -m alertas.motor --sin-telegram     calcula sin enviar (SIN_TELEGRAM=1 make alertas)
  python -m alertas.motor --dia 2026-11-21
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import psycopg
import yaml
from psycopg.types.json import Jsonb

from alertas import envio
from alertas.reglas import CALCULOS, TODOS, Contexto, Resultado
from ingesta import telegram
from ingesta.concesionario import Concesionario, cargar as cargar_concesionario
from ingesta.db import conectar
from ingesta.scrapers_vw import hoy_madrid
from panel import contrato

LOG = logging.getLogger("alertas.motor")
AQUI = Path(__file__).resolve().parent
ROLES = ("direccion", "ventas", "marketing", "tecnico")
TECNICO = "tecnico"
PREFIJO_EJEMPLO = "[EJEMPLO] "


class ErrorReglas(Exception):
    pass


@dataclass(frozen=True)
class Regla:
    id: str
    nombre: str
    calculo: str
    roles: tuple[str, ...]
    fuente: tuple[str, ...]
    tema: str
    nivel: str
    datos: dict                     # la entrada entera del YAML (umbrales, textos, n_minimo…)

    def texto(self, caso: str | None) -> str:
        if caso:
            return self.datos["textos"][caso]
        return self.datos["texto"]


def cargar_reglas(fichero: Path | None = None) -> tuple[str, list[Regla]]:
    """Lee y valida alertas/reglas.yaml. Devuelve (versión, reglas)."""
    cfg = yaml.safe_load((fichero or AQUI / "reglas.yaml").read_text(encoding="utf-8")) or {}
    reglas, vistos = [], set()
    for r in cfg.get("reglas") or []:
        faltan = [c for c in ("id", "nombre", "calculo", "roles", "fuente", "tema", "nivel") if not r.get(c)]
        if faltan:
            raise ErrorReglas(f"Regla {r.get('id', '?')}: faltan {', '.join(faltan)}")
        if r["id"] in vistos:
            raise ErrorReglas(f"Regla repetida: {r['id']}")
        vistos.add(r["id"])
        if r["calculo"] not in CALCULOS:
            raise ErrorReglas(f"Regla {r['id']}: cálculo desconocido «{r['calculo']}»")
        if set(r["roles"]) - set(ROLES):
            raise ErrorReglas(f"Regla {r['id']}: roles desconocidos {sorted(set(r['roles']) - set(ROLES))}")
        if not (r.get("texto") or r.get("textos")):
            raise ErrorReglas(f"Regla {r['id']}: sin texto")
        reglas.append(Regla(r["id"], r["nombre"], r["calculo"], tuple(r["roles"]), tuple(r["fuente"]), r["tema"],
                            r["nivel"], r))
    if not reglas:
        raise ErrorReglas("alertas/reglas.yaml no tiene reglas")
    return str(cfg.get("version") or "sin versión"), reglas


def origen_regla(regla: Regla, modos: dict[str, contrato.Modo]) -> tuple[str | None, str | None]:
    """(origen, motivo si queda inactiva). Real si todas sus tablas del contrato tienen filas reales;
    si no, ejemplo si todas tienen filas de ejemplo; si no, inactiva."""
    del_contrato = [t for t in regla.fuente if t in modos]
    if not del_contrato:
        return contrato.REAL, None
    if all(modos[t].real for t in del_contrato):
        return contrato.REAL, None
    if all(modos[t].ejemplo for t in del_contrato) and not any(modos[t].real for t in del_contrato):
        return contrato.EJEMPLO, None
    sin = [t for t in del_contrato if not modos[t].real]
    return None, f"sin filas reales en {', '.join(sin)}"


def _anteriores(pg, regla: str, origen: str, dia: date) -> dict[tuple[str, str], dict]:
    """Alertas abiertas (nueva o abierta) de la evaluación anterior de la regla, por (rol, clave)."""
    previo = pg.execute("SELECT max(dia) FROM alertas_evaluacion WHERE regla = %s AND origen = %s AND dia < %s",
                        (regla, origen, dia)).fetchone()[0]
    if previo is None:
        return {}
    cols = ("rol", "clave", "tipo", "texto", "cifras", "abierta_desde")
    return {(f[0], f[1]): dict(zip(cols, f)) for f in pg.execute(
        f"SELECT {', '.join(cols)} FROM alertas WHERE fecha = %s AND regla = %s AND origen = %s "
        "AND estado IN ('nueva', 'abierta')", (previo, regla, origen))}


def filas_de(regla: Regla, origen: str, dia: date, res: Resultado, previas: dict) -> list[dict]:
    """Filas de la tabla alertas de una regla en un día."""
    roles = (TECNICO,) if origen == contrato.EJEMPLO else regla.roles
    prefijo = PREFIJO_EJEMPLO if origen == contrato.EJEMPLO else ""
    filas, vistas = [], set()
    for d in res.disparos:
        texto = prefijo + regla.texto(d.texto).format(**d.huecos)
        for rol in roles:
            antes = previas.get((rol, d.clave))
            vistas.add((rol, d.clave))
            filas.append({"rol": rol, "clave": d.clave, "tipo": d.tipo, "texto": texto, "cifras": d.cifras,
                          "estado": "abierta" if antes else "nueva",
                          "abierta_desde": antes["abierta_desde"] if antes else dia})
    for (rol, clave), antes in sorted(previas.items()):
        if (rol, clave) in vistas:
            continue
        sigue = TODOS in res.sin_evaluar or antes["tipo"] in res.sin_evaluar
        filas.append({"rol": rol, "clave": clave, "tipo": antes["tipo"], "texto": antes["texto"],
                      "cifras": antes["cifras"], "estado": "abierta" if sigue else "cerrada",
                      "abierta_desde": antes["abierta_desde"]})
    for f in filas:
        f.update(dia=dia, regla=regla.id, tema=regla.datos["tema"], nivel=regla.datos["nivel"], origen=origen)
    return filas


COLS = ("fecha", "regla", "rol", "clave", "tema", "nivel", "tipo", "texto", "cifras", "origen", "estado", "abierta_desde")


def evaluar(pg: psycopg.Connection, dia: date, conc: Concesionario | None = None,
            reglas: list[Regla] | None = None) -> dict:
    """Evalúa todas las reglas el día `dia` y escribe alertas y alertas_evaluacion. Devuelve el resumen."""
    conc = conc or cargar_concesionario()
    if reglas is None:
        _, reglas = cargar_reglas()
    modos = contrato.modos(pg)
    resumen: dict = {"dia": str(dia), "reglas": {}, "nuevas": 0, "abiertas": 0, "cerradas": 0}
    for regla in reglas:
        origen, motivo = origen_regla(regla, modos)
        if origen is None:
            resumen["reglas"][regla.id] = f"inactiva: {motivo}"
            continue
        res = CALCULOS[regla.calculo](Contexto(pg, dia, conc, origen, regla.datos))
        previas = _anteriores(pg, regla.id, origen, dia)
        filas = filas_de(regla, origen, dia, res, previas)
        with pg.transaction():
            pg.execute("DELETE FROM alertas WHERE fecha = %s AND regla = %s AND origen = %s", (dia, regla.id, origen))
            pg.execute("DELETE FROM alertas_evaluacion WHERE dia = %s AND regla = %s AND origen = %s",
                       (dia, regla.id, origen))
            with pg.cursor() as cur:
                cur.executemany(
                    f"INSERT INTO alertas ({', '.join(COLS)}) VALUES ({', '.join(['%s'] * len(COLS))})",
                    [tuple(Jsonb(f["cifras"]) if c == "cifras" else f["dia" if c == "fecha" else c] for c in COLS)
                     for f in filas])
            pg.execute("INSERT INTO alertas_evaluacion (dia, origen, regla, estado, motivo, disparadas) "
                       "VALUES (%s, %s, %s, %s, %s, %s)",
                       (dia, origen, regla.id, "sin_evaluar" if res.sin_evaluar else "evaluada", res.motivo,
                        len(res.disparos)))
        estados = [f["estado"] for f in filas]
        for e, clave in (("nueva", "nuevas"), ("abierta", "abiertas"), ("cerrada", "cerradas")):
            resumen[clave] += estados.count(e)
        resumen["reglas"][regla.id] = (f"{origen}: {len(res.disparos)} disparadas"
                                       + (f" (sin evaluar: {res.motivo})" if res.motivo else ""))
    return resumen


# --------------------------------------------------------------------------- línea de órdenes

def _anotar(pg, dia: date, estado: str, detalle: dict) -> None:
    pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) VALUES ('alertas', 'alertas', %s, %s, %s)",
               (dia, estado, Jsonb(detalle)))


def ejecutar(dia: date | None = None, sin_telegram: bool = False, enviar=None, entorno=None) -> int:
    """Evalúa, anota en fuentes_estado y envía. Un fallo de Telegram no rompe la tarea (envio.py)."""
    dia = dia or hoy_madrid()
    inicio = time.monotonic()
    paso = "conexión a Postgres"
    try:
        with conectar(autocommit=True) as pg:
            paso = "reglas"
            version, reglas = cargar_reglas()
            paso = "evaluación"
            resumen = evaluar(pg, dia, reglas=reglas)
            resumen["version"] = version
            paso = "envío"
            resumen["envio"] = envio.enviar_dia(pg, dia, enviar=enviar, entorno=entorno, sin_telegram=sin_telegram)
            resumen["segundos"] = round(time.monotonic() - inicio, 1)
            _anotar(pg, dia, "ok", resumen)
        LOG.info("Alertas del %s: %s", dia, resumen)
        return 0
    except Exception as exc:
        error = telegram.limpiar(f"{type(exc).__name__}: {exc}")[:500]
        LOG.error("Motor de alertas fallido en «%s»: %s", paso, error)
        try:
            with conectar(autocommit=True, connect_timeout=10) as pg:
                _anotar(pg, dia, "error", {"paso": paso, "error": error})
        except Exception:
            LOG.warning("Tampoco se pudo anotar el fallo en fuentes_estado")
        return 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dia", type=date.fromisoformat, help="día de evaluación (por defecto, hoy)")
    parser.add_argument("--sin-telegram", action="store_true", help="calcula sin enviar nada")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    return ejecutar(args.dia, sin_telegram=args.sin_telegram)


if __name__ == "__main__":
    sys.exit(main())
