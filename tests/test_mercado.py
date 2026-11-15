"""Precálculo de Mercado (analitica/mercado.py): una prueba por regla estadística de la vista."""

from datetime import date, timedelta

import pytest

from analitica import mercado as am
from ingesta import scrapers_vw as sv
from sinteticos import capturar, coche, fallar

D0 = date(2026, 10, 1)
# Flotas de pocos coches: el control de caída anómala del scraper (25 %) saltaría con cualquier baja.
PERMISIVO = {"max_count_drop_fraction": 0.99, "max_missing_fraction": 0.99}


def d(n: int) -> date:
    return D0 + timedelta(days=n)


def car(vid, fuente="vo", modelo="Golf", precio=2_500_000, cp="41001", dealer="ESP00217", nombre=None):
    return coche(vid, fuente=fuente, modelo=modelo, precio=precio, cp=cp, dealer_id=dealer,
                 dealer=nombre or f"Concesionario {dealer}")


def plan(datos, fuente, dias: dict[int, list[str]], **kw):
    """Capturas de `fuente`: {día: [ids]}; los días que faltan no tienen captura."""
    for n in sorted(dias):
        capturar(datos, fuente, d(n).isoformat(), [car(v, fuente=fuente, **kw) for v in dias[n]], PERMISIVO)


def precalcular(pg, hasta):
    sv.cargar(pg, dia=hasta)
    return am.precalcular(pg, hasta=hasta)


def fila_dia(pg, dia, tipo="VO", zona="espana"):
    r = pg.execute("SELECT anuncios, altas_hoy, ausencias_hoy, bajas_7, ayer_completo, hace7_completo, "
                   "ventana_completa FROM panel_mercado_dia WHERE dia = %s AND tipo = %s AND zona = %s",
                   (dia, tipo, zona)).fetchone()
    return None if r is None else dict(zip(
        ["anuncios", "altas", "ausencias", "bajas", "ayer", "hace7", "ventana"], r))


def fila_modelo(pg, dia, modelo="Golf", tipo="VO", zona="espana"):
    r = pg.execute("SELECT anuncios, anuncios_hace7, altas_7, ausencias_7, precio_mediano_cents, n_precio, "
                   "dias_mediana, dias_cota FROM panel_mercado_modelo WHERE dia = %s AND tipo = %s AND zona = %s "
                   "AND modelo = %s", (dia, tipo, zona, modelo)).fetchone()
    return None if r is None else dict(zip(
        ["anuncios", "hace7", "altas7", "ausencias7", "precio", "n_precio", "dias", "cota"], r))


# ---------------------------------------------------------------- un día cuenta solo con captura completa

def test_dia_sin_captura_completa_no_tiene_filas(pg, datos):
    plan(datos, "vo", {0: ["A", "B"], 1: ["A", "B"]})
    fallar(datos, "vo", d(2).isoformat())          # intento fallido: no es captura completa
    plan(datos, "vo", {3: ["A", "B"]})
    precalcular(pg, d(3))
    assert [fila_dia(pg, d(n)) is not None for n in range(4)] == [True, True, False, True]


def test_vn_solo_cuenta_si_tienen_captura_vn_y_vn_otros(pg, datos):
    # vn desde el día 0; vn_otros empieza el día 2 (fuente que empieza más tarde)
    plan(datos, "vn", {0: ["N1", "N2"], 1: ["N1", "N2", "N3"], 2: ["N1", "N2", "N3"], 3: ["N1", "N2", "N3", "N4"]})
    plan(datos, "vn_otros", {2: ["E1"], 3: ["E1", "E2"]})
    precalcular(pg, d(3))
    assert fila_dia(pg, d(0), "VN") is None and fila_dia(pg, d(1), "VN") is None
    dia2 = fila_dia(pg, d(2), "VN")
    assert dia2["anuncios"] == 4                      # VN = vn + vn_otros
    assert dia2["altas"] is None and not dia2["ayer"]  # el día 1 no cuenta para VN: sin altas
    dia3 = fila_dia(pg, d(3), "VN")
    # N4 (vn, con captura de vn el día 2) y E2 (vn_otros, con su captura del día 2) son altas
    assert dia3["altas"] == 2 and dia3["ausencias"] == 0


# ---------------------------------------------------------------- altas observadas

def test_altas_primera_captura_y_tras_hueco_no_son_altas(pg, datos):
    plan(datos, "vo", {0: ["A", "B"], 1: ["A", "B", "C"], 3: ["A", "C", "D"], 4: ["A", "C", "D", "E"]})
    precalcular(pg, d(4))
    assert fila_dia(pg, d(0))["altas"] is None      # primera captura: nada es alta
    assert fila_dia(pg, d(1))["altas"] == 1         # C
    dia3 = fila_dia(pg, d(3))                         # tras el hueco del día 2: sin cifra, no un cero
    assert dia3["altas"] is None and dia3["ausencias"] is None
    assert fila_dia(pg, d(4))["altas"] == 1         # E; D apareció tras un hueco y no lo es


def test_ausencias_de_hoy_estaban_ayer_y_hoy_no(pg, datos):
    plan(datos, "vo", {0: ["A", "B", "C"], 1: ["A", "D"]})
    precalcular(pg, d(1))
    assert fila_dia(pg, d(1))["ausencias"] == 2     # B y C
    assert fila_dia(pg, d(1))["altas"] == 1         # D


# ---------------------------------------------------------------- reglas de 7 días

def _semana(datos, sin=()):
    """A siempre; B solo los días 0–2 (retirada el 5); C hasta el 5 (ausente sin retirar el 7);
    D solo el día 0 (retirada el 3); E aparece el día 4."""
    dias = {}
    for n in range(9):
        if n in sin:
            continue
        ids = ["A"]
        ids += ["B"] if n <= 2 else []
        ids += ["C"] if n <= 5 else []
        ids += ["D"] if n == 0 else []
        ids += ["E"] if n >= 4 else []
        dias[n] = ids
    plan(datos, "vo", dias)


def test_variacion_y_ventana_de_7_dias(pg, datos):
    _semana(datos)
    precalcular(pg, d(8))
    m7 = fila_modelo(pg, d(7))
    assert m7["anuncios"] == 2 and m7["hace7"] == 4             # A, E frente a A, B, C, D
    assert m7["altas7"] == 1                                      # E (día 4)
    assert m7["ausencias7"] == 3                                  # B, C y D: vistos del día 0 al 6, hoy no
    # B (sin ver los días 3–5) y D (1–3) ya están retiradas; C solo lleva dos capturas sin verse
    assert fila_dia(pg, d(7))["bajas"] == 2
    m8 = fila_modelo(pg, d(8))
    assert m8["ausencias7"] == 2                                  # D se vio por última vez el día 0: fuera
    # antes del día 7 no hay captura de hace 7 días: sin variación (NULL, nunca 0)
    m6 = fila_modelo(pg, d(6))
    assert m6["hace7"] is None and m6["altas7"] is None and m6["ausencias7"] is None


def test_falta_un_dia_en_la_ventana_sin_cifra(pg, datos):
    _semana(datos, sin={3})
    precalcular(pg, d(8))
    dia8 = fila_dia(pg, d(8))
    assert dia8["hace7"] and not dia8["ventana"]                 # hace 7 días (el 1) sí; el 3 falta
    m8 = fila_modelo(pg, d(8))
    assert m8["hace7"] == 3 and m8["altas7"] is None and m8["ausencias7"] is None
    assert dia8["bajas"] is None


def test_ausencia_es_baja_solo_si_el_scraper_la_retira(pg, datos):
    plan(datos, "vo", {0: ["A", "B"], 1: ["A", "B"], 2: ["A"], 3: ["A"], 4: ["A"], 5: ["A"], 6: ["A"], 7: ["A"]})
    precalcular(pg, d(7))
    # B falta desde el día 2: not_seen el 2 y 3, retirada el 4 (tercera captura sin verla)
    assert fila_dia(pg, d(3))["bajas"] is None                   # sin ventana completa todavía
    assert fila_dia(pg, d(7))["bajas"] == 1
    plan(datos, "vo", {8: ["A", "C"]})
    precalcular(pg, d(8))
    assert fila_dia(pg, d(8))["bajas"] == 1


# ---------------------------------------------------------------- días publicado y precio

def test_dias_publicado_cota_inferior(pg, datos):
    plan(datos, "vo", {0: ["A", "B"], 1: ["A", "B", "C"], 3: ["A", "C", "D"], 4: ["A", "C", "D", "E"]})
    precalcular(pg, d(4))
    # edades el día 4: E 0 (alta), D 1 (tras hueco), C 3 (alta), A 4 (primera captura)
    m = fila_modelo(pg, d(4))
    assert (m["dias"], m["cota"]) == (1, True)                   # el mediano (D) no tiene alta observada


def test_dias_publicado_exacto_si_la_mitad_baja_tiene_altas(pg, datos):
    plan(datos, "vo", {0: ["A"], 1: ["A", "B"], 2: ["A", "B", "C"], 3: ["A", "B", "C", "D"], 4: ["A", "B", "C", "D", "E"]})
    precalcular(pg, d(4))
    m = fila_modelo(pg, d(4))
    # edades: E 0, D 1, C 2, B 3 (todas altas observadas) y A 4 (primera captura): mediana 2, exacta
    assert (m["dias"], m["cota"]) == (2, False)


def test_precio_mediano_solo_con_precio(pg, datos):
    coches = [car("P1", precio=1_000_000), car("P2", precio=2_000_000), car("P3", precio=3_000_000),
              car("P4", precio=None), car("P5", precio=9_000_000)]
    capturar(datos, "vo", d(0).isoformat(), coches)
    precalcular(pg, d(0))
    m = fila_modelo(pg, d(0))
    assert (m["anuncios"], m["n_precio"], m["precio"]) == (5, 4, 2_500_000)   # media de los dos centrales


# ---------------------------------------------------------------- zonas, modelo y sustitución

def test_zonas_segun_la_provincia_del_concesionario(pg, datos):
    capturar(datos, "vo", d(0).isoformat(), [
        car("S1", cp="41001"), car("S2", cp="41500", dealer="ESP00710"), car("M1", cp="29001"),
        car("X1", cp="28001"), car("N1", cp=None)])
    precalcular(pg, d(0))
    zonas = {z: pg.execute("SELECT anuncios, propios, concesionarios, zona_nombre FROM panel_mercado_dia "
                           "WHERE dia = %s AND tipo = 'VO' AND zona = %s", (d(0), z)).fetchone()
             for z in ("provincia", "comunidad", "espana")}
    assert zonas["provincia"] == (2, 1, 2, "Sevilla")
    assert zonas["comunidad"] == (3, 1, 2, "Andalucía")
    assert zonas["espana"] == (5, 1, 2, "España")


def test_modelo_normalizado(pg, datos):
    capturar(datos, "vo", d(0).isoformat(), [
        coche("M1", modelo="T-Roc"), coche("M2", modelo=" T-Roc "), coche("M3", modelo="T-ROC"),
        coche("M4", modelo="T-Roc"), coche("M5", modelo="")])
    precalcular(pg, d(0))
    modelos = dict(pg.execute("SELECT modelo, anuncios FROM panel_mercado_modelo WHERE dia = %s AND zona = 'espana'",
                              (d(0),)).fetchall())
    assert modelos == {"T-Roc": 4, "Sin modelo": 1}


def test_modelo_sin_la_marca_delante_y_rehacer_el_historico(pg, datos, monkeypatch):
    """«Volkswagen T-Cross» se muestra «T-Cross» (marca de config/concesionario.yaml). Rehacer todo el
    histórico (make precalcular, sin argumentos) sustituye las filas viejas sin duplicar modelos."""
    import dataclasses
    from ingesta.concesionario import cargar
    conc = cargar()
    for n in range(3):
        capturar(datos, "vo", d(n).isoformat(), [
            coche("B1", modelo="Volkswagen T-Cross"), coche("B2", modelo="T-Cross"),
            coche("B3", modelo="VOLKSWAGEN  Golf Variant"), coche("B4", modelo="Golf Variant"),
            coche("B5", modelo="Volkswagen")], PERMISIVO)
    sv.cargar(pg, dia=d(2))
    # Filas como las dejaba la versión anterior (sin quitar la marca): con otra marca configurada
    am.precalcular(pg, hasta=d(2), conc=dataclasses.replace(conc, marca="Otra"))
    viejos = {m for (m,) in pg.execute("SELECT DISTINCT modelo FROM panel_mercado_modelo")}
    assert "Volkswagen T-Cross" in viejos and "T-Cross" in viejos
    am.precalcular(pg, hasta=d(2), conc=conc)                         # make precalcular: todo el histórico
    for n in range(3):
        modelos = dict(pg.execute("SELECT modelo, anuncios FROM panel_mercado_modelo WHERE dia = %s AND zona = "
                                  "'espana' AND tipo = 'VO'", (d(n),)).fetchall())
        assert modelos == {"T-Cross": 2, "Golf Variant": 2, "Volkswagen": 1}   # solo la marca: se deja
    assert pg.execute("SELECT count(*) FROM panel_mercado_modelo WHERE modelo ILIKE 'volkswagen %'").fetchone()[0] == 0
    assert am.normalizar_modelo("volkswagen   ID.4", "Volkswagen") == "ID.4"
    assert am.normalizar_modelo("Volkswagenwagen", "Volkswagen") == "Volkswagenwagen"


def test_recalcular_un_dia_lo_sustituye(pg, datos):
    plan(datos, "vo", {0: ["A", "B"], 1: ["A", "B", "C"]})
    precalcular(pg, d(1))
    antes = pg.execute("SELECT count(*) FROM panel_mercado_modelo").fetchone()[0]
    am.precalcular(pg, d(1), d(1))
    am.precalcular(pg, d(1), d(1))
    assert pg.execute("SELECT count(*) FROM panel_mercado_modelo").fetchone()[0] == antes
    assert fila_dia(pg, d(1))["anuncios"] == 3
    # Las tablas de hechos no se tocan: siguen igual
    assert pg.execute("SELECT count(*) FROM stock_externo_snapshot").fetchone()[0] == 5


def test_capturas_por_fuente_y_dias_sin_captura(pg, datos):
    plan(datos, "vo", {0: ["A"], 2: ["A", "B"]})
    capturar(datos, "vo", d(3).isoformat(), [car("A"), car("Z", cp=None)], PERMISIVO)
    precalcular(pg, d(4))
    filas = pg.execute("SELECT dia, completa, anuncios, sin_provincia FROM panel_mercado_capturas "
                       "WHERE fuente = 'vo' ORDER BY dia").fetchall()
    assert [(f[0], f[1]) for f in filas] == [(d(0), True), (d(1), False), (d(2), True), (d(3), True), (d(4), False)]
    assert filas[3][2:] == (2, 1)


# ---------------------------------------------------------------- tarea: anotación y aviso si falla

def test_tarea_anota_ok(pg, datos, pg_url, monkeypatch):
    plan(datos, "vo", {0: ["A"]})
    sv.cargar(pg, dia=d(0))
    monkeypatch.setattr(am, "hoy_madrid", lambda: d(0))
    assert am.ejecutar(None, d(0), avisar=lambda t: pytest.fail("no debe avisar")) == 0
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE tipo = 'precalculo'").fetchone()
    assert estado == "ok" and detalle["dias"] == 1


def test_tarea_fallida_anota_y_avisa_por_telegram(pg, pg_url, monkeypatch):
    monkeypatch.setattr(am, "hoy_madrid", lambda: d(0))

    def roto(*a, **k):
        raise RuntimeError("fallo de prueba")

    monkeypatch.setattr(am, "precalcular", roto)
    enviados = []
    assert am.ejecutar(None, None, avisar=lambda t: enviados.append(t) or 1) == 1
    assert enviados == ["CDF 01/10 · el precálculo del panel ha fallado\nPaso: precálculo (RuntimeError)\n"
                        "Detalle: registro de precalcular de hoy (make logs)"]
    estados = dict(pg.execute("SELECT tipo, estado FROM fuentes_estado").fetchall())
    assert estados == {"precalculo": "error", "aviso": "ok"}


# ---------------------------------------------------------------- nuestros por modelo

def test_propios_por_modelo(pg, datos):
    capturar(datos, "vo", d(0).isoformat(), [
        car("P1", dealer="ESP00710"), car("P2", dealer="ESP00710", modelo="Polo"), car("O1"), car("O2"),
        car("O3", modelo="Polo"), car("O4", modelo="Tiguan"), car("X1", dealer="ESP00710", cp="28001")])
    precalcular(pg, d(0))
    propios = {(z, m): (n, p) for z, m, n, p in pg.execute(
        "SELECT zona, modelo, anuncios, propios FROM panel_mercado_modelo WHERE dia = %s", (d(0),))}
    assert propios[("provincia", "Golf")] == (3, 1)
    assert propios[("provincia", "Polo")] == (2, 1)
    assert propios[("provincia", "Tiguan")] == (1, 0)          # 0 propios: 0, no NULL
    assert propios[("espana", "Golf")] == (4, 2)               # el de Madrid cuenta en España


# ---------------------------------------------------------------- por concesionario

def _conc(pg, dia, tipo="VO"):
    return {r[0]: dict(zip(["nombre", "propio", "anuncios", "hace7", "precio", "n_precio"], r[1:]))
            for r in pg.execute("SELECT dealer_id, concesionario, es_propio, anuncios, anuncios_hace7, "
                                "precio_mediano_cents, n_precio FROM panel_mercado_concesionario "
                                "WHERE dia = %s AND tipo = %s", (dia, tipo))}


def test_por_concesionario_de_la_provincia(pg, datos):
    """A (propio) siempre; B siempre (cambia de nombre el día 6); C deja de publicar el día 7;
    D aparece el día 7; M es de Madrid (fuera de la provincia); N no dice su concesionario."""
    for n in range(9):
        coches = [car(f"A{i}", dealer="ESP00710", nombre="Propio", precio=(i + 1) * 1_000_000) for i in range(3)]
        coches += [car(f"B{i}", dealer="ESPB", nombre="Nombre viejo" if n < 6 else "Nombre nuevo") for i in range(2)]
        coches += [car("C0", dealer="ESPC")] if n != 7 else []
        coches += [car(f"D{i}", dealer="ESPD") for i in range(4)] if n >= 7 else []
        coches += [car("M0", dealer="ESPM", cp="28001"), car("N0", dealer=None)]
        capturar(datos, "vo", d(n).isoformat(), coches, PERMISIVO)
    precalcular(pg, d(8))
    dia7 = _conc(pg, d(7))
    assert set(dia7) == {"ESP00710", "ESPB", "ESPD"}          # C no publica ese día; M es de otra provincia
    assert dia7["ESP00710"] == {"nombre": "Propio", "propio": True, "anuncios": 3, "hace7": 3,
                                "precio": 2_000_000, "n_precio": 3}
    assert dia7["ESPB"]["nombre"] == "Nombre nuevo" and not dia7["ESPB"]["propio"]
    assert dia7["ESPD"]["anuncios"] == 4 and dia7["ESPD"]["hace7"] == 0     # nuevo: hace 7 días no tenía
    assert _conc(pg, d(3))["ESPB"]["nombre"] == "Nombre viejo"             # el más reciente hasta ese día
    assert "ESPC" in _conc(pg, d(8))                                        # vuelve a publicar
    assert _conc(pg, d(6))["ESPB"]["hace7"] is None                         # sin captura de hace 7 días
    # El peso se calcula sobre todos los anuncios de la provincia, también los que no dicen concesionario
    total = pg.execute("SELECT anuncios FROM panel_mercado_dia WHERE dia = %s AND tipo = 'VO' AND zona = 'provincia'",
                       (d(7),)).fetchone()[0]
    assert total == 3 + 2 + 4 + 1


def test_por_concesionario_vn_suma_vn_y_vn_otros(pg, datos):
    plan(datos, "vn", {0: ["N1", "N2"], 1: ["N1", "N2"]}, dealer="ESPX")
    plan(datos, "vn_otros", {1: ["E1"]}, dealer="ESPX")
    precalcular(pg, d(1))
    assert _conc(pg, d(0), "VN") == {}                         # sin vn_otros, el día no cuenta para VN
    assert _conc(pg, d(1), "VN")["ESPX"]["anuncios"] == 3
