"""Lo que se deriva de la API de Walcu (tablas walcu_api_*), después de cada carga de ingesta/walcu_api.py.

Siempre: el inventario de estados (de coches y de leads) y de location, con sus recuentos y si están mapeados en
config/walcu_api.yaml (make walcu-api-mapeo; también va al resumen de fuentes_estado).

Solo con datos de la API y el mapeo definido (estados de venta del lead y location incluidas o excluidas). Si no,
no se deduce ninguna venta ni se excluye ninguna sede, y todo sigue como ahora:
1. walcu_campana_semana (fuente api): las mismas columnas, exclusiones y cohortes que la carga por CSV
   (scripts/walcu_agregar.py y config/walcu_export.yaml).
   - Exclusiones: location excluida (o fuera de las incluidas), comerciales excluidos (WALCU_COMERCIALES_EXCLUIDOS:
     ids de usuario de Walcu, que solo se usan en memoria para calcular su seudónimo), empresas del grupo con su
     propio marketing (en el origen o en la campaña) y campañas de otras marcas o de posventa (por el nombre).
   - Cita = el lead tiene una cita a la que el cliente asistió (showed_up). Venta = el lead está en un estado de
     venta (o de sus hitos); si vuelve a otro estado, deja de serlo. Las dos, en la semana del lead (cohorte).
   - Dos cifras de ventas: ventas (todas: coste por venta y Dirección) y ventas_con_cita (el lead tuvo una cita
     a la que el cliente asistió, que empezó antes de la venta: la tasa de cita a venta, comparable con el CSV,
     donde toda venta tiene cita).
   - Tipo: la regla de siempre (el nombre de la campaña; si no lo dice, location de VO; si no, la tipología de
     car_list; si no, el tipo por defecto). Se informa del porcentaje de acuerdo con la tipología de car_list.
   - Zona: la del lead (cercana, fuera o sin_dato, de su código postal, que solo se lee en memoria).
   - Solo se escribe si cambia algo frente a lo vigente; las semanas empiezan en la primera semana completa de
     la carga inicial. Después se rehacen el cruce con Meta y las referencias. El CSV sigue de respaldo.
2. panel_ventas_crm: ventas por mes de la venta (la fecha del primer hito Firmado; si no, la del primer estado de
   venta), tipo (la tipología del coche: car_list o el coche) y modelo. Dirección: «Ventas del mes».
3. walcu_api_cp_semana: leads, citas y ventas por código postal, semana del lead y tipo. El código postal solo
   llega en memoria, con los leads de cada carga: se suma la diferencia entre lo que cuenta ahora cada lead y lo
   ya sumado (walcu_api_cp_contado, sin el código postal). Si el código postal de un lead cambia, lo ya sumado
   se resta del nuevo (no se sabe el anterior); si desaparece, el lead se queda como estaba.
4. panel_stock_vehiculo (además, con el mapeo de estados del vehículo): los coches en stock (ni vendidos ni de
   baja) de las location que cuentan, con su fecha de entrada real. Un traspaso a otra sede mantiene el id: si la
   sede cuenta sigue en stock y, si no, sale del stock sin ser una venta (las ventas solo salen de los leads).
"""

from __future__ import annotations

import hashlib
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import psycopg

from ingesta import concesionario as cc
from ingesta import walcu
from ingesta.campanas import plegar

LOG = logging.getLogger("ingesta.walcu_crm")
SIN_MODELO = "Sin modelo"


# --------------------------------------------------------------------------- mapeo

def _conjunto(valores) -> frozenset[str]:
    return frozenset(plegar(v) for v in valores or [] if plegar(v))


@dataclass(frozen=True)
class Mapeo:
    vendido: frozenset[str]
    reservado: frozenset[str]
    baja: frozenset[str]
    publicado: frozenset[str]
    finales_vehiculo: frozenset[str]
    venta: frozenset[str]
    firmado: frozenset[str]
    matriculado: frozenset[str]
    entregado: frozenset[str]
    descartado: frozenset[str]
    finales_lead: frozenset[str]
    num_inexistente: frozenset[str]
    incluidas: frozenset[str]
    excluidas: frozenset[str]
    vo: frozenset[str]
    tipos: dict[str, str]
    generar: bool

    @property
    def estados_venta(self) -> frozenset[str]:
        return self.venta | self.firmado | self.matriculado | self.entregado

    @property
    def ventas_definidas(self) -> bool:
        return bool(self.estados_venta)

    @property
    def sedes_definidas(self) -> bool:
        return bool(self.incluidas or self.excluidas)

    @property
    def definido(self) -> bool:
        return self.ventas_definidas and self.sedes_definidas

    @property
    def stock_definido(self) -> bool:
        return self.definido and bool(self.vendido or self.baja)

    def es_venta(self, estado) -> bool:
        return plegar(estado) in self.estados_venta

    def cuenta(self, location) -> bool:
        """La location cuenta: no está excluida y, si hay lista de incluidas, está en ella. Sin location, cuenta
        solo si no hay lista de incluidas."""
        p = plegar(location)
        if p and p in self.excluidas:
            return False
        return not self.incluidas or p in self.incluidas

    def tipo(self, t) -> str | None:
        return self.tipos.get(plegar(t))

    def clase_estado_vehiculo(self, estado) -> str | None:
        p = plegar(estado)
        for nombre in ("vendido", "reservado", "baja", "publicado"):
            if p in getattr(self, nombre):
                return nombre
        return None

    def clase_estado_lead(self, estado) -> str | None:
        p = plegar(estado)
        for nombre in ("firmado", "matriculado", "entregado", "venta", "descartado", "num_inexistente"):
            if p in getattr(self, nombre):
                return nombre
        return None

    def clase_location(self, location) -> str | None:
        p = plegar(location)
        if p in self.excluidas:
            return "excluida"
        if p in self.incluidas:
            return "incluida" + (" (VO)" if p in self.vo else "")
        return "VO" if p in self.vo else None


def mapeo(cfg) -> Mapeo:
    m = cfg.mapeo if hasattr(cfg, "mapeo") else cfg
    ev, el, loc = m.get("estados_vehiculo") or {}, m.get("estados_lead") or {}, m.get("locations") or {}
    hitos = el.get("hitos_venta") or {}
    tipos = {plegar(a): t for t, alias in (m.get("tipos") or {}).items() for a in alias or [] if plegar(a)}
    return Mapeo(
        _conjunto(ev.get("vendido")), _conjunto(ev.get("reservado")), _conjunto(ev.get("baja")),
        _conjunto(ev.get("publicado")), _conjunto(ev.get("finales")),
        _conjunto(el.get("venta")), _conjunto(hitos.get("firmado")), _conjunto(hitos.get("matriculado")),
        _conjunto(hitos.get("entregado")), _conjunto(el.get("descartado")), _conjunto(el.get("finales")),
        _conjunto(el.get("num_inexistente")),
        _conjunto(loc.get("incluidas")), _conjunto(loc.get("excluidas")), _conjunto(loc.get("vo")), tipos,
        bool((m.get("semanal") or {}).get("generar", True)))


# --------------------------------------------------------------------------- inventario (sin mapear)

def _mostrable(v) -> str:
    """Un estado o una location tal cual, salvo que parezca un dato personal."""
    if v is None or str(v).strip() == "":
        return "(vacío)"
    return "(omitido: parece un dato personal)" if walcu.personal(str(v)) else str(v)


def inventario(pg: psycopg.Connection, cfg) -> dict:
    m = mapeo(cfg)
    n = {t: pg.execute(f"SELECT count(*) FROM walcu_api_{t}_vigente").fetchone()[0]
         for t in ("vehiculo", "lead", "cita")}
    ev = [(_mostrable(e), c, m.clase_estado_vehiculo(e)) for e, c in pg.execute(
        "SELECT estado, count(*) FROM walcu_api_vehiculo_vigente GROUP BY 1 ORDER BY 2 DESC, 1")]
    el = [(_mostrable(e), c, m.clase_estado_lead(e)) for e, c in pg.execute(
        """SELECT estado, count(DISTINCT lead) FROM (
               SELECT lead_id AS lead, estado FROM walcu_api_lead_estado
               UNION SELECT id_walcu, estado FROM walcu_api_lead_vigente) x
           GROUP BY 1 ORDER BY 2 DESC, 1""")]
    loc = [(_mostrable(lo), v, ld, m.clase_location(lo)) for lo, v, ld in pg.execute(
        """SELECT location, sum(v), sum(l) FROM (
               SELECT location, 1 AS v, 0 AS l FROM walcu_api_vehiculo_vigente
               UNION ALL SELECT location, 0, 1 FROM walcu_api_lead_vigente) x
           GROUP BY 1 ORDER BY 2 + 3 DESC, 1""")]
    tipos = [(_mostrable(t), c, m.tipo(t)) for t, c in pg.execute(
        """SELECT t, count(*) FROM (SELECT tipo AS t FROM walcu_api_vehiculo_vigente
                                   UNION ALL SELECT tipo_vehiculo FROM walcu_api_lead_vigente) x
           GROUP BY 1 ORDER BY 2 DESC, 1""")]
    return {"vehiculos": n["vehiculo"], "leads": n["lead"], "citas": n["cita"], "estados_vehiculo": ev,
            "estados_lead": el, "locations": loc, "tipos": tipos,
            "sin_mapear": {"estados_vehiculo": sum(1 for _, _, c in ev if c is None),
                           "estados_lead": sum(1 for _, _, c in el if c is None),
                           "locations": sum(1 for *_, c in loc if c is None),
                           "tipos": sum(1 for _, _, c in tipos if c is None)},
            "mapeo": {"ventas": m.ventas_definidas, "sedes": m.sedes_definidas, "estados_vehiculo": bool(m.vendido or m.baja)}}


def texto_inventario(inv: dict) -> list[str]:
    def marca(c):
        return c if c else "SIN MAPEAR"
    lineas = [f"Walcu (API): {inv['vehiculos']} coches, {inv['leads']} leads y {inv['citas']} citas.",
              "Estados de los coches (status.status) → estados_vehiculo de config/walcu_api.yaml:"]
    lineas += [f"  «{e}»: {c} coches · {marca(k)}" for e, c, k in inv["estados_vehiculo"]] or ["  —"]
    lineas.append("Estados de los leads (en su historia) → estados_lead:")
    lineas += [f"  «{e}»: {c} leads · {marca(k)}" for e, c, k in inv["estados_lead"]] or ["  —"]
    lineas.append("location (sede, instalación o marca) → locations:")
    lineas += [f"  «{lo}»: {v} coches, {ld} leads · {marca(k)}" for lo, v, ld, k in inv["locations"]] or ["  —"]
    lineas.append("Tipos de vehículo (type) → tipos:")
    lineas += [f"  «{t}»: {c} · {marca(k)}" for t, c, k in inv["tipos"]] or ["  —"]
    m = inv["mapeo"]
    if not (m["ventas"] and m["sedes"]):
        lineas.append("Mapeo pendiente (estados de venta del lead y location): no se deducen ventas ni se excluyen "
                      "sedes; Walcu por semanas sigue con el CSV.")
    return lineas


# --------------------------------------------------------------------------- leads

@dataclass
class Lead:
    id: str
    dia: date | None
    source: str | None
    campana: str | None
    location: str | None
    estado: str | None
    comercial: str | None
    tipo_vehiculo: str | None        # car_list o, sin él, el del coche enlazado
    modelo: str | None
    cita: bool
    venta: date | None               # día de la venta (None: no es venta)
    inexistente: bool
    zona: str | None = None          # cercana, fuera o sin_dato (None: la carga no leía el código postal)
    venta_con_cita: bool = False     # venta con una cita asistida que empezó antes de la venta


def _leads_zona(zona: str | None) -> str:
    return {"cercana": "leads_cercana", "fuera": "leads_fuera"}.get(zona or "", "leads_sin_zona")


def _dia(d: datetime | None) -> date | None:
    from ingesta.walcu_api import MADRID
    return d.astimezone(MADRID).date() if d else None


def momento_venta(historia: list[tuple[str, datetime]], estado: str | None, estado_at: datetime | None,
                  m: Mapeo) -> datetime | None:
    """Si el lead está ahora en un estado de venta: el primer Firmado; si no hay, el primer estado de venta de su
    historia (o, sin historia, su estado actual). Si no está en venta (venta anulada), None."""
    if not m.es_venta(estado):
        return None
    firmado = [f for e, f in historia if plegar(e) in m.firmado]
    ventas = [f for e, f in historia if m.es_venta(e)]
    return min(firmado) if firmado else (min(ventas) if ventas else estado_at)


def fecha_venta(historia: list[tuple[str, datetime]], estado: str | None, estado_at: datetime | None,
                m: Mapeo) -> date | None:
    """El día de momento_venta (Madrid)."""
    return _dia(momento_venta(historia, estado, estado_at, m))


def leer_leads(pg: psycopg.Connection, m: Mapeo) -> list[Lead]:
    historia: dict[str, list] = defaultdict(list)
    for lid, e, f in pg.execute("SELECT lead_id, estado, fecha FROM walcu_api_lead_estado ORDER BY lead_id, fecha"):
        historia[lid].append((e, f))
    leads = []
    citas = {lid: (bool(n), primera) for lid, n, primera in pg.execute(
        """SELECT lead_id, count(*), min(inicio_at) FROM walcu_api_cita_vigente
           WHERE asistio AND coalesce(lead_tipo, 'salelead') = 'salelead' AND lead_id IS NOT NULL GROUP BY 1""")}
    for (lid, alta, source, campana, loc, estado, estado_at, comercial, tipo_l, modelo_l, tipo_v, modelo_v,
         zona) in pg.execute(
            """SELECT l.id_walcu, l.alta_at, l.origen_source, l.origen_campaign, l.location, l.estado, l.estado_at,
                      l.comercial_seudo, l.tipo_vehiculo, l.modelo_vehiculo, v.tipo, v.modelo, l.zona
               FROM walcu_api_lead_vigente l LEFT JOIN walcu_api_vehiculo_vigente v ON v.id_walcu = l.vehiculo_id
               ORDER BY l.id_walcu"""):
        cita, primera = citas.get(lid, (False, None))
        venta_at = momento_venta(historia[lid], estado, estado_at, m)
        leads.append(Lead(lid, _dia(alta), source, campana, loc, estado, comercial, tipo_l or tipo_v,
                          modelo_l or modelo_v, cita, _dia(venta_at), plegar(estado) in m.num_inexistente, zona,
                          venta_at is not None and primera is not None and primera <= venta_at))
    return leads


def comerciales_excluidos(entorno: Mapping[str, str]) -> frozenset[str]:
    """Seudónimos de los comerciales excluidos (WALCU_COMERCIALES_EXCLUIDOS: ids de usuario de Walcu separados por
    comas). Los ids solo se usan aquí, en memoria."""
    from ingesta.walcu_api import seudonimo
    clave = (entorno.get("WALCU_SEUDONIMO_CLAVE") or "").strip()
    ids = [i.strip() for i in (entorno.get("WALCU_COMERCIALES_EXCLUIDOS") or "").split(",") if i.strip()]
    return frozenset(seudonimo(clave, i) for i in ids) if clave else frozenset()


def _agregador_base():
    from scripts.walcu_agregar import Agregador
    return Agregador


class Agregador:
    """Como scripts/walcu_agregar.py (misma configuración de orígenes, palabras de VO y tipo por defecto), pero
    con los leads de la API."""

    def __init__(self, cfg_export: dict, conc: cc.Concesionario, m: Mapeo, excluidos: frozenset[str]):
        base = _agregador_base()
        self.base = base(cfg_export, conc, frozenset())
        self.conc, self.m, self.excluidos = conc, m, excluidos
        self.cuentas: Counter = Counter()
        self.tipo_por: Counter = Counter()
        self.acuerdo: Counter = Counter()
        self.filas = self.base.filas

    def excluido(self, lead: Lead) -> str | None:
        if lead.comercial and lead.comercial in self.excluidos:
            return "comerciales_excluidos"
        if not self.m.cuenta(lead.location):
            return "otra_sede"
        if self.conc.otra_empresa(lead.source) or self.conc.otra_empresa(lead.campana):
            return "otra_empresa"
        if self.conc.otra_marca(lead.campana):
            return "otra_marca"
        return None

    @staticmethod
    def campana(lead: Lead) -> str:
        from scripts.walcu_agregar import OMITIDA, SIN_CAMPANA
        campana = " ".join(str(lead.campana or "").split()) or SIN_CAMPANA
        return OMITIDA if walcu.personal(campana) else campana

    def tipo_y_via(self, lead: Lead, campana: str) -> tuple[str, str]:
        """La regla de siempre: el nombre de la campaña; si no lo dice, location de VO; si no, la tipología de
        car_list; si no, el tipo por defecto."""
        t = self.base.tipo_campana(campana)
        if t:
            return t, "campana"
        if plegar(lead.location) in self.m.vo:
            return "VO", "location"
        if self.m.tipo(lead.tipo_vehiculo):
            return self.m.tipo(lead.tipo_vehiculo), "car_list"
        return self.base.tipo_defecto, "defecto"

    def tipo(self, lead: Lead, campana: str) -> str:
        t, via = self.tipo_y_via(lead, campana)
        self.tipo_por[via] += 1
        de_car_list = self.m.tipo(lead.tipo_vehiculo)
        if de_car_list:
            self.acuerdo["con_tipologia"] += 1
            self.acuerdo["iguales"] += t == de_car_list
        return t

    def anadir(self, lead: Lead, desde: date | None) -> str | None:
        """El tipo del lead si cuenta; None si se excluye. Solo suma a la semana si es de `desde` en adelante."""
        from scripts.walcu_agregar import OTROS
        self.cuentas["leidos"] += 1
        motivo = self.excluido(lead)
        if motivo:
            self.cuentas[motivo] += 1
            return None
        if lead.dia is None:
            self.cuentas["sin_fecha"] += 1
            return None
        campana = self.campana(lead)
        self.cuentas["campanas_omitidas"] += walcu.personal(str(lead.campana or "")) is not None
        tipo = self.tipo(lead, campana)
        semana = lead.dia - timedelta(days=lead.dia.weekday())
        if desde is not None and semana < desde:
            return tipo
        origen = self.base.origenes.get(plegar(lead.source), OTROS)
        a = self.filas[(semana.isoformat(), origen, campana, tipo)]
        a["leads"] += 1
        a[_leads_zona(lead.zona)] += 1
        a["citas"] += lead.cita
        a["ventas"] += lead.venta is not None
        a["ventas_con_cita"] += lead.venta_con_cita
        a["num_inexistente"] += lead.inexistente
        return tipo

    def cuenta_semana(self, lead: Lead, desde: date | None) -> tuple[date, str] | None:
        """(semana, tipo) en que cuenta el lead, sin tocar los recuentos; None si no cuenta."""
        if self.excluido(lead) or lead.dia is None:
            return None
        semana = lead.dia - timedelta(days=lead.dia.weekday())
        if desde is not None and semana < desde:
            return None
        return semana, self.tipo_y_via(lead, self.campana(lead))[0]

    def csv(self) -> bytes:
        return self.base.csv()


def acuerdo_pct(ag: Agregador) -> float | None:
    n = ag.acuerdo["con_tipologia"]
    return round(ag.acuerdo["iguales"] / n * 100, 1) if n else None


# --------------------------------------------------------------------------- escritura

def primer_lunes(d: date | None) -> date | None:
    """La primera semana completa desde `d`."""
    if d is None:
        return None
    return d if d.weekday() == 0 else d + timedelta(days=7 - d.weekday())


def escribir_semanal(pg: psycopg.Connection, contenido: bytes, momento: datetime,
                     con_cita: Mapping[tuple, int] | None = None) -> dict:
    """Valida como un CSV de Walcu y, si cambia algo frente a lo vigente de esas semanas, lo añade (fuente api).
    `con_cita`: (semana ISO, origen, campaña, tipo) → ventas con cita (las de la API; el CSV no las trae)."""
    v = walcu.validar(contenido)
    con_cita = con_cita or {}
    for f in v.filas:
        f["ventas_con_cita"] = int(con_cita.get((f["semana"].isoformat(), f["origen"], f["campana"], f["tipo"]), 0))
    semanas = sorted({f["semana"] for f in v.filas})
    cols = walcu.COLUMNAS + ("ventas_con_cita",)
    nuevas = {tuple(f[c] for c in cols) for f in v.filas}
    vigentes = {tuple(r) for r in pg.execute(
        f"SELECT {', '.join(walcu.COLUMNAS)}, coalesce(ventas_con_cita, ventas) FROM walcu_campana_semana_vigente "
        "WHERE semana BETWEEN %s AND %s", (semanas[0], semanas[-1]))}
    resumen = {"filas": len(v.filas), "semanas": len(semanas), "desde": str(semanas[0]), "hasta": str(semanas[-1]),
               "avisos": v.avisos}
    if nuevas == vigentes:
        return {**resumen, "escrito": False}
    huella = hashlib.sha256(contenido).hexdigest()
    run_id = f"walcu-api-semana-{momento:%Y%m%dT%H%M%S%f}-{huella[:8]}"
    with pg.cursor() as cur:
        cur.executemany(
            f"INSERT INTO walcu_campana_semana (fecha_carga, run_id, fichero_origen, huella_fichero, fuente, "
            f"{', '.join(cols)}) VALUES (%s, %s, 'api', %s, 'api', {', '.join(['%s'] * len(cols))})",
            [(momento, run_id, huella, *(f[c] for c in cols)) for f in v.filas])
    return {**resumen, "escrito": True, "run_id": run_id}


def ventas_crm(leads: list[Lead], ag: Agregador) -> dict[tuple[date, str, str], list[int]]:
    """(mes, tipo, modelo) → [ventas, ventas con cita]. El tipo es la tipología del coche vendido; sin ella, la
    del lead."""
    cuenta: dict = defaultdict(lambda: [0, 0])
    for lead in leads:
        if lead.venta is None or ag.excluido(lead):
            continue
        tipo = ag.m.tipo(lead.tipo_vehiculo) or ag.tipo_y_via(lead, ag.campana(lead))[0]
        c = cuenta[(lead.venta.replace(day=1), tipo, lead.modelo or SIN_MODELO)]
        c[0] += 1
        c[1] += lead.venta_con_cita
    return dict(cuenta)


def ventas_semana(pg: psycopg.Connection, leads: list[Lead], ag: Agregador, desde: date | None, hoy: date) -> dict:
    """Ventas del CRM por semana de la venta, tipo y modelo (walcu_ventas_semana, fuente api; manda sobre el CSV),
    para la regla de caída de ventas de la campaña general. Cubre desde la primera semana completa de la carga
    inicial hasta la semana de hoy. Solo se escribe si cambia algo frente a la última carga de la API."""
    from ingesta import walcu_ventas as wv
    if desde is None:
        return {"escrito": False, "motivo": "sin carga inicial"}
    hasta = hoy - timedelta(days=hoy.weekday())
    cuenta: Counter = Counter()
    for lead in leads:
        if lead.venta is None or ag.excluido(lead):
            continue
        semana = lead.venta - timedelta(days=lead.venta.weekday())
        if desde <= semana <= hasta:
            tipo = ag.m.tipo(lead.tipo_vehiculo) or ag.tipo_y_via(lead, ag.campana(lead))[0]
            cuenta[(semana, tipo, lead.modelo or SIN_MODELO)] += 1
    filas = [{"semana": s, "tipo": t, "modelo": mo, "ventas": n} for (s, t, mo), n in sorted(cuenta.items())]
    h = hashlib.sha256(json.dumps([desde.isoformat(), hasta.isoformat()] + [[str(f["semana"]), f["tipo"], f["modelo"],
                                  f["ventas"]] for f in filas]).encode()).hexdigest()
    previa = pg.execute("SELECT huella FROM walcu_ventas_carga WHERE fuente = 'api' ORDER BY fecha_carga DESC, id DESC "
                        "LIMIT 1").fetchone()
    resumen = {"semanas": (hasta - desde).days // 7 + 1, "ventas": sum(cuenta.values())}
    if previa and previa[0] == h:
        return {**resumen, "escrito": False}
    return {**resumen, "escrito": True, "run_id": wv.escribir(pg, filas, "api", desde, hasta, h)}


# --------------------------------------------------------------------------- código postal (solo agregado)

def actualizar_cp(pg: psycopg.Connection, leads: list[Lead], ag: Agregador, desde: date | None,
                  postales: Mapping[str, str | None], run_id: str) -> dict:
    """Suma a walcu_api_cp_semana la diferencia entre lo que cuenta ahora cada lead de `postales` (los de esta
    carga: id → código postal, solo en memoria) y lo ya sumado (walcu_api_cp_contado). Recuentos, sin códigos."""
    por_id = {lead.id: lead for lead in leads}
    ids = [i for i in postales if i in por_id]
    contado = {r[0]: r[1:] for r in pg.execute(
        """SELECT lead_id, semana, tipo, con_cp, citas, ventas, ventas_con_cita FROM walcu_api_cp_contado_vigente
           WHERE lead_id = ANY(%s)""", (ids,))} if ids else {}
    delta: dict[tuple, list[int]] = defaultdict(lambda: [0, 0, 0, 0])
    nuevos_contados, cuentas = [], Counter()
    for lid in ids:
        lead, cp = por_id[lid], postales[lid]
        antes = contado.get(lid)
        if cp is None and antes is not None and antes[2]:
            cuentas["codigo_postal_perdido"] += 1          # se queda como estaba
            continue
        donde = ag.cuenta_semana(lead, desde)
        ahora = (None, None, cp is not None, 0, 0, 0) if donde is None else \
            (donde[0], donde[1], cp is not None, int(lead.cita), int(lead.venta is not None), int(lead.venta_con_cita))
        if antes is not None and tuple(antes) == ahora:
            continue
        if antes is not None and antes[0] is not None:
            d = delta[(cp if antes[2] else None, antes[0], antes[1])]
            for i, n in enumerate((1, antes[3], antes[4], antes[5])):
                d[i] -= n
        if ahora[0] is not None:
            d = delta[(cp, ahora[0], ahora[1])]
            for i, n in enumerate((1, ahora[3], ahora[4], ahora[5])):
                d[i] += n
        nuevos_contados.append((run_id, lid, *ahora))
        cuentas["con_codigo_postal" if cp else "sin_codigo_postal"] += 1
    filas = [(run_id, cp, semana, tipo, *v) for (cp, semana, tipo), v in delta.items() if any(v)]
    with pg.cursor() as cur:
        if filas:
            cur.executemany("INSERT INTO walcu_api_cp_semana (run_id, codigo_postal, semana, tipo, leads, citas, "
                            "ventas, ventas_con_cita) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", filas)
        if nuevos_contados:
            cur.executemany("INSERT INTO walcu_api_cp_contado (run_id, lead_id, semana, tipo, con_cp, citas, ventas, "
                            "ventas_con_cita) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)", nuevos_contados)
    return {"leads_cambiados": len(nuevos_contados), "filas": len(filas), **dict(cuentas)}


def stock(pg: psycopg.Connection, m: Mapeo, hoy: date) -> tuple[list[dict], Counter]:
    """Coches en stock hoy: ni vendidos ni de baja, de una location que cuenta y con tipo conocido."""
    filas, cuentas = [], Counter()
    for vid, tipo, modelo, version, loc, estado, publicado, entrada, precio in pg.execute(
            """SELECT id_walcu, tipo, modelo, version, location, estado, publicado, fecha_entrada, precio_cents
               FROM walcu_api_vehiculo_vigente ORDER BY id_walcu"""):
        clase = m.clase_estado_vehiculo(estado)
        if clase in ("vendido", "baja") or plegar(estado) in m.finales_vehiculo:
            cuentas[clase or "final"] += 1
            continue
        if not m.cuenta(loc):
            cuentas["otra_sede"] += 1
            continue
        t = m.tipo(tipo)
        if t is None or entrada is None:
            cuentas["sin_tipo" if t is None else "sin_fecha_entrada"] += 1
            continue
        cuentas["en_stock"] += 1
        filas.append({"dia": hoy, "origen": "real", "stock_id": vid, "tipo": t, "modelo": modelo or SIN_MODELO,
                      "version": version, "dias_exposicion": max((hoy - entrada).days, 0), "precio_cents": precio,
                      "publicado": publicado, "fecha_entrada": entrada})
    return filas, cuentas


def entradas(pg: psycopg.Connection) -> dict[str, date]:
    """Bastidor seudonimizado → fecha de entrada (para enlazar los anuncios de la web con el CRM)."""
    return {b: f for b, f in pg.execute(
        "SELECT bastidor_seudo, fecha_entrada FROM walcu_api_vehiculo_vigente "
        "WHERE bastidor_seudo IS NOT NULL AND fecha_entrada IS NOT NULL")}


def _insertar(pg, tabla: str, filas: list[dict]) -> None:
    if not filas:
        return
    cols = list(filas[0])
    with pg.cursor() as cur:
        cur.executemany(f"INSERT INTO {tabla} ({', '.join(cols)}) VALUES ({', '.join(['%s'] * len(cols))})",
                        [tuple(f[c] for c in cols) for f in filas])


def actualizar(pg: psycopg.Connection, cfg, hoy: date, entorno: Mapping[str, str],
               conc: cc.Concesionario | None = None, cfg_export: dict | None = None,
               momento: datetime | None = None, postales: Mapping[str, str | None] | None = None,
               run_id: str | None = None) -> dict:
    """Inventario y, con el mapeo definido, Walcu por semanas, ventas y stock. Devuelve el resumen."""
    inv = inventario(pg, cfg)
    resumen: dict = {"sin_mapear": inv["sin_mapear"], "vehiculos": inv["vehiculos"], "leads": inv["leads"],
                     "citas": inv["citas"]}
    if not (inv["leads"] or inv["vehiculos"]):
        return {**resumen, "estado": "sin datos de la API todavía"}
    m = mapeo(cfg)
    if not m.definido:
        return {**resumen, "estado": "mapeo pendiente: no se deducen ventas ni se excluyen sedes (make walcu-api-mapeo)"}
    from scripts.walcu_agregar import cargar_config
    conc = conc or cc.cargar()
    ag = Agregador(cfg_export or cargar_config(), conc, m, comerciales_excluidos(entorno))
    leads = leer_leads(pg, m)
    from ingesta.walcu_api import cobertura
    desde = primer_lunes(cobertura(pg))
    for lead in leads:
        ag.anadir(lead, desde)
    resumen["exclusiones"] = {k: ag.cuentas[k] for k in ("leidos", "comerciales_excluidos", "otra_sede",
                                                         "otra_empresa", "otra_marca", "sin_fecha")}
    resumen["tipo_por"] = dict(ag.tipo_por)
    resumen["acuerdo_tipo_pct"] = acuerdo_pct(ag)
    momento = momento or datetime.now(timezone.utc)
    with pg.transaction():
        if m.generar and ag.filas:
            try:
                resumen["semanal"] = escribir_semanal(pg, ag.csv(), momento,
                                                      {k: a["ventas_con_cita"] for k, a in ag.filas.items()})
            except walcu.ErrorWalcu as exc:
                resumen["semanal"] = {"escrito": False, "error": exc.problemas[:5]}
        ventas = ventas_crm(leads, ag)
        pg.execute("DELETE FROM panel_ventas_crm WHERE origen = 'real'")
        _insertar(pg, "panel_ventas_crm", [{"dia": hoy, "origen": "real", "mes": mes, "tipo": t, "modelo": mo,
                                            "ventas": n, "ventas_con_cita": c}
                                           for (mes, t, mo), (n, c) in sorted(ventas.items())])
        resumen["ventas"] = sum(n for n, _ in ventas.values())
        resumen["ventas_con_cita"] = sum(c for _, c in ventas.values())
        resumen["ventas_semana"] = ventas_semana(pg, leads, ag, desde, hoy)
        if postales:
            resumen["codigo_postal"] = actualizar_cp(pg, leads, ag, desde, postales,
                                                     run_id or f"walcu-api-cp-{momento:%Y%m%dT%H%M%S%f}")
        if m.stock_definido:
            filas, cuentas = stock(pg, m, hoy)
            pg.execute("DELETE FROM panel_stock_vehiculo WHERE origen = 'real' AND dia = %s", (hoy,))
            _insertar(pg, "panel_stock_vehiculo", filas)
            resumen["stock"] = dict(cuentas)
        else:
            resumen["stock"] = "estados del vehículo sin mapear (vendido o baja): sin stock"
    if (resumen.get("semanal") or {}).get("escrito"):
        from ingesta import referencias_meta as ref
        resumen["referencias"] = ref.actualizar(pg)
    resumen["estado"] = (f"activo: {resumen['ventas']} ventas ({resumen['ventas_con_cita']} con cita); acuerdo del tipo con car_list "
                         f"{resumen['acuerdo_tipo_pct'] if resumen['acuerdo_tipo_pct'] is not None else '—'} %")
    LOG.info("Walcu (CRM): %s", resumen["estado"])
    return resumen
