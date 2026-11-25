"""Vista «Ventas» (docs/prototipo/Ventas.dc.html): vehículos en stock ordenados por riesgo.

Sin el modo ejemplo explícito (PANEL_MODO_EJEMPLO=1), solo datos reales (contrato.VISTAS_SOLO_REAL). La
probabilidad de venta a 90 días (panel_riesgo_vehiculo) aún no tiene modelo: sin filas reales, la lista dice
«Pendiente: modelo de probabilidad de venta (necesita el stock del CRM)» y las tarjetas de arriba salen de fuentes
reales: nuestro stock publicado (Mercado), el precio provisional (panel_precio_vo), las salidas del mes
(panel_salidas_stock) y los leads y citas de Walcu por semanas (panel_campana_semana).

Lee panel_riesgo_vehiculo y panel_curva_grupo (contrato), el precio provisional de nuestros VO
(panel_precio_vo) y las salidas de nuestro stock de las últimas 4 semanas (panel_salidas_stock). Las probabilidades, curvas y medianas
llegan ya calculadas, con su p10–p90, su versión de modelo y su n; aquí solo se ordenan y se escriben.
"""

from __future__ import annotations

from datetime import date
from urllib.parse import quote

import psycopg

from ingesta.concesionario import Concesionario
from panel import alertas as pal
from panel import contrato
from panel import direccion as dr
from panel import formato as fmt
from panel import negocio as ng
from panel import precio as pr
from panel import salidas as sal

POR_PAGINA = 25
PENDIENTE_RIESGO = "modelo de probabilidad de venta (necesita el stock del CRM)"
SEMANAS_WALCU = 4


def _tarjeta(valor: str, texto: str) -> tuple[str, str]:
    return valor, texto


def _pendiente(fuente: str) -> tuple[str, str]:
    return fmt.GUION, f"Pendiente de conectar: {fuente}"


def tarjetas_reales(pg: psycopg.Connection, org: dict, tipo: str, hoy: date) -> dict:
    """Tarjetas de arriba con lo que ya tiene fuente real (sin el modelo de probabilidad de venta)."""
    tarjetas = []
    pub = dr.publicado(pg, tipo)
    if pub["hay"]:
        tarjetas.append(_tarjeta(pub["valor"], f"{pub['etiqueta'].lower()} en la web"
                                 + (f" ({pub['sub'].split(' · ')[0]})" if tipo == "todo" else "")))
    else:
        tarjetas.append(_pendiente("stock publicado (scrapers)"))
    if tipo != "VN":
        dia = ng.ultimo_dia(pg, pr.TABLA, contrato.REAL)
        if dia is None:
            tarjetas.append(_pendiente("precio provisional de nuestros VO"))
        else:
            n = pg.execute(f"SELECT count(*) FROM {pr.TABLA} WHERE origen = 'real' AND dia = %s AND orden IN (1, 2)",
                           (dia,)).fetchone()[0]
            tarjetas.append(_tarjeta(fmt.miles(n), "VO con el precio a bajar o revisar frente al mercado"))
    kpi, _ = sal.mensual(pg, contrato.REAL if org.get("salidas") == contrato.REAL else None, tipo, hoy)
    tarjetas.append(_tarjeta(kpi["valor"], kpi["etiqueta"].lower() + " (estimación de ventas)") if kpi.get("hay")
                    else _pendiente("salidas de nuestro stock publicado"))
    suf = "" if tipo == "todo" else f"_{tipo.lower()}"
    fila = pg.execute(
        f"""SELECT count(DISTINCT semana), coalesce(sum(leads{suf}), 0), coalesce(sum(citas{suf}), 0)
            FROM panel_campana_semana WHERE origen = 'real' AND walcu AND dia = (
                SELECT max(dia) FROM panel_campana_semana WHERE origen = 'real')
            AND semana >= (SELECT max(semana) FROM panel_campana_semana WHERE origen = 'real' AND walcu) - %s""",
        (7 * (SEMANAS_WALCU - 1),)).fetchone()
    if fila and fila[0]:
        tarjetas.append(_tarjeta(fmt.miles(fila[1]), f"leads de las campañas de Meta en {fila[0]} semanas, con "
                                                     f"{fmt.miles(fila[2])} citas (Walcu)"))
    else:
        tarjetas.append(_pendiente("leads y citas de Walcu por semanas"))
    hay = any(v != fmt.GUION for v, _ in tarjetas)
    return ng.bloque(contrato.REAL if hay else None, hay=True, tarjetas=tarjetas)
# Botones del detalle: acción → (texto del botón, quién recibe el aviso, cómo se dice hecha)
ACCIONES = {"campana": ("Pedir campaña a marketing", "marketing", "Campaña pedida a marketing"),
            "revisar_precio": ("Revisar precio", "ventas", "Revisión de precio pedida")}
BLOQUEO_DIAS = 7
ENVIO = {"enviado": "aviso enviado", "error": "el aviso no se pudo enviar", None: "aviso en cola (sale en un minuto)"}
SUGERENCIAS = {"campana": "Campaña", "revisar_precio": "Revisar precio", "mantener": "Mantener"}
COLS = ("stock_id", "tipo", "modelo", "version", "detalle", "dias_exposicion", "grupo", "p_venta90_p10",
        "p_venta90_p50", "p_venta90_p90", "precio_mercado_pct", "similares_n", "campana_activa", "sugerencia",
        "leads_14", "leads_14_previos", "modelo_version", "n")


def _fila(v: dict) -> dict:
    p50 = float(v["p_venta90_p50"])
    nombre = f"{v['modelo']} {v['version']}" if v["version"] else v["modelo"]
    return {
        "stock_id": v["stock_id"], "nombre": nombre, "detalle": v["detalle"] or "", "tipo": v["tipo"],
        "tipo_largo": "Nuevo" if v["tipo"] == "VN" else "Ocasión", "dias": fmt.miles(v["dias_exposicion"]),
        "dias_n": v["dias_exposicion"], "p": fmt.porcentaje(p50 * 100, 0),
        "p_intervalo": ng.intervalo_pct(float(v["p_venta90_p10"]), float(v["p_venta90_p90"])),
        "p_ancho": round(p50 * 100, 1), "riesgo": p50 < 0.5,
        "precio": ng.signo_pct(None if v["precio_mercado_pct"] is None else float(v["precio_mercado_pct"])),
        "sugerencia": SUGERENCIAS[v["sugerencia"]], "sugerencia_clase": v["sugerencia"],
    }


def datos(pg: psycopg.Connection, conc: Concesionario, modos: dict, tipo: str, solo_riesgo: bool,
          sel: str | None, hoy: date, mostrar: int = POR_PAGINA, modelo_vo: str | None = None,
          ejemplo: bool = True) -> dict:
    """`mostrar`: cuántas filas de la lista se pintan (de 25 en 25; siempre incluye el vehículo elegido).
    Los filtros y el resumen se aplican a todos los vehículos, no solo a los mostrados. `modelo_vo`: filtro
    por modelo del bloque «Precio de nuestros VO»; viaja en todos los enlaces de la vista. `ejemplo`: modo
    ejemplo explícito; sin él, solo filas reales."""
    org = contrato.origenes(modos, "ventas", ejemplo)
    riesgo = "&riesgo=1" if solo_riesgo else ""
    extra_vo = f"&{pr.PARAMETRO}={quote(modelo_vo, safe='')}" if modelo_vo else ""
    extra = riesgo + extra_vo
    base = {"tipo": tipo, "solo_riesgo": solo_riesgo, "tipos": ng.selector_tipo("/ventas", tipo, "#ventas", extra),
            "url_riesgo": f"/ventas?tipo={tipo}{'' if solo_riesgo else '&riesgo=1'}{extra_vo}", "origenes": org}

    def leer(origen):
        dia = ng.ultimo_dia(pg, "panel_riesgo_vehiculo", origen)
        if dia is None:
            return None, []
        cur = pg.execute(f"""SELECT {', '.join(COLS)} FROM panel_riesgo_vehiculo WHERE origen = %s AND dia = %s
                             ORDER BY p_venta90_p50, dias_exposicion DESC, stock_id""", (origen, dia))
        return dia, [dict(zip(COLS, f)) for f in cur]

    # Resumen y lista (misma tabla, mismo origen)
    dia, todos = leer(org["lista"])
    del_tipo = [v for v in todos if tipo == "todo" or v["tipo"] == tipo]
    if todos:
        resumen = ng.bloque(org["resumen"], tarjetas=[
            (sum(1 for v in del_tipo if float(v["p_venta90_p50"]) < 0.5), "con menos del 50 % de venderse en 90 días"),
            (sum(1 for v in del_tipo if v["dias_exposicion"] > 90), "llevan más de 90 días"),
            (sum(1 for v in del_tipo if v["campana_activa"]), "tienen ya una campaña activa"),
        ])
    elif org["lista"] is None:
        resumen = tarjetas_reales(pg, org, tipo, hoy)
    else:
        resumen = ng.bloque(org["resumen"], hay=False)
    visibles = [v for v in del_tipo if not solo_riesgo or float(v["p_venta90_p50"]) < 0.5]
    elegido = next((v for v in visibles if v["stock_id"] == sel), visibles[0] if visibles else None)
    mostrar = max(POR_PAGINA, mostrar)
    if elegido is not None:                     # el elegido siempre a la vista: se amplía de 25 en 25
        pos = next(i for i, v in enumerate(visibles) if v["stock_id"] == elegido["stock_id"])
        mostrar = max(mostrar, (pos // POR_PAGINA + 1) * POR_PAGINA)
    mostrar = min(mostrar, len(visibles)) if visibles else POR_PAGINA
    filas = []
    for v in visibles[:mostrar]:
        f = _fila(v)
        f["elegido"] = elegido is not None and v["stock_id"] == elegido["stock_id"]
        f["url"] = f"/ventas?tipo={tipo}{extra}&v={v['stock_id']}&n={mostrar}"
        filas.append(f)
    quedan = len(visibles) - mostrar
    mas = None
    if quedan > 0:
        siguiente = mostrar + min(POR_PAGINA, quedan)
        mas = {"texto": f"Ver {min(POR_PAGINA, quedan)} más",
               "url": f"/ventas?tipo={tipo}{extra}" + (f"&v={elegido['stock_id']}" if elegido else "") + f"&n={siguiente}"}
    lista = ng.bloque(org["lista"], hay=bool(todos), filas=filas, n_total=len(del_tipo), mas=mas,
                      recuento=f"{fmt.miles(len(filas))} de {fmt.miles(len(visibles))}")
    if org["lista"] is None:
        lista["pendiente"] = PENDIENTE_RIESGO

    # Detalle del vehículo elegido con la curva de su grupo
    detalle = ng.bloque(org["detalle"], hay=False)
    if org["detalle"] != org["lista"]:   # el bloque de detalle es real y la lista no (o al revés): se relee
        _, otros = leer(org["detalle"])
        otros = [v for v in otros if (tipo == "todo" or v["tipo"] == tipo)
                 and (not solo_riesgo or float(v["p_venta90_p50"]) < 0.5)]
        elegido = next((v for v in otros if v["stock_id"] == sel), otros[0] if otros else None)
    if elegido is not None:
        dia_c = ng.ultimo_dia(pg, "panel_curva_grupo", org["detalle"])
        curva = pg.execute(
            """SELECT descripcion, t_dias, s_p10, s_p50, s_p90, mediana_p10, mediana_p50, mediana_p90, n, n_ventas,
                      modelo_version FROM panel_curva_grupo WHERE origen = %s AND dia = %s AND grupo = %s""",
            (org["detalle"], dia_c, elegido["grupo"])).fetchone() if dia_c else None
        f = _fila(elegido)
        a = elegido["dias_exposicion"]
        puntos = []
        if curva:
            desc, t, s10, s50, s90, m10, m50, m90, n, n_ventas, version = curva
            if m50 is None:
                puntos.append(f"Más de la mitad de su grupo ({desc}) sigue sin venderse a los {t[-1]} días.")
            else:
                puntos.append(f"La mitad de los coches de su grupo ({desc}) se vende antes de "
                              f"{fmt.miles(m50)} días ({fmt.miles(m10)}–{fmt.miles(m90)}); este lleva {fmt.miles(a)}.")
            serie = {"t": list(t), "p10": [float(x) for x in s10], "p50": [float(x) for x in s50],
                     "p90": [float(x) for x in s90], "a": a, "b": a + 90}
            base_curva = f"Curva de {fmt.miles(n)} coches parecidos, {fmt.miles(n_ventas)} ya vendidos · modelo {version}"
        else:
            serie, base_curva = None, None
        precio, similares = elegido["precio_mercado_pct"], elegido["similares_n"]
        if not similares:
            puntos.append(f"Sin similares publicados en {conc.provincia}.")
        elif abs(float(precio)) < 0.5:
            puntos.append(f"{similares} similares en {conc.provincia}, con el precio en la mediana.")
        else:
            puntos.append(f"{similares} similares en {conc.provincia}, de media un "
                          f"{fmt.decimal(abs(float(precio)))} % más {'baratos' if precio > 0 else 'caros'}.")
        if elegido["leads_14"] is not None:
            cambio = ng.cambio(elegido["leads_14"], elegido["leads_14_previos"])
            puntos.append(f"Leads de {elegido['modelo']} en 14 días: {fmt.miles(elegido['leads_14'])}"
                          + (f", {cambio}." if cambio else "."))
        hechas = recientes(pg, elegido["stock_id"], org["detalle"])
        bloqueadas = {h["accion"] for h in hechas}
        botones = [{"accion": a, "titulo": t, "secundario": i > 0, "bloqueado": a in bloqueadas}
                   for i, (a, (t, _, _)) in enumerate(ACCIONES.items())]
        detalle = ng.bloque(org["detalle"], v=f, puntos=puntos, serie=serie, base_curva=base_curva,
                            p_texto=f["p"], intervalo=f["p_intervalo"], n=elegido["n"],
                            version=elegido["modelo_version"], botones=botones, hechas=hechas,
                            ejemplo=org["detalle"] == contrato.EJEMPLO)
    elif todos:
        detalle = ng.bloque(org["detalle"], hay=False, filtrado=True)
    alertas = pal.bloque_rol(pg, org["alertas"], "ventas", tipo)
    precio = pr.bloque(pg, conc, org["precio"], modelo_vo, f"/ventas?tipo={tipo}{riesgo}", "#ventas")
    salidas = sal.recientes(pg, conc, org["salidas"], tipo, hoy)
    stock_crm = pr.stock_crm(pg, org["stock_crm"])
    if org["lista"] is None:
        detalle["pendiente"] = PENDIENTE_RIESGO
    return {**base, "con_riesgo": org["lista"] is not None, "resumen": resumen, "lista": lista, "detalle": detalle, "dia": dia, "alertas": alertas,
            "mostrar": mostrar, "precio": precio, "salidas": salidas, "stock_crm": stock_crm}


# --------------------------------------------------------------------------- botones del detalle

def recientes(pg: psycopg.Connection, stock_id: str, origen: str | None) -> list[dict]:
    """Acciones pedidas para el vehículo en los últimos 7 días (la más reciente de cada una), con el
    estado de su aviso."""
    if origen is None:
        return []
    filas = pg.execute(
        """SELECT DISTINCT ON (a.accion) a.accion, a.pedido_at, a.usuario,
                  (SELECT s.estado FROM alertas_salida s WHERE s.accion_id = a.id ORDER BY s.id DESC LIMIT 1)
           FROM acciones a WHERE a.stock_id = %s AND a.origen = %s AND a.pedido_at > now() - make_interval(days => %s)
           ORDER BY a.accion, a.pedido_at DESC""", (stock_id, origen, BLOQUEO_DIAS)).fetchall()
    return [{"accion": a, "texto": f"{ACCIONES[a][2]} el {fmt.fecha_hora(t)} por {u} · {ENVIO[e]}"}
            for a, t, u, e in sorted(filas, key=lambda f: list(ACCIONES).index(f[0]))]


def pedir(pg: psycopg.Connection, modos: dict, stock_id: str, accion: str, usuario: str) -> tuple[bool, str]:
    """Anota la acción en la tabla acciones (el aviso lo envía cdf_jobs). Devuelve (hecha, mensaje).
    No deja repetir la misma acción sobre el mismo vehículo en 7 días. Nunca escribe en Walcu."""
    if accion not in ACCIONES:
        return False, "Acción desconocida."
    origen = contrato.origenes(modos, "ventas")["detalle"]
    dia = ng.ultimo_dia(pg, "panel_riesgo_vehiculo", origen)
    v = pg.execute(
        """SELECT tipo, modelo, version, dias_exposicion, p_venta90_p10, p_venta90_p50, p_venta90_p90
           FROM panel_riesgo_vehiculo WHERE origen = %s AND dia = %s AND stock_id = %s""",
        (origen, dia, stock_id)).fetchone() if dia else None
    if v is None:
        return False, "Ese vehículo ya no está en la lista."
    titulo, destino, hecha = ACCIONES[accion]
    with pg.transaction():
        # Dos pulsaciones a la vez no la anotan dos veces
        pg.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", (f"acciones|{stock_id}|{accion}",))
        previa = pg.execute(
            """SELECT pedido_at, usuario FROM acciones WHERE stock_id = %s AND accion = %s AND origen = %s
               AND pedido_at > now() - make_interval(days => %s) ORDER BY pedido_at DESC LIMIT 1""",
            (stock_id, accion, origen, BLOQUEO_DIAS)).fetchone()
        if previa:
            return False, (f"{hecha} el {fmt.fecha_hora(previa[0])} por {previa[1]}: no se repite hasta pasados "
                           f"{BLOQUEO_DIAS} días.")
        tipo, modelo, version, dias, p10, p50, p90 = v
        pg.execute(
            """INSERT INTO acciones (stock_id, accion, usuario, origen, tipo, vehiculo, dias_exposicion,
                                     p_venta90_p10, p_venta90_p50, p_venta90_p90)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
            (stock_id, accion, usuario, origen, tipo, f"{modelo} {version}" if version else modelo, dias, p10, p50, p90))
    a_quien = "al grupo técnico (datos de ejemplo)" if origen == contrato.EJEMPLO else f"a {ng.DESTINATARIOS[destino]}"
    return True, f"{hecha}: el aviso sale por Telegram {a_quien} en un minuto."
