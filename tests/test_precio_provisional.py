"""Precio provisional de nuestros VO (analitica/precio.py, panel/precio.py, tabla panel_precio_vo).

Cada regla con sus bordes, la ampliación del segmento, «sin comparables suficientes», «al menos N días»,
que nuestros coches no cuentan como comparables, los datos de ejemplo y el bloque en Ventas y Dirección
(y no en Marketing). Sin red: Postgres local, capturas sintéticas escritas por el código real del scraper
y el TestClient.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from analitica import mercado as am
from analitica import precio as pv
from ingesta import scrapers_vw as sv
from conftest import sin_pausas_texto
from ingesta.concesionario import ErrorConfiguracion, cargar as cargar_concesionario
from panel import ejemplo
from sinteticos import RAIZ, capturar, coche
from test_contrato_panel import ETIQUETA, a_real, app_con, bloques, con_ejemplo, conc, personas  # noqa: F401
from test_panel import HOY, dentro

REGLAS = pv.Reglas(min_comparables=5, min_comparables_amplio=3, dias_muy_por_encima=30, dias_por_encima=60,
                   dias_en_precio=90)
AVISO = "Recomendación provisional basada en el mercado actual; no es una probabilidad de venta."


def _coche(precio=2_000_000, *, modelo="Golf", acabado="Life", motor="TSI 150 CV · Gasolina", ant=0, km=0,
           cercana=True, comunidad=True):
    return pv.Coche(modelo, acabado, motor, ant, km, cercana, comunidad, precio)


def _comp(p25=1_900_000, mediana=2_000_000, p75=2_100_000, nivel="segmento"):
    return pv.Comparacion(nivel, "cercana", 7, p25, mediana, p75)


# ================================================================== configuración

def test_umbrales_salen_de_la_configuracion():
    r = pv.cargar_reglas()
    assert r == REGLAS                                   # los de config/concesionario.yaml
    texto = (RAIZ / "analitica/precio.py").read_text()
    for numero in ("30", "60", "90"):                    # ningún umbral de días escrito en el código
        assert not re.search(rf"\b{numero}\b", texto), numero


def test_configuracion_ausente_o_mal_escrita(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("propio: {}\n")
    assert pv.cargar_reglas(f) is None                   # sin la sección, no se calcula
    f.write_text("precio_provisional: {min_comparables: 5, min_comparables_amplio: 3, dias_muy_por_encima: 30, "
                 "dias_por_encima: 60}\n")
    with pytest.raises(ErrorConfiguracion, match="dias_en_precio"):
        pv.cargar_reglas(f)
    f.write_text("precio_provisional: {min_comparables: 3, min_comparables_amplio: 5, dias_muy_por_encima: 30, "
                 "dias_por_encima: 60, dias_en_precio: 90}\n")
    with pytest.raises(ErrorConfiguracion, match="min_comparables_amplio"):
        pv.cargar_reglas(f)


# ================================================================== reglas

@pytest.mark.parametrize("precio, dias, regla", [
    # 1. Muy por encima: > p75 y 30 días o más
    (2_100_001, 30, "muy_por_encima"),
    (2_300_000, 75, "muy_por_encima"),                   # también cumple la 2: gana la 1
    (2_100_000, 30, "en_linea"),                         # en el p75 no está por encima (y < 60 días)
    (2_100_001, 29, "en_linea"),
    # 2. Por encima y parado: > mediana y 60 días o más
    (2_000_001, 60, "por_encima_parado"),
    (2_100_000, 89, "por_encima_parado"),
    (2_000_001, 59, "en_linea"),
    # 3. En precio pero no sale: ≤ mediana y 90 días o más
    (2_000_000, 90, "en_precio_no_sale"),
    (1_500_000, 120, "en_precio_no_sale"),               # también barato: gana la 3
    (2_000_000, 89, "en_linea"),
    # 4. Barato: < p25
    (1_899_999, 0, "barato"),
    (1_900_000, 10, "en_linea"),                         # en el p25 no está por debajo
    # 5. En línea
    (2_050_000, 45, "en_linea"),
])
def test_cada_regla_y_sus_bordes(precio, dias, regla):
    assert pv.decidir(precio, dias, _comp(), REGLAS) == regla


def test_sin_precio_y_sin_comparables_no_recomiendan():
    assert pv.decidir(None, 200, _comp(), REGLAS) == "sin_precio"
    assert pv.decidir(3_000_000, 200, pv.Comparacion(None, None, 2), REGLAS) == "sin_comparables"


def test_textos_de_cada_regla():
    conc = cargar_concesionario()
    c = _comp(p25=1_875_049, mediana=1_999_950)
    t = lambda regla, comp=c: pv.texto(regla, comp, _coche(), conc)  # noqa: E731
    assert t("muy_por_encima") == "Muy por encima del mercado: bajar a la mediana (20.000 €)"
    assert t("por_encima_parado") == "Plantéate bajar a la mediana (20.000 €)"
    assert t("en_precio_no_sale") == "El precio está en mercado: revisa fotos, anuncio y leads"
    assert t("barato") == "Por debajo del mercado (p25: 18.750 €): si tiene interés, hay margen"
    assert t("en_linea") == "En línea con el mercado"
    assert t("sin_comparables", pv.Comparacion(None, None, 1)) == "Sin comparables suficientes"
    assert t("sin_precio") == "Sin precio publicado: sin recomendación"


@pytest.mark.parametrize("regla, orden", [("muy_por_encima", 1), ("por_encima_parado", 1), ("en_precio_no_sale", 2),
                                          ("barato", 3), ("en_linea", 4), ("sin_comparables", 4), ("sin_precio", 4)])
def test_orden_de_la_tabla(regla, orden):
    assert pv.ORDEN[regla] == orden


# ================================================================== segmento y ampliación

def _otros(n, precio=1_800_000, **kw):
    return [_coche(None if precio is None else precio + 50_000 * i, **kw) for i in range(n)]


def test_segmento_completo_con_cinco():
    c = pv.comparar(_coche(), _otros(5) + _otros(9, km=1) + _otros(9, cercana=False), REGLAS)
    assert (c.nivel, c.zona, c.n) == ("segmento", "cercana", 5)
    assert (c.p25, c.mediana, c.p75) == (1_850_000, 1_900_000, 1_950_000)     # percentile_cont, como Mercado


def test_se_amplia_sin_km_luego_sin_antiguedad_luego_comunidad():
    nuestro = _coche()
    # 4 en el segmento: se quita el tramo de km
    c = pv.comparar(nuestro, _otros(4) + _otros(1, km=2), REGLAS)
    assert (c.nivel, c.n) == ("sin_km", 5)
    # 4 con la misma antigüedad: se quita también el tramo de antigüedad
    c = pv.comparar(nuestro, _otros(4) + _otros(1, ant=1, km=1), REGLAS)
    assert (c.nivel, c.n) == ("sin_antiguedad", 5)
    # 4 en la zona cercana: se amplía a la comunidad
    c = pv.comparar(nuestro, _otros(4) + _otros(1, ant=2, cercana=False), REGLAS)
    assert (c.nivel, c.zona, c.n) == ("comunidad", "comunidad", 5)
    # En el nivel más amplio bastan 3
    c = pv.comparar(nuestro, _otros(2) + _otros(1, cercana=False), REGLAS)
    assert (c.nivel, c.n) == ("comunidad", 3)
    # Fuera de la comunidad y de la zona cercana no cuentan nunca
    c = pv.comparar(nuestro, _otros(2) + _otros(9, cercana=False, comunidad=False), REGLAS)
    assert (c.nivel, c.n) == (None, 2)


def test_la_ampliacion_se_dice_en_la_recomendacion():
    conc = cargar_concesionario()
    for nivel, cola in (("segmento", "En línea con el mercado"),
                        ("sin_km", "En línea con el mercado. Comparado sin el tramo de km"),
                        ("sin_antiguedad", "En línea con el mercado. Comparado sin los tramos de antigüedad ni de km"),
                        ("comunidad", f"En línea con el mercado. Comparado sin tramos y ampliado a {conc.comunidad}")):
        assert pv.texto("en_linea", _comp(nivel=nivel), _coche(), conc) == cola


def test_sin_comparables_suficientes():
    c = pv.comparar(_coche(), _otros(2) + _otros(5, modelo="Polo") + _otros(5, acabado="GTI")
                    + _otros(5, motor="TDI 150 CV · Diésel") + _otros(3, precio=None), REGLAS)
    assert (c.nivel, c.n, c.mediana) == (None, 2, None)
    assert pv.decidir(2_000_000, 200, c, REGLAS) == "sin_comparables"


def test_sin_acabado_o_motor_no_se_compara():
    conc = cargar_concesionario()
    for nuestro, motivo in ((_coche(acabado=None), "acabado sin clasificar"), (_coche(motor=None), "sin dato de motor")):
        c = pv.comparar(nuestro, _otros(20, acabado=None, motor=None) + _otros(20), REGLAS)
        assert c.nivel is None
        assert pv.texto("sin_comparables", c, nuestro, conc) == f"Sin comparables suficientes: {motivo}"


def test_sin_km_o_sin_matriculacion_empieza_en_el_nivel_que_se_puede():
    c = pv.comparar(_coche(km=None), _otros(9), REGLAS)
    assert c.nivel == "sin_km"
    c = pv.comparar(_coche(ant=None), _otros(9), REGLAS)
    assert c.nivel == "sin_antiguedad"


# ================================================================== precálculo con capturas

D0 = date(2026, 10, 1)
PERMISIVO = {"max_count_drop_fraction": 0.99, "max_missing_fraction": 0.99}


VERSION = "Volkswagen Golf Life 1.5 TSI 110 kW (150 CV)"      # acabado y motor del catálogo


def _vo(vid, precio, dealer="ESP00217", cp="41001", **kw):
    return coche(vid, dealer_id=dealer, dealer=f"Concesionario {dealer}", precio=precio, cp=cp, version=VERSION,
                 **kw)


def test_precalculo_nuestros_no_cuentan_y_al_menos_n_dias(pg, datos):
    conc = cargar_concesionario()
    propio = conc.dealer_ids[0]
    otros = [_vo(f"ESP{i:09d}", 2_000_000 + 100_000 * i) for i in range(5)]       # mediana 22.000 €
    lejos = [_vo("ESP900000001", 9_000_000, cp="28001")]                           # fuera de la comunidad
    viejo = _vo("ESP700000001", 3_000_000, dealer=propio)                          # desde el primer día de datos
    gemelo = _vo("ESP700000002", 2_600_000, dealer=propio)                         # nuestro, mismo segmento
    nuevo = _vo("ESP700000003", 1_500_000, dealer=propio)                          # aparece el segundo día
    capturar(datos, "vo", D0.isoformat(), otros + lejos + [viejo, gemelo], PERMISIVO)
    dia = D0 + timedelta(days=40)
    for n in range(1, 41):
        d = (D0 + timedelta(days=n)).isoformat()
        capturar(datos, "vo", d, [_vo(c["vehicle_id"], c["business"]["price_retail_cents"],
                                      dealer=c["business"]["dealer_id"], cp=c["business"]["dealer_postcode"])
                                  for c in otros + lejos + [viejo, gemelo, nuevo]], PERMISIVO)
    sv.cargar(pg, dia=dia)
    resumen = am.precalcular(pg, desde=dia, hasta=dia, conc=conc)
    assert resumen["filas_precio_vo"] == 3
    filas = {r[0]: r[1:] for r in pg.execute(
        """SELECT id_anuncio, n_comparables, mediana_cents, dias_publicado, dias_cota, regla, nivel, origen
           FROM panel_precio_vo WHERE dia = %s""", (dia,))}
    assert set(filas) == {"ESP700000001", "ESP700000002", "ESP700000003"}           # solo los nuestros
    for n, mediana, *_ , origen in filas.values():
        assert (n, mediana, origen) == (5, 2_200_000, "real")    # ni nuestros coches ni el de fuera cuentan
    assert filas["ESP700000001"][2:6] == (40, True, "muy_por_encima", "segmento")   # «al menos 40 días»
    assert filas["ESP700000002"][2:5] == (40, True, "muy_por_encima")
    assert filas["ESP700000003"][2:5] == (39, False, "barato")                      # lo vimos aparecer
    # Recalcular el día lo sustituye, y las filas de ejemplo no se tocan
    pg.execute("INSERT INTO panel_precio_vo (dia, origen, fuente, id_anuncio, modelo, dias_publicado, dias_cota, "
               "n_comparables, regla, orden, recomendacion) VALUES (%s, 'ejemplo', 'vo', 'EJ', 'Golf', 1, false, 0, "
               "'sin_comparables', 4, 'x')", (dia,))
    am.precalcular(pg, desde=dia, hasta=dia, conc=conc)
    assert pg.execute("SELECT origen, count(*) FROM panel_precio_vo GROUP BY 1 ORDER BY 1").fetchall() == \
        [("ejemplo", 1), ("real", 3)]


def test_sin_configuracion_el_precalculo_sigue(pg, datos, tmp_path, monkeypatch):
    f = tmp_path / "concesionario.yaml"
    texto = sin_pausas_texto((RAIZ / "config/concesionario.yaml").read_text())
    f.write_text(texto.split("precio_provisional:")[0] + "modelos:" + texto.split("\nmodelos:")[1])
    monkeypatch.setenv("CDF_CONCESIONARIO", str(f))
    conc = cargar_concesionario()
    capturar(datos, "vo", D0.isoformat(), [_vo("ESP700000001", 2_000_000, dealer=conc.dealer_ids[0])], PERMISIVO)
    sv.cargar(pg, dia=D0)
    resumen = am.precalcular(pg, hasta=D0, conc=conc)
    assert resumen["filas_precio_vo"] == 0 and "precio_vo" in resumen
    assert resumen["filas_modelo"] > 0


# ================================================================== datos de ejemplo

def test_ejemplo_coherente_con_las_reglas(conc):
    filas = ejemplo.generar(HOY, conc)["panel_precio_vo"]
    assert {f["regla"] for f in filas} == set(pv.ORDEN)                       # todas las reglas a la vista
    assert {f["nivel"] for f in filas} == {None, *(n for n, *_ in pv.NIVELES)}
    assert any(f["dias_cota"] for f in filas)
    for f in filas:
        comp = pv.Comparacion(f["nivel"], f["zona"], f["n_comparables"], f["p25_cents"], f["mediana_cents"],
                              f["p75_cents"])
        nuestro = pv.Coche(f["modelo"], f["acabado"], f["motor"], 0, 0, True, True, f["precio_cents"])
        if f["regla"] != "sin_comparables":
            assert pv.decidir(f["precio_cents"], f["dias_publicado"], comp, REGLAS) == f["regla"], f
        assert f["recomendacion"] == pv.texto(f["regla"], comp, nuestro, conc), f
        assert f["orden"] == pv.ORDEN[f["regla"]]


# ================================================================== panel

def _filas_html(trozo: str) -> list[tuple[str, str]]:
    return re.findall(r'<tr class="precio-[a-z]+" data-regla="([a-z_]+)">\s*<th scope="row" class="izq">\s*'
                      r'<span class="vehiculo-nombre">([^<]+)</span>', trozo)


def test_ventas_y_direccion_muestran_el_bloque_ordenado_y_marketing_no(pg, panel_url, personas, con_ejemplo):
    filas = ejemplo.generar(HOY, con_ejemplo)["panel_precio_vo"]
    app = app_con(panel_url)
    tec = dentro(app, "tecnico")
    for ruta in ("/ventas", "/ventas?tipo=VO", "/direccion"):
        origen, trozo = bloques(tec.get(ruta).text)["precio"]
        assert origen == "ejemplo" and ETIQUETA in trozo and "Precio de nuestros VO" in trozo, ruta
        assert AVISO in trozo
        reglas = [r for r, _ in _filas_html(trozo)]
        assert len(reglas) == len(filas)
        assert [pv.ORDEN[r] for r in reglas] == sorted(pv.ORDEN[r] for r in reglas)   # 1 y 2, 3, 4, resto
        assert "al menos 61" in trozo                                         # «al menos N días»
        for f in filas:
            assert f["id_anuncio"] in trozo
    assert "Precio de nuestros VO" not in tec.get("/marketing").text
    assert "precio" not in bloques(tec.get("/marketing").text)


def test_filtro_por_modelo(pg, panel_url, personas, con_ejemplo):
    filas = ejemplo.generar(HOY, con_ejemplo)["panel_precio_vo"]
    modelo = filas[0]["modelo"]
    tec = dentro(app_con(panel_url), "tecnico")
    for ruta in ("/ventas?tipo=VO&modelo_vo=", "/direccion?tipo=todo&modelo_vo="):
        html = tec.get(ruta + modelo).text
        _, trozo = bloques(html)["precio"]
        esperados = [f for f in filas if f["modelo"] == modelo]
        assert len(_filas_html(trozo)) == len(esperados) < len(filas)
        assert f"{len(esperados)} de {len(filas)} coches" in trozo
        assert f'aria-current="true">{modelo}</a>' in trozo
        # El filtro viaja en el selector de tipo
        assert f"tipo=VN&amp;modelo_vo={modelo}" in html or f"tipo=VN&modelo_vo={modelo}" in html
    # Un modelo que no está: todos
    _, trozo = bloques(tec.get("/ventas?modelo_vo=Inexistente").text)["precio"]
    assert len(_filas_html(trozo)) == len(filas)


def test_ventas_abierto_a_ventas_con_precio_real(pg, panel_url, personas, con_ejemplo):
    """Con las demás tablas de Ventas ya reales, el precio real deja la vista abierta al rol de ventas."""
    from panel import precio as pp
    a_real(pg, "panel_riesgo_vehiculo", "panel_curva_grupo", "panel_alertas", "panel_precio_vo", "panel_salidas_stock",
           "panel_stock_vehiculo")
    html = dentro(app_con(panel_url), "ventas").get("/ventas").text
    origen, trozo = bloques(html)["precio"]
    assert origen == "real" and ETIQUETA not in trozo and pp.AVISO in trozo
