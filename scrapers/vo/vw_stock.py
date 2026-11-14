#!/usr/bin/env python3
"""Histórico diario de Volkswagen España, ocasión. Python 3.11+, sin dependencias."""
from __future__ import annotations

import argparse
import base64
import csv
import gzip
import hashlib
import io
import json
import logging
import math
import os
import re
import sqlite3
import sys
import time
import threading
import uuid
from collections import Counter
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from email.utils import parsedate_to_datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit, quote
from urllib.request import HTTPRedirectHandler, Request, build_opener
from zoneinfo import ZoneInfo

VERSION = "1.0.0"
SOURCE_BASE = "https://www.volkswagen.es/es/stock-resultados.html"
SOURCE = SOURCE_BASE + "/.app?pageIndex-app=1&t_manuf-app=BQ&t_cartype-app=U&sort-app=DATE_OFFER&sortdirection-app=DESC"
CONFIG_URL = "https://www.volkswagen.es/es.global-config.json"
SORT = "DATE_OFFER"
SORT_DIRECTION = "DESC"
FILTERS = {"t_manuf": "BQ", "t_cartype": "U"}
SCOPE = "volkswagen-es-used-BQ-U-passenger-v1"
USER_AGENT = f"VWStockHistory/{VERSION}"
ROOT = Path(__file__).resolve().parent
DEFAULTS = {
    "data_dir": "data",
    "timezone": "Europe/Madrid",
    "page_size": 12,
    "request_interval_seconds": 1.5,
    "timeout_seconds": 40,
    "http_attempts": 3,
    "max_in_flight": 2,
    "missing_confirmations": 3,
    "max_count_drop_fraction": 0.25,
    "max_missing_fraction": 0.25,
    "min_retail_price_coverage": 0.95,
    "max_source_age_hours": 24,
    "max_pages": 1500,
    "robots_policy": "respect",
    "authorization_reference": "",
}
LOG = logging.getLogger("vw_stock")


class CollectorError(Exception):
    pass


class RobotsDenied(CollectorError):
    pass


def utc_now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(value):
    return hashlib.sha256(value).hexdigest()


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def load_config(path):
    path = Path(path).resolve()
    if not path.is_file():
        raise CollectorError(f"No existe el archivo de configuración: {path}")
    custom = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(custom, dict) or set(custom) - set(DEFAULTS):
        raise CollectorError("Configuración desconocida; consulta config.example.json.")
    cfg = {**DEFAULTS, **custom}
    for key in ("request_interval_seconds", "timeout_seconds", "max_source_age_hours",
                "max_count_drop_fraction", "max_missing_fraction", "min_retail_price_coverage"):
        if type(cfg[key]) not in (int, float) or not math.isfinite(cfg[key]):
            raise CollectorError(f"{key} debe ser un número finito.")
    for key in ("data_dir", "timezone", "authorization_reference", "robots_policy"):
        if not isinstance(cfg[key], str):
            raise CollectorError(f"{key} debe ser texto.")
    if not cfg["data_dir"].strip():
        raise CollectorError("data_dir no puede estar vacío.")
    for key in ("page_size", "http_attempts", "missing_confirmations", "max_pages", "max_in_flight"):
        if type(cfg[key]) is not int or cfg[key] < 1:
            raise CollectorError(f"{key} debe ser entero positivo.")
    if cfg["page_size"] != 12 or cfg["missing_confirmations"] < 2:
        raise CollectorError("La API comprobada sirve páginas de 12; missing_confirmations >= 2.")
    if cfg["max_in_flight"] > 4:
        raise CollectorError("max_in_flight no puede superar 4.")
    for key in ("max_count_drop_fraction", "max_missing_fraction"):
        if not 0 <= cfg[key] < 1:
            raise CollectorError(f"{key} debe estar entre 0 y 1, sin incluir 1.")
    if not 0 < cfg["min_retail_price_coverage"] <= 1:
        raise CollectorError("min_retail_price_coverage debe estar en (0, 1].")
    if cfg["request_interval_seconds"] < 1 or cfg["timeout_seconds"] <= 0 or cfg["max_source_age_hours"] <= 0:
        raise CollectorError("Intervalo mínimo 1 s; timeout y antigüedad deben ser positivos.")
    if cfg["robots_policy"] not in ("respect", "authorized"):
        raise CollectorError("robots_policy: respect o authorized.")
    if cfg["robots_policy"] == "authorized" and not cfg["authorization_reference"].strip():
        raise CollectorError("Falta referencia a la autorización del titular del servicio.")
    ZoneInfo(cfg["timezone"])
    cfg["data_dir"] = str((path.parent / cfg["data_dir"]).resolve())
    return cfg


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise CollectorError("Redirección inesperada: revisar el adaptador; no se reenviaron cabeceras.")


class HttpClient:
    def __init__(self, cfg):
        self.cfg = cfg
        self.last_request = 0.0
        self.not_before = 0.0
        self.request_lock = threading.Lock()
        self.local = threading.local()

    @property
    def opener(self):
        # urllib handlers are never shared across worker threads.
        if not hasattr(self.local, "opener"):
            self.local.opener = build_opener(NoRedirect())
        return self.local.opener

    def get(self, url, headers=None):
        for attempt in range(self.cfg["http_attempts"]):
            with self.request_lock:
                delay = max(self.last_request + self.cfg["request_interval_seconds"], self.not_before) - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                self.last_request = time.monotonic()
            req = Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,text/html",
                                        "Accept-Encoding": "gzip", **(headers or {})})
            try:
                with self.opener.open(req, timeout=self.cfg["timeout_seconds"]) as response:
                    body = response.read(20 * 1024 * 1024 + 1)
                    if len(body) > 20 * 1024 * 1024:
                        raise CollectorError("Respuesta excesiva (>20 MiB); no se procesa.")
                    encoding = response.headers.get("Content-Encoding", "").lower()
                    if encoding == "gzip":
                        with gzip.GzipFile(fileobj=io.BytesIO(body)) as packed:
                            body = packed.read(20 * 1024 * 1024 + 1)
                        if len(body) > 20 * 1024 * 1024:
                            raise CollectorError("Respuesta descomprimida excesiva (>20 MiB).")
                    elif encoding and encoding != "identity":
                        raise CollectorError("Codificación HTTP inesperada.")
                    return body, response.headers
            except HTTPError as exc:
                if exc.code not in (408, 429, 500, 502, 503, 504) or attempt + 1 == self.cfg["http_attempts"]:
                    raise
                retry = exc.headers.get("Retry-After", "")
                if retry:
                    from email.utils import parsedate_to_datetime
                    try:
                        wait = float(retry)
                    except ValueError:
                        try:
                            wait = (parsedate_to_datetime(retry) - datetime.now(timezone.utc)).total_seconds()
                        except (ValueError, TypeError):
                            raise CollectorError("Retry-After no interpretable; se detiene.") from exc
                    if not math.isfinite(wait) or wait > 300:
                        raise CollectorError("Servidor solicita pausa >5 min; reintentar en otra ejecución.") from exc
                else:
                    wait = 2 ** (attempt + 1)
                with self.request_lock:
                    self.not_before = max(self.not_before, time.monotonic() + max(1, wait))
            except (URLError, TimeoutError, ConnectionError):
                if attempt + 1 == self.cfg["http_attempts"]:
                    raise
                time.sleep(2 ** (attempt + 1))
        raise CollectorError("Sin respuesta HTTP.")


def robot_rules(raw, url, user_agent):
    """Merge matching groups and apply longest Allow/Disallow (* and $ supported)."""
    groups, agents, rules, delays, started = [], [], [], [], False
    for line in raw.splitlines() + ["User-agent: __end__"]:
        line = line.split("#", 1)[0].strip()
        if ":" not in line:
            continue
        key, value = [s.strip() for s in line.split(":", 1)]
        key = key.lower()
        if key == "user-agent":
            if started:
                groups.append((agents, rules, delays))
                agents, rules, delays, started = [], [], [], False
            agents.append(value.lower())
        elif agents:
            started = True
            if key in ("allow", "disallow") and value:
                rules.append((key == "allow", value))
            elif key == "crawl-delay":
                try:
                    n = float(value)
                    if math.isfinite(n) and n > 0:
                        delays.append(n)
                except ValueError:
                    pass
    product = user_agent.split("/", 1)[0].lower()
    specific = [g for g in groups if any(a != "*" and a in product for a in g[0])]
    selected = specific or [g for g in groups if "*" in g[0]]
    target = urlsplit(url).path or "/"
    if urlsplit(url).query:
        target += "?" + urlsplit(url).query
    matches = []
    for _, rules, _ in selected:
        for allow, pattern in rules:
            end = pattern.endswith("$")
            core = pattern[:-1] if end else pattern
            regex = "^" + ".*".join(re.escape(part) for part in core.split("*")) + ("$" if end else "")
            if re.search(regex, target):
                matches.append((len(core.replace("*", "").encode()), allow))
    return (max(matches)[1] if matches else True), max([d for g in selected for d in g[2]] or [0])


def check_robots(client, url, cfg):
    parsed = urlsplit(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        raw, _ = client.get(robots_url)
        allowed, crawl_delay = robot_rules(raw.decode("utf-8", errors="replace"), url, USER_AGENT)
        if crawl_delay:
            client.cfg["request_interval_seconds"] = max(cfg["request_interval_seconds"], crawl_delay)
        evidence = {"url": robots_url, "sha256": sha(raw), "allowed": allowed, "checked_at": utc_now()}
    except HTTPError as exc:
        # RFC 9309 2.3.1.3: 4xx on robots.txt means unavailable. This exception
        # applies ONLY to robots.txt, never to 401/403 on the data endpoint.
        if not (400 <= exc.code < 500) or exc.code == 429:
            raise CollectorError(f"No se pudo verificar robots.txt: HTTP {exc.code}.") from exc
        allowed = True
        evidence = {"url": robots_url, "allowed": True, "http_status": exc.code,
                    "robots_status": "unavailable", "policy_basis": "RFC9309-2.3.1.3", "checked_at": utc_now()}
    if not allowed and cfg["robots_policy"] != "authorized":
        raise RobotsDenied(f"{robots_url} no permite este rastreo. Sin descarga de stock; consulta README.md.")
    evidence["policy"] = cfg["robots_policy"]
    evidence["authorization_reference"] = cfg["authorization_reference"]
    return evidence




def cents(value):
    if value is None:
        return None
    if isinstance(value, bool):
        raise CollectorError("Importe booleano inválido.")
    try:
        n = Decimal(str(value))
        if not n.is_finite() or n < 0 or n != n.quantize(Decimal("0.01")):
            raise ValueError()
        return int(n * 100)
    except (InvalidOperation, ValueError):
        raise CollectorError(f"Importe numérico inválido: {value!r}") from None


def find_feature_config(html):
    match = re.search(r'<script\b(?=[^>]*\bid=["\x27]spaModel["\x27])[^>]*>(.*?)</script>', html, re.S)
    if not match:
        raise CollectorError("No se encuentra spaModel en la página oficial.")
    root = json.loads(match.group(1))
    found = []
    def walk(value):
        if isinstance(value, dict):
            if value.get("name") == "gsl_pages" and value.get("baseUrl"):
                found.append(value)
            for child in value.values():
                walk(child)
        elif isinstance(value, list):
            for child in value:
                walk(child)
    walk(root)
    unique = {json_text(v): v for v in found}
    if len(unique) != 1:
        raise CollectorError("Configuración GSL ausente o ambigua; revisar adaptador.")
    feature = next(iter(unique.values()))
    if not re.fullmatch(r"https://v[0-9]+-[0-9]+-[0-9]+\.gsl\.feature-app\.io", feature["baseUrl"]):
        raise CollectorError("El buscador cambió de dominio; revisar antes de continuar.")
    if feature.get("url") != "/apps/pages/pages.umd.js" or not feature.get("featureAppApiKey"):
        raise CollectorError("Cambió la configuración pública GSL.")
    return feature


def discover(client, cfg, evidence):
    evidence["robots"] = [check_robots(client, SOURCE, cfg), check_robots(client, CONFIG_URL, cfg)]
    html, _ = client.get(SOURCE)
    feature = find_feature_config(html.decode("utf-8"))
    config_raw, _ = client.get(CONFIG_URL)
    config = json.loads(config_raw)["spaAsyncConfig"]
    endpoint = config["serviceConfigEndpoint"]
    values = endpoint.get("endpoint") or {}
    if any(values.get(k) != v for k, v in {"country": "es", "language": "es", "type": "publish",
                                         "content": "onehub_pkw", "envName": "prod"}.items()):
        raise CollectorError("La configuración no corresponde a Volkswagen España en producción.")
    if not isinstance(endpoint.get("signature"), str) or not endpoint["signature"]:
        raise CollectorError("Falta la configuración pública firmada del buscador.")
    api_url = feature["baseUrl"] + "/bff/car/search"
    evidence["robots"].append(check_robots(client, api_url, cfg))
    evidence.update({"api": api_url, "app_version": feature.get("featureAppVersion"),
                     "html_sha256": sha(html), "config_sha256": sha(config_raw),
                     "filters": FILTERS, "sort": SORT, "sort_direction": SORT_DIRECTION})
    # Keep the provider's endpoint object order: use the browser's JSON.stringify form.
    params = {**FILTERS, "sort": SORT, "sortdirection": SORT_DIRECTION,
              "country": "ES", "language": "es", "market": "passenger",
              "endpoint": json.dumps(endpoint, ensure_ascii=False, separators=(",", ":")),
              "oneapiKey": feature["featureAppApiKey"]}
    return api_url, params


def check_response_date(headers, cfg):
    h = {k.lower(): v for k, v in headers.items()}
    try:
        stamp = parsedate_to_datetime(h["date"])
        if stamp.tzinfo is None:
            raise ValueError()
        age = (datetime.now(timezone.utc) - stamp).total_seconds()
        cache_age = float(h.get("age", "0"))
    except (KeyError, ValueError, TypeError):
        raise CollectorError("Falta Date HTTP válida; no se confirma la captura.") from None
    if not math.isfinite(cache_age) or cache_age < 0 or age < -300 or max(age, cache_age) > cfg["max_source_age_hours"] * 3600:
        raise CollectorError("Respuesta HTTP antigua o reloj desajustado.")




def normalize(car):
    if not isinstance(car, dict):
        raise CollectorError("Vehículo no es un objeto JSON.")
    ident, key = car.get("carid"), car.get("key")
    if not isinstance(ident, str) or not ident.startswith("ESP") or not isinstance(key, str) or not key:
        raise CollectorError("Falta carid/key estable del mercado español.")
    try:
        decoded = base64.b64decode(key + "=" * (-len(key) % 4), validate=True).decode("utf-8")
    except (ValueError, UnicodeError):
        raise CollectorError("key del anuncio no es interpretable.") from None
    if decoded != ident:
        raise CollectorError("carid y key no identifican el mismo anuncio.")
    dealer = car.get("dealer") or {}
    if (car.get("cartype") or {}).get("code") != "U" or (car.get("mbvManuf") or {}).get("code") != "V" or dealer.get("country") != "ESP":
        raise CollectorError("Anuncio fuera de Volkswagen de ocasión en España.")
    def label(obj):
        if obj is None:
            return None
        if not isinstance(obj, dict):
            raise CollectorError("Etiqueta estructurada inesperada.")
        value = obj.get("value")
        return value if value not in (None, "") else None
    def numeric(obj):
        value = (obj or {}).get("raw_value")
        if value is not None and (isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0):
            raise CollectorError("Valor numérico del vehículo inválido.")
        return value
    prices = car.get("parsedPrice") or {}
    if prices and prices.get("unit") != "EUR":
        raise CollectorError("Precio en moneda inesperada.")
    model = label(car.get("model"))
    if not model:
        raise CollectorError("Falta el modelo del vehículo.")
    first_registration = car.get("initialreg")
    if first_registration:
        try:
            first_registration = datetime.fromisoformat(first_registration.replace("Z", "+00:00")).date().isoformat()
        except (ValueError, TypeError):
            raise CollectorError("Fecha de matriculación inválida.") from None
    vin_source = car.get("vin")
    vin = vin_source.strip().upper() if isinstance(vin_source, str) else ""
    vin = vin if re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin) else None
    motor = car.get("motor") or {}
    colors = car.get("color") or {}
    for flag in ("isVat", "isDwa", "isReserved"):
        if car.get(flag) is not None and type(car[flag]) is not bool:
            raise CollectorError(f"Estado no booleano: {flag}.")
    # shouldFetchReserved is a UI request switch, NOT the reservation state.
    finance = (car.get("hypermediaFinancing") or {}).get("default") or {}
    terms = {k: finance[k] for k in ("TotalAmount", "Bonusfinancing", "TotalAmountFinanced", "DownPayment",
                                    "FinalRate", "Rate", "TAE", "InterestRateNominal", "Duration", "Kilometers",
                                    "NetCreditAmount", "MinFinancingAmount", "openingFee", "totalPriceInInstallments",
                                    "totalCreditCost") if k in finance}
    business = {
        "brand": "Volkswagen", "model": model, "model_code": (car.get("model", {}).get("mbv") or {}).get("code"),
        "carline": car.get("basemodelKtText") or car.get("modelKtText"),
        "version": label(car.get("localCarTitle")) or label(car.get("subtitle")) or car.get("title"),
        "model_year": label(car.get("modelyear")), "vin": vin,
        "price_retail_cents": cents(prices.get("value")),
        "price_basis_label": (prices.get("suffix") or {}).get("label"),
        "price_new_reference_cents": cents((prices.get("list") or {}).get("value")),
        "price_reference_label": (prices.get("list") or {}).get("label"),
        "currency": "EUR", "financing_terms": terms,
        "mileage_km": numeric(car.get("mileage")), "first_registration_date": first_registration,
        "fuel": label(motor.get("fuel")), "power_kw": numeric(motor.get("powerKw")),
        "power_cv": numeric(motor.get("powerPs")), "engine_capacity_cc": numeric(motor.get("capacity")),
        "exterior_color": label(colors.get("out")), "interior_color": label(colors.get("in")),
        "pollution_badge": label(car.get("emis")), "reservation": car.get("isReserved"),
        "vat_reclaimable": car.get("isVat"), "approved": car.get("isDwa"),
        "business_model": car.get("businessModel"),
        "dealer_id": dealer.get("key"), "dealer_name": label(dealer.get("name")),
        "dealer_city": label(dealer.get("city")), "dealer_postcode": label(dealer.get("zip")),
        "campaign_codes": sorted({c["key"] for c in car.get("campaigns", []) if c.get("key")}),
    }
    return {"vehicle_id": ident, "business": business, "business_hash": sha(json_text(business).encode()),
            "encoded_id": key, "public_url": SOURCE_BASE + "/.app/search/car/" + quote(key, safe=""),
            "source_offer_at": car.get("dateoffer"), "source_production_at": car.get("dateproduction"),
            "image_urls": [p["href"] for p in car.get("images", []) if p.get("href")],
            "source_vin": vin_source, "reservation_check_requested_by_ui": car.get("shouldFetchReserved"),
            "source_price_disclaimer": (prices.get("suffix") or {}).get("disclaimer"),
            "raw_record_hash": sha(json_text(car).encode())}


def validate_page(page, offset, cfg):
    if not isinstance(page, dict) or not isinstance(page.get("meta"), dict):
        raise CollectorError("Esquema cambiado: falta meta.")
    meta = page["meta"]
    total, rows = meta.get("resultNumber"), page.get("cars")
    if type(total) is not int or total < 0 or not isinstance(rows, list):
        raise CollectorError("Esquema cambiado: cars/resultNumber inválidos.")
    if any(type(meta.get(key)) is not int for key in ("page", "pageItems", "pageMax")):
        raise CollectorError("Paginación sin valores enteros válidos.")
    if offset % cfg["page_size"] or meta.get("page") != offset // cfg["page_size"] + 1 or meta.get("pageItems") != cfg["page_size"]:
        raise CollectorError("La API no confirma la página y el tamaño solicitado.")
    if meta.get("pageMax") != math.ceil(total / cfg["page_size"]):
        raise CollectorError("Número de páginas incoherente con el total.")
    if meta.get("sort") != SORT or meta.get("sortDirection") != SORT_DIRECTION:
        raise CollectorError("Orden de paginación inesperado.")
    if len(rows) > cfg["page_size"]:
        raise CollectorError("Página demasiado larga.")
    return rows, total


def collect_pages(fetch, cfg):
    """Bounded prefetch, ordered validation, and a shared HTTP start-rate limit."""
    if cfg["max_in_flight"] == 1:
        return collect_ordered_pages(fetch, cfg)
    futures = {}
    with ThreadPoolExecutor(max_workers=cfg["max_in_flight"]) as pool:
        first = fetch(0, "page")
        _, total = validate_page(first[0], 0, cfg)
        next_offset = cfg["page_size"]
        def refill():
            nonlocal next_offset
            limit = min(total, cfg["max_pages"] * cfg["page_size"])
            while len(futures) < cfg["max_in_flight"] and next_offset < limit:
                offset = next_offset
                futures[offset] = pool.submit(fetch, offset, "page")
                next_offset += cfg["page_size"]
        def ordered(offset, kind):
            if kind == "recheck":
                return fetch(offset, kind)
            if offset == 0:
                refill()
                return first
            result = futures.pop(offset).result()
            refill()
            return result
        try:
            return collect_ordered_pages(ordered, cfg)
        finally:
            for future in futures.values():
                future.cancel()


def collect_ordered_pages(fetch, cfg):
    """A complete pass plus a first-page membership recheck; no false completion."""
    vehicles, first_ids, expected, offset, pages = {}, None, None, 0, 0
    while expected is None or offset < expected:
        if pages >= cfg["max_pages"]:
            raise CollectorError("Se alcanzó max_pages sin completar el stock.")
        page, observed_at = fetch(offset, "page")
        rows, total = validate_page(page, offset, cfg)
        if expected is None:
            expected = total
        if total != expected:
            raise CollectorError("El total cambió durante la captura; no se publica un snapshot parcial.")
        if not rows or len(rows) != min(cfg["page_size"], expected - offset):
            raise CollectorError("Página vacía, truncada o stock vacío: requiere revisión.")
        page_ids = []
        for row in rows:
            value = normalize(row)
            vid = value["vehicle_id"]
            if vid in vehicles:
                raise CollectorError("ID repetido entre páginas: posible deriva de paginación.")
            value["observed_at"] = observed_at
            vehicles[vid] = value
            page_ids.append(vid)
        if first_ids is None:
            first_ids = page_ids
        offset += len(rows)
        pages += 1
        LOG.info("Capturados %s/%s", offset, expected)
    page, _ = fetch(0, "recheck")
    rows, total = validate_page(page, 0, cfg)
    if total != expected or [normalize(v)["vehicle_id"] for v in rows] != first_ids:
        raise CollectorError("La primera página cambió al terminar; se descarta la captura.")
    if len(vehicles) != expected:
        raise CollectorError("El número de IDs únicos no coincide con resultNumber.")
    priced = sum(v["business"]["price_retail_cents"] is not None and v["business"]["price_retail_cents"] > 0 for v in vehicles.values())
    if priced / len(vehicles) < cfg["min_retail_price_coverage"]:
        raise CollectorError("Cobertura de precios demasiado baja; revisar el adaptador.")
    return list(vehicles.values()), expected, pages


SCHEMA = """
CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(
 run_id TEXT PRIMARY KEY, snapshot_day TEXT NOT NULL, started_at TEXT NOT NULL,
 finished_at TEXT, status TEXT NOT NULL, expected_count INTEGER, observed_count INTEGER,
 error TEXT, raw_dir TEXT NOT NULL, config_json TEXT NOT NULL);
CREATE UNIQUE INDEX IF NOT EXISTS one_success_per_day ON runs(snapshot_day) WHERE status='success';
CREATE TABLE IF NOT EXISTS vehicles(
 vehicle_id TEXT PRIMARY KEY, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
 first_missing_at TEXT, missing_successful_days INTEGER NOT NULL DEFAULT 0,
 status TEXT NOT NULL, last_business_json TEXT NOT NULL, last_business_hash TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS observations(
 run_id TEXT NOT NULL REFERENCES runs(run_id), vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id),
 observed_at TEXT NOT NULL, data_json TEXT NOT NULL, PRIMARY KEY(run_id,vehicle_id));
CREATE INDEX IF NOT EXISTS observations_vehicle ON observations(vehicle_id,observed_at);
CREATE TABLE IF NOT EXISTS events(
 event_id INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(run_id),
 vehicle_id TEXT NOT NULL REFERENCES vehicles(vehicle_id), detected_at TEXT NOT NULL,
 kind TEXT NOT NULL, field TEXT, old_json TEXT, new_json TEXT);
CREATE INDEX IF NOT EXISTS events_vehicle ON events(vehicle_id,detected_at);
"""


def connect_db(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    db.execute("PRAGMA journal_mode=WAL")
    db.executescript(SCHEMA)
    with db:
        db.execute("INSERT OR IGNORE INTO metadata VALUES('scope',?)", (SCOPE,))
        db.execute("INSERT OR IGNORE INTO metadata VALUES('schema_version','1')")
    if db.execute("SELECT value FROM metadata WHERE key='scope'").fetchone()[0] != SCOPE:
        db.close()
        raise CollectorError("Base de datos de otro ámbito; no se mezcla con este histórico.")
    if db.execute("SELECT value FROM metadata WHERE key='schema_version'").fetchone()[0] != "1":
        db.close()
        raise CollectorError("Versión de base de datos no compatible.")
    return db


@contextmanager
def process_lock(path):
    # OS lock releases automatically after a crash; the lock file may remain.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    f = path.open("a+b")
    locked = False
    try:
        if os.name == "nt":
            import msvcrt
            if f.tell() == 0:
                f.write(b"0")
                f.flush()
            f.seek(0)
            try:
                msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                raise CollectorError("Otra captura está en ejecución.") from None
        else:
            import fcntl
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                raise CollectorError("Otra captura está en ejecución.") from None
        locked = True
        yield
    finally:
        if locked:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f, fcntl.LOCK_UN)
        f.close()


def start_run(db, day, raw_dir, cfg):
    run_id = uuid.uuid4().hex
    with db:
        db.execute("INSERT INTO runs(run_id,snapshot_day,started_at,status,raw_dir,config_json) VALUES(?,?,?,'running',?,?)",
                   (run_id, day, utc_now(), str(raw_dir), json_text(cfg)))
    return run_id


def commit_snapshot(db, run_id, records, expected, cfg):
    if len(records) != expected or expected <= 0:
        raise CollectorError("Solo se aceptan snapshots completos y no vacíos.")
    by_id = {v["vehicle_id"]: v for v in records}
    if len(by_id) != expected:
        raise CollectorError("IDs duplicados; no se modifica el histórico.")
    run = db.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
    if not run or run["status"] != "running":
        raise CollectorError("Ejecución no válida para confirmar.")
    previous_run = db.execute("SELECT * FROM runs WHERE status='success' ORDER BY snapshot_day DESC LIMIT 1").fetchone()
    if previous_run and run["snapshot_day"] <= previous_run["snapshot_day"]:
        raise CollectorError("Fecha ya confirmada o anterior al último snapshot; no se reescribe.")
    if previous_run:
        previous_ids = {v[0] for v in db.execute("SELECT vehicle_id FROM observations WHERE run_id=?", (previous_run["run_id"],))}
        drop = 1 - expected / previous_run["observed_count"]
        missing_fraction = len(previous_ids - set(by_id)) / len(previous_ids)
        if drop > cfg["max_count_drop_fraction"] or missing_fraction > cfg["max_missing_fraction"]:
            raise CollectorError(f"Caída/cambio anómalo de stock (caída={drop:.1%}, ausentes={missing_fraction:.1%}); revisión necesaria.")
    existing = {r["vehicle_id"]: r for r in db.execute("SELECT * FROM vehicles")}
    detected = utc_now()
    counts = Counter()
    def event(vid, kind, field=None, old=None, new=None):
        db.execute("INSERT INTO events(run_id,vehicle_id,detected_at,kind,field,old_json,new_json) VALUES(?,?,?,?,?,?,?)",
                   (run_id, vid, detected, kind, field, json_text(old), json_text(new)))
        counts[kind] += 1
    with db:
        for vid, record in by_id.items():
            business = record["business"]
            old = existing.get(vid)
            if old is None:
                db.execute("INSERT INTO vehicles VALUES(?,?,?,NULL,0,'present',?,?)",
                           (vid, record["observed_at"], record["observed_at"], json_text(business), record["business_hash"]))
                event(vid, "new")
            else:
                if old["status"] != "present":
                    event(vid, "returned", "status", old["status"], "present")
                prior = json.loads(old["last_business_json"])
                if old["last_business_hash"] != record["business_hash"]:
                    for field in sorted(set(prior) | set(business)):
                        if prior.get(field) != business.get(field):
                            event(vid, "changed", field, prior.get(field), business.get(field))
                db.execute("UPDATE vehicles SET last_seen_at=?,first_missing_at=NULL,missing_successful_days=0,status='present',last_business_json=?,last_business_hash=? WHERE vehicle_id=?",
                           (record["observed_at"], json_text(business), record["business_hash"], vid))
            db.execute("INSERT INTO observations VALUES(?,?,?,?)", (run_id, vid, record["observed_at"], json_text(record)))
        for vid in set(existing) - set(by_id):
            old = existing[vid]
            if old["status"] == "removed":
                continue
            misses = old["missing_successful_days"] + 1
            status = "removed" if misses >= cfg["missing_confirmations"] else "missing"
            if old["status"] == "present":
                event(vid, "not_seen", "status", "present", "missing")
            if status == "removed":
                event(vid, "removed", "status", old["status"], "removed")
            db.execute("UPDATE vehicles SET first_missing_at=COALESCE(first_missing_at,?),missing_successful_days=?,status=? WHERE vehicle_id=?",
                       (run["started_at"], misses, status, vid))
        db.execute("UPDATE runs SET status='success',finished_at=?,expected_count=?,observed_count=? WHERE run_id=?",
                   (utc_now(), expected, len(records), run_id))
    return dict(counts)


def csv_bytes(rows, fields):
    out = io.StringIO(newline="")
    writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for row in rows:
        clean = {}
        for key, value in row.items():
            if isinstance(value, (dict, list)):
                value = json_text(value)
            # Protect spreadsheet users from formula interpretation of scraped text.
            if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                value = "'" + value
            clean[key] = value
        writer.writerow(clean)
    return out.getvalue().encode("utf-8-sig")


def write_csv(path, rows, fields):
    """Stream large historical exports, then replace atomically."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with tmp.open("w", encoding="utf-8-sig", newline="") as out:
            writer = csv.DictWriter(out, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            for row in rows:
                clean = {}
                for key, value in row.items():
                    if isinstance(value, (dict, list)):
                        value = json_text(value)
                    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
                        value = "'" + value
                    clean[key] = value
                writer.writerow(clean)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def export_data(db, data_dir):
    folder = Path(data_dir) / "exports"
    rows = (dict(r) for r in db.execute("SELECT * FROM runs ORDER BY started_at"))
    write_csv(folder / "runs.csv", rows, [d[0] for d in db.execute("SELECT * FROM runs LIMIT 0").description])
    rows = (dict(r) for r in db.execute("SELECT * FROM events ORDER BY event_id"))
    write_csv(folder / "changes.csv", rows, [d[0] for d in db.execute("SELECT * FROM events LIMIT 0").description])
    latest = db.execute("SELECT run_id FROM runs WHERE status='success' ORDER BY snapshot_day DESC LIMIT 1").fetchone()
    def flatten(row):
        d = json.loads(row["data_json"])
        return {"snapshot_day": row["snapshot_day"], "run_id": row["run_id"], "vehicle_id": row["vehicle_id"],
                "observed_at": row["observed_at"], **d["business"], "public_url": d["public_url"], "source_offer_at": d.get("source_offer_at"), "source_production_at": d.get("source_production_at")}
    fields = ["snapshot_day", "run_id", "vehicle_id", "observed_at"]
    sample = db.execute("SELECT data_json FROM observations LIMIT 1").fetchone()
    if sample:
        fields += list(json.loads(sample[0])["business"])
    fields += ["public_url", "source_offer_at", "source_production_at"]
    query = "SELECT o.*,r.snapshot_day FROM observations o JOIN runs r USING(run_id)"
    all_rows = db.execute(query + " ORDER BY r.snapshot_day,o.vehicle_id")
    write_csv(folder / "history.csv", (flatten(r) for r in all_rows), fields)
    latest_rows = db.execute(query + " WHERE o.run_id=? ORDER BY o.vehicle_id", (latest[0],)) if latest else []
    write_csv(folder / "stock_latest.csv", (flatten(r) for r in latest_rows), fields)
    rows = (dict(r) for r in db.execute("SELECT vehicle_id,first_seen_at,last_seen_at,first_missing_at,missing_successful_days,status FROM vehicles ORDER BY vehicle_id"))
    write_csv(folder / "vehicles.csv", rows, ["vehicle_id", "first_seen_at", "last_seen_at", "first_missing_at", "missing_successful_days", "status"])


def live_run(cfg):
    data = Path(cfg["data_dir"])
    with process_lock(data / "collector.lock"):
        db = connect_db(data / "vw_stock.sqlite3")
        try:
            with db:
                # With the OS lock held, no earlier 'running' entry can still be collecting.
                db.execute("UPDATE runs SET status='interrupted',finished_at=?,error='Proceso anterior interrumpido' WHERE status='running'", (utc_now(),))
            day = datetime.now(ZoneInfo(cfg["timezone"])).date().isoformat()
            if db.execute("SELECT 1 FROM runs WHERE snapshot_day=? AND status='success'", (day,)).fetchone():
                export_data(db, data)
                LOG.info("%s ya tiene snapshot completo. No se repite ni se reescribe.", day)
                return 0
            raw_dir = data / "raw" / (day + "_" + uuid.uuid4().hex[:12])
            raw_dir.mkdir(parents=True)
            run_id = start_run(db, day, raw_dir, cfg)
            evidence = {"run_id": run_id, "version": VERSION, "source": SOURCE, "pages": []}
            try:
                client = HttpClient(cfg)
                api_url, params = discover(client, cfg, evidence)
                def fetch(offset, kind):
                    page_number = offset // cfg["page_size"] + 1
                    url = api_url + "?" + urlencode({**params, "pageitems": cfg["page_size"], "page": page_number})
                    raw, headers = client.get(url, {"Origin": "https://www.volkswagen.es", "Referer": SOURCE})
                    observed = utc_now()
                    filename = f"{kind}_{page_number:05d}.json.gz"
                    atomic_write(raw_dir / filename, gzip.compress(raw, mtime=0))
                    http_date = next((v for k, v in headers.items() if k.lower() == "date"), None)
                    evidence["pages"].append({"file": filename, "api_page": page_number, "observed_at": observed,
                                              "sha256_uncompressed": sha(raw), "http_date": http_date})
                    # Do not persist public API keys or signed endpoint tokens in URLs/logs.
                    check_response_date(headers, cfg)
                    return json.loads(raw), observed
                records, total, pages = collect_pages(fetch, cfg)
                evidence.update({"capture_status": "complete", "total": total, "pages_count": pages})
                atomic_write(raw_dir / "manifest.json", json_text(evidence).encode())
                counts = commit_snapshot(db, run_id, records, total, cfg)
            except Exception as exc:
                # No snapshot transaction has succeeded here; preserve attempted run and raw evidence.
                evidence.update({"capture_status": "failed", "error": str(exc)})
                atomic_write(raw_dir / "manifest.json", json_text(evidence).encode())
                with db:
                    db.execute("UPDATE runs SET status='failed',finished_at=?,error=? WHERE run_id=? AND status='running'", (utc_now(), str(exc), run_id))
                raise
            # Exports are recoverable from SQLite; an export failure must not relabel a committed snapshot.
            export_data(db, data)
            LOG.info("Snapshot confirmado: %s vehículos; eventos=%s", total, counts)
            return 0
        finally:
            db.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "check", "export"))
    parser.add_argument("--config", type=Path, default=ROOT / "config.json")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = load_config(args.config)
        if args.command == "check":
            result = {"version": VERSION, "source": SOURCE}
            discover(HttpClient(cfg), cfg, result)
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0
        if args.command == "export":
            data = Path(cfg["data_dir"])
            if not (data / "vw_stock.sqlite3").exists():
                raise CollectorError("No existe histórico todavía.")
            with process_lock(data / "collector.lock"):
                db = connect_db(data / "vw_stock.sqlite3")
                try:
                    export_data(db, data)
                finally:
                    db.close()
            return 0
        return live_run(cfg)
    except RobotsDenied as exc:
        LOG.error("%s", exc)
        return 3
    except (CollectorError, HTTPError, URLError, TimeoutError, ConnectionError, OSError,
            ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        LOG.error("%s: %s", type(exc).__name__, exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
