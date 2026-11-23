"""Tráfico de la web (GA4) por canal, con las reglas de config/canales.yaml.

- Valores reservados de GA4 ((direct), (not set)…): no son campañas.
- Canal de cada fila por su fuente y su medio (Meta, Google Ads, portales, correo, búsqueda orgánica,
  directo, referencia); si no casa ninguna regla, la agrupación de canales por defecto de GA4 como
  respaldo y, si tampoco, «otros».
- Tipo de stock (VN, VO) por la página de destino, si sus palabras lo dicen.

ingesta/campanas.py lo usa al rehacer las tablas del panel tras cada descarga de GA4: con ello
escribe panel_trafico_canal_dia (Marketing), panel_trafico_otros y panel_campanas_google_ads
(«Estado de las fuentes»), y decide qué filas de GA4 se intentan clasificar como campaña de Meta.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from ingesta.campanas import RAIZ, plegar

META, OTROS = "meta", "otros"
CONDICIONES = ("fuente", "medio", "fuente_palabra", "medio_palabra", "fuente_contiene")


class ErrorCanales(Exception):
    pass


def palabras(texto: str | None) -> set[str]:
    return {p for p in re.split(r"[^a-z0-9]+", plegar(texto)) if p}


@dataclass(frozen=True)
class Canales:
    reservados: frozenset[str]
    reglas: tuple[tuple[str, tuple[dict[str, frozenset[str]], ...]], ...]   # (canal, condiciones)
    nombres: dict[str, str]                                                  # canal → nombre en el panel
    respaldo: dict[str, str]                                                 # agrupación de GA4 → canal
    paginas: dict[str, frozenset[str]]                                       # VN / VO → palabras

    def reservado(self, campana: str | None) -> bool:
        """Un valor de GA4 que no es una campaña: de la lista, entre paréntesis o vacío."""
        t = plegar(campana)
        return not t or t in self.reservados or (t.startswith("(") and t.endswith(")"))

    @staticmethod
    def _casa(cond: dict[str, frozenset[str]], fuente: str, medio: str) -> bool:
        for clave, valores in cond.items():
            if clave == "fuente" and fuente not in valores:
                return False
            if clave == "medio" and medio not in valores:
                return False
            if clave == "fuente_palabra" and not palabras(fuente) & valores:
                return False
            if clave == "medio_palabra" and not palabras(medio) & valores:
                return False
            if clave == "fuente_contiene" and not any(v in fuente for v in valores):
                return False
        return True

    def canal(self, fuente: str | None, medio: str | None, canal_ga4: str | None = None) -> str:
        f, m = plegar(fuente), plegar(medio)
        for canal, condiciones in self.reglas:
            if any(self._casa(c, f, m) for c in condiciones):
                return canal
        return self.respaldo.get(plegar(canal_ga4), OTROS)

    def tipo(self, pagina: str | None) -> str | None:
        """VN o VO si las palabras de la página de destino lo dicen (solo uno de los dos)."""
        p = palabras(pagina)
        tipos = [t for t in ("VN", "VO") if p & self.paginas.get(t, frozenset())]
        return tipos[0] if len(tipos) == 1 else None

    def nombre(self, canal: str) -> str:
        return self.nombres.get(canal, "Otros")


def ruta() -> Path:
    return Path(os.environ.get("CDF_CANALES", RAIZ / "config" / "canales.yaml"))


def _lista(valor, donde: str) -> frozenset[str]:
    if isinstance(valor, str):
        valor = [valor]
    if not isinstance(valor, list) or not valor:
        raise ErrorCanales(f"config/canales.yaml: {donde} debe ser una lista con algún valor")
    return frozenset(plegar(str(v)) for v in valor)


def cargar(fichero: Path | None = None) -> Canales:
    cfg = yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {}
    reglas, nombres = [], {OTROS: "Otros"}
    for i, r in enumerate(cfg.get("canales") or [], 1):
        donde = f"canal {i}"
        if not isinstance(r, dict) or not r.get("canal") or not r.get("nombre"):
            raise ErrorCanales(f"config/canales.yaml: {donde} sin «canal» o sin «nombre»")
        canal = str(r["canal"])
        if canal in nombres:
            raise ErrorCanales(f"config/canales.yaml: el canal «{canal}» está repetido")
        condiciones = []
        for j, c in enumerate(r.get("si") or [], 1):
            if not isinstance(c, dict) or not c or set(c) - set(CONDICIONES):
                raise ErrorCanales(f"config/canales.yaml: {donde}, condición {j}: claves válidas "
                                   f"{', '.join(CONDICIONES)}")
            condiciones.append({k: _lista(v, f"{donde}, {k}") for k, v in c.items()})
        if not condiciones:
            raise ErrorCanales(f"config/canales.yaml: {donde} («{canal}») sin condiciones «si»")
        reglas.append((canal, tuple(condiciones)))
        nombres[canal] = str(r["nombre"])
    if META not in nombres:
        raise ErrorCanales("config/canales.yaml: falta el canal «meta»")
    respaldo = {plegar(str(k)): str(v) for k, v in (cfg.get("respaldo_ga4") or {}).items()}
    if set(respaldo.values()) - set(nombres):
        raise ErrorCanales("config/canales.yaml: «respaldo_ga4» apunta a canales que no existen: "
                           + ", ".join(sorted(set(respaldo.values()) - set(nombres))))
    paginas = {t: _lista(v, f"paginas.{t}") for t, v in (cfg.get("paginas") or {}).items() if t in ("VN", "VO")}
    reservados = frozenset(plegar(str(v)) for v in cfg.get("valores_reservados") or [])
    if not reservados:
        raise ErrorCanales("config/canales.yaml: falta la lista «valores_reservados»")
    return Canales(reservados, tuple(reglas), nombres, respaldo, paginas)
