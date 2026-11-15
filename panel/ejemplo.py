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
    campanas = [
        {"campana_id": "EJ-C1", "titulo": f"{vn[2 % len(vn)]} · Oferta del mes", "tipo_campana": "modelo",
         "modelo": vn[2 % len(vn)], "inicio": hoy - timedelta(days=26), "gasto": 6600, "cpm": 5.2, "ctr": 0.011,
         "conv": 0.017},
        {"campana_id": "EJ-C2", "titulo": f"{vn[1]} · Financiación", "tipo_campana": "modelo", "modelo": vn[1],
         "inicio": hoy - timedelta(days=13), "gasto": 8600, "cpm": 5.6, "ctr": 0.012, "conv": 0.0095},
        {"campana_id": "EJ-C3", "titulo": f"{lento} · Entrega inmediata", "tipo_campana": "modelo", "modelo": lento,
         "inicio": hoy - timedelta(days=48), "gasto": 5000, "cpm": 6.1, "ctr": 0.0062, "conv": 0.024},
        {"campana_id": "EJ-C4", "titulo": "Campaña general · Ventas de temporada", "tipo_campana": "general",
         "modelo": None, "inicio": hoy - timedelta(days=5), "gasto": 12000, "cpm": 4.8, "ctr": 0.010, "conv": 0.015},
    ]
    leads: dict[tuple[date, str, str | None], list[int]] = {}

    def sumar(d, modelo, campana, n):
        citas = sum(r.random() < 0.34 for _ in range(n))
        ventas = sum(r.random() < 0.17 for _ in range(citas))
        f = leads.setdefault((d, modelo, campana), [0, 0, 0])
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
                "modelo": c["modelo"], "inicio": c["inicio"], "gasto_cents": gasto,
                "impresiones": impresiones, "clics": clics})
            if c["modelo"]:
                sumar(d, c["modelo"], c["campana_id"], n)
            else:
                for _ in range(n):
                    sumar(d, r.choice(vn[:6]), c["campana_id"], 1)
            d += UN_DIA
    d = hoy - timedelta(days=56)
    while d <= fin_meta:                                           # leads sin campaña (orgánicos)
        for i, m in enumerate(sorted(set(vn) | set(vo), key=lambda x: (x not in vn[:4], x))):
            n = sum(r.random() < max(0.15, 0.7 - 0.08 * i) for _ in range(2))
            if n:
                sumar(d, m, None, n)
        d += UN_DIA
    for (d, modelo, campana), (n, citas, ventas) in sorted(leads.items(), key=lambda kv: (kv[0][0], kv[0][1], kv[0][2] or "")):
        filas["panel_leads_dia"].append({"dia": d, "modelo": modelo, "campana_id": campana, "leads": n,
                                         "citas": citas, "ventas": ventas})

    def leads_modelo(modelo, desde, hasta):
        return sum(v[0] for (d, m, _), v in leads.items() if m == modelo and desde <= d <= hasta)

    # ---- stock propio (Walcu) y riesgo de cada vehículo (modelo de supervivencia)
    activa = {c["modelo"] for c in campanas if c["modelo"]}
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
            "campana_activa": v["tipo"] == "VN" and v["modelo"] in activa, "sugerencia": sugerencia,
            "leads_14": leads_modelo(v["modelo"], l14_desde, l14_hasta),
            "leads_14_previos": leads_modelo(v["modelo"], l14_desde - timedelta(days=14), l14_desde - UN_DIA),
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
    stock_modelo = {m: sum(1 for v in vehiculos if v["tipo"] == "VN" and v["modelo"] == m) for m in vn}
    diseno = {  # (eslabón que falla, probabilidad de estar peor)
        "EJ-C1": (None, None), "EJ-C2": ("web", 0.78), "EJ-C3": ("anuncio", 0.95), "EJ-C4": (None, None)}
    for c in campanas:
        dias_vida = (hoy - c["inicio"]).days
        propias = [f for f in filas["panel_campana_dia"] if f["campana_id"] == c["campana_id"]]
        gasto = sum(f["gasto_cents"] for f in propias) / 100
        imp = sum(f["impresiones"] for f in propias)
        clics = sum(f["clics"] for f in propias)
        nl = sum(v[0] for (d, m, cp), v in leads.items() if cp == c["campana_id"])
        nc = sum(v[1] for (d, m, cp), v in leads.items() if cp == c["campana_id"])
        valores = {
            "entrega": ("Coste por mil impresiones", "€", "bajo", gasto / imp * 1000 if imp else None),
            "anuncio": ("Clics por impresión", "%", "alto", clics / imp * 100 if imp else None),
            "web": ("Leads por clic", "%", "alto", nl / clics * 100 if clics else None),
            "calidad_lead": ("Citas por lead", "%", "alto", nc / nl * 100 if nl else None),
            "stock": ("Unidades del modelo en stock", "uds", "alto",
                      stock_modelo.get(c["modelo"]) if c["modelo"] else None),
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
                if f["modelo"] == modelo and desde28 <= f["dia"] <= fin_meta) / 100
        v = sum(v[2] for (d, m, cp), v in leads.items() if m == modelo and cp and desde28 <= d <= fin_meta)
        return g / v if v and g else None

    def viejos(modelo):
        return sum(1 for v in vehiculos if v["modelo"] == modelo and v["tipo"] == "VN" and v["dias_exposicion"] > 60)

    c3 = [f for f in filas["panel_campana_dia"] if f["campana_id"] == "EJ-C3"]
    def _cpl(desde, hasta):
        g = sum(f["gasto_cents"] for f in c3 if desde <= f["dia"] <= hasta) / 100
        n = sum(v[0] for (d, m, cp), v in leads.items() if cp == "EJ-C3" and desde <= d <= hasta)
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
        filas["panel_recomendacion_modelo"].append({"dia": hoy, "modelo": m, "accion": accion, "motivo": motivo,
                                                    "modelo_version": VERSION, "n": 28})

    # ---- seis semanas siguientes para una campaña general
    estacional = {1: 0.84, 2: 0.93, 3: 1.08, 4: 1.0, 5: 1.04, 6: 1.12, 7: 1.06, 8: 0.78, 9: 0.97, 10: 1.02,
                  11: 1.0, 12: 0.9}
    lunes = hoy + timedelta(days=7 - hoy.weekday())
    n_viejos = sum(1 for v in vehiculos if v["dias_exposicion"] > 90)
    for i in range(6):
        s = lunes + timedelta(weeks=i)
        dias = [s + timedelta(days=k) for k in range(7)]
        motivos = []
        ind = estacional[s.month] * r.uniform(0.97, 1.03)
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
        if i in (1, 2):
            motivos.append({"tipo": "atencion", "texto": f"{vn[1]} gana atención frente a sus rivales"})
        if i in (3, 4):
            motivos.append({"tipo": "presion_competencia",
                            "texto": f"Más anuncios de otros concesionarios en {conc.provincia}"})
            ind *= 0.96
        nivel = "favorable" if ind >= 1.03 else ("poco_favorable" if ind <= 0.92 else "normal")
        filas["panel_semana_campana"].append({
            "dia": hoy, "semana": s, "nivel": nivel, "indice_p10": _r(ind * 0.88, 2), "indice_p50": _r(ind, 2),
            "indice_p90": _r(ind * 1.12, 2), "motivos": motivos, "modelo_version": VERSION, "n": 6})

    # ---- campañas terminadas frente a lo esperado sin campaña
    pasadas = [
        ("EJ-P1", f"{vn[0]} · Fin de stock", 150, 120, 6100, (98, 81), (8, 3)),
        ("EJ-P2", f"{vn[3 % len(vn)]} · Financiación", 120, 95, 3900, (41, 46), (4, 4)),
        ("EJ-P3", "Campaña general · Primavera", 230, 200, 9800, (162, 120), (12, 10)),
        ("EJ-P4", f"{vn[4 % len(vn)]} · Oferta", 300, 272, 2700, (27, 39), (2, 4)),
    ]
    for cid, titulo, ini, fin, gasto, (leads_obs, leads_sin), (ventas_obs, ventas_sin) in pasadas:
        lp = (round(leads_sin * 0.8), leads_sin, round(leads_sin * 1.22))
        vp = (max(0, round(ventas_sin * 0.5)), ventas_sin, round(ventas_sin * 1.7 + 1))
        filas["panel_campana_resultado"].append({
            "dia": hoy, "campana_id": cid, "titulo": titulo, "inicio": hoy - timedelta(days=ini),
            "fin": hoy - timedelta(days=fin), "gasto_cents": gasto * 100, "leads": leads_obs,
            "leads_sin_p10": lp[0], "leads_sin_p50": lp[1], "leads_sin_p90": lp[2],
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
    return filas


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
