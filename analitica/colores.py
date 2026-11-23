"""Familia de color y acabado de pintura de un texto de color exterior, con config/colores.yaml.

El texto se guarda tal cual (tabla color_anuncio, ingesta/colores.py); la familia se calcula aquí,
en el precálculo, para poder cambiar las reglas sin tocar los datos. Sin distinguir mayúsculas ni
tildes; una palabra clave casa con las palabras del texto que empiezan por ella; si casan varias
familias, gana la que aparece antes en el texto. La ruta del fichero se puede cambiar con CDF_COLORES.
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent


class ErrorColores(Exception):
    pass


@dataclass(frozen=True)
class Color:
    familia: str                 # una de las familias o la de «sin especificar»
    reconocida: bool             # False: «sin especificar»
    acabado: str | None          # metalizado, perla, mate o None


def normalizar(texto: str) -> list[str]:
    """Palabras del texto en minúsculas y sin tildes («Gris Grisáceo (metalizado)» → gris, grisaceo, metalizado)."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFD", texto) if unicodedata.category(c) != "Mn")
    return re.findall(r"[a-z0-9]+", sin_tildes.casefold())


class Clasificador:
    def __init__(self, cfg: dict):
        familias = cfg.get("familias") or []
        if not familias:
            raise ErrorColores("config/colores.yaml no tiene familias")
        self.familias: list[tuple[str, tuple[str, ...]]] = []
        vistas: dict[str, str] = {}
        for f in familias:
            nombre = str(f["nombre"]).strip()
            palabras = tuple(w for p in f.get("palabras") or [] for w in normalizar(str(p)))
            if not nombre or not palabras:
                raise ErrorColores(f"Familia sin nombre o sin palabras en config/colores.yaml: {f!r}")
            for p in palabras:
                if p in vistas:
                    raise ErrorColores(f"La palabra «{p}» está en {vistas[p]} y en {nombre}")
                vistas[p] = nombre
            self.familias.append((nombre, palabras))
        self.sin_especificar = str(cfg.get("sin_especificar") or "Sin especificar")
        self.acabados = {str(k): frozenset(w for p in v or [] for w in normalizar(str(p)))
                         for k, v in (cfg.get("acabados_pintura") or {}).items()}

    @property
    def nombres(self) -> list[str]:
        """Familias en el orden de la configuración, con «sin especificar» al final."""
        return [n for n, _ in self.familias] + [self.sin_especificar]

    @lru_cache(maxsize=4096)
    def clasificar(self, texto: str) -> Color:
        palabras = normalizar(texto or "")
        familia = None
        for palabra in palabras:          # la primera palabra del texto que casa con alguna familia
            familia = next((n for n, claves in self.familias if any(palabra.startswith(c) for c in claves)), None)
            if familia:
                break
        acabado = next((a for a, claves in self.acabados.items() if claves & set(palabras)), None)
        return Color(familia or self.sin_especificar, familia is not None, acabado)


def ruta() -> Path:
    return Path(os.environ.get("CDF_COLORES", RAIZ / "config" / "colores.yaml"))


def cargar(fichero: Path | None = None) -> Clasificador:
    return Clasificador(yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {})
