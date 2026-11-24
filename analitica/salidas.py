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


# Motivos de un candidato (un anuncio nuestro que no está el siguiente día con captura buena)
SALIDA = "salida"
AUSENCIA_CORTA = "ausencia_corta"     # regla 1: vuelve antes de K días buenos sin él
PENDIENTE = "pendiente"               # regla 1: aún no hay K días buenos sin él
ARRANQUE = "arranque"                 # fecha antes del primer día bueno de la fuente + K
REAPARICION = "reaparicion"           # regla 2: vuelve antes de R días
REPUBLICACION = "republicacion"       # la regla de la republicación (la decide sin_republicaciones)


@dataclass(frozen=True)
class Candidato:
    clave: Clave
    ultimo: date
    fecha: date
    motivo: str
    provisional: bool = False
    sin_captura: bool = False   # regla 1: con todos los días naturales habría K; faltan por días sin captura buena

    def salida(self) -> Salida:
        return Salida(self.clave, self.ultimo, self.fecha, self.provisional)


def candidatos(vistos: dict[Clave, list[date]], buenos: dict[str, list[date]], hasta: date,
               reglas: Reglas) -> list[Candidato]:
    """Cada vez que un anuncio nuestro visto el día D no está el siguiente día con captura buena de su fuente,
    con el motivo por el que es o no es una salida (reglas 1 y 2 y el arranque), sin mirar después de `hasta`.

    `vistos`: días (ordenados) en que se vio cada anuncio nuestro. `buenos`: días (ordenados) con captura
    buena de cada fuente."""
    K, R = reglas.dias_ausente, reglas.dias_reaparicion
    salida = []
    for clave, dias in vistos.items():
        dias = sorted({d for d in dias if d <= hasta})
        buenos_f = [d for d in buenos.get(clave[0], ()) if d <= hasta]
        if not dias or not buenos_f:
            continue
        desde = buenos_f[0] + K * UN_DIA            # arranque: antes, nada cuenta como salida
        for i, visto in enumerate(dias):
            siguiente = dias[i + 1] if i + 1 < len(dias) else None
            # días buenos estrictamente después de `visto` (y antes de volver a verlo)
            a = bisect.bisect_right(buenos_f, visto)
            b = bisect.bisect_left(buenos_f, siguiente) if siguiente else len(buenos_f)
            if b == a:
                continue                             # está el siguiente día bueno (o aún no lo hay)
            fecha = visto + UN_DIA
            if b - a < K:                            # aún no hay K días buenos sin él: no es salida
                naturales = ((siguiente or hasta + UN_DIA) - fecha).days
                motivo = AUSENCIA_CORTA if siguiente else PENDIENTE
                salida.append(Candidato(clave, visto, fecha, motivo, sin_captura=naturales >= K))
            elif fecha < desde:
                salida.append(Candidato(clave, visto, fecha, ARRANQUE))
            elif siguiente is not None and (siguiente - fecha).days < R:
                salida.append(Candidato(clave, visto, fecha, REAPARICION))   # reserva o retirada temporal
            else:
                salida.append(Candidato(clave, visto, fecha, SALIDA, (hasta - fecha).days < R))
    return sorted(salida, key=lambda c: (c.fecha, c.clave))


def detectar(vistos: dict[Clave, list[date]], buenos: dict[str, list[date]], hasta: date,
             reglas: Reglas) -> list[Salida]:
    """Salidas por ausencia (reglas 1 y 2 y el arranque), sin mirar después de `hasta`."""
    return [c.salida() for c in candidatos(vistos, buenos, hasta, reglas) if c.motivo == SALIDA]


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


def _detectar_en(h, catalogo, reglas: Reglas, desde: date | None, hasta: date):
    """(candidatos de reglas 1 y 2 y arranque, salidas tras la republicación, filas de nuestros anuncios, coches)
    con fecha de salida desde `desde` (None: todo el histórico)."""
    propias = h.propias()                                    # {clave: [(día, Fila)]}, solo anuncios nuestros
    vistos = {c: [d for d, _ in v] for c, v in propias.items()}
    buenos = {f: sorted(dias) for f, dias in h.completos.items()}
    todos = [c for c in candidatos(vistos, buenos, hasta, reglas) if desde is None or c.fecha >= desde]
    salidas = [c.salida() for c in todos if c.motivo == SALIDA]

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
    quedan = sin_republicaciones(salidas, coches, primero, reglas)
    return todos, quedan, propias, ultima, coches


def calcular(h, catalogo, reglas: Reglas, desde: date, hasta: date, reglas_precio: pv.Reglas | None) -> list[dict]:
    """Filas de panel_salidas_stock (origen real) con fecha de salida en [desde, hasta]. `h` es el lector de
    hechos de analitica/mercado.py."""
    conc = h.conc
    _, salidas, _, ultima, coches = _detectar_en(h, catalogo, reglas, desde, hasta)

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


# --------------------------------------------------------------------------- diagnóstico (make salidas-diagnostico)

# Columnas de descarte por día (el último día en que se vio cada candidato)
DESCARTES = (("sin_captura", "sin K días buenos sin él por días sin captura buena"),
             ("ausencia_corta", "vuelve antes de K días buenos sin él"),
             ("pendiente", "aún sin K días buenos sin él"),
             ("arranque", "antes del primer día bueno de la fuente + K"),
             ("reaparicion", "reaparece antes de R días"),
             ("republicacion", "republicación"))


def _motivo(c: Candidato) -> str:
    return "sin_captura" if c.motivo in (AUSENCIA_CORTA, PENDIENTE) and c.sin_captura else c.motivo


def diagnostico(h, catalogo, reglas: Reglas, hasta: date) -> dict[str, list[dict]]:
    """Por fuente y día (del primer día con captura buena o con un anuncio nuestro hasta `hasta`): anuncios
    nuestros, identificadores distintos, anuncios nuevos, los que no están el siguiente día con captura buena
    y cuántos descarta cada regla; y las salidas, como las calcula el precálculo. Solo recuentos."""
    todos, quedan, propias, _, _ = _detectar_en(h, catalogo, reglas, None, hasta)
    finales = {(s.clave, s.ultimo): s.provisional for s in quedan}
    fuentes = sorted(set(h.completos) | {c[0] for c in propias})
    salida: dict[str, list[dict]] = {}
    for fuente in fuentes:
        buenos = {d for d in h.completos.get(fuente, ()) if d <= hasta}
        ids_dia: dict[date, list[str]] = {}
        primero: dict[str, date] = {}
        for (f, i), v in propias.items():
            if f != fuente:
                continue
            for d, _ in v:
                if d <= hasta:
                    ids_dia.setdefault(d, []).append(i)
            primero[i] = v[0][0]
        dias = buenos | set(ids_dia)
        if not dias:
            continue
        por_dia: dict[date, dict] = {}
        d = min(dias)
        while d <= hasta:
            ids = ids_dia.get(d, [])
            por_dia[d] = {"dia": d, "captura_buena": d in buenos, "anuncios": len(ids),
                          "ids_distintos": len(set(ids)), "nuevos": sum(1 for i in set(ids) if primero[i] == d),
                          "desaparecen": 0, **{k: 0 for k, _ in DESCARTES}, "salidas": 0, "provisionales": 0}
            d += UN_DIA
        for c in todos:
            if c.clave[0] != fuente:
                continue
            f = por_dia[c.ultimo]
            f["desaparecen"] += 1
            if c.motivo != SALIDA:
                f[_motivo(c)] += 1
            elif (c.clave, c.ultimo) in finales:
                f["salidas"] += 1
                f["provisionales"] += finales[(c.clave, c.ultimo)]
            else:
                f["republicacion"] += 1
        salida[fuente] = list(por_dia.values())
    return salida


def texto_diagnostico(diag: dict[str, list[dict]], reglas: Reglas, hasta: date) -> str:
    cab = ["día", "captura", "anuncios", "ids", "nuevos", "desaparecen", "sin_captura", "vuelve<K", "pendiente",
           "arranque", "reaparece", "republicado", "salidas"]
    lineas = [f"Salidas de nuestro stock: diagnóstico hasta {hasta.isoformat()} (solo lectura, sin datos personales)",
              f"Reglas: K = {reglas.dias_ausente} días buenos sin él, R = {reglas.dias_reaparicion} días, "
              f"republicación en {reglas.republicacion_dias} días y a menos de {reglas.republicacion_km} km (VO).",
              "Cada anuncio que desaparece (no está el siguiente día con captura buena) cuenta en el último día "
              "en que se vio, en una sola columna: la regla que lo descarta o salidas.",
              "Columnas: " + "; ".join(f"{c}: {t}" for c, t in DESCARTES) + "."]
    totales = []
    for fuente, filas in diag.items():
        lineas += ["", f"== {fuente} ({TIPO_FUENTE.get(fuente, '?')})", " ".join(f"{c:>11}" for c in cab)]
        for f in filas:
            valores = [f["dia"].isoformat(), "sí" if f["captura_buena"] else "NO", f["anuncios"],
                       f["ids_distintos"], f["nuevos"], f["desaparecen"], *(f[k] for k, _ in DESCARTES), f["salidas"]]
            lineas.append(" ".join(f"{v:>11}" for v in valores))
        suma = {k: sum(f[k] for f in filas)
                for k in ("desaparecen", *(k for k, _ in DESCARTES), "salidas", "provisionales")}
        lineas.append(f"Total {fuente}: {suma['desaparecen']} desaparecen; "
                      + ", ".join(f"{k} {suma[k]}" for k, _ in DESCARTES)
                      + f"; salidas {suma['salidas']} ({suma['provisionales']} provisionales)")
        totales.append(f"{fuente} {suma['salidas']} ({suma['provisionales']} provisionales)")
    lineas += ["", "Salidas por fuente: " + (", ".join(totales) if totales else "sin datos")]
    return "\n".join(lineas)


def main(argv=None) -> int:
    import argparse
    import sys

    from analitica import mercado, versiones
    from ingesta.concesionario import cargar as cargar_concesionario
    from ingesta.db import conectar
    from ingesta.scrapers_vw import hoy_madrid

    p = argparse.ArgumentParser(description="Diagnóstico de las salidas de nuestro stock (solo lectura).")
    p.add_argument("orden", choices=["diagnostico"])
    p.add_argument("--hasta", type=date.fromisoformat, help="último día (por defecto, hoy)")
    args = p.parse_args(argv)
    reglas = cargar_reglas()
    if reglas is None:
        print("Sin salidas_stock en config/concesionario.yaml: no se calculan salidas.")
        return 1
    conc = cargar_concesionario()
    hasta = args.hasta or hoy_madrid()
    with conectar() as pg:
        pg.read_only = True                     # transacción de solo lectura: ni una escritura
        diag = diagnostico(mercado.Hechos(pg, conc), versiones.cargar(conc.marca), reglas, hasta)
        pg.rollback()
    sys.stdout.write(texto_diagnostico(diag, reglas, hasta) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
