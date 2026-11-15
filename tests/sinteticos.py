"""Bases SQLite sintéticas, escritas con el código real de los scrapers.

Se importan scrapers/vo/vw_stock.py y scrapers/vn/vw_vn_stock.py (sin modificarlos) y se usan sus
funciones connect_db, start_run y commit_snapshot. Así el esquema, los eventos y las ausencias son
exactamente los que producen en el servidor. Ningún dato real: anuncios, bastidores y precios
inventados.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent


def _modulo(nombre: str, ruta: Path):
    spec = importlib.util.spec_from_file_location(nombre, ruta)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


VO = _modulo("cdf_test_vw_stock", RAIZ / "scrapers" / "vo" / "vw_stock.py")
VN = _modulo("cdf_test_vw_vn_stock", RAIZ / "scrapers" / "vn" / "vw_vn_stock.py")

CONFIG = {
    "vo": (VO, dict(VO.DEFAULTS), "vo/vw_stock.sqlite3"),
    "vn": (VN, {**VN.DEFAULTS, "fuel_codes": ["B", "D"]}, "vn/vw_vn_stock.sqlite3"),
    "vn_otros": (VN, {**VN.DEFAULTS, "fuel_codes": ["E", "H"]}, "vn_otros/vw_vn_stock.sqlite3"),
}


def coche(vid: str, *, fuente: str = "vo", dealer_id: str = "ESP00217", dealer: str = "CONCESIONARIO PRUEBA",
          cp: str | None = "41001", modelo: str = "Golf", precio: int | None = 2_500_000,
          km: float | None = 15000, dia: str = "2026-11-10", ciudad: str = "Sevilla") -> dict:
    vn = fuente != "vo"
    business = {
        "brand": "Volkswagen", "model": modelo, "model_code": "5G1", "carline": modelo,
        "version": f"{modelo} 1.5 TSI Life", "model_year": "2025", "vin": "WVWZZZ" + vid[-11:].rjust(11, "0"),
        "price_retail_cents": precio, "price_basis_label": "al contado PVP Concesionario",
        "price_new_reference_cents": None, "price_reference_label": None, "currency": "EUR",
        "financing_terms": {"Rate": "199"}, "mileage_km": None if vn else km,
        "first_registration_date": None if vn else "2024-03-01",
        "fuel": "Gasolina" if fuente != "vn_otros" else "Eléctrico",
        "power_kw": 110, "power_cv": 150, "engine_capacity_cc": 1498,
        "exterior_color": "Blanco", "interior_color": "Negro", "reservation": None,
        "vat_reclaimable": False, "approved": not vn, "business_model": "retail",
        "dealer_id": dealer_id, "dealer_name": dealer, "dealer_city": ciudad, "dealer_postcode": cp,
        "campaign_codes": [],
    }
    if vn:
        business.update({"vehicle_type": "N", "registration_plate": None, "commission_number": "K" + vid[-6:],
                         "price_conditions_text": "Condiciones de prueba",
                         "fuel_code": "B" if fuente == "vn" else "E",
                         "emission_label_source": "C", "delivery_status_code": "40",
                         "delivery_status_text": "En España y listo para enviar a tu concesionario Volkswagen."})
    else:
        business["pollution_badge"] = "C"
    texto = json.dumps(business, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return {
        "vehicle_id": vid, "business": business, "business_hash": hashlib.sha256(texto.encode()).hexdigest(),
        "encoded_id": "x", "public_url": "https://example.invalid/" + vid,
        "source_offer_at": None, "source_production_at": None,
        "image_urls": ["https://example.invalid/1.jpg", "https://example.invalid/2.jpg"],
        "source_vin": None, "reservation_check_requested_by_ui": False,
        "source_price_disclaimer": None, "raw_record_hash": "0" * 64,
        "observed_at": f"{dia}T03:10:00+00:00",
    }


def capturar(base: Path, fuente: str, dia: str, coches: list[dict], ajustes: dict | None = None) -> str:
    """Una pasada success del scraper `fuente` el día `dia` con estos anuncios. Devuelve run_id.

    `ajustes` cambia la configuración del scraper solo en esta pasada (p. ej. sus límites de caída
    anómala, que con las flotas diminutas de algunas pruebas saltarían).
    """
    mod, cfg, fichero = CONFIG[fuente]
    cfg = {**cfg, **(ajustes or {})}
    ruta = base / fichero
    db = mod.connect_db(ruta) if fuente == "vo" else mod.connect_db(ruta, cfg)
    try:
        for c in coches:
            c["observed_at"] = f"{dia}T03:10:00+00:00"
        run_id = mod.start_run(db, dia, base / "raw" / dia, cfg)
        mod.commit_snapshot(db, run_id, coches, len(coches), cfg)
        return run_id
    finally:
        db.close()


def fallar(base: Path, fuente: str, dia: str, error: str = "El total cambió durante la captura") -> str:
    """Un intento fallido, como los que deja el scraper cuando la paginación se mueve."""
    mod, cfg, fichero = CONFIG[fuente]
    ruta = base / fichero
    db = mod.connect_db(ruta) if fuente == "vo" else mod.connect_db(ruta, cfg)
    try:
        run_id = mod.start_run(db, dia, base / "raw" / dia, cfg)
        with db:
            db.execute("UPDATE runs SET status='failed', error=? WHERE run_id=?", (error, run_id))
        return run_id
    finally:
        db.close()


def flota(prefijo: str, n: int, fuente: str = "vo", **kw) -> list[dict]:
    return [coche(f"ESP{prefijo}{i:06d}", fuente=fuente, **kw) for i in range(n)]
