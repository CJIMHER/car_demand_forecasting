"""Vista «Marketing» (docs/prototipo/Marketing.dc.html y tres bloques nuevos), por tipo: Todo, VN o VO.

Lee panel_campana_dia (Meta), panel_leads_dia (Walcu), panel_campana_salud,
panel_recomendacion_modelo, panel_atencion_relativa, panel_semana_campana,
panel_campana_resultado, panel_trafico_canal_dia (GA4), panel_campana_mes y panel_referencia_meta (Meta y
Walcu por mes; ingesta/referencias_meta.py) y, en VO, panel_riesgo_vehiculo (contrato). Solo sumas y
cocientes de filas ya preparadas.

Cada campaña tiene un tipo (de un modelo o general) y un ámbito (VN, VO o ambos), los dos de
config/campanas.yaml (ingesta/campanas.py). En VN o VO se ven las de ese ámbito y las de ambos; de una
de ambos, los leads, citas y ventas del tipo elegido, y sin costes por lead, cita ni venta: su gasto
es común a VN y VO y no se reparte con ninguna regla. Las tarjetas de arriba, en VN o VO, cuentan
solo las campañas de ese ámbito.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta

import psycopg

from ingesta import canales as cn
from ingesta.concesionario import Concesionario
from panel import alertas as pal
from panel import contrato
from panel import formato as fmt
from panel import negocio as ng

VENTANA = 28
# Columnas de la recomendación por modelo (criterios de Meta, docs/criterios_marketing_meta.md). Las
# acciones «reducir» y «cambiar» (datos de ejemplo de antes) y «parar» van en «Revisar o parar».
ACCIONES = (("aumentar", "Subir presupuesto"), ("lanzar", "Lanzar"), ("mantener", "Continuar"),
            ("revisar", "Revisar o parar"), ("no_lanzar", "No lanzar"))
COLUMNA = {"reducir": "revisar", "cambiar": "revisar", "parar": "revisar"}
CHIP = {"reducir": "Reducir", "cambiar": "Cambiar", "parar": "Parar"}
DECISIONES = {"continuar": "Continuar", "revisar": "Revisar", "parar": "Parar", "subir": "Subir presupuesto",
              "sin_juzgar": "Sin juzgar todavía"}
NOMBRE_REGLA = {"a": "Regla a", "b": "Regla b", "c": "Regla c", "d": "Regla d", "e": "Regla e", "f": "Regla f",
                "subir": "Subir presupuesto"}
ESTADOS = {"bien": "Bien", "vigilar": "Vigilar", "revisar": "Revisar", "sin_datos": "Sin datos suficientes"}
GRAVEDAD = {"revisar": 3, "vigilar": 2, "bien": 1}
ESLABONES = {"entrega": "Entrega", "anuncio": "Anuncio", "web": "Web", "calidad_lead": "Calidad del lead",
             "stock": "Stock"}
NIVELES = {"favorable": "Favorable", "normal": "Normal", "poco_favorable": "Poco favorable"}
MOTIVOS = {"estacionalidad": "Estacionalidad", "calendario": "Calendario", "stock_envejecido": "Stock envejecido",
           "atencion": "Atención", "presion_competencia": "Presión de otros concesionarios"}
AMBITOS = {"VN": "Nuevos (VN)", "VO": "Ocasión (VO)", "ambos": "VN y VO"}
SIN_MERCADO = "sin datos de mercado"
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
    """Ámbito de cada campaña (VN, VO o ambos)."""
    return dict(pg.execute("SELECT DISTINCT campana_id, ambito FROM panel_campana_dia WHERE origen = %s", (origen,)))


def _de_tipo(tipo: str) -> tuple[str, ...]:
    """Ámbitos de campaña que se ven con cada opción del selector."""
    return ("VN", "VO", "ambos") if tipo == "todo" else (tipo, "ambos")


def _sin_comunes(n: int) -> str:
    return "sin la campaña de VN y VO" if n == 1 else "sin las campañas de VN y VO"


def _hay(pg, tabla: str, origen) -> bool:
    return ng.ultimo_dia(pg, tabla, origen) is not None


def kpis(pg, origen, tipo: str = "todo") -> dict:
    """En VN o VO, solo las campañas de ese ámbito (sin las de ambos, cuyo gasto no se reparte)."""
    v = _ventana(pg, origen)
    if v is None:
        return ng.bloque(origen, hay=False)
    ini, fin, ini_ant, fin_ant = v
    ambitos = ("VN", "VO", "ambos") if tipo == "todo" else (tipo,)
    gasto, activas = pg.execute(
        """SELECT coalesce(sum(gasto_cents), 0) / 100.0, count(DISTINCT campana_id) FILTER (WHERE dia = %s)
           FROM panel_campana_dia WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = ANY(%s)""",
        (fin, origen, ini, fin, list(ambitos))).fetchone()
    gasto_ant = pg.execute("SELECT coalesce(sum(gasto_cents), 0) / 100.0 FROM panel_campana_dia "
                           "WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = ANY(%s)",
                           (origen, ini_ant, fin_ant, list(ambitos))).fetchone()[0]
    comunes = 0 if tipo == "todo" else pg.execute(
        """SELECT count(DISTINCT campana_id) FROM panel_campana_dia
           WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = 'ambos'""", (origen, ini, fin)).fetchone()[0]
    sin_comun = f" · {_sin_comunes(comunes)}" if comunes else ""
    hay_leads = _hay(pg, "panel_leads_dia", origen)
    tarjetas = [{"etiqueta": "Inversión" if tipo == "todo" else f"Inversión {tipo}", "valor": _euros(gasto),
                 "sub": f"{activas} campaña{'s' if activas != 1 else ''} activa{'s' if activas != 1 else ''}"
                        + sin_comun}]
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
    nota = (f"Solo las campañas de {AMBITOS[tipo]}, {_sin_comunes(comunes)}: su gasto es común a VN y VO "
            f"y no se reparte." if comunes else "")
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
    decision_dia = ng.ultimo_dia(pg, "panel_campana_decision", origen)
    decisiones = {c: (d, r) for c, d, r in pg.execute(
        "SELECT campana_id, decision, reglas FROM panel_campana_decision WHERE origen = %s AND dia = %s",
        (origen, decision_dia))} if decision_dia else {}
    filas = []
    for cid, titulo, ambito, tipo_campana, modelo, fuera, inicio, gasto, ultimo in pg.execute(
            """SELECT campana_id, max(titulo), max(ambito), max(tipo_campana), max(modelo), bool_or(sin_mercado),
                      min(inicio), sum(gasto_cents) / 100.0, max(dia) FROM panel_campana_dia
               WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = ANY(%s) GROUP BY campana_id""",
            (origen, ini, fin, list(_de_tipo(tipo)))):
        gasto = float(gasto)
        # Una de ambos vista en VN o VO: sus leads de ese tipo y ningún coste (el gasto no se reparte)
        comun = tipo != "todo" and ambito == "ambos"
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
        decision, reglas = decisiones.get(cid, (None, []))
        criterios = [f"{NOMBRE_REGLA[r['regla']]}: {r['texto']}" + (f". {r['revisar']}" if r.get("revisar") else "")
                     + ("" if r["estado"] in ("activa", "no_aplica") else " (no salta)")
                     for r in reglas if r["estado"] in ("activa", "sin_volumen") and r["regla"] != "e"
                     or r["estado"] == "no_aplica"]
        salud_texto = (f"Frente a campañas con los mismos días de vida: {ESTADOS[estado]}"
                       + (f" (n = {n_salud} campañas)" if n_salud else "")) if salud.get(cid) else ""
        filas.append({
            "nombre": titulo, "cpv_num": coste(ve), "gasto_num": gasto, "ambito": ambito, "comun": comun,
            "nota": AMBITOS[ambito] + (f" · {modelo}" if tipo_campana == "modelo" else " · General")
                    + (f" · {SIN_MERCADO}" if fuera else "")
                    + (f" · leads, citas y ventas de {tipo} · {COMUN}" if comun else "")
                    + f" · {dias_vida} días de vida · desde el {fmt.fecha_corta(inicio)}"
                    + ("" if ultimo == fin else " · terminada"),
            "gasto": _euros(gasto), "leads": fmt.miles(l), "citas": fmt.miles(c), "ventas": fmt.miles(ve),
            "cpl": _euros(coste(l)), "cpc": _euros(coste(c)), "cpv": _euros(coste(ve)),
            # La decisión sale de los criterios de Meta; la salud frente a su referencia es solo detalle
            "decision": decision, "decision_texto": DECISIONES[decision] if decision else "Sin decidir todavía",
            "criterios": criterios, "salud_texto": salud_texto, "estado": estado if salud.get(cid) else None,
            "fallos": fallos, "alerta": decision in ("revisar", "parar"), "tipo_campana": tipo_campana,
            "sin_mercado": bool(fuera),
        })
    if orden == "gasto":
        filas.sort(key=lambda f: (-f["gasto_num"], f["nombre"]))
    else:
        filas.sort(key=lambda f: (f["cpv_num"] is None, f["cpv_num"] or 0, f["nombre"]))
    # Campañas de otras marcas del grupo en la misma cuenta: fuera de la tabla y de los criterios; solo se cuentan
    n_otras, gasto_otras = pg.execute(
        """SELECT count(DISTINCT campana_id), coalesce(sum(gasto_cents), 0) / 100.0 FROM panel_campana_otra_marca
           WHERE origen = %s AND dia BETWEEN %s AND %s AND gasto_cents > 0""", (origen, ini, fin)).fetchone()
    otras = (f"Campañas de otras marcas en esta cuenta: {n_otras}, gasto {_euros(float(gasto_otras))} en "
             f"{VENTANA} días." if n_otras else "")
    return ng.bloque(origen, filas=filas, orden=orden, hay_leads=hay_leads, hay_salud=bool(salud_dia), otras=otras,
                     hay_decision=bool(decisiones),
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
               WHERE origen = %s AND dia = %s AND (%s = 'todo' OR tipo = %s)
               ORDER BY prioridad NULLS LAST, modelo, tipo""", (origen, dia, tipo, tipo)):
        grupos[COLUMNA.get(accion, accion)].append({"modelo": modelo, "motivo": motivo,
                                                     "tipo": tipo_r if tipo == "todo" else None,
                                                     "accion": CHIP.get(accion)})
    return ng.bloque(origen, columnas=[{"clave": k, "titulo": t, "elementos": grupos[k]} for k, t in ACCIONES
                                       if grupos[k]])


def atencion(pg, origen) -> dict:
    dia = ng.ultimo_dia(pg, "panel_atencion_relativa", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    filas = pg.execute("""SELECT modelo, mes, relativo_puntos, entrega, semana_fin, cuota_pct, cuota_anterior_pct,
                                 n_competidores, n_comparaciones
                          FROM panel_atencion_relativa
                          WHERE origen = %s AND dia = %s ORDER BY relativo_puntos DESC, modelo""",
                       (origen, dia)).fetchall()
    tope = max(abs(float(f[2])) for f in filas) or 1
    mes, entrega, semana_fin = filas[0][1], filas[0][3], filas[0][4]
    # Con Google Trends (entrega): cuota de búsquedas en su comparación, en puntos con un decimal
    decimales = 1 if entrega else 0
    salida = []
    for m, _, p, _, _, cuota, antes, n_comp, n_cmp in filas:
        p = float(p)
        texto = fmt.decimal(p, decimales)
        salida.append({
            "modelo": m, "texto": ("+" if round(p, decimales) > 0 else "") + texto,
            "pos": round(max(p, 0) / tope * 100, 1), "neg": round(max(-p, 0) / tope * 100, 1),
            "detalle": (f"Cuota de búsquedas: {fmt.porcentaje(float(cuota), 1)}, frente a "
                        f"{fmt.porcentaje(float(antes), 1)} en las 4 semanas anteriores · {n_comp} competidores"
                        + (f" en {n_cmp} comparaciones" if n_cmp and n_cmp > 1 else "")) if cuota is not None else ""})
    return ng.bloque(origen, mes=f"{fmt.MESES[mes.month - 1]} de {mes.year}", filas=salida,
                     entrega=fmt.fecha(entrega) if entrega else None,
                     semana_fin=fmt.fecha_corta(semana_fin + timedelta(days=6)) if semana_fin else None)


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
    """Todo: el resultado completo de cada campaña. VN o VO: las de ese ámbito y las de ambos con sus
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
                          ELSE (ambito = %s AND tipo = 'todo') OR (ambito = 'ambos' AND tipo = %s) END)
               ORDER BY fin DESC, campana_id""", (origen, dia, tipo, tipo, tipo)):
        comun = tipo != "todo" and ambito == "ambos"
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


def trafico(pg, origen, tipo: str = "todo") -> dict:
    """Sesiones, sesiones con interacción y eventos clave por canal: últimos 28 días frente a los 28
    anteriores. En VN o VO, solo las sesiones cuya página de destino dice el tipo; las demás cuentan
    solo en «Todo» (y se dice cuántas son)."""
    fin = ng.ultimo_dia(pg, "panel_trafico_canal_dia", origen)
    if fin is None:
        return ng.bloque(origen, hay=False)
    ini = fin - timedelta(days=VENTANA - 1)
    ini_ant, fin_ant = ini - timedelta(days=VENTANA), ini - timedelta(days=1)
    actual: dict[str, list[float]] = defaultdict(lambda: [0, 0, 0.0])
    previo: dict[str, int] = defaultdict(int)
    total = sin_tipo = 0
    hay_previo = False
    for canal, tipo_fila, es_actual, s, si, ek in pg.execute(
            """SELECT canal, tipo, dia >= %s, sum(sesiones), sum(sesiones_interaccion), sum(eventos_clave)
               FROM panel_trafico_canal_dia WHERE origen = %s AND dia BETWEEN %s AND %s GROUP BY 1, 2, 3""",
            (ini, origen, ini_ant, fin)):
        if es_actual:
            total += int(s)
            sin_tipo += int(s) if tipo_fila == "sin_tipo" else 0
        if tipo != "todo" and tipo_fila != tipo:
            continue
        if es_actual:
            a = actual[canal]
            a[0], a[1], a[2] = a[0] + int(s), a[1] + int(si), a[2] + float(ek)
        else:
            previo[canal] += int(s)
            hay_previo = True
    try:
        nombres = cn.cargar()
    except (OSError, cn.ErrorCanales):
        nombres = None
    filas = []
    for canal, (s, si, ek) in sorted(actual.items(), key=lambda x: (x[0] == cn.OTROS, -x[1][0], x[0])):
        antes = previo.get(canal, 0)
        filas.append({
            "canal": canal, "nombre": nombres.nombre(canal) if nombres else canal,
            "sesiones": fmt.miles(s),
            "variacion": (fmt.porcentaje((s / antes - 1) * 100, 0, signo=True) if antes
                          else ("nuevo" if hay_previo and s else fmt.GUION)),
            "interaccion": fmt.miles(si), "interaccion_pct": fmt.porcentaje(si / s * 100 if s else None, 0),
            "eventos": fmt.miles(ek)})
    suma = sum(a[0] for a in actual.values())
    nota = ""
    if tipo != "todo":
        nota = (f"Solo las sesiones cuya página de destino es de {AMBITOS[tipo]}. {fmt.miles(sin_tipo)} de "
                f"{fmt.miles(total)} sesiones ({fmt.porcentaje(sin_tipo / total * 100 if total else None, 0)}) "
                "llegan a una página que no dice si es de nuevos o de ocasión: cuentan solo en «Todo».")
    return ng.bloque(origen, filas=filas, desde=fmt.fecha_corta(ini), hasta=fmt.fecha_corta(fin), tipo=tipo,
                     total=fmt.miles(suma), hay_previo=hay_previo, nota=nota)


def _euros2(x: float | None) -> str:
    """Con dos decimales si no es entero: 17,47 € · 120 €."""
    if x is None:
        return fmt.GUION
    return f"{fmt.miles(x)} €" if abs(x - round(x)) < 0.005 else f"{fmt.decimal(x, 2)} €"


def ultimo_mes(pg, origen, tipo: str = "todo") -> dict:
    """Campañas de Meta del último mes con gasto: gasto, leads (de Walcu y según Meta), citas, ventas y coste
    por lead, por cita y por venta reales, con la decisión de los criterios de Meta. Las de ambos, en VN o
    VO, con sus leads, citas y ventas de ese tipo y sin costes (su gasto no se reparte)."""
    dia = ng.ultimo_dia(pg, "panel_campana_mes", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    mes = pg.execute("SELECT max(mes) FROM panel_campana_mes WHERE origen = %s AND dia = %s AND gasto_cents > 0",
                     (origen, dia)).fetchone()[0]
    if mes is None:
        return ng.bloque(origen, hay=False)
    decision_dia = ng.ultimo_dia(pg, "panel_campana_decision", origen)
    decisiones = dict(pg.execute("SELECT campana_id, decision FROM panel_campana_decision WHERE origen = %s AND dia = %s",
                                 (origen, decision_dia)).fetchall()) if decision_dia else {}
    sufijo = "" if tipo == "todo" else f"_{tipo.lower()}"
    filas, walcu, sin_cruce = [], False, 0
    tot = [0.0, 0, 0, 0, 0]
    for (cid, titulo, ambito, tipo_campana, modelo, gasto, leads_meta, hay_walcu, cruce, leads, citas, ventas,
         l_t, c_t, v_t) in pg.execute(
            f"""SELECT campana_id, titulo, ambito, tipo_campana, modelo, gasto_cents / 100.0, leads_meta, walcu, cruce,
                       leads, citas, ventas, leads{sufijo or ''}, citas{sufijo or ''}, ventas{sufijo or ''}
                FROM panel_campana_mes WHERE origen = %s AND dia = %s AND mes = %s
                ORDER BY gasto_cents DESC, titulo""", (origen, dia, mes)):
        walcu = walcu or hay_walcu
        if cid is None:                 # leads de Facebook en Walcu sin campaña de Meta
            sin_cruce += int(l_t or 0)
            continue
        if tipo != "todo" and ambito not in (tipo, "ambos"):
            continue
        gasto = float(gasto)
        comun = tipo != "todo" and ambito == "ambos"
        l, c, v = (l_t, c_t, v_t) if tipo != "todo" else (leads, citas, ventas)
        con_walcu = cruce is not None
        coste = (lambda n: None) if comun or not con_walcu else (lambda n: _div(gasto, n) if n else None)
        if not comun:
            tot[0] += gasto
        if con_walcu:
            tot[1] += int(l or 0)
            tot[2] += int(c or 0)
            tot[3] += int(v or 0)
        tot[4] += int(leads_meta or 0) if not comun else 0
        decision = decisiones.get(cid)
        filas.append({
            "nombre": titulo, "ambito": ambito,
            "nota": (AMBITOS[ambito] if ambito else "Sin regla")
                    + (f" · {modelo}" if tipo_campana == "modelo" and modelo else " · General" if tipo_campana else "")
                    + (f" · leads, citas y ventas de {tipo} · {COMUN}" if comun else "")
                    + ("" if con_walcu or not hay_walcu else " · no casa con ningún lead de Walcu"),
            "gasto": _euros2(gasto), "leads": fmt.miles(l) if con_walcu else fmt.GUION,
            "leads_meta": fmt.miles(leads_meta) if leads_meta is not None else fmt.GUION,
            "citas": fmt.miles(c) if con_walcu else fmt.GUION, "ventas": fmt.miles(v) if con_walcu else fmt.GUION,
            "cpl": _euros2(coste(l)), "cpc": _euros2(coste(c)), "cpv": _euros2(coste(v)),
            "decision": decision, "decision_texto": DECISIONES[decision] if decision else "Sin decidir",
            "alerta": decision in ("revisar", "parar"), "comun": comun})
    total = {"gasto": _euros2(tot[0]), "leads": fmt.miles(tot[1]) if walcu else fmt.GUION,
             "leads_meta": fmt.miles(tot[4]), "citas": fmt.miles(tot[2]) if walcu else fmt.GUION,
             "ventas": fmt.miles(tot[3]) if walcu else fmt.GUION,
             "cpl": _euros2(_div(tot[0], tot[1])) if walcu else fmt.GUION,
             "cpc": _euros2(_div(tot[0], tot[2])) if walcu else fmt.GUION,
             "cpv": _euros2(_div(tot[0], tot[3])) if walcu else fmt.GUION}
    nombre_mes = f"{fmt.MESES[mes.month - 1]} de {mes.year}"
    return ng.bloque(origen, filas=filas, total=total, mes=nombre_mes, walcu=walcu, tipo=tipo,
                     sin_cruce=fmt.miles(sin_cruce) if sin_cruce else "", hasta=fmt.fecha_corta(dia),
                     hay_comun=any(f["comun"] for f in filas))


METRICAS_REF = (("gasto", "Gasto", "euros"), ("leads_meta", "Leads según Meta", "n"), ("leads", "Leads según Walcu", "n"),
                ("citas", "Citas", "n"), ("ventas", "Ventas", "n"), ("coste_lead", "Coste por lead", "euros"),
                ("coste_cita", "Coste por cita", "euros"), ("coste_venta", "Coste por venta", "euros"),
                ("lead_cita", "Lead a cita", "pct"), ("cita_venta", "Cita a venta", "pct"),
                ("fuera_zona", "Fuera de zona", "pct"), ("inexistente", "Número inexistente", "pct"))


def referencias(pg, origen, tipo: str = "todo") -> dict:
    """Referencias históricas: por año, con la diferencia entre los dos últimos."""
    from ingesta import referencias_meta as ref
    if origen is None:
        return ng.bloque(origen, hay=False)
    filas_ref = {a: x for (a, t), x in ref.leer_referencias(pg, origen).items() if t == tipo}
    if not filas_ref:
        return ng.bloque(origen, hay=False)
    anios = sorted(filas_ref)[-2:]
    ms = [ref.metricas(filas_ref[a]) for a in anios]
    columnas = [f"{a} ({fmt.MESES[filas_ref[a]['desde_mes'].month - 1][:3]}–"
                f"{fmt.MESES[filas_ref[a]['hasta_mes'].month - 1][:3]})" for a in anios]

    def valor(x, unidad):
        if x is None:
            return fmt.GUION
        return _euros2(x) if unidad == "euros" else fmt.porcentaje(x, 1) if unidad == "pct" else fmt.miles(x)
    filas = []
    for k, nombre, unidad in METRICAS_REF:
        dif = fmt.GUION
        if len(ms) == 2 and ms[0][k] is not None and ms[1][k] is not None:
            if unidad == "pct":
                dif = f"{fmt.decimal(ms[1][k] - ms[0][k], 1)} puntos".replace("-", "−")
                dif = dif if dif.startswith("−") else "+" + dif
            elif ms[0][k]:
                dif = fmt.porcentaje((ms[1][k] / ms[0][k] - 1) * 100, 0, signo=True)
        filas.append({"nombre": nombre, "valores": [valor(m[k], unidad) for m in ms], "diferencia": dif,
                      "clave": k})
    return ng.bloque(origen, columnas=columnas, filas=filas, dos=len(ms) == 2, tipo=tipo,
                     anio_ref=anios[0])


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
        "trafico": trafico(pg, org["trafico"], tipo),
        "mes": ultimo_mes(pg, org["mes"], tipo), "referencias": referencias(pg, org["referencias"], tipo),
        "alertas": pal.bloque_rol(pg, org["alertas"], "marketing", tipo),
    }
