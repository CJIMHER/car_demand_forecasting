"""Salidas de nuestro stock publicado (tabla panel_salidas_stock del contrato): estimación de ventas.

Las salidas llegan ya calculadas (analitica/salidas.py, en el precálculo): aquí solo se cuentan y se escriben.
- Dirección: la tarjeta «Salidas del mes» y el bloque por mes (en curso y anterior), tipo y modelo. Con ventas del
  CRM (panel_ventas_crm, ingesta/walcu_crm.py), la tarjeta es «Ventas del mes» con ellas, se añade su bloque por
  mes, tipo y modelo, y las salidas quedan como comprobación.
- Ventas: las salidas de las últimas 4 semanas, un coche por fila, con su último precio, sus días publicado y
  la diferencia frente a la mediana de su segmento.
Las provisionales (aún pueden reaparecer) van en gris.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import psycopg

from panel import formato as fmt
from panel import negocio as ng
from panel import precio as pr

TABLA = "panel_salidas_stock"
NOTA = ("Estimación: un coche que deja de estar publicado. Incluye coches traspasados a otras sedes o empresas "
        "del grupo. Se validará con las ventas del CRM.")
NOTA_COMPROBACION = ("Comprobación: coches que dejan de estar publicados, frente a las ventas del CRM. Incluye "
                     "traspasos a otras sedes o empresas del grupo.")
TABLA_CRM = "panel_ventas_crm"
NOTA_CRM = ("Ventas del CRM: leads en estado de venta, en el mes de la firma, por la tipología y el modelo del coche "
            "vendido. Los traspasos entre sedes no son ventas.")
SEMANAS = 4


def _tipos(tipo: str) -> tuple[str, ...]:
    return ("VN", "VO") if tipo == "todo" else (tipo,)


def _mes_anterior(hoy: date) -> date:
    return (hoy.replace(day=1) - timedelta(days=1)).replace(day=1)


def _celda(n: int, prov: int) -> dict:
    """Las confirmadas y, aparte (en gris), las provisionales."""
    return {"n": fmt.miles(n - prov), "prov": (f"+ {fmt.miles(prov)} provisional{'es' if prov != 1 else ''}"
                                               if prov else ""), "total": n}


def _grupos(cuenta: dict[tuple[str, str], dict[date, tuple[int, int]]], tipos: tuple[str, ...], actual: date,
            anterior: date) -> tuple[list[dict], callable]:
    def suma(claves, mes):
        n = sum(cuenta[c].get(mes, (0, 0))[0] for c in claves)
        return n, sum(cuenta[c].get(mes, (0, 0))[1] for c in claves)

    grupos = []
    for t in tipos:
        claves = sorted((c for c in cuenta if c[0] == t),
                        key=lambda c: (-sum(n for n, _ in cuenta[c].values()), c[1]))
        grupos.append({
            "tipo": t, "nombre": "Nuevos (VN)" if t == "VN" else "Ocasión (VO)",
            "filas": [{"modelo": c[1], "anterior": _celda(*suma([c], anterior)), "actual": _celda(*suma([c], actual))}
                      for c in claves],
            "anterior": _celda(*suma(claves, anterior)), "actual": _celda(*suma(claves, actual)),
        })
    return grupos, suma


def mensual(pg: psycopg.Connection, origen: str | None, tipo: str, hoy: date, crm: bool = False) -> tuple[dict, dict]:
    """(tarjeta «Salidas del mes», bloque por mes, tipo y modelo). Mes en curso y mes anterior. `crm`: hay ventas
    del CRM, así que las salidas pasan a ser una comprobación."""
    if origen is None:
        return ng.bloque(None, hay=False), ng.bloque(None, hay=False)
    actual, anterior = hoy.replace(day=1), _mes_anterior(hoy)
    tipos = _tipos(tipo)
    filas = pg.execute(
        f"""SELECT tipo, modelo, date_trunc('month', dia)::date, count(*), count(*) FILTER (WHERE provisional)
            FROM {TABLA} WHERE origen = %s AND dia >= %s AND dia <= %s AND tipo = ANY(%s)
            GROUP BY 1, 2, 3""", (origen, anterior, hoy, list(tipos))).fetchall()
    calculado = ng.ultimo_dia(pg, TABLA, origen)
    cuenta: dict[tuple[str, str], dict[date, tuple[int, int]]] = defaultdict(dict)
    for t, modelo, mes, n, prov in filas:
        cuenta[(t, modelo)][mes] = (n, prov)
    grupos, suma = _grupos(cuenta, tipos, actual, anterior)
    todas = list(cuenta)
    n, prov = suma(todas, actual)
    por_tipo = {t: suma([c for c in todas if c[0] == t], actual)[0] for t in tipos}
    partes = ([f"VN {fmt.miles(por_tipo['VN'])} · VO {fmt.miles(por_tipo['VO'])}"] if tipo == "todo" else []) + \
        ([f"{fmt.miles(prov)} provisional{'es' if prov != 1 else ''}"] if prov else []) + ["estimación de ventas"]
    tarjeta = ng.bloque(origen, etiqueta="Salidas del mes" if tipo == "todo" else f"Salidas {tipo} del mes",
                        valor=fmt.miles(n), sub=" · ".join(partes), badge=None, tono="neutro")
    bloque = ng.bloque(origen, grupos=grupos, total=_celda(*suma(todas, actual)),
                       total_anterior=_celda(*suma(todas, anterior)), dos=len(tipos) > 1,
                       mes_actual=f"{fmt.MESES[actual.month - 1]} (hasta el {fmt.fecha_corta(hoy)})",
                       mes_anterior=fmt.MESES[anterior.month - 1], nota=NOTA_COMPROBACION if crm else NOTA,
                       titulo=("Salidas de nuestro stock (comprobación de las ventas del CRM)" if crm
                               else "Salidas de nuestro stock (estimación de ventas)"),
                       calculado=fmt.fecha_corta(calculado))
    return tarjeta, bloque


def crm(pg: psycopg.Connection, origen: str | None, tipo: str, hoy: date) -> tuple[dict, dict]:
    """(tarjeta «Ventas del mes», bloque por mes, tipo y modelo) con las ventas del CRM (panel_ventas_crm), del
    último cálculo. Mes en curso y mes anterior."""
    dia = ng.ultimo_dia(pg, TABLA_CRM, origen) if origen else None
    if dia is None:
        return ng.bloque(origen, hay=False), ng.bloque(origen, hay=False)
    actual, anterior = hoy.replace(day=1), _mes_anterior(hoy)
    tipos = _tipos(tipo)
    cuenta: dict[tuple[str, str], dict[date, tuple[int, int]]] = defaultdict(dict)
    for t, modelo, mes, n in pg.execute(
            f"""SELECT tipo, modelo, mes, ventas FROM {TABLA_CRM} WHERE origen = %s AND dia = %s AND mes >= %s
                AND mes <= %s AND tipo = ANY(%s)""", (origen, dia, anterior, actual, list(tipos))):
        cuenta[(t, modelo)][mes] = (n, 0)
    grupos, suma = _grupos(cuenta, tipos, actual, anterior)
    todas = list(cuenta)
    n = suma(todas, actual)[0]
    por_tipo = {t: suma([c for c in todas if c[0] == t], actual)[0] for t in tipos}
    partes = ([f"VN {fmt.miles(por_tipo['VN'])} · VO {fmt.miles(por_tipo['VO'])}"] if tipo == "todo" else []) + \
        ["ventas del CRM"]
    tarjeta = ng.bloque(origen, etiqueta="Ventas del mes" if tipo == "todo" else f"Ventas {tipo} del mes",
                        valor=fmt.miles(n), sub=" · ".join(partes), badge=None, tono="neutro")
    bloque = ng.bloque(origen, grupos=grupos, total=_celda(*suma(todas, actual)),
                       total_anterior=_celda(*suma(todas, anterior)), dos=len(tipos) > 1,
                       mes_actual=f"{fmt.MESES[actual.month - 1]} (hasta el {fmt.fecha_corta(hoy)})",
                       mes_anterior=fmt.MESES[anterior.month - 1], nota=NOTA_CRM,
                       titulo="Ventas del CRM", calculado=fmt.fecha_corta(dia))
    return tarjeta, bloque


def diferencia(precio: int | None, mediana: int | None) -> float | None:
    """Precio frente a la mediana de su segmento, en %."""
    if precio is None or not mediana:
        return None
    return (precio - mediana) / mediana * 100


def recientes(pg: psycopg.Connection, conc, origen: str | None, tipo: str, hoy: date) -> dict:
    """Las salidas de las últimas 4 semanas (hasta hoy), un coche por fila, de la más reciente a la más antigua."""
    if origen is None:
        return ng.bloque(None, hay=False)
    desde = hoy - timedelta(weeks=SEMANAS)
    cols = ("dia", "tipo", "id_anuncio", "modelo", "acabado", "motor", "anio", "km", "color", "precio_cents",
            "dias_publicado", "dias_cota", "provisional", "nivel", "n_comparables", "mediana_cents")
    filas = []
    for v in (dict(zip(cols, f)) for f in pg.execute(
            f"""SELECT {', '.join(cols)} FROM {TABLA} WHERE origen = %s AND dia > %s AND dia <= %s
                AND tipo = ANY(%s) ORDER BY dia DESC, tipo, modelo, id_anuncio""",
            (origen, desde, hoy, list(_tipos(tipo))))):
        nombre = f"{v['modelo']} {v['acabado']}" if v["acabado"] else f"{v['modelo']} (acabado sin clasificar)"
        detalle = " · ".join(str(x) for x in (v["motor"], v["anio"], v["km"], v["color"]) if x)
        filas.append({
            "nombre": nombre, "detalle": detalle, "id": v["id_anuncio"], "tipo": v["tipo"],
            "fecha": fmt.fecha_corta(v["dia"]), "provisional": v["provisional"],
            "precio": fmt.euros(v["precio_cents"]), "dias": pr.dias_texto(v["dias_publicado"], v["dias_cota"]),
            "mediana": fmt.euros(v["mediana_cents"]),
            "diferencia": ng.signo_pct(diferencia(v["precio_cents"], v["mediana_cents"])),
            "nivel": pr.nivel_texto(v["nivel"], conc) if v["nivel"] else "Sin comparables suficientes",
        })
    n_prov = sum(f["provisional"] for f in filas)
    return ng.bloque(origen, filas=filas, nota=NOTA, desde=fmt.fecha_corta(desde + timedelta(days=1)),
                     hasta=fmt.fecha_corta(hoy),
                     recuento=f"{fmt.miles(len(filas))} coche{'s' if len(filas) != 1 else ''}"
                     + (f", {fmt.miles(n_prov)} provisional{'es' if n_prov != 1 else ''}" if n_prov else ""))
