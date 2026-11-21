"""Criterios de Cayetano para las campañas de Meta (docs/criterios_marketing_meta.md).

Umbrales, volúmenes mínimos y textos en alertas/reglas.yaml («criterios_meta»), los mismos para VN y
VO. De aquí salen, con las mismas cifras:
- las alertas meta_a … meta_f y meta_subir (alertas/reglas.py → criterio_meta);
- la decisión de cada campaña (panel_campana_decision): continuar, revisar, parar o subir presupuesto;
- la recomendación por modelo de Marketing (panel_recomendacion_modelo): la de sus campañas o, sin
  campaña, lanzar o no lanzar (regla e).

Las comparaciones con los umbrales se hacen con fracciones exactas: 4,99 % salta y 5 % no.

Datos: Meta (panel_campana_dia: gasto, impresiones y clics) y Walcu (panel_leads_dia: leads, citas, ventas
y el recuento de leads con código postal dentro y fuera de la zona cercana). La regla e y el contexto
de la d, del precálculo de Mercado en la zona cercana (panel_mercado_segmento, mismo modelo, acabado
y motor; los anuncios propios son nuestro stock publicado en la web de la marca).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from fractions import Fraction as F

import psycopg
from psycopg.types.json import Jsonb

from panel import formato as fmt

CIEN = F(100)
DECISIONES = ("parar", "revisar", "subir", "continuar")      # de más a menos grave


def _f(x) -> F:
    return F(str(x))


def num(x: F | float, decimales: int = 2) -> str:
    """Con hasta dos decimales y sin ceros sobrantes: 5 · 4,99 · 3,6."""
    texto = fmt.decimal(float(x), decimales)
    if "," in texto:
        texto = texto.rstrip("0").rstrip(",")
    return texto


def pct(fr: F) -> str:
    return f"{num(fr * CIEN)} %"


def euros(cents: F | int) -> str:
    return f"{num(F(cents) / CIEN)} €"


@dataclass
class Resultado:
    """Una regla aplicada a una campaña (o la e a un modelo)."""
    regla: str
    estado: str                 # activa, no, sin_volumen, sin_dato
    cifras_texto: str
    revisar: str = ""
    decision: str | None = None  # la que propone si está activa
    cifras: dict = field(default_factory=dict)

    @property
    def activa(self) -> bool:
        return self.estado == "activa"

    def json(self) -> dict:
        return {"regla": self.regla, "estado": self.estado, "decision": self.decision, "texto": self.cifras_texto,
                "revisar": self.revisar, "cifras": self.cifras}


@dataclass
class Juicio:
    campana_id: str
    titulo: str
    modelo: str | None
    ambito: str
    dias_vida: int
    leads: int
    citas: int
    ventas: int
    gasto_cents: int
    decision: str = "sin_juzgar"
    reglas: dict[str, Resultado] = field(default_factory=dict)

    @property
    def tipo(self) -> str | None:
        return self.ambito if self.ambito in ("VN", "VO") else None


# --------------------------------------------------------------------------- regla e (por modelo)

@dataclass
class Modelo:
    tipo: str
    modelo: str
    dia: date
    unidades: int
    resultado: Resultado
    posicion: float | None      # media de (nuestro / competencia − 1) ponderada por unidades: menor, mejor
    segmentos: list[dict]


def _segmento(s: dict) -> str:
    return (f"{s['acabado']} {s['motor']}: nuestro {euros(s['propio'])} frente a {euros(s['competencia'])} "
            f"(n = {s['n_competencia']})")


def regla_e(pg: psycopg.Connection, hoy: date, conc, crit: dict) -> dict[tuple[str, str], Modelo]:
    """Por tipo y modelo con unidades propias en la zona cercana: ¿se puede lanzar una campaña?"""
    c = crit["e"]
    minimo = int(c["unidades_minimas"])
    n_min = int(getattr(conc, c["n_minimo_competencia"]))
    salida = {}
    for tipo in ("VN", "VO"):
        dia = pg.execute("SELECT max(dia) FROM panel_mercado_segmento WHERE tipo = %s AND zona = 'cercana' "
                         "AND dia <= %s", (tipo, hoy)).fetchone()[0]
        if dia is None:
            continue
        segs: dict[str, list[dict]] = defaultdict(list)
        for modelo, acabado, motor, propios, n_p, p_p, n_c, p_c in pg.execute(
                """SELECT modelo, acabado, motor, propios, n_precio_propios, precio_propios_p50_cents,
                          n_precio_competencia, precio_competencia_p50_cents
                   FROM panel_mercado_segmento WHERE dia = %s AND tipo = %s AND zona = 'cercana'
                     AND nivel = 'motor' AND acabado_reconocido AND propios > 0
                   ORDER BY modelo, propios DESC, acabado, motor""", (dia, tipo)):
            segs[modelo].append({"acabado": acabado, "motor": motor, "propios": propios, "n_propio": n_p or 0,
                                 "propio": p_p, "n_competencia": n_c or 0, "competencia": p_c})
        for modelo, unidades in pg.execute(
                """SELECT modelo, propios FROM panel_mercado_segmento WHERE dia = %s AND tipo = %s
                   AND zona = 'cercana' AND nivel = 'modelo' AND propios > 0 ORDER BY modelo""", (dia, tipo)):
            comparables = [s for s in segs[modelo] if s["n_competencia"] >= n_min and s["propio"] is not None
                           and s["competencia"] is not None]
            con = sum(s["n_propio"] for s in comparables)
            debajo = [s for s in comparables if s["propio"] < s["competencia"]]
            n_debajo = sum(s["n_propio"] for s in debajo)
            posicion = (sum((s["propio"] / s["competencia"] - 1) * s["n_propio"] for s in comparables) / con
                        if con else None)
            cifras = {"unidades": unidades, "unidades_minimas": minimo, "unidades_con_comparables": con,
                      "unidades_por_debajo": n_debajo, "n_minimo_competencia": n_min, "dia_mercado": str(dia),
                      "segmentos": [{k: s[k] for k in ("acabado", "motor", "n_propio", "propio", "n_competencia",
                                                       "competencia")} for s in comparables]}
            stock = f"{unidades} unidades propias en la zona cercana (mínimo {minimo})"
            if unidades < minimo:
                r = Resultado("e", "activa", stock, decision="no_lanzar", cifras=cifras)
            elif not con:
                r = Resultado("e", "sin_dato", f"{stock}; sin competencia comparable del mismo modelo, acabado y "
                              f"motor en la zona cercana (mínimo {n_min} anuncios)", decision="no_lanzar", cifras=cifras)
            elif n_debajo * 2 > con:
                ejemplos = "; ".join(_segmento(s) for s in debajo[:3])
                r = Resultado("e", "no", f"{stock}; precio por debajo de la mediana de la competencia en {n_debajo} "
                              f"de {con} unidades con comparables ({ejemplos})", decision="lanzar", cifras=cifras)
            else:
                encima = [s for s in comparables if s not in debajo]
                ejemplos = "; ".join(_segmento(s) for s in encima[:3])
                r = Resultado("e", "activa", f"{stock}; precio por encima de la mediana de la competencia en "
                              f"{con - n_debajo} de {con} unidades con comparables ({ejemplos})",
                              decision="no_lanzar", cifras=cifras)
            salida[(tipo, modelo)] = Modelo(tipo, modelo, dia, unidades, r, posicion, comparables)
    return salida


# --------------------------------------------------------------------------- campañas

def _medias(pg, origen: str, dia: date, dias: int) -> dict[str, tuple[int, int, int]]:
    """Gasto, leads y citas de cada campaña en los últimos `dias` (para «en su media»)."""
    desde = dia - timedelta(days=dias - 1)
    gasto = dict(pg.execute("SELECT campana_id, sum(gasto_cents) FROM panel_campana_dia WHERE origen = %s "
                            "AND dia BETWEEN %s AND %s GROUP BY 1", (origen, desde, dia)).fetchall())
    resto = {c: (int(l), int(ci)) for c, l, ci in pg.execute(
        "SELECT campana_id, sum(leads), sum(citas) FROM panel_leads_dia WHERE origen = %s AND campana_id IS NOT NULL "
        "AND dia BETWEEN %s AND %s GROUP BY 1", (origen, desde, dia))}
    return {c: (int(g), *resto.get(c, (0, 0))) for c, g in gasto.items()}


def _media(medias: dict, sin: str, i: int) -> F | None:
    """Coste medio por lead (i=1) o por cita (i=2) de las demás campañas."""
    g = sum(v[0] for c, v in medias.items() if c != sin)
    n = sum(v[i] for c, v in medias.items() if c != sin)
    return F(g, n) if n else None


def juzgar(pg: psycopg.Connection, hoy: date, origen: str, conc, crit: dict,
           modelos_e: dict[tuple[str, str], Modelo]) -> tuple[date | None, list[Juicio]]:
    """Juicio de cada campaña activa (con gasto el último día de datos de Meta)."""
    dia = pg.execute("SELECT max(dia) FROM panel_campana_dia WHERE origen = %s AND dia <= %s",
                     (origen, hoy)).fetchone()[0]
    if dia is None:
        return None, []
    medias = _medias(pg, origen, dia, int(crit["media_dias"]))
    tolerancia = 1 + _f(crit["media_tolerancia_pct"]) / CIEN
    juicios = []
    for cid, titulo, modelo, ambito, inicio in pg.execute(
            """SELECT DISTINCT ON (campana_id) campana_id, titulo, modelo, ambito, inicio FROM panel_campana_dia
               WHERE origen = %s AND dia = %s AND gasto_cents > 0 ORDER BY campana_id""", (origen, dia)).fetchall():
        gasto, impresiones, clics = (int(x) for x in pg.execute(
            "SELECT sum(gasto_cents), sum(impresiones), sum(clics) FROM panel_campana_dia WHERE origen = %s AND campana_id = %s "
            "AND dia BETWEEN %s AND %s", (origen, cid, inicio, dia)).fetchone())
        leads, citas, ventas, zona, fuera, con_cp_filas = (int(x) for x in pg.execute(
            """SELECT coalesce(sum(leads), 0), coalesce(sum(citas), 0), coalesce(sum(ventas), 0),
                      coalesce(sum(leads_cp_zona), 0), coalesce(sum(leads_cp_fuera), 0), count(leads_cp_zona)
               FROM panel_leads_dia WHERE origen = %s AND campana_id = %s AND dia BETWEEN %s AND %s""",
            (origen, cid, inicio, dia)).fetchone())
        j = Juicio(cid, titulo, modelo, ambito, (dia - inicio).days, leads, citas, ventas, gasto)
        juicios.append(j)
        if j.dias_vida < int(crit["dias_vida_minimos"]):
            continue
        media_cpl, media_cpc = _media(medias, cid, 1), _media(medias, cid, 2)
        cpl = F(gasto, leads) if leads else None
        cpc = F(gasto, citas) if citas else None
        cpl_media = cpl is not None and media_cpl is not None and cpl <= media_cpl * tolerancia
        cpc_media = cpc is not None and media_cpc is not None and cpc <= media_cpc * tolerancia
        r = j.reglas
        r["a"] = _a(crit["a"], leads, citas, zona + fuera if con_cp_filas else None, fuera)
        r["b"] = _b(pg, origen, crit["b"], j, dia, impresiones, clics)
        r["c"] = _c(crit["c"], gasto, leads, cpc, media_cpc, crit["media_tolerancia_pct"])
        r["d"] = _d(crit["d"], j, cpl_media, cpc_media, media_cpl, media_cpc, modelos_e)
        r["f"] = _fatiga(pg, origen, crit["f"], cid, dia, j.dias_vida)
        if j.tipo and j.modelo and (j.tipo, j.modelo) in modelos_e:
            r["e"] = modelos_e[(j.tipo, j.modelo)].resultado
        r["subir"] = _subir(crit, j, cpl, cpc, r)
        if r["c"].activa and r["c"].decision == "parar":
            j.decision = "parar"
        elif any(r[k].activa for k in ("a", "b", "d", "f")) or (r["c"].activa and r["c"].decision == "revisar"):
            j.decision = "revisar"
        elif r["subir"].activa:
            j.decision = "subir"
        else:
            j.decision = "continuar"
    return dia, juicios


def _a(c: dict, leads: int, citas: int, con_cp: int | None, fuera: int) -> Resultado:
    partes, activa, sin_volumen, revisar, cifras = [], False, False, [c["revisar"]], {}
    umbral = _f(c["lead_cita_minima_pct"]) / CIEN
    n_min = int(c["n_minimo_leads"])
    if leads:
        tasa = F(citas, leads)
        texto = f"lead a cita: {citas} de {leads} = {pct(tasa)} (umbral {num(umbral * CIEN)} %)"
        cifras.update(citas=citas, leads=leads, lead_cita_pct=float(tasa * CIEN), umbral_pct=float(umbral * CIEN),
                      n_minimo_leads=n_min)
        if leads < n_min:
            texto += f"; sin volumen: {leads} leads, mínimo {n_min}"
            sin_volumen = tasa < umbral
        elif tasa < umbral:
            activa = True
        partes.append(texto)
    if con_cp:
        umbral_f = _f(c["fuera_zona_maxima_pct"]) / CIEN
        tasa_f = F(fuera, con_cp)
        partes.append(f"fuera de zona: {fuera} de {con_cp} leads con código postal = {pct(tasa_f)} "
                      f"(umbral {num(umbral_f * CIEN)} %)")
        cifras.update(fuera_zona=fuera, leads_con_cp=con_cp, fuera_zona_pct=float(tasa_f * CIEN),
                      umbral_fuera_pct=float(umbral_f * CIEN))
        if con_cp >= int(c["n_minimo_leads_con_cp"]) and tasa_f > umbral_f:
            activa = True
            revisar.append(c["revisar_fuera_zona"])
    if not partes:
        return Resultado("a", "sin_dato", "sin leads todavía")
    estado = "activa" if activa else ("sin_volumen" if sin_volumen else "no")
    return Resultado("a", estado, "; ".join(partes), " ".join(revisar), "revisar" if activa else None, cifras)


def _b(pg, origen: str, c: dict, j: Juicio, dia: date, impresiones: int, clics: int) -> Resultado:
    """Tres señales; cualquiera la dispara: tasa de clic baja (el anuncio no atrae), tasa de clic a lead baja
    (atrae pero no convence) o leads por impresión claramente por debajo del mismo modelo y mes."""
    if not impresiones:
        return Resultado("b", "sin_dato", "sin impresiones")
    partes, revisar, senales = [], [], []
    u_clic = _f(c["tasa_clic_minima_pct"]) / CIEN
    tasa_clic = F(clics, impresiones)
    partes.append(f"tasa de clic: {clics} de {impresiones} impresiones = {pct(tasa_clic)} (umbral {num(u_clic * CIEN)} %)")
    cifras = {"impresiones": impresiones, "clics": clics, "leads": j.leads, "tasa_clic_pct": float(tasa_clic * CIEN),
              "umbral_tasa_clic_pct": float(u_clic * CIEN)}
    if tasa_clic < u_clic:
        senales.append("no_atrae")
        revisar.append(c["revisar_no_atrae"])
    if clics:
        u_lead = _f(c["clic_lead_minima_pct"]) / CIEN
        clic_lead = F(j.leads, clics)
        partes.append(f"clic a lead: {j.leads} de {clics} clics = {pct(clic_lead)} (umbral {num(u_lead * CIEN)} %)")
        cifras.update(clic_lead_pct=float(clic_lead * CIEN), umbral_clic_lead_pct=float(u_lead * CIEN))
        if clic_lead < u_lead:
            senales.append("no_convence")
            revisar.append(c["revisar_no_convence"])
    if j.modelo:   # campañas del mismo modelo y mismo mes del año, en años anteriores
        otras = pg.execute(
            """SELECT campana_id, sum(impresiones) FROM panel_campana_dia WHERE origen = %s AND modelo = %s
               AND campana_id <> %s AND extract(month FROM dia) = %s AND extract(year FROM dia) < %s GROUP BY 1""",
            (origen, j.modelo, j.campana_id, dia.month, dia.year)).fetchall()
        imp = sum(int(i) for _, i in otras)
        if imp:
            leads_h = pg.execute(
                """SELECT coalesce(sum(leads), 0) FROM panel_leads_dia WHERE origen = %s AND campana_id = ANY(%s)
                   AND extract(month FROM dia) = %s AND extract(year FROM dia) < %s""",
                (origen, [o for o, _ in otras], dia.month, dia.year)).fetchone()[0]
            historico = F(int(leads_h), imp)
            tasa = F(j.leads, impresiones)
            factor = _f(c["factor_historico"])
            partes.append(f"leads por impresión: {pct(tasa)} frente a {pct(historico)} de las campañas de {j.modelo} "
                          f"en {fmt.MESES[dia.month - 1]} de años anteriores (n = {len(otras)} campañas; "
                          f"umbral {num(factor * CIEN)} % de esa cifra)")
            cifras.update(leads_impresion_pct=float(tasa * CIEN), historico_pct=float(historico * CIEN),
                          historico_campanas=len(otras), factor=float(factor))
            if tasa < historico * factor:
                senales.append("historico")
                revisar.append(c["revisar_historico"])
    cifras["senales"] = senales
    activa = bool(senales)
    return Resultado("b", "activa" if activa else "no", "; ".join(partes), " ".join(revisar),
                     "revisar" if activa else None, cifras)


def _c(c: dict, gasto: int, leads: int, cpc: F | None, media_cpc: F | None, tolerancia) -> Resultado:
    if not leads:
        return Resultado("c", "sin_dato", "sin leads todavía")
    umbral = _f(c["coste_lead_maximo"]) * CIEN
    cpl = F(gasto, leads)
    texto = f"coste por lead: {euros(gasto)} / {leads} leads = {euros(cpl)} (umbral {euros(umbral)})"
    cifras = {"coste_lead_cents": float(cpl), "umbral_cents": float(umbral), "leads": leads}
    if cpl <= umbral:
        return Resultado("c", "no", texto, cifras=cifras)
    if media_cpc is None:
        return Resultado("c", "activa", texto + "; sin media de coste por cita", c["sin_media"], "revisar", cifras)
    limite = media_cpc * (1 + _f(tolerancia) / CIEN)
    texto += (f"; coste por cita: {euros(cpc) if cpc is not None else 'sin citas'} frente a su media de "
              f"{euros(media_cpc)} (hasta {euros(limite)})")
    cifras.update(coste_cita_cents=None if cpc is None else float(cpc), media_coste_cita_cents=float(media_cpc))
    if cpc is not None and cpc <= limite:
        return Resultado("c", "activa", texto, c["continuar"], "continuar", cifras)
    return Resultado("c", "activa", texto, c["parar"], "parar", cifras)


def _d(c: dict, j: Juicio, cpl_media: bool, cpc_media: bool, media_cpl, media_cpc,
       modelos_e: dict[tuple[str, str], Modelo]) -> Resultado:
    if not j.citas:
        return Resultado("d", "sin_dato", "sin citas todavía")
    umbral = _f(c["tasa_venta_minima_pct"]) / CIEN
    n_min = int(c["n_minimo_citas"])
    tasa = F(j.ventas, j.citas)
    texto = f"tasa de venta: {j.ventas} de {j.citas} citas = {pct(tasa)} (umbral {num(umbral * CIEN)} %)"
    cifras = {"ventas": j.ventas, "citas": j.citas, "tasa_venta_pct": float(tasa * CIEN),
              "umbral_pct": float(umbral * CIEN), "n_minimo_citas": n_min}
    if j.citas < n_min:
        return Resultado("d", "sin_volumen" if tasa < umbral else "no",
                         texto + f"; sin volumen: {j.citas} citas, mínimo {n_min}", cifras=cifras)
    if tasa >= umbral:
        return Resultado("d", "no", texto, cifras=cifras)
    if media_cpl is None or media_cpc is None:
        return Resultado("d", "sin_dato", texto + "; sin media de coste por lead o por cita", cifras=cifras)
    if not (cpl_media and cpc_media):
        return Resultado("d", "no", texto + "; coste por lead o por cita fuera de su media (lo cubre la regla c)",
                         cifras=cifras)
    similares = ""
    m = modelos_e.get((j.tipo, j.modelo)) if j.tipo and j.modelo else None
    if m and m.segmentos:
        similares = " (" + "; ".join(f"{s['acabado']} {s['motor']}: {s['n_competencia']} de otros concesionarios a "
                                     f"{euros(s['competencia'])}, el nuestro a {euros(s['propio'])}"
                                     for s in m.segmentos[:int(c["segmentos_mostrados"])]) + ")"
        cifras["similares"] = m.segmentos[:int(c["segmentos_mostrados"])]
    return Resultado("d", "activa", texto + "; coste por lead y por cita en su media",
                     c["revisar"].format(similares=similares), "revisar", cifras)


def _fatiga(pg, origen: str, c: dict, cid: str, dia: date, dias_vida: int) -> Resultado:
    rec, ant = int(c["dias_recientes"]), int(c["dias_anteriores"])
    if dias_vida + 1 < rec + ant:
        return Resultado("f", "sin_dato", f"menos de {rec + ant} días de vida")
    corte, inicio = dia - timedelta(days=rec), dia - timedelta(days=rec + ant)
    leads = dict(pg.execute(
        "SELECT dia > %s, coalesce(sum(leads), 0) FROM panel_leads_dia WHERE origen = %s AND campana_id = %s "
        "AND dia > %s AND dia <= %s GROUP BY 1", (corte, origen, cid, inicio, dia)).fetchall())
    gasto_rec = int(pg.execute("SELECT coalesce(sum(gasto_cents), 0) FROM panel_campana_dia WHERE origen = %s "
                               "AND campana_id = %s AND dia > %s AND dia <= %s", (origen, cid, corte, dia)).fetchone()[0])
    l_rec, l_ant = int(leads.get(True, 0)), int(leads.get(False, 0))
    if not l_ant:
        return Resultado("f", "sin_dato", f"sin leads en los {ant} días anteriores")
    por_dia_rec, por_dia_ant = F(l_rec, rec), F(l_ant, ant)
    caida = 1 - por_dia_rec / por_dia_ant
    umbral_caida = _f(c["caida_minima_pct"]) / CIEN
    umbral_cpl = _f(c["coste_lead_maximo"]) * CIEN
    cpl = F(gasto_rec, l_rec) if l_rec else None
    texto = (f"leads por día: {num(por_dia_rec)} en los últimos {rec} días frente a {num(por_dia_ant)} en los {ant} "
             f"anteriores (−{pct(caida)}; umbral {num(umbral_caida * CIEN)} %); coste por lead de esos {rec} días: "
             f"{euros(cpl) if cpl is not None else 'sin leads'} (umbral {euros(umbral_cpl)})")
    cifras = {"leads_dia_recientes": float(por_dia_rec), "leads_dia_anteriores": float(por_dia_ant),
              "caida_pct": float(caida * CIEN), "coste_lead_cents": None if cpl is None else float(cpl)}
    activa = caida >= umbral_caida and (cpl is None and gasto_rec > 0 or cpl is not None and cpl > umbral_cpl)
    return Resultado("f", "activa" if activa else "no", texto, c["revisar"], "revisar" if activa else None, cifras)


def _subir(crit: dict, j: Juicio, cpl: F | None, cpc: F | None, r: dict[str, Resultado]) -> Resultado:
    s, a, d = crit["subir"], crit["a"], crit["d"]
    cpv = F(j.gasto_cents, j.ventas) if j.ventas else None
    lead_cita = F(j.citas, j.leads) if j.leads else None
    venta = F(j.ventas, j.citas) if j.citas else None
    pruebas = [
        ("coste por lead", cpl is not None and cpl < _f(s["coste_lead_maximo"]) * CIEN,
         f"coste por lead {euros(cpl) if cpl is not None else '—'} (< {num(_f(s['coste_lead_maximo']))} €)"),
        ("coste por cita", cpc is not None and cpc < _f(s["coste_cita_maximo"]) * CIEN,
         f"por cita {euros(cpc) if cpc is not None else '—'} (< {num(_f(s['coste_cita_maximo']))} €)"),
        ("lead a cita", lead_cita is not None and j.leads >= int(a["n_minimo_leads"])
         and lead_cita > _f(s["lead_cita_minima_pct"]) / CIEN,
         f"lead a cita {pct(lead_cita) if lead_cita is not None else '—'} (> {num(_f(s['lead_cita_minima_pct']))} %; "
         f"n = {j.leads} leads)"),
        ("tasa de venta", venta is not None and j.citas >= int(d["n_minimo_citas"])
         and venta > _f(s["tasa_venta_minima_pct"]) / CIEN,
         f"tasa de venta {pct(venta) if venta is not None else '—'} (> {num(_f(s['tasa_venta_minima_pct']))} %; "
         f"n = {j.citas} citas)"),
        ("coste por venta", cpv is not None and cpv < _f(s["coste_venta_maximo"]) * CIEN,
         f"coste por venta {euros(cpv) if cpv is not None else '—'} (< {num(_f(s['coste_venta_maximo']))} €)"),
    ]
    e = r.get("e")
    if j.tipo and j.modelo:
        pruebas.append(("regla e", e is not None and e.decision == "lanzar",
                        f"regla e: {e.cifras_texto if e else 'sin datos de mercado'}"))
    activas = [k for k in ("a", "b", "c", "d", "f") if r[k].activa]
    if activas:
        pruebas.append(("ninguna regla de revisión", False, f"reglas activas: {', '.join(activas)}"))
    texto = "; ".join(t for _, _, t in pruebas)
    ok = all(cumple for _, cumple, _ in pruebas)
    return Resultado("subir", "activa" if ok else "no", texto, decision="subir" if ok else None,
                     cifras={"cumplen": [n for n, c, _ in pruebas if c], "no_cumplen": [n for n, c, _ in pruebas if not c]})


# --------------------------------------------------------------------------- escritura

def escribir_decisiones(pg, hoy: date, origen: str, juicios: list[Juicio], version: str) -> int:
    with pg.transaction():
        pg.execute("DELETE FROM panel_campana_decision WHERE origen = %s AND dia = %s", (origen, hoy))
        for j in juicios:
            pg.execute(
                """INSERT INTO panel_campana_decision (dia, origen, campana_id, titulo, modelo, ambito, dias_vida,
                   decision, reglas, leads, citas, ventas, modelo_version, n)
                   VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (hoy, origen, j.campana_id, j.titulo, j.modelo, j.ambito, j.dias_vida, j.decision,
                 Jsonb([r.json() for r in j.reglas.values()]), j.leads, j.citas, j.ventas,
                 version, j.leads))
    return len(juicios)


ACCION = {"subir": "aumentar", "continuar": "mantener", "revisar": "revisar", "parar": "parar"}
NOMBRE_DECISION = {"subir": "Subir presupuesto", "continuar": "Continuar", "revisar": "Revisar", "parar": "Parar",
                   "sin_juzgar": "Sin juzgar"}


def recomendaciones(hoy: date, modelos_e: dict[tuple[str, str], Modelo], juicios_reales: list[Juicio],
                    version: str) -> list[dict]:
    """Una por tipo y modelo: la de sus campañas activas (la más grave) o, sin campaña, la regla e."""
    por_modelo: dict[tuple[str, str], list[Juicio]] = defaultdict(list)
    for j in juicios_reales:
        if j.tipo and j.modelo and j.decision != "sin_juzgar":
            por_modelo[(j.tipo, j.modelo)].append(j)
    filas = []
    for clave in sorted(set(modelos_e) | set(por_modelo)):
        tipo, modelo = clave
        js = por_modelo.get(clave)
        if js:
            peor = min(js, key=lambda x: DECISIONES.index(x.decision))
            if peor.decision == "subir" and not all(x.decision == "subir" for x in js):
                peor = next(x for x in js if x.decision != "subir")
            activas = [r for r in peor.reglas.values() if r.activa and r.regla != "e"]
            reglas = [r for r in activas if r.decision == peor.decision] or activas
            motivo = f"Campaña «{peor.titulo}»: " + " · ".join(f"regla {r.regla}: {r.cifras_texto}" for r in reglas) \
                if reglas else f"Campaña «{peor.titulo}»: ninguna regla de revisión activa."
            filas.append({"tipo": tipo, "modelo": modelo, "accion": ACCION[peor.decision], "motivo": motivo,
                          "regla": ",".join(r.regla for r in reglas) or None,
                          "cifras": {"campana_id": peor.campana_id, "reglas": [r.json() for r in reglas]},
                          "prioridad": None, "n": peor.leads})
        else:
            m = modelos_e[clave]
            r = m.resultado
            texto = ("Lanzar" if r.decision == "lanzar" else "No lanzar") + f" (regla e): {r.cifras_texto}."
            filas.append({"tipo": tipo, "modelo": modelo, "accion": r.decision, "motivo": texto, "regla": "e",
                          "cifras": r.cifras, "prioridad": None, "n": m.unidades})
    # Priorizar: más stock y mejor posición de precio
    lanzar = sorted((f for f in filas if f["accion"] == "lanzar"),
                    key=lambda f: (-modelos_e[(f["tipo"], f["modelo"])].unidades,
                                   modelos_e[(f["tipo"], f["modelo"])].posicion or 0, f["modelo"]))
    for i, f in enumerate(lanzar, 1):
        f["prioridad"] = i
    for f in filas:
        f.update(dia=hoy, modelo_version=version)
    return filas


def escribir_recomendaciones(pg, hoy: date, filas: list[dict]) -> int:
    cols = ("dia", "tipo", "modelo", "accion", "motivo", "regla", "cifras", "prioridad", "modelo_version", "n")
    with pg.transaction():
        pg.execute("DELETE FROM panel_recomendacion_modelo WHERE origen = 'real' AND dia = %s", (hoy,))
        with pg.cursor() as cur:
            cur.executemany(
                f"INSERT INTO panel_recomendacion_modelo ({', '.join(cols)}, origen) VALUES "
                f"({', '.join(['%s'] * len(cols))}, 'real')",
                [tuple(Jsonb(f[c]) if c == "cifras" else f[c] for c in cols) for f in filas])
    return len(filas)
