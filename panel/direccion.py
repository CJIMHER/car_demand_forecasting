"""Vista «Dirección» (docs/prototipo/Main.dc.html y Movil.dc.html).

Lee panel_stock_vehiculo, panel_ventas_mes y las alertas (las del motor, tabla alertas, o las de
ejemplo de panel_alertas: panel/alertas.py) y, para la tarjeta del
mercado, panel_mercado_dia (datos reales del precálculo). Nada se calcula sobre tablas de hechos:
solo recuentos y medias de las filas ya preparadas.
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


def datos(pg: psycopg.Connection, conc: Concesionario, modos: dict, tipo: str, hoy: date,
          modelo_vo: str | None = None) -> dict:
    org = contrato.origenes(modos, "direccion")
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

    # ---- ventas del mes frente a la banda prevista
    clave = "todo" if tipo == "todo" else tipo
    ventas_kpi = ng.bloque(org["ventas_kpi"], hay=False)
    ventas_mes = ng.bloque(org["ventas_mes"], hay=False)
    for nombre in ("ventas_kpi", "ventas_mes"):
        origen = org[nombre]
        dia = ng.ultimo_dia(pg, "panel_ventas_mes", origen)
        if dia is None:
            continue
        filas = pg.execute(
            """SELECT dia_mes, ventas_acumuladas, previsto_p10, previsto_p50, previsto_p90, n FROM panel_ventas_mes
               WHERE origen = %s AND dia = %s AND tipo = %s ORDER BY dia_mes""", (origen, dia, clave)).fetchall()
        if not filas:
            continue
        hoy_f = next((f for f in filas if f[0] == dia.day), filas[-1])
        real = hoy_f[1]
        p10, p90 = round(float(hoy_f[2])), round(float(hoy_f[4]))
        if real is None:
            badge, tono = None, "neutro"
        elif real < float(hoy_f[2]):
            badge, tono = "Por debajo", "aviso"
        elif real > float(hoy_f[4]):
            badge, tono = "Por encima", "info"
        else:
            badge, tono = "En lo previsto", "neutro"
        if nombre == "ventas_kpi":
            ventas_kpi = ng.bloque(origen, etiqueta="Ventas del mes" if tipo == "todo" else f"Ventas {tipo} del mes",
                                   valor=fmt.miles(real), sub=f"Previsto a estas alturas: {p10}–{p90}",
                                   badge=badge, tono=tono)
        else:
            ultimo = filas[-1]
            serie = {"dias": [f[0] for f in filas], "real": [f[1] for f in filas],
                     "p10": [float(f[2]) for f in filas], "p50": [float(f[3]) for f in filas],
                     "p90": [float(f[4]) for f in filas], "hoy": dia.day}
            ventas_mes = ng.bloque(origen, serie=serie, n=filas[0][5],
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

    return {
        "tipo": tipo,
        "tipos": ng.selector_tipo("/direccion", tipo, "#direccion",
                                  f"&{pr.PARAMETRO}={quote(modelo_vo, safe='')}" if modelo_vo else ""),
        "fecha": fmt.fecha_larga(hoy), "kpis": [stock, antiguedad, ventas_kpi, alertas_kpi],
        "ventas_mes": ventas_mes, "alertas": alertas, "edad": edad, "mercado": mercado(pg, conc, tipo),
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
