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
    n_minimo_mostrar: int = 3         # segmentos: por debajo, «—» y «pocos anuncios» en vez de la cifra
    # Segmentos de VO: tramos (hasta inclusive o None en el último, etiqueta). Vacíos si la
    # configuración no los trae: entonces no se calcula la matriz de antigüedad y km.
    tramos_antiguedad: tuple[tuple[int | None, str], ...] = ()
    tramos_km: tuple[tuple[int | None, str], ...] = ()
    # Zona cercana: la provincia propia y las de propio.provincias_cercanas (criterios de Meta, regla e;
    # zona «cercana» de Mercado). Sin provincias cercanas, solo la propia.
    provincias_cercanas: tuple[str, ...] = ()
    # Dominio de la web propia, tal como aparece como origen de los leads en Walcu (scripts/walcu_agregar.py)
    web: str = ""

    @property
    def zona_cercana(self) -> tuple[str, ...]:
        return (self.provincia_codigo,) + tuple(p for p in self.provincias_cercanas if p != self.provincia_codigo)

    @property
    def zona_cercana_nombre(self) -> str:
        nombres = [PROVINCIAS[p] for p in self.zona_cercana]
        return nombres[0] if len(nombres) == 1 else ", ".join(nombres[:-1]) + " y " + nombres[-1]


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
    cercanas = tuple(str(c).strip().zfill(2) for c in (propio.get("provincias_cercanas") or []))
    if any(c not in PROVINCIAS for c in cercanas):
        raise ErrorConfiguracion("config/concesionario.yaml: provincias_cercanas lleva un código que no es INE (01–52)")
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
        n_minimo_mostrar=int(panel.get("n_minimo_mostrar", 3)),
        tramos_antiguedad=_tramos(cfg, "antiguedad_anios"),
        tramos_km=_tramos(cfg, "km"),
        provincias_cercanas=cercanas,
        web=str(propio.get("web") or "").strip(),
    )


def _tramos(cfg: dict, clave: str) -> tuple[tuple[int | None, str], ...]:
    """Tramos de segmentos.<clave>: cada uno con «hasta» (inclusive) y «etiqueta»; el último, sin «hasta»."""
    lista = (cfg.get("segmentos") or {}).get(clave) or []
    tramos, anterior = [], None
    for i, t in enumerate(lista):
        hasta = t.get("hasta")
        etiqueta = str(t.get("etiqueta") or "").strip()
        ultimo = i == len(lista) - 1
        if not etiqueta or (hasta is None) != ultimo:
            raise ErrorConfiguracion(f"config/concesionario.yaml: segmentos.{clave}: cada tramo lleva etiqueta y "
                                     "«hasta», salvo el último, que no lleva «hasta»")
        if hasta is not None:
            hasta = int(hasta)
            if anterior is not None and hasta <= anterior:
                raise ErrorConfiguracion(f"config/concesionario.yaml: segmentos.{clave}: «hasta» tiene que crecer")
            anterior = hasta
        tramos.append((hasta, etiqueta))
    return tuple(tramos)


def _lista(valor) -> tuple[str, ...]:
    return tuple(str(v).strip() for v in (valor or []) if str(v).strip())
