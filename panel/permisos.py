"""Roles y permisos del panel. ÚNICO sitio donde se decide quién ve qué.

Las rutas solo preguntan `visible(...)`; la navegación sale de `navegacion(...)`.

Dos capas:
1. Permiso por rol (PERMISOS): direccion ve Dirección, Ventas, Marketing y Mercado; ventas, Ventas
   y Mercado; marketing, Marketing y Mercado; tecnico, todo y «Estado de las fuentes».
2. Datos de la vista (Dirección, Ventas y Marketing, según panel/contrato.py):
   - 'real' (algún bloque con datos reales y ninguno de ejemplo): la ven todos los roles con permiso;
   - 'ejemplo' (algún bloque con datos de ejemplo): solo los roles de PANEL_EJEMPLO_VISIBLE_PARA
     (por defecto, tecnico); los demás la ven desactivada («Se activa al conectar sus datos»);
   - 'vacia' (sin filas): desactivada para todos.
   Mercado y «Estado de las fuentes» no dependen del contrato: siempre activas para quien tiene permiso.
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
    de_contrato: bool     # True: se activa según sus datos (panel/contrato.py)


@dataclass(frozen=True)
class Entrada:
    clave: str
    titulo: str
    ruta: str
    activa: bool          # False: aparece en la navegación desactivada («Se activa al conectar sus datos»)


VISTAS = (
    Vista("direccion", "Dirección", "/direccion", de_contrato=True),
    Vista("ventas", "Ventas", "/ventas", de_contrato=True),
    Vista("marketing", "Marketing", "/marketing", de_contrato=True),
    Vista("mercado", "Mercado", "/mercado", de_contrato=False),
    Vista("estado", "Estado de las fuentes", "/estado-fuentes", de_contrato=False),
)
POR_CLAVE = {v.clave: v for v in VISTAS}

PERMISOS: dict[str, frozenset[str]] = {
    "direccion": frozenset({"direccion", "ventas", "marketing", "mercado"}),
    "ventas": frozenset({"ventas", "mercado"}),
    "marketing": frozenset({"marketing", "mercado"}),
    "tecnico": frozenset({"direccion", "ventas", "marketing", "mercado", "estado"}),
}

TEXTO_DESACTIVADA = "Se activa al conectar sus datos"
EJEMPLO_VISIBLE_POR_DEFECTO = frozenset({"tecnico"})


def roles_ejemplo(valor: str | None) -> frozenset[str]:
    """PANEL_EJEMPLO_VISIBLE_PARA: roles separados por comas (vacío: solo tecnico)."""
    roles = frozenset(r.strip() for r in (valor or "").split(",") if r.strip())
    desconocidos = roles - set(ROLES)
    if desconocidos:
        raise ValueError(f"PANEL_EJEMPLO_VISIBLE_PARA: roles desconocidos {', '.join(sorted(desconocidos))}; "
                         f"roles: {', '.join(ROLES)}")
    return roles or EJEMPLO_VISIBLE_POR_DEFECTO


def puede(rol: str | None, vista: str) -> bool:
    return vista in PERMISOS.get(rol or "", frozenset())


def visible(rol: str | None, vista: str, estados: dict[str, str], ejemplo_para: frozenset[str]) -> bool:
    """El rol puede abrir la vista con los datos que tiene ahora."""
    if not puede(rol, vista):
        return False
    if not POR_CLAVE[vista].de_contrato:
        return True
    estado = estados.get(vista, "vacia")
    if estado == "real":
        return True
    if estado == "ejemplo":
        return rol in ejemplo_para
    return False


def navegacion(rol: str, estados: dict[str, str], ejemplo_para: frozenset[str]) -> list[Entrada]:
    return [Entrada(v.clave, v.titulo, v.ruta, visible(rol, v.clave, estados, ejemplo_para))
            for v in VISTAS if puede(rol, v.clave)]


def inicio(rol: str, estados: dict[str, str], ejemplo_para: frozenset[str]) -> str:
    """Primera vista activa que puede ver el rol."""
    for v in navegacion(rol, estados, ejemplo_para):
        if v.activa:
            return v.ruta
    return "/salir"
