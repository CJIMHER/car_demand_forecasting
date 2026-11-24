"""Cálculo de cada regla de alertas/reglas.yaml. Deterministas: solo comparaciones con umbrales.

Cada función recibe el contexto de la evaluación (día, origen de los datos, umbrales de la regla) y
devuelve un Resultado: las alertas que saltan (Disparo, con su clave, sus cifras en bruto para la
tabla y sus huecos ya escritos para el texto) y los tipos (VN, VO) que hoy no se han podido evaluar,
con el motivo. Una alerta abierta de un tipo sin evaluar se mantiene abierta; nunca se cierra ni se
reabre sin datos.

Las reglas de mercado leen panel_mercado_* (precálculo de las 09:10) y solo evalúan un tipo si hoy
tiene captura completa y también la tiene el día de hace 7 (panel_mercado_dia.hace7_completo). Las
preparadas leen las tablas del contrato del panel con el origen que les da el motor.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta

import psycopg
from psycopg import sql

from ingesta.concesionario import Concesionario
from panel import formato as fmt

TODOS = "*"           # en Resultado.sin_evaluar: la regla entera
TIPOS = ("VN", "VO")
TIPO_TEXTO = {"VN": "nuevos", "VO": "de ocasión"}
SCRAPERS = {"vo": "VO", "vn": "VN", "vn_otros": "VN eléctricos e híbridos"}
CONECTORES = {"meta": "Meta", "ga4": "GA4", "gtrends": "Google Trends", "walcu_api": "Walcu (API)"}


@dataclass
class Disparo:
    clave: str
    cifras: dict
    huecos: dict
    tipo: str | None = None
    texto: str | None = None          # para reglas con varios textos (fuente_caida): cuál


@dataclass
class Resultado:
    disparos: list[Disparo] = field(default_factory=list)
    sin_evaluar: set = field(default_factory=set)   # tipos sin evaluar hoy, o {TODOS}
    motivo: str | None = None


@dataclass
class Contexto:
    pg: psycopg.Connection
    hoy: date
    conc: Concesionario
    origen: str
    regla: dict
    memo: dict = field(default_factory=dict)   # compartido por las reglas de una pasada (criterios de Meta)

    @property
    def u(self) -> dict:
        return self.regla.get("umbrales") or {}

    def n_minimo(self) -> int:
        return int(getattr(self.conc, self.regla["n_minimo"]))

    def ultimo_dia(self, tabla: str) -> date | None:
        return self.pg.execute(sql.SQL("SELECT max(dia) FROM {} WHERE origen = %s").format(sql.Identifier(tabla)),
                               (self.origen,)).fetchone()[0]


def _pct(x: float, d: int = 1) -> str:
    return fmt.decimal(x, d)


def _dia(d) -> str:
    return fmt.fecha_corta(date.fromisoformat(str(d)[:10]) if not isinstance(d, date) else d)


# --------------------------------------------------------------------------- fuente caída

def fuente_caida(ctx: Contexto) -> Resultado:
    pg, hoy = ctx.pg, ctx.hoy
    ultimos = {(f, t): (e, det or {}) for f, t, e, det in pg.execute(
        """SELECT DISTINCT ON (fuente, tipo) fuente, tipo, estado, detalle FROM fuentes_estado
           WHERE dia = %s ORDER BY fuente, tipo, id DESC""", (hoy,))}
    r = Resultado()

    def disparo(clave, texto, dia_dato: date | None = None, **huecos):
        d = dia_dato or hoy
        r.disparos.append(Disparo(clave, {"caso": texto, "dia": str(d), **huecos}, {"dia": _dia(d), **huecos},
                                  texto=texto))

    # Scrapers: la comprobación de las 09:00
    if not any((f, "comprobacion") in ultimos for f in SCRAPERS):
        disparo("comprobacion", "sin_comprobacion")
    for f, etiqueta in SCRAPERS.items():
        estado, det = ultimos.get((f, "comprobacion"), ("ok", {}))
        if estado == "sin_captura":
            detalle = f"; la última, el {_dia(det['ultima_captura'])}" if det.get("ultima_captura") else ""
            disparo(f"scraper|{f}", "scraper_sin_captura", fuente=etiqueta, detalle=detalle)
        elif estado == "sin_cargar":
            disparo(f"scraper|{f}", "scraper_sin_cargar", fuente=etiqueta, anuncios=fmt.miles(det.get("anuncios") or 0))
        elif estado == "error":
            disparo(f"scraper|{f}", "scraper_error", fuente=etiqueta, error=str(det.get("error", ""))[:150])
        estado, det = ultimos.get((f, "carga"), ("ok", {}))
        if estado == "error":
            disparo(f"carga|{f}", "carga_error", fuente=etiqueta, error=str(det.get("error", ""))[:150])
    # Precálculo de las 09:10
    if ("panel_mercado", "precalculo") not in ultimos:
        disparo("precalculo", "sin_precalculo")
    elif ultimos[("panel_mercado", "precalculo")][0] == "error":
        det = ultimos[("panel_mercado", "precalculo")][1]
        disparo("precalculo", "precalculo_error", paso=str(det.get("paso", "")), error=str(det.get("error", ""))[:150])
    # Copia: la de las 09:30 todavía no ha corrido; se mira la última
    ultima = pg.execute("SELECT dia, estado, detalle FROM fuentes_estado WHERE tipo = 'copia' AND dia <= %s "
                        "ORDER BY id DESC LIMIT 1", (hoy,)).fetchone()
    if ultima is not None:
        if ultima[1] != "ok":
            disparo("copia", "copia_error", ultima[0], paso=str((ultima[2] or {}).get("paso", "desconocido")))
        else:
            dias = (hoy - ultima[0]).days
            if dias > int(ctx.u.get("dias_sin_copia", 2)):
                disparo("copia", "sin_copia", ultima[0], dias=str(dias))
    # Conectores: un error de descarga (sin credenciales no es un error)
    for f, etiqueta in CONECTORES.items():
        estado, det = ultimos.get((f, "descarga"), ("ok", {}))
        if estado == "error":
            disparo(f"descarga|{f}", "descarga_error", fuente=etiqueta, error=str(det.get("error", ""))[:150])
    return r


# --------------------------------------------------------------------------- Google Trends

def gtrends_pendiente(ctx: Contexto) -> Resultado:
    """La descarga de Google Trends es manual: aviso si la última entrega cargada tiene más de
    `dias_maximos` días (o si no hay ninguna)."""
    ultima = ctx.pg.execute("SELECT max(fecha_descarga) FROM gtrends_entrega").fetchone()[0]
    r = Resultado()
    if ultima is None:
        r.disparos.append(Disparo("gtrends", {"caso": "sin_entrega"}, {}, texto="sin_entrega"))
        return r
    dias = (ctx.hoy - ultima).days
    if dias > int(ctx.u.get("dias_maximos", 35)):
        r.disparos.append(Disparo("gtrends", {"caso": "caducada", "dia": str(ultima), "dias": dias},
                                  {"dia": _dia(ultima), "dias": str(dias)}, texto="caducada"))
    return r


# --------------------------------------------------------------------------- mercado

def _tipos_mercado(ctx: Contexto) -> tuple[list[str], Resultado]:
    """Tipos evaluables hoy: captura completa hoy y hace 7 días (provincia)."""
    filas = dict(ctx.pg.execute("SELECT tipo, hace7_completo FROM panel_mercado_dia WHERE dia = %s AND zona = 'provincia'",
                                (ctx.hoy,)).fetchall())
    r, ok, motivos = Resultado(), [], []
    for tipo in TIPOS:
        if tipo not in filas:
            r.sin_evaluar.add(tipo)
            motivos.append(f"{tipo}: sin captura completa de hoy")
        elif not filas[tipo]:
            r.sin_evaluar.add(tipo)
            motivos.append(f"{tipo}: falta la captura de hace 7 días")
        else:
            ok.append(tipo)
    r.motivo = "; ".join(motivos) or None
    return ok, r


def _competidores(ctx: Contexto, dia: date, tipo: str, por_acabado: bool) -> dict[tuple, tuple]:
    propios = set(ctx.conc.dealer_ids)
    filas = ctx.pg.execute(
        f"""SELECT dealer_id, modelo, acabado, concesionario, anuncios, n_precio, precio_mediano_cents
            FROM panel_mercado_competidor WHERE dia = %s AND tipo = %s AND NOT es_propio
            AND acabado IS {'NOT ' if por_acabado else ''}NULL""", (dia, tipo)).fetchall()
    return {(d, m, a): (nombre, n, n_precio, p) for d, m, a, nombre, n, n_precio, p in filas if d not in propios}


def competidor_stock(ctx: Contexto) -> Resultado:
    tipos, r = _tipos_mercado(ctx)
    n_min, u = ctx.n_minimo(), ctx.u
    for tipo in tipos:
        hoy = _competidores(ctx, ctx.hoy, tipo, False)
        antes = _competidores(ctx, ctx.hoy - timedelta(days=7), tipo, False)
        for (dealer, modelo, _), (nombre, ahora, _, _) in sorted(hoy.items()):
            base = antes.get((dealer, modelo, None), (None, 0))[1]
            if base < n_min:
                continue
            subida = ahora - base
            pct = subida / base * 100
            if subida >= u["subida_unidades"] and pct >= u["subida_pct"]:
                r.disparos.append(Disparo(
                    f"{tipo}|{dealer}|{modelo}", {"dealer_id": dealer, "modelo": modelo, "antes": base, "ahora": ahora,
                                                 "subida_pct": round(pct, 1), "n_minimo": n_min},
                    {"concesionario": nombre, "modelo": modelo, "tipo_texto": TIPO_TEXTO[tipo],
                     "provincia": ctx.conc.provincia, "antes": fmt.miles(base), "ahora": fmt.miles(ahora),
                     "subida_pct": _pct(pct, 0)}, tipo=tipo))
    return r


def competidor_precio(ctx: Contexto) -> Resultado:
    tipos, r = _tipos_mercado(ctx)
    n_min, u = ctx.n_minimo(), ctx.u
    for tipo in tipos:
        hoy = _competidores(ctx, ctx.hoy, tipo, True)
        antes = _competidores(ctx, ctx.hoy - timedelta(days=7), tipo, True)
        for clave, (nombre, _, n, p) in sorted(hoy.items()):
            previo = antes.get(clave)
            if previo is None or n < n_min or previo[2] < n_min:
                continue
            p0 = previo[3]
            bajada = (p0 - p) / p0 * 100
            if bajada >= u["bajada_pct"]:
                dealer, modelo, acabado = clave
                r.disparos.append(Disparo(
                    f"{tipo}|{dealer}|{modelo}|{acabado}",
                    {"dealer_id": dealer, "modelo": modelo, "acabado": acabado, "antes_cents": p0, "ahora_cents": p,
                     "bajada_pct": round(bajada, 1), "n": n, "n_hace7": previo[2], "n_minimo": n_min},
                    {"concesionario": nombre, "modelo": modelo, "acabado": acabado, "tipo_texto": TIPO_TEXTO[tipo],
                     "antes": fmt.euros(p0), "ahora": fmt.euros(p), "bajada_pct": _pct(bajada), "n": fmt.miles(n)},
                    tipo=tipo))
    return r


def perdida_peso(ctx: Contexto) -> Resultado:
    tipos, r = _tipos_mercado(ctx)
    n_min, u = ctx.n_minimo(), ctx.u

    def leer(dia, tipo):
        return {m: (a, p) for m, a, p in ctx.pg.execute(
            "SELECT modelo, anuncios, propios FROM panel_mercado_modelo WHERE dia = %s AND tipo = %s AND zona = 'provincia'",
            (dia, tipo))}

    for tipo in tipos:
        hoy, antes = leer(ctx.hoy, tipo), leer(ctx.hoy - timedelta(days=7), tipo)
        for modelo, (a, p) in sorted(hoy.items()):
            if modelo not in antes or a < n_min or antes[modelo][0] < n_min:
                continue
            a0, p0 = antes[modelo]
            peso, peso0 = p / a * 100, p0 / a0 * 100
            caida = peso0 - peso
            if round(caida, 6) > u["caida_puntos"]:
                r.disparos.append(Disparo(
                    f"{tipo}|{modelo}", {"modelo": modelo, "peso_pct": round(peso, 1), "peso_hace7_pct": round(peso0, 1),
                                         "caida_puntos": round(caida, 1), "propios": p, "anuncios": a,
                                         "propios_hace7": p0, "anuncios_hace7": a0, "n_minimo": n_min},
                    {"modelo": modelo, "tipo_texto": TIPO_TEXTO[tipo], "provincia": ctx.conc.provincia,
                     "caida_puntos": _pct(caida), "antes": _pct(peso0), "ahora": _pct(peso),
                     "propios": fmt.miles(p), "anuncios": fmt.miles(a)}, tipo=tipo))
    return r


# --------------------------------------------------------------------------- preparadas (contrato del panel)

def _riesgo(ctx: Contexto) -> list[dict]:
    dia = ctx.ultimo_dia("panel_riesgo_vehiculo")
    if dia is None:
        return []
    cols = ("stock_id", "tipo", "modelo", "version", "dias_exposicion", "grupo", "p_venta90_p10", "p_venta90_p50",
            "p_venta90_p90")
    return [dict(zip(cols, f)) for f in ctx.pg.execute(
        f"SELECT {', '.join(cols)} FROM panel_riesgo_vehiculo WHERE origen = %s AND dia = %s ORDER BY stock_id",
        (ctx.origen, dia))]


def _vehiculo(v: dict) -> str:
    return f"{v['modelo']} {v['version']}" if v["version"] else v["modelo"]


def vehiculo_envejecido(ctx: Contexto) -> Resultado:
    r = Resultado()
    pct = int(ctx.u.get("percentil_curva", 75))
    dia = ctx.ultimo_dia("panel_curva_grupo")
    curvas = {g: (t, s, n) for g, t, s, n in ctx.pg.execute(
        "SELECT grupo, t_dias, s_p50, n FROM panel_curva_grupo WHERE origen = %s AND dia = %s",
        (ctx.origen, dia))} if dia else {}
    for v in _riesgo(ctx):
        if v["grupo"] not in curvas:
            continue
        t, s, n = curvas[v["grupo"]]
        # Percentil P de los días hasta salir: primer punto en que ya ha salido el P % (S ≤ 1 − P/100)
        limite = next((ti for ti, si in zip(t, s) if float(si) <= 1 - pct / 100), None)
        if limite is None or v["dias_exposicion"] <= limite:
            continue
        r.disparos.append(Disparo(
            v["stock_id"], {"stock_id": v["stock_id"], "dias": v["dias_exposicion"], "limite_dias": limite,
                            "percentil": pct, "grupo": v["grupo"], "n": n},
            {"vehiculo": _vehiculo(v), "stock_id": v["stock_id"], "dias": fmt.miles(v["dias_exposicion"]),
             "percentil": str(pct), "limite": fmt.miles(limite), "n": fmt.miles(n)}, tipo=v["tipo"]))
    return r


def riesgo_inmovilizacion(ctx: Contexto) -> Resultado:
    r = Resultado()
    maxima = float(ctx.u.get("prob_maxima", 0.5))
    for v in _riesgo(ctx):
        p = float(v["p_venta90_p50"])
        if p < maxima:
            r.disparos.append(Disparo(
                v["stock_id"], {"stock_id": v["stock_id"], "p_venta90_p10": float(v["p_venta90_p10"]), "p_venta90_p50": p,
                                "p_venta90_p90": float(v["p_venta90_p90"]), "dias": v["dias_exposicion"]},
                {"vehiculo": _vehiculo(v), "stock_id": v["stock_id"], "prob": _pct(p * 100, 0),
                 "p10": _pct(float(v["p_venta90_p10"]) * 100, 0), "p90": _pct(float(v["p_venta90_p90"]) * 100, 0),
                 "dias": fmt.miles(v["dias_exposicion"])}, tipo=v["tipo"]))
    return r


def ventas_fuera_banda(ctx: Contexto) -> Resultado:
    r = Resultado()
    dia = ctx.ultimo_dia("panel_ventas_mes")
    if dia is None:
        return r
    textos = {"todo": "totales", "VN": "de nuevos", "VO": "de ocasión"}
    for tipo, real, p10, p90 in ctx.pg.execute(
            """SELECT tipo, ventas_acumuladas, previsto_p10, previsto_p90 FROM panel_ventas_mes
               WHERE origen = %s AND dia = %s AND dia_mes = %s ORDER BY tipo""", (ctx.origen, dia, dia.day)):
        if real is None or float(p10) <= real <= float(p90):
            continue
        sentido = "por debajo" if real < float(p10) else "por encima"
        r.disparos.append(Disparo(
            tipo, {"tipo": tipo, "ventas": real, "p10": float(p10), "p90": float(p90), "dia_mes": dia.day},
            {"tipo_texto": textos[tipo], "sentido": sentido, "real": fmt.miles(real), "dia_mes": str(dia.day),
             "p10": fmt.miles(float(p10)), "p90": fmt.miles(float(p90))},
            tipo=None if tipo == "todo" else tipo))
    return r


def caida_leads(ctx: Contexto) -> Resultado:
    r = Resultado()
    dia = ctx.ultimo_dia("panel_leads_dia")
    if dia is None:
        return r
    dias, u = int(ctx.u["dias"]), ctx.u
    suma: dict[tuple, list[int]] = defaultdict(lambda: [0, 0])
    for modelo, tipo, d, leads in ctx.pg.execute(
            "SELECT modelo, tipo, dia, sum(leads) FROM panel_leads_dia WHERE origen = %s AND dia > %s AND dia <= %s "
            "GROUP BY 1, 2, 3", (ctx.origen, dia - timedelta(days=2 * dias), dia)):
        suma[(modelo, tipo)][0 if d > dia - timedelta(days=dias) else 1] += int(leads)
    for (modelo, tipo), (ahora, antes) in sorted(suma.items()):
        if antes < u["base_minima"]:
            continue
        caida = (antes - ahora) / antes * 100
        if caida >= u["caida_pct"]:
            r.disparos.append(Disparo(
                f"{tipo}|{modelo}", {"modelo": modelo, "leads": ahora, "leads_previos": antes, "caida_pct": round(caida, 1),
                                     "dias": dias},
                {"modelo": modelo, "tipo_texto": TIPO_TEXTO[tipo], "ahora": fmt.miles(ahora), "antes": fmt.miles(antes),
                 "dias": str(dias), "caida_pct": _pct(caida, 0)}, tipo=tipo))
    return r


def criterio_meta(ctx: Contexto) -> Resultado:
    """Reglas a, b, c, d, f y subir presupuesto de los criterios de Meta (alertas/criterios.py)."""
    from alertas import criterios as cr
    clave = ("juicios", ctx.origen)
    if clave not in ctx.memo:
        crit = ctx.memo["criterios"]
        if "modelos_e" not in ctx.memo:
            ctx.memo["modelos_e"] = cr.regla_e(ctx.pg, ctx.hoy, ctx.conc, crit)
        ctx.memo[clave] = cr.juzgar(ctx.pg, ctx.hoy, ctx.origen, ctx.conc, crit, ctx.memo["modelos_e"])
    _, juicios = ctx.memo[clave]
    letra = ctx.regla["criterio"]
    r = Resultado()
    for j in juicios:
        res = j.reglas.get(letra)
        if res is None or not res.activa:
            continue
        decision = {"subir": "subir presupuesto"}.get(res.decision, res.decision)
        r.disparos.append(Disparo(
            j.campana_id, {"campana_id": j.campana_id, "regla": letra, "decision": res.decision, **res.cifras},
            {"titulo": j.titulo, "decision": decision, "cifras": res.cifras_texto, "revisar": res.revisar},
            tipo=j.tipo))
    return r


def ventana_favorable(ctx: Contexto) -> Resultado:
    r = Resultado()
    dia = ctx.ultimo_dia("panel_semana_campana")
    if dia is None:
        return r
    lunes = ctx.hoy - timedelta(days=ctx.hoy.weekday())          # las N semanas que empiezan el lunes siguiente
    hasta = lunes + timedelta(days=7 * int(ctx.u.get("semanas", 2)))
    textos = {"todo": "VN y VO", "VN": "nuevos", "VO": "de ocasión"}
    for tipo, semana, p10, p50, p90, motivos in ctx.pg.execute(
            """SELECT tipo, semana, indice_p10, indice_p50, indice_p90, motivos FROM panel_semana_campana
               WHERE origen = %s AND dia = %s AND nivel = 'favorable' AND tipo = 'todo' AND semana > %s AND semana <= %s
               ORDER BY semana""", (ctx.origen, dia, lunes, hasta)):
        texto_motivos = " ".join(m["texto"].rstrip(".") + "." for m in motivos or [])
        r.disparos.append(Disparo(
            f"{tipo}|{semana}", {"tipo": tipo, "semana": str(semana), "indice_p10": float(p10), "indice_p50": float(p50),
                                 "indice_p90": float(p90)},
            {"semana": fmt.fecha_corta(semana), "tipo_texto": textos[tipo], "indice": fmt.decimal(float(p50), 2),
             "p10": fmt.decimal(float(p10), 2), "p90": fmt.decimal(float(p90), 2), "motivos": texto_motivos}))
    return r


CALCULOS = {f.__name__: f for f in (fuente_caida, gtrends_pendiente, competidor_stock, competidor_precio, perdida_peso,
                                    vehiculo_envejecido, riesgo_inmovilizacion, ventas_fuera_banda, caida_leads,
                                    criterio_meta, ventana_favorable)}
