"""Tráfico de la web (GA4) por canal (config/canales.yaml, ingesta/canales.py): valores reservados, canal
por fuente y medio con la agrupación de GA4 de respaldo, tipo por la página de destino; qué filas de GA4
se intentan clasificar como campaña de Meta; Google Ads guardado sin reglas; bloque de Marketing y
«Estado de las fuentes». Sin red.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from ingesta import campanas as cmp
from ingesta import canales as cn

HOY = date(2026, 11, 22)


@pytest.fixture
def canales():
    return cn.cargar()


# ================================================================== reglas

@pytest.mark.parametrize("valor", ["(direct)", "(organic)", "(referral)", "(not set)", "(none)", "(cross-network)",
                                   "(data not available)", "(Not Set)", "(algo nuevo de GA4)", "", "  "])
def test_valores_reservados_no_son_campanas(canales, valor):
    assert canales.reservado(valor)


def test_una_campana_no_es_un_valor_reservado(canales):
    assert not canales.reservado("VW [VN][2026] Taigo noviembre")
    assert not canales.reservado("Search_Marca")


@pytest.mark.parametrize("fuente, medio, canal_ga4, canal", [
    # Meta: por una palabra de la fuente o del medio
    ("facebook", "paid", "Paid Social", "meta"),
    ("fb", "cpc", None, "meta"),
    ("instagram", "paid", None, "meta"),
    ("ig", "social", None, "meta"),
    ("l.facebook.com", "referral", "Organic Social", "meta"),
    ("m.facebook.com", "referral", None, "meta"),
    ("an", "paid", None, "meta"),                      # Audience Network
    ("Facebook_Mobile_Feed", "paid", None, "meta"),
    ("newsletter_x", "meta", None, "meta"),            # medio de Meta
    # Google Ads: google con cpc, ppc o paid
    ("google", "cpc", "Paid Search", "google_ads"),
    ("google", "ppc", None, "google_ads"),
    ("Google", "Paid", None, "google_ads"),
    ("adwords", "cpc", None, "google_ads"),
    # Búsqueda orgánica
    ("google", "organic", "Organic Search", "organica"),
    ("bing", "organic", None, "organica"),
    ("www.google.com", "referral", None, "organica"),
    ("duckduckgo.com", "referral", None, "organica"),
    # Directo, referencia, correo y portales
    ("(direct)", "(none)", "Direct", "directo"),
    ("(direct)", "(not set)", None, "directo"),
    ("blog.ejemplo.es", "referral", "Referral", "referencia"),
    ("newsletter", "email", "Email", "correo"),
    ("mailchimp", "(not set)", None, "correo"),
    ("www.coches.net", "referral", "Referral", "portales"),
    ("m.milanuncios.com", "referral", None, "portales"),
    ("es.wallapop.com", "referral", None, "portales"),
    ("autoscout24.es", "cpc", None, "portales"),
    # Respaldo con la agrupación de GA4 y «otros»
    ("chatgpt.com", "(not set)", "Organic Search", "organica"),
    ("app", "(not set)", "Direct", "directo"),
    ("tiktok", "paid", "Paid Social", "otros"),
    ("bing", "cpc", "Paid Search", "otros"),           # Paid Search no se traduce: no es Google Ads
    ("rara", "rara", None, "otros"),
])
def test_canal_por_fuente_y_medio(canales, fuente, medio, canal_ga4, canal):
    assert canales.canal(fuente, medio, canal_ga4) == canal


@pytest.mark.parametrize("pagina, tipo", [
    ("/ocasion/t-roc-2021", "VO"), ("/vehiculos-de-ocasion", "VO"), ("/seminuevos/golf", "VO"),
    ("/nuevos/taigo", "VN"), ("/gama/t-roc", "VN"), ("/vn/oferta", "VN"),
    ("/", None), ("/contacto", None), ("/ocasion/nuevos", None),       # ninguna o las dos: no se sabe
])
def test_tipo_por_la_pagina_de_destino(canales, pagina, tipo):
    assert canales.tipo(pagina) == tipo


@pytest.mark.parametrize("texto", [
    "valores_reservados: [(direct)]\ncanales:\n  - {canal: otro, nombre: Otro, si: [{medio: [x]}]}\n",   # sin meta
    "valores_reservados: [(direct)]\ncanales:\n  - {canal: meta, nombre: Meta, si: [{fuente_rara: [x]}]}\n",
    "valores_reservados: [(direct)]\ncanales:\n  - {canal: meta, nombre: Meta}\n",
    "valores_reservados: [(direct)]\ncanales:\n  - {canal: meta, nombre: Meta, si: [{medio: []}]}\n",
    "valores_reservados: [(direct)]\ncanales:\n  - {canal: meta, nombre: Meta, si: [{medio: [x]}]}\n"
    "  - {canal: meta, nombre: Meta 2, si: [{medio: [y]}]}\n",
    "valores_reservados: [(direct)]\ncanales:\n  - {canal: meta, nombre: Meta, si: [{medio: [x]}]}\n"
    "respaldo_ga4: {Direct: directo}\n",
    "canales:\n  - {canal: meta, nombre: Meta, si: [{medio: [x]}]}\n",                                  # sin reservados
])
def test_configuracion_no_valida(tmp_path, texto):
    f = tmp_path / "canales.yaml"
    f.write_text(texto)
    with pytest.raises(cn.ErrorCanales):
        cn.cargar(f)


# ================================================================== tablas

def meta(pg, cid, nombre, dia=HOY):
    pg.execute("""INSERT INTO campanas_meta_dia (descargado_at, run_id, origen, cuenta_id, dia, nivel, campana_id,
                  campana_nombre, gasto_cents, impresiones, clics, leads, huella)
                  VALUES (now(), 'r', 'prueba', '1', %s, 'campana', %s, %s, 1000, 1000, 20, 1, %s)""",
               (dia, cid, nombre, f"{cid}{dia}"))


def ga4(pg, fuente, medio, campana="(not set)", pagina="/", sesiones=10, interaccion=5, eventos=1, dia=HOY,
        campana_id=None, canal_ga4=None):
    pg.execute("""INSERT INTO ga4_trafico_dia (descargado_at, run_id, origen, propiedad_id, dia, fuente, medio, campana,
                  pagina_destino, sesiones, sesiones_interaccion, eventos_clave, campana_id, canal_ga4, huella)
                  VALUES (now(), 'r', 'p', '1', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
               (dia, fuente, medio, campana, pagina, sesiones, interaccion, eventos, campana_id, canal_ga4,
                f"{dia}{fuente}{medio}{campana}{pagina}"))


def _escenario(pg):
    meta(pg, "120200001", "VW [VN][2026] Taigo noviembre")
    meta(pg, "120200002", "Campaña rara sin regla")
    # Campaña de Meta que llega por GA4 con su utm: se une por nombre y no sale sin regla
    ga4(pg, "facebook", "paid", "vw_vn_2026_taigo_noviembre", "/nuevos/taigo", 30, 20, 3)
    # Otra de Meta por el ID, aunque llegue con otro nombre y otra fuente
    ga4(pg, "ig", "paid", "taigo-ig", "/nuevos/taigo", 8, 4, 0, campana_id="120200001")
    ga4(pg, "newsletter", "email", "VW [VN][2026] Taigo noviembre", "/nuevos/taigo", 2, 1, 0)   # por nombre: Meta
    # Desde Meta pero sin regla: sí sale en «Campañas sin regla»
    ga4(pg, "facebook", "paid", "Prueba de anuncio", "/", 5, 2, 0)
    # Valores reservados en Meta (tráfico orgánico de Facebook): Meta, pero no es campaña
    ga4(pg, "l.facebook.com", "referral", "(referral)", "/ocasion/polo", 6, 3, 0)
    ga4(pg, "m.facebook.com", "referral", "(cross-network)", "/", 1, 0, 0)
    # Google Ads: se guarda con su nombre, sin reglas, y nunca sale sin regla
    ga4(pg, "google", "cpc", "Search_Marca", "/ocasion/golf", 40, 30, 4, campana_id="987")
    ga4(pg, "google", "cpc", "PMax Ocasión", "/ocasion", 20, 10, 2, dia=HOY - timedelta(days=1))
    ga4(pg, "google", "cpc", "(not set)", "/", 3, 1, 0)
    # El resto del tráfico, por canal
    ga4(pg, "google", "organic", "(organic)", "/", 100, 60, 3)
    ga4(pg, "(direct)", "(none)", "(direct)", "/ocasion/t-cross", 50, 25, 1)
    ga4(pg, "blog.ejemplo.es", "referral", "(referral)", "/", 7, 3, 0)
    ga4(pg, "www.coches.net", "referral", "(referral)", "/ocasion/t-roc", 12, 9, 2)
    ga4(pg, "tiktok", "paid", "Campaña de TikTok", "/", 9, 2, 0, canal_ga4="Paid Social")
    ga4(pg, "rara", "rara", "(not set)", "/nuevos", 4, 1, 0, dia=HOY - timedelta(days=3))
    ga4(pg, "rara", "rara", "(not set)", "/", 1, 0, 0)


def test_actualizar_canales_campanas_de_meta_y_google_ads(pg):
    _escenario(pg)
    assert cmp.actualizar(pg) == {"campanas_clasificadas": 1, "campanas_sin_regla": 2}
    sin = sorted(pg.execute("SELECT fuente, campana FROM panel_campanas_sin_regla").fetchall())
    # Solo campañas de Meta: ni los valores reservados, ni Google Ads, ni TikTok, ni el resto
    assert sin == [("ga4", "Prueba de anuncio"), ("meta", "Campaña rara sin regla")]
    trafico = {(c, t): (s, si, float(e)) for c, t, s, si, e in pg.execute(
        """SELECT canal, tipo, sum(sesiones), sum(sesiones_interaccion), sum(eventos_clave)
           FROM panel_trafico_canal_dia WHERE origen = 'real' GROUP BY 1, 2""")}
    assert trafico == {
        ("meta", "VN"): (40, 25, 3.0), ("meta", "sin_tipo"): (6, 2, 0.0), ("meta", "VO"): (6, 3, 0.0),
        ("google_ads", "VO"): (60, 40, 6.0), ("google_ads", "sin_tipo"): (3, 1, 0.0),
        ("organica", "sin_tipo"): (100, 60, 3.0), ("directo", "VO"): (50, 25, 1.0),
        ("referencia", "sin_tipo"): (7, 3, 0.0), ("portales", "VO"): (12, 9, 2.0),
        ("otros", "sin_tipo"): (10, 2, 0.0), ("otros", "VN"): (4, 1, 0.0)}
    # La campaña por correo con el nombre de una de Meta cuenta como Meta (dentro de los 40 de VN)
    otros = pg.execute("SELECT fuente, medio, canal_ga4, primer_dia, ultimo_dia, sesiones FROM panel_trafico_otros "
                       "ORDER BY fuente").fetchall()
    assert otros == [("rara", "rara", None, HOY - timedelta(days=3), HOY, 5),
                     ("tiktok", "paid", "Paid Social", HOY, HOY, 9)]
    google = pg.execute("SELECT campana, campana_id, primer_dia, sesiones, sesiones_interaccion, eventos_clave "
                        "FROM panel_campanas_google_ads ORDER BY campana").fetchall()
    assert [(g[0], g[1], g[2], g[3], g[4], float(g[5])) for g in google] == [
        ("PMax Ocasión", None, HOY - timedelta(days=1), 20, 10, 2.0), ("Search_Marca", "987", HOY, 40, 30, 4.0)]
    # Se rehace entero: una segunda pasada no duplica
    cmp.actualizar(pg)
    assert pg.execute("SELECT sum(sesiones) FROM panel_trafico_canal_dia WHERE origen = 'real'").fetchone()[0] == 298


# ================================================================== panel

def _cliente(pg, panel_url):
    from panel import usuarios
    from test_contrato_panel import app_con
    from test_panel import dentro
    usuarios.alta(pg, "tecnico", "T", "tecnico", "clave-temporal-123")
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    return dentro(app_con(panel_url), "tecnico")


def test_marketing_trafico_por_canal_y_selector(pg, panel_url):
    from test_contrato_panel import bloques
    _escenario(pg)
    # 28 días antes: algo de tráfico para comparar
    ga4(pg, "google", "organic", "(organic)", "/", 80, 40, 1, dia=HOY - timedelta(days=30))
    ga4(pg, "google", "cpc", "Search_Marca", "/ocasion/golf", 50, 30, 1, dia=HOY - timedelta(days=40))
    cmp.actualizar(pg)
    cl = _cliente(pg, panel_url)
    origen, html = bloques(cl.get("/marketing?tipo=todo").text)["trafico"]
    assert origen == "real" and "Tráfico de la web por canal" in html
    assert "378 sesiones" not in html and "298 sesiones" in html
    fila = html.split('data-canal="organica"')[1].split("</tr>")[0]
    assert "Búsqueda orgánica" in fila and ">100<" in fila and "+25 %" in fila and ">60<" in fila and "60 %" in fila
    fila = html.split('data-canal="google_ads"')[1].split("</tr>")[0]
    assert "Google Ads" in fila and ">63<" in fila and "+26 %" in fila
    assert html.index('data-canal="google_ads"') > html.index('data-canal="organica"')   # por sesiones
    assert html.index('data-canal="otros"') > html.index('data-canal="referencia"')       # «otros», al final
    assert "cuentan solo en «Todo»" not in html
    # VO: solo las sesiones con página de destino de ocasión, y lo dice
    _, vo = bloques(cl.get("/marketing?tipo=VO").text)["trafico"]
    assert "128 sesiones de VO" in vo
    assert 'data-canal="organica"' not in vo and 'data-canal="portales"' in vo
    assert "126 de 298 sesiones (42 %) llegan a una página que no dice si es de nuevos o de ocasión" in vo
    fila = vo.split('data-canal="google_ads"')[1].split("</tr>")[0]
    assert ">60<" in fila and "+20 %" in fila
    _, vn = bloques(cl.get("/marketing?tipo=VN").text)["trafico"]
    assert "44 sesiones de VN" in vn and 'data-canal="meta"' in vn


def test_estado_de_las_fuentes_canales_otros_y_google_ads(pg, panel_url):
    _escenario(pg)
    cmp.actualizar(pg)
    html = _cliente(pg, panel_url).get("/estado-fuentes").text
    trozo = html.split('data-bloque="trafico-canales"')[1].split("</section>")[0]
    assert "298 sesiones" in trozo
    fila = trozo.split('data-canal="organica"')[1].split("</tr>")[0]
    assert ">100<" in fila and "33,6 %" in fila
    assert "tiktok" in trozo and "Paid Social" in trozo and "rara" in trozo
    assert "Search_Marca" in trozo and "PMax Ocasión" in trozo
    sin = html.split('data-bloque="campanas-sin-regla"')[1].split("</section>")[0]
    assert "Prueba de anuncio" in sin and "Search_Marca" not in sin and "TikTok" not in sin
    assert "(referral)" not in sin and "(cross-network)" not in sin
