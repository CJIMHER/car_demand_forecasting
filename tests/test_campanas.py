"""Campañas clasificadas por reglas sobre el nombre (config/campanas.yaml, ingesta/campanas.py): tipo
(modelo o general), ámbito (VN, VO o ambos) y modelo normalizado con el catálogo; «sin datos de mercado»
para un modelo que no capturamos; asignaciones a mano con prioridad; panel_campana_dia real y campañas
sin regla con su motivo; criterios de Meta y vista Marketing. Sin red.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from alertas import criterios as cr
from alertas import motor
from ingesta import campanas as cmp
from ingesta.concesionario import cargar as cargar_concesionario

HOY = date(2026, 11, 20)

# Los cinco nombres reales de la cuenta, las generales y uno que no casa: (tipo, ámbito, modelo, sin mercado)
ESPERADO = {
    "VW [VN][2026] Taigo noviembre": ("modelo", "VN", "Taigo", False),
    "VW [VN][2026] Taigo Noviembre 2": ("modelo", "VN", "Taigo", False),
    "VW [VN][2026] T-Roc noviembre": ("modelo", "VN", "T-Roc", False),
    "VW [VO][2026] Caravelle octubre": ("modelo", "VO", "Caravelle", True),
    "VW [VO][2026] T-Cross octubre": ("modelo", "VO", "T-Cross", False),
    "Boutique Ocasión": ("general", "ambos", None, False),
    "VW [VO][2026] Black Friday": ("general", "VO", None, False),
    "VW [VN][2026] Special Days diciembre": ("general", "VN", None, False),
    "Liquidacion VO": ("general", "ambos", None, False),
}
SIN_REGLA = "Prueba de anuncio"


@pytest.fixture
def config():
    return cmp.cargar()


def _de(c: cmp.Clasificacion) -> tuple:
    return c.tipo, c.ambito, c.modelo, c.sin_mercado


# ================================================================== reglas

@pytest.mark.parametrize("nombre", list(ESPERADO))
def test_nombres_reales_y_generales(config, nombre):
    c = config.clasificar(nombre)
    assert c.ok and c.origen == "regla" and _de(c) == ESPERADO[nombre]


def test_un_nombre_que_no_casa_dice_por_que(config):
    c = config.clasificar(SIN_REGLA)
    assert not c.ok and c.tipo is None
    assert c.motivo == ("no es general (days, friday, liquidacion, boutique) ni lleva año (2026) seguido del "
                        "modelo y el mes")


@pytest.mark.parametrize("nombre, motivo", [
    ("VW [VN][2026] noviembre", "no hay modelo entre el año y el mes"),
    ("VW [VN][2026] Golf", "no es general (days, friday, liquidacion, boutique) ni lleva el mes (enero … diciembre) "
                           "después del año y el modelo"),
    ("VW [VN][VO][2026] Golf enero", "lleva las etiquetas [VN] y [VO] a la vez"),
])
def test_motivos_de_las_que_no_casan(config, nombre, motivo):
    assert config.clasificar(nombre).motivo == motivo


@pytest.mark.parametrize("nombre", [
    "vw [vn][2026] taigo NOVIEMBRE", "VW [ VN ] [ 2026 ]   Taigo   noviembre", "VW[VN][2026]Taigo noviembre",
    "VW [VN] 2026 TAIGO Noviembre 3",
])
def test_sin_mayusculas_ni_espacios(config, nombre):
    assert _de(config.clasificar(nombre)) == ("modelo", "VN", "Taigo", False)


@pytest.mark.parametrize("nombre, modelo", [
    ("VW [VN][2026] TROC noviembre", "T-Roc"), ("VW [VN][2026] t roc noviembre", "T-Roc"),
    ("VW [VO][2026] T-Cross octubre", "T-Cross"), ("VW [VN][2026] id.4 enero", "ID.4"),
    ("VW [VN][2026] T-Roc Cabrio marzo", "T-Roc Cabriolet"),           # nombre «en_texto» del catálogo
])
def test_modelo_normalizado_con_el_catalogo(config, nombre, modelo):
    c = config.clasificar(nombre)
    assert c.modelo == modelo and not c.sin_mercado


def test_generales_con_tilde_pegadas_y_con_ambito(config):
    assert _de(config.clasificar("LIQUIDACIÓN de stock")) == ("general", "ambos", None, False)
    assert _de(config.clasificar("VW [VO][2026] BlackFriday noviembre")) == ("general", "VO", None, False)
    # Una general con año y mes sigue siendo general: no se busca modelo
    assert _de(config.clasificar("VW [VN][2026] Golf Days mayo")) == ("general", "VN", None, False)


def test_las_asignaciones_a_mano_mandan(tmp_path):
    f = tmp_path / "campanas.yaml"
    f.write_text("reglas: {generales: [liquidacion]}\nasignaciones:\n"
                 "  - {campana: Liquidacion VO, tipo: general, ambito: VO}\n"
                 "  - {campana: 'VW [VN][2026] Taigo noviembre', tipo: modelo, ambito: VO, modelo: 't roc'}\n"
                 "  - {campana: Prueba de anuncio, tipo: modelo, ambito: VN, modelo: Amarok}\n")
    c = cmp.cargar(f)
    assert _de(c.clasificar("Liquidacion VO")) == ("general", "VO", None, False)
    assert _de(c.clasificar("liquidación  vo")) == ("general", "VO", None, False)   # comparada normalizada
    assert _de(c.clasificar("VW [VN][2026] Taigo noviembre")) == ("modelo", "VO", "T-Roc", False)
    assert _de(c.clasificar(SIN_REGLA)) == ("modelo", "VN", "Amarok", True)
    assert c.clasificar(SIN_REGLA).origen == "asignacion"
    assert c.clasificar("VW [VN][2026] Taigo Noviembre 2").origen == "regla"      # otra campaña: la regla


@pytest.mark.parametrize("texto", [
    "reglas: []\n",                                                                  # formato antiguo
    "reglas: {generales: []}\n",
    "reglas: {generales: [days]}\nasignaciones:\n  - {campana: X, tipo: otro, ambito: VN}\n",
    "reglas: {generales: [days]}\nasignaciones:\n  - {campana: X, tipo: general, ambito: general}\n",
    "reglas: {generales: [days]}\nasignaciones:\n  - {campana: X, tipo: modelo, ambito: VN}\n",
    "reglas: {generales: [days]}\nasignaciones:\n  - {campana: X, tipo: general, ambito: VN, modelo: Golf}\n",
    "reglas: {generales: [days]}\nasignaciones:\n  - {campana: X, tipo: general, ambito: VN}\n"
    "  - {campana: x, tipo: general, ambito: VO}\n",
])
def test_configuracion_no_valida(tmp_path, texto):
    f = tmp_path / "campanas.yaml"
    f.write_text(texto)
    with pytest.raises(cmp.ErrorCampanas):
        cmp.cargar(f)


# ================================================================== tablas del panel

def meta_dia(pg, cid, nombre, dias=3, gasto=1_000, hasta=HOY):
    for i in range(dias):
        d = hasta - timedelta(days=dias - 1 - i)
        pg.execute("""INSERT INTO campanas_meta_dia (descargado_at, run_id, origen, cuenta_id, dia, nivel, campana_id,
                      campana_nombre, gasto_cents, impresiones, clics, huella)
                      VALUES (now(), 'r', 'prueba', '1', %s, 'campana', %s, %s, %s, 1000, 20, %s)""",
                   (d, cid, nombre, gasto, f"{cid}{i}"))


def test_actualizar_escribe_panel_campana_dia_y_campanas_sin_regla(pg):
    nombres = list(ESPERADO) + [SIN_REGLA]
    for i, n in enumerate(nombres):
        meta_dia(pg, f"C{i}", n)
    pg.execute("""INSERT INTO ga4_trafico_dia (descargado_at, run_id, origen, propiedad_id, dia, fuente, medio, campana,
                  pagina_destino, sesiones, sesiones_interaccion, eventos_clave, huella) VALUES
                  (now(), 'r', 'p', '1', %s, 'facebook', 'paid', 'vw_vn_2026_taigo_noviembre', '/', 5, 4, 0, 'a'),
                  (now(), 'r', 'p', '1', %s, 'facebook', 'paid', 'otra cosa', '/', 7, 6, 0, 'b')""", (HOY, HOY))
    assert cmp.actualizar(pg) == {"campanas_clasificadas": len(ESPERADO), "campanas_sin_regla": 2}
    filas = pg.execute("""SELECT titulo, tipo_campana, ambito, modelo, sin_mercado, count(*), min(inicio)
                          FROM panel_campana_dia WHERE origen = 'real' GROUP BY 1, 2, 3, 4, 5""").fetchall()
    assert {f[0]: f[1:5] for f in filas} == ESPERADO
    assert {f[5] for f in filas} == {3} and {f[6] for f in filas} == {HOY - timedelta(days=2)}
    sin = pg.execute("SELECT fuente, campana, motivo FROM panel_campanas_sin_regla ORDER BY fuente").fetchall()
    # En GA4, el utm_campaign de la campaña de Taigo se une a la de Meta y se clasifica como ella
    assert [s[:2] for s in sin] == [("ga4", "otra cosa"), ("meta", SIN_REGLA)]
    assert all(s[2].startswith("no es general") for s in sin)
    # Se rehace entero: con una asignación nueva, la campaña sin regla pasa a Marketing
    assert cmp.actualizar(pg, cmp.Campanas(
        cmp.cargar().generales, {cmp.normalizar(SIN_REGLA): cmp.Clasificacion("general", "VO", origen="asignacion")},
        {}, "v24.0", cmp.catalogo_modelos())) == {"campanas_clasificadas": len(nombres), "campanas_sin_regla": 1}
    assert pg.execute("SELECT count(*) FROM panel_campana_dia WHERE origen = 'real'").fetchone()[0] == 3 * len(nombres)


def test_el_nombre_vigente_clasifica_toda_la_campana(pg):
    meta_dia(pg, "C", "Prueba", dias=2, hasta=HOY - timedelta(days=1))
    meta_dia(pg, "C", "VW [VO][2026] Black Friday", dias=1)
    cmp.actualizar(pg)
    assert pg.execute("SELECT DISTINCT titulo, tipo_campana, ambito FROM panel_campana_dia").fetchall() == [
        ("VW [VO][2026] Black Friday", "general", "VO")]
    assert pg.execute("SELECT count(*) FROM panel_campanas_sin_regla").fetchone() == (0,)


def test_estado_de_las_fuentes_muestra_el_motivo(pg, panel_url):
    from panel import usuarios
    from test_contrato_panel import app_con
    from test_panel import dentro
    usuarios.alta(pg, "tecnico", "T", "tecnico", "clave-temporal-123")
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    meta_dia(pg, "C", SIN_REGLA)
    cmp.actualizar(pg)
    html = dentro(app_con(panel_url), "tecnico").get("/estado-fuentes").text
    assert "<th scope=\"col\" class=\"izq\">Motivo</th>" in html
    assert f"{SIN_REGLA}</td><td class=\"izq detalle\">No es general (days, friday, liquidacion, boutique)" in html


# ================================================================== criterios de Meta: sin datos de mercado

@pytest.fixture
def conc():
    return cargar_concesionario()


@pytest.fixture
def crit():
    return motor.cargar_criterios()


def test_sin_datos_de_mercado_la_regla_e_no_decide(pg, conc, crit):
    from test_criterios_meta import campana, segmento
    # El Caravelle no está en Mercado; aunque hubiera una fila con su nombre, no se usa
    segmento(pg, "modelo", "Caravelle", 1, tipo="VO")
    # Cifras de «subir presupuesto» (coste por lead 4,90 €, lead a cita 10 %, tasa de venta 40 %…)
    campana(pg, "CAR", leads=100, citas=10, ventas=4, gasto=49_000, impresiones=1_000, modelo="Caravelle",
            ambito="VO", sin_mercado=True)
    modelos_e = cr.regla_e(pg, HOY, conc, crit)
    _, js = cr.juzgar(pg, HOY, "real", conc, crit, modelos_e)
    j = next(x for x in js if x.campana_id == "CAR")
    e, subir = j.reglas["e"], j.reglas["subir"]
    assert e.estado == "no_aplica" and e.decision is None
    assert e.cifras_texto == ("Caravelle: sin datos de mercado (no está en el catálogo de turismos que capturamos); "
                              "la regla e y la comparación con la competencia no se pueden aplicar")
    assert subir.estado == "no_aplica" and subir.decision is None
    assert "regla e: no se puede aplicar (Caravelle sin datos de mercado)" in subir.cifras_texto
    assert j.decision == "continuar"
    # La recomendación del modelo sale de su campaña, nunca de la regla e
    filas = cr.recomendaciones(HOY, modelos_e, js, "v")
    caravelle = [f for f in filas if f["modelo"] == "Caravelle"]
    assert [(f["tipo"], f["accion"]) for f in caravelle] == [("VO", "mantener")]


def test_sin_datos_de_mercado_la_regla_d_no_compara_con_la_competencia(pg, conc, crit):
    from test_criterios_meta import campana
    campana(pg, "REF", dias=10, leads=1_000, citas=100, gasto=50_000, hasta=HOY - timedelta(days=1), modelo="Polo")
    campana(pg, "CAR", leads=100, citas=10, ventas=1, gasto=5_000, impresiones=1_000, modelo="Caravelle",
            ambito="VO", sin_mercado=True)
    _, js = cr.juzgar(pg, HOY, "real", conc, crit, {})
    d = next(x for x in js if x.campana_id == "CAR").reglas["d"]
    assert d.activa and "los similares en la zona cercana (no se puede aplicar: Caravelle sin datos de mercado)" \
        in d.revisar and d.cifras["sin_mercado"] is True


# ================================================================== vista Marketing

def test_marketing_lee_tipo_y_ambito_de_las_reglas(pg, panel_url):
    from panel import usuarios
    from test_contrato_panel import app_con, bloques
    from test_panel import dentro
    usuarios.alta(pg, "tecnico", "T", "tecnico", "clave-temporal-123")
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    for i, n in enumerate(list(ESPERADO) + [SIN_REGLA]):
        meta_dia(pg, f"C{i}", n, gasto=1_000 * (i + 1))
    cmp.actualizar(pg)
    cl = dentro(app_con(panel_url), "tecnico")
    origen, camp = bloques(cl.get("/marketing?tipo=todo").text)["campanas"]
    assert origen == "real" and SIN_REGLA not in camp
    for texto in ("Ocasión (VO) · Caravelle · sin datos de mercado", "Ocasión (VO) · T-Cross ·",
                  "Nuevos (VN) · Taigo ·", "Ocasión (VO) · General ·", "Nuevos (VN) · General ·", "VN y VO · General ·"):
        assert texto in camp, texto
    assert "Nuevos (VN) · T-Roc · 3 días de vida" in camp
    # En VO: las de VO (también la general de VO, con su coste) y las de ambos, sin coste
    vo = bloques(cl.get("/marketing?tipo=VO").text)
    _, camp_vo = vo["campanas"]
    assert "Black Friday" in camp_vo and "Caravelle" in camp_vo and "Taigo" not in camp_vo
    assert "Boutique Ocasión" in camp_vo and "Liquidacion VO" in camp_vo
    assert camp_vo.count("gasto común a VN y VO") == 2
    _, kpis = vo["kpis"]
    # Inversión de VO: Caravelle (4), T-Cross (5) y Black Friday (7), en miles de céntimos por día, 3 días
    assert "Inversión VO" in kpis and "480 €" in kpis and "sin las campañas de VN y VO" in kpis
