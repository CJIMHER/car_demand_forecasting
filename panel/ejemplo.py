"""Datos de ejemplo del panel: rellenan las tablas del contrato (docs/contrato_panel.md) con filas
origen = 'ejemplo', para ver Dirección, Ventas y Marketing antes de conectar Walcu, GA4 y Meta.

    python -m panel.ejemplo cargar     (make ejemplo-cargar)
    python -m panel.ejemplo borrar     (make ejemplo-borrar)

- Solo funcionan con PANEL_PERMITE_EJEMPLO=1 (en el servidor, en el .env).
- Solo tocan las filas origen = 'ejemplo' de las tablas del contrato. Nunca escriben en las tablas de
  hechos, en las panel_mercado_* ni en las filas reales.
- Deterministas: el mismo día da las mismas filas. Coherentes entre vistas: las alertas de Dirección
  citan vehículos de Ventas (por número de stock) y campañas de Marketing (por campana_id); los
  leads de Ventas son la suma de los de Marketing; el stock de Dirección es el de Ventas.
- Nada se ajusta ni se calcula como lo hará un modelo: las cifras de modelo (probabilidades,
  curvas, bandas) se escriben directamente, con su p10, p50 y p90, versión «ejemplo» y su n.
- Modelos de la marca configurada (config/concesionario.yaml, «modelos»); provincia, del mismo
  fichero. Ningún dato personal: vehículos por número de stock inventado y leads agregados.
"""

from __future__ import annotations

import argparse
import calendar
import math
import os
import random
import sys
from collections import defaultdict
from datetime import date, timedelta

import psycopg
from psycopg.types.json import Jsonb

from ingesta.concesionario import Concesionario, cargar as cargar_concesionario
from panel.contrato import EJEMPLO, TABLAS

SEMILLA = 20261115
VERSION = "ejemplo"
UN_DIA = timedelta(days=1)

MOTORES = ("1.0 TSI", "1.5 TSI", "2.0 TDI")
ACABADOS = ("Life", "Style", "R-Line")
COLORES = ("blanco", "gris", "negro", "azul", "rojo")
CAMBIOS = ("manual", "DSG")
T_DIAS = list(range(0, 301, 10))
K_WEIBULL = 0.85        # forma < 1: cuanto más tiempo lleva un coche, menos probable que salga


class NoPermitido(Exception):
    pass


def permitido(entorno=os.environ) -> bool:
    return entorno.get("PANEL_PERMITE_EJEMPLO", "").strip() == "1"


# --------------------------------------------------------------------------- utilidades

def _s(t: float, lam: float) -> float:
    return math.exp(-((t / lam) ** K_WEIBULL))


def _p90(a: int, lam: float) -> float:
    """P(venta en los próximos 90 días | lleva a días) = 1 − S(a+90)/S(a)."""
    return 1 - _s(a + 90, lam) / _s(a, lam)


def _mediana(lam: float) -> float:
    return lam * math.log(2) ** (1 / K_WEIBULL)


def _r(x: float, n: int = 3) -> float:
    return round(x, n)


def _veredicto(obs: float, p10: float, p90: float) -> str:
    return "por_encima" if obs > p90 else ("por_debajo" if obs < p10 else "normal")


def _pct(x: float) -> str:
    return f"{round(x * 100)} %"


def _signo(x: int) -> str:
    return f"{x:+d}".replace("-", "−")


def _coma(x: float, d: int = 1) -> str:
    return f"{x:.{d}f}".replace(".", ",")


# --------------------------------------------------------------------------- generación

def generar(hoy: date, conc: Concesionario) -> dict[str, list[dict]]:
    """Filas de ejemplo de cada tabla del contrato para el día `hoy` (sin dia, origen ni calculado_at)."""
    if not conc.modelos_vn or not conc.modelos_vo:
        raise ValueError("config/concesionario.yaml: faltan los modelos de la marca («modelos: VN: [...] VO: [...]»)")
    r = random.Random(SEMILLA)
    filas: dict[str, list[dict]] = {t: [] for t in TABLAS}
    vn, vo = list(conc.modelos_vn), list(conc.modelos_vo)

    # ---- grupos (curvas de supervivencia) por tipo y modelo
    lam = {}
    for tipo, modelos, base in (("VN", vn, 85.0), ("VO", vo, 100.0)):
        for i, m in enumerate(modelos):
            lam[(tipo, m)] = base * (0.75 + 0.12 * ((i * 7) % 6)) * r.uniform(0.95, 1.05)
    lento = max(vn[:5], key=lambda m: lam[("VN", m)])      # el modelo VN que peor sale

    # ---- campañas activas y leads (Meta y Walcu)
    fin_meta = hoy - UN_DIA                                # Meta da los datos del día anterior
    # gasto: céntimos al día; cpm: € por mil impresiones; ctr: clics por impresión; conv: leads por clic
    # ámbito: VN, VO o ambos; una de ambos reparte sus leads entre VN y VO según el vehículo
    vo_campana = vo[1 % len(vo)]
    campanas = [
        {"campana_id": "EJ-C1", "titulo": f"{vn[2 % len(vn)]} · Oferta del mes", "tipo_campana": "modelo",
         "ambito": "VN", "modelo": vn[2 % len(vn)], "inicio": hoy - timedelta(days=26), "gasto": 6600, "cpm": 5.2,
         "ctr": 0.011, "conv": 0.017},
        {"campana_id": "EJ-C2", "titulo": f"{vn[1]} · Financiación", "tipo_campana": "modelo", "ambito": "VN",
         "modelo": vn[1], "inicio": hoy - timedelta(days=13), "gasto": 8600, "cpm": 5.6, "ctr": 0.012, "conv": 0.0095},
        {"campana_id": "EJ-C3", "titulo": f"{lento} · Entrega inmediata", "tipo_campana": "modelo", "ambito": "VN",
         "modelo": lento, "inicio": hoy - timedelta(days=48), "gasto": 5000, "cpm": 6.1, "ctr": 0.0062, "conv": 0.024},
        {"campana_id": "EJ-C4", "titulo": "Campaña general · Ventas de temporada", "tipo_campana": "general",
         "ambito": "ambos", "modelo": None, "inicio": hoy - timedelta(days=5), "gasto": 12000, "cpm": 4.8,
         "ctr": 0.010, "conv": 0.015},
        {"campana_id": "EJ-C5", "titulo": "Ocasión · Seminuevos con garantía", "tipo_campana": "general",
         "ambito": "VO", "modelo": None, "inicio": hoy - timedelta(days=34), "gasto": 4200, "cpm": 4.4,
         "ctr": 0.012, "conv": 0.016},
        {"campana_id": "EJ-C6", "titulo": f"{vo_campana} de ocasión · Financiación", "tipo_campana": "modelo",
         "ambito": "VO", "modelo": vo_campana, "inicio": hoy - timedelta(days=19), "gasto": 3100, "cpm": 5.0,
         "ctr": 0.0105, "conv": 0.0105},
    ]
    leads: dict[tuple[date, str, str, str | None], list[int]] = {}

    def sumar(d, modelo, tipo, campana, n):
        citas = sum(r.random() < 0.34 for _ in range(n))
        ventas = sum(r.random() < 0.17 for _ in range(citas))
        f = leads.setdefault((d, modelo, tipo, campana), [0, 0, 0])
        f[0] += n
        f[1] += citas
        f[2] += ventas

    for c in campanas:
        d = max(c["inicio"], hoy - timedelta(days=56))
        while d <= fin_meta:
            gasto = round(c["gasto"] * r.uniform(0.8, 1.2))          # céntimos
            impresiones = round(gasto / 100 / c["cpm"] * 1000)
            clics = round(impresiones * c["ctr"] * r.uniform(0.9, 1.1))
            conv = c["conv"]
            if c["campana_id"] == "EJ-C3" and d > fin_meta - timedelta(days=7):
                conv *= 0.42                                       # el coste por lead se dispara la última semana
            n = sum(r.random() < conv for _ in range(clics))
            filas["panel_campana_dia"].append({
                "dia": d, "campana_id": c["campana_id"], "titulo": c["titulo"], "tipo_campana": c["tipo_campana"],
                "ambito": c["ambito"], "modelo": c["modelo"], "inicio": c["inicio"], "gasto_cents": gasto,
                "impresiones": impresiones, "clics": clics})
            for _ in range(n):
                tipo = c["ambito"] if c["ambito"] != "ambos" else ("VN" if r.random() < 0.62 else "VO")
                modelo = c["modelo"] or r.choice(vn[:6] if tipo == "VN" else vo[:6])
                sumar(d, modelo, tipo, c["campana_id"], 1)
            d += UN_DIA
    d = hoy - timedelta(days=56)
    while d <= fin_meta:                                           # leads sin campaña (orgánicos)
        for tipo, modelos, base in (("VN", vn, 0.7), ("VO", vo, 0.45)):
            for i, m in enumerate(modelos):
                n = sum(r.random() < max(0.12, base - 0.08 * i) for _ in range(2))
                if n:
                    sumar(d, m, tipo, None, n)
        d += UN_DIA
    for (d, modelo, tipo, campana), (n, citas, ventas) in sorted(
            leads.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2], kv[0][3] or "")):
        # Código postal (sesión criterios de Meta): de cada 10 leads, ~1 sin código postal y ~2 de fuera de
        # la zona cercana; determinista. Solo el recuento: nunca el código.
        con_cp = n - n // 10
        fuera = (con_cp * (2 + (d.toordinal() + len(modelo)) % 2)) // 10
        filas["panel_leads_dia"].append({"dia": d, "modelo": modelo, "tipo": tipo, "campana_id": campana, "leads": n,
                                         "citas": citas, "ventas": ventas, "leads_cp_zona": con_cp - fuera,
                                         "leads_cp_fuera": fuera})

    def leads_modelo(modelo, tipo, desde, hasta):
        return sum(v[0] for (d, m, t, _), v in leads.items() if m == modelo and t == tipo and desde <= d <= hasta)

    # ---- stock propio (Walcu) y riesgo de cada vehículo (modelo de supervivencia)
    activa = {(c["ambito"], c["modelo"]) for c in campanas if c["modelo"]}
    vehiculos = []
    n_id = 1001
    for tipo, modelos, total in (("VN", vn, 96), ("VO", vo, 46)):
        pesos = [1 / (i + 1.5) for i in range(len(modelos))]
        for _ in range(total):
            m = r.choices(modelos, pesos)[0]
            dias = int(min(330, r.expovariate(1 / 62)))
            if tipo == "VN":
                version = f"{'Pro' if m.upper().startswith('ID') else r.choice(MOTORES)} {r.choice(ACABADOS)}"
                detalle = f"{r.choice((95, 115, 150, 204))} CV · {r.choice(CAMBIOS)} · {r.choice(COLORES)}"
            else:
                version = f"{r.choice(MOTORES)} {r.choice(ACABADOS)}"
                detalle = f"{r.randint(2019, 2024)} · {r.randint(8, 95) * 1000:,} km".replace(",", ".")
            vehiculos.append({"stock_id": f"EJ{n_id}", "tipo": tipo, "modelo": m, "version": version,
                              "detalle": detalle, "dias_exposicion": dias,
                              "precio_cents": r.randint(150, 520) * 10_000})
            n_id += 1
    for v in vehiculos:
        filas["panel_stock_vehiculo"].append({"dia": hoy, **{k: v[k] for k in (
            "stock_id", "tipo", "modelo", "version", "dias_exposicion", "precio_cents")}})
    # Último día del mes anterior: los que ya estaban (con menos días) y algunos que se han vendido
    fin_mes_ant = hoy.replace(day=1) - UN_DIA
    delta = (hoy - fin_mes_ant).days
    for v in vehiculos:
        if v["dias_exposicion"] >= delta:
            filas["panel_stock_vehiculo"].append({"dia": fin_mes_ant, "stock_id": v["stock_id"], "tipo": v["tipo"],
                                                  "modelo": v["modelo"], "version": v["version"],
                                                  "dias_exposicion": v["dias_exposicion"] - delta,
                                                  "precio_cents": v["precio_cents"]})
    for tipo, modelos, objetivo in (("VN", vn, 92), ("VO", vo, 44)):
        ya = sum(1 for f in filas["panel_stock_vehiculo"] if f["dia"] == fin_mes_ant and f["tipo"] == tipo)
        for _ in range(max(0, objetivo - ya)):
            filas["panel_stock_vehiculo"].append({"dia": fin_mes_ant, "stock_id": f"EJ{n_id}", "tipo": tipo,
                                                  "modelo": r.choice(modelos), "version": r.choice(ACABADOS),
                                                  "dias_exposicion": int(r.expovariate(1 / 50)),
                                                  "precio_cents": r.randint(150, 520) * 10_000})
            n_id += 1

    l14_desde, l14_hasta = hoy - timedelta(days=14), hoy - UN_DIA
    for v in vehiculos:
        lm = lam[(v["tipo"], v["modelo"])]
        a = v["dias_exposicion"]
        p10, p50, p90 = sorted((_p90(a, lm * 1.18), _p90(a, lm), _p90(a, lm * 0.85)))
        precio = None if r.random() < 0.06 else _r(r.uniform(-3, 7.5), 1)
        similares = None if precio is None else r.randint(3, 26)
        sugerencia = ("revisar_precio" if p50 < 0.5 and precio is not None and precio > 3 else
                      "campana" if p50 < 0.5 else "mantener")
        filas["panel_riesgo_vehiculo"].append({
            "dia": hoy, "stock_id": v["stock_id"], "tipo": v["tipo"], "modelo": v["modelo"], "version": v["version"],
            "detalle": v["detalle"], "dias_exposicion": a, "grupo": f"{v['tipo']} · {v['modelo']}",
            "p_venta90_p10": _r(p10), "p_venta90_p50": _r(p50), "p_venta90_p90": _r(p90),
            "precio_mercado_pct": precio, "similares_n": similares,
            "campana_activa": (v["tipo"], v["modelo"]) in activa, "sugerencia": sugerencia,
            "leads_14": leads_modelo(v["modelo"], v["tipo"], l14_desde, l14_hasta),
            "leads_14_previos": leads_modelo(v["modelo"], v["tipo"], l14_desde - timedelta(days=14), l14_desde - UN_DIA),
            "modelo_version": VERSION, "n": 0})
    for (tipo, m), lm in lam.items():
        if not any(v["tipo"] == tipo and v["modelo"] == m for v in vehiculos):
            continue
        n_ep = r.randint(18, 90)
        bajo, alto = lm * 0.85, lm * 1.18
        filas["panel_curva_grupo"].append({
            "dia": hoy, "grupo": f"{tipo} · {m}", "descripcion": f"{m} {'nuevos' if tipo == 'VN' else 'de ocasión'}",
            "t_dias": T_DIAS, "s_p10": [_r(_s(t, bajo)) for t in T_DIAS], "s_p50": [_r(_s(t, lm)) for t in T_DIAS],
            "s_p90": [_r(_s(t, alto)) for t in T_DIAS],
            "mediana_p10": _r(_mediana(bajo), 0), "mediana_p50": _r(_mediana(lm), 0), "mediana_p90": _r(_mediana(alto), 0),
            "n": n_ep, "n_ventas": int(n_ep * r.uniform(0.5, 0.75)), "modelo_version": VERSION})
    n_grupo = {f["grupo"]: f["n"] for f in filas["panel_curva_grupo"]}
    for f in filas["panel_riesgo_vehiculo"]:
        f["n"] = n_grupo[f["grupo"]]

    # ---- ventas del mes frente a la banda prevista
    dias_mes = calendar.monthrange(hoy.year, hoy.month)[1]
    ritmos = {"VN": 1.1, "VO": 0.67}
    reales = {}
    for tipo, ritmo in ritmos.items():
        acum, serie = 0, []
        for d in range(1, hoy.day + 1):
            acum += sum(r.random() < ritmo * 0.74 / 2 for _ in range(2))
            serie.append(acum)
        reales[tipo] = serie
    reales["todo"] = [a + b for a, b in zip(reales["VN"], reales["VO"])]
    ritmos["todo"] = ritmos["VN"] + ritmos["VO"]
    for tipo in ("VN", "VO", "todo"):
        for d in range(1, dias_mes + 1):
            p50 = ritmos[tipo] * d
            ancho = 1.28 * math.sqrt(p50) * 1.1
            filas["panel_ventas_mes"].append({
                "dia": hoy, "tipo": tipo, "dia_mes": d,
                "ventas_acumuladas": reales[tipo][d - 1] if d <= hoy.day else None,
                "previsto_p10": _r(max(0.0, p50 - ancho), 1), "previsto_p50": _r(p50, 1),
                "previsto_p90": _r(p50 + ancho, 1), "modelo_version": VERSION, "n": 36})

    # ---- atención frente a los rivales (Google Trends)
    mes_ant = fin_mes_ant.replace(day=1)
    puntos = (14, 9, 2, -3, -5, -8)
    atencion = {}
    orden_at = [vn[1], vn[3 % len(vn)], vn[4 % len(vn)], vn[2 % len(vn)], vn[5 % len(vn)], vn[0]]
    for m, p in zip(dict.fromkeys(orden_at), puntos):
        atencion[m] = p
        filas["panel_atencion_relativa"].append({"dia": hoy, "modelo": m, "mes": mes_ant, "relativo_puntos": p,
                                                 "n_competidores": 9})

    # ---- salud de las campañas activas
    desde28 = fin_meta - timedelta(days=27)
    stock_modelo = {(t, m): sum(1 for v in vehiculos if v["tipo"] == t and v["modelo"] == m)
                    for t, ms in (("VN", vn), ("VO", vo)) for m in ms}
    diseno = {  # (eslabón que falla, probabilidad de estar peor)
        "EJ-C1": (None, None), "EJ-C2": ("web", 0.78), "EJ-C3": ("anuncio", 0.95), "EJ-C4": (None, None),
        "EJ-C5": (None, None), "EJ-C6": ("calidad_lead", 0.74)}
    for c in campanas:
        dias_vida = (hoy - c["inicio"]).days
        propias = [f for f in filas["panel_campana_dia"] if f["campana_id"] == c["campana_id"]]
        gasto = sum(f["gasto_cents"] for f in propias) / 100
        imp = sum(f["impresiones"] for f in propias)
        clics = sum(f["clics"] for f in propias)
        nl = sum(v[0] for (d, m, t, cp), v in leads.items() if cp == c["campana_id"])
        nc = sum(v[1] for (d, m, t, cp), v in leads.items() if cp == c["campana_id"])
        valores = {
            "entrega": ("Coste por mil impresiones", "€", "bajo", gasto / imp * 1000 if imp else None),
            "anuncio": ("Clics por impresión", "%", "alto", clics / imp * 100 if imp else None),
            "web": ("Leads por clic", "%", "alto", nl / clics * 100 if clics else None),
            "calidad_lead": ("Citas por lead", "%", "alto", nc / nl * 100 if nl else None),
            "stock": ("Unidades del modelo en stock", "uds", "alto",
                      stock_modelo.get((c["ambito"], c["modelo"])) if c["modelo"] else None),
        }
        falla, prob_falla = diseno[c["campana_id"]]
        for eslabon, (medida, unidad, mejor, valor) in valores.items():
            sin = dias_vida < 7 or valor is None
            if sin:
                filas["panel_campana_salud"].append({
                    "dia": hoy, "campana_id": c["campana_id"], "dias_vida": dias_vida, "eslabon": eslabon,
                    "medida": medida, "unidad": unidad, "mejor": mejor, "valor": None if valor is None else _r(valor, 2),
                    "ref_p10": None, "ref_p50": None, "ref_p90": None, "prob_peor": None, "estado": "sin_datos",
                    "modelo_version": VERSION, "n": 31 if valor is not None else 0})
                continue
            if eslabon == falla:
                prob = prob_falla
                ref50 = valor * (1.75 if mejor == "alto" else 0.6)
            else:
                prob = _r(r.uniform(0.15, 0.55), 2)
                ref50 = valor * (r.uniform(0.88, 1.02) if mejor == "alto" else r.uniform(0.98, 1.12))
            estado = "revisar" if prob >= 0.9 else ("vigilar" if prob >= 0.7 else "bien")
            filas["panel_campana_salud"].append({
                "dia": hoy, "campana_id": c["campana_id"], "dias_vida": dias_vida, "eslabon": eslabon,
                "medida": medida, "unidad": unidad, "mejor": mejor, "valor": _r(valor, 2),
                "ref_p10": _r(ref50 * 0.8, 2), "ref_p50": _r(ref50, 2), "ref_p90": _r(ref50 * 1.22, 2),
                "prob_peor": prob, "estado": estado, "modelo_version": VERSION, "n": 31})

    # ---- recomendación por modelo (reglas)
    def cpv(modelo):
        g = sum(f["gasto_cents"] for f in filas["panel_campana_dia"]
                if f["modelo"] == modelo and f["ambito"] == "VN" and desde28 <= f["dia"] <= fin_meta) / 100
        v = sum(v[2] for (d, m, t, cp), v in leads.items() if m == modelo and t == "VN" and cp
                and desde28 <= d <= fin_meta)
        return g / v if v and g else None

    def viejos(modelo, tipo="VN"):
        return sum(1 for v in vehiculos if v["modelo"] == modelo and v["tipo"] == tipo
                   and v["dias_exposicion"] > conc.objetivo_antiguedad_dias)

    c3 = [f for f in filas["panel_campana_dia"] if f["campana_id"] == "EJ-C3"]
    def _cpl(desde, hasta):
        g = sum(f["gasto_cents"] for f in c3 if desde <= f["dia"] <= hasta) / 100
        n = sum(v[0] for (d, m, t, cp), v in leads.items() if cp == "EJ-C3" and desde <= d <= hasta)
        return g / n if n else None
    cpl7, cpl28 = _cpl(fin_meta - timedelta(days=6), fin_meta), _cpl(desde28, fin_meta)
    ratio = (cpl7 / cpl28) if cpl7 and cpl28 else 2.0
    recomendaciones = []
    m_aum = vn[1]
    recomendaciones.append((m_aum, "aumentar",
                            f"Gana atención frente a sus rivales ({_signo(atencion.get(m_aum, 0))}), "
                            f"{viejos(m_aum)} unidades llevan más de 60 días"
                            + (f" y su coste por venta es de {round(cpv(m_aum))} €." if cpv(m_aum) else ".")))
    for m in (vn[2 % len(vn)], vn[3 % len(vn)]):
        if m in (m_aum, lento):
            continue
        recomendaciones.append((m, "mantener", (f"Coste por venta de {round(cpv(m))} €. " if cpv(m) else "")
                                + f"Atención {_signo(atencion.get(m, 0))} frente a sus rivales: revisar en dos semanas."))
    recomendaciones.append((lento, "cambiar", f"El coste por lead de la última semana es {_coma(ratio)} veces su "
                                              f"media de 28 días. Cambiar la creatividad antes de invertir más."))
    m_red = vn[0] if vn[0] not in (m_aum, lento) else vn[5 % len(vn)]
    recomendaciones.append((m_red, "reducir", f"Pierde atención frente a sus rivales ({_signo(atencion.get(m_red, -8))}) "
                                              f"y solo {viejos(m_red)} unidades llevan más de 60 días."))
    for m, accion, motivo in recomendaciones:
        filas["panel_recomendacion_modelo"].append({"dia": hoy, "tipo": "VN", "modelo": m, "accion": accion,
                                                    "motivo": motivo, "modelo_version": VERSION, "n": 28})
    # Ocasión: por modelo del stock de ocasión (unidades que superan el objetivo de antigüedad y precio)
    objetivo = conc.objetivo_antiguedad_dias
    en_vo = sorted({v["modelo"] for v in vehiculos if v["tipo"] == "VO"},
                   key=lambda m: (-viejos(m, "VO"), m))
    for i, m in enumerate(en_vo[:4]):
        unidades = sum(1 for v in vehiculos if v["tipo"] == "VO" and v["modelo"] == m)
        if i == 0:
            accion, motivo = "aumentar", (f"{viejos(m, 'VO')} de sus {unidades} unidades de ocasión superan los "
                                          f"{objetivo} días: conviene darles salida.")
        elif i == len(en_vo[:4]) - 1:
            accion, motivo = "reducir", (f"Solo {viejos(m, 'VO')} de sus {unidades} unidades de ocasión superan los "
                                         f"{objetivo} días: se venden sin ayuda.")
        else:
            accion, motivo = "mantener", (f"{unidades} unidades de ocasión, {viejos(m, 'VO')} por encima de "
                                          f"{objetivo} días: revisar en dos semanas.")
        filas["panel_recomendacion_modelo"].append({"dia": hoy, "tipo": "VO", "modelo": m, "accion": accion,
                                                    "motivo": motivo, "modelo_version": VERSION, "n": 28})

    # ---- seis semanas siguientes para una campaña general
    estacional = {1: 0.84, 2: 0.93, 3: 1.08, 4: 1.0, 5: 1.04, 6: 1.12, 7: 1.06, 8: 0.78, 9: 0.97, 10: 1.02,
                  11: 1.0, 12: 0.9}
    lunes = hoy + timedelta(days=7 - hoy.weekday())
    # Por tipo: la venta esperada de VN, de VO y de las dos (el VO es menos estacional)
    for tipo_s, amplitud in (("todo", 1.0), ("VN", 1.15), ("VO", 0.6)):
        n_viejos = sum(1 for v in vehiculos if v["dias_exposicion"] > 90 and tipo_s in ("todo", v["tipo"]))
        for i in range(6):
            s = lunes + timedelta(weeks=i)
            dias = [s + timedelta(days=k) for k in range(7)]
            motivos = []
            ind = (1 + (estacional[s.month] - 1) * amplitud) * r.uniform(0.97, 1.03)
            motivos.append({"tipo": "estacionalidad",
                            "texto": ("Semanas de más ventas que la media del año" if ind >= 1.03 else
                                      "Semanas de menos ventas que la media del año" if ind <= 0.95 else
                                      "Ventas en la media del año")})
            festivos = [d for d in dias if (d.month, d.day) in ((12, 6), (12, 8), (12, 25), (1, 1), (1, 6))]
            if festivos:
                ind *= 0.9
                motivos.append({"tipo": "calendario", "texto": f"Festivo el {festivos[0].day}/{festivos[0].month}: "
                                                               "menos visitas al concesionario"})
            elif s.day <= 7 or (s + timedelta(days=6)).day <= 7:
                motivos.append({"tipo": "calendario", "texto": "Principio de mes: más solicitudes de financiación"})
            if i < 2:
                motivos.append({"tipo": "stock_envejecido", "texto": f"{n_viejos} vehículos superan los 90 días"})
            if i in (1, 2) and tipo_s != "VO":
                motivos.append({"tipo": "atencion", "texto": f"{vn[1]} gana atención frente a sus rivales"})
            if i in (3, 4):
                motivos.append({"tipo": "presion_competencia",
                                "texto": f"Más anuncios de otros concesionarios en {conc.provincia}"})
                ind *= 0.96
            nivel = "favorable" if ind >= 1.03 else ("poco_favorable" if ind <= 0.92 else "normal")
            filas["panel_semana_campana"].append({
                "dia": hoy, "tipo": tipo_s, "semana": s, "nivel": nivel, "indice_p10": _r(ind * 0.88, 2),
                "indice_p50": _r(ind, 2), "indice_p90": _r(ind * 1.12, 2), "motivos": motivos,
                "modelo_version": VERSION, "n": 6})

    # ---- campañas terminadas frente a lo esperado sin campaña
    # Una fila «todo» por campaña; las de ambos, además, una por tipo con sus leads y ventas
    # (observado, esperado sin campaña p50): (leads VN, leads VO) y (ventas VN, ventas VO)
    pasadas = [
        ("EJ-P1", f"{vn[0]} · Fin de stock", "VN", 150, 120, 6100, {"todo": ((98, 81), (8, 3))}),
        ("EJ-P2", f"{vn[3 % len(vn)]} · Financiación", "VN", 120, 95, 3900, {"todo": ((41, 46), (4, 4))}),
        ("EJ-P3", "Campaña general · Primavera", "ambos", 230, 200, 9800,
         {"VN": ((118, 86), (9, 7)), "VO": ((44, 34), (3, 3))}),
        ("EJ-P4", f"{vn[4 % len(vn)]} · Oferta", "VN", 300, 272, 2700, {"todo": ((27, 39), (2, 4))}),
        ("EJ-P5", "Ocasión · Fin de semana de seminuevos", "VO", 90, 86, 2200, {"todo": ((63, 38), (7, 3))}),
        ("EJ-P6", "Campaña general · Rebajas de enero", "ambos", 310, 290, 7600,
         {"VN": ((71, 74), (5, 6)), "VO": ((39, 33), (4, 2))}),
    ]
    for cid, titulo, ambito, ini, fin, gasto, por_tipo in pasadas:
        if ambito == "ambos":
            por_tipo["todo"] = tuple(tuple(x + y for x, y in zip(por_tipo["VN"][k], por_tipo["VO"][k]))
                                     for k in (0, 1))      # leads y ventas: VN + VO
        for tipo, ((leads_obs, leads_sin), (ventas_obs, ventas_sin)) in por_tipo.items():
            lp = (round(leads_sin * 0.8), leads_sin, round(leads_sin * 1.22))
            vp = (max(0, round(ventas_sin * 0.5)), ventas_sin, round(ventas_sin * 1.7 + 1))
            filas["panel_campana_resultado"].append({
                "dia": hoy, "campana_id": cid, "titulo": titulo, "ambito": ambito, "tipo": tipo,
                "inicio": hoy - timedelta(days=ini), "fin": hoy - timedelta(days=fin), "gasto_cents": gasto * 100,
                "leads": leads_obs, "leads_sin_p10": lp[0], "leads_sin_p50": lp[1], "leads_sin_p90": lp[2],
                "ventas": ventas_obs, "ventas_sin_p10": vp[0], "ventas_sin_p50": vp[1], "ventas_sin_p90": vp[2],
                "veredicto_leads": _veredicto(leads_obs, *lp[::2]), "veredicto_ventas": _veredicto(ventas_obs, *vp[::2]),
                "modelo_version": VERSION, "n": 24})

    # ---- alertas de hoy, sacadas de las filas anteriores
    alertas = []
    hoy_todo = next(f for f in filas["panel_ventas_mes"] if f["tipo"] == "todo" and f["dia_mes"] == hoy.day)
    if hoy_todo["ventas_acumuladas"] < hoy_todo["previsto_p10"]:
        alertas.append({"regla": "desvio_ventas", "tema": "ventas", "tipo": None, "destinatarios": ["direccion", "ventas"],
                        "nivel": "aviso", "abierta_desde": hoy - timedelta(days=3),
                        "texto": f"Ventas del mes por debajo de lo previsto: {hoy_todo['ventas_acumuladas']} frente a "
                                 f"{math.ceil(hoy_todo['previsto_p10'])}–{math.floor(hoy_todo['previsto_p90'])}."})
    riesgo = filas["panel_riesgo_vehiculo"]
    candidatos = [f for f in riesgo if f["dias_exposicion"] > 90 and f["p_venta90_p50"] < 0.5]
    citado = None
    if candidatos:
        f = max(candidatos, key=lambda x: (x["dias_exposicion"], x["stock_id"]))
        citado = f["stock_id"]
        alertas.append({"regla": "riesgo_inmovilizacion", "tema": "stock", "tipo": f["tipo"],
                        "destinatarios": ["ventas", "direccion"], "nivel": "aviso", "abierta_desde": hoy,
                        "stock_id": f["stock_id"], "modelo": f["modelo"],
                        "texto": f"{f['modelo']} {f['version']} ({f['stock_id']}) lleva {f['dias_exposicion']} días; "
                                 f"su probabilidad de venderse en 90 días es del {_pct(f['p_venta90_p50'])} "
                                 f"({_pct(f['p_venta90_p10'])}–{_pct(f['p_venta90_p90'])})."})
    alertas.append({"regla": "coste_campana", "tema": "campanas", "tipo": "VN", "destinatarios": ["marketing"],
                    "nivel": "aviso", "abierta_desde": hoy, "campana_id": "EJ-C3", "modelo": lento,
                    "texto": f"{campanas[2]['titulo']}: el coste por lead de la última semana es {_coma(ratio)} veces "
                             f"su media de 28 días."})
    caros = [f for f in riesgo if f["precio_mercado_pct"] is not None and f["precio_mercado_pct"] > 2
             and f["similares_n"] and f["stock_id"] != citado]
    if caros:
        f = max(caros, key=lambda x: (x["similares_n"], x["stock_id"]))
        alertas.append({"regla": "presion_mercado", "tema": "mercado", "tipo": f["tipo"], "destinatarios": ["ventas"],
                        "nivel": "info", "abierta_desde": hoy - timedelta(days=2), "stock_id": f["stock_id"],
                        "modelo": f["modelo"],
                        "texto": f"{f['modelo']} {f['version']} ({f['stock_id']}): {f['similares_n']} unidades similares "
                                 f"en {conc.provincia}, de media un {_coma(f['precio_mercado_pct'])} % más baratas."})
    alertas.append({"regla": "oportunidad_campana", "tema": "campanas", "tipo": "VN", "destinatarios": ["marketing"],
                    "nivel": "info", "abierta_desde": hoy - timedelta(days=6), "modelo": vn[1],
                    "texto": f"{vn[1]} gana atención frente a sus rivales ({_signo(atencion[vn[1]])} puntos): "
                             f"buen momento para una campaña."})
    for i, a in enumerate(alertas, 1):
        filas["panel_alertas"].append({"dia": hoy, "alerta_id": f"EJ-A{i}", "stock_id": None, "campana_id": None,
                                       "modelo": None, **a})

    # ---- tráfico de la web por canal (GA4): 56 días, por canal y tipo de la página de destino.
    # Generador aparte para no mover las cifras de los demás bloques.
    rt = random.Random(SEMILLA + 1)
    # canal: sesiones al día, fracción con interacción, eventos clave por sesión, reparto VN / VO / sin tipo
    canales = {"organica": (120, 0.62, 0.030, (0.35, 0.30, 0.35)), "directo": (70, 0.55, 0.025, (0.15, 0.15, 0.70)),
               "meta": (55, 0.40, 0.020, (0.50, 0.30, 0.20)), "google_ads": (45, 0.58, 0.040, (0.55, 0.30, 0.15)),
               "portales": (25, 0.66, 0.050, (0.05, 0.85, 0.10)), "referencia": (12, 0.50, 0.015, (0.30, 0.30, 0.40)),
               "correo": (6, 0.70, 0.060, (0.40, 0.40, 0.20)), "otros": (4, 0.35, 0.010, (0.20, 0.20, 0.60))}
    for i in range(2 * 28):
        d = fin_meta - timedelta(days=i)
        crece = 1.0 if i < 28 else 0.9                     # los 28 días anteriores, algo menos
        for canal, (base, inter, ev, reparto) in canales.items():
            total = max(1, round(base * crece * (0.8 if d.weekday() >= 5 else 1.0) * rt.uniform(0.85, 1.15)))
            for tipo, parte in zip(("VN", "VO", "sin_tipo"), reparto):
                s = round(total * parte)
                if not s:
                    continue
                si = min(s, round(s * inter * rt.uniform(0.9, 1.1)))
                filas["panel_trafico_canal_dia"].append({"dia": d, "canal": canal, "tipo": tipo, "sesiones": s,
                                                         "sesiones_interaccion": si,
                                                         "eventos_clave": round(s * ev * rt.uniform(0.7, 1.3))})

    # ---- campaña de otra marca del grupo en la misma cuenta (solo se cuenta)
    for i in range(10):
        filas["panel_campana_otra_marca"].append({"dia": fin_meta - timedelta(days=i), "campana_id": "EJ-OM1",
                                                  "titulo": "Otra marca del grupo · Oferta", "gasto_cents": 2500,
                                                  "impresiones": 4000, "clics": 40})

    # ---- Meta y Walcu por campaña y semana, y referencias por año (sin azar: salen de las filas de arriba)
    _campana_semana(filas, fin_meta)
    _referencias(filas, fin_meta)
    return filas


def _lunes(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _completas(semana: date, cargada: date) -> tuple[bool, bool]:
    """Citas y ventas completas con los plazos por defecto (14 y 60 días desde el domingo)."""
    antiguedad = (cargada - (semana + timedelta(days=6))).days
    return antiguedad >= 14, antiguedad >= 60


def _campana_semana(filas: dict[str, list[dict]], fin_meta: date) -> None:
    """panel_campana_semana: las campañas de panel_campana_dia por semana, con los leads de panel_leads_dia.
    Walcu «cargado» el día siguiente a fin_meta."""
    cargada = fin_meta + UN_DIA
    meta: dict[tuple[date, str], dict] = {}
    for f in filas["panel_campana_dia"]:
        m = meta.setdefault((_lunes(f["dia"]), f["campana_id"]), {
            "titulo": f["titulo"], "ambito": f["ambito"], "tipo_campana": f["tipo_campana"], "modelo": f["modelo"],
            "gasto_cents": 0, "impresiones": 0, "clics": 0})
        m["gasto_cents"] += f["gasto_cents"]
        m["impresiones"] += f["impresiones"]
        m["clics"] += f["clics"]
    walcu: dict[tuple[date, str], dict] = defaultdict(lambda: defaultdict(int))
    for f in filas["panel_leads_dia"]:
        if f["campana_id"] is None:
            continue
        w = walcu[(_lunes(f["dia"]), f["campana_id"])]
        for k in ("leads", "citas", "ventas"):
            w[k] += f[k]
            w[f"{k}_{f['tipo'].lower()}"] += f[k]
    for (semana, cid), m in sorted(meta.items()):
        w = walcu.get((semana, cid), defaultdict(int))
        leads = w["leads"]
        fuera, sin_zona = round(leads * 0.2), round(leads * 0.1)
        c_ok, v_ok = _completas(semana, cargada)
        filas["panel_campana_semana"].append({
            "dia": fin_meta, "semana": semana, "campana_id": cid, **m, "leads_meta": round(leads * 1.1),
            "walcu": True, "cruce": "nombre", "citas_completas": c_ok, "ventas_completas": v_ok,
            "leads": leads, "citas": w["citas"], "ventas": w["ventas"],
            **{f"{k}_{t}": w[f"{k}_{t}"] for k in ("leads", "citas", "ventas") for t in ("vn", "vo")},
            "leads_cercana": leads - fuera - sin_zona, "leads_fuera": fuera, "leads_sin_zona": sin_zona,
            "num_inexistente": round(leads * 0.08)})
    semana = _lunes(fin_meta) - timedelta(days=7)
    c_ok, v_ok = _completas(semana, cargada)
    filas["panel_campana_semana"].append({
        "dia": fin_meta, "semana": semana, "campana_id": None, "titulo": "(sin campaña)", "ambito": None,
        "tipo_campana": None, "modelo": None, "gasto_cents": 0, "impresiones": 0, "clics": 0, "leads_meta": None,
        "walcu": True, "cruce": "nombre", "citas_completas": c_ok, "ventas_completas": v_ok,
        "leads": 4, "citas": 0, "ventas": 0, "leads_vn": 3, "leads_vo": 1,
        "citas_vn": 0, "citas_vo": 0, "ventas_vn": 0, "ventas_vo": 0, "leads_cercana": 3, "leads_fuera": 1,
        "leads_sin_zona": 0, "num_inexistente": 0})


def _referencias(filas: dict[str, list[dict]], fin_meta: date) -> None:
    """panel_referencia_meta: el año ISO anterior completo y el actual hasta la última semana terminada."""
    anio, semana_actual, _ = fin_meta.isocalendar()
    #           tipo: gasto (€), leads Meta, leads, citas, ventas, inexistentes (por semana del año anterior)
    base = {"VN": (440, 53, 44, 4.2, 1.4, 3.5), "VO": (185, 16, 14, 3.2, 1.2, 0.2)}
    for a, semanas, factor in ((anio - 1, 52, 1.0), (anio, max(semana_actual - 1, 1), 1.15)):
        lunes1 = date.fromisocalendar(a, 1, 1)
        # Del año en curso, las 2 últimas semanas sin citas completas y las 9 últimas sin ventas completas
        s_citas = semanas if a < anio else max(semanas - 2, 0)
        s_ventas = semanas if a < anio else max(semanas - 9, 0)
        total = defaultdict(int)
        for tipo, (g, lm, l, c, v, inex) in base.items():
            x = {"gasto_cents": round(g * semanas * factor * 100), "leads_meta": round(lm * semanas * factor),
                 "leads": round(l * semanas * factor), "citas": round(c * semanas * (2 - factor)),
                 "ventas": round(v * semanas * (2 - factor)), "num_inexistente": round(inex * semanas * factor)}
            x["leads_fuera"] = round(x["leads"] * (0.22 if tipo == "VN" else 0.12))
            x["leads_sin_zona"] = round(x["leads"] * 0.1)
            x["leads_cercana"] = x["leads"] - x["leads_fuera"] - x["leads_sin_zona"]
            x.update(semanas_citas=s_citas, gasto_citas_cents=round(g * s_citas * factor * 100),
                     leads_citas=round(l * s_citas * factor), citas_citas=round(c * s_citas * (2 - factor)),
                     semanas_ventas=s_ventas, gasto_ventas_cents=round(g * s_ventas * factor * 100),
                     citas_ventas=round(c * s_ventas * (2 - factor)), ventas_ventas=round(v * s_ventas * (2 - factor)))
            for k, n in x.items():
                if not k.startswith("semanas"):
                    total[k] += n
            filas["panel_referencia_meta"].append({
                "dia": fin_meta, "anio": a, "tipo": tipo, "desde_semana": lunes1,
                "hasta_semana": lunes1 + timedelta(days=7 * (semanas - 1)), "semanas": semanas, **x})
        filas["panel_referencia_meta"].append({
            "dia": fin_meta, "anio": a, "tipo": "todo", "desde_semana": lunes1,
            "hasta_semana": lunes1 + timedelta(days=7 * (semanas - 1)), "semanas": semanas,
            "semanas_citas": s_citas, "semanas_ventas": s_ventas, **total})


# --------------------------------------------------------------------------- escritura

def borrar(pg: psycopg.Connection) -> int:
    """Quita todas las filas de ejemplo de las tablas del contrato. Devuelve cuántas."""
    total = 0
    with pg.transaction():
        for t in TABLAS:
            total += pg.execute(f"DELETE FROM {t} WHERE origen = %s", (EJEMPLO,)).rowcount
    return total


def cargar(pg: psycopg.Connection, hoy: date, conc: Concesionario) -> dict[str, int]:
    """Sustituye las filas de ejemplo por las del día `hoy`. Las filas reales no se tocan."""
    filas = generar(hoy, conc)
    with pg.transaction():
        for t in TABLAS:
            pg.execute(f"DELETE FROM {t} WHERE origen = %s", (EJEMPLO,))
            if not filas[t]:
                continue
            cols = list(filas[t][0])
            valores = [tuple(Jsonb(f[c]) if c == "motivos" else f[c] for c in cols) + (EJEMPLO,) for f in filas[t]]
            with pg.cursor() as cur:
                cur.executemany(f"INSERT INTO {t} ({', '.join(cols)}, origen) VALUES "
                                f"({', '.join(['%s'] * (len(cols) + 1))})", valores)
    return {t: len(f) for t, f in filas.items()}


def main(argv=None, entorno=os.environ) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("orden", choices=("cargar", "borrar"))
    parser.add_argument("--dia", type=date.fromisoformat, help="día de los datos (por defecto, hoy en Madrid)")
    args = parser.parse_args(argv)
    if not permitido(entorno):
        print("No se cargan ni se borran datos de ejemplo: hace falta PANEL_PERMITE_EJEMPLO=1 en el .env.",
              file=sys.stderr)
        return 1
    from ingesta.db import conectar
    from panel.formato import hoy as hoy_madrid
    with conectar(autocommit=True) as pg:
        if args.orden == "borrar":
            print(f"Datos de ejemplo borrados: {borrar(pg)} filas.")
        else:
            cuantas = cargar(pg, args.dia or hoy_madrid(), cargar_concesionario())
            print("Datos de ejemplo cargados (origen = 'ejemplo'):")
            for t, n in cuantas.items():
                print(f"  {t:<28} {n:>6}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
