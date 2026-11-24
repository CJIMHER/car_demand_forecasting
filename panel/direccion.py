"""Vista «Dirección» (docs/prototipo/Main.dc.html y Movil.dc.html).

Lee panel_stock_vehiculo, panel_ventas_mes, las salidas de nuestro stock publicado (panel_salidas_stock,
estimación de ventas: panel/salidas.py) y las alertas (las del motor, tabla alertas, o las de
ejemplo de panel_alertas: panel/alertas.py); el precio provisional de nuestros VO (panel_precio_vo); Meta y
Walcu por semanas (panel_campana_semana) con las referencias por año (panel_referencia_meta) y GA4 por canal
(panel_trafico_canal_dia), con los mismos cálculos que Marketing; y, para la tarjeta del mercado y nuestro
stock publicado, panel_mercado_dia (datos reales del precálculo). Nada se calcula sobre tablas de hechos:
solo recuentos y medias de las filas ya preparadas.

Sin el modo ejemplo explícito (PANEL_MODO_EJEMPLO=1) no se muestra ninguna fila de ejemplo: un bloque sin
filas reales dice «Pendiente de conectar: <fuente>» si su fuente aún no existe (PENDIENTE) o «Sin datos
todavía» si existe pero no ha escrito nada. Sin stock interno, la tarjeta de stock es la de nuestro stock
publicado en la web (Mercado).
"""

from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta
from urllib.parse import quote

import psycopg

from ingesta.concesionario import Concesionario
from panel import alertas as pal
from panel import contrato
from panel import formato as fmt
from panel import negocio as ng
from panel import precio as pr
from panel import prevision as pv
from panel import salidas as sal

# Bloques cuya fuente real aún no existe: lo que dicen en vez de datos de ejemplo
STOCK_INTERNO = "stock interno (Walcu o DMS)"
PENDIENTE = {"stock": STOCK_INTERNO, "antiguedad": STOCK_INTERNO, "edad": STOCK_INTERNO,
             "ventas_mes": "predicción de ventas"}

TRAMOS = (("0–30 días", 0, 30), ("31–60 días", 31, 60), ("61–90 días", 61, 90), ("Más de 90", 91, None))


def _tipos(tipo: str) -> tuple[str, ...]:
    return ("VN", "VO") if tipo == "todo" else (tipo,)


def _stock(pg, origen, tipo: str) -> tuple[dict, float | None, dict | None]:
    """Tarjeta de stock, antigüedad media y días de cada vehículo del tipo pedido."""
    dia = ng.ultimo_dia(pg, "panel_stock_vehiculo", origen)
    if dia is None:
        return ng.bloque(origen, hay=False), None, None
    filas = pg.execute("SELECT tipo, dias_exposicion FROM panel_stock_vehiculo WHERE origen = %s AND dia = %s",
                       (origen, dia)).fetchall()
    sel = [d for t, d in filas if t in _tipos(tipo)]
    por_tipo = Counter(t for t, _ in filas)
    fin_ant = dia.replace(day=1) - timedelta(days=1)
    dia_ant = pg.execute("SELECT max(dia) FROM panel_stock_vehiculo WHERE origen = %s AND dia <= %s",
                         (origen, fin_ant)).fetchone()[0]
    previo = None
    if dia_ant is not None and dia_ant.month == fin_ant.month and dia_ant.year == fin_ant.year:
        previo = pg.execute("SELECT count(*) FROM panel_stock_vehiculo WHERE origen = %s AND dia = %s AND tipo = ANY(%s)",
                            (origen, dia_ant, list(_tipos(tipo)))).fetchone()[0]
    n = len(sel)
    partes = [f"VN {fmt.miles(por_tipo['VN'])} · VO {fmt.miles(por_tipo['VO'])}"] if tipo == "todo" else []
    badge, tono = None, "neutro"
    if previo:
        dif = n - previo
        partes.append(f"{'+' if dif > 0 else ''}{fmt.miles(dif)} frente a {fmt.MESES[fin_ant.month - 1]}"
                      if dif else f"Igual que a final de {fmt.MESES[fin_ant.month - 1]}")
        badge = "Estable" if abs(dif) <= 0.05 * previo else ("Sube" if dif > 0 else "Baja")
    etiqueta = "Stock total" if tipo == "todo" else f"Stock {tipo}"
    stock = ng.bloque(origen, etiqueta=etiqueta, valor=fmt.miles(n), sub=" · ".join(partes), badge=badge, tono=tono)
    return stock, (sum(sel) / n if n else None), {"sel": sel, "dia": dia}


def publicado(pg: psycopg.Connection, tipo: str) -> dict:
    """Nuestro stock publicado en la web (anuncios propios del último día de Mercado, en toda España)."""
    tipos = _tipos(tipo)
    filas = {t: (p, d) for t, p, d in pg.execute(
        """SELECT DISTINCT ON (tipo) tipo, propios, dia FROM panel_mercado_dia WHERE zona = 'espana'
           AND tipo = ANY(%s) ORDER BY tipo, dia DESC""", (list(tipos),))}
    if set(filas) != set(tipos):
        return ng.bloque(None, hay=False, pendiente=STOCK_INTERNO)
    n = sum(p for p, _ in filas.values())
    partes = ([f"VN {fmt.miles(filas['VN'][0])} · VO {fmt.miles(filas['VO'][0])}"] if tipo == "todo" else []) + [
        f"anuncios propios en la web el {fmt.fecha_corta(min(d for _, d in filas.values()))}",
        f"stock interno: pendiente de conectar ({STOCK_INTERNO})"]
    return ng.bloque("real", etiqueta="Stock publicado" if tipo == "todo" else f"Stock {tipo} publicado",
                     valor=fmt.miles(n), sub=" · ".join(partes), badge=None, tono="neutro", publicado=True)


def datos(pg: psycopg.Connection, conc: Concesionario, modos: dict, tipo: str, hoy: date,
          modelo_vo: str | None = None, ejemplo: bool = True) -> dict:
    """`ejemplo`: modo ejemplo explícito. Sin él, los bloques solo leen filas reales."""
    org = contrato.origenes(modos, "direccion", ejemplo)
    tipos = _tipos(tipo)

    # ---- stock: tarjetas y tramos de días en exposición (los tres bloques leen la misma tabla,
    # así que tienen el mismo origen)
    stock, media, info = _stock(pg, org["stock"], tipo)
    objetivo = conc.objetivo_antiguedad_dias
    if media is not None:
        encima = round(media) > objetivo
        antiguedad = ng.bloque(org["antiguedad"], etiqueta="Antigüedad media", valor=f"{fmt.miles(media)} días",
                               sub=f"Objetivo: {objetivo} días", badge="Por encima" if encima else "En objetivo",
                               tono="aviso" if encima else "neutro")
    else:
        antiguedad = ng.bloque(org["antiguedad"], hay=False)
    if info and info["sel"]:
        cuentas = [sum(1 for d in info["sel"] if d >= lo and (hi is None or d <= hi)) for _, lo, hi in TRAMOS]
        maximo = max(cuentas) or 1
        edad = ng.bloque(org["edad"], tramos=[
            {"etiqueta": e, "n": fmt.miles(c), "ancho": round(c / maximo * 100, 1), "viejo": hi is None}
            for (e, _, hi), c in zip(TRAMOS, cuentas)])
    else:
        edad = ng.bloque(org["edad"], hay=False)

    # ---- salidas de nuestro stock (estimación de ventas mientras no lleguen las del CRM): tarjeta y bloque
    ventas_kpi, _ = sal.mensual(pg, org["ventas_kpi"], tipo, hoy)
    _, salidas = sal.mensual(pg, org["salidas"], tipo, hoy)

    # ---- ventas del mes frente a la banda prevista (solo con el modo ejemplo: la predicción aún no existe)
    clave = "todo" if tipo == "todo" else tipo
    ventas_mes = ng.bloque(org["ventas_mes"], hay=False)
    dia = ng.ultimo_dia(pg, "panel_ventas_mes", org["ventas_mes"])
    filas = pg.execute(
        """SELECT dia_mes, ventas_acumuladas, previsto_p10, previsto_p50, previsto_p90, n FROM panel_ventas_mes
           WHERE origen = %s AND dia = %s AND tipo = %s ORDER BY dia_mes""",
        (org["ventas_mes"], dia, clave)).fetchall() if dia else []
    if filas:
        hoy_f = next((f for f in filas if f[0] == dia.day), filas[-1])
        real = hoy_f[1]
        p10, p90 = round(float(hoy_f[2])), round(float(hoy_f[4]))
        ultimo = filas[-1]
        serie = {"dias": [f[0] for f in filas], "real": [f[1] for f in filas],
                 "p10": [float(f[2]) for f in filas], "p50": [float(f[3]) for f in filas],
                 "p90": [float(f[4]) for f in filas], "hoy": dia.day}
        ventas_mes = ng.bloque(org["ventas_mes"], serie=serie, n=filas[0][5],
                               rango=f"Previsión a fin de mes: {math.floor(float(ultimo[2]))}–{math.ceil(float(ultimo[4]))}",
                               mes=f"{fmt.MESES[dia.month - 1]} de {dia.year}",
                               resumen=(f"Ventas acumuladas: {fmt.miles(real)} a día {dia.day}; "
                                        f"lo previsto a estas alturas, entre {p10} y {p90}."))

    # ---- alertas (motor de alertas o, sin él, las de ejemplo: panel/alertas.py)
    alertas_kpi = ng.bloque(org["alertas_kpi"], hay=False)
    alertas = ng.bloque(org["alertas"], hay=False)
    for nombre in ("alertas_kpi", "alertas"):
        origen = org[nombre]
        dia, filas = pal.leer(pg, origen, tipos)
        if dia is None:
            continue
        if nombre == "alertas_kpi":
            temas = Counter(a["tema"] for a in filas)
            nuevas = sum(1 for a in filas if a["abierta_desde"] == dia)
            alertas_kpi = ng.bloque(
                origen, etiqueta="Alertas abiertas", valor=fmt.miles(len(filas)),
                sub=" · ".join(f"{n} de {ng.TEMAS[t].lower()}" for t, n in temas.most_common()) or "Ninguna abierta",
                badge=(f"{nuevas} nueva{'s' if nuevas != 1 else ''} hoy" if nuevas else None), tono="info")
        else:
            alertas = ng.bloque(origen, filas=[
                {"texto": a["texto"], "nivel": a["nivel"], "nueva": a["abierta_desde"] == dia,
                 "pie": f"{ng.TEMAS[a['tema']]} · para {ng.destinatarios(a['destinatarios'])}"}
                for a in filas], dia=dia)

    # ---- sin fuente real todavía: «Pendiente de conectar»; sin stock interno, nuestro stock publicado
    kpis = [stock, antiguedad, ventas_kpi, alertas_kpi]
    for nombre, b in zip(("stock", "antiguedad", "ventas_kpi", "alertas_kpi"), kpis):
        if not b.get("hay") and nombre in PENDIENTE:
            b["pendiente"] = PENDIENTE[nombre]
    if org["stock"] is None:
        kpis[0] = publicado(pg, tipo)
    for nombre, b in (("ventas_mes", ventas_mes), ("edad", edad)):
        if not b.get("hay"):
            b["pendiente"] = PENDIENTE[nombre]

    # ---- Meta y Walcu (últimas 4 semanas y referencias por año) y GA4 por canal: lo mismo que Marketing
    from panel import marketing as mk
    marketing = mk.ultimas_semanas(pg, org["marketing"], tipo) if org["marketing"] else ng.bloque(None, hay=False)
    referencias = mk.referencias(pg, org["referencias"], tipo)
    trafico = mk.trafico(pg, org["trafico"], tipo) if org["trafico"] else ng.bloque(None, hay=False)

    return {
        "tipo": tipo, "kpis": kpis, "marketing": marketing, "referencias": referencias, "trafico": trafico,
        "tipos": ng.selector_tipo("/direccion", tipo, "#direccion",
                                  f"&{pr.PARAMETRO}={quote(modelo_vo, safe='')}" if modelo_vo else ""),
        "fecha": fmt.fecha_larga(hoy),
        "ventas_mes": ventas_mes, "salidas": salidas, "prevision": pv.direccion(pg, org["prevision"], tipo), "alertas": alertas, "edad": edad, "mercado": mercado(pg, conc, tipo),
        "precio": pr.bloque(pg, conc, org["precio"], modelo_vo, f"/direccion?tipo={tipo}", "#direccion"),
        "origenes": org,
    }


def mercado(pg: psycopg.Connection, conc: Concesionario, tipo: str) -> dict:
    """Tarjeta del mercado (datos reales de panel_mercado_dia, provincia del concesionario)."""
    tipos = _tipos(tipo)
    dias = dict(pg.execute("SELECT tipo, max(dia) FROM panel_mercado_dia WHERE zona = 'provincia' GROUP BY tipo"))
    if any(dias.get(t) is None for t in tipos):
        return {"hay": False, "provincia": conc.provincia}
    dia = min(dias[t] for t in tipos)  # el último día con datos de todos los tipos pedidos
    filas = {t: (a, p) for t, a, p in pg.execute(
        "SELECT tipo, anuncios, propios FROM panel_mercado_dia WHERE zona = 'provincia' AND dia = %s AND tipo = ANY(%s)",
        (dia, list(tipos)))}
    if set(filas) != set(tipos):
        return {"hay": False, "provincia": conc.provincia}
    anuncios = sum(a for a, _ in filas.values())
    propios = sum(p for _, p in filas.values())
    otros = {t: a - p for t, (a, p) in filas.items()}
    if tipo == "todo":
        sub = f"{fmt.miles(otros['VN'])} nuevos · {fmt.miles(otros['VO'])} de ocasión"
    else:
        sub = "vehículos nuevos" if tipo == "VN" else "vehículos de ocasión"
    return {"hay": True, "provincia": conc.provincia, "marca": conc.marca, "valor": fmt.miles(anuncios - propios),
            "sub": sub, "peso": fmt.porcentaje(propios / anuncios * 100 if anuncios else None, 0),
            "dia": fmt.fecha_corta(dia)}
