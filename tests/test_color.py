"""Color de los anuncios: familias (config/colores.yaml) con nombres reales, carga de la exportación del
componente de fichas (solo añadir, sin tocar los hechos de los scrapers), unión con el stock (VIN distinto
anula el color, sin mirar al futuro), precálculo frente a SQL directo, cobertura y su nota en el detalle de
un modelo, filtro de color en la matriz de VO y «Estado de las fuentes».

Sin red: Postgres local, capturas sintéticas escritas por el código real de los scrapers y exportaciones de
colores sintéticas con nombres de color reales.
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import date, timedelta
from urllib.parse import quote

import psycopg
import pytest

from analitica import colores as ac
from analitica import mercado as am
from ingesta import colores as ic
from ingesta import scrapers_vw as sv
from ingesta.concesionario import cargar as cargar_concesionario
from sinteticos import RAIZ, capturar, coche, flota
from test_panel import dentro
from test_panel import app, personas  # noqa: F401  (fixtures)
from test_segmentos import ZONA_SQL, _casos, _clasificar, _escenario, _texto

HOY = date(2026, 11, 14)


@pytest.fixture(scope="module")
def paleta():
    return ac.cargar()


def exportar(base, lineas: list[dict]) -> None:
    """Exportación del componente de fichas con estas líneas (los campos que faltan, por defecto)."""
    ruta = base / "vw_details" / "exports" / "colors_latest.jsonl.gz"
    ruta.parent.mkdir(parents=True, exist_ok=True)
    filas = [{"scope": "x", "source_name": "vo", "vin": None, "status": "ok", "interior_color": "Negro Titanio",
              "interior_color_json": None, "color_json": None, "fetched_at": "2026-11-10T05:00:00+00:00",
              "source_snapshot_day": "2026-11-10", "present": True, **x} for x in lineas]
    ruta.write_bytes(gzip.compress("\n".join(json.dumps(f, ensure_ascii=False) for f in filas).encode()))


def vin(vid: str) -> str:
    """El VIN que pone sinteticos.coche a un anuncio."""
    return "WVWZZZ" + vid[-11:].rjust(11, "0")


# ================================================================== familias

REALES = {
    "Blanco Puro": ("Blanco", None), "Blanco puro": ("Blanco", None), "Blanco, y punto": ("Blanco", None),
    "Negro Noche (metalizado)": ("Negro", "metalizado"), "Negro Profundo efecto perla": ("Negro", "perla"),
    "Plata Réflex metalizado": ("Plata", "metalizado"), "Gris Ceniza metalizado": ("Gris", "metalizado"),
    "Gris Grisáceo (metalizado)": ("Gris", "metalizado"), "Gris Delfín (metalizado)": ("Gris", "metalizado"),
    "Azul Arrecife metalizado": ("Azul", "metalizado"), "Gris Indy metalizado": ("Gris", "metalizado"),
    "Pintura exterior metalizada": ("Sin especificar", "metalizado"),
}


@pytest.mark.parametrize("texto, esperado", REALES.items())
def test_familias_con_los_nombres_reales(paleta, texto, esperado):
    c = paleta.clasificar(texto)
    assert (c.familia, c.acabado) == esperado
    assert c.reconocida is (esperado[0] != "Sin especificar")


def test_familias_sin_mayusculas_ni_tildes_y_la_primera_palabra_gana(paleta):
    assert paleta.clasificar("BLANCO PURO").familia == paleta.clasificar("blanco puro").familia == "Blanco"
    assert paleta.clasificar("plata reflex metalizado").familia == "Plata"
    assert paleta.clasificar("Marrón Avellana").familia == paleta.clasificar("MARRON avellana").familia == "Marrón/beige"
    assert paleta.clasificar("Beige Arena").familia == "Marrón/beige"
    assert paleta.clasificar("Rojo Kings con techo negro").familia == "Rojo"     # el primero en el texto
    assert paleta.clasificar("Techo negro, carrocería roja").familia == "Negro"
    for texto in ("Verde Kiwi", "Amarillo Ascot", "Naranja Energía"):
        assert paleta.clasificar(texto).familia == texto.split()[0]
    assert paleta.clasificar("Violeta Lila").familia == paleta.clasificar("Morado Imperial").familia == "Otros"
    # Sin color reconocible: «sin especificar»; «mate» solo como palabra entera
    for texto in ("Pintura exterior metalizada", "Bicolor especial", "", "   "):
        assert paleta.clasificar(texto).familia == "Sin especificar"
    assert paleta.clasificar("Gris Mate").acabado == "mate"
    assert paleta.clasificar("Gris Matemático").acabado is None
    assert paleta.nombres[-1] == "Sin especificar" and paleta.nombres[0] == "Blanco"


def test_familias_de_la_configuracion(tmp_path):
    (tmp_path / "c.yaml").write_text("familias:\n  - {nombre: Claro, palabras: [blanc, plata]}\n"
                                     "sin_especificar: Otro\n", encoding="utf-8")
    p = ac.cargar(tmp_path / "c.yaml")
    assert p.clasificar("Plata Réflex").familia == "Claro" and p.clasificar("Negro").familia == "Otro"
    (tmp_path / "mal.yaml").write_text("familias:\n  - {nombre: A, palabras: [gris]}\n  - {nombre: B, palabras: [Gris]}\n",
                                       encoding="utf-8")
    with pytest.raises(ac.ErrorColores):
        ac.cargar(tmp_path / "mal.yaml")


# ================================================================== carga de la exportación

def _hechos(pg) -> tuple:
    return tuple(pg.execute(f"SELECT count(*), md5(string_agg(t::text, ',' ORDER BY t::text)) FROM {t} t").fetchone()
                 for t in ("stock_externo_snapshot", "eventos_externos", "anuncios_externos", "scraper_runs"))


def test_carga_solo_anade_y_no_toca_los_hechos_de_los_scrapers(pg, datos):
    capturar(datos, "vo", "2026-11-10", flota("1", 3))
    sv.cargar(pg, dia=date(2026, 11, 10))
    antes = _hechos(pg)
    lineas = [{"vehicle_id": "ESP1000000", "vin": vin("ESP1000000"), "exterior_color": "Gris Indy metalizado"},
              {"vehicle_id": "ESP1000001", "status": "pending", "exterior_color": None, "fetched_at": None},
              {"vehicle_id": "ESP1000002", "status": "listing_color", "exterior_color": "Blanco Puro"},
              {"vehicle_id": "X", "source_name": "otra"}]
    exportar(datos, lineas)
    r = ic.ejecutar(pg, date(2026, 11, 10))
    assert r["estado"] == "ok" and r["filas_nuevas"] == 3 and r["descartadas"] == {"fuente": 1}
    assert r["por_estado"] == {"ok": 1, "pending": 1, "listing_color": 1}
    # Tal cual: el texto exacto, el VIN y el estado
    assert pg.execute("SELECT fuente, id_anuncio, bastidor, color_exterior, color_interior, estado FROM color_anuncio "
                      "WHERE id_anuncio = 'ESP1000000'").fetchone() == \
        ("vo", "ESP1000000", vin("ESP1000000"), "Gris Indy metalizado", "Negro Titanio", "ok")
    # Otra vez el mismo fichero: nada nuevo. Un cambio de estado: una fila más (la anterior queda)
    assert ic.ejecutar(pg, date(2026, 11, 10))["filas_nuevas"] == 0
    lineas[1] = {"vehicle_id": "ESP1000001", "exterior_color": "Negro Noche (metalizado)",
                 "fetched_at": "2026-11-11T05:00:00+00:00"}
    exportar(datos, lineas)
    assert ic.ejecutar(pg, date(2026, 11, 11))["filas_nuevas"] == 1
    assert pg.execute("SELECT estado FROM color_anuncio WHERE id_anuncio = 'ESP1000001' ORDER BY id").fetchall() == \
        [("pending",), ("ok",)]
    # Las tablas de hechos de los scrapers, idénticas; ni un evento ni un cambio de anuncio
    assert _hechos(pg) == antes
    # Solo añadir
    with pytest.raises(psycopg.errors.RaiseException):
        pg.execute("UPDATE color_anuncio SET color_exterior = 'x'")
    with pytest.raises(psycopg.errors.RaiseException):
        pg.execute("DELETE FROM color_anuncio")
    anot = pg.execute("SELECT estado, detalle->>'filas_nuevas' FROM fuentes_estado WHERE fuente = 'colores' "
                      "AND tipo = 'carga' ORDER BY id").fetchall()
    assert anot == [("ok", "3"), ("ok", "0"), ("ok", "1")]


def test_sin_fichero_la_carga_del_stock_no_falla(pg_url, pg, datos, monkeypatch):
    capturar(datos, "vo", HOY.isoformat(), flota("1", 3))
    capturar(datos, "vn", HOY.isoformat(), flota("2", 3, fuente="vn"))
    capturar(datos, "vn_otros", HOY.isoformat(), flota("3", 3, fuente="vn_otros"))
    monkeypatch.setattr(sv, "hoy_madrid", lambda: HOY)
    monkeypatch.setattr(sv, "conectar", lambda **kw: psycopg.connect(pg_url, **kw))
    assert sv.main(["cargar"]) == 0           # la tarea de las 08:30, tal cual
    assert pg.execute("SELECT count(*) FROM stock_externo_snapshot").fetchone()[0] == 9
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE fuente = 'colores'").fetchone()
    assert estado == "sin_captura" and "No existe la exportación de colores" in detalle["error"]
    assert pg.execute("SELECT count(*) FROM color_anuncio").fetchone()[0] == 0
    # El resumen de Telegram de las 09:00 cuenta solo las cargas de los scrapers
    filas, errores, cargadas = sv.datos_resumen(pg, HOY)
    assert errores == {} and cargadas == 9


def test_fichero_ilegible_se_anota_y_no_lanza(pg, datos):
    ruta = datos / "vw_details" / "exports" / "colors_latest.jsonl.gz"
    ruta.parent.mkdir(parents=True)
    ruta.write_bytes(b"esto no es gzip")
    r = ic.ejecutar(pg, HOY)
    assert r["estado"] == "error" and "BadGzipFile" in r["error"]
    assert pg.execute("SELECT estado FROM fuentes_estado WHERE fuente = 'colores'").fetchone() == ("error",)
    # Una línea mala no tira el fichero entero
    exportar(datos, [{"vehicle_id": "ESP1"}])
    ruta.write_bytes(gzip.compress(gzip.decompress(ruta.read_bytes()) + b"\n{roto"))
    r = ic.ejecutar(pg, HOY)
    assert r["estado"] == "ok" and r["filas_nuevas"] == 1 and r["descartadas"] == {"json": 1}


# ================================================================== unión con el stock

def _colores_del_dia(pg, dia):
    h = am.Hechos(pg, cargar_concesionario())
    return {k[1]: (f.color, f.color_estado, f.color_vin_distinto) for k, f in h.filas(dia, "VO").items()}


def test_vin_distinto_anula_el_color(pg, datos):
    capturar(datos, "vo", HOY.isoformat(), [coche("ESPA1"), coche("ESPA2"), coche("ESPA3"), coche("ESPA4", color="Rojo Kings")])
    sv.cargar(pg, dia=HOY)
    exportar(datos, [
        {"vehicle_id": "ESPA1", "vin": vin("ESPA1"), "exterior_color": "Azul Arrecife metalizado"},    # mismo VIN
        {"vehicle_id": "ESPA2", "vin": "WVWZZZ99999999999", "exterior_color": "Blanco Puro"},           # otro coche
        {"vehicle_id": "ESPA3", "vin": None, "exterior_color": "Gris Ceniza metalizado"},                 # sin VIN: vale
        {"vehicle_id": "ESPA4", "vin": "WVWZZZ99999999999", "exterior_color": "Blanco Puro"},           # anula también el del scraper
    ])
    ic.ejecutar(pg, HOY)
    assert _colores_del_dia(pg, HOY) == {
        "ESPA1": ("Azul Arrecife metalizado", "ok", False), "ESPA2": (None, "ok", True),
        "ESPA3": ("Gris Ceniza metalizado", "ok", False), "ESPA4": (None, "ok", True)}
    am.precalcular(pg, hasta=HOY)
    assert pg.execute("SELECT color, anuncios FROM panel_mercado_segmento WHERE nivel = 'color' AND zona = 'espana' "
                      "ORDER BY 1").fetchall() == [("Azul", 1), ("Gris", 1)]
    assert pg.execute("SELECT estado, anuncios, con_color, bastidor_distinto FROM panel_mercado_color_fuente").fetchall() == \
        [("ok", 4, 2, 2)]


def test_sin_mirar_al_futuro_y_estados_sin_color(pg, datos):
    """El color de un día es el último sabido ese día (fetched_at en Madrid); solo ok y listing_color valen."""
    d1, d2 = date(2026, 11, 10), date(2026, 11, 11)
    for d in (d1, d2):
        capturar(datos, "vo", d.isoformat(), [coche("ESPB1"), coche("ESPB2"), coche("ESPB3")])
    sv.cargar(pg, dia=d2)
    exportar(datos, [
        {"vehicle_id": "ESPB1", "exterior_color": "Negro Noche (metalizado)", "fetched_at": "2026-11-10T23:30:00+00:00"},
        {"vehicle_id": "ESPB2", "exterior_color": "Plata Réflex metalizado", "fetched_at": "2026-11-10T21:30:00+00:00"},
        {"vehicle_id": "ESPB3", "status": "error", "exterior_color": "Blanco Puro"},
    ])
    ic.ejecutar(pg, d2)
    # 23:30 UTC del 10 es ya el 11 en Madrid (00:30); 21:30 UTC, todavía el 10
    assert _colores_del_dia(pg, d1) == {"ESPB1": (None, None, False), "ESPB2": ("Plata Réflex metalizado", "ok", False),
                                        "ESPB3": (None, "error", False)}
    assert _colores_del_dia(pg, d2)["ESPB1"] == ("Negro Noche (metalizado)", "ok", False)
    # Una ficha posterior en error no borra el color ya sabido
    exportar(datos, [{"vehicle_id": "ESPB2", "status": "error", "exterior_color": None,
                      "fetched_at": "2026-11-11T05:00:00+00:00"}])
    ic.ejecutar(pg, d2)
    assert _colores_del_dia(pg, d2)["ESPB2"] == ("Plata Réflex metalizado", "error", False)


# ================================================================== precálculo frente a SQL directo

SQL_COLOR = """
CREATE TEMP TABLE color_dia AS
SELECT s.id, CASE WHEN u.vin IS NOT NULL AND s.bastidor IS NOT NULL AND upper(u.vin) <> upper(s.bastidor) THEN NULL
                  ELSE coalesce(u.color_exterior, s.color_exterior) END AS texto
FROM stock_externo_snapshot s
LEFT JOIN (SELECT DISTINCT ON (fuente, id_anuncio) fuente, id_anuncio, bastidor AS vin, color_exterior
           FROM color_anuncio
           WHERE estado IN ('ok', 'listing_color') AND color_exterior IS NOT NULL
             AND (coalesce(fetched_at, captured_at) AT TIME ZONE 'Europe/Madrid')::date <= %(dia)s
           ORDER BY fuente, id_anuncio, (coalesce(fetched_at, captured_at) AT TIME ZONE 'Europe/Madrid')::date DESC,
                    id DESC) u USING (fuente, id_anuncio)
WHERE s.snapshot_day = %(dia)s
"""


def _familias(pg, paleta, dia):
    """Tabla temporal color_dia (id, texto) por SQL directo y familia (id → familia) con el clasificador."""
    pg.execute(SQL_COLOR, {"dia": dia})
    pg.execute("ALTER TABLE color_dia ADD COLUMN familia text")
    for i, texto in pg.execute("SELECT id, texto FROM color_dia WHERE texto IS NOT NULL").fetchall():
        pg.execute("UPDATE color_dia SET familia = %s WHERE id = %s", (paleta.clasificar(texto).familia, i))


def _escenario_color(pg, datos):
    conc = _escenario(pg, datos)    # escribe también la exportación de colores (60 % de los más antiguos)
    ic.ejecutar(pg, HOY)
    am.precalcular(pg, hasta=HOY, conc=conc)
    return conc


def test_familias_y_cobertura_cuadran_con_sql_directo(pg, datos, paleta):
    from analitica import versiones
    conc = _escenario_color(pg, datos)
    dia = pg.execute("SELECT max(dia) FROM panel_mercado_segmento WHERE tipo = 'VO'").fetchone()[0]
    _clasificar(pg, versiones.cargar("Volkswagen"), dia)
    _familias(pg, paleta, dia)
    caso_ant, caso_km = _casos(conc)
    p = {"dia": dia, "prov": conc.provincia_codigo, "ccaa": list(conc.provincias_comunidad)}
    comprobadas = 0
    niveles = {   # (acabado, combustible, motor, antigüedad, km), siempre con la familia
        "color": ("NULL", "NULL", "NULL", "NULL", "NULL"),
        "color_antiguedad_km": ("NULL", "NULL", "NULL", caso_ant, caso_km),
        "acabado_color_antiguedad_km": ("c.acabado", "NULL", "NULL", caso_ant, caso_km),
        "combustible_color_antiguedad_km": ("NULL", "c.comb", "NULL", caso_ant, caso_km),
        "motor_color_antiguedad_km": ("NULL", "c.comb", "c.motor", caso_ant, caso_km),
        "acabado_combustible_color_antiguedad_km": ("c.acabado", "c.comb", "NULL", caso_ant, caso_km),
        "acabado_motor_color_antiguedad_km": ("c.acabado", "c.comb", "c.motor", caso_ant, caso_km),
    }
    for zona in ("provincia", "comunidad", "espana"):
        for nivel, (a, co, mo, an, k) in niveles.items():
            directo = {tuple(f[:7]): tuple(None if x is None else int(x) for x in f[7:]) for f in pg.execute(
                f"""SELECT s.modelo, {a}, {co}, {mo}, {an}, {k}, x.familia, count(*), count(s.precio_cents),
                           round(percentile_cont(0.25) WITHIN GROUP (ORDER BY s.precio_cents)::numeric),
                           round(percentile_cont(0.5) WITHIN GROUP (ORDER BY s.precio_cents)::numeric),
                           round(percentile_cont(0.75) WITHIN GROUP (ORDER BY s.precio_cents)::numeric),
                           count(*) FILTER (WHERE s.es_propio)
                    FROM stock_externo_snapshot s JOIN clase c USING (id) JOIN color_dia x USING (id)
                    WHERE s.snapshot_day = %(dia)s AND s.tipo_stock = 'VO' AND {ZONA_SQL[zona]} AND x.familia IS NOT NULL
                      {"AND s.fecha_matriculacion IS NOT NULL AND s.km IS NOT NULL" if an != "NULL" else ""}
                    GROUP BY 1, 2, 3, 4, 5, 6, 7""", p)}
            precalculado = {tuple(f[:7]): f[7:] for f in pg.execute(
                """SELECT modelo, acabado, combustible, motor, antiguedad_orden, km_orden, color, anuncios, n_precio,
                          precio_p25_cents, precio_p50_cents, precio_p75_cents, propios
                   FROM panel_mercado_segmento WHERE dia = %s AND tipo = 'VO' AND zona = %s AND nivel = %s""",
                (dia, zona, nivel))}
            assert precalculado == directo, (zona, nivel)
            comprobadas += len(directo)
        # Cobertura: con_color de cada modelo
        directo = dict(pg.execute(
            f"""SELECT s.modelo, count(x.familia) FROM stock_externo_snapshot s JOIN color_dia x USING (id)
                WHERE s.snapshot_day = %(dia)s AND s.tipo_stock = 'VO' AND {ZONA_SQL[zona]} GROUP BY 1""", p).fetchall())
        assert dict(pg.execute("SELECT modelo, con_color FROM panel_mercado_segmento WHERE dia = %s AND tipo = 'VO' "
                               "AND zona = %s AND nivel = 'modelo'", (dia, zona)).fetchall()) == directo
    assert comprobadas > 300
    # Estado de las fuentes: por fuente y estado
    directo = {(e or "sin_ficha"): (n, c) for e, n, c in pg.execute(
        """SELECT u.estado, count(*), count(x.texto) FROM stock_externo_snapshot s JOIN color_dia x USING (id)
           LEFT JOIN (SELECT DISTINCT ON (fuente, id_anuncio) fuente, id_anuncio, estado FROM color_anuncio
                      WHERE (coalesce(fetched_at, captured_at) AT TIME ZONE 'Europe/Madrid')::date <= %(dia)s
                      ORDER BY fuente, id_anuncio, (coalesce(fetched_at, captured_at) AT TIME ZONE 'Europe/Madrid')::date DESC, id DESC
                     ) u USING (fuente, id_anuncio)
           WHERE s.snapshot_day = %(dia)s AND s.fuente = 'vo' GROUP BY 1""", p)}
    assert {e: (n, c) for e, n, c in pg.execute("SELECT estado, anuncios, con_color FROM panel_mercado_color_fuente "
                                                "WHERE dia = %s AND fuente = 'vo'", (dia,))} == directo
    # Sin el cruce completo: el color no se cruza con el acabado ni el motor fuera de la matriz
    niveles_color = {n for (n,) in pg.execute("SELECT DISTINCT nivel FROM panel_mercado_segmento WHERE color IS NOT NULL")}
    assert niveles_color == set(niveles)
    assert {n for (n,) in pg.execute("SELECT DISTINCT nivel FROM panel_mercado_segmento WHERE tipo = 'VN'")} == \
        {"modelo", "acabado", "motor", "color"}


# ================================================================== la vista: cobertura y nota

def _vo(pg, datos, coloreados: int, total: int, dia=HOY):
    """`total` anuncios de Golf en VO, los `coloreados` primeros con color (alternando dos familias)."""
    capturar(datos, "vo", dia.isoformat(), flota("1", total))
    sv.cargar(pg, dia=dia)
    nombres = ["Gris Indy metalizado", "Blanco Puro"]
    exportar(datos, [{"vehicle_id": f"ESP1{i:06d}", "exterior_color": nombres[i % 2]} for i in range(coloreados)])
    ic.ejecutar(pg, dia)
    am.precalcular(pg, hasta=dia)


def _bloque_color(html: str) -> str:
    return _texto(html.split('data-bloque="color">', 1)[1].split("</section>", 1)[0])


@pytest.mark.parametrize("coloreados, total, texto, nota", [
    (8, 9, "Color conocido en el 88 % de los anuncios", True),          # 88,9 %: hacia abajo, con nota
    (9, 10, "Color conocido en el 90 % de los anuncios.", False),       # 90 % justo: sin nota
    (10, 10, "Color conocido en el 100 % de los anuncios.", False),
    (0, 4, "Color conocido en el 0 % de los anuncios", True),
])
def test_cobertura_y_nota_del_bloque_de_color(app, pg, datos, personas, coloreados, total, texto, nota):
    _vo(pg, datos, coloreados, total)
    html = dentro(app, "tecnico").get("/mercado/modelo/Golf?tipo=VO&zona=espana").text
    bloque = _bloque_color(html)
    assert bloque.startswith("Por color " + texto)
    assert ("mientras se completa, predominan los publicados hace más tiempo" in bloque) is nota
    assert ("Color conocido en el X % de los anuncios; mientras se completa, predominan los publicados hace más tiempo"
            .replace("X", texto.split("el ")[1].split(" %")[0]) in bloque) is nota
    if coloreados:
        assert f"Gris Anuncios {(coloreados + 1) // 2}" in bloque and f"Blanco Anuncios {coloreados // 2}" in bloque
    else:
        assert "Ningún anuncio de Golf en España tiene todavía su color." in bloque


def test_bloque_de_color_en_vn_y_vo_con_las_reglas_de_n(app, pg, datos, personas, paleta):
    conc = _escenario_color(pg, datos)
    cl = dentro(app, "tecnico")
    dia = {t: pg.execute("SELECT max(dia) FROM panel_mercado_segmento WHERE tipo = %s", (t,)).fetchone()[0]
           for t in ("VN", "VO")}
    vistas = 0
    for tipo in ("VN", "VO"):
        for zona in ("provincia", "espana"):
            for modelo, anuncios, con_color in pg.execute(
                    "SELECT modelo, anuncios, con_color FROM panel_mercado_segmento WHERE dia = %s AND tipo = %s "
                    "AND zona = %s AND nivel = 'modelo'", (dia[tipo], tipo, zona)).fetchall():
                html = cl.get(f"/mercado/modelo/{quote(modelo)}?tipo={tipo}&zona={zona}").text
                bloque = html.split('data-bloque="color"', 1)[1].split("</section>", 1)[0]
                pct = con_color * 100 // anuncios
                assert f'data-cobertura="{pct}"' in bloque and f"Color conocido en el {pct} %" in bloque
                filas = re.findall(r'<div class="seg-fila seg-color"><span class="seg-nombre">([^<]+)</span>(.*?)</div>',
                                   bloque, re.S)
                esperadas = pg.execute(
                    "SELECT color, anuncios, n_precio FROM panel_mercado_segmento WHERE dia = %s AND tipo = %s AND zona = %s "
                    "AND modelo = %s AND nivel = 'color' ORDER BY color = 'Sin especificar', anuncios DESC, color COLLATE \"C\"",
                    (dia[tipo], tipo, zona, modelo)).fetchall()
                assert [f[0] for f in filas] == [e[0] for e in esperadas]
                assert sum(e[1] for e in esperadas) == con_color
                for (_, cifras), (_, n, n_precio) in zip(filas, esperadas):
                    t = _texto(cifras)
                    precio = t.split("Precio mediano", 1)[1].split("Días publicado")[0]
                    if n_precio < conc.n_minimo_mostrar:
                        assert "€" not in precio
                    elif n_precio < conc.n_minimo_medianas:
                        assert "€" in precio and f"n = {n_precio}" in precio
                    else:
                        assert "€" in precio and "–" in precio      # con su p25–p75
                    vistas += 1
    assert vistas > 50


# ================================================================== filtro de color en la matriz de VO

def _chips(html: str, grupo: str) -> list[tuple[str, str, bool]]:
    bloque = re.search(rf'data-filtro="{grupo}">(.*?)</div>', html, re.S)
    if not bloque:
        return []
    return [(et, url.replace("&amp;", "&"), bool(act)) for url, act, et in re.findall(
        r'<a class="chip-filtro" href="([^"]+)"[^>]*?( aria-current="true")?>([^<]+)</a>', bloque.group(1))]


def test_filtro_de_color_combinable_cuadra_con_sql_directo(app, pg, datos, personas, paleta):
    from analitica import versiones
    from panel.mercado import url_detalle
    conc = _escenario_color(pg, datos)
    dia = pg.execute("SELECT max(dia) FROM panel_mercado_segmento WHERE tipo = 'VO'").fetchone()[0]
    _clasificar(pg, versiones.cargar("Volkswagen"), dia)
    _familias(pg, paleta, dia)
    caso_ant, caso_km = _casos(conc)
    cl = dentro(app, "tecnico")
    combinaciones = 0
    for zona, modelo in (("espana", "T-Roc"), ("espana", "Golf")):
        p = {"dia": dia, "modelo": modelo, "prov": conc.provincia_codigo, "ccaa": list(conc.provincias_comunidad)}
        cuartetos = set(pg.execute(
            f"""SELECT DISTINCT c.acabado, c.comb, c.motor, x.familia FROM stock_externo_snapshot s JOIN clase c USING (id)
                JOIN color_dia x USING (id)
                WHERE s.snapshot_day = %(dia)s AND s.tipo_stock = 'VO' AND {ZONA_SQL[zona]} AND x.familia IS NOT NULL
                  AND s.modelo = %(modelo)s AND s.fecha_matriculacion IS NOT NULL AND s.km IS NOT NULL""", p).fetchall())
        familias = sorted({q[3] for q in cuartetos})
        assert len(familias) >= 3
        acabado = sorted({q[0] for q in cuartetos})[0]
        for a in (None, acabado):
            for col in familias:
                con = [q for q in cuartetos if a is None or q[0] == a]
                if not any(q[3] == col for q in con):
                    continue
                r = cl.get(url_detalle(modelo, "VO", zona, a, None, None, col))
                assert r.status_code == 200, (modelo, a, col, r.headers.get("location"))
                html = r.text
                combinaciones += 1
                assert f"Precio por antigüedad y km · {' · '.join(x for x in (a, col) if x)}</h2>" in html
                assert [e for e, _, act in _chips(html, "color") if act] == [col]
                assert {e for e, *_ in _chips(html, "color")[1:]} == {q[3] for q in con}
                # Al cambiar de acabado se conserva el color
                for et, url, _ in _chips(html, "acabado")[1:]:
                    assert ("color=" in url) == any(q[0] == et and q[3] == col for q in cuartetos), (et, url)
                directo = {(i, j): n for i, j, n in pg.execute(
                    f"""SELECT {caso_ant}, {caso_km}, count(s.precio_cents)
                        FROM stock_externo_snapshot s JOIN clase c USING (id) JOIN color_dia x USING (id)
                        WHERE s.snapshot_day = %(dia)s AND s.tipo_stock = 'VO' AND {ZONA_SQL[zona]}
                          AND s.modelo = %(modelo)s AND s.fecha_matriculacion IS NOT NULL AND s.km IS NOT NULL
                          AND x.familia = %(col)s AND (%(a)s::text IS NULL OR c.acabado = %(a)s)
                        GROUP BY 1, 2""", {**p, "col": col, "a": a})}
                vistas = [int(n) for n in re.findall(r'<td data-etiqueta="[^"]*" data-n="(\d+)"', html)]
                assert vistas == [directo.get((i, j), 0) for i in range(3) for j in range(4)], (modelo, a, col)
                # Cobertura de la matriz: con color / anuncios de la matriz sin el filtro de color
                n, con_c = pg.execute(
                    f"""SELECT count(*), count(x.familia) FROM stock_externo_snapshot s JOIN clase c USING (id)
                        JOIN color_dia x USING (id)
                        WHERE s.snapshot_day = %(dia)s AND s.tipo_stock = 'VO' AND {ZONA_SQL[zona]}
                          AND s.modelo = %(modelo)s AND s.fecha_matriculacion IS NOT NULL AND s.km IS NOT NULL
                          AND (%(a)s::text IS NULL OR c.acabado = %(a)s)""", {**p, "a": a}).fetchone()
                matriz = html.split('id="t-matriz"', 1)[1].split("</section>", 1)[0]
                assert f"Color conocido en el {con_c * 100 // n} % de los anuncios" in _texto(matriz)
    assert combinaciones >= 6
    # Un color que no existe se descarta; en VN (sin matriz) se conserva si el modelo lo tiene
    base = "/mercado/modelo/T-Roc?tipo=VO&zona=espana"
    r = cl.get(base + "&color=Fucsia")
    assert r.status_code == 303 and r.headers["location"] == base
    col = pg.execute("SELECT color FROM panel_mercado_segmento WHERE tipo = 'VO' AND zona = 'espana' AND modelo = 'T-Roc' "
                     "AND nivel = 'color_antiguedad_km' AND dia = %s ORDER BY anuncios DESC LIMIT 1", (dia,)).fetchone()[0]
    html = cl.get(url_detalle("T-Roc", "VO", "espana", None, None, None, col)).text
    tipos = {et: url.replace("&amp;", "&") for url, et in
             re.findall(r'<a class="segmento" href="([^"]+)"[^>]*>([^<]+)</a>', html)}
    tiene_vn = pg.execute("SELECT count(*) FROM panel_mercado_segmento WHERE tipo = 'VN' AND zona = 'espana' "
                          "AND modelo = 'T-Roc' AND nivel = 'color' AND color = %s AND dia = (SELECT max(dia) FROM "
                          "panel_mercado_dia WHERE tipo = 'VN')", (col,)).fetchone()[0]
    assert ("color=" in tipos["Nuevos (VN)"]) == bool(tiene_vn)


# ================================================================== «Estado de las fuentes»

def test_estado_de_las_fuentes_con_color(app, pg, datos, personas):
    capturar(datos, "vo", HOY.isoformat(), flota("1", 6))
    sv.cargar(pg, dia=HOY)
    sin_familia = [f"Tono raro {i}" for i in range(22)]
    exportar(datos, [{"vehicle_id": "ESP1000000", "exterior_color": "Pintura exterior metalizada"},
                     {"vehicle_id": "ESP1000001", "exterior_color": "Blanco Puro"},
                     {"vehicle_id": "ESP1000002", "status": "pending", "exterior_color": None},
                     {"vehicle_id": "ESP1000003", "status": "empty", "exterior_color": None},
                     {"vehicle_id": "ESP1000004", "vin": "WVWZZZ99999999999", "exterior_color": "Negro"}])
    ic.ejecutar(pg, HOY)
    # Más anuncios con nombres sin familia (otro día, para los 20 más frecuentes)
    otro = HOY + timedelta(days=1)
    capturar(datos, "vo", otro.isoformat(), flota("1", 6) + [coche(f"ESPR{i:03d}{j}") for i in range(22)
                                                             for j in range(1 + (i < 2))])
    sv.cargar(pg, dia=otro)
    exportar(datos, [{"vehicle_id": f"ESPR{i:03d}{j}", "exterior_color": sin_familia[i], "fetched_at": f"{otro}T05:00:00+00:00"}
                     for i in range(22) for j in range(1 + (i < 2))])
    ic.ejecutar(pg, otro)
    am.precalcular(pg, hasta=HOY)
    html = dentro(app, "tecnico").get("/estado-fuentes").text
    t = _texto(html)
    assert "Color de los anuncios" in t and "Ocasión · 33,3 % con color (2 de 6 anuncios)" in t
    filas = {e: _texto(x) for e, x in re.findall(r'<tr data-estado="(\w+)">(.*?)</tr>', html, re.S)}
    assert filas["ok"] == "Con ficha leída 3 2 67 % 1"            # 3 con ficha: 2 con color, 1 con VIN distinto
    assert filas["pending"] == "Pendiente 1 0 0 % 0" and filas["empty"] == "Ficha sin color 1 0 0 % 0"
    assert filas["sin_ficha"] == "Sin ficha en la exportación 1 0 0 % 0"
    assert "Pintura exterior metalizada" in html and "nombres de color sin familia más frecuentes" in html
    am.precalcular(pg, hasta=otro)
    html = dentro(app, "tecnico").get("/estado-fuentes").text
    textos = re.findall(r'<td class="izq detalle">(Tono raro \d+|Pintura exterior metalizada)</td>', html)
    assert len(textos) == 20 and textos[:2] == ["Tono raro 0", "Tono raro 1"]


# ================================================================== migración e infraestructura

def test_migracion_0011_ida_y_vuelta(pg_url, pg, datos):
    from alembic import command
    from alembic.config import Config
    cfg = Config(str(RAIZ / "alembic.ini"))
    _escenario_color(pg, datos)
    assert pg.execute("SELECT count(*) FROM panel_mercado_segmento WHERE nivel LIKE '%color%'").fetchone()[0] > 0
    sin_color = pg.execute("SELECT count(*) FROM panel_mercado_segmento WHERE nivel NOT LIKE '%color%'").fetchone()
    command.downgrade(cfg, "0010")
    assert pg.execute("SELECT count(*) FROM panel_mercado_segmento").fetchone() == sin_color
    assert pg.execute("SELECT to_regclass('color_anuncio')").fetchone() == (None,)
    command.upgrade(cfg, "head")
    with pytest.raises(psycopg.errors.CheckViolation):    # color solo en los niveles de color
        pg.execute("""INSERT INTO panel_mercado_segmento (dia, tipo, zona, nivel, modelo, color, anuncios, propios, n_precio)
                      VALUES ('2026-11-01', 'VO', 'espana', 'modelo', 'Golf', 'Gris', 1, 0, 0)""")
    with pytest.raises(psycopg.errors.CheckViolation):    # con_color no pasa de los anuncios
        pg.execute("""INSERT INTO panel_mercado_segmento (dia, tipo, zona, nivel, modelo, anuncios, propios, n_precio, con_color)
                      VALUES ('2026-11-01', 'VO', 'espana', 'modelo', 'Golf', 1, 0, 0, 2)""")


def test_el_panel_no_lee_color_anuncio_y_si_sus_derivadas(panel_url):
    with psycopg.connect(panel_url, autocommit=True) as c:
        for t in ("panel_mercado_color_fuente", "panel_mercado_color_sin_familia"):
            c.execute(f"SELECT count(*) FROM {t}")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("SELECT count(*) FROM color_anuncio")


def test_la_exportacion_se_monta_en_solo_lectura():
    compose = (RAIZ / "infra" / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- /opt/cdf/data/vw_details/exports:/datos/scrapers/vw_details/exports:ro" in compose
    assert "colors.sqlite3:" not in compose and "/opt/cdf/data/vw_details:" not in compose
