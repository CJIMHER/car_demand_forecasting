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
   - Tipo: la regla de siempre (el nombre de la campaña; si no lo dice, location de VO; si no, la tipología de
     car_list; si no, el tipo por defecto). Se informa del porcentaje de acuerdo con la tipología de car_list.
   - Sin código postal (no se lee de la API): todos los leads van a leads_sin_zona.
   - Solo se escribe si cambia algo frente a lo vigente; las semanas empiezan en la primera semana completa de
     la carga inicial. Después se rehacen el cruce con Meta y las referencias. El CSV sigue de respaldo.
2. panel_ventas_crm: ventas por mes de la venta (la fecha del primer hito Firmado; si no, la del primer estado de
   venta), tipo (la tipología del coche: car_list o el coche) y modelo. Dirección: «Ventas del mes».
3. panel_stock_vehiculo (además, con el mapeo de estados del vehículo): los coches en stock (ni vendidos ni de
   baja) de las location que cuentan, con su fecha de entrada real. Un traspaso a otra sede mantiene el id: si la
   sede cuenta sigue en stock y, si no, sale del stock sin ser una venta (las ventas solo salen de los leads).
"""

from __future__ import annotations

import hashlib
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


def _dia(d: datetime | None) -> date | None:
    from ingesta.walcu_api import MADRID
    return d.astimezone(MADRID).date() if d else None


def fecha_venta(historia: list[tuple[str, datetime]], estado: str | None, estado_at: datetime | None,
                m: Mapeo) -> date | None:
    """Si el lead está ahora en un estado de venta: el día del primer Firmado; si no hay, el del primer estado de
    venta de su historia (o, sin historia, el de su estado actual). Si no está en venta (venta anulada), None."""
    if not m.es_venta(estado):
        return None
    firmado = [f for e, f in historia if plegar(e) in m.firmado]
    ventas = [f for e, f in historia if m.es_venta(e)]
    cuando = min(firmado) if firmado else (min(ventas) if ventas else estado_at)
    return _dia(cuando)


def leer_leads(pg: psycopg.Connection, m: Mapeo) -> list[Lead]:
    historia: dict[str, list] = defaultdict(list)
    for lid, e, f in pg.execute("SELECT lead_id, estado, fecha FROM walcu_api_lead_estado ORDER BY lead_id, fecha"):
        historia[lid].append((e, f))
    leads = []
    for (lid, alta, source, campana, loc, estado, estado_at, comercial, tipo_l, modelo_l, tipo_v, modelo_v,
         cita) in pg.execute(
            """SELECT l.id_walcu, l.alta_at, l.origen_source, l.origen_campaign, l.location, l.estado, l.estado_at,
                      l.comercial_seudo, l.tipo_vehiculo, l.modelo_vehiculo, v.tipo, v.modelo,
                      EXISTS (SELECT 1 FROM walcu_api_cita_vigente c WHERE c.lead_id = l.id_walcu AND c.asistio
                              AND coalesce(c.lead_tipo, 'salelead') = 'salelead')
               FROM walcu_api_lead_vigente l LEFT JOIN walcu_api_vehiculo_vigente v ON v.id_walcu = l.vehiculo_id
               ORDER BY l.id_walcu"""):
        leads.append(Lead(lid, _dia(alta), source, campana, loc, estado, comercial, tipo_l or tipo_v,
                          modelo_l or modelo_v, bool(cita), fecha_venta(historia[lid], estado, estado_at, m),
                          plegar(estado) in m.num_inexistente))
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
        a["leads_sin_zona"] += 1
        a["citas"] += lead.cita
        a["ventas"] += lead.venta is not None
        a["num_inexistente"] += lead.inexistente
        return tipo

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


def escribir_semanal(pg: psycopg.Connection, contenido: bytes, momento: datetime) -> dict:
    """Valida como un CSV de Walcu y, si cambia algo frente a lo vigente de esas semanas, lo añade (fuente api)."""
    v = walcu.validar(contenido)
    semanas = sorted({f["semana"] for f in v.filas})
    cols = walcu.COLUMNAS
    nuevas = {tuple(f[c] for c in cols) for f in v.filas}
    vigentes = {tuple(r) for r in pg.execute(
        f"SELECT {', '.join(cols)} FROM walcu_campana_semana_vigente WHERE semana BETWEEN %s AND %s",
        (semanas[0], semanas[-1]))}
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


def ventas_crm(leads: list[Lead], ag: Agregador) -> dict[tuple[date, str, str], int]:
    """(mes, tipo, modelo) → ventas. El tipo es la tipología del coche vendido; sin ella, la del lead."""
    cuenta: Counter = Counter()
    for lead in leads:
        if lead.venta is None or ag.excluido(lead):
            continue
        tipo = ag.m.tipo(lead.tipo_vehiculo) or ag.tipo_y_via(lead, ag.campana(lead))[0]
        cuenta[(lead.venta.replace(day=1), tipo, lead.modelo or SIN_MODELO)] += 1
    return dict(cuenta)


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
               momento: datetime | None = None) -> dict:
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
                resumen["semanal"] = escribir_semanal(pg, ag.csv(), momento)
            except walcu.ErrorWalcu as exc:
                resumen["semanal"] = {"escrito": False, "error": exc.problemas[:5]}
        ventas = ventas_crm(leads, ag)
        pg.execute("DELETE FROM panel_ventas_crm WHERE origen = 'real'")
        _insertar(pg, "panel_ventas_crm", [{"dia": hoy, "origen": "real", "mes": mes, "tipo": t, "modelo": mo,
                                            "ventas": n} for (mes, t, mo), n in sorted(ventas.items())])
        resumen["ventas"] = sum(ventas.values())
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
    resumen["estado"] = (f"activo: {resumen['ventas']} ventas; acuerdo del tipo con car_list "
                         f"{resumen['acuerdo_tipo_pct'] if resumen['acuerdo_tipo_pct'] is not None else '—'} %")
    LOG.info("Walcu (CRM): %s", resumen["estado"])
    return resumen
