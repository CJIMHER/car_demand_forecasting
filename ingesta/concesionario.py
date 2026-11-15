"""Datos del concesionario de esta instancia, leídos de config/concesionario.yaml.

Una instancia, un concesionario: nada propio de un concesionario concreto (nombre, código,
provincia, marca, textos del panel, umbrales) va en el código ni en las plantillas; sale de aquí.
La ruta del fichero se puede cambiar con CDF_CONCESIONARIO.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from ingesta.provincias import PROVINCIAS, comunidad

RAIZ = Path(__file__).resolve().parent.parent


class ErrorConfiguracion(Exception):
    pass


@dataclass(frozen=True)
class Concesionario:
    nombre: str                       # nombre comercial
    nombre_panel: str                 # cabecera del panel (p. ej. «<marca> <ciudad>»)
    marca: str
    dealer_ids: tuple[str, ...]
    provincia_codigo: str
    provincia: str
    comunidad_codigo: str
    comunidad: str
    provincias_comunidad: tuple[str, ...]
    n_minimo_medianas: int            # por debajo, precio y días se muestran atenuados
    n_minimo_porcentaje: int          # variación a 7 días: porcentaje solo si hace 7 días había al menos estos
    objetivo_antiguedad_dias: int = 60  # Dirección: antigüedad media del stock frente a este objetivo
    modelos_vn: tuple[str, ...] = ()  # modelos de la marca (datos de ejemplo del panel)
    modelos_vo: tuple[str, ...] = ()


def ruta() -> Path:
    return Path(os.environ.get("CDF_CONCESIONARIO", RAIZ / "config" / "concesionario.yaml"))


def cargar(fichero: Path | None = None) -> Concesionario:
    cfg = yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {}
    propio = cfg.get("propio") or {}
    panel = cfg.get("panel") or {}
    faltan = [c for c in ("nombre", "nombre_panel", "marca", "provincia_codigo", "dealer_ids") if not propio.get(c)]
    if faltan:
        raise ErrorConfiguracion(f"config/concesionario.yaml: faltan en «propio» {', '.join(faltan)}")
    prov = str(propio["provincia_codigo"]).strip().zfill(2)
    if prov not in PROVINCIAS:
        raise ErrorConfiguracion(f"config/concesionario.yaml: provincia_codigo {prov!r} no es un código INE (01–52)")
    ccaa_codigo, ccaa, provincias = comunidad(prov)
    return Concesionario(
        nombre=str(propio["nombre"]),
        nombre_panel=str(propio["nombre_panel"]),
        marca=str(propio["marca"]),
        dealer_ids=tuple(str(i).strip().upper() for i in propio["dealer_ids"] if str(i).strip()),
        provincia_codigo=prov,
        provincia=PROVINCIAS[prov],
        comunidad_codigo=ccaa_codigo,
        comunidad=ccaa,
        provincias_comunidad=provincias,
        n_minimo_medianas=int(panel.get("n_minimo_medianas", 5)),
        n_minimo_porcentaje=int(panel.get("n_minimo_porcentaje", 10)),
        objetivo_antiguedad_dias=int(panel.get("objetivo_antiguedad_dias", 60)),
        modelos_vn=_lista((cfg.get("modelos") or {}).get("VN")),
        modelos_vo=_lista((cfg.get("modelos") or {}).get("VO")),
    )


def _lista(valor) -> tuple[str, ...]:
    return tuple(str(v).strip() for v in (valor or []) if str(v).strip())
