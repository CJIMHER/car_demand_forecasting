"""Bloque «Precio de nuestros VO» de Ventas y Dirección (tabla panel_precio_vo del contrato).

La recomendación llega ya calculada (analitica/precio.py, en el precálculo de Mercado): aquí solo se ordena,
se filtra por modelo y se escribe. Es provisional: no es una probabilidad de venta.
"""

from __future__ import annotations

from urllib.parse import quote

import psycopg

from ingesta.concesionario import Concesionario
from panel import formato as fmt
from panel import negocio as ng

TABLA = "panel_precio_vo"
AVISO = "Recomendación provisional basada en el mercado actual; no es una probabilidad de venta."
PARAMETRO = "modelo_vo"
COLS = ("fuente", "id_anuncio", "modelo", "version", "acabado", "motor", "antiguedad", "km", "precio_cents",
        "dias_publicado", "dias_cota", "nivel", "n_comparables", "p25_cents", "mediana_cents", "p75_cents", "regla",
        "orden", "recomendacion")
CLASE = {1: "bajar", 2: "revisar", 3: "barato", 4: "linea"}


def nivel_texto(nivel: str | None, conc: Concesionario) -> str:
    return {None: "Sin comparables suficientes",
            "segmento": "Mismo segmento, zona cercana",
            "sin_km": "Sin tramo de km, zona cercana",
            "sin_antiguedad": "Sin tramos de antigüedad ni km, zona cercana",
            "comunidad": f"Sin tramos, zona cercana y {conc.comunidad}"}[nivel]


def dias_texto(dias: int, cota: bool) -> str:
    return f"al menos {fmt.miles(dias)}" if cota else fmt.miles(dias)


def _fila(v: dict, conc: Concesionario) -> dict:
    nombre = f"{v['modelo']} {v['acabado']}" if v["acabado"] else f"{v['modelo']} (acabado sin clasificar)"
    detalle = " · ".join(x for x in (v["motor"], v["antiguedad"], v["km"]) if x)
    return {
        "nombre": nombre, "modelo": v["modelo"], "detalle": detalle, "id": v["id_anuncio"],
        "precio": fmt.euros(v["precio_cents"]), "mediana": fmt.euros(v["mediana_cents"]),
        "rango": (f"{fmt.euros(v['p25_cents'])} – {fmt.euros(v['p75_cents'])}"
                  if v["p25_cents"] is not None else fmt.GUION),
        "n": fmt.miles(v["n_comparables"]), "nivel": nivel_texto(v["nivel"], conc),
        "dias": dias_texto(v["dias_publicado"], v["dias_cota"]), "recomendacion": v["recomendacion"],
        "regla": v["regla"], "clase": CLASE[v["orden"]],
    }


def bloque(pg: psycopg.Connection, conc: Concesionario, origen: str | None, modelo: str | None, base_url: str,
           objetivo: str) -> dict:
    """Tabla del último día con ese origen, ordenada por la regla (1 y 2, 3, 4, el resto) y, dentro, por días
    publicado de más a menos. `base_url` lleva ya los demás parámetros de la vista (…?tipo=VO)."""
    dia = ng.ultimo_dia(pg, TABLA, origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    todos = [dict(zip(COLS, f)) for f in pg.execute(
        f"""SELECT {', '.join(COLS)} FROM {TABLA} WHERE origen = %s AND dia = %s
            ORDER BY orden, dias_publicado DESC, modelo, id_anuncio""", (origen, dia))]
    modelos = sorted({v["modelo"] for v in todos})
    modelo = modelo if modelo in modelos else None
    filtro = [{"id": "", "etiqueta": "Todos", "activa": modelo is None, "url": base_url, "objetivo": objetivo}] + [
        {"id": m, "etiqueta": m, "activa": m == modelo, "url": f"{base_url}&{PARAMETRO}={quote(m, safe='')}",
         "objetivo": objetivo} for m in modelos]
    filas = [_fila(v, conc) for v in todos if modelo is None or v["modelo"] == modelo]
    return ng.bloque(origen, filas=filas, filtro=filtro, modelo=modelo, aviso=AVISO, dia=fmt.fecha_corta(dia),
                     recuento=f"{fmt.miles(len(filas))} de {fmt.miles(len(todos))} coches")
