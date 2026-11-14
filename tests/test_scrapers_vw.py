import hashlib
import re
from datetime import date

import psycopg
import pytest

from ingesta import scrapers_vw as sv
from sinteticos import RAIZ, VN, VO, capturar, coche, fallar, flota

D1, D2, D3, D4, D5, D6 = (f"2026-11-{d:02d}" for d in range(10, 16))


def _tres_fuentes(datos, dia=D1):
    vo = flota("VO", 8) + [coche("ESPPROPIO0001", dealer_id="ESP00710", dealer="AVISA")]
    vn = flota("VN", 6, fuente="vn") + [coche("ESPPROPIO0002", fuente="vn", dealer_id="ESP00710", dealer="AVISA")]
    otros = flota("EH", 4, fuente="vn_otros", cp="28045")
    capturar(datos, "vo", dia, vo)
    capturar(datos, "vn", dia, vn)
    capturar(datos, "vn_otros", dia, otros)
    return vo, vn, otros


def _n(pg, sql, *args):
    return pg.execute(sql, args).fetchone()[0]


def test_esquema_de_los_scrapers_no_ha_cambiado():
    """La ingesta asume el esquema 1 de los scrapers: si cambia, esta prueba avisa."""
    congelado = (RAIZ / "scrapers/vn/tests/fixtures/legacy_v100_schema.sql").read_text()
    norma = lambda s: re.sub(r"\s+", " ", s).strip()
    assert norma(VO.SCHEMA) == norma(congelado)
    assert norma(VN.SCHEMA) == norma(congelado)


def test_carga_basica(pg, datos):
    _tres_fuentes(datos)
    fallar(datos, "vo", D2)  # un intento fallido no se carga
    res = sv.cargar(pg, dia=date(2026, 11, 10))
    assert {k: v["anuncios"] for k, v in res.items()} == {"vo": 9, "vn": 7, "vn_otros": 4}

    filas = pg.execute("SELECT fuente, tipo_stock, count(*) FROM stock_externo_snapshot "
                       "GROUP BY 1, 2 ORDER BY 1").fetchall()
    assert filas == [("vn", "VN", 7), ("vn_otros", "VN", 4), ("vo", "VO", 9)]
    assert _n(pg, "SELECT count(*) FROM scraper_runs") == 3

    fila = pg.execute("SELECT provincia_codigo, provincia, codigo_postal, km, precio_cents, anio, "
                      "fecha_matriculacion, bastidor, etiqueta_precio, datos "
                      "FROM stock_externo_snapshot WHERE id_anuncio = 'ESPVO000000'").fetchone()
    assert fila[:3] == ("41", "Sevilla", "41001")
    assert fila[3:7] == (15000, 2_500_000, 2025, date(2024, 3, 1))
    assert fila[7].startswith("WVWZZZ") and fila[8] == "al contado PVP Concesionario"
    assert "image_urls" not in fila[9] and fila[9]["n_imagenes"] == 2
    assert fila[9]["business"]["financing_terms"] == {"Rate": "199"}
    assert "model" not in fila[9]["business"]  # ya está en columna

    # VN: sin kilómetros (desconocido, no cero) y con estado de entrega
    vn = pg.execute("SELECT km, estado_entrega_codigo, estado_entrega, combustible FROM stock_externo_snapshot "
                    "WHERE fuente = 'vn_otros' LIMIT 1").fetchone()
    assert vn[0] is None and vn[1] == "40" and vn[2].startswith("En España") and vn[3] == "Eléctrico"
    assert _n(pg, "SELECT count(DISTINCT provincia) FROM stock_externo_snapshot WHERE fuente='vn_otros'") == 1

    # Concesionario propio
    propios = pg.execute("SELECT fuente, id_anuncio FROM stock_externo_snapshot WHERE es_propio ORDER BY 1").fetchall()
    assert propios == [("vn", "ESPPROPIO0002"), ("vo", "ESPPROPIO0001")]

    # Eventos 'new' de todos y anuncios presentes
    assert _n(pg, "SELECT count(*) FROM eventos_externos WHERE tipo = 'new'") == 20
    assert _n(pg, "SELECT count(*) FROM anuncios_externos WHERE estado = 'presente'") == 20
    ok = pg.execute("SELECT fuente, estado FROM fuentes_estado WHERE tipo = 'carga' ORDER BY 1").fetchall()
    assert ok == [("vn", "ok"), ("vn_otros", "ok"), ("vo", "ok")]


def test_dos_cargas_el_mismo_dia_no_duplican(pg, datos):
    _tres_fuentes(datos)
    sv.cargar(pg)
    antes = _n(pg, "SELECT count(*) FROM stock_externo_snapshot")
    res = sv.cargar(pg)
    assert all(r["runs_nuevos"] == 0 for r in res.values())
    assert _n(pg, "SELECT count(*) FROM stock_externo_snapshot") == antes
    assert _n(pg, "SELECT count(*) FROM eventos_externos") == 20
    # Aunque se perdiera el registro de pasadas, las claves únicas impiden duplicar
    with pytest.raises(psycopg.errors.UniqueViolation):
        pg.execute("INSERT INTO stock_externo_snapshot (captured_at, snapshot_day, run_id, fuente, tipo_stock, "
                   "id_anuncio, datos) SELECT captured_at, snapshot_day, run_id, fuente, tipo_stock, id_anuncio, "
                   "datos FROM stock_externo_snapshot LIMIT 1")


def test_carga_incremental(pg, datos):
    vo, _, _ = _tres_fuentes(datos)
    sv.cargar(pg)
    nuevo = coche("ESPVONUEVO01")
    capturar(datos, "vo", D2, vo + [nuevo])
    res = sv.cargar(pg)
    assert res["vo"]["runs_nuevos"] == 1 and res["vo"]["dias"] == [D2] and res["vo"]["anuncios"] == 10
    assert res["vn"]["runs_nuevos"] == 0
    assert _n(pg, "SELECT count(*) FROM stock_externo_snapshot WHERE snapshot_day = %s", D2) == 10


def test_desaparecer_no_es_vender(pg, datos):
    base = flota("VO", 10)
    capturar(datos, "vo", D1, base)
    sv.cargar(pg, ["vo"])
    sigue = base[1:]
    capturar(datos, "vo", D2, sigue)  # ESPVO000000 desaparece
    sv.cargar(pg, ["vo"])
    estado = lambda: pg.execute("SELECT estado, ausente_desde IS NOT NULL, reapariciones, last_seen::date "
                                "FROM anuncios_externos WHERE id_anuncio = 'ESPVO000000'").fetchone()
    assert estado() == ("ausente", True, 0, date(2026, 11, 10))
    capturar(datos, "vo", D3, sigue)
    capturar(datos, "vo", D4, sigue)  # tercera ausencia: el scraper lo da por retirado
    sv.cargar(pg, ["vo"])
    assert estado()[:3] == ("retirado", True, 0)
    capturar(datos, "vo", D5, base)  # vuelve
    sv.cargar(pg, ["vo"])
    assert estado() == ("presente", False, 1, date(2026, 11, 14))
    kinds = [r[0] for r in pg.execute("SELECT tipo FROM eventos_externos WHERE id_anuncio = 'ESPVO000000' "
                                      "ORDER BY event_id")]
    assert kinds == ["new", "not_seen", "removed", "returned"]
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("UPDATE anuncios_externos SET estado = 'vendido'")


def test_cambio_de_precio_queda_en_eventos_y_snapshots(pg, datos):
    base = flota("VO", 5)
    capturar(datos, "vo", D1, base)
    cambiado = [dict(c) for c in base]
    nuevo = coche("ESPVO000000", precio=2_400_000)
    cambiado[0] = nuevo
    capturar(datos, "vo", D2, cambiado)
    sv.cargar(pg, ["vo"])
    precios = [r[0] for r in pg.execute("SELECT precio_cents FROM stock_externo_snapshot "
                                        "WHERE id_anuncio = 'ESPVO000000' ORDER BY snapshot_day")]
    assert precios == [2_500_000, 2_400_000]
    ev = pg.execute("SELECT valor_anterior, valor_nuevo FROM eventos_externos "
                    "WHERE tipo = 'changed' AND campo = 'price_retail_cents'").fetchone()
    assert ev == (2_500_000, 2_400_000)


def test_bases_de_los_scrapers_no_se_modifican(pg, datos):
    _tres_fuentes(datos)
    huella = {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in datos.rglob("*.sqlite3")}
    sv.cargar(pg)
    sv.comprobar(pg, dia=date(2026, 11, 10))
    assert {p: hashlib.sha256(p.read_bytes()).hexdigest() for p in datos.rglob("*.sqlite3")} == huella


def test_conexion_sqlite_de_solo_lectura(datos):
    _tres_fuentes(datos)
    db = sv.abrir_sqlite(sv.FUENTES["vo"])
    with pytest.raises(Exception, match="readonly"):
        db.execute("DELETE FROM runs")
    db.close()


def test_ambito_equivocado_se_rechaza_y_no_frena_las_demas(pg, datos):
    _tres_fuentes(datos)
    # La base de vn_otros sustituida por una de B/D: no se mezclan ámbitos
    (datos / "vn_otros" / "vw_vn_stock.sqlite3").write_bytes((datos / "vn" / "vw_vn_stock.sqlite3").read_bytes())
    res = sv.cargar(pg)
    assert "error" in res["vn_otros"] and "ámbito" in res["vn_otros"]["error"]
    assert res["vo"]["anuncios"] == 9 and res["vn"]["anuncios"] == 7
    assert _n(pg, "SELECT count(*) FROM fuentes_estado WHERE fuente='vn_otros' AND estado='error'") == 1
    assert _n(pg, "SELECT count(*) FROM scraper_runs WHERE fuente='vn_otros'") == 0


def test_comprobacion_de_las_nueve(pg, datos):
    _tres_fuentes(datos, D1)
    capturar(datos, "vo", D2, flota("VO", 8))
    fallar(datos, "vn", D2, "El total cambió durante la captura")
    (datos / "vn_otros" / "vw_vn_stock.sqlite3").unlink()
    res = sv.comprobar(pg, dia=date(2026, 11, 11))
    assert res["vo"]["estado"] == "ok" and res["vo"]["anuncios"] == 8  # cargado antes de comprobar
    assert res["vn"]["estado"] == "sin_captura"
    assert res["vn"]["ultima_captura"] == D1 and res["vn"]["ultimo_intento"]["estado"] == "failed"
    assert res["vn_otros"]["estado"] == "error"
    filas = pg.execute("SELECT fuente, estado FROM fuentes_estado WHERE tipo='comprobacion' ORDER BY 1").fetchall()
    assert filas == [("vn", "sin_captura"), ("vn_otros", "error"), ("vo", "ok")]


def test_comprobacion_sin_cargar(pg, datos):
    _tres_fuentes(datos)
    res = sv.comprobar(pg, dia=date(2026, 11, 10), cargar_antes=False)
    assert {r["estado"] for r in res.values()} == {"sin_cargar"}


def test_tablas_de_hechos_solo_admiten_anadir(pg, datos):
    _tres_fuentes(datos)
    sv.cargar(pg)
    for sql in ("UPDATE stock_externo_snapshot SET precio_cents = 1",
                "DELETE FROM stock_externo_snapshot",
                "TRUNCATE stock_externo_snapshot CASCADE",
                "DELETE FROM eventos_externos",
                "UPDATE scraper_runs SET observed_count = 0",
                "DELETE FROM fuentes_estado"):
        with pytest.raises(psycopg.errors.RaiseException, match="solo admite añadir"):
            pg.execute(sql)
    pg.execute("UPDATE stock_externo_snapshot SET es_propio = NOT es_propio")  # la única excepción


def test_remarcar_propio(pg, datos, tmp_path, monkeypatch):
    _tres_fuentes(datos)
    sv.cargar(pg)
    otra = tmp_path / "concesionario.yaml"
    otra.write_text("propio:\n  nombre: AVISA\n  dealer_ids: [ESP00217]\n", encoding="utf-8")
    monkeypatch.setenv("CDF_CONCESIONARIO", str(otra))
    assert sv.remarcar_propio(pg) == 20  # 18 de ESP00217 pasan a propios y los 2 de ESP00710 dejan de serlo
    assert _n(pg, "SELECT count(*) FROM stock_externo_snapshot WHERE es_propio") == 18
    assert sv.remarcar_propio(pg) == 0


def test_recuento_y_concesionarios(pg, datos):
    _tres_fuentes(datos)
    sv.cargar(pg)
    filas = sv.recuento(pg)
    assert [(str(f[0]), f[1], f[3], f[4], f[5]) for f in filas] == [
        (D1, "vn", 7, 7, 1), (D1, "vn_otros", 4, 4, 0), (D1, "vo", 9, 9, 1)]
    nuestros = sv.concesionarios(pg)
    assert {(f[0], f[1], f[4]) for f in nuestros} == {("ESP00710", "AVISA", "VN"), ("ESP00710", "AVISA", "VO")}


def test_config_concesionario_del_repositorio():
    assert sv.dealers_propios(RAIZ / "config" / "concesionario.yaml") == {"ESP00710"}
