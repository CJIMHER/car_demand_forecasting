"""Vista «Marketing» (docs/prototipo/Marketing.dc.html y tres bloques nuevos), por tipo: Todo, VN o VO.

Lee panel_campana_dia (Meta), panel_leads_dia (Walcu), panel_campana_salud,
panel_recomendacion_modelo, panel_atencion_relativa, panel_semana_campana,
panel_campana_resultado, panel_trafico_canal_dia (GA4), panel_campana_semana y panel_referencia_meta (Meta
y Walcu por semana; ingesta/referencias_meta.py) y, en VO, panel_riesgo_vehiculo (contrato). Solo sumas y
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
from panel import mensajes as msj
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
AMBITOS = {"VN": "Nuevos (VN)", "VO": "Ocasión (VO)", "ambos": "VN y VO"}
SIN_MERCADO = "sin datos de mercado"
COMUN = "gasto común a VN y VO"
# Reglas cuyo texto de «revisar» es una lista de cosas (como en sus avisos de alertas/reglas.yaml): llevan
# «Revisar:» delante; las demás (b, c) ya son una frase completa
CON_REVISAR = ("a", "d", "f")


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


def _walcu_vida(pg, origen, tipo: str) -> dict[str, list]:
    """Walcu semanal de cada campaña (panel_campana_semana, último cálculo), semana a semana, las semanas con
    carga de Walcu: [(semana, gasto €, leads, citas, ventas, citas completas, ventas completas, casa)]. Con `tipo`
    VN o VO, los leads, citas y ventas de ese tipo. Las mismas filas que las reglas (alertas/criterios.py,
    walcu_semana): una campaña sin ninguna semana que case no tiene datos de Walcu."""
    dia = ng.ultimo_dia(pg, "panel_campana_semana", origen)
    if dia is None:
        return {}
    suf = "" if tipo == "todo" else f"_{tipo.lower()}"
    salida: dict[str, list] = defaultdict(list)
    for cid, semana, gasto, leads, citas, ventas, c_ok, v_ok, casa in pg.execute(
            f"""SELECT campana_id, semana, gasto_cents / 100.0, coalesce(leads{suf}, 0), coalesce(citas{suf}, 0),
                       coalesce(ventas{suf}, 0), citas_completas, ventas_completas, cruce IS NOT NULL
                FROM panel_campana_semana WHERE origen = %s AND dia = %s AND walcu AND campana_id IS NOT NULL""",
            (origen, dia)):
        salida[cid].append((semana, float(gasto), int(leads), int(citas), int(ventas), bool(c_ok), bool(v_ok),
                            bool(casa)))
    return salida


def _plazos() -> tuple[int, int]:
    """Días para tener las citas y las ventas completas (cohorte de config/campanas_walcu.yaml)."""
    from ingesta import referencias_meta as ref
    try:
        plazos = ref.cargar()
        return plazos.dias_cita, plazos.dias_venta
    except (OSError, ref.ErrorCruce):
        return 14, 60


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


def _resultado(pg, origen, tipo: str, cids: list[str], desde: date | None, hasta: date | None,
               walcu: dict[str, list], walcu_tipo: dict[str, list]) -> dict:
    """Gasto, leads, citas y ventas de unas campañas, como las tarjetas de campaña y las reglas: Walcu por semanas
    (las semanas de [desde, hasta] con carga que casa) y, de las que no casan, los leads según Meta. Los costes,
    con el gasto de las semanas con Walcu; «provisional» si alguna semana con leads aún puede sumar citas o
    ventas. Sin `desde`, toda la vida de cada campaña."""
    ambito = _ambitos(pg, origen)
    r = {"gasto": 0.0, "gasto_walcu": 0.0, "leads": 0, "citas": 0, "ventas": 0, "leads_meta": 0, "segun_meta": 0,
         "con_walcu": 0, "citas_prov": False, "ventas_prov": False}
    for cid in cids:
        comun = tipo != "todo" and ambito.get(cid) == "ambos"
        gasto, leads_meta = pg.execute(
            """SELECT coalesce(sum(gasto_cents), 0) / 100.0, sum(leads_meta) FROM panel_campana_dia
               WHERE origen = %s AND campana_id = %s AND (%s::date IS NULL OR dia >= %s) AND (%s::date IS NULL OR dia <= %s)""",
            (origen, cid, desde, desde, hasta, hasta)).fetchone()
        lunes_ini = desde - timedelta(days=desde.weekday()) if desde else None
        semanas = [s for s in (walcu_tipo if comun else walcu).get(cid, [])
                   if (lunes_ini is None or s[0] >= lunes_ini) and (hasta is None or s[0] <= hasta)]
        if not comun:
            r["gasto"] += float(gasto)
        if any(s[7] for s in semanas):
            r["con_walcu"] += 1
            if not comun:
                r["gasto_walcu"] += sum(s[1] for s in semanas)
            for i, k in ((2, "leads"), (3, "citas"), (4, "ventas")):
                r[k] += sum(s[i] for s in semanas)
            r["citas_prov"] |= any(not s[5] and s[2] for s in semanas)
            r["ventas_prov"] |= any(not s[6] and s[2] for s in semanas)
        elif leads_meta is not None:
            r["segun_meta"] += 1
            r["leads_meta"] += int(leads_meta)
    return r


def kpis(pg, origen, tipo: str = "todo") -> dict:
    """Últimos 28 días: inversión de Meta y, como las tarjetas de campaña y las reglas, leads, citas y ventas de
    Walcu por semanas de las campañas que casan (de las que no, los leads según Meta) y sus costes por lead, cita
    y venta, «provisional» si las semanas aún no tienen las citas o las ventas completas. En VN o VO, solo las
    campañas de ese ámbito (sin las de ambos, cuyo gasto no se reparte)."""
    v = _ventana(pg, origen)
    if v is None:
        return ng.bloque(origen, hay=False)
    ini, fin, ini_ant, fin_ant = v
    ambitos = ("VN", "VO", "ambos") if tipo == "todo" else (tipo,)
    gasto, activas = pg.execute(
        """SELECT coalesce(sum(gasto_cents), 0) / 100.0, count(DISTINCT campana_id) FILTER (WHERE dia = %s)
           FROM panel_campana_dia WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = ANY(%s)""",
        (fin, origen, ini, fin, list(ambitos))).fetchone()
    comunes = 0 if tipo == "todo" else pg.execute(
        """SELECT count(DISTINCT campana_id) FROM panel_campana_dia
           WHERE origen = %s AND dia BETWEEN %s AND %s AND ambito = 'ambos'""", (origen, ini, fin)).fetchone()[0]
    sin_comun = f" · {_sin_comunes(comunes)}" if comunes else ""
    tarjetas = [{"etiqueta": "Inversión" if tipo == "todo" else f"Inversión {tipo}", "valor": _euros(gasto),
                 "sub": f"{activas} campaña{'s' if activas != 1 else ''} activa{'s' if activas != 1 else ''}"
                        + sin_comun}]
    walcu = _walcu_vida(pg, origen, "todo")
    walcu_t = walcu if tipo == "todo" else _walcu_vida(pg, origen, tipo)

    def de(desde, hasta):
        cids = [c for (c,) in pg.execute(
            """SELECT DISTINCT campana_id FROM panel_campana_dia WHERE origen = %s AND dia BETWEEN %s AND %s
               AND gasto_cents > 0 AND ambito = ANY(%s)""", (origen, desde, hasta, list(ambitos)))]
        return _resultado(pg, origen, tipo, cids, desde, hasta, walcu, walcu_t)
    r, r_ant = de(ini, fin), de(ini_ant, fin_ant)
    if r["con_walcu"] or r["segun_meta"]:
        if r["con_walcu"]:
            l, base = r["leads"], r["gasto_walcu"]
            meta = f" · más {fmt.miles(r['leads_meta'])} según Meta (sin cruce con Walcu)" if r["segun_meta"] else ""
            sub_l = f"{fmt.miles(r['citas'])} citas · {fmt.miles(r['ventas'])} ventas" + meta
            cpl, cpl_ant = _div(base, l), (_div(r_ant["gasto_walcu"], r_ant["leads"]) if r_ant["con_walcu"] else None)
        else:
            l, base = r["leads_meta"], float(gasto)
            sub_l = "según Meta (sin cruce con Walcu): sin citas ni ventas"
            cpl, cpl_ant = _div(base, l), (_div(r_ant["gasto"], r_ant["leads_meta"]) if r_ant["segun_meta"] else None)
        comp = (f"{fmt.porcentaje((cpl / cpl_ant - 1) * 100, 0, signo=True)} frente a los {VENTANA} días anteriores"
                if cpl is not None and cpl_ant else f"Sin datos de los {VENTANA} días anteriores")
        tarjetas.append({"etiqueta": "Leads", "valor": fmt.miles(l), "sub": sub_l})
        tarjetas.append({"etiqueta": "Coste por lead", "valor": _euros(cpl), "sub": comp})
        if r["con_walcu"]:
            tarjetas.append({"etiqueta": "Coste por cita", "valor": _euros(_div(base, r["citas"])),
                             "sub": "provisional: las citas de las últimas semanas aún pueden crecer"
                             if r["citas_prov"] else "Con las citas de Walcu"})
            tarjetas.append({"etiqueta": "Coste por venta", "valor": _euros(_div(base, r["ventas"])),
                             "sub": "provisional: las ventas de las últimas semanas aún pueden crecer"
                             if r["ventas_prov"] else "Lo que de verdad importa"})
        else:
            for e in ("Coste por cita", "Coste por venta"):
                tarjetas.append({"etiqueta": e, "valor": fmt.GUION, "sub": "Sin carga de Walcu que case",
                                 "sin_dato": True})
    else:
        for e in ("Leads", "Coste por lead", "Coste por cita", "Coste por venta"):
            tarjetas.append({"etiqueta": e, "valor": fmt.GUION, "sub": "Sin datos todavía", "sin_dato": True})
    nota = (f"Solo las campañas de {AMBITOS[tipo]}, {_sin_comunes(comunes)}: su gasto es común a VN y VO "
            f"y no se reparte." if comunes else "")
    return ng.bloque(origen, tarjetas=tarjetas, desde=fmt.fecha_corta(ini), hasta=fmt.fecha_corta(fin), nota=nota)


def campanas(pg, origen, orden: str, tipo: str = "todo", ciudad: str = "") -> dict:
    v = _ventana(pg, origen)
    if v is None:
        return ng.bloque(origen, hay=False, orden=orden)
    ini, fin, _, _ = v
    # Leads, citas y ventas: Walcu semanal, sobre toda la vida de la campaña (las mismas semanas que las reglas)
    walcu = _walcu_vida(pg, origen, "todo")
    walcu_tipo = _walcu_vida(pg, origen, tipo) if tipo != "todo" else walcu
    hay_leads = any(s[7] for v in walcu.values() for s in v)
    vida = {cid: (float(g), None if n_meta is None else int(n_meta)) for cid, g, n_meta in pg.execute(
        """SELECT campana_id, sum(gasto_cents) / 100.0, sum(leads_meta) FROM panel_campana_dia
           WHERE origen = %s AND dia <= %s GROUP BY campana_id""", (origen, fin))}
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
        # Gasto de toda la vida de la campaña; los costes, con el de las semanas con carga de Walcu (como las reglas)
        gasto, leads_meta = vida.get(cid, (float(gasto), None))
        # Una de ambos vista en VN o VO: sus leads de ese tipo y ningún coste (el gasto no se reparte)
        comun = tipo != "todo" and ambito == "ambos"
        lunes_inicio = inicio - timedelta(days=inicio.weekday())
        semanas = [s for s in (walcu_tipo if comun else walcu).get(cid, []) if s[0] >= lunes_inicio]
        segun_meta = citas_prov = ventas_prov = False
        if any(s[7] for s in semanas):
            gasto_base = sum(s[1] for s in semanas)
            l, c, ve = (sum(s[i] for s in semanas) for i in (2, 3, 4))
            # Provisional: una semana con leads que aún puede sumar citas (o ventas); sin leads, no crece
            citas_prov = any(not s[5] and s[2] for s in semanas)
            ventas_prov = any(not s[6] and s[2] for s in semanas)
        else:                                   # sin carga de Walcu que case: los leads según Meta
            gasto_base, l, c, ve = gasto, leads_meta, None, None
            segun_meta = leads_meta is not None
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
        coste = (lambda n: None) if comun else (lambda n: _div(gasto_base, n) if n else None)
        decision, reglas = decisiones.get(cid, (None, []))
        criterios = [f"{NOMBRE_REGLA[r['regla']]}: {r['texto']}"
                     + (f". {'Revisar: ' if r['regla'] in CON_REVISAR else ''}{r['revisar']}" if r.get("revisar") else "")
                     + ("" if r["estado"] in ("activa", "no_aplica") else " (no salta)")
                     for r in reglas if r["estado"] in ("activa", "sin_volumen") and r["regla"] != "e"
                     or r["estado"] == "no_aplica"]
        mensaje = msj.de_campana(reglas, decision, ciudad) if decision else None
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
            "segun_meta": segun_meta, "citas_prov": citas_prov, "ventas_prov": ventas_prov,
            # La decisión sale de los criterios de Meta; la salud frente a su referencia es solo detalle
            "decision": decision, "decision_texto": DECISIONES[decision] if decision else "Sin decidir todavía",
            # Para todos: diagnóstico y acciones; criterios (cifras frente a umbrales) y salud, solo para técnico
            "mensaje": mensaje.json() if mensaje else None,
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
    dias_cita, dias_venta = _plazos()
    return ng.bloque(origen, filas=filas, orden=orden, hay_leads=hay_leads, hay_salud=bool(salud_dia), otras=otras,
                     dias_cita=dias_cita, dias_venta=dias_venta,
                     hay_decision=bool(decisiones),
                     tipo=tipo, hay_comun=any(f["comun"] for f in filas),
                     ordenes=[{"id": o, "etiqueta": e, "activa": o == orden, "url": f"/marketing?tipo={tipo}&orden={o}"}
                              for o, e in (("venta", "Por coste por venta"), ("gasto", "Por gasto"))])


def mensaje_recomendacion(accion: str, regla: str | None, cifras: dict | None, ciudad: str) -> msj.Mensaje | None:
    """Diagnóstico y acciones de la recomendación de un modelo: la de su campaña o, sin ella, lanzar o no."""
    c = cifras or {}
    if regla == "e" or accion in ("lanzar", "no_lanzar"):
        return msj.componer(msj.situacion_e(accion, c, c.get("estado")), ciudad) if c else None
    if "activas" in c or "reglas" in c:
        return msj.de_campana(c.get("activas", c.get("reglas")) or [], c.get("decision") or
                              {"aumentar": "subir", "mantener": "continuar"}.get(accion, accion), ciudad)
    return None


def recomendacion(pg, origen, tipo: str = "todo", ciudad: str = "") -> dict:
    """VN: por modelo del stock nuevo; VO: del de ocasión; Todo: las dos, cada una con su etiqueta. Cada una con
    su diagnóstico y sus acciones; el motivo con las cifras de las reglas, solo para técnico (sin mensaje, el
    motivo para todos: filas de ejemplo antiguas)."""
    dia = ng.ultimo_dia(pg, "panel_recomendacion_modelo", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    grupos = {k: [] for k, _ in ACCIONES}
    for tipo_r, modelo, accion, motivo, regla, cifras in pg.execute(
            """SELECT tipo, modelo, accion, motivo, regla, cifras FROM panel_recomendacion_modelo
               WHERE origen = %s AND dia = %s AND (%s = 'todo' OR tipo = %s)
               ORDER BY prioridad NULLS LAST, modelo, tipo""", (origen, dia, tipo, tipo)):
        m = mensaje_recomendacion(accion, regla, cifras, ciudad)
        grupos[COLUMNA.get(accion, accion)].append({"modelo": modelo, "motivo": motivo,
                                                     "mensaje": m.json() if m else None,
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
            "modelo": m, "texto": ("+" if round(p, decimales) > 0 else "") + texto, "interes": msj.interes(p),
            "pos": round(max(p, 0) / tope * 100, 1), "neg": round(max(-p, 0) / tope * 100, 1),
            "detalle": (f"Cuota de búsquedas: {fmt.porcentaje(float(cuota), 1)}, frente a "
                        f"{fmt.porcentaje(float(antes), 1)} en las 4 semanas anteriores · {n_comp} competidores"
                        + (f" en {n_cmp} comparaciones" if n_cmp and n_cmp > 1 else "")) if cuota is not None else ""})
    return ng.bloque(origen, mes=f"{fmt.MESES[mes.month - 1]} de {mes.year}", filas=salida,
                     entrega=fmt.fecha(entrega) if entrega else None,
                     semana_fin=fmt.fecha_corta(semana_fin + timedelta(days=6)) if semana_fin else None)


PASADAS_MAX = 10


def pasadas(pg, origen, tipo: str = "todo") -> dict:
    """Campañas terminadas (sin gasto en el último día de datos de Meta), de la más reciente a la más antigua:
    fechas, gasto, leads, citas, ventas y costes de toda su vida, como las tarjetas de campaña. En VN o VO, las de
    ese ámbito y las de ambos con sus leads, citas y ventas de ese tipo (sin costes: su gasto no se reparte). Sin
    comparación con lo que habría pasado sin la campaña: no hay modelo que lo calcule todavía."""
    fin_datos = ng.ultimo_dia(pg, "panel_campana_dia", origen)
    if fin_datos is None:
        return ng.bloque(origen, hay=False)
    terminadas = pg.execute(
        """SELECT campana_id, max(titulo), max(ambito), min(dia), max(dia) FILTER (WHERE gasto_cents > 0)
           FROM panel_campana_dia WHERE origen = %s AND ambito = ANY(%s) GROUP BY campana_id
           HAVING max(dia) FILTER (WHERE gasto_cents > 0) < %s ORDER BY 5 DESC, 1 LIMIT %s""",
        (origen, list(_de_tipo(tipo)), fin_datos, PASADAS_MAX)).fetchall()
    walcu = _walcu_vida(pg, origen, "todo")
    walcu_t = walcu if tipo == "todo" else _walcu_vida(pg, origen, tipo)
    filas = []
    for cid, titulo, ambito, inicio, fin in terminadas:
        comun = tipo != "todo" and ambito == "ambos"
        r = _resultado(pg, origen, tipo, [cid], None, None, walcu, walcu_t)
        gasto = float(pg.execute("SELECT coalesce(sum(gasto_cents), 0) / 100.0 FROM panel_campana_dia "
                                 "WHERE origen = %s AND campana_id = %s", (origen, cid)).fetchone()[0])
        con_walcu = bool(r["con_walcu"])
        coste = (lambda n: None) if comun or not con_walcu else (lambda n: _div(r["gasto_walcu"], n) if n else None)
        filas.append({
            "nombre": titulo, "ambito": AMBITOS[ambito], "fechas": f"{fmt.fecha(inicio)} – {fmt.fecha(fin)}",
            "gasto": _euros(gasto) + (f" ({COMUN})" if comun else ""), "de_tipo": f" {tipo}" if comun else "",
            "leads": fmt.miles(r["leads"] if con_walcu else r["leads_meta"] if r["segun_meta"] else None),
            "segun_meta": not con_walcu and bool(r["segun_meta"]),
            "citas": fmt.miles(r["citas"]) if con_walcu else fmt.GUION,
            "ventas": fmt.miles(r["ventas"]) if con_walcu else fmt.GUION,
            "citas_prov": r["citas_prov"], "ventas_prov": r["ventas_prov"],
            "cpl": _euros(coste(r["leads"])), "cpc": _euros(coste(r["citas"])), "cpv": _euros(coste(r["ventas"]))})
    return ng.bloque(origen, hay=bool(filas), filas=filas, tipo=tipo)


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


SEMANAS_BLOQUE = 4


def ultimas_semanas(pg, origen, tipo: str = "todo") -> dict:
    """Campañas de Meta de las últimas 4 semanas completas (de lunes a domingo, terminadas el último día con
    datos de Meta): gasto, leads (de Walcu y según Meta), citas, ventas y coste por lead, por cita y por
    venta, con la decisión de los criterios de Meta. Cohorte: las citas y ventas van a la semana del lead; el
    coste por cita solo usa las semanas con las citas completas y el de venta, las de ventas completas
    (config/campanas_walcu.yaml); la tabla de semanas marca las incompletas. Las de ambos, en VN o VO, con
    sus leads, citas y ventas de ese tipo y sin costes (su gasto no se reparte)."""
    dia = ng.ultimo_dia(pg, "panel_campana_semana", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    ultima = dia - timedelta(days=dia.weekday()) - timedelta(days=0 if dia.weekday() == 6 else 7)
    semanas_b = [ultima - timedelta(days=7 * i) for i in range(SEMANAS_BLOQUE - 1, -1, -1)]
    hay_gasto = pg.execute("SELECT EXISTS (SELECT 1 FROM panel_campana_semana WHERE origen = %s AND dia = %s "
                           "AND semana = ANY(%s) AND gasto_cents > 0)", (origen, dia, semanas_b)).fetchone()[0]
    if not hay_gasto:
        return ng.bloque(origen, hay=False)
    decision_dia = ng.ultimo_dia(pg, "panel_campana_decision", origen)
    decisiones = dict(pg.execute("SELECT campana_id, decision FROM panel_campana_decision WHERE origen = %s AND dia = %s",
                                 (origen, decision_dia)).fetchall()) if decision_dia else {}
    sufijo = "" if tipo == "todo" else f"_{tipo.lower()}"
    # Estado de cada semana (lo mismo en todas sus filas) y sus totales de Walcu
    estado = {s: (False, None, None) for s in semanas_b}
    por_semana = {s: [0, 0, 0] for s in semanas_b}
    campanas_b: dict[str | None, dict] = {}
    for (semana, cid, titulo, ambito, tipo_campana, modelo, gasto, leads_meta, hay_walcu, cruce, c_ok, v_ok,
         l, c, v) in pg.execute(
            f"""SELECT semana, campana_id, titulo, ambito, tipo_campana, modelo, gasto_cents, leads_meta, walcu,
                       cruce, citas_completas, ventas_completas, leads{sufijo}, citas{sufijo}, ventas{sufijo}
                FROM panel_campana_semana WHERE origen = %s AND dia = %s AND semana = ANY(%s)
                ORDER BY semana, titulo""", (origen, dia, semanas_b)):
        if hay_walcu:
            estado[semana] = (True, c_ok, v_ok)
        if cid is not None and tipo != "todo" and ambito not in (tipo, "ambos"):
            continue
        if cruce is not None:
            for i, n in enumerate((l, c, v)):
                por_semana[semana][i] += int(n or 0)
        x = campanas_b.setdefault(cid if cid is not None else ("sin", titulo), {
            "cid": cid, "titulo": titulo, "ambito": ambito, "tipo_campana": tipo_campana, "modelo": modelo,
            "gasto": 0, "leads_meta": None, "cruce": False, "leads": 0, "citas": 0, "ventas": 0,
            "gasto_c": 0, "citas_c": 0, "gasto_v": 0, "ventas_v": 0})
        x["titulo"] = titulo
        x["gasto"] += int(gasto)
        if leads_meta is not None:
            x["leads_meta"] = (x["leads_meta"] or 0) + int(leads_meta)
        if cruce is not None:
            x["cruce"] = True
            x["leads"] += int(l or 0)
            x["citas"] += int(c or 0)
            x["ventas"] += int(v or 0)
            if c_ok:
                x["gasto_c"] += int(gasto)
                x["citas_c"] += int(c or 0)
            if v_ok:
                x["gasto_v"] += int(gasto)
                x["ventas_v"] += int(v or 0)
    walcu = any(e[0] for e in estado.values())
    filas, sin_cruce = [], 0
    tot = defaultdict(int)
    for x in sorted(campanas_b.values(), key=lambda x: (-x["gasto"], x["titulo"])):
        if x["cid"] is None:            # leads de Facebook en Walcu sin campaña de Meta
            sin_cruce += x["leads"]
            continue
        if not x["gasto"] and not x["leads"]:
            continue
        comun = tipo != "todo" and x["ambito"] == "ambos"
        con_walcu = x["cruce"]
        gasto = x["gasto"] / 100

        def coste(g, n, ok=con_walcu and not comun):
            return _euros2(_div(g / 100, n)) if ok and n else fmt.GUION
        if not comun:
            tot["gasto"] += x["gasto"]
            tot["leads_meta"] += x["leads_meta"] or 0
        if con_walcu:
            for k in ("leads", "citas", "ventas"):
                tot[k] += x[k]
            if not comun:
                for k in ("gasto_c", "citas_c", "gasto_v", "ventas_v"):
                    tot[k] += x[k]
                tot["gasto_l"] += x["gasto"]
        decision = decisiones.get(x["cid"])
        ambito, tipo_campana, modelo = x["ambito"], x["tipo_campana"], x["modelo"]
        filas.append({
            "nombre": x["titulo"], "ambito": ambito,
            "nota": (AMBITOS[ambito] if ambito else "Sin clasificar")
                    + (f" · {modelo}" if tipo_campana == "modelo" and modelo else " · General" if tipo_campana else "")
                    + (f" · leads, citas y ventas de {tipo} · {COMUN}" if comun else "")
                    + ("" if con_walcu or not walcu else " · no casa con ningún lead de Walcu"),
            "gasto": _euros2(gasto), "leads": fmt.miles(x["leads"]) if con_walcu else fmt.GUION,
            "leads_meta": fmt.miles(x["leads_meta"]) if x["leads_meta"] is not None else fmt.GUION,
            "citas": fmt.miles(x["citas"]) if con_walcu else fmt.GUION,
            "ventas": fmt.miles(x["ventas"]) if con_walcu else fmt.GUION,
            "cpl": coste(x["gasto"], x["leads"]), "cpc": coste(x["gasto_c"], x["citas_c"]),
            "cpv": coste(x["gasto_v"], x["ventas_v"]),
            "decision": decision, "decision_texto": DECISIONES[decision] if decision else "Sin decidir",
            "alerta": decision in ("revisar", "parar"), "comun": comun})

    def coste_total(g, n):
        return _euros2(_div(g / 100, n)) if walcu and n else fmt.GUION
    total = {"gasto": _euros2(tot["gasto"] / 100), "leads": fmt.miles(tot["leads"]) if walcu else fmt.GUION,
             "leads_meta": fmt.miles(tot["leads_meta"]), "citas": fmt.miles(tot["citas"]) if walcu else fmt.GUION,
             "ventas": fmt.miles(tot["ventas"]) if walcu else fmt.GUION,
             "cpl": coste_total(tot["gasto_l"], tot["leads"]), "cpc": coste_total(tot["gasto_c"], tot["citas_c"]),
             "cpv": coste_total(tot["gasto_v"], tot["ventas_v"])}
    lista = []
    for s in semanas_b:
        cargada, c_ok, v_ok = estado[s]
        texto = ("Sin carga de Walcu" if not cargada else "Completa" if c_ok and v_ok
                 else "Ventas incompletas" if c_ok else "Citas y ventas incompletas")
        n = por_semana[s]
        lista.append({"semana": f"{fmt.fecha_corta(s)}–{fmt.fecha_corta(s + timedelta(days=6))}",
                      "leads": fmt.miles(n[0]) if cargada else fmt.GUION,
                      "citas": fmt.miles(n[1]) if cargada else fmt.GUION,
                      "ventas": fmt.miles(n[2]) if cargada else fmt.GUION,
                      "estado": texto, "incompleta": cargada and not (c_ok and v_ok), "sin_carga": not cargada})
    dias_cita, dias_venta = _plazos()
    nombre = f"{fmt.fecha_corta(semanas_b[0])} – {fmt.fecha_corta(semanas_b[-1] + timedelta(days=6))}"
    sin_carga = [x["semana"] for x in lista if x["sin_carga"]]
    return ng.bloque(origen, filas=filas, total=total, mes=nombre, walcu=walcu, tipo=tipo,
                     sin_cruce=fmt.miles(sin_cruce) if sin_cruce else "", hasta=fmt.fecha_corta(dia),
                     hay_comun=any(f["comun"] for f in filas), semanas=lista, sin_carga=", ".join(sin_carga),
                     dias_cita=dias_cita, dias_venta=dias_venta)


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
    columnas = [f"{a} ({filas_ref[a]['semanas']} semanas)" for a in anios]

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
    incompletas = [f"{a}: {x['semanas_citas']} de {x['semanas']} semanas con las citas completas y "
                   f"{x['semanas_ventas']} con las ventas completas"
                   for a, x in ((a, filas_ref[a]) for a in anios)
                   if x["semanas_citas"] < x["semanas"] or x["semanas_ventas"] < x["semanas"]]
    return ng.bloque(origen, columnas=columnas, filas=filas, dos=len(ms) == 2, tipo=tipo,
                     anio_ref=anios[0], incompletas="; ".join(incompletas))


SEMANAS_PREVISION = 4


def datos(pg: psycopg.Connection, conc: Concesionario, modos: dict, orden: str, hoy: date,
          tipo: str = "todo", gasto: int = 0, objetivos: dict | None = None, ejemplo: bool = True) -> dict:
    """`gasto`: escenario de gasto de la previsión (% sobre el previsto); `objetivos`: citas objetivo por tipo
    para «cuánto gasto haría falta». `ejemplo`: modo ejemplo explícito; sin él, solo filas reales."""
    from panel import campana_general as cg
    from panel import prevision as pp
    org = contrato.origenes(modos, "marketing", ejemplo)
    ocasion_b = ocasion(pg, org["ocasion"], conc) if tipo == "VO" else None
    if ocasion_b is not None and not ocasion_b.get("hay"):
        ocasion_b["pendiente"] = "stock interno (API de Walcu)"
    return {
        "prevision": pp.marketing(pg, org["prevision"], tipo, gasto, objetivos or {}, SEMANAS_PREVISION,
                                  f"/marketing?tipo={tipo}&orden={orden}", "#marketing"),
        "tipo": tipo, "orden": orden, "tipos": ng.selector_tipo("/marketing", tipo, "#marketing", f"&orden={orden}"),
        "kpis": kpis(pg, org["kpis"], tipo), "recomendacion": recomendacion(pg, org["recomendacion"], tipo, conc.ciudad),
        "campanas": campanas(pg, org["campanas"], orden, tipo, conc.ciudad),
        "atencion": atencion(pg, org["atencion"]) if tipo != "VO" else None,
        "ocasion": ocasion_b,
        "campana_general": cg.bloque(pg, org["campana_general"], tipo),
        "pasadas": pasadas(pg, org["pasadas"], tipo), "origenes": org,
        "trafico": trafico(pg, org["trafico"], tipo),
        "mes": ultimas_semanas(pg, org["mes"], tipo), "referencias": referencias(pg, org["referencias"], tipo),
        "alertas": pal.bloque_rol(pg, org["alertas"], "marketing", tipo),
    }
