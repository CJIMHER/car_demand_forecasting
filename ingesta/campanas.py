"""Campañas: regla de cada una (modelo y ámbito) y unión de Meta, GA4 y Walcu por utm_campaign.

La regla de unión y las reglas de cada campaña están documentadas en config/campanas.yaml. Este
módulo las aplica y recalcula panel_campanas_sin_regla (las campañas descargadas de Meta y GA4 que
no encajan en ninguna regla), que el panel lista en «Estado de las fuentes».
"""

from __future__ import annotations

import os
import re
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import psycopg
import yaml

RAIZ = Path(__file__).resolve().parent.parent
AMBITOS = ("VN", "VO", "general")
# Valores de GA4 que no son una campaña: tráfico directo, orgánico o de referencia
NO_CAMPANA = {"(not set)", "(direct)", "(organic)", "(referral)", "(none)", "(not provided)", ""}


class ErrorCampanas(Exception):
    pass


def normalizar(nombre: str | None) -> str:
    """«T-Roc Ocasión · Noviembre» → «t_roc_ocasion_noviembre»."""
    texto = unicodedata.normalize("NFKD", str(nombre or "")).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "_", texto).strip("_")


@dataclass(frozen=True)
class Regla:
    patron: re.Pattern
    modelo: str | None
    ambito: str


@dataclass(frozen=True)
class Campanas:
    reglas: tuple[Regla, ...]
    equivalencias: dict[str, str]
    meta_version: str

    def regla(self, nombre: str | None) -> Regla | None:
        n = normalizar(nombre)
        return next((r for r in self.reglas if r.patron.search(n)), None)

    def clave_union(self, utm_campaign: str | None) -> str:
        """Clave con que se unen un lead de Walcu (o una sesión de GA4) y una campaña de Meta."""
        n = normalizar(utm_campaign)
        return normalizar(self.equivalencias.get(n, n))


def ruta() -> Path:
    return Path(os.environ.get("CDF_CAMPANAS", RAIZ / "config" / "campanas.yaml"))


def cargar(fichero: Path | None = None) -> Campanas:
    cfg = yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {}
    reglas = []
    for i, r in enumerate(cfg.get("reglas") or [], 1):
        if not isinstance(r, dict) or not r.get("patron"):
            raise ErrorCampanas(f"config/campanas.yaml, regla {i}: falta «patron»")
        if r.get("ambito") not in AMBITOS:
            raise ErrorCampanas(f"config/campanas.yaml, regla {i}: «ambito» debe ser VN, VO o general")
        if r.get("ambito") != "general" and not r.get("modelo"):
            raise ErrorCampanas(f"config/campanas.yaml, regla {i}: una campaña de VN o VO necesita «modelo»")
        try:
            patron = re.compile(str(r["patron"]))
        except re.error as exc:
            raise ErrorCampanas(f"config/campanas.yaml, regla {i}: patrón no válido ({exc})") from None
        reglas.append(Regla(patron, r.get("modelo") or None, r["ambito"]))
    equivalencias = {normalizar(k): normalizar(v) for k, v in (cfg.get("equivalencias") or {}).items()}
    return Campanas(tuple(reglas), equivalencias, str((cfg.get("meta") or {}).get("version_api") or "v24.0"))


def actualizar_sin_regla(pg: psycopg.Connection, campanas: Campanas | None = None) -> int:
    """Recalcula panel_campanas_sin_regla con las campañas de Meta y GA4 sin regla. Devuelve cuántas."""
    campanas = campanas or cargar()
    meta = pg.execute(
        """SELECT campana_nombre, min(dia), max(dia), sum(gasto_cents) FROM campanas_meta_dia_vigente
           WHERE nivel = 'campana' GROUP BY campana_nombre""").fetchall()
    ga4 = pg.execute(
        """SELECT campana, min(dia), max(dia), sum(sesiones) FROM ga4_trafico_dia_vigente GROUP BY campana""").fetchall()
    filas = [("meta", n or "(sin nombre)", p, u, g, None) for n, p, u, g in meta if campanas.regla(n) is None]
    filas += [("ga4", c, p, u, None, s) for c, p, u, s in ga4
              if c not in NO_CAMPANA and campanas.regla(campanas.clave_union(c)) is None]
    with pg.transaction():
        pg.execute("DELETE FROM panel_campanas_sin_regla")
        with pg.cursor() as cur:
            cur.executemany("INSERT INTO panel_campanas_sin_regla (fuente, campana, primer_dia, ultimo_dia, gasto_cents, "
                            "sesiones) VALUES (%s, %s, %s, %s, %s, %s)", filas)
    return len(filas)
