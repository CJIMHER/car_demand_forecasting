"""Precio provisional de nuestros VO (tabla panel_precio_vo, en el precálculo de Mercado).

Hasta tener un modelo de probabilidad de venta, cada coche VO nuestro recibe una recomendación sencilla de
precio con lo que ya hay en Mercado. Es provisional: la sustituirá el modelo.

- Nuestros VO: los anuncios propios (es_propio) del scraper VO en el día del precálculo.
- Días publicado: desde el primer día en que vimos el anuncio (fuente + id). Si no lo vimos aparecer (el
  primer día de datos o tras un hueco de capturas), es una cota inferior: «al menos N días».
- Segmento de comparación: mismo modelo, acabado, motor, tramo de antigüedad y tramo de km, en la zona
  cercana, sin nuestros coches y solo con precio. p25, mediana y p75 como en Mercado (percentile_cont).
- Con menos de `min_comparables`, se amplía por este orden y se dice en la recomendación: sin el tramo de
  km, sin el de antigüedad, y la zona cercana más la comunidad. Con menos de `min_comparables_amplio` en el
  nivel más amplio: «sin comparables suficientes», sin recomendación.
- Reglas, en orden (la primera que se cumple): muy por encima (precio > p75 y días ≥ dias_muy_por_encima),
  por encima y parado (precio > mediana y días ≥ dias_por_encima), en precio pero no sale (precio ≤ mediana
  y días ≥ dias_en_precio), barato (precio < p25) y, si no, en línea.

Los umbrales salen de config/concesionario.yaml (precio_provisional), nunca del código.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path

import yaml

from analitica.segmentos import anios_cumplidos, percentil, tramo
from ingesta.concesionario import Concesionario, ErrorConfiguracion, ruta as ruta_concesionario

CLAVES = ("min_comparables", "min_comparables_amplio", "dias_muy_por_encima", "dias_por_encima", "dias_en_precio")

# (nivel, zona, con tramo de antigüedad, con tramo de km), de más estrecho a más amplio
NIVELES = (("segmento", "cercana", True, True),
           ("sin_km", "cercana", True, False),
           ("sin_antiguedad", "cercana", False, False),
           ("comunidad", "comunidad", False, False))

# Orden en la tabla: primero las reglas 1 y 2, luego la 3, luego la 4 y el resto al final
ORDEN = {"muy_por_encima": 1, "por_encima_parado": 1, "en_precio_no_sale": 2, "barato": 3, "en_linea": 4,
         "sin_comparables": 4, "sin_precio": 4}

COLS = ["dia", "origen", "fuente", "id_anuncio", "modelo", "version", "acabado", "motor", "antiguedad", "km",
        "precio_cents", "dias_publicado", "dias_cota", "nivel", "zona", "n_comparables", "p25_cents",
        "mediana_cents", "p75_cents", "regla", "orden", "recomendacion"]


@dataclass(frozen=True)
class Reglas:
    min_comparables: int
    min_comparables_amplio: int
    dias_muy_por_encima: int
    dias_por_encima: int
    dias_en_precio: int


def cargar_reglas(fichero: Path | None = None) -> Reglas | None:
    """precio_provisional de config/concesionario.yaml. None si no está (no se calcula); un valor que falta o
    no es un entero positivo es un error."""
    cfg = (yaml.safe_load((fichero or ruta_concesionario()).read_text(encoding="utf-8")) or {}).get(
        "precio_provisional")
    if cfg is None:
        return None
    valores = {}
    for clave in CLAVES:
        v = cfg.get(clave) if isinstance(cfg, dict) else None
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ErrorConfiguracion(f"config/concesionario.yaml: precio_provisional.{clave} tiene que ser un "
                                     "entero positivo")
        valores[clave] = v
    r = Reglas(**valores)
    if r.min_comparables_amplio > r.min_comparables:
        raise ErrorConfiguracion("config/concesionario.yaml: precio_provisional.min_comparables_amplio no puede ser "
                                 "mayor que min_comparables")
    return r


@dataclass(frozen=True)
class Coche:
    """Lo que hace falta de un anuncio para compararlo."""
    modelo: str
    acabado: str | None        # None: acabado sin clasificar
    motor: str | None          # None: sin dato de motor
    antiguedad: int | None     # índice del tramo
    km: int | None
    cercana: bool
    comunidad: bool
    precio_cents: int | None


@dataclass(frozen=True)
class Comparacion:
    nivel: str | None          # None: sin comparables suficientes
    zona: str | None
    n: int
    p25: int | None = None
    mediana: int | None = None
    p75: int | None = None


def comparar(nuestro: Coche, otros: list[Coche], reglas: Reglas) -> Comparacion:
    """El segmento más estrecho con al menos min_comparables; si ninguno llega, el más amplio si tiene al
    menos min_comparables_amplio. `otros`: anuncios que no son nuestros."""
    if nuestro.acabado is None or nuestro.motor is None:
        return Comparacion(None, None, 0)
    base = [o for o in otros if o.precio_cents is not None and o.modelo == nuestro.modelo
            and o.acabado == nuestro.acabado and o.motor == nuestro.motor]
    precios: list[int] = []
    for nivel, zona, con_ant, con_km in NIVELES:
        if (con_ant and nuestro.antiguedad is None) or (con_km and nuestro.km is None):
            continue                                    # sin ese dato, el nivel no se puede usar
        precios = sorted(o.precio_cents for o in base
                         if (o.cercana or (zona == "comunidad" and o.comunidad))
                         and (not con_ant or o.antiguedad == nuestro.antiguedad)
                         and (not con_km or o.km == nuestro.km))
        if len(precios) >= reglas.min_comparables or (nivel == NIVELES[-1][0]
                                                      and len(precios) >= reglas.min_comparables_amplio):
            return Comparacion(nivel, zona, len(precios), percentil(precios, 0.25), percentil(precios, 0.5),
                               percentil(precios, 0.75))
    return Comparacion(None, None, len(precios))


def decidir(precio: int | None, dias: int, c: Comparacion, reglas: Reglas) -> str:
    if precio is None:
        return "sin_precio"
    if c.nivel is None:
        return "sin_comparables"
    if precio > c.p75 and dias >= reglas.dias_muy_por_encima:
        return "muy_por_encima"
    if precio > c.mediana and dias >= reglas.dias_por_encima:
        return "por_encima_parado"
    if precio <= c.mediana and dias >= reglas.dias_en_precio:
        return "en_precio_no_sale"
    if precio < c.p25:
        return "barato"
    return "en_linea"


def euros(cents: int) -> str:
    """18.500 € (al euro, la mitad hacia arriba)."""
    return f"{(cents + 50) // 100:,}".replace(",", ".") + " €"


def texto(regla: str, c: Comparacion, nuestro: Coche, conc: Concesionario) -> str:
    if regla == "sin_precio":
        return "Sin precio publicado: sin recomendación"
    if regla == "sin_comparables":
        if nuestro.acabado is None:
            return "Sin comparables suficientes: acabado sin clasificar"
        if nuestro.motor is None:
            return "Sin comparables suficientes: sin dato de motor"
        return "Sin comparables suficientes"
    base = {"muy_por_encima": f"Muy por encima del mercado: bajar a la mediana ({euros(c.mediana)})",
            "por_encima_parado": f"Plantéate bajar a la mediana ({euros(c.mediana)})",
            "en_precio_no_sale": "El precio está en mercado: revisa fotos, anuncio y leads",
            "barato": f"Por debajo del mercado (p25: {euros(c.p25)}): si tiene interés, hay margen",
            "en_linea": "En línea con el mercado"}[regla]
    return base + {"segmento": "",
                   "sin_km": ". Comparado sin el tramo de km",
                   "sin_antiguedad": ". Comparado sin los tramos de antigüedad ni de km",
                   "comunidad": f". Comparado sin tramos y ampliado a {conc.comunidad}"}[c.nivel]


def calcular(h, dia: date, catalogo, reglas: Reglas) -> list[dict]:
    """Filas de panel_precio_vo (origen real) de un día válido de VO. `h` es el lector de hechos de
    analitica/mercado.py."""
    conc: Concesionario = h.conc
    nuestros, otros = [], []
    for clave, f in h.filas(dia, "VO").items():
        v = catalogo.leer("VO", f.modelo, f.version, f.combustible, f.potencia_cv)
        zonas = h.zonas(f.provincia)
        c = Coche(f.modelo, v.acabado if v.reconocido else None,
                  None if v.motor.startswith("Sin dato de motor") else v.motor,
                  tramo(anios_cumplidos(f.matriculacion, dia), conc.tramos_antiguedad), tramo(f.km, conc.tramos_km),
                  "cercana" in zonas, "comunidad" in zonas, f.precio_cents)
        (nuestros if f.es_propio else otros).append((clave, f, c))
    comparables = [c for _, _, c in otros]
    filas = []
    for (fuente, id_anuncio), f, c in nuestros:
        dias = (dia - h.primero[(fuente, id_anuncio)]).days
        comp = comparar(c, comparables, reglas)
        regla = decidir(c.precio_cents, dias, comp, reglas)
        filas.append({
            "dia": dia, "origen": "real", "fuente": fuente, "id_anuncio": id_anuncio, "modelo": f.modelo,
            "version": f.version, "acabado": c.acabado, "motor": c.motor,
            "antiguedad": None if c.antiguedad is None else conc.tramos_antiguedad[c.antiguedad][1],
            "km": None if c.km is None else conc.tramos_km[c.km][1], "precio_cents": c.precio_cents,
            "dias_publicado": dias, "dias_cota": not h.alta_observada((fuente, id_anuncio)),
            "nivel": comp.nivel, "zona": comp.zona, "n_comparables": comp.n, "p25_cents": comp.p25,
            "mediana_cents": comp.mediana, "p75_cents": comp.p75, "regla": regla, "orden": ORDEN[regla],
            "recomendacion": texto(regla, comp, c, conc),
        })
    return filas
