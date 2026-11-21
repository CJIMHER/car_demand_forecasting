"""Segmentos de Mercado por modelo: lectura de la versión (acabado y motor), cortes de antigüedad y
km, precálculo (cuadra con SQL directo), migración 0007, informe de cobertura y la vista de detalle.

Sin red: Postgres local y el TestClient, con capturas sintéticas escritas por el código real de los
scrapers. Los textos de versión de las pruebas de lectura son reales (publicados en la web, sin datos
personales).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

import psycopg
import pytest

from analitica import mercado as am
from analitica import segmentos as sg
from analitica import versiones
from ingesta import scrapers_vw as sv
from ingesta.concesionario import ErrorConfiguracion, cargar as cargar_concesionario
from sinteticos import RAIZ, capturar, coche, flota

MARCA = "Volkswagen"     # la de config/concesionario.yaml; aquí solo como dato de entrada
HOY = date(2026, 11, 14)


@pytest.fixture(scope="module")
def cat():
    return versiones.cargar(MARCA)


# ================================================================== lectura de la versión

# (tipo, texto real, CV del campo power_cv, combustible) → (acabado, motor)
PARES = [
    (("VO", "Volkswagen T-Roc R-Line 1.5 eTSI 110 kW (150 CV) DSG", 150, "Gasolina"),
     ("VN", "T-Roc R-Line  1.5 eTSI 110 kW (150 CV)  Automático DSG 7 vel.", 150, "Gasolina"),
     ("R-Line", "eTSI 150 CV · Gasolina · Automático")),
    (("VO", "Volkswagen T-Roc ``Más`` 1.5 eTSI 85 kW (116 CV) DSG", 116, "Gasolina"),
     ("VN", 'T-Roc "Más"  1.5 eTSI 85 kW (116 CV)  Automático DSG 7 vel.', 116, "Gasolina"),
     ("Más", "eTSI 116 CV · Gasolina · Automático")),
]


@pytest.mark.parametrize("vo, vn, esperado", PARES)
def test_vo_y_vn_dan_el_mismo_acabado_y_motor(cat, vo, vn, esperado):
    a = cat.leer(vo[0], "T-Roc", vo[1], vo[3], vo[2])
    b = cat.leer(vn[0], "T-Roc", vn[1], vn[3], vn[2])
    assert (a.acabado, a.motor) == (b.acabado, b.motor) == esperado
    assert a.reconocido and b.reconocido


def test_textos_reales_del_encargo(cat):
    leer = lambda tipo, texto, cv=None, comb="Gasolina": cat.leer(tipo, "T-Roc", texto, comb, cv)  # noqa: E731
    v = leer("VO", "Volkswagen T-Roc ``Más`` 1.5 TSI 110 kW (150 CV) DSG", 150)
    assert (v.acabado, v.tecnologia, v.cambio) == ("Más", "TSI", "Automático")
    v = leer("VO", "Volkswagen T-Roc Dark Line 1.0 TSI 85 kW (115 CV)", 116)
    assert v.acabado == "Dark Line" and v.tecnologia == "TSI"
    assert v.cv == 116 and "116 CV" in v.motor          # los CV salen de power_cv, no del texto (115)
    assert v.cambio is None                             # en VO no se deduce nada si falta «DSG»
    v = leer("VN", "T-Roc T-Roc  1.5 eTSI 85 kW (116 CV)  Automático DSG 7 vel.", 116)
    assert (v.acabado, v.reconocido) == ("Básico", True)
    v = leer("VN", 'T-Cross "Más"  1.0 TSI 85 kW (116 CV)  6 vel.')
    assert v.cambio == "Manual"                         # en VN, «6 vel.» sin «Automático» es manual


def test_texto_desconocido_va_a_sin_clasificar(cat):
    for modelo, texto in [("T-Roc", "Volkswagen T-Roc Dark 1.0 TSI 81 kW (110 CV)"),       # no es «Dark Line»
                          ("T-Cross", "Volkswagen T-Cross 1.0 TSI 70 kW (95 CV)"),         # sin acabado escrito
                          ("Tayron", "Tayron R-Line Classic  2.0 TDI 4Motion 142 kW (193 CV)  Automático DSG 7 vel."),
                          ("Tiguan", "Tiguan Tiguan Classic  1.5 eTSI 96 kW (130 CV)  Automático DSG 7 vel."),
                          ("Golf", "Polo Life 1.0 TSI 70 kW (95 CV)"),                     # otro modelo
                          ("Golf", None), ("Golf", "")]:
        v = cat.leer("VO", modelo, texto, "Gasolina", 110)
        assert (v.acabado, v.reconocido) == ("Sin clasificar", False), texto
    # El motor se lee aunque el acabado no
    assert cat.leer("VO", "T-Roc", "Volkswagen T-Roc Dark 1.0 TSI 81 kW (110 CV)", "Gasolina", 110).motor == \
        "TSI 110 CV · Gasolina"


def test_sinonimos_comillas_espacios_y_motores(cat):
    assert cat.leer("VO", "Golf", "Volkswagen Golf Mas 2.0 TDI 85 kW (115 CV)", "Diésel", 116).acabado == "Más"
    assert cat.leer("VO", "Golf", "VOLKSWAGEN   golf   r line 1.5 eTSI 110 kW (150 CV) DSG", "Gasolina", 150).acabado \
        == "R-Line"
    v = cat.leer("VO", "Tiguan", "Volkswagen Tiguan R-Line 1.5 TSI eHybrid 150 kW (204 CV) DSG", "Híbrido", 204)
    assert v.motor == "eHybrid 204 CV · Híbrido · Automático"
    v = cat.leer("VN", "Tiguan", "Tiguan R-Line 4Motion  2.0 TDI 4Motion 142 kW (193 CV)  Automático DSG 7 vel.",
                 "Diésel", 193)
    assert (v.acabado, v.motor) == ("R-Line", "TDI 193 CV · Diésel · Automático")
    v = cat.leer("VO", "ID.4", "Volkswagen ID.4 Pro 77kWh Auto 150 kW (204 CV)", "Eléctrico", 204)
    assert (v.acabado, v.motor) == ("Pro", "Eléctrico 204 CV · Automático")
    v = cat.leer("VN", "T-Roc Cabriolet", "T-Roc Cabrio R-Line  1.5 TSI 110 kW (150 CV)  Automático DSG 7 vel.",
                 "Gasolina", 150)
    assert v.acabado == "R-Line"
    v = cat.leer("VN", "Golf", "Golf GTE  1.5 TSI 130 kW (177 CV) / 85 kW (115 CV)  Automático DSG 6 vel.",
                 "Híbrido", 272)
    assert (v.acabado, v.tecnologia, v.cv) == ("GTE", "eHybrid", 272)
    assert cat.leer("VO", "Golf", "x", None, None).motor == "Sin dato de motor"


def test_catalogo_sin_marca_ni_concesionario_en_el_codigo():
    """Regla Replicable: ni la marca ni el concesionario ni su provincia escritos en el código o las plantillas."""
    prohibidas = re.compile(r"\b(volkswagen|avisa|sevilla)\b", re.IGNORECASE)
    ficheros = [p for carpeta in ("panel", "analitica") for p in (RAIZ / carpeta).rglob("*")
                if p.suffix in (".py", ".html", ".js", ".css") and "vendor" not in p.parts]
    assert len(ficheros) > 20
    for p in ficheros:
        for i, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            assert not prohibidas.search(linea), f"{p.relative_to(RAIZ)}:{i}: {linea.strip()}"


def test_sinonimo_repetido_en_dos_acabados_no_se_acepta():
    with pytest.raises(versiones.ErrorCatalogo):
        versiones.Catalogo({"comunes": [{"nombre": "Más"}, {"nombre": "Plus", "sinonimos": ["Mas"]}]})


# ================================================================== cortes de antigüedad y km

def test_cortes_de_antiguedad_en_los_bordes():
    conc = cargar_concesionario()
    ant = conc.tramos_antiguedad
    assert [e for _, e in ant] == ["0–2 años", "3–4 años", "más de 4 años"]
    dia = date(2026, 11, 21)
    casos = {
        date(2023, 11, 22): (2, 0),   # 2 años y 364 días
        date(2023, 11, 21): (3, 1),   # 3 años justos
        date(2021, 11, 22): (4, 1),   # 4 años y 364 días
        date(2021, 11, 21): (5, 2),   # 5 años justos
        date(2026, 11, 21): (0, 0),   # matriculado el mismo día
    }
    for matriculacion, (anios, tramo) in casos.items():
        assert sg.anios_cumplidos(matriculacion, dia) == anios, matriculacion
        assert sg.tramo(anios, ant) == tramo, matriculacion
    assert sg.anios_cumplidos(date(2020, 2, 29), date(2023, 2, 28)) == 2      # el 29/2 se cumple el 1/3
    assert sg.anios_cumplidos(date(2020, 2, 29), date(2023, 3, 1)) == 3
    assert sg.anios_cumplidos(None, dia) is None and sg.anios_cumplidos(date(2027, 1, 1), dia) is None


def test_cortes_de_km_en_los_bordes():
    km = cargar_concesionario().tramos_km
    assert [e for _, e in km] == ["0–20.000 km", "20.001–60.000 km", "60.001–100.000 km", "más de 100.000 km"]
    for valor, tramo in {0: 0, 20000: 0, 20001: 1, 60000: 1, 60001: 2, 100000: 2, 100001: 3, 450000: 3}.items():
        assert sg.tramo(valor, km) == tramo, valor
    assert sg.tramo(None, km) is None and sg.tramo(-5, km) is None


def test_cortes_salen_de_la_configuracion(tmp_path):
    base = (RAIZ / "config" / "concesionario.yaml").read_text(encoding="utf-8")
    otro = base.split("segmentos:")[0] + (
        "segmentos:\n  antiguedad_anios:\n    - {hasta: 1, etiqueta: nuevo}\n    - {etiqueta: viejo}\n"
        "  km:\n    - {hasta: 50000, etiqueta: pocos}\n    - {etiqueta: muchos}\n")
    (tmp_path / "c.yaml").write_text(otro, encoding="utf-8")
    conc = cargar_concesionario(tmp_path / "c.yaml")
    assert conc.tramos_antiguedad == ((1, "nuevo"), (None, "viejo")) and conc.tramos_km == ((50000, "pocos"), (None, "muchos"))
    (tmp_path / "mal.yaml").write_text(base.split("segmentos:")[0] + (
        "segmentos:\n  km:\n    - {hasta: 50000, etiqueta: a}\n    - {hasta: 40000, etiqueta: b}\n    - {etiqueta: c}\n"),
        encoding="utf-8")
    with pytest.raises(ErrorConfiguracion):
        cargar_concesionario(tmp_path / "mal.yaml")
    (tmp_path / "sin.yaml").write_text(base.split("segmentos:")[0], encoding="utf-8")
    assert cargar_concesionario(tmp_path / "sin.yaml").tramos_km == ()


def test_percentil_como_percentile_cont():
    assert sg.percentil([], 0.5) is None
    assert sg.percentil([10], 0.25) == 10
    assert sg.percentil([10, 20], 0.5) == 15
    assert sg.percentil([10, 20, 30, 41], 0.25) == 18          # 17,5 → 18 (la mitad, hacia arriba)
    assert sg.percentil([10, 20, 30, 41], 0.75) == 33          # 32,75


# ================================================================== migración 0007

def test_stock_externo_tiene_version_combustible_cv_matriculacion_y_km(pg, datos):
    cols = {c for (c,) in pg.execute("SELECT column_name FROM information_schema.columns "
                                     "WHERE table_name = 'stock_externo_snapshot'")}
    assert {"version", "combustible", "potencia_cv", "fecha_matriculacion", "km", "cilindrada_cc",
            "color_exterior"} <= cols
    capturar(datos, "vo", "2026-11-10", [coche("ESP1", cv=116, version="Volkswagen Polo Life 1.0 TSI 70 kW (95 CV)"),
                                          coche("ESP2", cv=None, color="Azul")])
    sv.cargar(pg, dia=date(2026, 11, 10))
    filas = pg.execute("SELECT id_anuncio, potencia_cv, cilindrada_cc, color_exterior, km, fecha_matriculacion "
                       "FROM stock_externo_snapshot ORDER BY 1").fetchall()
    assert filas == [("ESP1", 116, 1498, None, 15000, date(2024, 3, 1)), ("ESP2", None, 1498, "Azul", 15000, date(2024, 3, 1))]
    # Sigue siendo de solo añadir: es_propio se puede cambiar, nada más
    pg.execute("UPDATE stock_externo_snapshot SET es_propio = NOT es_propio")
    with pytest.raises(psycopg.errors.RaiseException):
        pg.execute("UPDATE stock_externo_snapshot SET km = 1")
    with pytest.raises(psycopg.errors.GeneratedAlways):
        pg.execute("UPDATE stock_externo_snapshot SET potencia_cv = 1")


def test_migracion_rellena_las_filas_ya_cargadas(pg_url, pg, datos):
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(RAIZ / "alembic.ini"))
    command.downgrade(cfg, "0006")
    assert "potencia_cv" not in {c for (c,) in pg.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = 'stock_externo_snapshot'")}
    capturar(datos, "vo", "2026-11-10", [coche("ESP1", cv=204), coche("ESP2", cv=None)])
    sv.cargar(pg, dia=date(2026, 11, 10))
    command.upgrade(cfg, "head")
    assert pg.execute("SELECT id_anuncio, potencia_cv FROM stock_externo_snapshot ORDER BY 1").fetchall() == \
        [("ESP1", 204), ("ESP2", None)]
    assert pg.execute("SELECT count(*) FROM panel_mercado_segmento").fetchone() == (0,)


def test_cdf_panel_solo_lee_las_tablas_de_segmentos(panel_url):
    with psycopg.connect(panel_url, autocommit=True) as c:
        for t in ("panel_mercado_segmento", "panel_mercado_sin_clasificar"):
            c.execute(f"SELECT count(*) FROM {t}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"DELETE FROM {t}")


# ================================================================== precálculo

def _escenario(pg, datos, conc=None, hasta=HOY, dias=12):
    from escenario_mercado import escenario
    conc = conc or cargar_concesionario()
    escenario(datos, hasta, conc.provincia_codigo, conc.provincias_comunidad, conc.dealer_ids[0], dias=dias)
    sv.cargar(pg, dia=hasta)
    am.precalcular(pg, hasta=hasta, conc=conc)
    return conc


def _clasificar(pg, cat, dia):
    """Tabla temporal con el acabado y el motor de cada anuncio del día (los da el catálogo; el resto,
    SQL directo)."""
    pg.execute("CREATE TEMP TABLE clase (id bigint PRIMARY KEY, acabado text, motor text)")
    filas = pg.execute("SELECT id, tipo_stock, modelo, version, combustible, potencia_cv FROM stock_externo_snapshot "
                       "WHERE snapshot_day = %s", (dia,)).fetchall()
    with pg.cursor() as cur:
        cur.executemany("INSERT INTO clase VALUES (%s, %s, %s)", [
            (i, (v := cat.leer(t, am.normalizar_modelo(m, MARCA), ver, comb, cv)).acabado, v.motor)
            for i, t, m, ver, comb, cv in filas])


ZONA_SQL = {"provincia": "s.provincia_codigo = %(prov)s", "comunidad": "s.provincia_codigo = ANY(%(ccaa)s)",
            "espana": "true"}


def test_percentiles_y_n_cuadran_con_sql_directo(pg, datos, cat):
    conc = _escenario(pg, datos)
    dia = pg.execute("SELECT max(dia) FROM panel_mercado_segmento WHERE tipo = 'VO'").fetchone()[0]
    _clasificar(pg, cat, dia)
    km = conc.tramos_km
    caso_km = "CASE " + " ".join(f"WHEN s.km <= {h} THEN {i}" for i, (h, _) in enumerate(km) if h is not None) + \
              f" ELSE {len(km) - 1} END"
    ant = conc.tramos_antiguedad
    anios = "extract(year FROM age(s.snapshot_day, s.fecha_matriculacion))"
    caso_ant = "CASE " + " ".join(f"WHEN {anios} <= {h} THEN {i}" for i, (h, _) in enumerate(ant) if h is not None) + \
               f" ELSE {len(ant) - 1} END"
    niveles = {
        "modelo": ("s.modelo", "NULL", "NULL", "NULL", "NULL"),
        "acabado": ("s.modelo", "c.acabado", "NULL", "NULL", "NULL"),
        "motor": ("s.modelo", "c.acabado", "c.motor", "NULL", "NULL"),
        "antiguedad_km": ("s.modelo", "NULL", "NULL", caso_ant, caso_km),
        "acabado_antiguedad_km": ("s.modelo", "c.acabado", "NULL", caso_ant, caso_km),
    }
    p = {"dia": dia, "prov": conc.provincia_codigo, "ccaa": list(conc.provincias_comunidad)}
    comprobadas = 0
    for zona in ("provincia", "comunidad", "espana"):
        for nivel, (m, a, mo, an, k) in niveles.items():
            directo = {tuple(f[:5]): f[5:] for f in pg.execute(
                f"""SELECT {m}, {a}, {mo}, {an}, {k}, count(*), count(s.precio_cents),
                           round(percentile_cont(0.25) WITHIN GROUP (ORDER BY s.precio_cents)::numeric),
                           round(percentile_cont(0.5) WITHIN GROUP (ORDER BY s.precio_cents)::numeric),
                           round(percentile_cont(0.75) WITHIN GROUP (ORDER BY s.precio_cents)::numeric),
                           count(*) FILTER (WHERE s.es_propio)
                    FROM stock_externo_snapshot s JOIN clase c USING (id)
                    WHERE s.snapshot_day = %(dia)s AND s.tipo_stock = 'VO' AND {ZONA_SQL[zona]}
                      {"AND s.fecha_matriculacion IS NOT NULL AND s.km IS NOT NULL" if an != "NULL" else ""}
                    GROUP BY 1, 2, 3, 4, 5""", p)}
            precalculado = {tuple(f[:5]): f[5:] for f in pg.execute(
                """SELECT modelo, acabado, motor, antiguedad_orden, km_orden, anuncios, n_precio, precio_p25_cents,
                          precio_p50_cents, precio_p75_cents, propios
                   FROM panel_mercado_segmento WHERE dia = %s AND tipo = 'VO' AND zona = %s AND nivel = %s""",
                (dia, zona, nivel))}
            directo = {k: tuple(None if x is None else int(x) for x in v) for k, v in directo.items()}
            assert precalculado == directo, (zona, nivel)
            comprobadas += len(directo)
    assert comprobadas > 300
    # VN: sin matriz, con acabado y motor
    niveles_vn = {n for (n,) in pg.execute("SELECT DISTINCT nivel FROM panel_mercado_segmento WHERE tipo = 'VN'")}
    assert niveles_vn == {"modelo", "acabado", "motor"}


def test_segmento_con_dias_publicado_y_cota(pg, datos):
    """Los días publicado del nivel modelo son los mismos que los de la tabla por modelo."""
    _escenario(pg, datos)
    distintos = pg.execute(
        """SELECT count(*) FROM panel_mercado_segmento s JOIN panel_mercado_modelo m USING (dia, tipo, zona, modelo)
           WHERE s.nivel = 'modelo' AND (s.dias_mediana IS DISTINCT FROM m.dias_mediana OR s.dias_cota <> m.dias_cota
                                         OR s.anuncios <> m.anuncios OR s.propios <> m.propios)""").fetchone()[0]
    total = pg.execute("SELECT count(*) FROM panel_mercado_segmento WHERE nivel = 'modelo'").fetchone()[0]
    assert total > 0 and distintos == 0


def test_sin_matriz_si_la_configuracion_no_trae_cortes(pg, datos, tmp_path):
    base = (RAIZ / "config" / "concesionario.yaml").read_text(encoding="utf-8")
    (tmp_path / "c.yaml").write_text(base.split("segmentos:")[0], encoding="utf-8")
    conc = cargar_concesionario(tmp_path / "c.yaml")
    capturar(datos, "vo", "2026-11-10", flota("1", 5))
    sv.cargar(pg, dia=date(2026, 11, 10))
    am.precalcular(pg, hasta=date(2026, 11, 10), conc=conc)
    assert {n for (n,) in pg.execute("SELECT DISTINCT nivel FROM panel_mercado_segmento")} == \
        {"modelo", "acabado", "motor"}


def test_color_preparado_para_cuando_haya_dato(pg, datos):
    capturar(datos, "vo", "2026-11-10", flota("1", 3) + [coche("ESP9", color="Azul Atlantic")])
    sv.cargar(pg, dia=date(2026, 11, 10))
    am.precalcular(pg, hasta=date(2026, 11, 10))
    assert pg.execute("SELECT color, anuncios FROM panel_mercado_segmento WHERE nivel = 'color' AND zona = 'espana'"
                      ).fetchall() == [("Azul Atlantic", 1)]


# ================================================================== cobertura en «Estado de las fuentes»

def test_informe_de_cobertura_cuenta_los_sin_clasificar(pg, datos, cat):
    raro = "Volkswagen Golf Edición Inventada 1.5 TSI 110 kW (150 CV)"
    capturar(datos, "vo", "2026-11-10", [coche(f"ESP{i}", version="Volkswagen Golf Life 1.5 TSI 110 kW (150 CV)")
                                          for i in range(7)] + [coche("ESPR1", version=raro), coche("ESPR2", version=raro),
                                                                coche("ESPX", modelo="Polo", version="Polo 1.0")])
    sv.cargar(pg, dia=date(2026, 11, 10))
    am.precalcular(pg, hasta=date(2026, 11, 10))
    assert pg.execute("SELECT modelo, version, anuncios FROM panel_mercado_sin_clasificar ORDER BY anuncios DESC"
                      ).fetchall() == [("Golf", raro, 2), ("Polo", "Polo 1.0", 1)]
    from panel import mercado as pm
    c = pm.cobertura(pg)
    vo = c["tipos"][0]
    assert vo["tipo"] == "VO" and vo["total"] == "70,0 %" and vo["anuncios"] == "10"
    assert {m["modelo"]: (m["reconocidos"], m["porcentaje"]) for m in vo["modelos"]} == \
        {"Golf": ("7", "78 %"), "Polo": ("0", "0 %")}
    assert [t["version"] for t in c["textos"]] == [raro, "Polo 1.0"]


def test_cobertura_solo_veinte_textos_del_ultimo_dia(pg, datos):
    dia1, dia2 = date(2026, 11, 10), date(2026, 11, 11)
    capturar(datos, "vo", dia1.isoformat(), [coche("ESPVIEJO", version="Golf viejo")])
    capturar(datos, "vo", dia2.isoformat(), [coche("ESPVIEJO", version="Golf Life 1.0 TSI 70 kW (95 CV)")] +
             [coche(f"ESP{i}", version=f"Golf raro {i}") for i in range(25)])
    sv.cargar(pg, dia=dia2)
    am.precalcular(pg, hasta=dia2)
    from panel import mercado as pm
    textos = [t["version"] for t in pm.cobertura(pg)["textos"]]
    assert len(textos) == 20 and "Golf viejo" not in textos


# ================================================================== la vista de detalle

from test_panel import CLAVE, cliente, dentro  # noqa: E402
from test_panel import app, personas  # noqa: E402,F401  (fixtures)


def _texto(html: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html)).strip()


def _casillas_precio(html: str) -> list[tuple[int, str, str]]:
    """(n, clases, texto) de cada casilla de precio: filas por acabado y motor y matriz."""
    salida = []
    for n, resto in re.findall(r'class="seg-c seg-precio" data-n="(\d+)">(.*?)</span>\s*<span class="seg-c">', html, re.S):
        salida.append((int(n), resto, _texto(resto).removeprefix("Precio mediano").strip()))
    for n, resto in re.findall(r'<td data-etiqueta="[^"]*" data-n="(\d+)"[^>]*>(.*?)</td>', html, re.S):
        salida.append((int(n), resto, _texto(resto)))
    return salida


def test_el_modelo_enlaza_a_su_detalle(app, pg, datos, personas):
    _escenario(pg, datos)
    cl = dentro(app, "ventas")
    for tipo in ("VN", "VO"):
        html = cl.get(f"/mercado?zona=comunidad&tipo={tipo}").text
        enlaces = re.findall(r'<a class="enlace-modelo" href="([^"]+)">([^<]+)</a>', html)
        assert len(enlaces) >= 5
        for url, modelo in enlaces[:3]:
            assert url.replace("&amp;", "&").endswith(f"?tipo={tipo}&zona=comunidad")
            r = cl.get(url.replace("&amp;", "&"))
            assert r.status_code == 200 and f"<h1>{modelo}</h1>" in r.text and "Por acabado y motor" in r.text
            assert ("Precio por antigüedad y km" in r.text) == (tipo == "VO")
            assert "Precio publicado, no precio de venta. Los segmentos con pocos anuncios no son representativos." \
                in r.text
            assert 'aria-current="true">Andalucía' in r.text
    # Un modelo con espacios y punto en el nombre
    assert cl.get("/mercado/modelo/ID.%20Buzz?tipo=VN&zona=espana").status_code == 200
    # htmx: solo el bloque del detalle
    r = cl.get("/mercado/modelo/Golf?tipo=VO&zona=espana", headers={"HX-Request": "true"})
    assert r.text.lstrip().startswith('<div id="detalle-modelo"') and "<html" not in r.text
    # Sin sesión, al acceso
    assert cliente(app).get("/mercado/modelo/Golf").headers["location"] == "/acceso"


def test_n_menor_que_3_nunca_muestra_precio(app, pg, datos, personas):
    _escenario(pg, datos)
    cl = dentro(app, "tecnico")
    vistas = 0
    for tipo in ("VN", "VO"):
        for zona in ("provincia", "comunidad", "espana"):
            modelos = [m for (m,) in pg.execute(
                "SELECT DISTINCT modelo FROM panel_mercado_segmento WHERE tipo = %s AND zona = %s", (tipo, zona))]
            for modelo in modelos:
                html = cl.get(f"/mercado/modelo/{modelo}?tipo={tipo}&zona={zona}").text
                for n, crudo, texto in _casillas_precio(html):
                    vistas += 1
                    if n < 3:
                        assert "€" not in texto and texto.startswith("—"), (modelo, zona, n, texto)
                        assert "pocos anuncios" in texto or "sin anuncios" in texto
                    elif n < 5:
                        assert "€" in texto and 'class="atenuado"' in crudo and f"n = {n}" in texto, texto
                    else:
                        assert "€" in texto and "atenuado" not in crudo, texto
    assert vistas > 1000


def test_cifras_de_la_vista_cuadran_con_la_tabla(app, pg, datos, personas):
    _escenario(pg, datos)
    cl = dentro(app, "tecnico")
    dia = pg.execute("SELECT max(dia) FROM panel_mercado_segmento WHERE tipo = 'VO'").fetchone()[0]
    acabado, n, p25, p50, p75 = pg.execute(
        """SELECT acabado, anuncios, precio_p25_cents, precio_p50_cents, precio_p75_cents FROM panel_mercado_segmento
           WHERE dia = %s AND tipo = 'VO' AND zona = 'espana' AND modelo = 'T-Roc' AND nivel = 'acabado'
             AND n_precio >= 5 ORDER BY anuncios DESC LIMIT 1""", (dia,)).fetchone()
    html = cl.get("/mercado/modelo/T-Roc?tipo=VO&zona=espana").text
    fila = _texto(html.split(f'<span class="seg-flecha" aria-hidden="true"></span>{acabado}<', 1)[1].split("</summary>")[0])
    euros = lambda c: f"{round(c / 100):,}".replace(",", ".")  # noqa: E731
    assert f"Anuncios {n} " in fila and f"{euros(p50)} €" in fila and f"{euros(p25)}–{euros(p75)} €" in fila
    # Matriz con filtro de acabado: las casillas son las de ese acabado
    r = cl.get(f"/mercado/modelo/T-Roc?tipo=VO&zona=espana&acabado={acabado}").text
    assert f"Precio por antigüedad y km · {acabado}" in r
    assert re.search(rf'aria-current="true">{re.escape(acabado)}</a>', r)
    esperadas = {(a, k): n for a, k, n in pg.execute(
        """SELECT antiguedad_orden, km_orden, n_precio FROM panel_mercado_segmento WHERE dia = %s AND tipo = 'VO'
           AND zona = 'espana' AND modelo = 'T-Roc' AND nivel = 'acabado_antiguedad_km' AND acabado = %s""",
        (dia, acabado))}
    matriz = [int(n) for n in re.findall(r'<td data-etiqueta="[^"]*" data-n="(\d+)"', r)]
    assert len(matriz) == 12 and sum(matriz) == sum(esperadas.values())
    assert matriz == [esperadas.get((a, k), 0) for a in range(3) for k in range(4)]
    # Un acabado que no existe: «Todos»
    r = cl.get("/mercado/modelo/T-Roc?tipo=VO&zona=espana&acabado=Inventado").text
    assert re.search(r'aria-current="true">Todos</a>', r) and "Precio por antigüedad y km</h2>" in r


def test_nota_para_ver_espana_si_casi_todo_es_raya(app, pg, datos, personas):
    _escenario(pg, datos)
    cl = dentro(app, "tecnico")
    html = cl.get("/mercado/modelo/T-Roc?tipo=VO&zona=provincia").text
    casillas = _casillas_precio(html)
    assert sum(1 for n, *_ in casillas if n < 3) * 2 > len(casillas)
    assert "Ver T-Roc en España" in html and 'href="/mercado/modelo/T-Roc?tipo=VO&amp;zona=espana"' in html
    html = cl.get("/mercado/modelo/T-Roc?tipo=VO&zona=espana").text
    assert "Ver T-Roc en España" not in html


def test_modelo_sin_anuncios_y_sin_datos(app, pg, datos, personas):
    cl = dentro(app, "tecnico")
    assert "Todavía no hay datos de ocasión" in cl.get("/mercado/modelo/Golf?tipo=VO").text
    _escenario(pg, datos)
    html = cl.get("/mercado/modelo/Inventado?tipo=VO&zona=espana").text
    assert "Sin anuncios de Inventado en España" in html


def test_bloque_de_color_solo_con_dato(app, pg, datos, personas):
    capturar(datos, "vo", HOY.isoformat(), flota("1", 4))
    sv.cargar(pg, dia=HOY)
    am.precalcular(pg, hasta=HOY)
    cl = dentro(app, "tecnico")
    assert "Por color exterior" not in cl.get("/mercado/modelo/Golf?tipo=VO&zona=espana").text
    capturar(datos, "vo", (HOY + timedelta(days=1)).isoformat(), flota("1", 4) + [coche("ESP9", color="Gris Urano")])
    sv.cargar(pg, dia=HOY + timedelta(days=1))
    am.precalcular(pg, hasta=HOY + timedelta(days=1))
    html = cl.get("/mercado/modelo/Golf?tipo=VO&zona=espana").text
    assert "Por color exterior" in html and "Gris Urano" in html


def test_estado_de_las_fuentes_muestra_la_cobertura(app, pg, datos, personas):
    raro = "Volkswagen Golf Edición Inventada 1.5 TSI 110 kW (150 CV)"
    capturar(datos, "vo", HOY.isoformat(), [coche("ESP1", version="Volkswagen Golf Life 1.5 TSI 110 kW (150 CV)"),
                                            coche("ESP2", version=raro)])
    sv.cargar(pg, dia=HOY)
    am.precalcular(pg, hasta=HOY)
    html = dentro(app, "tecnico").get("/estado-fuentes").text
    assert "Acabados reconocidos en el texto de versión" in html
    assert "Ocasión (VO) · 50,0 % de 2 anuncios en España" in _texto(html)
    assert raro in html and "textos sin clasificar más frecuentes" in html
