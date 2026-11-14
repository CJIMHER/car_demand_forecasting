"""Provincia a partir del código postal español.

Las dos primeras cifras del código postal son el código INE de la provincia (01–52).
"""

from __future__ import annotations

import re

PROVINCIAS = {
    "01": "Araba/Álava", "02": "Albacete", "03": "Alicante/Alacant", "04": "Almería",
    "05": "Ávila", "06": "Badajoz", "07": "Illes Balears", "08": "Barcelona",
    "09": "Burgos", "10": "Cáceres", "11": "Cádiz", "12": "Castellón/Castelló",
    "13": "Ciudad Real", "14": "Córdoba", "15": "A Coruña", "16": "Cuenca",
    "17": "Girona", "18": "Granada", "19": "Guadalajara", "20": "Gipuzkoa",
    "21": "Huelva", "22": "Huesca", "23": "Jaén", "24": "León",
    "25": "Lleida", "26": "La Rioja", "27": "Lugo", "28": "Madrid",
    "29": "Málaga", "30": "Murcia", "31": "Navarra", "32": "Ourense",
    "33": "Asturias", "34": "Palencia", "35": "Las Palmas", "36": "Pontevedra",
    "37": "Salamanca", "38": "Santa Cruz de Tenerife", "39": "Cantabria", "40": "Segovia",
    "41": "Sevilla", "42": "Soria", "43": "Tarragona", "44": "Teruel",
    "45": "Toledo", "46": "Valencia/València", "47": "Valladolid", "48": "Bizkaia",
    "49": "Zamora", "50": "Zaragoza", "51": "Ceuta", "52": "Melilla",
}


def normalizar_cp(valor) -> str | None:
    """Código postal de 5 cifras, o None si no lo es.

    La web publica a veces espacios ("46910 ") y podría perder el cero inicial ("8720").
    """
    if valor is None:
        return None
    texto = re.sub(r"\s+", "", str(valor))
    if re.fullmatch(r"\d{4}", texto):
        texto = "0" + texto
    if not re.fullmatch(r"\d{5}", texto):
        return None
    return texto


def provincia(cp) -> tuple[str | None, str | None]:
    """(código INE, nombre) de la provincia del código postal; (None, None) si no se reconoce."""
    cp = normalizar_cp(cp)
    if cp is None or cp[:2] not in PROVINCIAS:
        return None, None
    return cp[:2], PROVINCIAS[cp[:2]]
