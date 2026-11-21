"""Lectura del texto de versión de un anuncio: acabado y motor (catálogo en config/versiones.yaml).

El acabado va dentro del texto de versión y la web lo escribe distinto en VO y en VN:
  VO  «<marca> T-Roc ``Más`` 1.5 TSI 110 kW (150 CV) DSG»
  VN  «T-Roc "Más"  1.5 eTSI 85 kW (116 CV)  Automático DSG 7 vel.»
Las dos formas dan el mismo acabado («Más»). Un texto cuyo acabado no está en el catálogo va a
«Sin clasificar»: nunca se adivina. El motor es combustible + potencia en CV (del campo power_cv de
la web, nunca del texto) + tecnología (TSI, eTSI, TDI…) y el cambio cuando el texto lo dice.

Nada propio de una marca o un concesionario está aquí: la marca sale de config/concesionario.yaml
y los acabados y tecnologías, de config/versiones.yaml (ruta cambiable con CDF_VERSIONES).
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent

AUTOMATICO = "Automático"
MANUAL = "Manual"
COMILLAS = re.compile(r"``|''|[\"“”«»„‟]")
# El motor empieza en la cilindrada («1.5», «2,0») o en la potencia («110 kW», no «77 kWh»)
INICIO_MOTOR = re.compile(r"(?<![\w.])\d[.,]\d(?![\w.])|\b\d+ ?kw\b")


class ErrorCatalogo(Exception):
    pass


def ruta() -> Path:
    return Path(os.environ.get("CDF_VERSIONES", RAIZ / "config" / "versiones.yaml"))


def plegar(texto: str) -> str:
    """Para comparar: sin tildes, en minúsculas, sin comillas, el guion como espacio y sin espacios sobrantes."""
    t = COMILLAS.sub(" ", texto)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c)).casefold()
    t = t.replace("-", " ").replace("–", " ")
    return re.sub(r"\s+", " ", t).strip()


@dataclass(frozen=True)
class Version:
    acabado: str
    reconocido: bool
    tecnologia: str | None
    cv: int | None
    combustible: str | None
    cambio: str | None
    grupo_combustible: str = ""   # Gasolina, Diésel, Híbrido, Eléctrico… (filtro de la matriz de precios)

    @property
    def motor(self) -> str:
        """«eTSI 150 CV · Gasolina · Automático»; sin tecnología ni CV, «Sin dato de motor» con el
        combustible si lo hay («Sin dato de motor · Gasolina»)."""
        partes = []
        cabeza = " ".join(p for p in (self.tecnologia, f"{self.cv} CV" if self.cv else None) if p)
        if cabeza:
            partes.append(cabeza)
        if self.combustible and plegar(self.combustible) != plegar(self.tecnologia or ""):
            partes.append(self.combustible)
        if self.cambio:
            partes.append(self.cambio)
        if not cabeza:
            return " · ".join(["Sin dato de motor"] + ([self.combustible] if self.combustible else []))
        return " · ".join(partes)


class Catalogo:
    def __init__(self, cfg: dict, marca: str | None = None):
        self.marca = marca
        self.basico = str(cfg.get("basico") or "Básico")
        self.sin_clasificar = str(cfg.get("sin_clasificar") or "Sin clasificar")
        self.ignorar = re.compile(r"\b(?:" + "|".join(cfg.get("ignorar") or ["(?!)"]) + r")\b")
        self.comunes = self._acabados(cfg.get("comunes"))
        self.modelos: dict[str, tuple[list[str], dict[str, str]]] = {}
        for modelo, datos in (cfg.get("modelos") or {}).items():
            datos = datos or {}
            nombres = [str(modelo)] + [str(n) for n in datos.get("en_texto") or []]
            self.modelos[plegar(str(modelo))] = (
                sorted({plegar(n) for n in nombres}, key=len, reverse=True),
                {**self.comunes, **self._acabados(datos.get("acabados"))})
        self.tecnologias = [(str(t["nombre"]), [re.compile(p) for p in t.get("patrones") or []])
                            for t in cfg.get("tecnologias") or []]
        self.por_combustible = {plegar(k): str(v) for k, v in (cfg.get("por_combustible") or {}).items()}
        cambio = cfg.get("cambio") or {}
        self.automatico = [re.compile(p) for p in cambio.get("automatico") or []]
        self.manual = [re.compile(p) for p in cambio.get("manual") or []]
        self.manual_vn = [re.compile(p) for p in cambio.get("manual_vn") or []]
        self._grupo: dict[str, str] = {}
        for g in cfg.get("combustibles") or []:
            nombre = str(g["nombre"])
            for forma in [nombre] + [str(f) for f in g.get("formas") or []]:
                clave = plegar(forma)
                if self._grupo.get(clave, nombre) != nombre:
                    raise ErrorCatalogo(f"config/versiones.yaml: el combustible «{forma}» está en dos grupos")
                self._grupo[clave] = nombre
        self.sin_combustible = str(cfg.get("sin_combustible") or "Sin dato de combustible")
        self._cache: dict[tuple, Version] = {}

    @staticmethod
    def _acabados(lista) -> dict[str, str]:
        """{forma plegada: nombre a mostrar}, con sus sinónimos."""
        salida = {}
        for a in lista or []:
            if isinstance(a, str):
                a = {"nombre": a}
            nombre = str(a["nombre"])
            for forma in [nombre] + [str(s) for s in a.get("sinonimos") or []]:
                clave = plegar(forma)
                if clave in salida and salida[clave] != nombre:
                    raise ErrorCatalogo(f"config/versiones.yaml: «{forma}» es a la vez {salida[clave]} y {nombre}")
                salida[clave] = nombre
        return salida

    # ------------------------------------------------------------------ lectura

    def leer(self, tipo: str, modelo: str, version: str | None, combustible: str | None = None,
             cv: int | None = None) -> Version:
        clave = (tipo, modelo, version, combustible, cv)
        if clave not in self._cache:
            texto = plegar(version or "")
            acabado, reconocido = self._acabado(modelo, texto)
            self._cache[clave] = Version(acabado, reconocido, self._tecnologia(texto, combustible), cv,
                                         _limpio(combustible), self._cambio(tipo, texto),
                                         self.grupo_combustible(combustible))
        return self._cache[clave]

    def grupo_combustible(self, combustible: str | None) -> str:
        """Grupo del filtro de combustible. Un combustible fuera del catálogo se queda tal cual."""
        limpio = _limpio(combustible)
        if limpio is None:
            return self.sin_combustible
        return self._grupo.get(plegar(limpio), limpio)

    def _sin_prefijo(self, texto: str, prefijos: list[str]) -> tuple[str, bool]:
        for p in prefijos:
            if p and (texto == p or texto.startswith(p + " ")):
                return texto[len(p):].strip(), True
        return texto, False

    def _acabado(self, modelo: str, texto: str) -> tuple[str, bool]:
        nombres, acabados = self.modelos.get(plegar(modelo), ([plegar(modelo)], self.comunes))
        if self.marca:
            marca = plegar(self.marca)
            while texto.startswith(marca + " "):
                texto = texto[len(marca):].strip()
        texto, con_modelo = self._sin_prefijo(texto, nombres)
        if not con_modelo:
            return self.sin_clasificar, False
        m = INICIO_MOTOR.search(texto)
        if not m:
            return self.sin_clasificar, False
        tramo = texto[:m.start()].strip()
        tramo, repetido = self._sin_prefijo(tramo, nombres)
        tramo = re.sub(r"\s+", " ", self.ignorar.sub(" ", tramo)).strip()
        if repetido:   # «T-Roc T-Roc 1.5 eTSI…»: el modelo repetido y nada más es el acabado básico
            return (self.basico, True) if not tramo else (self.sin_clasificar, False)
        if tramo in acabados:
            return acabados[tramo], True
        return self.sin_clasificar, False

    def _tecnologia(self, texto: str, combustible: str | None) -> str | None:
        for nombre, patrones in self.tecnologias:
            if any(p.search(texto) for p in patrones):
                return nombre
        return self.por_combustible.get(plegar(combustible or ""))

    def _cambio(self, tipo: str, texto: str) -> str | None:
        texto = texto.replace(".", " ")
        if any(p.search(texto) for p in self.automatico):
            return AUTOMATICO
        if any(p.search(texto) for p in self.manual):
            return MANUAL
        if tipo == "VN" and any(p.search(texto) for p in self.manual_vn):
            return MANUAL
        return None


def _limpio(valor: str | None) -> str | None:
    if valor is None:
        return None
    texto = re.sub(r"\s+", " ", str(valor)).strip()
    return texto or None


def cargar(marca: str | None = None, fichero: Path | None = None) -> Catalogo:
    try:
        cfg = yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {}
    except FileNotFoundError as exc:
        raise ErrorCatalogo(f"No existe el catálogo de versiones: {exc.filename}") from None
    return Catalogo(cfg, marca)
