"""Roles y permisos del panel. ÚNICO sitio donde se decide quién ve qué.

Las rutas solo preguntan `puede(rol, vista)`; la navegación sale de `navegacion(rol)`.
"""

from __future__ import annotations

from dataclasses import dataclass

ROLES = {
    "direccion": "Dirección",
    "ventas": "Ventas",
    "marketing": "Marketing",
    "tecnico": "Técnico",
}


@dataclass(frozen=True)
class Vista:
    clave: str
    titulo: str
    ruta: str
    activa: bool          # False: aparece en la navegación desactivada («Se activa al conectar sus datos»)


VISTAS = (
    Vista("direccion", "Dirección", "/direccion", activa=False),
    Vista("ventas", "Ventas", "/ventas", activa=False),
    Vista("marketing", "Marketing", "/marketing", activa=False),
    Vista("mercado", "Mercado", "/mercado", activa=True),
    Vista("estado", "Estado de las fuentes", "/estado-fuentes", activa=True),
)
POR_CLAVE = {v.clave: v for v in VISTAS}

PERMISOS: dict[str, frozenset[str]] = {
    "direccion": frozenset({"direccion", "ventas", "marketing", "mercado"}),
    "ventas": frozenset({"ventas", "mercado"}),
    "marketing": frozenset({"marketing", "mercado"}),
    "tecnico": frozenset({"direccion", "ventas", "marketing", "mercado", "estado"}),
}

TEXTO_DESACTIVADA = "Se activa al conectar sus datos"


def puede(rol: str | None, vista: str) -> bool:
    return vista in PERMISOS.get(rol or "", frozenset())


def navegacion(rol: str) -> list[Vista]:
    return [v for v in VISTAS if puede(rol, v.clave)]


def inicio(rol: str) -> str:
    """Primera vista activa que puede ver el rol."""
    for v in navegacion(rol):
        if v.activa:
            return v.ruta
    return "/salir"
