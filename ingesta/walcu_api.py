"""Conector de la API de Walcu: coches, leads de venta y citas, incremental por updated_at (solo lectura).

Órdenes (en el servidor, con make):
  python -m ingesta.walcu_api incremental            cada día a las 06:30 (make walcu-api)
  python -m ingesta.walcu_api inicial --desde AAAA-MM-DD   carga inicial por tramos mensuales (make walcu-api-inicial)
  python -m ingesta.walcu_api probar                 una página de cada recurso: solo nombres de campo, tipos y
                                                     recuentos, ningún valor (make walcu-api-probar)
  python -m ingesta.walcu_api mapeo                  estados y location encontrados, con sus recuentos, para
                                                     completar config/walcu_api.yaml (make walcu-api-mapeo)

Privacidad (config/walcu_api.yaml):
- Lista blanca de campos por recurso: todo lo demás se descarta en memoria, antes de guardar nada. Lo que se
  guarda en el bucket es ese JSON ya filtrado, nunca la respuesta original.
- Clientes, comerciales, matrículas y bastidores se guardan solo como HMAC-SHA256 con WALCU_SEUDONIMO_CLAVE.
- Nombres, teléfonos, correos, direcciones, DNI, notas, comentarios, «computed» y campos personalizados no se
  admiten en la lista blanca (el conector no arranca si aparecen). Un texto de la lista blanca con aspecto de
  dato personal se guarda vacío.
- Única excepción a «computed»: el código postal del cliente del lead (saleleads.codigo_postal), solo ese campo
  y solo en memoria. Del lead se guardan la zona (cercana, fuera o sin_dato) y la provincia (dos primeros
  dígitos); el código postal completo, solo en el agregado por semana y tipo (walcu_api_cp_semana), nunca junto
  al lead, al cliente, en el bucket ni en el registro.
- El registro y los errores no llevan valores: solo nombres de campo y recuentos. Los errores de la API no
  copian su mensaje, solo el código HTTP.
- El cliente HTTP solo sabe hacer GET.

Descarga: incremental por updated_at con una marca de agua por recurso (walcu_api_marca); inserción idempotente
(versionada por huella, como Meta y GA4: volver a descargar no duplica). Reintentos ante errores de red, 429 y
5xx; 401 o 403 para en seco y lo dice. Estado en fuentes_estado (fuente walcu_api, tipo descarga). Sin
WALCU_API_KEY no hace ninguna petición, anota «sin_configurar» y termina sin error (no dispara la alerta de fuente
caída). Los leads con citas nuevas que no vienen en la descarga de leads se piden uno a uno (GET), para sumar
su cita en el agregado por código postal.

Después de cada carga, ingesta/walcu_crm.py deriva lo que pueda: con el mapeo de config/walcu_api.yaml vacío,
solo informa de los estados y location sin mapear.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import psycopg
import yaml

from ingesta import conectores as cx
from ingesta.walcu import personal

LOG = logging.getLogger("ingesta.walcu_api")
FUENTE = "walcu_api"
RECURSOS = ("cars", "saleleads", "dates")
MADRID = ZoneInfo("Europe/Madrid")
RAIZ = Path(__file__).resolve().parent.parent

# Campos que nunca se admiten en la lista blanca: un segmento de la ruta que es uno de estos o que contiene
# uno de los fragmentos
PROHIBIDOS = frozenset({"computed", "custom", "notes", "note", "comments", "comment", "inquiry", "description",
                        "title", "government_id", "tax_id", "contacts", "provider", "address", "first_name",
                        "last_name", "raw_name", "salutation", "gender", "coordinates"})
FRAGMENTOS_PROHIBIDOS = ("phone", "email", "address", "name", "note", "comment", "dni", "nif", "gdpr", "contact",
                         "postal", "birth")


class ErrorConfiguracion(Exception):
    pass


class CredencialRechazada(cx.ErrorDescarga):
    """401 o 403: la credencial no vale o no tiene permiso. No se reintenta."""


# --------------------------------------------------------------------------- configuración

@dataclass(frozen=True)
class Recurso:
    nombre: str
    ruta: str
    campos: tuple[str, ...]
    seudonimizar: frozenset[str]
    codigo_postal: str = ""          # solo saleleads: ruta del código postal (solo en memoria)
    ruta_uno: str = ""               # GET de un elemento por id: /saleleads/{id}


@dataclass(frozen=True)
class Config:
    url_base: str
    cabecera: str
    prefijo: str
    filtro_desde: str
    filtro_hasta: str
    solape: timedelta
    paginacion: dict
    reintentos: int
    timeout: float
    recursos: dict[str, Recurso]
    mapeo: dict = field(default_factory=dict)       # estados_vehiculo, estados_lead, locations, tipos, semanal
    leads_por_cita_max: int = 2000


def ruta_config() -> Path:
    return Path(os.environ.get("CDF_WALCU_API") or RAIZ / "config" / "walcu_api.yaml")


def _segmentos(ruta: str) -> list[str]:
    return [s[:-2] if s.endswith("[]") else s for s in ruta.split(".")]


def prohibido(ruta: str, permitidos: frozenset[str] = frozenset()) -> bool:
    """Si la ruta de la lista blanca toca un campo personal (o un campo personalizado no confirmado)."""
    if ruta in permitidos:
        return False
    for s in _segmentos(ruta):
        if s in PROHIBIDOS or any(f in s.lower() for f in FRAGMENTOS_PROHIBIDOS):
            return True
    return False


def cargar(fichero: Path | None = None) -> Config:
    datos = yaml.safe_load((fichero or ruta_config()).read_text(encoding="utf-8")) or {}
    try:
        aut, filtro, pag = datos["autenticacion"], datos["filtro"], dict(datos["paginacion"])
        permitidos = frozenset(str(c) for c in datos.get("personalizados_no_personales") or [])
        recursos = {}
        for nombre in RECURSOS:
            r = datos["recursos"][nombre]
            campos = tuple(str(c) for c in r["campos"])
            seud = frozenset(str(c) for c in r.get("seudonimizar") or [])
            malos = [c for c in campos if prohibido(c, permitidos)]
            if malos:
                raise ErrorConfiguracion(f"{nombre}: campos personales en la lista blanca: {', '.join(malos)}")
            if not seud <= set(campos):
                raise ErrorConfiguracion(f"{nombre}: seudonimizar tiene campos fuera de la lista blanca: "
                                         + ", ".join(sorted(seud - set(campos))))
            if "_id" not in campos or "updated_at" not in campos:
                raise ErrorConfiguracion(f"{nombre}: la lista blanca necesita _id y updated_at")
            cp = str(r.get("codigo_postal") or "").strip()
            if cp and (nombre != "saleleads" or cp.split(".")[-1] != "postal_code" or "[]" in cp or cp in campos):
                raise ErrorConfiguracion(f"{nombre}: codigo_postal solo vale en saleleads, para un único campo "
                                         "postal_code fuera de la lista blanca")
            recursos[nombre] = Recurso(nombre, str(r["ruta"]), campos, seud, cp, str(r.get("ruta_uno") or ""))
        if pag.get("tipo") not in ("pagina", "cursor"):
            raise ErrorConfiguracion("paginacion.tipo tiene que ser pagina o cursor")
        if int(pag["tamano"]) < 1:
            raise ErrorConfiguracion("paginacion.tamano tiene que ser al menos 1")
        mapeo = {k: datos.get(k) or {} for k in ("estados_vehiculo", "estados_lead", "locations", "tipos", "semanal")}
        return Config(str(datos["url_base"]), str(aut["cabecera"]), str(aut.get("prefijo") or ""),
                      str(filtro["desde"]), str(filtro["hasta"]), timedelta(minutes=int(filtro["solape_minutos"])),
                      pag, int(datos["reintentos"]), float(datos["timeout_segundos"]), recursos, mapeo,
                      int(datos.get("leads_por_cita_max", 2000)))
    except (KeyError, TypeError, ValueError) as exc:
        raise ErrorConfiguracion(f"config/walcu_api.yaml incompleto: {type(exc).__name__} {exc}") from None


# --------------------------------------------------------------------------- seudónimos y filtro

def normalizar(valor) -> str:
    """Para que el mismo identificador dé siempre el mismo seudónimo: sin espacios ni guiones, en mayúsculas."""
    return re.sub(r"[\s-]+", "", str(valor)).upper()


def seudonimo(clave: str, valor) -> str:
    """HMAC-SHA256 (hex) del valor normalizado, con la clave secreta WALCU_SEUDONIMO_CLAVE."""
    return hmac.new(clave.encode("utf-8"), normalizar(valor).encode("utf-8"), hashlib.sha256).hexdigest()


def _arbol(campos: tuple[str, ...]) -> dict:
    raiz: dict = {}
    for c in campos:
        nodo = raiz
        partes = c.split(".")
        for i, p in enumerate(partes):
            lista = p.endswith("[]")
            ent = nodo.setdefault(p[:-2] if lista else p, {"lista": lista, "hijos": {}, "hoja": None})
            if i == len(partes) - 1:
                ent["hoja"] = c
            nodo = ent["hijos"]
    return raiz


class Filtro:
    """Lista blanca de un recurso: copia solo esos campos y seudonimiza los que toca. Cuenta lo descartado por
    nombre de campo (nunca guarda valores)."""

    def __init__(self, recurso: Recurso, clave: str):
        if not clave:
            raise ErrorConfiguracion("falta WALCU_SEUDONIMO_CLAVE: sin ella no se guarda nada")
        self.recurso, self.clave = recurso, clave
        self.arbol = _arbol(recurso.campos)
        self.cuentas: Counter = Counter()

    def __call__(self, obj: Mapping) -> dict:
        self.cuentas["elementos"] += 1
        if isinstance(obj, Mapping):
            for k in obj:
                if k not in self.arbol:
                    self.cuentas["descartados"] += 1
        return self._objeto(obj, self.arbol)

    def _objeto(self, obj, arbol: dict) -> dict:
        salida = {}
        if not isinstance(obj, Mapping):
            return salida
        for k, ent in arbol.items():
            if k not in obj:
                continue
            v = obj[k]
            if ent["lista"]:
                if isinstance(v, list):
                    salida[k] = [self._valor(e, ent) for e in v]
            else:
                salida[k] = self._valor(v, ent)
        return salida

    def _valor(self, v, ent):
        if ent["hoja"] is None:
            return self._objeto(v, ent["hijos"]) if isinstance(v, Mapping) else None
        if isinstance(v, (Mapping, list)):          # la lista blanca dice campo simple: lo demás no se guarda
            self.cuentas["estructura_inesperada"] += 1
            return None
        if v is None:
            return None
        if ent["hoja"] in self.recurso.seudonimizar:
            return seudonimo(self.clave, v)
        if isinstance(v, str) and personal(v):
            self.cuentas["vaciados_por_parecer_personales"] += 1
            return None
        return v


def valor(obj, ruta: str):
    """Valor de una ruta «a.b» (None si falta); «a[].b» da la lista de los b."""
    actual = obj
    partes = ruta.split(".")
    for i, p in enumerate(partes):
        if p.endswith("[]"):
            lista = actual.get(p[:-2]) if isinstance(actual, Mapping) else None
            if not isinstance(lista, list):
                return []
            resto = ".".join(partes[i + 1:])
            return [valor(e, resto) if resto else e for e in lista]
        actual = actual.get(p) if isinstance(actual, Mapping) else None
        if actual is None:
            return None
    return actual


# --------------------------------------------------------------------------- código postal (solo en memoria)

def codigo_postal(v) -> str | None:
    """Código postal español de 5 dígitos (01–52), o None. Admite 4 dígitos (sin el cero) y «41001.0»."""
    if v is None or isinstance(v, (bool, Mapping, list)):
        return None
    t = re.sub(r"\.0$", "", str(v).strip())
    if not re.fullmatch(r"\d{4,5}", t):
        return None
    t = t.zfill(5)
    return t if "01" <= t[:2] <= "52" else None


def zona(cp: str | None, cercanas) -> str:
    """cercana (la provincia del concesionario o una de las cercanas), fuera o sin_dato."""
    if cp is None:
        return "sin_dato"
    return "cercana" if cp[:2] in cercanas else "fuera"


# --------------------------------------------------------------------------- cliente (solo GET)

class Cliente:
    """Cliente de la API de Walcu. Solo GET: no tiene ningún método para escribir."""

    def __init__(self, cfg: Config, api_key: str, dealer_id: str, dormir: Callable = time.sleep):
        self.cfg, self._clave, self._dealer, self._dormir = cfg, api_key, dealer_id, dormir
        self.base = cfg.url_base.replace("{dealer_id}", dealer_id).rstrip("/")
        self.peticiones = 0

    def get(self, ruta: str, parametros: Mapping[str, object]):
        url = self.base + ruta + ("?" + urlencode(parametros) if parametros else "")
        self.peticiones += 1
        try:
            datos, bruto = cx.peticion_json(
                url, metodo="GET", cabeceras={self.cfg.cabecera: self.cfg.prefijo + self._clave},
                secretos=[self._clave, self._dealer], dormir=self._dormir, intentos=self.cfg.reintentos,
                timeout=self.cfg.timeout, con_mensaje=False)
        except cx.ErrorDescarga as exc:
            if exc.codigo in (401, 403):
                raise CredencialRechazada(
                    f"Walcu rechazó la credencial (HTTP {exc.codigo}): revisa WALCU_API_KEY, WALCU_DEALER_ID y "
                    "la cabecera de autenticacion de config/walcu_api.yaml", exc.codigo) from None
            raise
        del bruto                                   # la respuesta original no se guarda nunca
        return datos


def elementos(datos, donde: str) -> list:
    lista = datos if not donde else valor(datos, donde)
    if not isinstance(lista, list):
        raise cx.ErrorDescarga("la respuesta no trae la lista de elementos donde dice paginacion.elementos")
    return lista


def paginas(cliente: Cliente, ruta: str, parametros: Mapping[str, object]) -> Iterator[tuple[list, object]]:
    """(elementos, respuesta) de cada página, con la paginación de la configuración."""
    p = cliente.cfg.paginacion
    tamano, maximo = int(p["tamano"]), int(p.get("max_paginas") or 10000)
    if p["tipo"] == "pagina":
        q = p["pagina"]
        n = int(q.get("primera", 1))
        for _ in range(maximo):
            datos = cliente.get(ruta, {**parametros, q["parametro"]: n, q["parametro_tamano"]: tamano})
            lista = elementos(datos, p.get("elementos") or "")
            yield lista, datos
            if len(lista) < tamano:
                return
            n += 1
    else:
        q = p["cursor"]
        cursor = None
        for _ in range(maximo):
            pet = {**parametros, q["parametro_tamano"]: tamano}
            if cursor:
                pet[q["parametro"]] = cursor
            datos = cliente.get(ruta, pet)
            lista = elementos(datos, p.get("elementos") or "")
            yield lista, datos
            cursor = valor(datos, q["siguiente"]) if isinstance(datos, Mapping) else None
            if not cursor or not lista:
                return
    raise cx.ErrorDescarga(f"más de {maximo} páginas en una petición (paginacion.max_paginas)")


# --------------------------------------------------------------------------- fechas

def instante(v) -> datetime | None:
    """ISO 8601 de la API → datetime con zona (UTC si no la trae). None si no se entiende."""
    if v is None or v == "":
        return None
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def dia_madrid(v) -> date | None:
    d = instante(v)
    return d.astimezone(MADRID).date() if d else None


def iso(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def tramos_mensuales(desde: date, hasta: datetime) -> list[tuple[datetime, datetime]]:
    """Meses naturales (hora de Madrid) desde `desde` hasta `hasta`; el último acaba en `hasta`."""
    tramos = []
    ini = datetime(desde.year, desde.month, desde.day, tzinfo=MADRID)
    while ini < hasta:
        sig = datetime(ini.year + (ini.month == 12), ini.month % 12 + 1, 1, tzinfo=MADRID)
        tramos.append((ini, min(sig, hasta)))
        ini = sig
    return tramos


# --------------------------------------------------------------------------- descarga

class Postales:
    """Código postal de los leads, solo en memoria: id del lead → código postal (o None). Al elemento filtrado
    (lo que se guarda y va al bucket) solo se le añaden la zona y la provincia."""

    def __init__(self, ruta: str, cercanas):
        self.ruta, self.cercanas = ruta, frozenset(cercanas)
        self.por_lead: dict[str, str | None] = {}

    def __call__(self, crudo, filtrado: dict) -> None:
        cp = codigo_postal(valor(crudo, self.ruta)) if isinstance(crudo, Mapping) else None
        filtrado["zona"] = zona(cp, self.cercanas)
        filtrado["provincia"] = cp[:2] if cp else None
        lid = _texto(crudo.get("_id")) if isinstance(crudo, Mapping) else None
        if lid:
            self.por_lead[lid] = cp


def descargar(cliente: Cliente, filtro: Filtro, desde: datetime, hasta: datetime,
              postales: Postales | None = None) -> tuple[list[dict], list[bytes]]:
    """Elementos filtrados de un recurso actualizados en [desde, hasta) y el JSON filtrado de cada página (para el
    bucket). Si llega algo actualizado antes de `desde`, el filtro de la API no se está aplicando: para. Con
    `postales` (leads), lee el código postal de cada elemento en memoria."""
    cfg = cliente.cfg
    filtrados, brutos = [], []
    margen = timedelta(minutes=1)
    for lista, _ in paginas(cliente, filtro.recurso.ruta, {cfg.filtro_desde: iso(desde), cfg.filtro_hasta: iso(hasta)}):
        pagina = []
        for e in lista:
            act = instante(e.get("updated_at")) if isinstance(e, Mapping) else None
            if act is not None and act < desde - margen:
                raise cx.ErrorDescarga(
                    f"{filtro.recurso.nombre}: la API devuelve elementos actualizados antes de lo pedido (el filtro "
                    "por updated_at no se aplica); se para para no descargarlo todo. Revisa filtro en "
                    "config/walcu_api.yaml con make walcu-api-probar")
            f = filtro(e)
            if postales is not None:
                postales(e, f)
            pagina.append(f)
        filtrados.extend(pagina)
        if pagina:
            brutos.append(json.dumps(pagina, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return filtrados, brutos


def leads_sueltos(cliente: Cliente, filtro: Filtro, ids: list[str], postales: Postales | None,
                  cuentas: Counter) -> tuple[list[dict], list[bytes]]:
    """Los leads de `ids`, uno a uno (GET de ruta_uno), filtrados como los demás. Los que ya no existen (404) se
    cuentan y se saltan."""
    ruta = filtro.recurso.ruta_uno
    filtrados = []
    if not ruta:
        cuentas["sin_ruta_uno"] += len(ids)
        return [], []
    for lid in ids:
        try:
            datos = cliente.get(ruta.replace("{id}", lid), {})
        except CredencialRechazada:
            raise
        except cx.ErrorDescarga as exc:
            if exc.codigo == 404:
                cuentas["no_encontrados"] += 1
                continue
            raise
        e = datos if isinstance(datos, Mapping) and "_id" in datos else None
        if e is None:
            try:
                lista = elementos(datos, cliente.cfg.paginacion.get("elementos") or "")
            except cx.ErrorDescarga:
                lista = []
            e = lista[0] if lista and isinstance(lista[0], Mapping) else None
        if e is None:
            cuentas["respuesta_inesperada"] += 1
            continue
        f = filtro(e)
        if postales is not None:
            postales(e, f)
        filtrados.append(f)
        cuentas["pedidos"] += 1
    brutos = [json.dumps(filtrados, ensure_ascii=False, sort_keys=True).encode("utf-8")] if filtrados else []
    return filtrados, brutos


# --------------------------------------------------------------------------- guardado

def _cents(v) -> int | None:
    try:
        return None if v is None else int(round(float(v) * 100))
    except (TypeError, ValueError):
        return None


def _entero(v) -> int | None:
    try:
        return None if v is None or isinstance(v, bool) else int(v)
    except (TypeError, ValueError):
        return None


def _texto(v) -> str | None:
    if v is None or isinstance(v, (dict, list)):
        return None
    s = str(v).strip()
    return s or None


def _primero(lista):
    return lista[0] if isinstance(lista, list) and lista else None


def _estado(v):
    return _texto(v.get("status")) if isinstance(v, Mapping) else _texto(v)


def fila_vehiculo(e: dict) -> dict:
    entrada = dia_madrid(e.get("purchase_date")) or dia_madrid(e.get("created_at"))
    return {"id_walcu": _texto(e.get("_id")), "tipo": _texto(e.get("type")), "marca": _texto(e.get("make")),
            "modelo": _texto(e.get("model")), "version": _texto(e.get("version")),
            "combustible": _texto(e.get("fuel")), "codigo_version": _texto(valor(e, "provider_codes.eurotax")),
            "location": _texto(e.get("location")), "fecha_entrada": entrada,
            "fecha_entrada_origen": "purchase_date" if dia_madrid(e.get("purchase_date")) else
            ("created_at" if entrada else None),
            "publicado": e.get("published") if isinstance(e.get("published"), bool) else None,
            "despublicado_at": instante(e.get("unpublished_at")),
            "precio_cents": _cents(valor(e, "prices.retail.current_price.price")), "km": _entero(e.get("mileage")),
            "anio": _entero(e.get("year")), "color": _texto(e.get("color")),
            "estado": _texto(valor(e, "status.status")), "estado_at": instante(valor(e, "status.assigned_at")),
            "matricula_seudo": _texto(e.get("license_plate")), "bastidor_seudo": _texto(e.get("vin")),
            "creado_at": instante(e.get("created_at")), "actualizado_at": instante(e.get("updated_at"))}


def fila_lead(e: dict) -> dict:
    coche = _primero(e.get("car_list")) or {}
    return {"id_walcu": _texto(e.get("_id")), "cliente_seudo": _texto(e.get("client_id")),
            "alta_at": instante(e.get("created_at")), "origen_source": _texto(valor(e, "origin.source")),
            "origen_medium": _texto(valor(e, "origin.medium")), "origen_campaign": _texto(valor(e, "origin.campaign")),
            "location": _texto(e.get("location")), "estado": _texto(valor(e, "status.status")),
            "estado_at": instante(valor(e, "status.assigned_at")),
            "comercial_seudo": _texto(valor(e, "current_assigned.to")),
            "vehiculo_id": _texto(coche.get("car_id")), "tipo_vehiculo": _texto(valor(coche, "car.type")),
            "marca_vehiculo": _texto(valor(coche, "car.make")), "modelo_vehiculo": _texto(valor(coche, "car.model")),
            "coches": len(e.get("car_list") or []), "actualizado_at": instante(e.get("updated_at")),
            "zona": e.get("zona") if e.get("zona") in ("cercana", "fuera", "sin_dato") else None,
            "provincia": e.get("provincia") if re.fullmatch(r"\d\d", str(e.get("provincia") or "")) else None}


def fila_cita(e: dict) -> dict:
    asistio = e.get("showed_up")
    return {"id_walcu": _texto(e.get("_id")), "lead_id": _texto(e.get("lead_id")),
            "lead_tipo": _texto(e.get("lead_type")), "cliente_seudo": _texto(e.get("client_id")),
            "vehiculo_id": _texto(valor(e, "salelead.car_id")), "inicio_at": instante(e.get("start_date")),
            "asistio": asistio if isinstance(asistio, bool) else None, "estado": _estado(e.get("status")),
            "actualizado_at": instante(e.get("updated_at"))}


def _historia(e: dict, con_usuario: bool) -> list[tuple]:
    filas = []
    for h in e.get("status_history") or []:
        if not isinstance(h, Mapping):
            continue
        estado, fecha = _texto(h.get("status")), instante(h.get("assigned_at"))
        if estado and fecha:
            filas.append((estado, fecha, _texto(h.get("user"))) if con_usuario else (estado, fecha))
    return filas


TABLAS = {"cars": ("walcu_api_vehiculo", fila_vehiculo), "saleleads": ("walcu_api_lead", fila_lead),
          "dates": ("walcu_api_cita", fila_cita)}


def _huella(fila: dict) -> str:
    return hashlib.sha256(json.dumps(fila, sort_keys=True, default=str).encode()).hexdigest()


def guardar(pg: psycopg.Connection, recurso: str, filtrados: list[dict], run_id: str, momento: datetime) -> dict:
    """Añade las versiones nuevas o cambiadas (idempotente) y la historia de estados. Recuentos."""
    tabla, hacer = TABLAS[recurso]
    filas, sin_id = {}, 0
    for e in filtrados:
        f = hacer(e)
        if not f["id_walcu"]:
            sin_id += 1
            continue
        f["huella"] = _huella(f)
        filas[f["id_walcu"]] = (f, e)              # el último de la descarga, si viene repetido
    vigentes = dict(pg.execute(f"SELECT id_walcu, huella FROM {tabla}_vigente WHERE id_walcu = ANY(%s)",
                               (list(filas),))) if filas else {}
    nuevas = [f for f, _ in filas.values() if vigentes.get(f["id_walcu"]) != f["huella"]]
    if nuevas:
        cols = list(nuevas[0])
        with pg.cursor() as cur:
            cur.executemany(f"INSERT INTO {tabla} (run_id, capturado_at, {', '.join(cols)}) "
                            f"VALUES (%s, %s, {', '.join(['%s'] * len(cols))})",
                            [(run_id, momento, *(f[c] for c in cols)) for f in nuevas])
    historia = 0
    if recurso in ("cars", "saleleads"):
        lead = recurso == "saleleads"
        hist = [(i, *h) for i, (_, e) in filas.items() for h in _historia(e, lead)]
        if hist:
            with pg.cursor() as cur:
                if lead:
                    cur.executemany("INSERT INTO walcu_api_lead_estado (lead_id, estado, fecha, usuario_seudo, run_id) "
                                    "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING", [(*h, run_id) for h in hist])
                else:
                    cur.executemany("INSERT INTO walcu_api_vehiculo_estado (vehiculo_id, estado, fecha, run_id) "
                                    "VALUES (%s, %s, %s, %s) ON CONFLICT DO NOTHING", [(*h, run_id) for h in hist])
                historia = len(hist)
    return {"elementos": len(filtrados), "nuevas": len(nuevas), "iguales": len(filas) - len(nuevas),
            "sin_id": sin_id, "estados": historia}


def ultima_marca(pg: psycopg.Connection, recurso: str) -> datetime | None:
    r = pg.execute("SELECT marca FROM walcu_api_marca WHERE recurso = %s ORDER BY id DESC LIMIT 1", (recurso,)).fetchone()
    return r[0] if r else None


def cobertura(pg: psycopg.Connection) -> date | None:
    """Primer día de la carga inicial (la más antigua)."""
    return pg.execute("SELECT min(cobertura_desde) FROM walcu_api_marca").fetchone()[0]


def foto_precios(pg: psycopg.Connection, dia: date, run_id: str) -> int:
    """Foto diaria del precio de los coches publicados (Walcu no fecha las bajadas). Una por coche y día."""
    return pg.execute(
        """INSERT INTO walcu_api_vehiculo_precio_dia (vehiculo_id, dia, precio_cents, run_id)
           SELECT id_walcu, %s, precio_cents, %s FROM walcu_api_vehiculo_vigente
           WHERE publicado AND precio_cents IS NOT NULL ON CONFLICT DO NOTHING""", (dia, run_id)).rowcount


# --------------------------------------------------------------------------- ejecución

def configurada(entorno: Mapping[str, str]) -> bool:
    """La API de Walcu está configurada si hay WALCU_API_KEY en el .env."""
    return bool((entorno.get("WALCU_API_KEY") or "").strip())


def credenciales(entorno: Mapping[str, str]) -> tuple[str, str, str]:
    """Sin WALCU_API_KEY: SinCredenciales (sin configurar, no es un error). Con ella pero sin el resto: error."""
    clave, dealer = (entorno.get("WALCU_API_KEY") or "").strip(), (entorno.get("WALCU_DEALER_ID") or "").strip()
    if not clave:
        raise cx.SinCredenciales("falta WALCU_API_KEY en el .env")
    if not dealer:
        raise ErrorConfiguracion("hay WALCU_API_KEY pero falta WALCU_DEALER_ID en el .env")
    seud = (entorno.get("WALCU_SEUDONIMO_CLAVE") or "").strip()
    if len(seud) < 32:
        raise ErrorConfiguracion("WALCU_SEUDONIMO_CLAVE falta o es corta (al menos 32 caracteres: openssl rand -hex 32)")
    return clave, dealer, seud


def ejecutar_con(pg: psycopg.Connection, hoy: date, entorno: Mapping[str, str], cfg: Config | None = None,
                 desde: date | None = None, subir=None, dormir: Callable = time.sleep,
                 momento: datetime | None = None, conc=None) -> dict:
    """Carga incremental (o inicial, con `desde`). Lanza SinCredenciales, CredencialRechazada o ErrorDescarga."""
    clave, dealer, seud = credenciales(entorno)
    cfg = cfg or cargar()
    if conc is None:
        from ingesta import concesionario as cc
        conc = cc.cargar()
    cliente = Cliente(cfg, clave, dealer, dormir)
    momento = momento or cx.ahora()
    run_id = f"walcu-api-{momento:%Y%m%dT%H%M%S%f}"
    resumen: dict = {"run_id": run_id, "modo": "inicial" if desde else "incremental", "recursos": {},
                     "paginacion": f"{cfg.paginacion['tipo']} (pendiente de confirmar con Walcu)"}
    if desde:
        resumen["desde"] = str(desde)
    r_leads = cfg.recursos["saleleads"]
    postales = Postales(r_leads.codigo_postal, conc.zona_cercana) if r_leads.codigo_postal else None
    citas_de: set[str] = set()
    for recurso in RECURSOS:
        if desde:
            tramos = tramos_mensuales(desde, momento)
        else:
            marca = ultima_marca(pg, recurso)
            if marca is None:
                raise cx.ErrorDescarga(f"{recurso}: sin carga inicial todavía (make walcu-api-inicial DESDE=AAAA-MM-DD)")
            tramos = [(marca - cfg.solape, momento)]
        filtro = Filtro(cfg.recursos[recurso], seud)
        total: Counter = Counter()
        for ini, fin in tramos:
            filtrados, brutos = descargar(cliente, filtro, ini, fin, postales if recurso == "saleleads" else None)
            if recurso == "dates":
                citas_de.update(lid for e in filtrados if (lid := _texto(e.get("lead_id")))
                                and (_texto(e.get("lead_type")) or "salelead") == "salelead")
            if brutos:          # el JSON ya filtrado, al bucket antes de escribir nada en Postgres
                cx.guardar_brutos(FUENTE, f"{run_id}_{recurso}_{ini.astimezone(MADRID):%Y%m%d}", hoy, brutos, subir)
            with pg.transaction():
                total.update(guardar(pg, recurso, filtrados, run_id, momento))
                total["paginas"] += len(brutos)
        pg.execute("INSERT INTO walcu_api_marca (recurso, marca, run_id, cobertura_desde) VALUES (%s, %s, %s, %s)",
                   (recurso, momento, run_id, desde))
        resumen["recursos"][recurso] = {**dict(total), **{f"filtro_{k}": n for k, n in filtro.cuentas.items()}}
        LOG.info("Walcu %s: %s elementos, %s versiones nuevas, %s campos descartados", recurso,
                 total["elementos"], total["nuevas"], filtro.cuentas["descartados"])
    if postales is not None:
        # Leads con citas de esta carga que no han venido en la de leads: uno a uno, para su código postal
        faltan = sorted(citas_de - set(postales.por_lead))
        sueltos: Counter = Counter()
        pedir = faltan[:max(cfg.leads_por_cita_max, 0)]
        sueltos["sin_pedir"] = len(faltan) - len(pedir)
        filtro = Filtro(r_leads, seud)
        filtrados, brutos = leads_sueltos(cliente, filtro, pedir, postales, sueltos)
        if brutos:
            cx.guardar_brutos(FUENTE, f"{run_id}_saleleads_por_cita", hoy, brutos, subir)
        with pg.transaction():
            sueltos.update({f"guardado_{k}": n for k, n in guardar(pg, "saleleads", filtrados, run_id, momento).items()})
        resumen["leads_por_cita"] = dict(sueltos)
        resumen["codigo_postal"] = {"leads": len(postales.por_lead),
                                    "con_codigo_postal": sum(1 for c in postales.por_lead.values() if c)}
    resumen["peticiones"] = cliente.peticiones
    resumen["precios_del_dia"] = foto_precios(pg, hoy, run_id)
    from ingesta import walcu_crm
    resumen["crm"] = walcu_crm.actualizar(pg, cfg, hoy, entorno, conc=conc, momento=momento,
                                          postales=postales.por_lead if postales is not None else None,
                                          run_id=run_id)
    return resumen


def ejecutar(entorno: Mapping[str, str] | None = None, desde: date | None = None, subir=None,
             dormir: Callable = time.sleep) -> int:
    from ingesta.db import conectar
    from ingesta.scrapers_vw import hoy_madrid
    entorno = os.environ if entorno is None else entorno
    hoy = hoy_madrid()
    if not configurada(entorno) and desde:
        LOG.error("Walcu sin configurar: falta WALCU_API_KEY en el .env")
        return 1
    secretos = [entorno.get("WALCU_API_KEY"), entorno.get("WALCU_DEALER_ID"), entorno.get("WALCU_SEUDONIMO_CLAVE")]
    try:
        with conectar(autocommit=True) as pg:
            try:
                resumen = ejecutar_con(pg, hoy, entorno, desde=desde, subir=subir, dormir=dormir)
            except cx.SinCredenciales as exc:
                cx.anotar(pg, FUENTE, hoy, "sin_configurar", {"motivo": str(exc)})
                LOG.info("Walcu (API) sin configurar (%s): no se descarga nada", exc)
                return 0
            except Exception as exc:
                error = cx.limpiar(f"{type(exc).__name__}: {exc}", secretos)[:500]
                cx.anotar(pg, FUENTE, hoy, "error", {"error": error})
                LOG.error("Descarga de Walcu fallida: %s", error)
                return 1
            cx.anotar(pg, FUENTE, hoy, "ok", json.loads(json.dumps(resumen, default=str)))
            for linea in _lineas_resumen(resumen):
                LOG.info("%s", linea)
        return 0
    except Exception as exc:   # Postgres caído
        LOG.error("Walcu: %s", cx.limpiar(type(exc).__name__, secretos))
        return 1


def _lineas_resumen(resumen: dict) -> list[str]:
    lineas = [f"Walcu ({resumen['modo']}): {resumen['peticiones']} peticiones; paginación {resumen['paginacion']}."]
    for r, c in resumen["recursos"].items():
        lineas.append(f"  {r}: {c.get('elementos', 0)} elementos, {c.get('nuevas', 0)} versiones nuevas, "
                      f"{c.get('iguales', 0)} iguales, {c.get('estados', 0)} estados en la historia, "
                      f"{c.get('filtro_descartados', 0)} campos descartados, "
                      f"{c.get('filtro_vaciados_por_parecer_personales', 0)} textos vaciados por parecer personales.")
    crm = resumen.get("crm") or {}
    lineas.append(f"  CRM: {crm.get('estado', '—')}")
    return lineas


# --------------------------------------------------------------------------- probar (sin guardar nada)

def _tipo(v) -> str:
    if v is None:
        return "nulo"
    if isinstance(v, bool):
        return "booleano"
    if isinstance(v, (int, float)):
        return "número"
    if isinstance(v, str):
        return "fecha" if instante(v) and re.match(r"\d{4}-\d\d-\d\d", v) else "texto"
    return "lista" if isinstance(v, list) else "objeto"


ID_HEX = re.compile(r"^[0-9a-fA-F]{24}$")


def _nombre(k: str) -> str:
    """El nombre de un campo tal cual, salvo que parezca un dato o un identificador (claves de un diccionario)."""
    if personal(k):
        return "<clave con aspecto de dato personal>"
    if ID_HEX.match(k) or re.search(r"\d{4,}", k):
        return "<clave>"
    return k


def describir(obj, prefijo: str, tipos: dict[str, Counter], presentes: Counter, profundidad: int = 0) -> None:
    """Rutas, tipos y en cuántos elementos aparece cada campo. Nunca valores."""
    if profundidad > 8:
        return
    if isinstance(obj, Mapping):
        for k, v in obj.items():
            ruta = f"{prefijo}.{_nombre(str(k))}" if prefijo else _nombre(str(k))
            tipos.setdefault(ruta, Counter())[_tipo(v)] += 1
            presentes[ruta] += 1
            describir(v, ruta, tipos, presentes, profundidad + 1)
    elif isinstance(obj, list):
        for e in obj:
            describir(e, prefijo + "[]", tipos, presentes, profundidad + 1)


def probar(entorno: Mapping[str, str], cfg: Config | None = None, escribir: Callable[[str], None] = print,
           dormir: Callable = time.sleep, ahora: datetime | None = None) -> int:
    """Una página de cada recurso, de los últimos 7 días: nombres de campo, tipos y recuentos. Nada se guarda."""
    cfg = cfg or cargar()
    clave, dealer, _ = credenciales(entorno)
    cliente = Cliente(cfg, clave, dealer, dormir)
    ahora = ahora or cx.ahora()
    desde = ahora - timedelta(days=7)
    p = cfg.paginacion
    q = p[p["tipo"]]
    escribir(f"Paginación configurada: {p['tipo']} (pendiente de confirmar con Walcu); una página de "
             f"{p['tamano']} elementos de cada recurso, actualizados en los últimos 7 días.")
    for nombre, r in cfg.recursos.items():
        parametros = {cfg.filtro_desde: iso(desde), cfg.filtro_hasta: iso(ahora), q["parametro_tamano"]: p["tamano"]}
        if p["tipo"] == "pagina":
            parametros[q["parametro"]] = q.get("primera", 1)
        datos = cliente.get(r.ruta, parametros)
        cp_ruta = r.codigo_postal
        escribir(f"\n== {nombre} ({r.ruta})")
        if isinstance(datos, Mapping):
            escribir("Respuesta: objeto con las claves " + ", ".join(_nombre(str(k)) for k in datos))
            if p["tipo"] == "cursor":
                escribir(f"  ¿trae {q['siguiente']}? {'sí' if valor(datos, q['siguiente']) else 'no'}")
        else:
            escribir(f"Respuesta: {_tipo(datos)}")
        try:
            lista = elementos(datos, p.get("elementos") or "")
        except cx.ErrorDescarga as exc:
            escribir(f"  {exc}")
            continue
        tipos: dict[str, Counter] = {}
        presentes: Counter = Counter()
        antiguos = 0
        for e in lista:
            describir(e, "", tipos, presentes)
            act = instante(e.get("updated_at")) if isinstance(e, Mapping) else None
            antiguos += act is not None and act < desde - timedelta(minutes=1)
        escribir(f"Elementos: {len(lista)}; actualizados antes de lo pedido: {antiguos}"
                 + (" (¡el filtro por updated_at no se aplica!)" if antiguos else ""))
        lista_blanca = set(r.campos)
        for ruta in sorted(tipos):
            estado = ("seudónimo" if ruta in r.seudonimizar else "se guarda") if ruta in lista_blanca else \
                ("solo en memoria (zona, provincia y agregado por código postal)" if ruta == cp_ruta else "se descarta")
            escribir(f"  {ruta}: {', '.join(f'{t} {n}' for t, n in tipos[ruta].most_common())}; "
                     f"en {presentes[ruta]} de {len(lista)}; {estado}")
        faltan = sorted(c for c in r.campos if c not in tipos)
        if faltan:
            escribir("  De la lista blanca no aparecen: " + ", ".join(faltan))
    escribir(f"\nPeticiones: {cliente.peticiones}. No se ha guardado nada.")
    return 0


# --------------------------------------------------------------------------- línea de órdenes

def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("orden", choices=("incremental", "inicial", "probar", "mapeo"))
    parser.add_argument("--desde", type=date.fromisoformat, help="inicial: primer día (AAAA-MM-DD)")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if args.orden == "probar":
        secretos = [os.environ.get(v) for v in ("WALCU_API_KEY", "WALCU_DEALER_ID", "WALCU_SEUDONIMO_CLAVE")]
        try:
            return probar(os.environ)
        except (cx.ErrorDescarga, cx.SinCredenciales, ErrorConfiguracion) as exc:
            print(cx.limpiar(f"No se ha podido probar: {exc}", secretos), file=sys.stderr)
            return 1
    if args.orden == "mapeo":
        from ingesta import walcu_crm
        from ingesta.db import conectar
        with conectar(autocommit=True) as pg:
            for linea in walcu_crm.texto_inventario(walcu_crm.inventario(pg, cargar())):
                print(linea)
        return 0
    if args.orden == "inicial" and not args.desde:
        parser.error("inicial necesita --desde AAAA-MM-DD (make walcu-api-inicial DESDE=2024-01-01)")
    return ejecutar(desde=args.desde if args.orden == "inicial" else None)


if __name__ == "__main__":
    sys.exit(main())
