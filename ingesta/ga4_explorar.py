"""Exploración de la propiedad de GA4 (make ga4-explorar): qué hay, antes de decidir qué se descarga.

Con la misma credencial que la descarga diaria (GA4_PROPERTY_ID, GA4_CREDENCIALES; ver ingesta/ga4.py)
consulta la GA4 Data API y enseña en pantalla:

  1. Eventos: recuento de cada uno en los últimos 12 meses, y su primer y último día con datos.
  2. Los 100 tipos de página más vistos en los últimos 12 meses (vistas y rutas distintas), agrupando
     las rutas con config/ga4_paginas.yaml (ingesta/ga4_paginas.py); las que no casan, por su forma
     («/ocasion/*»), y las que no cuentan (no comerciales u otra marca), como «(fuera: …)».
  3. Dimensiones y métricas de interacción disponibles (de los metadatos de la propiedad), con su total
     de los últimos 12 meses, las personalizadas y el número de todas por categoría.
  4. Desde qué día hay datos en la propiedad.
  5. Todos los dominios (hostName) con sus vistas, en cuáles aparecen las rutas /peticion/* y cómo se tratan
     (marca propia, compartido, otra marca del grupo o ajeno al grupo, con aviso).
  6. Los eventos de intención (config/ga4_intencion.yaml) por dominio, y si cuentan en las series (solo los de
     los dominios propios).

Solo agregados: ninguna dimensión de usuario ni identificador; de las páginas, solo la ruta (sin
parámetros). No guarda nada: ni en Postgres, ni en el bucket, ni en fuentes_estado.

Uso:
  python -m ingesta.ga4_explorar                  (make ga4-explorar)
  python -m ingesta.ga4_explorar --todas          además, la lista completa de dimensiones y métricas
  python -m ingesta.ga4_explorar --ejemplos       además, las rutas más vistas de cada tipo, con marca y modelo
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Mapping
from datetime import date, datetime, timedelta

from ingesta import conectores as cx
from ingesta import ga4
from ingesta import ga4_paginas as gp
from ingesta import ga4_series as gs
from ingesta.scrapers_vw import hoy_madrid

PRIMER_DIA_GA4 = date(2015, 8, 14)        # el primer día que admite la Data API
POR_PAGINA = 100_000
MAX_PAGINAS = 200
MAX_METRICAS = 10                         # por petición, límite de la API
TIPOS_PAGINA = 100
EJEMPLOS = 5

# Lo que interesa para medir la intención: (apiName, qué es). Solo se piden si la propiedad las tiene.
METRICAS_INTERACCION = [
    ("sessions", "sesiones"),
    ("engagedSessions", "sesiones con interacción"),
    ("engagementRate", "porcentaje de sesiones con interacción"),
    ("userEngagementDuration", "tiempo de interacción total (segundos)"),
    ("averageSessionDuration", "duración media de la sesión (segundos)"),
    ("screenPageViews", "vistas de página"),
    ("screenPageViewsPerSession", "vistas por sesión"),
    ("eventCount", "eventos"),
    ("keyEvents", "eventos clave"),
    ("activeUsers", "usuarios activos"),
    ("totalUsers", "usuarios totales"),
    ("newUsers", "usuarios nuevos"),
]
DIMENSIONES_INTERACCION = [
    ("date", "día"),
    ("eventName", "nombre del evento"),
    ("isKeyEvent", "¿es evento clave?"),
    ("pagePath", "ruta de la página"),
    ("landingPage", "página de destino"),
    ("deviceCategory", "tipo de dispositivo"),
    ("newVsReturning", "nuevo o recurrente"),
    ("sessionDefaultChannelGroup", "agrupación de canales"),
    ("sessionSource", "fuente"),
    ("sessionMedium", "medio"),
    ("sessionCampaignName", "campaña"),
]


ErrorPaginas = gp.ErrorPaginas


# --------------------------------------------------------------------------- tipos de página

def forma(ruta: str) -> str:
    """«/ocasion/golf-life-12345/» → «/ocasion/*»: los tramos con números o muy largos, «*»."""
    tramos = [("*" if re.search(r"[0-9]{2,}", t) or len(t) > 40 else t) for t in ruta.strip("/").split("/")]
    return "/" + "/".join(tramos) if any(tramos) else "/"


def grupo(p: gp.Pagina) -> str:
    """Nombre del grupo de una página: su tipo; «(fuera: motivo)» si no cuenta; «(sin tipo) /forma/*» si no casa."""
    if not p.cuenta:
        return f"(fuera: {p.excluida})"
    return p.tipo_pagina or f"(sin tipo) {forma(p.ruta)}"


def agrupar_paginas(filas: list[tuple[str, str, int]], paginas: gp.Paginas) -> list[dict]:
    """[(dominio, ruta, vistas)] → [{tipo, vistas, rutas, ejemplos}], de más a menos vistos. ejemplos: las páginas
    más vistas del grupo (dominio y ruta), con su marca y su modelo."""
    vistas: Counter = Counter()
    rutas: dict[str, Counter] = defaultdict(Counter)
    clase: dict[tuple[str, str], gp.Pagina] = {}
    for host, ruta, n in filas:
        p = paginas.clasificar(ruta, host)
        t = grupo(p)
        h = gp.normalizar_host(host) or "(not set)"
        vistas[t] += n
        rutas[t][(h, p.ruta)] += n
        clase[(h, p.ruta)] = p
    return [{"tipo": t, "vistas": n, "rutas": len(rutas[t]),
             "ejemplos": [(h, r, v, clase[(h, r)].marca, clase[(h, r)].modelo)
                          for (h, r), v in rutas[t].most_common(EJEMPLOS)]}
            for t, n in sorted(vistas.items(), key=lambda x: (-x[1], x[0]))]


TRATO = {"propio": "marca propia: cuenta", "compartido": "compartido: la marca sale de la ruta",
         "otra_marca": "otra marca del grupo: fuera", "ajeno": "AJENO AL GRUPO: fuera",
         "sin_dominio": "sin dominio en GA4: la marca sale de la ruta"}


def eventos_por_dominio(cli: Cliente, desde: date, hasta: date, nombres: list[str]) -> dict[str, list[tuple[str, int]]]:
    """{evento: [(dominio, recuento)], de más a menos} de los eventos de intención en [desde, hasta]."""
    filtro = {"filter": {"fieldName": "eventName", "inListFilter": {"values": sorted(nombres)}}}
    salida: dict[str, Counter] = defaultdict(Counter)
    for d, m in cli.informe(desde, hasta, ["eventName", "hostName"], ["eventCount"], filtro=filtro):
        salida[d[0]][gp.normalizar_host(d[1]) or "(not set)"] += int(_num(m[0]))
    return {e: sorted(c.items(), key=lambda x: (-x[1], x[0])) for e, c in salida.items()}


def dominios(filas: list[tuple[str, str, int]], paginas: gp.Paginas) -> list[dict]:
    """[(dominio, ruta, vistas)] → por dominio, de más a menos vistas: vistas, vistas de /peticion/* y trato."""
    vistas: Counter = Counter()
    peticion: Counter = Counter()
    for host, ruta, n in filas:
        h = gp.normalizar_host(host) or "(not set)"
        vistas[h] += n
        if gp.normalizar(ruta).startswith("/peticion/"):
            peticion[h] += n
    return [{"dominio": h, "vistas": n, "peticion": peticion[h],
             "trato": "sin_dominio" if h == "(not set)" else paginas.dominio(h)}
            for h, n in sorted(vistas.items(), key=lambda x: (-x[1], x[0]))]


# --------------------------------------------------------------------------- API

class Cliente:
    def __init__(self, token: str, propiedad: str, secretos: list[str], dormir=time.sleep):
        self.token, self.propiedad, self.secretos, self.dormir = token, propiedad, secretos, dormir
        self.peticiones = 0
        self.brutos: list[bytes] | None = None     # una lista: guarda cada respuesta en bruto (para el bucket)

    def _pedir(self, url: str, cuerpo: dict | None = None) -> dict:
        self.peticiones += 1
        cab = {"Authorization": f"Bearer {self.token}"}
        if cuerpo is not None:
            cab["Content-Type"] = "application/json"
        datos, bruto = cx.peticion_json(url, metodo="POST" if cuerpo is not None else "GET", cabeceras=cab,
                                        cuerpo=json.dumps(cuerpo).encode() if cuerpo is not None else None,
                                        es_limite=ga4.es_limite, secretos=self.secretos, dormir=self.dormir)
        if self.brutos is not None:
            self.brutos.append(bruto)
        return datos

    def metadatos(self) -> dict:
        return self._pedir(f"{ga4.API}/properties/{self.propiedad}/metadata")

    def informe(self, desde: date, hasta: date, dimensiones: list[str], metricas: list[str],
                orden: list[dict] | None = None, limite: int | None = None,
                filtro: dict | None = None) -> list[tuple[list[str], list[str]]]:
        """Filas (valores de las dimensiones, valores de las métricas), con todas las páginas (o hasta `limite`).
        `filtro`: dimensionFilter de la API."""
        filas, offset = [], 0
        for _ in range(MAX_PAGINAS):
            cuerpo = {"dateRanges": [{"startDate": desde.isoformat(), "endDate": hasta.isoformat()}],
                      "dimensions": [{"name": n} for n in dimensiones], "metrics": [{"name": n} for n in metricas],
                      "limit": min(POR_PAGINA, limite or POR_PAGINA), "offset": offset, "keepEmptyRows": False}
            if orden:
                cuerpo["orderBys"] = orden
            if filtro:
                cuerpo["dimensionFilter"] = filtro
            datos = self._pedir(f"{ga4.API}/properties/{self.propiedad}:runReport", cuerpo)
            pagina = datos.get("rows") or []
            for r in pagina:
                d = [x.get("value", "") for x in r.get("dimensionValues") or []]
                m = [x.get("value", "0") for x in r.get("metricValues") or []]
                if len(d) != len(dimensiones) or len(m) != len(metricas):
                    raise cx.ErrorDescarga("fila de GA4 con dimensiones o métricas inesperadas")
                filas.append((d, m))
            offset += len(pagina)
            if not pagina or offset >= int(datos.get("rowCount") or 0) or (limite and offset >= limite):
                return filas[:limite] if limite else filas
        raise cx.ErrorDescarga("demasiadas páginas")


# --------------------------------------------------------------------------- consultas

def _dia(valor: str) -> date:
    return datetime.strptime(valor, "%Y%m%d").date()


def _num(valor: str) -> float:
    return float(valor or 0)


def eventos(cli: Cliente, desde: date, hasta: date) -> list[dict]:
    """Cada evento: recuento en [desde, hasta] y primer y último día con datos (en toda la historia)."""
    recuento = {d[0]: _num(m[0]) for d, m in cli.informe(desde, hasta, ["eventName"], ["eventCount"])}
    dias: dict[str, list[date]] = {}
    for d, _m in cli.informe(PRIMER_DIA_GA4, hasta, ["eventName", "date"], ["eventCount"]):
        dia = _dia(d[1])
        p = dias.setdefault(d[0], [dia, dia])
        p[0], p[1] = min(p[0], dia), max(p[1], dia)
    nombres = set(recuento) | set(dias)
    lista = [{"evento": n, "recuento": recuento.get(n, 0.0), "primero": dias.get(n, [None])[0],
              "ultimo": dias.get(n, [None, None])[1]} for n in nombres]
    return sorted(lista, key=lambda e: (-e["recuento"], e["evento"]))


def paginas(cli: Cliente, desde: date, hasta: date) -> list[tuple[str, str, int]]:
    """Vistas de cada página (dominio, ruta) en [desde, hasta]."""
    filas = cli.informe(desde, hasta, ["hostName", "pagePath"], ["screenPageViews"],
                        orden=[{"metric": {"metricName": "screenPageViews"}, "desc": True}])
    return [(d[0], d[1], int(_num(m[0]))) for d, m in filas]


def primer_dia(cli: Cliente, hasta: date) -> date | None:
    filas = cli.informe(PRIMER_DIA_GA4, hasta, ["date"], ["eventCount"],
                        orden=[{"dimension": {"dimensionName": "date"}}], limite=1)
    return _dia(filas[0][0][0]) if filas else None


def disponibles(meta: dict) -> tuple[dict[str, dict], dict[str, dict]]:
    dims = {d.get("apiName"): d for d in meta.get("dimensions") or [] if d.get("apiName")}
    mets = {m.get("apiName"): m for m in meta.get("metrics") or [] if m.get("apiName")}
    return dims, mets


def totales(cli: Cliente, desde: date, hasta: date, metricas: list[str]) -> dict[str, float]:
    """Total de cada métrica en [desde, hasta], sin dimensiones (de 10 en 10, el límite de la API)."""
    salida = {}
    for i in range(0, len(metricas), MAX_METRICAS):
        lote = metricas[i:i + MAX_METRICAS]
        filas = cli.informe(desde, hasta, [], lote)
        valores = filas[0][1] if filas else ["0"] * len(lote)
        salida.update({n: _num(v) for n, v in zip(lote, valores)})
    return salida


# --------------------------------------------------------------------------- texto

def _n(x: float) -> str:
    if x != int(x):
        return f"{x:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{int(x):,}".replace(",", ".")


def _pct(parte: float, total: float) -> str:
    return f"{100 * parte / (total or 1):.1f}".replace(".", ",")


def _f(d: date | None) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def explorar(entorno: Mapping[str, str], hoy: date, escribir: Callable[[str], None] = print, todas: bool = False,
             tipos: gp.Paginas | None = None, dormir=time.sleep, firmar=ga4.jwt_firmado,
             ejemplos: bool = False) -> int:
    tipos = gp.cargar() if tipos is None else tipos
    propiedad, ruta = ga4.credenciales(entorno)
    token, secretos = ga4.token_acceso(ruta, dormir, firmar)
    cli = Cliente(token, propiedad, secretos, dormir)
    hasta = hoy - timedelta(days=1)
    desde = hasta - timedelta(days=364)
    escribir(f"Propiedad de GA4 {propiedad}. Últimos 12 meses: del {_f(desde)} al {_f(hasta)}.")

    escribir("\n== 1. Eventos (recuento en los últimos 12 meses; primer y último día con datos)")
    lista = eventos(cli, desde, hasta)
    if not lista:
        escribir("  Ningún evento.")
    ancho = max([len(e["evento"]) for e in lista] + [6])
    escribir(f"  {'evento':<{ancho}}  {'recuento':>12}  {'primer día':>10}  {'último día':>10}")
    for e in lista:
        escribir(f"  {e['evento']:<{ancho}}  {_n(e['recuento']):>12}  {_f(e['primero']):>10}  {_f(e['ultimo']):>10}")

    escribir(f"\n== 2. Los {TIPOS_PAGINA} tipos de página más vistos (últimos 12 meses; config/ga4_paginas.yaml)")
    vistas_pagina = paginas(cli, desde, hasta)
    grupos, leidas = agrupar_paginas(vistas_pagina, tipos), len(vistas_pagina)
    total = sum(g["vistas"] for g in grupos)
    sin_tipo = sum(g["vistas"] for g in grupos if g["tipo"].startswith("(sin tipo)"))
    fuera = sum(g["vistas"] for g in grupos if g["tipo"].startswith("(fuera"))
    escribir(f"  {_n(leidas)} páginas en {len(grupos)} tipos; sin tipo de config/ga4_paginas.yaml, el "
             f"{_pct(sin_tipo, total)} % de las vistas; fuera (dominio, no comercial u otra marca), el "
             f"{_pct(fuera, total)} %.")
    ancho = max([len(g["tipo"]) for g in grupos[:TIPOS_PAGINA]] + [4])
    escribir(f"  {'tipo':<{ancho}}  {'vistas':>12}  {'%':>6}  {'rutas':>7}")
    for g in grupos[:TIPOS_PAGINA]:
        escribir(f"  {g['tipo']:<{ancho}}  {_n(g['vistas']):>12}  {_pct(g['vistas'], total):>6}  "
                 f"{_n(g['rutas']):>7}")
        if ejemplos:
            for h, r, v, marca, modelo in g["ejemplos"]:
                escribir(f"      {_n(v):>9}  {h}{r}  [{marca}" + (f"; {modelo}]" if modelo else "]"))

    escribir("\n== 3. Dimensiones y métricas de interacción disponibles")
    dims, mets = disponibles(cli.metadatos())
    hay = [n for n, _ in METRICAS_INTERACCION if n in mets]
    valores = totales(cli, desde, hasta, hay) if hay else {}
    escribir("  Métricas (total de los últimos 12 meses):")
    for n, que in METRICAS_INTERACCION:
        escribir(f"    {n:<26} {que:<40} " + (_n(valores.get(n, 0)) if n in mets else "no disponible"))
    if valores.get("activeUsers"):
        escribir(f"    {'(calculada)':<26} {'tiempo medio de interacción por usuario':<40} "
                 f"{_n(round(valores.get('userEngagementDuration', 0) / valores['activeUsers'], 1))} s")
    if valores.get("engagedSessions"):
        escribir(f"    {'(calculada)':<26} {'tiempo medio por sesión con interacción':<40} "
                 f"{_n(round(valores.get('userEngagementDuration', 0) / valores['engagedSessions'], 1))} s")
    escribir("  Dimensiones:")
    for n, que in DIMENSIONES_INTERACCION:
        escribir(f"    {n:<26} {que:<40} {'disponible' if n in dims else 'no disponible'}")
    propias = [(k, x) for k, x in {**dims, **mets}.items() if x.get("customDefinition")]
    escribir(f"  Personalizadas ({len(propias)}):" + ("" if propias else " ninguna"))
    for k, x in sorted(propias):
        escribir(f"    {k}  ({x.get('uiName') or ''}; {'métrica' if k in mets else 'dimensión'})")
    for clase, todas_ in (("Dimensiones", dims), ("Métricas", mets)):
        por = Counter(x.get("category") or "(sin categoría)" for x in todas_.values())
        escribir(f"  {clase} en total: {len(todas_)} (" + ", ".join(f"{c} {n}" for c, n in sorted(por.items())) + ")")
        if todas:
            for k in sorted(todas_):
                escribir(f"    {k}  ({todas_[k].get('uiName') or ''}; {todas_[k].get('category') or ''})")

    escribir("\n== 4. Datos en la propiedad")
    primero = primer_dia(cli, hasta)
    escribir(f"  Primer día con datos: {_f(primero)}" if primero else "  La propiedad no tiene datos.")

    escribir("\n== 5. Dominios (hostName) de los últimos 12 meses (config/ga4_paginas.yaml, «dominios»)")
    lista_d = dominios(vistas_pagina, tipos)
    if not lista_d:
        escribir("  Ningún dominio.")
    ancho = max([len(d["dominio"]) for d in lista_d] + [7])
    escribir(f"  {'dominio':<{ancho}}  {'vistas':>12}  {'/peticion/*':>11}  trato")
    for d in lista_d:
        escribir(f"  {d['dominio']:<{ancho}}  {_n(d['vistas']):>12}  {_n(d['peticion']):>11}  {TRATO[d['trato']]}")
    con_peticion = [d["dominio"] for d in lista_d if d["peticion"]]
    escribir("  Las rutas /peticion/* aparecen en: " + (", ".join(con_peticion) if con_peticion else "ningún dominio"))
    ajenos = [d["dominio"] for d in lista_d if d["trato"] == "ajeno"]
    if ajenos:
        escribir(f"  ¡AVISO! Dominios ajenos al grupo en la propiedad (excluidos de las señales): {', '.join(ajenos)}")

    escribir("\n== 6. Eventos de intención por dominio (últimos 12 meses; en las series solo cuentan los de los "
             "dominios propios)")
    cfg_series = gs.cargar_config()
    nombres = cfg_series.eventos_intencion()
    en_series = {e for e, _ in cfg_series.eventos.values()}
    por_evento = eventos_por_dominio(cli, desde, hasta, nombres)
    ancho_e = max(len(e) for e in nombres)
    ancho_d = max([len(h) for v in por_evento.values() for h, _ in v] + [7])
    escribir(f"  {'evento':<{ancho_e}}  {'dominio':<{ancho_d}}  {'recuento':>10}  en las series")
    for e in nombres:
        filas = por_evento.get(e) or []
        if not filas:
            escribir(f"  {e:<{ancho_e}}  {'(ninguno)':<{ancho_d}}  {0:>10}")
        for i, (h, n) in enumerate(filas):
            trato = "sin_dominio" if h == "(not set)" else tipos.dominio(h)
            cuenta = ("no (solo exploración)" if e not in en_series else
                      "sí" if trato == "propio" else f"no ({TRATO[trato].split(':')[0]})")
            escribir(f"  {e if i == 0 else '':<{ancho_e}}  {h:<{ancho_d}}  {_n(n):>10}  {cuenta}")
    escribir(f"\nPeticiones a la API: {cli.peticiones}. No se ha guardado nada.")
    return 0


def main(argv=None, entorno: Mapping[str, str] | None = None, escribir: Callable[[str], None] = print,
         dormir=time.sleep, firmar=ga4.jwt_firmado) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--todas", action="store_true", help="lista completa de dimensiones y métricas")
    parser.add_argument("--ejemplos", action="store_true", help="las rutas más vistas de cada tipo de página")
    args = parser.parse_args(argv)
    entorno = os.environ if entorno is None else entorno
    try:
        return explorar(entorno, hoy_madrid(), escribir, args.todas, dormir=dormir, firmar=firmar,
                        ejemplos=args.ejemplos)
    except cx.SinCredenciales as exc:
        escribir(f"GA4 sin credenciales: {exc}. No se ha hecho ninguna petición.")
        return 1
    except (cx.ErrorDescarga, ErrorPaginas) as exc:
        escribir(f"GA4: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
