"""Vista «Marketing» (docs/prototipo/Marketing.dc.html y tres bloques nuevos), por tipo: Todo, VN o VO.

Lee panel_campana_dia (Meta), panel_leads_dia (Walcu), panel_campana_salud,
panel_recomendacion_modelo, panel_atencion_relativa, panel_semana_campana,
panel_campana_resultado y, en VO, panel_riesgo_vehiculo (contrato). Solo sumas y cocientes de filas
ya preparadas.

Cada campaña tiene un ámbito: VN, VO o general (las dos). En VN o VO se ven las de ese ámbito y las
generales; de una general, los leads, citas y ventas del tipo elegido, y sin costes por lead, cita
ni venta: su gasto es común a VN y VO y no se reparte con ninguna regla. Las tarjetas de arriba, en
VN o VO, cuentan solo las campañas de ese ámbito.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import psycopg

from ingesta.concesionario import Concesionario
from panel import alertas as pal
from panel import contrato
from panel import formato as fmt
from panel import negocio as ng

VENTANA = 28
ACCIONES = (("aumentar", "Aumentar"), ("mantener", "Mantener"), ("reducir_cambiar", "Reducir o cambiar"))
ESTADOS = {"bien": "Bien", "vigilar": "Vigilar", "revisar": "Revisar", "sin_datos": "Sin datos suficientes"}
GRAVEDAD = {"revisar": 3, "vigilar": 2, "bien": 1}
ESLABONES = {"entrega": "Entrega", "anuncio": "Anuncio", "web": "Web", "calidad_lead": "Calidad del lead",
             "stock": "Stock"}
NIVELES = {"favorable": "Favorable", "normal": "Normal", "poco_favorable": "Poco favorable"}
MOTIVOS = {"estacionalidad": "Estacionalidad", "calendario": "Calendario", "stock_envejecido": "Stock envejecido",
           "atencion": "Atención", "presion_competencia": "Presión de otros concesionarios"}
AMBITOS = {"VN": "Nuevos (VN)", "VO": "Ocasión (VO)", "general": "General (VN y VO)"}
COMUN = "gasto común a VN y VO"
VEREDICTOS = {"por_encima": "Por encima de lo esperado", "normal": "Dentro de lo normal",
              "por_debajo": "Por debajo de lo esperado"}


def _euros(x: float | None) -> str:
    return fmt.GUION if x is None else f"{fmt.miles(x)} €"


def _div(a: float, b: float) -> float | None:
    return a / b if b else None


def _valor(x, unidad: str) -> str:
    if x is None:
        return fmt.GUION
    x = float(x)
    if unidad == "€":
        return f"{fmt.decimal(x, 2)} €"
    if unidad == "%":
        return f"{fmt.decimal(x, 2)} %"
    return f"{fmt.miles(x)} {unidad}"


CON_ARTICULO = {"entrega": "la entrega", "anuncio": "el anuncio", "web": "la web", "calidad_lead": "la calidad del lead",
                "stock": "el stock"}


def _numero(x, unidad: str) -> str:
    x = float(x)
    return fmt.miles(x) if unidad == "uds" else fmt.decimal(x, 2)


def diagnostico(eslabon, medida, unidad, valor, p10, p50, p90, dias_vida, prob, n) -> str:
    """«Dónde falla: la web. Leads por clic: 1,36 %, frente a 2,38 % de referencia (1,91–2,91 %) en
    campañas con los mismos 13 días de vida. Probabilidad de ir peor: 78 % (n = 31 campañas)»."""
    return (f"Dónde falla: {CON_ARTICULO[eslabon]}. {medida[:1].upper()}{medida[1:]}: {_valor(valor, unidad)}, "
            f"frente a {_valor(p50, unidad)} de referencia ({_numero(p10, unidad)}–{_numero(p90, unidad)} {unidad}) "
            f"en campañas con los mismos {dias_vida} días de vida. Probabilidad de ir peor: "
            f"{fmt.porcentaje(float(prob) * 100, 0)} (n = {n} campañas)")


def _ventana(pg, origen) -> tuple[date, date, date, date] | None:
    fin = ng.ultimo_dia(pg, "panel_campana_dia", origen)
    if fin is None:
        return None
    ini = fin - timedelta(days=VENTANA - 1)
    return ini, fin, ini - timedelta(days=VENTANA), ini - timedelta(days=1)


def _leads(pg, origen, desde: date, hasta: date, tipo: str = "todo") -> dict[str | None, list[int]]:
    """Leads, citas y ventas por campaña (None: sin campaña) en [desde, hasta]; con `tipo`, solo los
    leads de vehículos de ese tipo."""
    salida: dict[str | None, list[int]] = defaultdict(lambda: [0, 0, 0])
    for campana, leads, citas, ventas in pg.execute(
            """SELECT campana_id, sum(leads), sum(citas), sum(ventas) FROM panel_leads_dia
               WHERE origen = %s AND dia BETWEEN %s AND %s AND (%s = 'todo' OR tipo = %s) GROUP BY campana_id""",
            (origen, desde, hasta, tipo, tipo)):
        salida[campana] = [int(leads), int(citas), int(ventas)]
    return salida


def _ambitos(pg, origen) -> dict[str, str]:
    """Ámbito de cada campaña (VN, VO o general)."""
    return dict(pg.execute("SELECT DISTINCT campana_id, ambito FROM panel_campana_dia WHERE origen = %s", (origen,)))


def _de_tipo(tipo: str) -> tuple[str, ...]:
    """Ámbitos de campaña que se ven con cada opción del selector."""
    return ("VN", "VO", "general") if tipo == "todo" else (tipo, "general")


def _sin_generales(n: int) -> str:
    return "sin la campaña general" if n == 1 else "sin las campañas generales"


def _hay(pg, tabla: str, origen) -> bool:
    return ng.ultimo_dia(pg, tabla, origen) is not None


def kpis(pg, origen, tipo: str = "todo") -> dict:
    """En VN o VO, solo las campañas de ese ámbito (sin las generales, cuyo gasto no se reparte)."""
    v = _ventana(pg, origen)
    if v is None:
        return ng.bloque(origen, hay=False)
    ini, fin, ini_ant, fin_ant = v
    ambitos = ("VN", "VO", "general") if tipo == "todo" else (tipo,)
    gasto, activas = pg.execute(
        """SELECT coalesce(sum(gasto_cents), 0) / 100.0, count(DISTINCT campana_id) FILTER (WHERE dia = %s)
           FROM panel_campana_dia WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = ANY(%s)""",
        (fin, origen, ini, fin, list(ambitos))).fetchone()
    gasto_ant = pg.execute("SELECT coalesce(sum(gasto_cents), 0) / 100.0 FROM panel_campana_dia "
                           "WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = ANY(%s)",
                           (origen, ini_ant, fin_ant, list(ambitos))).fetchone()[0]
    generales = 0 if tipo == "todo" else pg.execute(
        """SELECT count(DISTINCT campana_id) FROM panel_campana_dia
           WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = 'general'""", (origen, ini, fin)).fetchone()[0]
    sin_general = f" · {_sin_generales(generales)}" if generales else ""
    hay_leads = _hay(pg, "panel_leads_dia", origen)
    tarjetas = [{"etiqueta": "Inversión" if tipo == "todo" else f"Inversión {tipo}", "valor": _euros(gasto),
                 "sub": f"{activas} campaña{'s' if activas != 1 else ''} activa{'s' if activas != 1 else ''}"
                        + sin_general}]
    if hay_leads:
        ambito = _ambitos(pg, origen)

        def cuenta(k):          # campañas del ámbito de las tarjetas (en Todo, cualquier campaña)
            return k is not None and (tipo == "todo" or ambito.get(k) == tipo)
        actual = _leads(pg, origen, ini, fin)
        previo = _leads(pg, origen, ini_ant, fin_ant)
        l, c, ve = (sum(x[i] for k, x in actual.items() if cuenta(k)) for i in range(3))
        l_ant = sum(x[0] for k, x in previo.items() if cuenta(k))
        cpl, cpl_ant = _div(float(gasto), l), _div(float(gasto_ant), l_ant)
        tarjetas.append({"etiqueta": "Leads", "valor": fmt.miles(l),
                         "sub": f"{fmt.miles(c)} citas · {fmt.miles(ve)} ventas atribuidas"})
        comp = (f"{fmt.porcentaje((cpl / cpl_ant - 1) * 100, 0, signo=True)} frente a los {VENTANA} días anteriores"
                if cpl is not None and cpl_ant else f"Sin datos de los {VENTANA} días anteriores")
        tarjetas.append({"etiqueta": "Coste por lead", "valor": _euros(cpl), "sub": comp})
        tarjetas.append({"etiqueta": "Coste por venta", "valor": _euros(_div(float(gasto), ve)),
                         "sub": "Lo que de verdad importa"})
    else:
        for e in ("Leads", "Coste por lead", "Coste por venta"):
            tarjetas.append({"etiqueta": e, "valor": fmt.GUION, "sub": "Sin datos todavía", "sin_dato": True})
    nota = (f"Solo las campañas de {AMBITOS[tipo]}, {_sin_generales(generales)}: su gasto es común a VN y VO "
            f"y no se reparte." if generales else "")
    return ng.bloque(origen, tarjetas=tarjetas, desde=fmt.fecha_corta(ini), hasta=fmt.fecha_corta(fin), nota=nota)


def campanas(pg, origen, orden: str, tipo: str = "todo") -> dict:
    v = _ventana(pg, origen)
    if v is None:
        return ng.bloque(origen, hay=False, orden=orden)
    ini, fin, _, _ = v
    hay_leads = _hay(pg, "panel_leads_dia", origen)
    leads = _leads(pg, origen, ini, fin) if hay_leads else {}
    leads_tipo = _leads(pg, origen, ini, fin, tipo) if hay_leads and tipo != "todo" else {}
    salud_dia = ng.ultimo_dia(pg, "panel_campana_salud", origen)
    salud: dict[str, list] = defaultdict(list)
    if salud_dia:
        for fila in pg.execute(
                """SELECT campana_id, dias_vida, eslabon, medida, unidad, valor, ref_p10, ref_p50, ref_p90, prob_peor,
                          estado, n FROM panel_campana_salud WHERE origen = %s AND dia = %s""", (origen, salud_dia)):
            salud[fila[0]].append(fila)
    filas = []
    for cid, titulo, ambito, modelo, inicio, gasto, ultimo in pg.execute(
            """SELECT campana_id, max(titulo), max(ambito), max(modelo), min(inicio),
                      sum(gasto_cents) / 100.0, max(dia) FROM panel_campana_dia
               WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = ANY(%s) GROUP BY campana_id""",
            (origen, ini, fin, list(_de_tipo(tipo)))):
        gasto = float(gasto)
        # Una general vista en VN o VO: sus leads de ese tipo y ningún coste (el gasto no se reparte)
        comun = tipo != "todo" and ambito == "general"
        l, c, ve = (leads_tipo if comun else leads).get(cid, [0, 0, 0]) if hay_leads else (None, None, None)
        dias_vida = (fin - inicio).days + 1
        estado, fallos, n_salud = "sin_datos", [], None
        if salud.get(cid):
            con_datos = [s for s in salud[cid] if s[10] != "sin_datos"]
            dias_vida = salud[cid][0][1]
            if con_datos:
                estado = max(con_datos, key=lambda s: GRAVEDAD[s[10]])[10]
                n_salud = max(s[11] for s in con_datos)
                for s in sorted(con_datos, key=lambda s: -float(s[9])):
                    if s[10] in ("vigilar", "revisar"):
                        fallos.append(diagnostico(s[2], s[3], s[4], s[5], s[6], s[7], s[8], s[1], s[9], s[11]))
        coste = (lambda n: None) if comun else (lambda n: _div(gasto, n) if n else None)
        filas.append({
            "nombre": titulo, "cpv_num": coste(ve), "gasto_num": gasto, "ambito": ambito, "comun": comun,
            "nota": AMBITOS[ambito] + (f" · {modelo}" if modelo else "")
                    + (f" · leads, citas y ventas de {tipo} · {COMUN}" if comun else "")
                    + f" · {dias_vida} días de vida · desde el {fmt.fecha_corta(inicio)}"
                    + ("" if ultimo == fin else " · terminada"),
            "gasto": _euros(gasto), "leads": fmt.miles(l), "citas": fmt.miles(c), "ventas": fmt.miles(ve),
            "cpl": _euros(coste(l)), "cpc": _euros(coste(c)), "cpv": _euros(coste(ve)),
            "estado": estado if salud.get(cid) else None,
            "estado_texto": ESTADOS[estado] if salud.get(cid) else "Sin datos todavía",
            "fallos": fallos, "alerta": estado in ("vigilar", "revisar"),
            "titulo_salud": f"n = {n_salud} campañas" if n_salud else "",
        })
    if orden == "gasto":
        filas.sort(key=lambda f: (-f["gasto_num"], f["nombre"]))
    else:
        filas.sort(key=lambda f: (f["cpv_num"] is None, f["cpv_num"] or 0, f["nombre"]))
    return ng.bloque(origen, filas=filas, orden=orden, hay_leads=hay_leads, hay_salud=bool(salud_dia),
                     tipo=tipo, hay_comun=any(f["comun"] for f in filas),
                     ordenes=[{"id": o, "etiqueta": e, "activa": o == orden, "url": f"/marketing?tipo={tipo}&orden={o}"}
                              for o, e in (("venta", "Por coste por venta"), ("gasto", "Por gasto"))])


def recomendacion(pg, origen, tipo: str = "todo") -> dict:
    """VN: por modelo del stock nuevo; VO: del de ocasión; Todo: las dos, cada una con su etiqueta."""
    dia = ng.ultimo_dia(pg, "panel_recomendacion_modelo", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    grupos = {k: [] for k, _ in ACCIONES}
    for tipo_r, modelo, accion, motivo in pg.execute(
            """SELECT tipo, modelo, accion, motivo FROM panel_recomendacion_modelo
               WHERE origen = %s AND dia = %s AND (%s = 'todo' OR tipo = %s) ORDER BY modelo, tipo""",
            (origen, dia, tipo, tipo)):
        clave = "reducir_cambiar" if accion in ("reducir", "cambiar") else accion
        grupos[clave].append({"modelo": modelo, "motivo": motivo, "tipo": tipo_r if tipo == "todo" else None,
                              "accion": {"reducir": "Reducir", "cambiar": "Cambiar"}.get(accion)})
    return ng.bloque(origen, columnas=[{"clave": k, "titulo": t, "elementos": grupos[k]} for k, t in ACCIONES])


def atencion(pg, origen) -> dict:
    dia = ng.ultimo_dia(pg, "panel_atencion_relativa", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    filas = pg.execute("""SELECT modelo, mes, relativo_puntos FROM panel_atencion_relativa
                          WHERE origen = %s AND dia = %s ORDER BY relativo_puntos DESC, modelo""",
                       (origen, dia)).fetchall()
    tope = max(abs(float(p)) for _, _, p in filas) or 1
    mes = filas[0][1]
    return ng.bloque(origen, mes=f"{fmt.MESES[mes.month - 1]} de {mes.year}", filas=[
        {"modelo": m, "texto": ("+" if p > 0 else "") + fmt.decimal(float(p), 0),
         "pos": round(max(float(p), 0) / tope * 100, 1), "neg": round(max(-float(p), 0) / tope * 100, 1)}
        for m, _, p in filas])


def semanas(pg, origen, tipo: str = "todo") -> dict:
    """Las seis semanas siguientes con la venta esperada del tipo elegido."""
    dia = ng.ultimo_dia(pg, "panel_semana_campana", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    filas = []
    for semana, nivel, p10, p50, p90, motivos, n in pg.execute(
            """SELECT semana, nivel, indice_p10, indice_p50, indice_p90, motivos, n FROM panel_semana_campana
               WHERE origen = %s AND dia = %s AND tipo = %s ORDER BY semana LIMIT 6""", (origen, dia, tipo)):
        filas.append({"semana": f"Semana del {semana.day} de {fmt.MESES[semana.month - 1]}", "nivel": nivel,
                      "nivel_texto": NIVELES[nivel],
                      "indice": f"Ventas{'' if tipo == 'todo' else ' ' + tipo} esperadas: "
                                f"{fmt.decimal(float(p50), 2)} veces una semana media "
                                f"({fmt.decimal(float(p10), 2)}–{fmt.decimal(float(p90), 2)}) · n = {n} años",
                      "motivos": [{"tipo": MOTIVOS.get(m.get("tipo"), m.get("tipo")), "texto": m.get("texto", "")}
                                  for m in motivos]})
    return ng.bloque(origen, filas=filas, tipo=tipo)


def pasadas(pg, origen, tipo: str = "todo") -> dict:
    """Todo: el resultado completo de cada campaña. VN o VO: las de ese ámbito y las generales con sus
    leads y ventas de ese tipo."""
    dia = ng.ultimo_dia(pg, "panel_campana_resultado", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    filas = []
    for (titulo, ambito, inicio, fin, gasto, leads, l10, l50, l90, ventas, v10, v50, v90, vl, vv, n) in pg.execute(
            """SELECT titulo, ambito, inicio, fin, gasto_cents / 100.0, leads, leads_sin_p10, leads_sin_p50,
                      leads_sin_p90, ventas, ventas_sin_p10, ventas_sin_p50, ventas_sin_p90, veredicto_leads,
                      veredicto_ventas, n
               FROM panel_campana_resultado WHERE origen = %s AND dia = %s
                AND (CASE WHEN %s = 'todo' THEN tipo = 'todo'
                          ELSE (ambito = %s AND tipo = 'todo') OR (ambito = 'general' AND tipo = %s) END)
               ORDER BY fin DESC, campana_id""", (origen, dia, tipo, tipo, tipo)):
        comun = tipo != "todo" and ambito == "general"
        filas.append({
            "nombre": titulo, "ambito": AMBITOS[ambito], "fechas": f"{fmt.fecha(inicio)} – {fmt.fecha(fin)}",
            "gasto": _euros(float(gasto)) + (f" ({COMUN})" if comun else ""),
            "de_tipo": f" {tipo}" if comun else "",
            "leads": fmt.miles(leads), "leads_sin": f"{fmt.miles(l50)} ({fmt.miles(l10)}–{fmt.miles(l90)})",
            "ventas": fmt.miles(ventas), "ventas_sin": f"{fmt.miles(v50)} ({fmt.miles(v10)}–{fmt.miles(v90)})",
            "veredicto_leads": vl, "veredicto_ventas": vv,
            "veredicto_leads_texto": VEREDICTOS[vl], "veredicto_ventas_texto": VEREDICTOS[vv], "n": n})
    return ng.bloque(origen, filas=filas, tipo=tipo)


def ocasion(pg, origen, conc: Concesionario) -> dict:
    """VO: por modelo, unidades de ocasión, cuántas superan el objetivo de antigüedad y precio frente al
    mercado (media de las que tienen similares). De la misma tabla que la vista Ventas."""
    dia = ng.ultimo_dia(pg, "panel_riesgo_vehiculo", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    objetivo = conc.objetivo_antiguedad_dias
    filas, total = [], 0
    for modelo, unidades, viejas, precio, con_precio in pg.execute(
            """SELECT modelo, count(*), count(*) FILTER (WHERE dias_exposicion > %s), avg(precio_mercado_pct),
                      count(precio_mercado_pct)
               FROM panel_riesgo_vehiculo WHERE origen = %s AND dia = %s AND tipo = 'VO'
               GROUP BY modelo ORDER BY 3 DESC, 2 DESC, modelo""", (objetivo, origen, dia)):
        total += unidades
        filas.append({"modelo": modelo, "unidades": fmt.miles(unidades), "viejas": fmt.miles(viejas),
                      "viejas_pct": round(viejas / unidades * 100, 1) if unidades else 0,
                      "precio": ng.signo_pct(None if precio is None else float(precio)),
                      "precio_n": f"{con_precio} de {unidades} con similares" if con_precio < unidades else ""})
    return ng.bloque(origen, filas=filas, objetivo=objetivo, provincia=conc.provincia,
                     total=fmt.miles(total))


def datos(pg: psycopg.Connection, conc: Concesionario, modos: dict, orden: str, hoy: date,
          tipo: str = "todo") -> dict:
    org = contrato.origenes(modos, "marketing")
    return {
        "tipo": tipo, "tipos": ng.selector_tipo("/marketing", tipo, "#marketing", f"&orden={orden}"),
        "kpis": kpis(pg, org["kpis"], tipo), "recomendacion": recomendacion(pg, org["recomendacion"], tipo),
        "campanas": campanas(pg, org["campanas"], orden, tipo),
        "atencion": atencion(pg, org["atencion"]) if tipo != "VO" else None,
        "ocasion": ocasion(pg, org["ocasion"], conc) if tipo == "VO" else None,
        "semanas": semanas(pg, org["semanas"], tipo), "pasadas": pasadas(pg, org["pasadas"], tipo), "origenes": org,
        "alertas": pal.bloque_rol(pg, org["alertas"], "marketing", tipo),
    }
