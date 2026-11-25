"""Cruce Meta–Walcu por campaña y semana, y referencias de coste por año (make referencias-meta).

Une las campañas de Meta (campanas_meta_dia_vigente, nivel campaña: gasto, impresiones, clics y leads según
Meta, sumados por semana ISO, de lunes a domingo) con los leads de Walcu de origen Facebook
(walcu_campana_semana_vigente) por nombre de campaña normalizado (config/campanas_walcu.yaml: sufijos y
equivalencias). AiVORA y el resto de orígenes no entran: no son campañas de Facebook.

Cohorte: las citas y las ventas de Walcu van a la semana del lead. Una semana tiene las citas completas si,
el día en que se cargó, su domingo tenía al menos cohorte.lead_a_cita_dias (14) y las ventas completas con
cohorte.cita_a_venta_dias (60) (config/campanas_walcu.yaml). Las tasas y costes de cita (lead a cita, coste
por cita) solo usan semanas con las citas completas, y los de venta, semanas con las ventas completas.

Rehace:
- panel_campana_semana: por semana y campaña de Meta, sus cifras de Meta y, si casa, sus leads, citas y
  ventas de Walcu (también por tipo VN y VO), zona, números inexistentes y si sus citas y ventas están
  completas. Una campaña que casa en alguna semana tiene 0 leads de Walcu en las semanas con carga en que no
  aparece; una que no casa nunca queda sin cifras de Walcu (NULL: no se inventan ceros). Las campañas de
  Walcu sin campaña de Meta van con campana_id NULL.
- panel_referencia_meta: por año ISO (solo las semanas con datos de Meta Y carga de Walcu, ya terminadas al
  cargarse) y tipo (VN, VO y todo), las sumas de gasto, leads según Meta, leads, citas y ventas de Walcu,
  zona y números inexistentes; y, aparte, las de las semanas con citas completas y con ventas completas.
  Los leads de Walcu son todos los de Facebook de ese tipo (casen o no con una campaña). El gasto de una
  campaña de VN o de VO es de ese tipo; el de una de ambos o sin regla se reparte entre VN y VO en
  proporción a sus leads de Walcu de esa semana y, si no tiene, solo cuenta en «todo».
- panel_walcu_sin_cruce: lo que no casa, con el motivo.

Las campañas de otras marcas del grupo (propio.prefijos_excluidos y propio.palabras_excluidas de
config/concesionario.yaml; Concesionario.otra_marca) y las de empresas del grupo con su propio marketing
(propio.empresas_excluidas, si su nombre lleva ese texto; Concesionario.otra_empresa), de Meta y de Walcu,
no entran en nada de lo anterior: se cuentan y se avisa.

Las referencias de 2025 son la «media» de las reglas c y d (alertas/criterios.py).

Lo llaman ingesta/campanas.py (tras cada descarga de Meta, de GA4 y con make campanas) y la carga de
Walcu. make referencias-meta lo rehace y escribe la tabla y el informe.
"""

from __future__ import annotations

import logging
import os
import re
import sys
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import psycopg
import yaml

from ingesta import campanas as cmp
from ingesta import concesionario as cc

RAIZ = Path(__file__).resolve().parent.parent
LOG = logging.getLogger("ingesta.referencias_meta")
TIPOS = ("VN", "VO")
SIN_CAMPANA = "(sin campaña)"
# ventas: todas (coste por venta); ventas_con_cita: con una cita asistida antes (tasa de cita a venta). En las
# cargas por CSV son lo mismo (toda venta tiene cita).
CAMPOS_WALCU = ("leads", "citas", "ventas", "leads_cercana", "leads_fuera", "leads_sin_zona", "num_inexistente",
                "ventas_con_cita")
POR_TIPO = ("leads", "citas", "ventas", "ventas_con_cita")
MADUREZ = ("semanas_citas", "gasto_citas_cents", "leads_citas", "citas_citas",
           "semanas_ventas", "gasto_ventas_cents", "citas_ventas", "ventas_ventas", "ventas_con_cita_ventas")
SELECT_WALCU = ", ".join("coalesce(ventas_con_cita, ventas)" if k == "ventas_con_cita" else k for k in CAMPOS_WALCU)
ZONA = "Europe/Madrid"


class ErrorCruce(Exception):
    pass


@dataclass(frozen=True)
class Cruce:
    origenes_meta: frozenset[str]
    excluidos: frozenset[str]
    sufijo: re.Pattern
    equivalencias: dict[str, str]        # nombre de Walcu normalizado → nombre de Meta normalizado o «id:…»
    sin_equivalencia: frozenset[str] = frozenset()   # nombres de Walcu normalizados sin campaña de Meta
    dias_cita: int = 14                  # cohorte.lead_a_cita_dias
    dias_venta: int = 60                 # cohorte.cita_a_venta_dias

    def completas(self, semana: date, cargada: date) -> tuple[bool, bool]:
        """(citas completas, ventas completas) de una semana cargada el día `cargada`."""
        antiguedad = (cargada - (semana + timedelta(days=6))).days
        return antiguedad >= self.dias_cita, antiguedad >= self.dias_venta

    def normalizar(self, nombre: str | None) -> str:
        """Minúsculas, sin tildes, espacios simplificados, sin espacios entre corchetes («[VO] [2025]» =
        «[VO][2025]») y sin sufijos («-copy», «copia», «-OK», « Campaña», « - leadgen», « - landing»)."""
        t = re.sub(r"\]\s+\[", "][", cmp.plegar(nombre))
        while True:
            nuevo = self.sufijo.sub("", t).strip(" -_.")
            nuevo = re.sub(r"\s+", " ", nuevo)
            if nuevo == t:
                return t
            t = nuevo

    def es_meta(self, origen: str) -> bool:
        o = cmp.plegar(origen)
        return o in self.origenes_meta and o not in self.excluidos


def ruta() -> Path:
    return Path(os.environ.get("CDF_CAMPANAS_WALCU", RAIZ / "config" / "campanas_walcu.yaml"))


def cargar(fichero: Path | None = None) -> Cruce:
    cfg = yaml.safe_load((fichero or ruta()).read_text(encoding="utf-8")) or {}
    origenes = frozenset(cmp.plegar(o) for o in cfg.get("origenes_meta") or [])
    if not origenes:
        raise ErrorCruce("config/campanas_walcu.yaml: falta «origenes_meta»")
    sufijos = [re.escape(cmp.plegar(s)) for s in cfg.get("sufijos") or [] if cmp.plegar(s)]
    patron = re.compile(r"(?:^|[\s\-_(\[]+)(?:" + "|".join(sufijos) + r")(?:[\s\-_]*\d+)?[\s)\]]*$") if sufijos \
        else re.compile(r"(?!x)x")
    base = Cruce(origenes, frozenset(cmp.plegar(o) for o in cfg.get("excluidos") or []), patron, {})
    equivalencias = {}
    for walcu, meta in (cfg.get("equivalencias") or {}).items():
        m = str(meta).strip()
        equivalencias[base.normalizar(str(walcu))] = m if m.lower().startswith("id:") else base.normalizar(m)
    cohorte = cfg.get("cohorte") or {}
    try:
        dias_cita, dias_venta = int(cohorte["lead_a_cita_dias"]), int(cohorte["cita_a_venta_dias"])
    except (KeyError, TypeError, ValueError):
        raise ErrorCruce("config/campanas_walcu.yaml: falta «cohorte» con lead_a_cita_dias y cita_a_venta_dias "
                         "(números de días)") from None
    if not 0 <= dias_cita <= dias_venta:
        raise ErrorCruce("config/campanas_walcu.yaml: cohorte.lead_a_cita_dias debe ser ≥ 0 y ≤ cita_a_venta_dias")
    return Cruce(base.origenes_meta, base.excluidos, base.sufijo, equivalencias,
                 frozenset(base.normalizar(str(n)) for n in cfg.get("sin_equivalencia") or []), dias_cita, dias_venta)


def lunes(d: date) -> date:
    """El lunes de la semana ISO de `d`."""
    return d - timedelta(days=d.weekday())


# --------------------------------------------------------------------------- cálculo

def actualizar(pg: psycopg.Connection, campanas: cmp.Campanas | None = None, cruce: Cruce | None = None,
               conc: cc.Concesionario | None = None) -> dict:
    """Rehace panel_campana_semana, panel_referencia_meta y panel_walcu_sin_cruce (origen real)."""
    campanas = campanas or cmp.cargar()
    cruce = cruce or cargar()
    conc = conc or cc.cargar()
    # ---- Meta por campaña y semana
    meta: dict[tuple[date, str], list[int]] = {}
    nombres: dict[str, tuple[date, str]] = {}          # campaña → (último día, nombre vigente)
    por_nombre: dict[str, set[str]] = defaultdict(set)  # nombre normalizado (cualquiera que haya tenido) → campañas
    dia_meta = None
    for dia, cid, nombre, gasto, imp, clics, leads in pg.execute(
            """SELECT dia, campana_id, campana_nombre, gasto_cents, impresiones, clics, leads
               FROM campanas_meta_dia_vigente WHERE nivel = 'campana'"""):
        a = meta.setdefault((lunes(dia), cid), [0, 0, 0, 0, 0])
        a[0] += int(gasto)
        a[1] += int(imp)
        a[2] += int(clics)
        a[3] += int(leads or 0)
        a[4] = a[4] or (leads is not None)
        if nombre:
            if cid not in nombres or dia >= nombres[cid][0]:
                nombres[cid] = (dia, nombre)
            por_nombre[cruce.normalizar(nombre)].add(cid)
        dia_meta = dia if dia_meta is None or dia > dia_meta else dia_meta
    titulo = {cid: n for cid, (_, n) in nombres.items()}
    # Otras marcas: fuera de todo, por el nombre vigente de la campaña
    otras = {cid for cid in {c for _, c in meta}
             if conc.otra_marca(titulo.get(cid)) or conc.otra_empresa(titulo.get(cid))}
    excluidas = {"meta_campanas": len(otras), "meta_gasto_cents": sum(v[0] for (_, c), v in meta.items() if c in otras),
                 "walcu_filas": 0, "walcu_leads": 0}
    meta = {k: v for k, v in meta.items() if k[1] not in otras}
    por_nombre = defaultdict(set, {n: ids - otras for n, ids in por_nombre.items() if ids - otras})
    clasif = {cid: campanas.clasificar(titulo.get(cid) or "(sin nombre)") for cid in {c for _, c in meta}}
    gasto_semana: dict[tuple[date, str], int] = {k: v[0] for k, v in meta.items()}
    # ---- Walcu (Facebook) por campaña, semana y tipo; día de carga de cada semana
    cargada: dict[date, date] = dict(pg.execute(
        "SELECT semana, max((fecha_carga AT TIME ZONE %s)::date) FROM walcu_campana_semana_vigente GROUP BY 1",
        (ZONA,)).fetchall())
    completas = {s: cruce.completas(s, d) for s, d in cargada.items()}
    terminada = {s for s, d in cargada.items() if s + timedelta(days=6) < d}   # la semana ya había acabado
    walcu: dict[tuple[date, str, str], list[int]] = defaultdict(lambda: [0] * len(CAMPOS_WALCU))
    semanas_walcu: set[date] = set(cargada)                # semanas con carga de Walcu
    for semana, origen, camp, tipo, *vals in pg.execute(
            f"SELECT semana, origen, campana, tipo, {SELECT_WALCU} FROM walcu_campana_semana_vigente"):
        if conc.otra_marca(camp) or conc.otra_empresa(camp):
            excluidas["walcu_filas"] += 1
            excluidas["walcu_leads"] += int(vals[0])
            continue
        if not cruce.es_meta(origen):
            continue
        a = walcu[(semana, camp, tipo)]
        for i, v in enumerate(vals):
            a[i] += int(v)
    # ---- cruce: cada nombre de Walcu con una campaña de Meta
    asignada: dict[str, tuple[str, str]] = {}           # nombre de Walcu → (campaña de Meta, cómo)
    sin_cruce: dict[str, str] = {}
    for camp in sorted({c for _, c, _ in walcu}):
        if cmp.plegar(camp) == cmp.plegar(SIN_CAMPANA):
            sin_cruce[camp] = "el lead no lleva campaña en Walcu"
            continue
        n = cruce.normalizar(camp)
        if n in cruce.sin_equivalencia and n not in por_nombre:
            sin_cruce[camp] = "campaña de Walcu sin campaña de Meta (sin_equivalencia en config/campanas_walcu.yaml)"
            continue
        destino, como = cruce.equivalencias.get(n), "equivalencia"
        if destino is None:
            destino, como = n, "nombre"
        if destino.lower().startswith("id:"):
            cid = destino[3:].strip()
            if any(c == cid for _, c in meta):
                asignada[camp] = (cid, como)
            else:
                sin_cruce[camp] = f"la equivalencia apunta al ID {cid}, que no está en Meta"
            continue
        candidatas = por_nombre.get(destino, set())
        if len(candidatas) == 1:
            asignada[camp] = (next(iter(candidatas)), como)
            continue
        if len(candidatas) > 1:
            # La que tiene gasto en las semanas de esos leads, si es solo una
            semanas = {m for m, c, _ in walcu if c == camp}
            con_gasto = {c for c in candidatas if any(gasto_semana.get((m, c)) for m in semanas)}
            if len(con_gasto) == 1:
                asignada[camp] = (next(iter(con_gasto)), como)
            else:
                sin_cruce[camp] = (f"casa con {len(candidatas)} campañas de Meta del mismo nombre; añadir una "
                                   "equivalencia con su ID en config/campanas_walcu.yaml")
            continue
        sin_cruce[camp] = "ninguna campaña de Meta con ese nombre; añadir una equivalencia en config/campanas_walcu.yaml"
    conocidas = {cid for cid, _ in asignada.values()}
    # ---- filas de panel_campana_semana (semana = semana en lo que sigue)
    filas: dict[tuple[date, str | None, str], dict] = {}

    def fila(semana, cid, tit):
        clave = (semana, cid, tit)
        if clave not in filas:
            c = clasif.get(cid) if cid else None
            m = meta.get((semana, cid), [0, 0, 0, 0, False]) if cid else [0, 0, 0, 0, False]
            filas[clave] = {
                "semana": semana, "campana_id": cid, "titulo": tit,
                "ambito": c.ambito if c and c.ok else None, "tipo_campana": c.tipo if c and c.ok else None,
                "modelo": c.modelo if c and c.ok and c.tipo == "modelo" else None,
                "gasto_cents": m[0], "impresiones": m[1], "clics": m[2], "leads_meta": m[3] if m[4] else None,
                "walcu": semana in semanas_walcu, "cruce": None,
                "citas_completas": completas[semana][0] if semana in completas else None,
                "ventas_completas": completas[semana][1] if semana in completas else None,
                **{k: None for k in CAMPOS_WALCU}, **{f"{k}_{t.lower()}": None for k in POR_TIPO
                                                       for t in TIPOS}}
        return filas[clave]
    for (semana, cid) in meta:
        f = fila(semana, cid, titulo.get(cid) or "(sin nombre)")
        if cid in conocidas and f["walcu"]:
            f["cruce"] = next(como for c, como in asignada.values() if c == cid)
            for k in CAMPOS_WALCU + tuple(f"{k}_{t.lower()}" for k in POR_TIPO for t in TIPOS):
                f[k] = 0
    for (semana, camp, tipo), vals in walcu.items():
        if camp in asignada:
            cid, como = asignada[camp]
            f = fila(semana, cid, titulo.get(cid) or camp)
            f["cruce"] = f["cruce"] or como
        else:
            f = fila(semana, None, camp)
            f["cruce"] = "nombre"      # las cifras de Walcu son suyas: no hay campaña de Meta que casar
        for k in CAMPOS_WALCU + tuple(f"{k}_{t.lower()}" for k in POR_TIPO for t in TIPOS):
            f[k] = f[k] or 0
        for i, k in enumerate(CAMPOS_WALCU):
            f[k] += vals[i]
        for k in POR_TIPO:
            f[f"{k}_{tipo.lower()}"] += vals[CAMPOS_WALCU.index(k)]
    # ---- referencias por año ISO y tipo, en las semanas con Meta y Walcu ya terminadas al cargarse
    semanas_ref = {m for m, _ in meta} & terminada
    ref: dict[tuple[int, str], dict] = {}

    def r(semana, tipo):
        x = ref.setdefault((semana.isocalendar()[0], tipo), {
            "semanas": set(), "m_citas": set(), "m_ventas": set(), "gasto_cents": 0, "leads_meta": 0,
            **{k: 0 for k in CAMPOS_WALCU + MADUREZ}})
        x["semanas"].add(semana)
        return x

    def gasto_ref(x, semana, g):
        x["gasto_cents"] += g
        c_ok, v_ok = completas[semana]
        if c_ok:
            x["m_citas"].add(semana)
            x["gasto_citas_cents"] += g
        if v_ok:
            x["m_ventas"].add(semana)
            x["gasto_ventas_cents"] += g
    for (semana, camp, tipo), vals in walcu.items():
        if semana not in semanas_ref:
            continue
        c_ok, v_ok = completas[semana]
        w = dict(zip(CAMPOS_WALCU, vals))
        for t in (tipo, "todo"):
            x = r(semana, t)
            for k in CAMPOS_WALCU:
                x[k] += w[k]
            if c_ok:
                x["m_citas"].add(semana)
                x["leads_citas"] += w["leads"]
                x["citas_citas"] += w["citas"]
            if v_ok:
                x["m_ventas"].add(semana)
                x["citas_ventas"] += w["citas"]
                x["ventas_ventas"] += w["ventas"]
                x["ventas_con_cita_ventas"] += w["ventas_con_cita"]
    for (semana, cid), (g, _, _, lm, _) in meta.items():
        if semana not in semanas_ref:
            continue
        x = r(semana, "todo")
        gasto_ref(x, semana, g)
        x["leads_meta"] += lm
        c = clasif[cid]
        if c.ok and c.ambito in TIPOS:
            reparto = {c.ambito: (g, lm)}
        else:
            f = filas.get((semana, cid, titulo.get(cid) or "(sin nombre)"))
            lvn, lvo = (f["leads_vn"] or 0, f["leads_vo"] or 0) if f else (0, 0)
            if not lvn + lvo:
                continue                                  # sin leads de Walcu: solo cuenta en «todo»
            gvn, lmvn = round(g * lvn / (lvn + lvo)), round(lm * lvn / (lvn + lvo))
            reparto = {"VN": (gvn, lmvn), "VO": (g - gvn, lm - lmvn)}
        for t, (gt, lt) in reparto.items():
            y = r(semana, t)
            gasto_ref(y, semana, gt)
            y["leads_meta"] += lt
    # ---- informe de lo que no casa
    informe = []
    for camp, motivo in sin_cruce.items():
        semanas = sorted(m for m, c, _ in walcu if c == camp)
        informe.append(("walcu", camp, semanas[0], semanas[-1], None,
                        sum(v[0] for (m, c, _), v in walcu.items() if c == camp), motivo))
    for cid in sorted({c for _, c in meta} - conocidas):
        semanas = sorted(m for m, c in meta if c == cid and m in semanas_walcu and meta[(m, c)][0] > 0)
        if semanas:
            informe.append(("meta", titulo.get(cid) or cid, semanas[0], semanas[-1],
                            sum(meta[(m, cid)][0] for m in semanas), None,
                            "ningún lead de Walcu (Facebook) con ese nombre de campaña"))
    # ---- escritura
    dia = dia_meta or (max(semanas_walcu) if semanas_walcu else None)
    cols = ("semana", "campana_id", "titulo", "ambito", "tipo_campana", "modelo", "gasto_cents", "impresiones",
            "clics", "leads_meta", "walcu", "cruce", "citas_completas", "ventas_completas") + CAMPOS_WALCU \
        + tuple(f"{k}_{t.lower()}" for k in POR_TIPO for t in TIPOS)
    cols_ref = ("anio", "tipo", "desde_semana", "hasta_semana", "semanas", "gasto_cents", "leads_meta") \
        + CAMPOS_WALCU + MADUREZ
    for x in ref.values():
        x["semanas_citas"], x["semanas_ventas"] = len(x.pop("m_citas")), len(x.pop("m_ventas"))
    with pg.transaction():
        pg.execute("DELETE FROM panel_campana_semana WHERE origen = 'real'")
        pg.execute("DELETE FROM panel_referencia_meta WHERE origen = 'real'")
        pg.execute("DELETE FROM panel_walcu_sin_cruce")
        with pg.cursor() as cur:
            if dia is not None:
                cur.executemany(
                    f"INSERT INTO panel_campana_semana (dia, origen, {', '.join(cols)}) "
                    f"VALUES (%s, 'real', {', '.join(['%s'] * len(cols))})",
                    [(dia, *(f[c] for c in cols)) for f in filas.values()])
                cur.executemany(
                    f"INSERT INTO panel_referencia_meta (dia, origen, {', '.join(cols_ref)}) "
                    f"VALUES (%s, 'real', {', '.join(['%s'] * len(cols_ref))})",
                    [(dia, anio, tipo, min(x["semanas"]), max(x["semanas"]), len(x["semanas"]), x["gasto_cents"],
                      x["leads_meta"], *(x[k] for k in CAMPOS_WALCU + MADUREZ))
                     for (anio, tipo), x in sorted(ref.items())])
            cur.executemany("INSERT INTO panel_walcu_sin_cruce (fuente, campana, primera_semana, ultima_semana, gasto_cents, "
                            "leads, motivo) VALUES (%s, %s, %s, %s, %s, %s, %s)", informe)
    leads_fb = sum(v[0] for v in walcu.values())
    leads_cruce = sum(v[0] for (m, c, _), v in walcu.items() if c in asignada)
    if excluidas["meta_campanas"] or excluidas["walcu_filas"]:
        LOG.warning("Otras marcas excluidas: %s campañas de Meta y %s filas de Walcu (%s leads)",
                    excluidas["meta_campanas"], excluidas["walcu_filas"], excluidas["walcu_leads"])
    return {"campanas_semana": len(filas), "sin_cruce": len(informe), "referencias": len(ref),
            "leads_facebook_con_campana_de_meta_pct": round(leads_cruce / leads_fb * 100, 1) if leads_fb else None,
            "otras_marcas": excluidas}


# --------------------------------------------------------------------------- informe (make referencias-meta)

def _div(a, b):
    return a / b if b else None


def metricas(x: dict) -> dict:
    """Costes en euros y tasas en % de una fila de panel_referencia_meta (sumas). Los de cita, solo con las
    semanas de citas completas; los de venta, con las de ventas completas (cohorte)."""
    g = x["gasto_cents"] / 100
    con_cp = x["leads_cercana"] + x["leads_fuera"]
    pct = lambda a, b: None if not b else a / b * 100          # noqa: E731
    return {"gasto": g, "leads_meta": x["leads_meta"], "leads": x["leads"], "citas": x["citas"], "ventas": x["ventas"],
            "coste_lead": _div(g, x["leads"]), "coste_cita": _div(x["gasto_citas_cents"] / 100, x["citas_citas"]),
            "coste_venta": _div(x["gasto_ventas_cents"] / 100, x["ventas_ventas"]),
            "lead_cita": pct(x["citas_citas"], x["leads_citas"]), "cita_venta": pct(x["ventas_con_cita_ventas"], x["citas_ventas"]),
            "fuera_zona": pct(x["leads_fuera"], con_cp), "inexistente": pct(x["num_inexistente"], x["leads"])}


FILAS_INFORME = (("gasto", "Gasto", "€"), ("leads_meta", "Leads según Meta", ""), ("leads", "Leads según Walcu", ""),
                 ("citas", "Citas", ""), ("ventas", "Ventas", ""), ("coste_lead", "Coste por lead", "€"),
                 ("coste_cita", "Coste por cita", "€"), ("coste_venta", "Coste por venta", "€"),
                 ("lead_cita", "Lead a cita", "%"), ("cita_venta", "Cita a venta", "%"),
                 ("fuera_zona", "Fuera de zona", "%"), ("inexistente", "Número inexistente", "%"))


def leer_referencias(pg, origen: str = "real") -> dict[tuple[int, str], dict]:
    cols = ("anio", "tipo", "desde_semana", "hasta_semana", "semanas", "gasto_cents", "leads_meta") + CAMPOS_WALCU \
        + MADUREZ
    dia = pg.execute("SELECT max(dia) FROM panel_referencia_meta WHERE origen = %s", (origen,)).fetchone()[0]
    if dia is None:
        return {}
    return {(f[0], f[1]): dict(zip(cols, f)) for f in pg.execute(
        f"SELECT {', '.join(cols)} FROM panel_referencia_meta WHERE origen = %s AND dia = %s", (origen, dia))}


def _fmt(v, unidad):
    if v is None:
        return "—"
    if unidad == "%":
        return f"{v:.1f} %".replace(".", ",")
    if unidad == "€":
        return f"{v:,.2f} €".replace(",", "X").replace(".", ",").replace("X", ".")
    return f"{int(v):,}".replace(",", ".")


def informe(pg) -> str:
    ref = leer_referencias(pg)
    lineas = []
    anios = sorted({a for a, _ in ref})
    if not anios:
        lineas.append("Sin referencias: hacen falta semanas con datos de Meta y carga de Walcu a la vez.")
    for tipo in ("VN", "VO", "todo"):
        presentes = [a for a in anios if (a, tipo) in ref]
        if not presentes:
            continue
        cab = f"{'Tipo ' + tipo:<22}" + "".join(
            f"{a} ({ref[(a, tipo)]['semanas']} sem.)".rjust(20)
            for a in presentes) + ("Diferencia".rjust(16) if len(presentes) == 2 else "")
        lineas += ["", cab, "-" * len(cab)]
        ms = [metricas(ref[(a, tipo)]) for a in presentes]
        for k, nombre, unidad in FILAS_INFORME:
            fila = f"{nombre:<22}" + "".join(_fmt(m[k], unidad).rjust(20) for m in ms)
            if len(ms) == 2 and ms[0][k] is not None and ms[1][k] is not None:
                d = ms[1][k] - ms[0][k]
                txt = (f"{d:+.1f} pp".replace(".", ",") if unidad == "%" else
                       (f"{d:+.1f} %".replace(".", ",") if not ms[0][k] else
                        f"{(ms[1][k] / ms[0][k] - 1) * 100:+.0f} %".replace(".", ",")))
                fila += txt.rjust(16)
            lineas.append(fila)
    if ("todo" in {t for _, t in ref}) and anios:
        lineas += ["", "VN y VO: el gasto de las campañas de ambos o sin regla se reparte por sus leads de Walcu; "
                       "sin leads de Walcu, solo cuenta en «todo». Leads de Walcu: origen Facebook (sin AiVORA).",
                   "Lead a cita y coste por cita: solo semanas con las citas completas; cita a venta y coste por "
                   "venta: con las ventas completas (cohorte, config/campanas_walcu.yaml)."]
        for tipo in ("todo",):
            for a in anios:
                x = ref.get((a, tipo))
                if x and (x["semanas_citas"] < x["semanas"] or x["semanas_ventas"] < x["semanas"]):
                    lineas.append(f"  {a}: {x['semanas']} semanas; {x['semanas_citas']} con las citas completas y "
                                  f"{x['semanas_ventas']} con las ventas completas.")
    sin = pg.execute("SELECT fuente, campana, primera_semana, ultima_semana, gasto_cents, leads, motivo FROM panel_walcu_sin_cruce "
                     "ORDER BY fuente, coalesce(leads, 0) + coalesce(gasto_cents, 0) / 100 DESC, campana").fetchall()
    lineas += ["", f"Sin cruce entre Meta y Walcu: {len(sin)}"]
    for fuente, camp, p, u, g, l, motivo in sin:
        cifra = f"{_fmt(g / 100, '€')} de gasto" if fuente == "meta" else f"{l} leads"
        lineas.append(f"  [{fuente}] {camp} (semanas del {p:%d-%m-%Y} al {u:%d-%m-%Y}, {cifra}): {motivo}")
    if sin:
        lineas.append("Equivalencias en config/campanas_walcu.yaml; después, make referencias-meta.")
    return "\n".join(lineas)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    from ingesta.db import conectar
    try:
        campanas, cruce = cmp.cargar(), cargar()
    except (cmp.ErrorCampanas, ErrorCruce) as exc:
        LOG.error("%s", exc)
        return 1
    with conectar(autocommit=True) as pg:
        resumen = actualizar(pg, campanas, cruce)
        print(informe(pg))
        print(f"\nCampaña y semana: {resumen['campanas_semana']} filas; leads de Facebook con campaña de Meta: "
              f"{resumen['leads_facebook_con_campana_de_meta_pct']} %.")
        x = resumen["otras_marcas"]
        if x["meta_campanas"] or x["walcu_filas"]:
            print(f"Aviso: campañas de otras marcas o empresas del grupo excluidas (prefijos_excluidos, palabras_excluidas y "
                  "empresas_excluidas de config/concesionario.yaml): "
                  f"{x['meta_campanas']} de Meta ({_fmt(x['meta_gasto_cents'] / 100, '€')} de gasto) y "
                  f"{x['walcu_filas']} filas de Walcu ({x['walcu_leads']} leads).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
