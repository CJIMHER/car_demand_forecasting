"""Clasificación de las rutas de la web en GA4 con config/ga4_paginas.yaml: si cuentan (dominio y web
comercial), de qué marca son, de qué tipo de página, de qué tipo de stock (VN, VO) y, en las fichas, de qué
modelo. Lo usan make ga4-explorar y las señales semanales de intención (ga4_series.py).
"""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from ingesta.campanas import RAIZ, plegar

PROPIA, SIN_MARCA, OTRA = "propia", "sin_marca", "otra"
TIPOS_STOCK = ("VN", "VO")


class ErrorPaginas(Exception):
    pass


@dataclass(frozen=True)
class TipoPagina:
    nombre: str
    patron: re.Pattern
    tipo: str | None = None              # VN, VO o None
    ficha: bool = False
    requiere_modelo: bool = False


@dataclass(frozen=True)
class Pagina:
    ruta: str
    excluida: str | None                 # motivo: «dominio», «no comercial», «otra marca»; None si cuenta
    marca: str                           # propia, sin_marca u otra
    tipo_pagina: str | None              # None: sin tipo
    tipo: str | None                     # VN, VO o None
    ficha: bool
    modelo: str | None                   # solo en fichas de la marca propia

    @property
    def cuenta(self) -> bool:
        return self.excluida is None


@dataclass(frozen=True)
class Paginas:
    hosts: frozenset[str]
    excluir: tuple[re.Pattern, ...]
    propia: tuple[tuple[str, ...], ...]
    modelos_propios: tuple[tuple[str, tuple[str, ...]], ...]       # (nombre, palabras), las más largas antes
    otras: tuple[tuple[str, ...], ...]
    modelos_otras: tuple[tuple[str, ...], ...]
    sedes_otras: tuple[tuple[str, ...], ...]
    tipos: tuple[TipoPagina, ...]
    version: str

    def marca(self, palabras: list[str]) -> tuple[str, str | None]:
        """(marca, modelo propio que nombra la ruta)."""
        modelo = next((n for n, p in self.modelos_propios if _contiene(palabras, p)), None)
        if any(_contiene(palabras, p) for p in self.otras + self.sedes_otras):
            return OTRA, None
        if modelo or any(_contiene(palabras, p) for p in self.propia):
            return PROPIA, modelo
        if any(_contiene(palabras, p) for p in self.modelos_otras):
            return OTRA, None
        return SIN_MARCA, None

    def clasificar(self, ruta: str | None, host: str | None = None) -> Pagina:
        r = normalizar(ruta)
        if self.hosts and plegar(host) not in self.hosts:
            return Pagina(r, "dominio", SIN_MARCA, None, None, False, None)
        if any(p.search(r) for p in self.excluir):
            return Pagina(r, "no comercial", SIN_MARCA, None, None, False, None)
        palabras = _palabras(r)
        marca, modelo = self.marca(palabras)
        if marca == OTRA:
            return Pagina(r, "otra marca", OTRA, None, None, False, None)
        hay_modelo = modelo is not None or any(_contiene(palabras, p) for p in self.modelos_otras)
        for t in self.tipos:
            if t.patron.search(r) and (hay_modelo or not t.requiere_modelo):
                return Pagina(r, None, marca, t.nombre, t.tipo, t.ficha, modelo if t.ficha else None)
        return Pagina(r, None, marca, None, None, False, None)


def normalizar(ruta: str | None) -> str:
    """Solo la ruta, en minúsculas y sin tildes, sin parámetros ni la barra final."""
    r = plegar((ruta or "/").split("?", 1)[0].split("#", 1)[0]).replace(" ", "-")
    r = r if r.startswith("/") or r.startswith("(") else "/" + r
    return r.rstrip("/") or "/"


def _palabras(texto: str) -> list[str]:
    return [p for p in re.split(r"[^a-z0-9]+", texto) if p]


def _contiene(palabras: list[str], seguidas: tuple[str, ...]) -> bool:
    n = len(seguidas)
    return any(tuple(palabras[i:i + n]) == seguidas for i in range(len(palabras) - n + 1))


def ruta() -> Path:
    return Path(os.environ.get("CDF_GA4_PAGINAS", RAIZ / "config" / "ga4_paginas.yaml"))


def cargar(fichero: Path | None = None) -> Paginas:
    f = fichero or ruta()
    texto = f.read_text(encoding="utf-8")
    return _cargar(texto)


@lru_cache(maxsize=8)
def _cargar(texto: str) -> Paginas:
    cfg = yaml.safe_load(texto) or {}

    def error(que: str) -> ErrorPaginas:
        return ErrorPaginas(f"config/ga4_paginas.yaml: {que}")

    def regex(valor, donde: str) -> re.Pattern:
        try:
            return re.compile(str(valor))
        except re.error as exc:
            raise error(f"patrón de «{donde}» no válido: {exc}") from None

    def lista(v, donde: str, obligatoria: bool = False) -> list:
        if v is None and not obligatoria:
            return []
        if not isinstance(v, list) or (obligatoria and not v):
            raise error(f"«{donde}» debe ser una lista" + (" con algún valor" if obligatoria else ""))
        return v

    def secuencias(v, donde: str) -> tuple[tuple[str, ...], ...]:
        salida = []
        for x in lista(v, donde):
            p = tuple(_palabras(plegar(str(x))))
            if not p:
                raise error(f"«{donde}» tiene un valor vacío")
            salida.append(p)
        return tuple(sorted(salida, key=len, reverse=True))

    marcas = cfg.get("marcas") or {}
    if not isinstance(marcas, dict):
        raise error("«marcas» debe ser un diccionario")
    modelos = []
    mp = marcas.get("modelos_propios") or {}
    if not isinstance(mp, dict):
        raise error("«marcas.modelos_propios» debe ser un diccionario (modelo: [formas en la ruta])")
    for nombre, formas in mp.items():
        for p in secuencias(formas, f"marcas.modelos_propios.{nombre}"):
            modelos.append((str(nombre), p))
    modelos.sort(key=lambda x: len(x[1]), reverse=True)
    tipos = []
    for i, t in enumerate(lista(cfg.get("tipos"), "tipos", obligatoria=True), 1):
        if not isinstance(t, dict) or not str(t.get("nombre") or "").strip() or not t.get("patron"):
            raise error(f"el tipo {i} necesita «nombre» y «patron»")
        stock = t.get("tipo")
        if stock is not None and stock not in TIPOS_STOCK:
            raise error(f"el tipo de stock de «{t['nombre']}» debe ser VN, VO o nada")
        tipos.append(TipoPagina(str(t["nombre"]).strip(), regex(t["patron"], t["nombre"]), stock,
                                bool(t.get("ficha")), bool(t.get("requiere_modelo"))))
    return Paginas(
        hosts=frozenset(plegar(str(h)) for h in lista(cfg.get("hosts"), "hosts")),
        excluir=tuple(regex(p, "excluir") for p in lista(cfg.get("excluir"), "excluir")),
        propia=secuencias(marcas.get("propia"), "marcas.propia"), modelos_propios=tuple(modelos),
        otras=secuencias(marcas.get("otras"), "marcas.otras"),
        modelos_otras=secuencias(marcas.get("modelos_otras"), "marcas.modelos_otras"),
        sedes_otras=secuencias(marcas.get("sedes_otras"), "marcas.sedes_otras"),
        tipos=tuple(tipos), version=hashlib.sha256(texto.encode()).hexdigest()[:12])
