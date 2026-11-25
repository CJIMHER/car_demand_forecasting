"""Mensajes de acción para dirección, ventas y marketing: diagnóstico y acciones en lenguaje de negocio.

Traduce el resultado de los criterios de las campañas de Meta (alertas/criterios.py: reglas a, b, c, d, e, f y
subir presupuesto, con sus cifras) a situaciones de config/mensajes_marketing.yaml, sin umbrales, fórmulas ni
nombres de reglas. El detalle (cifras frente a umbrales) solo lo ve el rol técnico.

Sirve tanto para lo que se guardó como JSON (panel_campana_decision.reglas, panel_recomendacion_modelo.cifras)
como para los Resultado recién calculados (alertas): todo se lee de `regla`, `estado`, `decision` y `cifras`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
ORDEN = ("parar", "no_lanzar", "revisar", "continuar", "subir", "lanzar")
TECNICO = "tecnico"


class ErrorMensajes(Exception):
    pass


@dataclass(frozen=True)
class Mensaje:
    decision: str                       # la más importante de sus situaciones
    situaciones: tuple[str, ...]
    diagnosticos: tuple[str, ...]
    acciones: tuple[str, ...]

    def texto(self) -> str:
        """Una línea: diagnósticos y acciones (Telegram)."""
        return " ".join(self.diagnosticos) + (" Qué hacer: " + " ".join(self.acciones) if self.acciones else "")

    def json(self) -> dict:
        return {"decision": self.decision, "situaciones": list(self.situaciones),
                "diagnosticos": list(self.diagnosticos), "acciones": list(self.acciones)}


def ruta() -> Path:
    return Path(os.environ.get("CDF_MENSAJES_MARKETING") or RAIZ / "config" / "mensajes_marketing.yaml")


@lru_cache(maxsize=4)
def _cargar(fichero: str) -> dict:
    datos = yaml.safe_load(Path(fichero).read_text(encoding="utf-8")) or {}
    sit = datos.get("situaciones") or {}
    for clave, s in sit.items():
        if s.get("decision") not in ORDEN or not s.get("diagnostico") or not s.get("acciones"):
            raise ErrorMensajes(f"config/mensajes_marketing.yaml: la situación {clave} necesita decision "
                                f"({', '.join(ORDEN)}), diagnostico y acciones")
    return datos


def config() -> dict:
    return _cargar(str(ruta()))


def _f(x) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def situaciones_de(regla: dict) -> list[str]:
    """Situaciones de una regla activa (o de la e, aunque no esté activa: lanzar o no lanzar)."""
    letra, estado, decision = regla.get("regla"), regla.get("estado"), regla.get("decision")
    c = regla.get("cifras") or {}
    if letra == "e":
        return situacion_e(decision, c, estado)
    if estado != "activa":
        return []
    if letra == "a":
        salida = []
        pct, umbral = _f(c.get("lead_cita_pct")), _f(c.get("umbral_pct"))
        if pct is not None and umbral is not None and pct < umbral \
                and (c.get("leads") or 0) >= (c.get("n_minimo_leads") or 0):
            salida.append("lead_cita")
        fuera, umbral_f = _f(c.get("fuera_zona_pct")), _f(c.get("umbral_fuera_pct"))
        if fuera is not None and umbral_f is not None and fuera > umbral_f:
            salida.append("fuera_zona")
        return salida or ["lead_cita"]
    if letra == "b":
        mapa = {"no_atrae": "no_atrae", "no_convence": "no_convence", "historico": "menos_leads"}
        return [mapa[s] for s in c.get("senales") or [] if s in mapa] or ["no_atrae"]
    if letra == "c":
        return [{"parar": "caro_parar", "continuar": "caro_continuar"}.get(decision, "lead_caro")]
    if letra == "d":
        return ["cita_venta"]
    if letra == "f":
        return ["cansancio"]
    if letra == "subir":
        return ["subir"]
    return []


def situacion_e(decision: str | None, c: dict, estado: str | None = None) -> list[str]:
    """Lanzar o no lanzar un modelo (regla e), con sus cifras."""
    if decision == "lanzar":
        return ["lanzar"]
    if decision != "no_lanzar":
        return []
    if c.get("unidades") is not None and c.get("unidades_minimas") is not None \
            and c["unidades"] < c["unidades_minimas"]:
        return ["sin_stock"]
    if estado == "sin_dato" or not c.get("unidades_con_comparables"):
        return ["sin_comparables"]
    return ["precio_alto"]


def componer(situaciones: list[str], ciudad: str) -> Mensaje | None:
    """Mensaje de varias situaciones: ordenadas por importancia, sin repetir acciones."""
    sit = config()["situaciones"]
    claves = list(dict.fromkeys(s for s in situaciones if s in sit))
    if not claves:
        return None
    claves.sort(key=lambda k: ORDEN.index(sit[k]["decision"]))
    diag, acciones = [], []
    for k in claves:
        d = sit[k]["diagnostico"].format(ciudad=ciudad)
        if d not in diag:
            diag.append(d)
        for a in sit[k]["acciones"]:
            a = str(a).format(ciudad=ciudad)
            if a not in acciones:
                acciones.append(a)
    return Mensaje(sit[claves[0]]["decision"], tuple(claves), tuple(diag), tuple(acciones))


def de_campana(reglas: list[dict], decision: str | None, ciudad: str) -> Mensaje | None:
    """Mensaje de una campaña con sus reglas (panel_campana_decision.reglas) y su decisión."""
    if decision in (None, "sin_juzgar"):
        return componer(["pocos_datos"], ciudad)
    sit = [s for r in reglas if r.get("regla") != "e" for s in situaciones_de(r)]
    if decision == "subir":
        sit.append("subir")
    if not sit:
        poco = any(r.get("estado") == "sin_volumen" for r in reglas if r.get("regla") in ("a", "b", "c", "d"))
        sit = ["pocos_datos" if poco else "en_linea"]
    return componer(sit, ciudad)


def de_resultado(regla: dict, ciudad: str) -> Mensaje | None:
    """Mensaje de una sola regla (una alerta)."""
    return componer(situaciones_de(regla), ciudad)


def es_tecnico(usuario: dict | None) -> bool:
    return bool(usuario) and usuario.get("rol") == TECNICO


def interes(puntos: float | None) -> str:
    """Interés de búsqueda frente a sus rivales: sube, estable o baja."""
    if puntos is None:
        return "sin datos"
    umbral = float((config().get("interes") or {}).get("umbral_puntos", 1))
    return "sube" if puntos >= umbral else "baja" if puntos <= -umbral else "estable"
