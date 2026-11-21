"""Anuncios y precio mediano de cada concesionario de la provincia propia, por modelo y por modelo +
acabado (tabla panel_mercado_competidor).

La rellena el precálculo (analitica/mercado.py) para cada día válido y tipo. La usa la alerta de
presión de un competidor (alertas/reglas.yaml): un concesionario que sube su stock de un modelo en 7
días o baja el precio mediano de un segmento (modelo + acabado).

- Solo la provincia del concesionario propio y solo anuncios con concesionario (dealer_id).
- El acabado sale del catálogo (analitica/versiones.py); los textos sin acabado reconocido cuentan en
  el modelo, no en un segmento (nunca se adivina).
- Precio mediano: el de los anuncios con precio (mediana estadística, como en panel_mercado_modelo).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date

from analitica.versiones import Catalogo

COLS = ["dia", "tipo", "dealer_id", "concesionario", "es_propio", "modelo", "acabado", "anuncios", "n_precio",
        "precio_mediano_cents"]


def calcular(h, dia: date, tipo: str, catalogo: Catalogo) -> list[dict]:
    from analitica.mercado import precio_mediano   # evita la importación circular

    grupos: dict[tuple, dict] = defaultdict(lambda: {"anuncios": 0, "precios": [], "propio": False})
    for f in h.filas(dia, tipo).values():
        if not f.dealer_id or f.provincia != h.conc.provincia_codigo:
            continue
        v = catalogo.leer(tipo, f.modelo, f.version, f.combustible, f.potencia_cv)
        claves = [(f.dealer_id, f.modelo, None)]
        if v.reconocido:
            claves.append((f.dealer_id, f.modelo, v.acabado))
        for k in claves:
            g = grupos[k]
            g["anuncios"] += 1
            g["propio"] = g["propio"] or f.es_propio
            if f.precio_cents is not None:
                g["precios"].append(f.precio_cents)
    return [{
        "dia": dia, "tipo": tipo, "dealer_id": dealer, "concesionario": h.nombre_concesionario(dealer, dia),
        "es_propio": g["propio"], "modelo": modelo, "acabado": acabado, "anuncios": g["anuncios"],
        "n_precio": len(g["precios"]), "precio_mediano_cents": precio_mediano(g["precios"]),
    } for (dealer, modelo, acabado), g in sorted(grupos.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or ""))]
