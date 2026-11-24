"""Salidas de nuestro stock publicado: estimación de ventas (tabla panel_salidas_stock, en el precálculo).

Mientras no llegan las ventas del CRM, un coche NUESTRO que deja de estar publicado se cuenta como una
salida: casi siempre es una venta (también puede ser un traspaso a otra sede o empresa del grupo). Solo se
aplica a los anuncios propios (es_propio, los de «Nuestros» en Mercado). Para los de otros concesionarios
sigue valiendo «desaparecer no es vender»: aquí ni se leen.

Un anuncio nuestro visto el día D (fuente + id) tiene una salida con fecha D+1 si:

1. No aparece en los `dias_ausente` (K) días siguientes con captura buena de su fuente (pasada success
   cargada, la misma que da por buena la comprobación diaria). Un día sin captura completa no cuenta: la
   ventana se alarga hasta tener K días buenos. Mientras no los haya, no hay salida (ni provisional).
2. No reaparece antes de `dias_reaparicion` (R) días desde la salida: si reaparece, era una reserva o una
   retirada temporal y la salida se anula. Las salidas de los últimos R días (hasta el día del cálculo) son
   provisionales.
3. No es una republicación: si en la ventana de `republicacion_dias` días centrada en la salida aparece otro
   anuncio nuestro nuevo del mismo coche, no hay salida. VO: mismo modelo, acabado, motor, año de
   matriculación y color, y km a menos de `republicacion_km`; VN: mismo modelo, acabado, motor y color. Cada
   anuncio nuevo anula como mucho una salida (la más cercana en fechas).

Arranque: solo cuentan las salidas con fecha desde el primer día con captura buena de la fuente más K días,
para no tomar como salidas los coches del primer día.

Cada salida lleva el coche (tipo, modelo, acabado, motor, año, tramo de km, color), su último precio, sus
días publicado (con «al menos» como en Mercado), la fecha de salida y si es provisional; y, para el historial
del modelo de precios, la mediana de su segmento el último día que se vio (analitica/precio.py: el mismo
segmento y la misma ampliación; en VN, sin tramos de antigüedad ni de km). Ni matrícula ni bastidor.

Los umbrales salen de config/concesionario.yaml (salidas_stock), nunca del código.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import yaml

from analitica import precio as pv
from analitica.segmentos import anios_cumplidos, tramo
from ingesta.concesionario import ErrorConfiguracion, ruta as ruta_concesionario

CLAVES = ("dias_ausente", "dias_reaparicion", "republicacion_dias", "republicacion_km")
TIPO_FUENTE = {"vo": "VO", "vn": "VN", "vn_otros": "VN"}
UN_DIA = timedelta(days=1)

COLS = ["dia", "origen", "tipo", "fuente", "id_anuncio", "modelo", "version", "acabado", "motor", "anio", "km",
        "color", "precio_cents", "dias_publicado", "dias_cota", "ultimo_dia", "provisional", "nivel",
        "n_comparables", "mediana_cents"]

Clave = tuple[str, str]   # (fuente, id_anuncio)


@dataclass(frozen=True)
class Reglas:
    dias_ausente: int
    dias_reaparicion: int
    republicacion_dias: int
    republicacion_km: int

    @property
    def margen_republicacion(self) -> int:
        """Días antes y después de la salida: la mitad de la ventana, sin el día de la salida."""
        return self.republicacion_dias // 2


def cargar_reglas(fichero: Path | None = None) -> Reglas | None:
    """salidas_stock de config/concesionario.yaml. None si no está (no se calcula); un valor que falta o no es
    un entero positivo es un error."""
    cfg = (yaml.safe_load((fichero or ruta_concesionario()).read_text(encoding="utf-8")) or {}).get("salidas_stock")
    if cfg is None:
        return None
    valores = {}
    for clave in CLAVES:
        v = cfg.get(clave) if isinstance(cfg, dict) else None
        if isinstance(v, bool) or not isinstance(v, int) or v < 1:
            raise ErrorConfiguracion(f"config/concesionario.yaml: salidas_stock.{clave} tiene que ser un entero "
                                     "positivo")
        valores[clave] = v
    return Reglas(**valores)


# --------------------------------------------------------------------------- ausencia y reaparición

@dataclass(frozen=True)
class Salida:
    clave: Clave
    ultimo: date          # último día visto
    fecha: date           # ultimo + 1
    provisional: bool


def detectar(vistos: dict[Clave, list[date]], buenos: dict[str, list[date]], hasta: date,
             reglas: Reglas) -> list[Salida]:
    """Salidas por ausencia (reglas 1 y 2 y el arranque), sin mirar después de `hasta`.

    `vistos`: días (ordenados) en que se vio cada anuncio nuestro. `buenos`: días (ordenados) con captura
    buena de cada fuente."""
    K, R = reglas.dias_ausente, reglas.dias_reaparicion
    salidas = []
    for clave, dias in vistos.items():
        dias = [d for d in dias if d <= hasta]
        buenos_f = [d for d in buenos.get(clave[0], ()) if d <= hasta]
        if not dias or not buenos_f:
            continue
        desde = buenos_f[0] + K * UN_DIA            # arranque: antes, nada cuenta como salida
        for i, visto in enumerate(dias):
            siguiente = dias[i + 1] if i + 1 < len(dias) else None
            # días buenos estrictamente después de `visto` (y antes de volver a verlo)
            a = bisect.bisect_right(buenos_f, visto)
            b = bisect.bisect_left(buenos_f, siguiente) if siguiente else len(buenos_f)
            if b - a < K:
                continue                             # aún no hay K días buenos sin él: no es salida
            fecha = visto + UN_DIA
            if fecha < desde:
                continue
            if siguiente is not None and (siguiente - fecha).days < R:
                continue                             # reaparece: reserva o retirada temporal
            salidas.append(Salida(clave, visto, fecha, (hasta - fecha).days < R))
    return sorted(salidas, key=lambda s: (s.fecha, s.clave))


# --------------------------------------------------------------------------- republicación

@dataclass(frozen=True)
class Coche:
    """Lo que identifica un coche para la regla de republicación."""
    tipo: str
    modelo: str
    acabado: str           # el del catálogo o, sin él, el texto de versión
    motor: str
    anio: int | None
    color: str | None
    km: int | None


def mismo_coche(a: Coche, b: Coche, reglas: Reglas) -> bool:
    if (a.tipo, a.modelo, a.acabado, a.motor, a.color) != (b.tipo, b.modelo, b.acabado, b.motor, b.color):
        return False
    if a.tipo == "VN":
        return True
    if a.anio != b.anio:
        return False
    if a.km is None or b.km is None:
        return a.km is None and b.km is None
    return abs(a.km - b.km) < reglas.republicacion_km


def sin_republicaciones(salidas: list[Salida], coches: dict[Clave, Coche], primero: dict[Clave, date],
                        reglas: Reglas) -> list[Salida]:
    """Quita las salidas con un anuncio nuestro nuevo del mismo coche en la ventana centrada en la salida.
    `primero`: primer día visto de cada anuncio nuestro. Cada anuncio nuevo anula como mucho una salida, la más
    cercana en fechas (en empate, la anterior)."""
    m = reglas.margen_republicacion
    usados: set[Clave] = set()
    anuladas: set[Salida] = set()
    candidatas = []
    for s in salidas:
        for clave, dia in primero.items():
            if clave != s.clave and abs((dia - s.fecha).days) <= m and clave in coches and s.clave in coches \
                    and mismo_coche(coches[s.clave], coches[clave], reglas):
                candidatas.append((abs((dia - s.fecha).days), s.fecha, s.clave, clave, s))
    for _, _, _, nuevo, s in sorted(candidatas, key=lambda c: c[:4]):
        if s in anuladas or nuevo in usados:
            continue
        anuladas.add(s)
        usados.add(nuevo)
    return [s for s in salidas if s not in anuladas]


# --------------------------------------------------------------------------- precálculo

def _texto(v) -> str | None:
    return " ".join(str(v).split()).casefold() if v else None


def calcular(h, catalogo, reglas: Reglas, desde: date, hasta: date, reglas_precio: pv.Reglas | None) -> list[dict]:
    """Filas de panel_salidas_stock (origen real) con fecha de salida en [desde, hasta]. `h` es el lector de
    hechos de analitica/mercado.py."""
    conc = h.conc
    propias = h.propias()                                    # {clave: [(día, Fila)]}, solo anuncios nuestros
    vistos = {c: [d for d, _ in v] for c, v in propias.items()}
    buenos = {f: sorted(dias) for f, dias in h.completos.items()}
    salidas = [s for s in detectar(vistos, buenos, hasta, reglas) if s.fecha >= desde]

    def coche(clave: Clave, f) -> Coche:
        tipo = TIPO_FUENTE[clave[0]]
        v = catalogo.leer(tipo, f.modelo, f.version, f.combustible, f.potencia_cv)
        return Coche(tipo, f.modelo, v.acabado if v.reconocido else (_texto(f.version) or ""), v.motor,
                     None if tipo == "VN" or f.matriculacion is None else f.matriculacion.year,
                     _texto(f.color), None if tipo == "VN" else f.km)

    ultima = {(c, d): f for c, v in propias.items() for d, f in v}
    primero = {c: v[0][0] for c, v in propias.items()}
    coches = {s.clave: coche(s.clave, ultima[(s.clave, s.ultimo)]) for s in salidas}
    m = reglas.margen_republicacion
    for c, v in propias.items():                             # anuncios nuevos cerca de alguna salida
        dia = v[0][0]
        if c not in coches and any(abs((dia - s.fecha).days) <= m for s in salidas):
            coches[c] = coche(c, v[0][1])
    salidas = sin_republicaciones(salidas, coches, primero, reglas)

    # Mediana de su segmento el último día que se vio (sin nuestros coches), como el precio provisional
    filas = []
    por_dia: dict[tuple[date, str], list[Salida]] = {}
    for s in salidas:
        por_dia.setdefault((s.ultimo, TIPO_FUENTE[s.clave[0]]), []).append(s)
    for (dia, tipo), lista in sorted(por_dia.items()):
        otros = None
        if reglas_precio is not None and h.valido(dia, tipo):
            otros = []
            for f in h.filas(dia, tipo).values():
                if not f.es_propio:
                    otros.append(_comparable(h, catalogo, conc, tipo, f, dia))
        for s in lista:
            f = ultima[(s.clave, s.ultimo)]
            c = coches[s.clave]
            nuestro = _comparable(h, catalogo, conc, tipo, f, dia)
            comp = pv.comparar(nuestro, otros, reglas_precio) if otros is not None else None
            v = catalogo.leer(tipo, f.modelo, f.version, f.combustible, f.potencia_cv)
            km = tramo(f.km, conc.tramos_km) if tipo == "VO" else None
            filas.append({
                "dia": s.fecha, "origen": "real", "tipo": tipo, "fuente": s.clave[0], "id_anuncio": s.clave[1],
                "modelo": f.modelo, "version": f.version, "acabado": v.acabado if v.reconocido else None,
                "motor": None if v.motor.startswith("Sin dato de motor") else v.motor, "anio": c.anio,
                "km": None if km is None else conc.tramos_km[km][1], "color": f.color,
                "precio_cents": f.precio_cents, "dias_publicado": (s.ultimo - h.primero[s.clave]).days,
                "dias_cota": not h.alta_observada(s.clave), "ultimo_dia": s.ultimo, "provisional": s.provisional,
                "nivel": comp.nivel if comp else None, "n_comparables": comp.n if comp else None,
                "mediana_cents": comp.mediana if comp else None,
            })
    return filas


def _comparable(h, catalogo, conc, tipo: str, f, dia: date) -> pv.Coche:
    """Un anuncio como lo compara el precio provisional; en VN, sin tramos de antigüedad ni de km."""
    v = catalogo.leer(tipo, f.modelo, f.version, f.combustible, f.potencia_cv)
    zonas = h.zonas(f.provincia)
    vo = tipo == "VO"
    return pv.Coche(f.modelo, v.acabado if v.reconocido else None,
                    None if v.motor.startswith("Sin dato de motor") else v.motor,
                    tramo(anios_cumplidos(f.matriculacion, dia), conc.tramos_antiguedad) if vo else None,
                    tramo(f.km, conc.tramos_km) if vo else None,
                    "cercana" in zonas, "comunidad" in zonas, f.precio_cents)
