"""Previsión de leads, citas y ventas de las campañas de Meta (tablas panel_prevision_* del contrato).

La posterior llega ya entrenada (analitica/prevision.py, cada noche). Aquí no se entrena nada:
- Marketing, «Previsión 4 semanas»: por tipo y por modelo con campaña activa, con el escenario de gasto
  elegido, recalculado en el momento con la posterior guardada (analitica/predictiva.py: misma semilla, mismas
  cifras que el precálculo); y, para cada tipo, el gasto que haría falta para N citas.
- Dirección: las ventas que vienen de las campañas de Meta, por VN y VO (la previsión guardada).
- Estado de las fuentes: la validación hacia atrás.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from functools import lru_cache

import psycopg

from analitica import predictiva as pd
from panel import formato as fmt
from panel import negocio as ng

MODELO, SEMANA, VALIDACION = "panel_prevision_modelo", "panel_prevision_semana", "panel_prevision_validacion"
COLS = ("tipo", "modelo", "columnas", "media", "covarianza", "a", "b", "cita_a", "cita_b", "venta_a", "venta_b",
        "trends", "semanas", "pocos_datos", "motivo", "ultima_semana", "primera_semana", "gasto_semanal_cents",
        "regimen_desde", "simulaciones", "semilla", "intervalo", "validada", "validacion", "aviso_gasto")
POSTERIOR = COLS[:12] + ("regimen_desde", "simulaciones", "semilla", "intervalo")
NOTA_META = ("Solo lo que viene de las campañas de Meta (leads de Walcu de origen Facebook), no el total del "
             "concesionario.")


def _tipos(tipo: str) -> tuple[str, ...]:
    return ("VN", "VO") if tipo == "todo" else (tipo,)


def _posterior(v: dict) -> pd.Posterior:
    return pd.Posterior(tuple(v["columnas"]), tuple(v["media"]), tuple(map(tuple, v["covarianza"])), v["a"], v["b"],
                        (v["cita_a"], v["cita_b"]), (v["venta_a"], v["venta_b"]), v["trends"])


def leer(pg: psycopg.Connection, origen: str | None) -> tuple[date | None, list[dict]]:
    dia = ng.ultimo_dia(pg, MODELO, origen)
    if dia is None:
        return None, []
    filas = [dict(zip(COLS, f)) for f in pg.execute(
        f"SELECT {', '.join(COLS)} FROM {MODELO} WHERE origen = %s AND dia = %s ORDER BY tipo, modelo NULLS FIRST",
        (origen, dia))]
    return dia, filas


def _cifra(q: tuple[int, int, int]) -> dict:
    return {"p50": fmt.miles(q[1]), "intervalo": f"{fmt.miles(q[0])}–{fmt.miles(q[2])}"}


@lru_cache(maxsize=256)
def _simular(clave: tuple, gasto: float, semanas: tuple[date, ...]):
    v = dict(clave)
    post = _posterior({**v, "columnas": list(v["columnas"]), "media": list(v["media"]),
                       "covarianza": [list(r) for r in v["covarianza"]]})
    return pd.resumen(pd.simular(post, list(semanas), [gasto] * len(semanas), v["regimen_desde"], v["simulaciones"],
                                 v["semilla"]), float(v["intervalo"]))


def _congelar(v: dict) -> tuple:
    """La posterior como clave de caché (no cambia hasta el próximo cálculo)."""
    return tuple(sorted({**{k: v[k] for k in POSTERIOR}, "columnas": tuple(v["columnas"]), "media": tuple(v["media"]),
                         "covarianza": tuple(map(tuple, v["covarianza"]))}.items()))


@lru_cache(maxsize=64)
def _inversa(clave: tuple, semanas: tuple[date, ...], objetivo: int, maximo: float):
    v = dict(clave)
    post = _posterior({**v, "columnas": list(v["columnas"]), "media": list(v["media"]),
                       "covarianza": [list(r) for r in v["covarianza"]]})
    return pd.gasto_para_citas(post, list(semanas), objetivo, v["regimen_desde"], v["simulaciones"], v["semilla"],
                               float(v["intervalo"]), maximo)


def _euros(e: float | None) -> str:
    """Gasto redondeado a decenas de euros."""
    return fmt.GUION if e is None else f"{fmt.miles(round(e / 10) * 10)} €"


def marketing(pg: psycopg.Connection, origen: str | None, tipo: str, escenario: int, objetivos: dict[str, int | None],
              n_semanas: int, base_url: str, objetivo_html: str) -> dict:
    """Bloque «Previsión 4 semanas» de Marketing con el escenario de gasto pedido (% sobre el previsto)."""
    dia, filas = leer(pg, origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    par = pd.parametros_panel()
    escenario = escenario if escenario in par.escenarios else 0
    grupos = []
    for v in filas:
        if v["tipo"] not in _tipos(tipo):
            continue
        if v["modelo"] is not None and not v["gasto_semanal_cents"]:
            continue                                       # modelo sin campaña activa
        nombre = v["modelo"] or ("Nuevos (VN)" if v["tipo"] == "VN" else "Ocasión (VO)")
        if not v["validada"]:            # regla del proyecto: sin batir a la referencia, no se publica
            grupos.append({"tipo": v["tipo"], "modelo": v["modelo"], "nombre": nombre, "es_tipo": v["modelo"] is None,
                           "publicada": False, "validacion": v["validacion"], "pocos": v["pocos_datos"],
                           "motivo": v["motivo"] or ""})
            continue
        semanas = tuple(v["primera_semana"] + timedelta(weeks=j) for j in range(n_semanas))
        base = v["gasto_semanal_cents"] / 100
        gasto = base * (1 + escenario / 100)
        clave = _congelar(v)
        r = _simular(clave, gasto, semanas)
        fila = {"tipo": v["tipo"], "modelo": v["modelo"], "nombre": nombre, "es_tipo": v["modelo"] is None,
                "publicada": True, "validacion": v["validacion"],
                "aviso_gasto": v["aviso_gasto"] if escenario == 0 else None,
                "gasto": _euros(gasto), "leads": _cifra(r["leads_total"]), "citas": _cifra(r["citas_total"]),
                "ventas": _cifra(r["ventas_total"]), "pocos": v["pocos_datos"], "motivo": v["motivo"] or "",
                "semanas_datos": v["semanas"], "sin_gasto": gasto <= 0}
        if v["modelo"] is None:
            actual = _simular(clave, base, semanas)["citas_total"][1]
            objetivo = objetivos.get(v["tipo"]) or max(1, math.ceil(actual * par.objetivo_citas))
            maximo = max(base, 50.0) * par.gasto_maximo
            inv = _inversa(clave, semanas, objetivo, maximo)
            fila["objetivo"] = objetivo
            if inv is None:
                fila["inversa"] = (f"Para conseguir {fmt.miles(objetivo)} citas en {n_semanas} semanas no basta ni con "
                                   f"{_euros(maximo)} a la semana.")
            else:
                fila["inversa"] = (f"Para conseguir {fmt.miles(objetivo)} citas en {n_semanas} semanas haría falta un "
                                   f"gasto de unos {_euros(inv['mediana'])} a la semana (entre {_euros(inv['bajo'])} y "
                                   + (f"{_euros(inv['alto'])}" if inv["alto"] is not None else f"más de {_euros(maximo)}")
                                   + f"); el previsto es {_euros(base)}.")
        grupos.append(fila)
    if not grupos:
        return ng.bloque(origen, hay=False)
    ultima = min(v["ultima_semana"] for v in filas)
    primera = filas[0]["primera_semana"]
    pct = round(float(filas[0]["intervalo"]) * 100)
    escenarios = [{"id": e, "etiqueta": "Actual" if e == 0 else f"{'+' if e > 0 else '−'}{abs(e)} %",
                   "activa": e == escenario, "url": f"{base_url}&gasto={e}", "objetivo": objetivo_html}
                  for e in par.escenarios]
    return ng.bloque(origen, grupos=grupos, escenarios=escenarios, escenario=escenario, pct=pct,
                     aviso=f"Previsión con datos hasta la semana del {fmt.fecha(ultima)}",
                     periodo=f"{fmt.fecha_corta(primera)}–{fmt.fecha_corta(primera + timedelta(weeks=n_semanas) - timedelta(days=1))}",
                     n_semanas=n_semanas, hay_pocos=any(g["pocos"] for g in grupos), nota=NOTA_META,
                     calculado=fmt.fecha_corta(dia))


def direccion(pg: psycopg.Connection, origen: str | None, tipo: str) -> dict:
    """Ventas de las campañas de Meta en las semanas previstas, por VN y VO (escenario de gasto actual)."""
    dia = ng.ultimo_dia(pg, SEMANA, origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    filas = {t: (lo, med, hi, pocos, g) for t, lo, med, hi, pocos, g in pg.execute(
        f"""SELECT tipo, ventas_p10, ventas_p50, ventas_p90, pocos_datos, gasto_cents FROM {SEMANA}
            WHERE origen = %s AND dia = %s AND modelo IS NULL AND semana IS NULL AND escenario = 0""", (origen, dia))}
    validada = {t: (ok, texto) for t, ok, texto in pg.execute(
        f"SELECT tipo, validada, validacion FROM {MODELO} WHERE origen = %s AND dia = %s AND modelo IS NULL",
        (origen, dia))}
    info = pg.execute(f"""SELECT min(ultima_semana), min(primera_semana) FROM {MODELO}
                          WHERE origen = %s AND dia = %s""", (origen, dia)).fetchone()
    n = pg.execute(f"SELECT count(DISTINCT semana) FROM {SEMANA} WHERE origen = %s AND dia = %s AND semana IS NOT NULL",
                   (origen, dia)).fetchone()[0]
    tipos = [t for t in _tipos(tipo) if t in filas]
    if not tipos:
        return ng.bloque(origen, hay=False)
    return ng.bloque(origen, filas=[
        {"tipo": t, "nombre": "Nuevos (VN)" if t == "VN" else "Ocasión (VO)", "p50": fmt.miles(filas[t][1]),
         "intervalo": f"{fmt.miles(filas[t][0])}–{fmt.miles(filas[t][2])}", "pocos": filas[t][3],
         "gasto": _euros(filas[t][4] / 100 / max(n, 1)), "publicada": validada.get(t, (False, ""))[0],
         "validacion": validada.get(t, (False, "Sin validación todavía."))[1]} for t in tipos],
        n_semanas=n, aviso=f"Previsión con datos hasta la semana del {fmt.fecha(info[0])}", nota=NOTA_META,
        periodo=f"{fmt.fecha_corta(info[1])}–{fmt.fecha_corta(info[1] + timedelta(weeks=n) - timedelta(days=1))}")


def validacion(pg: psycopg.Connection) -> dict:
    """La validación hacia atrás del último cálculo (siempre real), para «Estado de las fuentes»."""
    dia = pg.execute(f"SELECT max(dia) FROM {VALIDACION} WHERE origen = 'real'").fetchone()[0]
    if dia is None:
        return {"hay": False}
    nombres = {"leads": "Leads", "citas": "Citas", "ventas": "Ventas"}
    filas = []
    for t, nivel, medida, variante, n, mm, mr, cob, mejora, desde, hasta, trends in pg.execute(
            f"""SELECT tipo, nivel, medida, variante, n, mae_modelo, mae_referencia, cobertura, mejora, desde_semana,
                       hasta_semana, trends FROM {VALIDACION} WHERE origen = 'real' AND dia = %s
                ORDER BY tipo, nivel DESC, array_position(ARRAY['leads','citas','ventas'], medida),
                         variante DESC""", (dia,)):
        cob = float(cob)
        filas.append({"tipo": t, "nivel": "Tipo" if nivel == "tipo" else "Modelos", "medida": nombres[medida],
                      "variante": "Gasto que hubo" if variante == "gasto_real" else "Gasto previsto",
                      "n": fmt.miles(n), "mae_modelo": fmt.decimal(float(mm)), "mae_referencia": fmt.decimal(float(mr)),
                      "mejora": "Sí" if mejora else "No", "cobertura": fmt.porcentaje(cob * 100, 0),
                      "cobertura_fuera": not 0.7 <= cob <= 0.9, "trends": trends,
                      "periodo": f"{fmt.fecha_corta(desde)}–{fmt.fecha_corta(hasta + timedelta(days=6))}"})
    return {"hay": True, "dia": fmt.fecha(dia), "filas": filas}
