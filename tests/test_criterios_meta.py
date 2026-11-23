"""Criterios de Cayetano para las campañas de Meta (docs/criterios_marketing_meta.md): reglas a–f, subir
presupuesto y la regla e, en el borde de cada umbral; volúmenes mínimos; la decisión de cada campaña y la
recomendación por modelo; la zona cercana de Mercado. Sin red.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from alertas import criterios as cr
from alertas import motor
from ingesta.concesionario import cargar as cargar_concesionario
from test_alertas import alertas, reglas

HOY = date(2026, 11, 20)
META = ("meta_a", "meta_b", "meta_c", "meta_d", "meta_f", "meta_subir")


@pytest.fixture
def conc():
    return cargar_concesionario()


@pytest.fixture
def crit():
    return motor.cargar_criterios()


def campana(pg, cid, *, dias=2, gasto=1000, impresiones=None, clics=None, leads=0, citas=0, ventas=0, zona=None, fuera=None,
            modelo="Golf", ambito="VN", hasta=HOY, titulo=None, leads_por_dia=None, gasto_por_dia=None,
            sin_mercado=False):
    """Una campaña de `dias` días hasta `hasta` (día de vida = dias − 1). Gasto (céntimos), impresiones,
    leads, citas y ventas totales; los de Walcu, el último día salvo `leads_por_dia`. Por defecto, clics y
    impresiones que no disparan la regla b: 10 clics por lead (10 % de clic a lead) y 10 % de tasa de clic."""
    if clics is None:
        clics = leads * 10 or 100
        if impresiones is not None:
            clics = min(clics, impresiones)
    if impresiones is None:
        impresiones = clics * 10
    inicio = hasta - timedelta(days=dias - 1)
    for i in range(dias):
        d = inicio + timedelta(days=i)
        g = gasto_por_dia[i] if gasto_por_dia else (gasto // dias + (gasto % dias if i == dias - 1 else 0))
        pg.execute("""INSERT INTO panel_campana_dia (dia, origen, campana_id, titulo, tipo_campana, modelo, ambito,
                      sin_mercado, inicio, gasto_cents, impresiones, clics)
                      VALUES (%s, 'real', %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                   (d, cid, titulo or cid, "modelo" if modelo else "general", modelo, ambito, sin_mercado, inicio, g,
                    impresiones if i == dias - 1 else 0, clics if i == dias - 1 else 0))
        n = leads_por_dia[i] if leads_por_dia else (leads if i == dias - 1 else 0)
        ultimo = i == dias - 1
        if n or ultimo:
            pg.execute("""INSERT INTO panel_leads_dia (dia, origen, modelo, tipo, campana_id, leads, citas, ventas,
                          leads_cp_zona, leads_cp_fuera) VALUES (%s, 'real', %s, %s, %s, %s, %s, %s, %s, %s)""",
                       (d, modelo or "General", ambito if ambito in ("VN", "VO") else "VN", cid, n,
                        citas if ultimo else 0, ventas if ultimo else 0,
                        zona if ultimo else None, fuera if ultimo else None))


def juicio(pg, conc, crit, cid, modelos_e=None):
    _, js = cr.juzgar(pg, HOY, "real", conc, crit, modelos_e or {})
    return next(j for j in js if j.campana_id == cid)


def segmento(pg, nivel, modelo, propios, *, acabado=None, motor_=None, n_p=0, p_p=None, n_c=0, p_c=None,
             zona="cercana", tipo="VN", dia=HOY):
    """Una fila de panel_mercado_segmento (precios en euros)."""
    anuncios = propios + n_c
    p50 = p_c or p_p
    pg.execute("""INSERT INTO panel_mercado_segmento (dia, tipo, zona, nivel, modelo, acabado, acabado_reconocido,
                  combustible, motor, anuncios, propios, n_precio, precio_p25_cents, precio_p50_cents, precio_p75_cents,
                  n_precio_propios, precio_propios_p50_cents, n_precio_competencia, precio_competencia_p50_cents)
                  VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
               (dia, tipo, zona, nivel, modelo, acabado, None if acabado is None else True,
                "Gasolina" if nivel == "motor" else None, motor_, anuncios, propios,
                (n_p + n_c) if p50 else 0, *(3 * [p50 * 100 if p50 else None]),
                n_p, p_p * 100 if p_p else None, n_c, p_c * 100 if p_c else None))


# ================================================================== regla a

@pytest.mark.parametrize("citas, salta", [(499, True), (500, False)])
def test_a_lead_a_cita_en_el_borde_del_5(pg, conc, crit, citas, salta):
    campana(pg, "C", leads=10_000, citas=citas, gasto=1_000)
    r = juicio(pg, conc, crit, "C").reglas["a"]
    assert r.activa is salta
    assert f"lead a cita: {citas} de 10000 = {'4,99' if salta else '5'} % (umbral 5 %)" in r.cifras_texto


@pytest.mark.parametrize("leads, estado", [(19, "sin_volumen"), (20, "activa")])
def test_a_lead_a_cita_necesita_20_leads(pg, conc, crit, leads, estado):
    campana(pg, "C", leads=leads, citas=0, gasto=100)
    r = juicio(pg, conc, crit, "C").reglas["a"]
    assert r.estado == estado and f"lead a cita: 0 de {leads} = 0 %" in r.cifras_texto
    if estado == "sin_volumen":
        assert "sin volumen: 19 leads, mínimo 20" in r.cifras_texto


@pytest.mark.parametrize("fuera, salta", [(3001, True), (3000, False)])
def test_a_fuera_de_zona_en_el_borde_del_30(pg, conc, crit, fuera, salta):
    campana(pg, "C", leads=10_000, citas=2_000, zona=10_000 - fuera, fuera=fuera, gasto=1_000)
    r = juicio(pg, conc, crit, "C").reglas["a"]
    assert r.activa is salta
    assert f"fuera de zona: {fuera} de 10000 leads con código postal = {'30,01' if salta else '30'} % (umbral 30 %)" \
        in r.cifras_texto
    assert ("nombren la provincia" in r.revisar) is salta


def test_a_sin_codigo_postal_esa_parte_no_se_aplica(pg, conc, crit):
    campana(pg, "C", leads=100, citas=50, gasto=1_000)
    r = juicio(pg, conc, crit, "C").reglas["a"]
    assert r.estado == "no" and "fuera de zona" not in r.cifras_texto


def test_se_juzga_desde_el_segundo_dia(pg, conc, crit):
    campana(pg, "C1", dias=1, leads=100, citas=0, gasto=100_000)
    campana(pg, "C2", dias=2, leads=100, citas=0, gasto=100_000)
    _, js = cr.juzgar(pg, HOY, "real", conc, crit, {})
    d = {j.campana_id: j for j in js}
    assert d["C1"].decision == "sin_juzgar" and d["C1"].reglas == {}
    assert d["C2"].decision == "revisar" and d["C2"].reglas["a"].activa       # 0 citas de 100 leads


# ================================================================== regla b

@pytest.mark.parametrize("clics, salta", [(99, True), (100, False)])
def test_b_tasa_de_clic_en_el_borde_del_1(pg, conc, crit, clics, salta):
    # 99 de 10 000 impresiones = 0,99 %: el anuncio no atrae. 100 = 1 %: no salta.
    campana(pg, "C", leads=clics, citas=clics, impresiones=10_000, clics=clics, gasto=100)
    r = juicio(pg, conc, crit, "C").reglas["b"]
    assert r.activa is salta
    assert f"tasa de clic: {clics} de 10000 impresiones = {'0,99' if salta else '1'} % (umbral 1 %)" in r.cifras_texto
    assert ("no_atrae" in r.cifras["senales"]) is salta
    assert ("El anuncio no atrae: revisar la creatividad (reel, foto) y el mensaje." in r.revisar) is salta
    if salta:
        assert r.decision == "revisar" and "no convence" not in r.revisar


@pytest.mark.parametrize("leads, salta", [(299, True), (300, False)])
def test_b_tasa_de_clic_a_lead_en_el_borde_del_3(pg, conc, crit, leads, salta):
    # 299 leads de 10 000 clics = 2,99 %: atrae pero no convence. 300 = 3 %: no salta. Tasa de clic, 10 %.
    campana(pg, "C", leads=leads, citas=leads, impresiones=100_000, clics=10_000, gasto=100)
    r = juicio(pg, conc, crit, "C").reglas["b"]
    assert r.activa is salta
    assert f"clic a lead: {leads} de 10000 clics = {'2,99' if salta else '3'} % (umbral 3 %)" in r.cifras_texto
    assert r.cifras["senales"] == (["no_convence"] if salta else [])
    assert ("Atrae pero no convence: revisar la llamada a la acción, la oferta y el formulario." == r.revisar) is salta


def test_b_las_dos_senales_a_la_vez(pg, conc, crit):
    campana(pg, "C", leads=1, citas=1, impresiones=10_000, clics=50, gasto=100)    # 0,5 % y 2 %
    r = juicio(pg, conc, crit, "C").reglas["b"]
    assert r.activa and r.cifras["senales"] == ["no_atrae", "no_convence"]
    assert "El anuncio no atrae" in r.revisar and "Atrae pero no convence" in r.revisar


def test_b_sin_clics_salta_por_tasa_de_clic_y_no_juzga_clic_a_lead(pg, conc, crit):
    campana(pg, "C", leads=0, impresiones=10_000, clics=0, gasto=100)
    r = juicio(pg, conc, crit, "C").reglas["b"]
    assert r.activa and r.cifras["senales"] == ["no_atrae"] and "clic a lead" not in r.cifras_texto


@pytest.mark.parametrize("impresiones, salta", [(999, False), (1_000, True)])
def test_b_tasa_de_clic_necesita_1000_impresiones(pg, conc, crit, impresiones, salta):
    # 0 clics: tasa de clic 0 %. Con 999 impresiones se muestra la cifra pero no salta; con 1.000, salta.
    campana(pg, "C", leads=0, impresiones=impresiones, clics=0, gasto=100)
    r = juicio(pg, conc, crit, "C").reglas["b"]
    assert r.activa is salta
    assert f"tasa de clic: 0 de {impresiones} impresiones = 0 % (umbral 1 %)" in r.cifras_texto
    assert r.cifras["senales"] == (["no_atrae"] if salta else [])
    if not salta:
        assert r.estado == "sin_volumen" and r.decision is None and r.cifras["senales_sin_volumen"] == ["no_atrae"]
        assert "sin volumen: 999 impresiones, mínimo 1000" in r.cifras_texto
        assert juicio(pg, conc, crit, "C").decision == "continuar"
    assert crit["b"]["n_minimo"] == {"impresiones": 1000, "clics": 50}


@pytest.mark.parametrize("clics, salta", [(49, False), (50, True)])
def test_b_clic_a_lead_necesita_50_clics(pg, conc, crit, clics, salta):
    # 0 leads de 49 o 50 clics (0 %), con la tasa de clic sobre su umbral (clics de 1.000 impresiones).
    campana(pg, "C", leads=0, impresiones=1_000, clics=clics, gasto=100)
    r = juicio(pg, conc, crit, "C").reglas["b"]
    assert r.activa is salta
    assert f"clic a lead: 0 de {clics} clics = 0 % (umbral 3 %)" in r.cifras_texto
    assert r.cifras["senales"] == (["no_convence"] if salta else [])
    assert ("sin volumen: 49 clics, mínimo 50" in r.cifras_texto) is not salta
    if not salta:
        assert r.estado == "sin_volumen" and r.revisar == ""


@pytest.mark.parametrize("leads, salta", [(40, True), (60, False)])
def test_b_claramente_por_debajo_del_mismo_modelo_y_mes(pg, conc, crit, leads, salta):
    # Noviembre de 2025, mismo modelo: 10 % de leads por impresión. Ahora, 4 % o 6 %, con tasa de clic y de
    # clic a lead sobre sus umbrales; «claramente por debajo» es menos de la mitad (factor 0,5): 4 % salta, 6 % no.
    campana(pg, "ANT", leads=100, citas=50, impresiones=1_000, hasta=date(2025, 11, 15), gasto=100)
    campana(pg, "C", leads=leads, citas=leads, impresiones=1_000, clics=200, gasto=100)
    r = juicio(pg, conc, crit, "C").reglas["b"]
    assert r.activa is salta
    assert r.cifras["senales"] == (["historico"] if salta else [])
    assert (f"leads por impresión: {leads // 10} % frente a 10 % de las campañas de Golf en noviembre de años "
            "anteriores (n = 1 campañas; umbral 50 % de esa cifra)") in r.cifras_texto
    assert ("Por debajo de campañas del mismo modelo y mes" in r.revisar) is salta


# ================================================================== regla c

def _referencia(pg, gasto=10_000, leads=100, citas=10):
    """Otra campaña de los últimos 90 días: coste por cita medio de 10 € (hasta 12 € con la tolerancia)."""
    campana(pg, "REF", dias=10, leads=leads, citas=citas, gasto=gasto, hasta=HOY - timedelta(days=1), modelo="Polo")


@pytest.mark.parametrize("gasto, salta", [(1_001, True), (1_000, False)])
def test_c_coste_por_lead_en_el_borde_de_10(pg, conc, crit, gasto, salta):
    _referencia(pg)
    campana(pg, "C", leads=1, citas=1, gasto=gasto)
    r = juicio(pg, conc, crit, "C").reglas["c"]
    assert r.activa is salta
    assert f"coste por lead: {'10,01' if salta else '10'} € / 1 leads = {'10,01' if salta else '10'} € (umbral 10 €)" \
        in r.cifras_texto


def test_c_continuar_si_el_coste_por_cita_esta_en_su_media_y_si_no_parar(pg, conc, crit):
    _referencia(pg)
    campana(pg, "EN_MEDIA", leads=1, citas=1, gasto=1_200)        # 12 € por cita: el límite de la media
    j = juicio(pg, conc, crit, "EN_MEDIA")
    assert j.reglas["c"].decision == "continuar" and j.decision != "parar"
    assert "coste por cita: 12 € frente a su media de 10 € (hasta 12 €)" in j.reglas["c"].cifras_texto


def test_c_parar_si_el_coste_por_cita_no_esta_en_su_media(pg, conc, crit):
    _referencia(pg)
    campana(pg, "CARA", leads=1, citas=1, gasto=1_201)
    j = juicio(pg, conc, crit, "CARA")
    assert j.reglas["c"].decision == "parar" and j.decision == "parar"
    assert "Parar y revisar audiencias y creatividad." == j.reglas["c"].revisar


# ================================================================== regla d

def _d(pg, citas, ventas, modelo="Golf"):
    # Coste por lead 0,50 € y por cita 5 €: igual que la referencia, así que «en su media»
    campana(pg, "REF", dias=10, leads=1_000, citas=100, gasto=50_000, hasta=HOY - timedelta(days=1), modelo="Polo")
    campana(pg, "C", leads=citas * 10, citas=citas, ventas=ventas, gasto=citas * 500, impresiones=citas * 100,
            modelo=modelo)


@pytest.mark.parametrize("ventas, salta", [(32, True), (33, False)])
def test_d_tasa_de_venta_en_el_borde_del_33(pg, conc, crit, ventas, salta):
    _d(pg, 100, ventas)
    r = juicio(pg, conc, crit, "C").reglas["d"]
    assert r.activa is salta
    assert f"tasa de venta: {ventas} de 100 citas = {ventas} % (umbral 33 %)" in r.cifras_texto


@pytest.mark.parametrize("citas, estado", [(5, "sin_volumen"), (6, "activa")])
def test_d_necesita_6_citas(pg, conc, crit, citas, estado):
    _d(pg, citas, 0)
    assert juicio(pg, conc, crit, "C").reglas["d"].estado == estado


def test_d_cita_los_similares_de_la_zona_cercana(pg, conc, crit):
    _d(pg, 100, 10)
    segmento(pg, "modelo", "Golf", 4)
    segmento(pg, "motor", "Golf", 4, acabado="Life", motor_="TSI 116 CV", n_p=4, p_p=24_000, n_c=12, p_c=23_500)
    modelos_e = cr.regla_e(pg, HOY, conc, crit)
    r = juicio(pg, conc, crit, "C", modelos_e).reglas["d"]
    assert r.activa and "Life TSI 116 CV: 12 de otros concesionarios a 23.500 €, el nuestro a 24.000 €" in r.revisar


# ================================================================== regla f

def test_f_cansancio_en_el_borde(pg, conc, crit):
    # 7 días a 10 leads y 3 días a 7: −30 % justo (salta: «al menos un 30 %»); coste por lead de esos
    # 3 días, 10,01 € (salta: «por encima de 10 €»)
    gasto = [100] * 7 + [7_007] * 3
    campana(pg, "C", dias=10, leads_por_dia=[10] * 7 + [7] * 3, gasto_por_dia=gasto, citas=30)
    r = juicio(pg, conc, crit, "C").reglas["f"]
    assert r.activa and "leads por día: 7 en los últimos 3 días frente a 10 en los 7 anteriores (−30 %" in r.cifras_texto
    assert "coste por lead de esos 3 días: 10,01 € (umbral 10 €)" in r.cifras_texto


@pytest.mark.parametrize("ultimos, gasto_dia", [([8] * 3, 8_008), ([7] * 3, 7_000)])
def test_f_no_salta(pg, conc, crit, ultimos, gasto_dia):
    # −20 % (aunque el lead sea caro), o −30 % con el lead a 10 € justos
    campana(pg, "C", dias=10, leads_por_dia=[10] * 7 + ultimos, gasto_por_dia=[100] * 7 + [gasto_dia] * 3, citas=30)
    assert not juicio(pg, conc, crit, "C").reglas["f"].activa


# ================================================================== regla e

def test_e_mismo_modelo_acabado_y_motor_en_la_zona_cercana(pg, conc, crit):
    segmento(pg, "modelo", "Golf", 3)
    segmento(pg, "motor", "Golf", 3, acabado="Life", motor_="TSI 116 CV", n_p=3, p_p=24_000, n_c=8, p_c=25_000)
    # Más barato, pero en otro acabado, en otro motor o fuera de la zona cercana: no cuenta
    segmento(pg, "motor", "Golf", 0, acabado="Style", motor_="TSI 116 CV", n_c=20, p_c=18_000)
    segmento(pg, "motor", "Golf", 0, acabado="Life", motor_="TDI 150 CV", n_c=20, p_c=18_000)
    segmento(pg, "motor", "Golf", 3, acabado="Life", motor_="TSI 116 CV", n_p=3, p_p=24_000, n_c=30, p_c=20_000,
             zona="comunidad")
    m = cr.regla_e(pg, HOY, conc, crit)[("VN", "Golf")]
    assert m.resultado.decision == "lanzar"
    assert "Life TSI 116 CV: nuestro 24.000 € frente a 25.000 € (n = 8)" in m.resultado.cifras_texto
    assert [(s["acabado"], s["motor"]) for s in m.segmentos] == [("Life", "TSI 116 CV")]


@pytest.mark.parametrize("unidades, decision", [(2, "no_lanzar"), (3, "lanzar")])
def test_e_al_menos_3_unidades(pg, conc, crit, unidades, decision):
    segmento(pg, "modelo", "Polo", unidades)
    segmento(pg, "motor", "Polo", unidades, acabado="Life", motor_="TSI 95 CV", n_p=unidades, p_p=15_000, n_c=8,
             p_c=16_000)
    m = cr.regla_e(pg, HOY, conc, crit)[("VN", "Polo")]
    assert m.resultado.decision == decision and f"{unidades} unidades propias en la zona cercana (mínimo 3)" in \
        m.resultado.cifras_texto


@pytest.mark.parametrize("nuestro, decision", [(24_999, "lanzar"), (25_000, "no_lanzar")])
def test_e_precio_por_debajo_de_la_mediana_de_la_competencia(pg, conc, crit, nuestro, decision):
    segmento(pg, "modelo", "Golf", 5)
    segmento(pg, "motor", "Golf", 5, acabado="Life", motor_="TSI 116 CV", n_p=5, p_p=nuestro, n_c=8, p_c=25_000)
    assert cr.regla_e(pg, HOY, conc, crit)[("VN", "Golf")].resultado.decision == decision


def test_e_sin_competencia_comparable_no_se_lanza_y_se_dice(pg, conc, crit):
    segmento(pg, "modelo", "Golf", 5)
    segmento(pg, "motor", "Golf", 5, acabado="Life", motor_="TSI 116 CV", n_p=5, p_p=20_000,
             n_c=conc.n_minimo_medianas - 1, p_c=25_000)
    r = cr.regla_e(pg, HOY, conc, crit)[("VN", "Golf")].resultado
    assert r.estado == "sin_dato" and r.decision == "no_lanzar" and "sin competencia comparable" in r.cifras_texto


# ================================================================== subir presupuesto

def _buena(pg, *, citas=10, ventas=4, leads=100, gasto=49_000, impresiones=1_000, cid="C"):
    """Coste por lead 4,90 €, por cita 49 €, lead a cita 10 %, tasa de venta 40 %, por venta 122,50 €."""
    campana(pg, cid, leads=leads, citas=citas, ventas=ventas, gasto=gasto, impresiones=impresiones)


def _golf_lanzable(pg):
    segmento(pg, "modelo", "Golf", 5)
    segmento(pg, "motor", "Golf", 5, acabado="Life", motor_="TSI 116 CV", n_p=5, p_p=20_000, n_c=8, p_c=25_000)


def test_subir_presupuesto_si_se_cumple_todo(pg, conc, crit):
    _golf_lanzable(pg)
    _buena(pg)
    j = juicio(pg, conc, crit, "C", cr.regla_e(pg, HOY, conc, crit))
    assert j.decision == "subir" and j.reglas["subir"].activa
    texto = j.reglas["subir"].cifras_texto
    for parte in ("coste por lead 4,9 € (< 10 €)", "por cita 49 € (< 100 €)", "lead a cita 10 % (> 5 %; n = 100 leads)",
                  "tasa de venta 40 % (> 33 %; n = 10 citas)", "coste por venta 122,5 € (< 400 €)", "regla e: 5 unidades"):
        assert parte in texto, parte


@pytest.mark.parametrize("cambio", ["lead_cita_5", "venta_33", "pocas_citas", "sin_e", "cpv_400"])
def test_subir_no_sale_en_el_borde(pg, conc, crit, cambio):
    if cambio != "sin_e":
        _golf_lanzable(pg)
    kw = {"lead_cita_5": dict(citas=5, ventas=2, gasto=20_000),      # 5 % justo (y 40 %): no «por encima»
          "venta_33": dict(citas=12, ventas=4, gasto=49_000),         # 33,33 %: sí > 33; probamos 33 abajo
          "pocas_citas": dict(citas=5, ventas=5, gasto=20_000, leads=50),   # menos de 6 citas
          "sin_e": {}, "cpv_400": dict(citas=10, ventas=1, gasto=40_000)}[cambio]   # 400 € por venta justos
    if cambio == "venta_33":
        kw = dict(citas=100, ventas=33, leads=1_000, gasto=490_000, impresiones=10_000)
    _buena(pg, **kw)
    j = juicio(pg, conc, crit, "C", cr.regla_e(pg, HOY, conc, crit))
    assert not j.reglas["subir"].activa and j.decision != "subir"


def test_subir_no_sale_con_una_regla_de_revision_activa(pg, conc, crit):
    _golf_lanzable(pg)
    _buena(pg, impresiones=1_000_000)     # 1 000 clics en 1 000 000 de impresiones: 0,1 %, regla b activa
    j = juicio(pg, conc, crit, "C", cr.regla_e(pg, HOY, conc, crit))
    assert j.reglas["b"].activa and not j.reglas["subir"].activa and j.decision == "revisar"
    assert "reglas activas: b" in j.reglas["subir"].cifras_texto


# ================================================================== el motor: alertas, decisión y recomendación

def test_motor_alertas_decision_y_recomendacion(pg, conc):
    _golf_lanzable(pg)
    segmento(pg, "modelo", "Polo", 2)                                        # sin stock: no lanzar
    segmento(pg, "modelo", "Taigo", 9)                                       # lanzable, más stock que el Golf
    segmento(pg, "motor", "Taigo", 9, acabado="Life", motor_="TSI 116 CV", n_p=9, p_p=19_000, n_c=8, p_c=21_000)
    _buena(pg, cid="BUENA")
    campana(pg, "MALA", leads=40, citas=1, gasto=50_000, modelo="T-Roc", titulo="T-Roc noviembre")   # a y c
    motor.evaluar(pg, HOY, conc, reglas(*META))
    a = {(x["regla"], x["clave"]): x for x in alertas(pg)}
    assert set(a) >= {("meta_a", "MALA"), ("meta_c", "MALA"), ("meta_subir", "BUENA")}
    assert {x["rol"] for x in a.values()} == {"marketing"}
    assert a[("meta_a", "MALA")]["texto"].startswith(
        "Campaña «T-Roc noviembre» · revisar (regla a): lead a cita: 1 de 40 = 2,5 % (umbral 5 %). Revisar: ")
    assert a[("meta_a", "MALA")]["cifras"]["n_minimo_leads"] == 20 and a[("meta_a", "MALA")]["cifras"]["leads"] == 40
    assert a[("meta_subir", "BUENA")]["texto"].startswith("Campaña «BUENA» · subir presupuesto: coste por lead 4,9 €")
    decisiones = dict(pg.execute("SELECT campana_id, decision FROM panel_campana_decision").fetchall())
    assert decisiones == {"BUENA": "subir", "MALA": "parar"}
    reco = {m: (a_, r, p) for m, a_, r, p in pg.execute(
        "SELECT modelo, accion, regla, prioridad FROM panel_recomendacion_modelo WHERE origen = 'real'")}
    assert reco["Golf"][0] == "aumentar"
    assert reco["T-Roc"][0] == "parar" and "c" in reco["T-Roc"][1]
    assert reco["Polo"] == ("no_lanzar", "e", None)
    assert reco["Taigo"] == ("lanzar", "e", 1)                               # el de más stock, primero
    motivo = pg.execute("SELECT motivo FROM panel_recomendacion_modelo WHERE modelo = 'Polo'").fetchone()[0]
    assert motivo == "No lanzar (regla e): 2 unidades propias en la zona cercana (mínimo 3)."


def test_sin_filas_reales_de_meta_y_walcu_las_reglas_de_campana_estan_inactivas(pg, conc):
    _golf_lanzable(pg)
    resumen = motor.evaluar(pg, HOY, conc, reglas(*META))
    assert all(t.startswith("inactiva") for t in resumen["reglas"].values())
    assert pg.execute("SELECT count(*) FROM panel_campana_decision").fetchone() == (0,)
    # La regla e no depende de Meta ni de Walcu: la recomendación sale con el mercado
    assert pg.execute("SELECT modelo, accion FROM panel_recomendacion_modelo").fetchall() == [("Golf", "lanzar")]


def test_marketing_muestra_la_decision_y_la_recomendacion(pg, panel_url, conc):
    from panel import usuarios
    from test_contrato_panel import app_con, bloques
    from test_panel import dentro
    usuarios.alta(pg, "tecnico", "T", "tecnico", "clave-temporal-123")
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    _golf_lanzable(pg)
    _referencia(pg)
    campana(pg, "MALA", leads=40, citas=1, gasto=50_000, modelo="T-Roc", titulo="T-Roc noviembre")
    motor.evaluar(pg, HOY, conc, reglas(*META))
    b = bloques(dentro(app_con(panel_url), "tecnico").get("/marketing").text)
    origen, camp = b["campanas"]
    assert origen == "real" and '<span class="chip chip-decision-parar">Parar</span>' in camp
    assert "Regla a: lead a cita: 1 de 40 = 2,5 % (umbral 5 %)" in camp
    origen, reco = b["recomendacion"]
    assert origen == "real" and '<span class="reco-titulo">Lanzar</span>' in reco and "Lanzar (regla e)" in reco


# ================================================================== zona cercana en Mercado

def test_zona_cercana_en_el_precalculo_y_en_mercado(pg, datos, panel_url, conc):
    from analitica import mercado as am
    from panel import usuarios
    from test_contrato_panel import app_con
    from test_panel import HOY as DIA, _con_datos, dentro
    _con_datos(pg, datos)
    for tipo, fuentes in am.TIPOS.items():
        directo = pg.execute(
            """SELECT count(*) FROM stock_externo_snapshot s WHERE snapshot_day = %s AND fuente = ANY(%s)
               AND provincia_codigo = ANY(%s)""", (DIA, list(fuentes), list(conc.zona_cercana))).fetchone()[0]
        fila = pg.execute("SELECT anuncios, zona_nombre FROM panel_mercado_dia WHERE dia = %s AND tipo = %s "
                          "AND zona = 'cercana'", (DIA, tipo)).fetchone()
        assert fila == (directo, conc.zona_cercana_nombre) and directo > 0
        prov, com = (pg.execute("SELECT anuncios FROM panel_mercado_dia WHERE dia = %s AND tipo = %s AND zona = %s",
                                (DIA, tipo, z)).fetchone()[0] for z in ("provincia", "comunidad"))
        assert prov < directo <= com           # las cercanas de esta configuración son de la misma comunidad
    # Nuestro precio y el de la competencia, por separado, en cada segmento
    assert pg.execute("""SELECT count(*) FROM panel_mercado_segmento WHERE zona = 'cercana'
                         AND n_precio_propios + n_precio_competencia <> n_precio""").fetchone() == (0,)
    usuarios.alta(pg, "ventas", "V", "ventas", "clave-temporal-123")
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    html = dentro(app_con(panel_url), "ventas").get("/mercado?zona=cercana&tipo=VO").text
    assert re.search(r'aria-current="true">Zona cercana</a>', html)
    assert f"Zona cercana: {conc.zona_cercana_nombre}." in html


def test_c_sin_media_de_coste_por_cita_se_revisa(pg, conc, crit):
    campana(pg, "C", leads=10, citas=5, gasto=20_000)           # 20 € por lead, ninguna otra campaña
    j = juicio(pg, conc, crit, "C")
    assert j.reglas["c"].decision == "revisar" and j.decision == "revisar"
    assert j.reglas["c"].revisar.startswith("Sin media de coste por cita")


def test_migracion_0010_ida_y_vuelta(pg_url, pg):
    from alembic import command
    from alembic.config import Config
    from sinteticos import RAIZ
    cfg = Config(str(RAIZ / "alembic.ini"))
    segmento(pg, "modelo", "Golf", 3)
    segmento(pg, "modelo", "Golf", 3, zona="provincia")
    command.downgrade(cfg, "0009")
    assert pg.execute("SELECT zona FROM panel_mercado_segmento").fetchall() == [("provincia",)]
    assert pg.execute("SELECT to_regclass('panel_campana_decision')").fetchone() == (None,)
    command.upgrade(cfg, "head")
    segmento(pg, "modelo", "Golf", 3)
    with pytest.raises(Exception):
        pg.execute("""INSERT INTO panel_leads_dia (dia, origen, modelo, tipo, campana_id, leads, citas, ventas,
                      leads_cp_zona, leads_cp_fuera) VALUES (%s, 'real', 'Golf', 'VN', 'C', 5, 0, 0, 4, 2)""", (HOY,))
