"""Segmentos de cada modelo para el detalle de Mercado (tabla panel_mercado_segmento).

Se calculan en el precálculo nocturno (analitica/mercado.py), para cada día válido, tipo y zona,
solo en los niveles que muestra el panel (nunca el cruce de todas las dimensiones a la vez):

  modelo · modelo+acabado · modelo+acabado+motor
  VO, la matriz antigüedad×km de: modelo · modelo+acabado · modelo+combustible · modelo+motor ·
      modelo+acabado+combustible · modelo+acabado+motor (los filtros de la matriz; motor implica
      combustible, así que no hace falta el cruce de los tres)
  color:   modelo+color (vacío mientras la web no publique el color)

Combustible: el grupo del filtro (Gasolina, Diésel, Híbrido, Eléctrico) según config/versiones.yaml;
se guarda en el nivel motor y en los niveles de la matriz con combustible o motor. El motor se
identifica por su combustible y su texto («eTSI 150 CV · Gasolina · Automático»).

Por segmento: anuncios, propios, precio p25/p50/p75 de los que tienen precio (interpolación lineal,
como percentile_cont de Postgres) y días publicado (mediana inferior con la cota «≥ N», la misma
regla que la tabla por modelo).

Antigüedad: años cumplidos desde la primera matriculación hasta el día de la captura. Km: el del
anuncio. Los tramos de los dos salen de config/concesionario.yaml (segmentos), nunca del código; un
anuncio sin matriculación o sin km no entra en la matriz (sí en los demás niveles).

Acabado y motor: analitica/versiones.py con el catálogo config/versiones.yaml. Los textos cuyo
acabado no está en el catálogo se guardan en panel_mercado_sin_clasificar (anuncios en España).
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from datetime import date

from analitica.versiones import Catalogo

NIVELES_MATRIZ = ("antiguedad_km", "acabado_antiguedad_km", "combustible_antiguedad_km", "motor_antiguedad_km",
                  "acabado_combustible_antiguedad_km", "acabado_motor_antiguedad_km")


def anios_cumplidos(desde: date | None, hasta: date) -> int | None:
    """Años cumplidos de `desde` a `hasta` (un 29 de febrero se cumple el 1 de marzo). None si no hay
    fecha o si es posterior a `hasta`."""
    if desde is None or desde > hasta:
        return None
    return hasta.year - desde.year - ((hasta.month, hasta.day) < (desde.month, desde.day))


def tramo(valor: int | None, tramos: tuple[tuple[int | None, str], ...]) -> int | None:
    """Índice del tramo de `valor` («hasta» inclusive; el último, sin límite). None sin valor o negativo."""
    if valor is None or valor < 0 or not tramos:
        return None
    for i, (hasta, _) in enumerate(tramos):
        if hasta is None or valor <= hasta:
            return i
    return None


def percentil(orden: list[int], p: float) -> int | None:
    """Percentil continuo (como percentile_cont de Postgres) de una lista ya ordenada, redondeado
    al céntimo (la mitad, hacia arriba)."""
    if not orden:
        return None
    k = (len(orden) - 1) * p
    i = math.floor(k)
    valor = orden[i] if i + 1 >= len(orden) else orden[i] + (orden[i + 1] - orden[i]) * (k - i)
    return math.floor(valor + 0.5)


def _vacio() -> dict:
    return {"anuncios": 0, "propios": 0, "precios": [], "edades": []}


def calcular(h, dia: date, tipo: str, catalogo: Catalogo) -> tuple[list[dict], list[dict]]:
    """Filas de panel_mercado_segmento y panel_mercado_sin_clasificar de un día válido del tipo.
    `h` es el lector de hechos de analitica/mercado.py."""
    from analitica.mercado import dias_publicado   # evita la importación circular

    conc = h.conc
    con_matriz = tipo == "VO" and bool(conc.tramos_antiguedad) and bool(conc.tramos_km)
    grupos: dict[tuple, dict] = defaultdict(_vacio)
    sin_clasificar: Counter = Counter()
    for clave, f in h.filas(dia, tipo).items():
        v = catalogo.leer(tipo, f.modelo, f.version, f.combustible, f.potencia_cv)
        if not v.reconocido:
            sin_clasificar[(f.modelo, re.sub(r"\s+", " ", f.version or "").strip() or "(sin versión)")] += 1
        acabado = (v.acabado, v.reconocido)
        comb, motor = v.grupo_combustible, v.motor
        # (nivel, modelo, acabado, combustible, motor, antigüedad, km, color)
        claves = [("modelo", f.modelo, None, None, None, None, None, None),
                  ("acabado", f.modelo, acabado, None, None, None, None, None),
                  ("motor", f.modelo, acabado, comb, motor, None, None, None)]
        if con_matriz:
            ant = tramo(anios_cumplidos(f.matriculacion, dia), conc.tramos_antiguedad)
            km = tramo(f.km, conc.tramos_km)
            if ant is not None and km is not None:
                claves += [("antiguedad_km", f.modelo, None, None, None, ant, km, None),
                           ("acabado_antiguedad_km", f.modelo, acabado, None, None, ant, km, None),
                           ("combustible_antiguedad_km", f.modelo, None, comb, None, ant, km, None),
                           ("motor_antiguedad_km", f.modelo, None, comb, motor, ant, km, None),
                           ("acabado_combustible_antiguedad_km", f.modelo, acabado, comb, None, ant, km, None),
                           ("acabado_motor_antiguedad_km", f.modelo, acabado, comb, motor, ant, km, None)]
        if f.color:
            claves.append(("color", f.modelo, None, None, None, None, None, f.color))
        edad = ((dia - h.primero[clave]).days, h.alta_observada(clave))
        for zona in h.zonas(f.provincia):
            for k in claves:
                g = grupos[(zona,) + k]
                g["anuncios"] += 1
                g["propios"] += f.es_propio
                if f.precio_cents is not None:
                    g["precios"].append(f.precio_cents)
                g["edades"].append(edad)

    filas = []
    for (zona, nivel, modelo, acabado, comb, motor, ant, km, color), g in grupos.items():
        precios = sorted(g["precios"])
        med, cota = dias_publicado(g["edades"])
        filas.append({
            "dia": dia, "tipo": tipo, "zona": zona, "nivel": nivel, "modelo": modelo,
            "acabado": acabado and acabado[0], "acabado_reconocido": acabado and acabado[1], "combustible": comb,
            "motor": motor,
            "antiguedad": None if ant is None else conc.tramos_antiguedad[ant][1],
            "antiguedad_orden": ant, "km": None if km is None else conc.tramos_km[km][1], "km_orden": km,
            "color": color, "anuncios": g["anuncios"], "propios": g["propios"], "n_precio": len(precios),
            "precio_p25_cents": percentil(precios, 0.25), "precio_p50_cents": percentil(precios, 0.5),
            "precio_p75_cents": percentil(precios, 0.75), "dias_mediana": med, "dias_cota": cota,
        })
    otras = [{"dia": dia, "tipo": tipo, "modelo": m, "version": t, "anuncios": n}
             for (m, t), n in sin_clasificar.items()]
    return filas, otras


COLS_SEGMENTO = ["dia", "tipo", "zona", "nivel", "modelo", "acabado", "acabado_reconocido", "combustible", "motor",
                 "antiguedad", "antiguedad_orden", "km", "km_orden", "color", "anuncios", "propios", "n_precio",
                 "precio_p25_cents", "precio_p50_cents", "precio_p75_cents", "dias_mediana", "dias_cota"]
COLS_SIN_CLASIFICAR = ["dia", "tipo", "modelo", "version", "anuncios"]
