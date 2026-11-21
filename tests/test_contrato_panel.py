"""Contrato de datos del panel (docs/contrato_panel.md) y vistas Dirección, Ventas y Marketing.

Sin red. Las vistas se prueban con el TestClient conectado como cdf_panel, con los datos de ejemplo
de panel/ejemplo.py y, para los bloques «reales», con esas mismas filas copiadas con origen 'real'.
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from decimal import Decimal

import psycopg
import pytest
from psycopg import sql

from ingesta.concesionario import cargar as cargar_concesionario
from panel import contrato, ejemplo, permisos, usuarios
from panel.app import crear_app
from panel.permisos import ROLES
from sinteticos import RAIZ
from test_panel import CLAVE, FICTICIO, HOY, Enlaces, _con_datos, dentro

VISTAS = {"direccion": "/direccion", "ventas": "/ventas", "marketing": "/marketing"}
MATRIZ = {  # escrita a mano: qué vistas de datos permite cada rol
    "direccion": {"direccion", "ventas", "marketing"},
    "ventas": {"ventas"},
    "marketing": {"marketing"},
    "tecnico": {"direccion", "ventas", "marketing"},
}
ETIQUETA = '<span class="etiqueta-ejemplo">Datos de ejemplo</span>'
BANDA = 'class="banda-ejemplo"'
PENDIENTE = "Se activa al conectar sus datos."


@pytest.fixture
def personas(pg):
    for rol in ROLES:
        usuarios.alta(pg, rol, f"Persona {rol}", rol, CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    return list(ROLES)


@pytest.fixture
def conc():
    return cargar_concesionario()


@pytest.fixture
def con_ejemplo(pg, conc):
    ejemplo.cargar(pg, HOY, conc)
    return conc


def app_con(panel_url, visible=None):
    return crear_app(dsn=panel_url, hoy=lambda: HOY, ejemplo_para=visible)


def bloques(html: str) -> dict[str, tuple[str, str]]:
    """{nombre del bloque: (origen, html del bloque hasta el siguiente bloque)}."""
    partes = re.split(r'(?=<[a-z]+ [^>]*data-bloque=")', html)
    salida = {}
    for p in partes:
        m = re.match(r'<[a-z]+ [^>]*data-bloque="([^"]+)" data-origen="([^"]*)"', p)
        if m:
            salida[m.group(1)] = (m.group(2), p)
    return salida


def a_real(pg, *tablas):
    """Las filas de ejemplo de estas tablas, copiadas como reales (lo que hará la fuente al conectarse)."""
    for t in tablas:
        cols = [c for (c,) in pg.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = %s AND column_name <> 'origen' "
            "ORDER BY ordinal_position", (t,))]
        lista = sql.SQL(", ").join(map(sql.Identifier, cols))
        pg.execute(sql.SQL("INSERT INTO {t} ({c}, origen) SELECT {c}, 'real' FROM {t} WHERE origen = 'ejemplo'")
                   .format(t=sql.Identifier(t), c=lista))


# ================================================================== el contrato en la base

def _columnas(pg, tabla):
    return {c: t for c, t in pg.execute("SELECT column_name, data_type FROM information_schema.columns "
                                       "WHERE table_name = %s", (tabla,))}


def test_todas_las_tablas_llevan_dia_origen_y_calculado_at(pg):
    for t in contrato.TABLAS:
        cols = _columnas(pg, t)
        assert cols["dia"] == "date" and cols["origen"] == "text", t
        assert cols["calculado_at"].startswith("timestamp"), t
        checks = " ".join(d for (d,) in pg.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid = %s::regclass", (t,)))
        assert "origen = ANY (ARRAY['real'::text, 'ejemplo'::text])" in checks, t
        assert re.search(r"(PRIMARY KEY|UNIQUE NULLS NOT DISTINCT) \(origen, dia", checks), t
    # El origen forma parte de la clave: una fila real y una de ejemplo del mismo día conviven
    pg.execute("INSERT INTO panel_recomendacion_modelo (dia, origen, tipo, modelo, accion, motivo, modelo_version, n) "
               "VALUES (%s, 'ejemplo', 'VN', 'Golf', 'mantener', 'x', 'v', 1), "
               "(%s, 'real', 'VN', 'Golf', 'mantener', 'x', 'v', 1)",
               (HOY, HOY))


def test_toda_salida_de_modelo_lleva_intervalo_version_y_n(pg):
    for t in contrato.TABLAS:
        cols = set(_columnas(pg, t))
        if "modelo_version" not in cols:
            continue
        assert "n" in cols, t
        terminadas = {c[:-4] for c in cols if c.endswith("_p50")}
        if t != "panel_recomendacion_modelo":          # la recomendación es una regla, no una cifra
            assert terminadas, t
        for base in terminadas:
            assert {f"{base}_p10", f"{base}_p90"} <= cols, (t, base)
    # p10 ≤ p50 ≤ p90 lo garantiza la base
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO panel_ventas_mes (dia, origen, tipo, dia_mes, previsto_p10, previsto_p50, previsto_p90, "
                   "modelo_version, n) VALUES (%s, 'ejemplo', 'VN', 1, 5, 4, 6, 'v', 1)", (HOY,))


PERSONALES = re.compile(r"(^|_)(nombre|apellido|telefono|tel|movil|correo|email|mail|dni|nif|nie|cliente|"
                        r"client|contacto|persona|direccion_postal|lead_id|vin)($|_)")


def test_ninguna_columna_con_datos_personales(pg):
    tablas = [t for (t,) in pg.execute("SELECT table_name FROM information_schema.tables "
                                       "WHERE table_name LIKE 'panel\\_%' AND table_schema = 'public'")]
    assert set(contrato.TABLAS) <= set(tablas)
    for t in tablas:
        if t == "panel_usuarios":   # usuario, nombre visible y rol de quien usa el panel (sesión 1)
            continue
        for c in _columnas(pg, t):
            if (t, c) == ("panel_mercado_dia", "zona_nombre"):   # nombre de la provincia o comunidad
                continue
            assert not PERSONALES.search(c), (t, c)


def test_datos_de_ejemplo_sin_nada_personal(pg, con_ejemplo):
    correo = re.compile(r"[\w.+-]+@[\w-]+\.\w+")
    telefono = re.compile(r"(\+34)?\s?[6789]\d{2}\s?\d{3}\s?\d{3}\b")
    for t in contrato.TABLAS:
        for fila in pg.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(t))):
            texto = " | ".join(v for v in fila if isinstance(v, str))   # los textos (los números no)
            assert not correo.search(texto) and not telefono.search(texto), (t, texto[:120])


def test_cdf_panel_solo_lee_las_tablas_del_contrato(panel_url):
    with psycopg.connect(panel_url, autocommit=True) as c:
        for t in contrato.TABLAS:
            c.execute(sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t)))
            for orden in ("DELETE FROM {}", "TRUNCATE {}"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(sql.SQL(orden).format(sql.Identifier(t)))
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(sql.SQL("INSERT INTO {} (dia, origen) VALUES (now(), 'real')").format(sql.Identifier(t)))


# ================================================================== el origen de cada bloque

def test_origen_de_un_bloque():
    M = contrato.Modo
    modos = {t: M(False, False) for t in contrato.TABLAS + contrato.MOTOR}
    assert contrato.estados(modos) == {"direccion": "vacia", "ventas": "vacia", "marketing": "vacia"}
    modos = {t: M(False, True) for t in contrato.TABLAS + contrato.MOTOR}
    assert set(contrato.estados(modos).values()) == {"ejemplo"}
    # Una fila real en una tabla: los bloques que la leen pasan a real (también su otra tabla)
    modos["panel_leads_dia"] = M(True, True)
    o = contrato.origenes(modos, "marketing")
    assert o["kpis"] == o["campanas"] == "real" and o["recomendacion"] == "ejemplo"
    assert contrato.estado_vista(modos, "marketing") == "ejemplo"       # quedan bloques de ejemplo
    for t in contrato.TABLAS + contrato.MOTOR:
        modos[t] = M(True, True)
    assert set(contrato.estados(modos).values()) == {"real"}
    # El motor de alertas evaluado con datos reales: los bloques de alertas pasan a real
    modos = {t: M(False, True) for t in contrato.TABLAS + contrato.MOTOR}
    modos["alertas_evaluacion"] = M(True, False)
    assert {v: contrato.origenes(modos, v)["alertas"] for v in contrato.VISTAS} == {
        "direccion": "real", "ventas": "real", "marketing": "real"}


# ================================================================== datos de ejemplo

def test_ejemplo_determinista_y_coherente_entre_vistas(conc):
    a, b = ejemplo.generar(HOY, conc), ejemplo.generar(HOY, conc)
    assert a == b
    assert all(a[t] for t in contrato.TABLAS)
    riesgo = {f["stock_id"]: f for f in a["panel_riesgo_vehiculo"]}
    stock_hoy = {f["stock_id"] for f in a["panel_stock_vehiculo"] if f["dia"] == HOY}
    assert set(riesgo) == stock_hoy                                    # Dirección y Ventas, el mismo stock
    campanas = {f["campana_id"] for f in a["panel_campana_dia"]}
    for al in a["panel_alertas"]:                                      # las alertas citan lo que existe
        if al["stock_id"]:
            assert al["stock_id"] in riesgo and al["stock_id"] in al["texto"]
        if al["campana_id"]:
            assert al["campana_id"] in campanas
    assert any(al["stock_id"] for al in a["panel_alertas"]) and any(al["campana_id"] for al in a["panel_alertas"])
    assert {f["campana_id"] for f in a["panel_campana_salud"]} <= campanas
    # Leads de Ventas = suma de los de Marketing del mismo modelo y tipo
    for v in riesgo.values():
        suma = sum(f["leads"] for f in a["panel_leads_dia"] if f["modelo"] == v["modelo"] and f["tipo"] == v["tipo"]
                   and HOY - timedelta(days=14) <= f["dia"] < HOY)
        assert v["leads_14"] == suma
    # Modelos de la marca configurada
    modelos = set(conc.modelos_vn) | set(conc.modelos_vo)
    for t in ("panel_stock_vehiculo", "panel_riesgo_vehiculo", "panel_leads_dia", "panel_recomendacion_modelo",
              "panel_atencion_relativa"):
        assert {f["modelo"] for f in a[t]} <= modelos, t
    # Veredictos de las campañas pasadas, coherentes con su intervalo
    for f in a["panel_campana_resultado"]:
        assert f["veredicto_ventas"] == ejemplo._veredicto(f["ventas"], f["ventas_sin_p10"], f["ventas_sin_p90"])
    # Seis semanas, desde el lunes siguiente
    for tipo in ("todo", "VN", "VO"):
        semanas = [f["semana"] for f in a["panel_semana_campana"] if f["tipo"] == tipo]
        assert len(semanas) == 6 and all(s.weekday() == 0 for s in semanas) and semanas[0] > HOY, tipo
    # Ámbitos: campañas de VN, de VO y una general con leads de los dos tipos
    ambito = {f["campana_id"]: f["ambito"] for f in a["panel_campana_dia"]}
    assert set(ambito.values()) == {"VN", "VO", "general"}
    tipos_de = {}
    for f in a["panel_leads_dia"]:
        if f["campana_id"]:
            tipos_de.setdefault(f["campana_id"], set()).add(f["tipo"])
    for cid, tipos in tipos_de.items():
        assert tipos == ({"VN", "VO"} if ambito[cid] == "general" else {ambito[cid]}), cid
    assert {f["tipo"] for f in a["panel_recomendacion_modelo"]} == {"VN", "VO"}
    # Campañas pasadas: una fila «todo» por campaña; las generales, además VN y VO, que suman «todo»
    res = {(f["campana_id"], f["tipo"]): f for f in a["panel_campana_resultado"]}
    for (cid, tipo), f in res.items():
        if f["ambito"] == "general" and tipo == "todo":
            for col in ("leads", "ventas", "leads_sin_p50", "ventas_sin_p50"):
                assert f[col] == res[(cid, "VN")][col] + res[(cid, "VO")][col], (cid, col)
        assert (cid, "todo") in res
    assert {f["ambito"] for f in a["panel_campana_resultado"]} == {"VN", "VO", "general"}


def _huella(pg, tabla):
    return pg.execute(sql.SQL("SELECT count(*), coalesce(md5(string_agg(t::text, '|' ORDER BY t::text)), '') "
                              "FROM {} t").format(sql.Identifier(tabla))).fetchone()


def test_ejemplo_no_toca_hechos_mercado_ni_filas_reales(pg, datos, conc):
    _con_datos(pg, datos, conc)
    pg.execute("INSERT INTO panel_alertas (dia, origen, alerta_id, regla, tema, destinatarios, nivel, texto, "
               "abierta_desde) VALUES (%s, 'real', 'R1', 'r', 'datos', '{tecnico}', 'aviso', 'real', %s)", (HOY, HOY))
    otras = [t for (t,) in pg.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                                      "AND table_type = 'BASE TABLE'") if t not in contrato.TABLAS]
    assert {"stock_externo_snapshot", "scraper_runs", "panel_mercado_dia", "panel_mercado_modelo"} <= set(otras)
    antes = {t: _huella(pg, t) for t in otras}
    assert antes["stock_externo_snapshot"][0] > 0 and antes["panel_mercado_modelo"][0] > 0
    ejemplo.cargar(pg, HOY, conc)
    ejemplo.cargar(pg, HOY, conc)                                      # dos veces: sustituye, no duplica
    n = pg.execute("SELECT count(*) FROM panel_riesgo_vehiculo WHERE origen = 'ejemplo'").fetchone()[0]
    assert n == len(ejemplo.generar(HOY, conc)["panel_riesgo_vehiculo"])
    ejemplo.borrar(pg)
    assert {t: _huella(pg, t) for t in otras} == antes
    for t in contrato.TABLAS:
        assert pg.execute(sql.SQL("SELECT count(*) FROM {} WHERE origen = 'ejemplo'").format(sql.Identifier(t))
                          ).fetchone()[0] == 0
    assert pg.execute("SELECT texto FROM panel_alertas WHERE origen = 'real'").fetchall() == [("real",)]


def test_orden_de_ejemplo_se_niega_sin_permiso(pg_url, pg, capsys):
    for entorno in ({}, {"PANEL_PERMITE_EJEMPLO": "0"}, {"PANEL_PERMITE_EJEMPLO": "si"}):
        for orden in ("cargar", "borrar"):
            assert ejemplo.main([orden, "--dia", HOY.isoformat()], entorno=entorno) == 1
            assert "PANEL_PERMITE_EJEMPLO=1" in capsys.readouterr().err
    assert pg.execute("SELECT count(*) FROM panel_riesgo_vehiculo").fetchone()[0] == 0
    assert ejemplo.main(["cargar", "--dia", HOY.isoformat()], entorno={"PANEL_PERMITE_EJEMPLO": "1"}) == 0
    assert pg.execute("SELECT count(*) FROM panel_riesgo_vehiculo WHERE origen = 'ejemplo'").fetchone()[0] > 0
    assert ejemplo.main(["borrar"], entorno={"PANEL_PERMITE_EJEMPLO": "1"}) == 0
    assert pg.execute("SELECT count(*) FROM panel_riesgo_vehiculo").fetchone()[0] == 0


# ================================================================== vistas con datos de ejemplo

def test_cada_bloque_de_ejemplo_lleva_su_etiqueta_y_la_vista_la_banda(pg, datos, panel_url, personas, conc):
    _con_datos(pg, datos, conc)                                        # mercado real para la tarjeta de Dirección
    ejemplo.cargar(pg, HOY, conc)
    cl = dentro(app_con(panel_url), "tecnico")
    for vista, ruta in VISTAS.items():
        for url in (ruta, ruta + "?tipo=VO", ruta + ("?tipo=VN" if vista != "marketing" else "?tipo=VN&orden=gasto")):
            html = cl.get(url).text
            assert BANDA in html, url
            b = bloques(html)
            # Marketing: «Atención frente a sus rivales» en Todo y VN; en VO, en su lugar, el stock de ocasión
            esperados = set(contrato.BLOQUES[vista]) - ({"atencion"} if "VO" in url else {"ocasion"}
                                                        if vista == "marketing" else set())
            assert esperados <= set(b), (url, esperados - set(b))
            if vista == "marketing":
                assert ("ocasion" in b) == ("VO" in url) and ("atencion" in b) != ("VO" in url), url
            for nombre in esperados:
                origen, trozo = b[nombre]
                assert origen == "ejemplo" and ETIQUETA in trozo, (url, nombre)
                assert "Sin datos todavía" not in trozo, (url, nombre)
            if vista == "direccion":
                origen, trozo = b["mercado"]
                assert origen == "real" and ETIQUETA not in trozo and "El mercado en Sevilla" in trozo
                assert "Ver el mercado" in trozo and "Sin datos todavía" not in trozo
    # htmx: solo la vista, sin la página
    r = cl.get("/ventas?tipo=VN&riesgo=1", headers={"HX-Request": "true"})
    assert r.text.lstrip().startswith('<div id="ventas"') and "<html" not in r.text


def test_vistas_sin_terceros_ni_codigo_en_linea(pg, panel_url, personas, con_ejemplo):
    cl = dentro(app_con(panel_url), "tecnico")
    for ruta in VISTAS.values():
        r = cl.get(ruta)
        p = Enlaces()
        p.feed(r.text)
        assert [u for u in p.urls if not u.startswith("/") or u.startswith("//")] == [], ruta
        assert p.scripts_en_linea == 0 and p.estilos == 0 and p.eventos == 0, ruta
        assert "http://" not in r.text and "https://" not in r.text


def test_direccion_cifras(pg, panel_url, personas, con_ejemplo):
    filas = ejemplo.generar(HOY, con_ejemplo)
    cl = dentro(app_con(panel_url), "tecnico")
    b = bloques(cl.get("/direccion").text)
    stock_hoy = [f for f in filas["panel_stock_vehiculo"] if f["dia"] == HOY]
    n_vn = sum(f["tipo"] == "VN" for f in stock_hoy)
    assert f'<span class="kpi-valor">{len(stock_hoy)}</span>' in b["stock"][1]
    assert f"VN {n_vn} · VO {len(stock_hoy) - n_vn}" in b["stock"][1] and "frente a octubre" in b["stock"][1]
    media = round(sum(f["dias_exposicion"] for f in stock_hoy) / len(stock_hoy))
    assert f"{media} días" in b["antiguedad"][1] and "Objetivo: 60 días" in b["antiguedad"][1]
    alertas = filas["panel_alertas"]
    assert f'<span class="kpi-valor">{len(alertas)}</span>' in b["alertas_kpi"][1]
    nuevas = sum(a["abierta_desde"] == HOY for a in alertas)
    assert nuevas > 1 and f"{nuevas} nuevas hoy" in b["alertas_kpi"][1]
    for a in alertas:
        assert a["texto"] in b["alertas"][1].replace("&#39;", "'")
    assert "para dirección y jefe de ventas" in b["alertas"][1] and "Campañas · para marketing" in b["alertas"][1]
    # Tramos de días en exposición: el de más de 90, en ámbar
    mas90 = sum(f["dias_exposicion"] > 90 for f in stock_hoy)
    assert re.search(rf'Más de 90</span>.*?relleno viejo.*?<span class="tramo-n">{mas90}</span>', b["edad"][1], re.S)
    # Ventas: el último real frente a su banda
    hoy_todo = next(f for f in filas["panel_ventas_mes"] if f["tipo"] == "todo" and f["dia_mes"] == HOY.day)
    assert f'<span class="kpi-valor">{hoy_todo["ventas_acumuladas"]}</span>' in b["ventas_kpi"][1]
    assert "Lo normal: 8 de cada 10 meses caen aquí" in b["ventas_mes"][1] and "data-ventas=" in b["ventas_mes"][1]
    # Selector VN: solo stock VN y solo alertas de VN o de los dos
    b = bloques(cl.get("/direccion?tipo=VN").text)
    assert f'<span class="kpi-valor">{n_vn}</span>' in b["stock"][1] and "Stock VN" in b["stock"][1]
    assert "Ventas VN del mes" in b["ventas_kpi"][1]
    vn = [a for a in alertas if a["tipo"] in (None, "VN")]
    assert f'<span class="kpi-valor">{len(vn)}</span>' in b["alertas_kpi"][1]


def test_direccion_tarjeta_de_mercado_sin_datos(pg, panel_url, personas, con_ejemplo):
    b = bloques(dentro(app_con(panel_url), "tecnico").get("/direccion").text)
    assert "Sin datos todavía" in b["mercado"][1] and ETIQUETA not in b["mercado"][1]


def test_ventas_orden_filtros_y_detalle(pg, panel_url, personas, con_ejemplo):
    filas = ejemplo.generar(HOY, con_ejemplo)["panel_riesgo_vehiculo"]
    cl = dentro(app_con(panel_url), "tecnico")
    html = cl.get("/ventas").text
    ids = re.findall(r'class="vehiculo-detalle">[^<]* · (EJ\d+)</span>', html)
    orden = [f["stock_id"] for f in sorted(filas, key=lambda f: (f["p_venta90_p50"], -f["dias_exposicion"], f["stock_id"]))]
    assert ids == orden[:25]                                           # de más a menos riesgo, 25 primeros
    assert '<span class="nota recuento">25 de 142</span>' in html
    assert re.search(r'<a class="boton-mas" href="/ventas\?tipo=todo&amp;v=EJ\d+&amp;n=50" hx-get="[^"]+" '
                     r'hx-target="#ventas"[^>]*>Ver 25 más</a>', html)
    html_todo = cl.get("/ventas?n=200").text
    assert re.findall(r'class="vehiculo-detalle">[^<]* · (EJ\d+)</span>', html_todo) == orden
    assert "142 de 142" in html_todo and "boton-mas" not in html_todo
    assert "Ver 17 más" in cl.get("/ventas?n=125").text                  # el último tramo, con lo que queda
    b = bloques(html)
    menos50 = sum(f["p_venta90_p50"] < 0.5 for f in filas)
    assert f'<span class="resumen-valor">{menos50}</span><span class="resumen-texto">con menos del 50 %' in b["resumen"][1]
    # El primero, elegido por defecto, con su intervalo, n y curva
    primero = next(f for f in filas if f["stock_id"] == orden[0])
    det = b["detalle"][1]
    assert primero["stock_id"] in det and "data-curva=" in det
    assert f"{round(primero['p_venta90_p10'] * 100)}–{round(primero['p_venta90_p90'] * 100)} %" in det
    assert f"Leads de {primero['modelo']} en 14 días: {primero['leads_14']}" in det
    # Botones activos (sesión 3): formularios POST con CSRF; su circuito se prueba en test_acciones.py
    assert re.search(r'<button type="submit" class="boton-accion" data-accion="campana" aria-[^>]*>Pedir campaña a marketing</button>', det)
    assert re.search(r'<button type="submit" class="boton-accion secundario" data-accion="revisar_precio" aria-[^>]*>Revisar precio</button>', det)
    assert det.count('action="/ventas/accion"') == 2 and det.count('name="csrf"') == 2
    # Filtros: tipo y «solo menos del 50 %»
    html = cl.get("/ventas?tipo=VO&riesgo=1").text
    ids = re.findall(r'class="vehiculo-detalle">[^<]* · (EJ\d+)</span>', html)
    assert ids and set(ids) == {f["stock_id"] for f in filas if f["tipo"] == "VO" and f["p_venta90_p50"] < 0.5}
    vn = [f for f in filas if f["tipo"] == "VN"]                       # el filtro se aplica a todos, no a los 25
    assert f"25 de {len(vn)}" in cl.get("/ventas?tipo=VN").text
    assert 'role="checkbox" aria-checked="true"' in html
    # Elegir otro vehículo
    otro = orden[5]
    html = cl.get(f"/ventas?v={otro}").text
    assert re.search(rf'aria-current="true">\s*<span class="vehiculo-nombre">[^<]+</span>\s*'
                     rf'<span class="vehiculo-detalle">[^<]* · {otro}<', html)
    assert f"· {otro} ·" in bloques(html)["detalle"][1]
    # Móvil: una copia del detalle justo debajo de la fila elegida (en escritorio se oculta con CSS)
    lista = bloques(html)["lista"][1]
    filas_html = re.findall(r"<tr[^>]*>", lista.split("<tbody>")[1])
    i = filas_html.index('<tr class="elegido">')
    assert filas_html[i + 1] == '<tr class="detalle-movil">' and lista.count('class="detalle-movil"') == 1
    assert re.search(rf'<tr class="detalle-movil">\s*<td colspan="6" class="izq">\s*<div>\s*<h3 id="t-detalle-movil"[^>]*>'
                     rf'[^<]+</h3>\s*<p class="nota">[^<]*· {otro} ·', lista)
    # Un vehículo más allá de los 25 primeros: la lista se amplía hasta él
    lejos = orden[60]
    html = cl.get(f"/ventas?v={lejos}").text
    assert "75 de 142" in html and f"· {lejos}</span>" in html


def test_diagnostico_en_lenguaje_llano():
    from panel.marketing import diagnostico
    assert diagnostico("web", "Leads por clic", "%", 1.36, 1.91, 2.38, 2.91, 13, 0.78, 31) == (
        "Dónde falla: la web. Leads por clic: 1,36 %, frente a 2,38 % de referencia (1,91–2,91 %) en campañas "
        "con los mismos 13 días de vida. Probabilidad de ir peor: 78 % (n = 31 campañas)")
    assert diagnostico("stock", "Unidades del modelo en stock", "uds", 4, 8, 11, 15, 20, 0.91, 12).startswith(
        "Dónde falla: el stock. Unidades del modelo en stock: 4 uds, frente a 11 uds de referencia (8–15 uds)")


def test_marketing_cifras_salud_y_notas(pg, panel_url, personas, con_ejemplo):
    filas = ejemplo.generar(HOY, con_ejemplo)
    cl = dentro(app_con(panel_url), "tecnico")
    html = cl.get("/marketing").text
    b = bloques(html)
    fin = max(f["dia"] for f in filas["panel_campana_dia"])
    ini = fin - timedelta(days=27)
    gasto = sum(f["gasto_cents"] for f in filas["panel_campana_dia"] if ini <= f["dia"] <= fin) / 100
    leads = sum(f["leads"] for f in filas["panel_leads_dia"] if f["campana_id"] and ini <= f["dia"] <= fin)
    from panel import formato as fmt
    assert f'<span class="kpi-valor">{fmt.miles(gasto)} €</span>' in b["kpis"][1]
    assert f'<span class="kpi-valor">{fmt.miles(leads)}</span>' in b["kpis"][1]
    assert f'{fmt.miles(gasto / leads)} €' in b["kpis"][1]
    # La salud frente a su referencia queda como detalle de cada campaña (la decisión es de los criterios
    # de Meta: sin pasar el motor, «Sin decidir todavía»)
    camp = b["campanas"][1]
    for estado in ("Bien", "Vigilar", "Revisar", "Sin datos suficientes"):
        assert f"Frente a campañas con los mismos días de vida: {estado}" in camp, estado
    assert "Nuevos (VN) · " in camp and "Ocasión (VO) · " in camp and "General (VN y VO) · " in camp
    assert "días de vida" in camp and "gasto común" not in camp           # en Todo, la general con sus costes
    # El detalle va en su propia fila, a todo el ancho; en «Decisión» queda solo la decisión
    assert re.search(r'<tr class="fila-diagnostico">\s*<td colspan="9">\s*<p class="diagnostico">Detalle · [^<]*'
                     r"Dónde falla: el anuncio\. Clics por impresión: [\d,]+ %, frente a [\d,]+ % de referencia "
                     r"\([\d,]+–[\d,]+ %\) en campañas con los mismos \d+ días de vida\. Probabilidad de ir peor: "
                     r"95 % \(n = 31 campañas\)", camp)
    assert "Dónde falla: la web. Leads por clic:" in camp
    decision = re.findall(r'<td data-etiqueta="Decisión"[^>]*>(.*?)</td>', camp, re.S)
    assert decision and all(re.fullmatch(r'\s*<span class="chip [^"]+">Sin decidir todavía</span>\s*', c)
                            for c in decision)
    assert "se compara con su referencia en campañas anteriores de su mismo ámbito (VN, VO o general)" in camp
    assert "explica, pero no decide" in camp
    # Recomendación, atención, semanas y campañas pasadas
    for accion in ("Subir presupuesto", "Continuar", "Revisar o parar"):
        assert f'<span class="reco-titulo">{accion}</span>' in b["recomendacion"][1]
    assert "Es una señal direccional" in b["atencion"][1]
    sem = b["semanas"][1]
    assert sem.count('class="semana"') == 6
    assert "Orientativo: describe cómo viene cada semana; no mide el efecto de lanzar en una u otra." in sem
    for motivo in ("Estacionalidad:", "Calendario:", "Stock envejecido:", "Atención:", "Presión de otros concesionarios:"):
        assert motivo in sem, motivo
    pas = b["pasadas"][1]
    assert pas.count('class="pasada"') == 6 and "sin campaña:" in pas
    for v in ("Por encima de lo esperado", "Dentro de lo normal"):
        assert v in pas
    # Ordenar por gasto
    html = cl.get("/marketing?orden=gasto").text
    nombres = re.findall(r'<span class="campana-nombre">([^<]+)</span>', bloques(html)["campanas"][1])
    gastos = {}
    for f in filas["panel_campana_dia"]:
        if ini <= f["dia"] <= fin:
            gastos[f["titulo"]] = gastos.get(f["titulo"], 0) + f["gasto_cents"]
    assert nombres == sorted(gastos, key=lambda n: -gastos[n])


# ================================================================== Marketing por tipo

def _filas_campanas(html: str) -> list[dict]:
    salida = []
    for ambito, fila in re.findall(r'<tr(?: class="aviso")? data-ambito="([^"]+)">(.*?)</tr>', html, re.S):
        celdas = dict(re.findall(r'<td data-etiqueta="([^"]+)"[^>]*>\s*(.*?)\s*</td>', fila, re.S))
        salida.append({"ambito": ambito, "nombre": re.search(r'class="campana-nombre">([^<]+)<', fila).group(1),
                       "nota": re.search(r'class="campana-nota">([^<]+)<', fila).group(1), **celdas})
    return salida


def _kpi(html: str, etiqueta_inicio: str) -> tuple[str, str]:
    m = re.search(r'<span class="kpi-etiqueta">' + etiqueta_inicio + r'[^<]*</span>\s*<span class="kpi-valor">([^<]+)'
                  r'</span>\s*<span class="kpi-sub">([^<]*)</span>', html)
    return m.group(1), m.group(2)


@pytest.mark.parametrize("tipo", ["todo", "VN", "VO"])
def test_marketing_selector_cuadra_con_las_filas(pg, panel_url, personas, con_ejemplo, tipo):
    from panel import formato as fmt
    filas = ejemplo.generar(HOY, con_ejemplo)
    cl = dentro(app_con(panel_url), "tecnico")
    html = cl.get(f"/marketing?tipo={tipo}").text
    b = bloques(html)
    assert re.search(r'aria-current="true">' + {"todo": "Todo", "VN": "Nuevos \\(VN\\)",
                                                 "VO": "Ocasión \\(VO\\)"}[tipo] + "<", html)
    fin = max(f["dia"] for f in filas["panel_campana_dia"])
    ini = fin - timedelta(days=27)
    ambito = {f["campana_id"]: f["ambito"] for f in filas["panel_campana_dia"]}
    titulo = {f["campana_id"]: f["titulo"] for f in filas["panel_campana_dia"]}
    gasto = {}
    for f in filas["panel_campana_dia"]:
        if ini <= f["dia"] <= fin:
            gasto[f["campana_id"]] = gasto.get(f["campana_id"], 0) + f["gasto_cents"] / 100
    def leads(cid, de_tipo=None):
        return sum(f["leads"] for f in filas["panel_leads_dia"] if f["campana_id"] == cid and ini <= f["dia"] <= fin
                   and (de_tipo is None or f["tipo"] == de_tipo))
    # Filas: en VN o VO, las de ese ámbito y las generales; de una general, solo los leads del tipo
    visibles = {cid for cid in gasto if tipo == "todo" or ambito[cid] in (tipo, "general")}
    tabla = _filas_campanas(b["campanas"][1])
    assert {f["nombre"] for f in tabla} == {titulo[c] for c in visibles}
    por_nombre = {f["nombre"]: f for f in tabla}
    for cid in visibles:
        f = por_nombre[titulo[cid]]
        comun = tipo != "todo" and ambito[cid] == "general"
        assert f["ambito"] == ambito[cid]
        assert f["Gasto"] == f"{fmt.miles(gasto[cid])} €"                 # el gasto, entero: no se reparte
        assert f["Leads"] == fmt.miles(leads(cid, tipo if comun else None)), cid
        if comun:
            assert (f["€ / lead"], f["€ / cita"], f["€ / venta"]) == ("—", "—", "—"), cid
            assert "gasto común a VN y VO" in f["nota"]
        else:
            assert f["€ / lead"] == f"{fmt.miles(gasto[cid] / leads(cid))} €" and "gasto común" not in f["nota"]
    # Tarjetas: en VN o VO, solo las campañas de ese ámbito, y lo dicen
    de_tarjetas = {cid for cid in gasto if tipo == "todo" or ambito[cid] == tipo}
    g = sum(gasto[c] for c in de_tarjetas)
    l = sum(leads(c) for c in de_tarjetas)
    valor, sub = _kpi(b["kpis"][1], "Inversión")
    assert valor == f"{fmt.miles(g)} €"
    assert _kpi(b["kpis"][1], "Leads")[0] == fmt.miles(l)
    assert _kpi(b["kpis"][1], "Coste por lead")[0] == f"{fmt.miles(g / l)} €"
    # …y cuadran con las filas de la tabla que no son generales
    assert g == pytest.approx(sum(float(f["Gasto"][:-2].replace(".", "")) for f in tabla
                                  if tipo == "todo" or f["ambito"] == tipo), abs=len(tabla))
    if tipo == "todo":
        assert "sin la campaña general" not in html
    else:
        assert "sin la campaña general" in sub and "Solo las campañas de" in html
        assert "Campaña general (VN y VO): leads, citas y ventas solo de " + tipo in b["campanas"][1]
    # Recomendación: VN o VO, del stock de ese tipo; Todo, las dos con su etiqueta
    reco = b["recomendacion"][1]
    esperadas = [f for f in filas["panel_recomendacion_modelo"] if tipo == "todo" or f["tipo"] == tipo]
    assert reco.count('class="reco-modelo"') == len(esperadas)
    etiquetas = re.findall(r'<span class="chip chip-tipo">(V[NO])</span>', reco)
    assert sorted(etiquetas) == (sorted(f["tipo"] for f in esperadas) if tipo == "todo" else [])
    # Semanas con la venta esperada del tipo elegido
    sem = [f for f in filas["panel_semana_campana"] if f["tipo"] == tipo]
    texto = b["semanas"][1]
    for f in sem:
        assert f"{fmt.decimal(f['indice_p50'], 2)} veces una semana media" in texto
    assert ("Ventas esperadas:" in texto) == (tipo == "todo")
    # Campañas pasadas: las generales en los tres, con los leads y ventas del tipo elegido
    res = [f for f in filas["panel_campana_resultado"]
           if (tipo == "todo" and f["tipo"] == "todo")
           or (tipo != "todo" and ((f["ambito"] == tipo and f["tipo"] == "todo") or (f["ambito"] == "general"
                                                                                     and f["tipo"] == tipo)))]
    pas = b["pasadas"][1]
    assert pas.count('class="pasada"') == len(res)
    assert sum(f["ambito"] == "general" for f in res) == 2
    for f in res:
        assert f'<strong>{fmt.miles(f["leads"])}</strong>' in pas
    if tipo != "todo":
        assert f'<span class="pasada-etiqueta">Leads {tipo}</span>' in pas and "(gasto común a VN y VO)" in pas


def test_marketing_atencion_en_todo_y_vn_y_ocasion_en_vo(pg, panel_url, personas, con_ejemplo):
    from panel import formato as fmt
    filas = ejemplo.generar(HOY, con_ejemplo)
    cl = dentro(app_con(panel_url), "tecnico")
    for tipo in ("todo", "VN"):
        b = bloques(cl.get(f"/marketing?tipo={tipo}").text)
        assert "atencion" in b and "ocasion" not in b
    b = bloques(cl.get("/marketing?tipo=VO").text)
    assert "atencion" not in b and "Atención frente a sus rivales" not in b["ocasion"][1]
    oc = b["ocasion"][1]
    assert "Ocasión: dónde hay stock que mover" in oc and b["ocasion"][0] == "ejemplo" and ETIQUETA in oc
    objetivo = con_ejemplo.objetivo_antiguedad_dias
    assert f"Más de {objetivo} días" in oc
    vo = [f for f in filas["panel_riesgo_vehiculo"] if f["tipo"] == "VO"]
    filas_html = re.findall(r'<tr>\s*<th scope="row" class="izq">([^<]+)</th>(.*?)</tr>', oc, re.S)
    assert {m for m, _ in filas_html} == {f["modelo"] for f in vo}
    for modelo, resto in filas_html:
        suyos = [f for f in vo if f["modelo"] == modelo]
        celdas = re.findall(r'<td data-etiqueta="[^"]+"[^>]*>([^<]*)</td>', resto)
        precios = [Decimal(str(f["precio_mercado_pct"])) for f in suyos if f["precio_mercado_pct"] is not None]
        media = float(sum(precios) / len(precios)) if precios else None
        from panel import negocio as ng
        assert celdas == [fmt.miles(len(suyos)), fmt.miles(sum(f["dias_exposicion"] > objetivo for f in suyos)),
                          ng.signo_pct(media)], modelo
    # Ordenadas por unidades que superan el objetivo
    viejas = [int(re.findall(r'<td[^>]*>([^<]*)</td>', r)[1]) for _, r in filas_html]
    assert viejas == sorted(viejas, reverse=True)


def test_marketing_orden_y_tipo_se_conservan(pg, panel_url, personas, con_ejemplo):
    cl = dentro(app_con(panel_url), "tecnico")
    html = cl.get("/marketing?tipo=VO&orden=gasto").text
    assert 'href="/marketing?tipo=VO&amp;orden=venta"' in html          # cambiar el orden mantiene el tipo
    assert 'href="/marketing?tipo=VN&amp;orden=gasto"' in html          # cambiar el tipo mantiene el orden
    r = cl.get("/marketing?tipo=VN", headers={"HX-Request": "true"})
    assert r.text.lstrip().startswith('<div id="marketing"') and "<html" not in r.text
    html = cl.get("/marketing?tipo=xx&orden=yy").text                   # valores desconocidos: Todo, por venta
    assert 'aria-current="true">Todo<' in html and 'aria-current="true">Por coste por venta<' in html


# ================================================================== visibilidad por rol

def _ve(cl, ruta) -> bool:
    r = cl.get(ruta)
    assert r.status_code == 200, ruta
    return PENDIENTE not in r.text


@pytest.mark.parametrize("visible", [None, "direccion,tecnico", "ventas, marketing"])
def test_con_datos_de_ejemplo_cada_rol_ve_segun_la_variable(pg, panel_url, personas, con_ejemplo, visible):
    app = app_con(panel_url, visible)
    roles_ejemplo = {r.strip() for r in visible.split(",")} if visible else {"tecnico"}
    for rol in ROLES:
        cl = dentro(app, rol)
        nav = cl.get("/mercado").text
        for vista, ruta in VISTAS.items():
            if vista not in MATRIZ[rol]:
                assert cl.get(ruta).status_code == 403
                continue
            esperado = rol in roles_ejemplo
            assert _ve(cl, ruta) == esperado, (rol, vista)
            assert (f'href="{ruta}"' in nav) == esperado, (rol, vista)
            titulo = permisos.POR_CLAVE[vista].titulo
            assert (f'{titulo}<span class="oculto"> (Se activa al conectar sus datos)' in nav) == (not esperado)
        # Donde entra cada rol
        destino = cl.get("/").headers["location"]
        assert destino == ("/direccion" if "direccion" in MATRIZ[rol] and rol in roles_ejemplo else
                           f"/{rol}" if rol in roles_ejemplo else "/mercado")


def test_variable_con_un_rol_desconocido_no_arranca():
    with pytest.raises(ValueError, match="roles desconocidos"):
        permisos.roles_ejemplo("tecnico,gerente")
    assert permisos.roles_ejemplo("") == permisos.roles_ejemplo(None) == frozenset({"tecnico"})


def test_al_sustituir_por_filas_reales_el_bloque_pierde_la_etiqueta(pg, panel_url, personas, con_ejemplo):
    app = app_con(panel_url)
    tec, dire = dentro(app, "tecnico"), dentro(app, "direccion")
    # 1. Llega Walcu con el stock: los tres bloques de stock pasan a reales; la vista sigue de ejemplo
    a_real(pg, "panel_stock_vehiculo")
    b = bloques(tec.get("/direccion").text)
    for nombre in ("stock", "antiguedad", "edad"):
        assert b[nombre][0] == "real" and ETIQUETA not in b[nombre][1], nombre
    for nombre in ("ventas_kpi", "ventas_mes", "alertas_kpi", "alertas"):
        assert b[nombre][0] == "ejemplo" and ETIQUETA in b[nombre][1], nombre
    assert BANDA in tec.get("/direccion").text and not _ve(dire, "/direccion")
    # 2. El resto de Dirección: la vista se abre a dirección, sin banda ni etiquetas
    a_real(pg, "panel_ventas_mes", "panel_alertas")
    html = dire.get("/direccion").text
    assert _ve(dire, "/direccion") and BANDA not in html and ETIQUETA not in html
    assert 'href="/direccion"' in html
    # 3. Ventas: el detalle lee dos tablas; con una real, el bloque es real y no mezcla
    a_real(pg, "panel_riesgo_vehiculo")
    b = bloques(tec.get("/ventas").text)
    assert {n: b[n][0] for n in ("resumen", "lista", "detalle")} == {"resumen": "real", "lista": "real", "detalle": "real"}
    assert "Sin curva de su grupo todavía" in b["detalle"][1] and ETIQUETA not in b["detalle"][1]
    assert _ve(dentro(app, "ventas"), "/ventas")                       # sin bloques de ejemplo: abierta a ventas
    a_real(pg, "panel_curva_grupo")
    assert "data-curva=" in bloques(tec.get("/ventas").text)["detalle"][1]
    # 4. Marketing: entran los leads de Walcu antes que Meta; los bloques que los leen no mezclan
    a_real(pg, "panel_leads_dia")
    b = bloques(tec.get("/marketing").text)
    assert b["kpis"][0] == "real" and "Sin datos todavía" in b["kpis"][1] and ETIQUETA not in b["kpis"][1]
    assert b["campanas"][0] == "real" and "Sin datos todavía" in b["campanas"][1]
    assert b["recomendacion"][0] == "ejemplo" and ETIQUETA in b["recomendacion"][1]
    assert not _ve(dentro(app, "marketing"), "/marketing")
    a_real(pg, "panel_campana_dia")
    b = bloques(tec.get("/marketing").text)
    assert "Sin datos todavía" not in b["kpis"][1]
    assert ">Sin decidir todavía</span>" in b["campanas"][1]            # sin pasar el motor: sin decisión
    a_real(pg, "panel_campana_salud", "panel_recomendacion_modelo", "panel_atencion_relativa",
           "panel_semana_campana", "panel_campana_resultado")
    html = dentro(app, "marketing").get("/marketing").text
    assert PENDIENTE not in html and BANDA not in html and ETIQUETA not in html


def test_sin_filas_dice_sin_datos_todavia_nunca_cero(pg, panel_url, personas, con_ejemplo):
    for t in ("panel_alertas", "panel_ventas_mes", "panel_atencion_relativa", "panel_campana_resultado"):
        pg.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(t)))
    cl = dentro(app_con(panel_url), "tecnico")
    b = bloques(cl.get("/direccion").text)
    for nombre in ("alertas_kpi", "alertas", "ventas_kpi", "ventas_mes"):
        origen, trozo = b[nombre]
        assert origen == "" and "Sin datos todavía" in trozo and ETIQUETA not in trozo, nombre
        assert 'class="kpi-valor">0<' not in trozo
    b = bloques(cl.get("/marketing").text)
    for nombre in ("atencion", "pasadas"):
        assert b[nombre][0] == "" and "Sin datos todavía" in b[nombre][1], nombre
    # Sin ninguna fila, las vistas de datos quedan desactivadas para todos
    ejemplo.borrar(pg)
    for ruta in VISTAS.values():
        assert not _ve(cl, ruta)


# ================================================================== replicable

def test_concesionario_ficticio_sin_rastro_del_propio_en_las_tres_vistas(pg, datos, panel_url, tmp_path,
                                                                         monkeypatch, personas):
    fichero = tmp_path / "concesionario.yaml"
    fichero.write_text(FICTICIO + "modelos:\n  VN: [Golf, T-Roc, Polo, Tiguan, T-Cross, Taigo]\n"
                                  "  VO: [Golf, Polo, T-Roc]\n", encoding="utf-8")
    monkeypatch.setenv("CDF_CONCESIONARIO", str(fichero))
    conc = cargar_concesionario()
    _con_datos(pg, datos, conc)
    ejemplo.cargar(pg, HOY, conc)
    app = crear_app(dsn=panel_url, hoy=lambda: HOY)
    cl = dentro(app, "tecnico")
    paginas = []
    for ruta in VISTAS.values():
        for extra in ("", "?tipo=VN", "?tipo=VO", "?riesgo=1", "?orden=gasto"):
            paginas.append(cl.get(ruta + extra).text)
            paginas.append(cl.get(ruta + extra, headers={"HX-Request": "true"}).text)
    for rol in ("direccion", "ventas", "marketing"):
        for ruta in VISTAS.values():
            paginas.append(dentro(app, rol).get(ruta).text)
    for html in paginas:
        assert "avisa" not in html.lower() and "sevilla" not in html.lower()
    direccion = cl.get("/direccion").text
    assert "El mercado en Valladolid" in direccion and "Marca Valladolid" in direccion
    assert "en Valladolid" in cl.get("/ventas").text


# ================================================================== no se rompe con otro día

@pytest.mark.parametrize("dia", [date(2026, 11, 1), date(2027, 1, 31), date(2027, 3, 2)])
def test_ejemplo_en_otros_dias(pg, panel_url, personas, conc, dia):
    ejemplo.cargar(pg, dia, conc)
    app = crear_app(dsn=panel_url, hoy=lambda: dia)
    cl = dentro(app, "tecnico")
    for ruta in VISTAS.values():
        r = cl.get(ruta)
        assert r.status_code == 200 and BANDA in r.text


# ================================================================== migración 0006 (Marketing por tipo)

def _migrar(destino: str, accion: str = "upgrade"):
    from alembic import command
    from alembic.config import Config
    getattr(command, accion)(Config(str(RAIZ / "alembic.ini")), destino)


def test_migracion_0006_desde_una_base_con_ejemplo_de_la_sesion_2(pg_url, pg, conc):
    """En producción, 0005 ya tiene filas de ejemplo sin tipo: 0006 las quita, añade las columnas y
    `make ejemplo-cargar` las vuelve a escribir. Las demás tablas del contrato no se tocan."""
    ejemplo.cargar(pg, HOY, conc)
    stock = pg.execute("SELECT count(*) FROM panel_stock_vehiculo").fetchone()[0]
    _migrar("0005", "downgrade")
    assert "ambito" not in _columnas(pg, "panel_campana_dia")
    pg.execute("INSERT INTO panel_campana_dia (dia, origen, campana_id, titulo, tipo_campana, modelo, inicio, "
               "gasto_cents, impresiones, clics) VALUES (%s, 'ejemplo', 'X', 'x', 'general', NULL, %s, 1, 1, 1)",
               (HOY, HOY))
    _migrar("head")
    for t in ejemplo_tablas_0006():
        assert pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0, t
    assert pg.execute("SELECT count(*) FROM panel_stock_vehiculo").fetchone()[0] == stock
    assert {"ambito"} <= set(_columnas(pg, "panel_campana_dia"))
    assert {"tipo"} <= set(_columnas(pg, "panel_leads_dia")) & set(_columnas(pg, "panel_semana_campana"))
    ejemplo.cargar(pg, HOY, conc)                                    # make ejemplo-cargar
    # Clave nueva de los leads: el mismo modelo, día y campaña puede tener una fila VN y otra VO
    pg.execute("INSERT INTO panel_leads_dia (dia, origen, modelo, tipo, campana_id, leads, citas, ventas) VALUES "
               "(%s, 'real', 'Golf', 'VN', NULL, 1, 0, 0), (%s, 'real', 'Golf', 'VO', NULL, 1, 0, 0)", (HOY, HOY))
    with pytest.raises(psycopg.errors.UniqueViolation):
        pg.execute("INSERT INTO panel_leads_dia (dia, origen, modelo, tipo, campana_id, leads, citas, ventas) VALUES "
                   "(%s, 'real', 'Golf', 'VN', NULL, 1, 0, 0)", (HOY,))
    # Resultado de una campaña de VN: solo su fila «todo» (las filas por tipo, solo en las generales)
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("INSERT INTO panel_campana_resultado (dia, origen, campana_id, titulo, ambito, tipo, inicio, fin, "
                   "gasto_cents, leads, leads_sin_p10, leads_sin_p50, leads_sin_p90, ventas, ventas_sin_p10, "
                   "ventas_sin_p50, ventas_sin_p90, veredicto_leads, veredicto_ventas, modelo_version, n) VALUES "
                   "(%s, 'real', 'Y', 'y', 'VN', 'VO', %s, %s, 1, 1, 1, 1, 1, 1, 1, 1, 1, 'normal', 'normal', 'v', 1)",
                   (HOY, HOY, HOY))


def test_migracion_0006_se_detiene_si_hay_filas_reales(pg_url, pg):
    _migrar("0005", "downgrade")
    pg.execute("INSERT INTO panel_leads_dia (dia, origen, modelo, campana_id, leads, citas, ventas) "
               "VALUES (%s, 'real', 'Golf', NULL, 1, 0, 0)", (HOY,))
    with pytest.raises(Exception, match="filas reales"):
        _migrar("head")
    assert pg.execute("SELECT count(*) FROM panel_leads_dia WHERE origen = 'real'").fetchone()[0] == 1


def ejemplo_tablas_0006():
    return ("panel_campana_dia", "panel_leads_dia", "panel_recomendacion_modelo", "panel_semana_campana",
            "panel_campana_resultado")
