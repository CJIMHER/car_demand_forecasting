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
    "Boutique Ocasión": ("general", "VO", None, False),
    "VW [VO][2026] Black Friday": ("general", "VO", None, False),
    "VW [VN][2026] Special Days diciembre": ("general", "VN", None, False),
    "Liquidacion VO": ("general", "VO", None, False),
    "Special Days": ("general", "ambos", None, False),
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


@pytest.mark.parametrize("nombre, ambito", [
    ("Boutique Ocasión", "VO"), ("boutique OCASION", "VO"), ("Liquidacion VO", "VO"), ("liquidación vo", "VO"),
    ("Black Friday nuevos", "VN"), ("Special Days VN", "VN"), ("Special Days", "ambos"),
    ("Special Days Volvo", "ambos"),               # «VO» solo como palabra entera
    ("Nuevo Golf Days", "ambos"),                  # «nuevo» no es «nuevos»
    ("Black Friday VN y VO", "ambos"),             # las dos: ambos
    ("[VN] Boutique Ocasión", "VN"),               # la etiqueta manda sobre las palabras
])
def test_sin_etiqueta_el_ambito_sale_de_las_palabras(config, nombre, ambito):
    c = config.clasificar(nombre)
    assert c.tipo == "general" and c.ambito == ambito


def test_palabras_de_ambito_en_una_campana_de_modelo(config):
    assert _de(config.clasificar("VW 2026 T-Roc Ocasión noviembre")) == ("modelo", "VO", "T-Roc", False)
    assert _de(config.clasificar("VW 2026 Taigo nuevos noviembre")) == ("modelo", "VN", "Taigo", False)


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
    assert "Boutique Ocasión" in camp_vo and "Liquidacion VO" in camp_vo and "Special Days" in camp_vo
    assert camp_vo.count("gasto común a VN y VO") == 1
    _, kpis = vo["kpis"]
    # Inversión de VO: Caravelle (4), T-Cross (5), Boutique Ocasión (6), Black Friday (7) y Liquidacion VO (9),
    # en miles de céntimos por día, 3 días
    assert "Inversión VO" in kpis and "930 €" in kpis and "sin la campaña de VN y VO" in kpis


# ================================================================== reglas b, c y f con los leads que cuenta Meta

SEMANA = date(2026, 11, 22)       # semana del 16 al 22 de noviembre
# Cifras reales de la semana: clics, leads según Meta y gasto. Las impresiones no venían en las cifras:
# unas que no disparan la tasa de clic (todas por encima del 1 %)
REALES = {
    "VW [VN][2026] T-Roc noviembre": dict(impresiones=30_000, clics=531, leads=14, gasto=24_455),
    "VW [VN][2026] Taigo Noviembre 2": dict(impresiones=60_000, clics=1_222, leads=30, gasto=34_801),
    "VW [VO][2026] T-Cross octubre": dict(impresiones=40_000, clics=677, leads=39, gasto=24_699),
}


def _repartir(total: int, dias: int) -> list[int]:
    return [total // dias + (total % dias if i == dias - 1 else 0) for i in range(dias)]


def meta_semana(pg, cid, nombre, *, impresiones, clics, leads, gasto, dias=7, hasta=SEMANA, leads_por_dia=None):
    """Una campaña en campanas_meta_dia: los totales repartidos entre sus días (el resto, el último)."""
    columnas = zip(_repartir(gasto, dias), _repartir(impresiones, dias), _repartir(clics, dias),
                   leads_por_dia or _repartir(leads, dias))
    for i, (g, im, cl, le) in enumerate(columnas):
        d = hasta - timedelta(days=dias - 1 - i)
        pg.execute("""INSERT INTO campanas_meta_dia (descargado_at, run_id, origen, cuenta_id, dia, nivel, campana_id,
                      campana_nombre, gasto_cents, impresiones, clics, leads, huella)
                      VALUES (now(), 'r', 'prueba', '1', %s, 'campana', %s, %s, %s, %s, %s, %s, %s)""",
                   (d, cid, nombre, g, im, cl, le, f"{cid}{i}"))


def _semana_real(pg):
    for i, (nombre, cifras) in enumerate(REALES.items()):
        meta_semana(pg, f"R{i}", nombre, **cifras)
    cmp.actualizar(pg)


def test_actualizar_guarda_los_leads_de_meta(pg):
    _semana_real(pg)
    leads = dict(pg.execute("SELECT titulo, sum(leads_meta) FROM panel_campana_dia WHERE origen = 'real' "
                            "GROUP BY 1").fetchall())
    assert leads == {n: c["leads"] for n, c in REALES.items()}


def test_reglas_b_y_c_con_los_leads_de_meta_semana_real(pg):
    from test_alertas import alertas, reglas
    _semana_real(pg)
    conc = cargar_concesionario()
    resumen = motor.evaluar(pg, SEMANA, conc, reglas("meta_a", "meta_b", "meta_c", "meta_d", "meta_f", "meta_subir"))
    # Sin Walcu: a, d y subir presupuesto se evalúan pero no se aplican («sin datos de Walcu»: no saltan);
    # b, c y f se aplican con los leads de Meta
    for r in ("meta_a", "meta_d", "meta_subir"):
        assert resumen["reglas"][r] == "real: 0 disparadas", r
    for r in ("meta_b", "meta_c", "meta_f"):
        assert resumen["reglas"][r].startswith("real"), r
    todas = alertas(pg, dia=SEMANA)
    assert {x["rol"] for x in todas} == {"marketing", "tecnico"}          # técnico, con el detalle
    m = {(x["regla"], x["clave"]): x for x in todas if x["rol"] == "marketing"}
    a = {(x["regla"], x["clave"]): x for x in todas if x["rol"] == "tecnico"}     # el detalle
    assert set(a) == set(m) == {("meta_b", "R0"), ("meta_c", "R0"), ("meta_b", "R1"), ("meta_c", "R1")}
    assert {x["origen"] for x in a.values()} == {"real"}
    assert m[("meta_b", "R0")]["texto"] == (
        "Campaña «VW [VN][2026] T-Roc noviembre» · revisar: Hacen clic, pero pocos dejan sus datos. Qué hacer: "
        "Revisar la llamada a la acción, el copy y las audiencias. Revisar el formulario.")
    assert ("clic a lead: 14 leads según Meta de 531 clics = 2,64 % (umbral 3 %)"
            in a[("meta_b", "R0")]["texto"])
    assert a[("meta_c", "R0")]["texto"] == (
        "Campaña «VW [VN][2026] T-Roc noviembre» · revisar (regla c): coste por lead: 244,55 € / 14 leads según Meta "
        "= 17,47 € (umbral 10 €). Sin datos de Walcu: falta el coste por cita para decidir entre continuar y parar.")
    assert "clic a lead: 30 leads según Meta de 1222 clics = 2,45 % (umbral 3 %)" in a[("meta_b", "R1")]["texto"]
    assert "coste por lead: 348,01 € / 30 leads según Meta = 11,60 € (umbral 10 €)" in a[("meta_c", "R1")]["texto"]
    assert a[("meta_c", "R1")]["cifras"]["fuente_leads"] == "meta"
    # T-Cross: 39 de 677 clics = 5,76 % y 6,33 € por lead: nada
    decisiones = dict(pg.execute("SELECT campana_id, decision FROM panel_campana_decision").fetchall())
    assert decisiones == {"R0": "revisar", "R1": "revisar", "R2": "continuar"}
    reglas_tcross = {r["regla"]: r for r in pg.execute(
        "SELECT reglas FROM panel_campana_decision WHERE campana_id = 'R2'").fetchone()[0]}
    assert reglas_tcross["b"]["estado"] == "no" and reglas_tcross["c"]["estado"] == "no"
    assert "6,33 €" in reglas_tcross["c"]["texto"] and "39 leads según Meta" in reglas_tcross["c"]["texto"]
    assert {reglas_tcross[k]["estado"] for k in ("a", "d", "subir")} == {"sin_dato"}
    assert "sin datos de Walcu: no se aplica; no hay ninguna carga de Walcu" in reglas_tcross["a"]["texto"]


def test_regla_f_con_los_leads_de_meta(pg):
    crit = motor.cargar_criterios()
    # 10 días: 7 con 3 leads al día y los 3 últimos con 1 (−67 %), a 30 € por día: 30 € por lead
    meta_semana(pg, "F", "VW [VN][2026] Polo noviembre", impresiones=100_000, clics=2_000, leads=24, gasto=30_000,
                dias=10, leads_por_dia=[3] * 7 + [1] * 3)
    cmp.actualizar(pg)
    _, js = cr.juzgar(pg, SEMANA, "real", cargar_concesionario(), crit, {})
    f = js[0].reglas["f"]
    assert f.activa and f.cifras_texto.startswith("leads según Meta por día: 1 en los últimos 3 días frente a 3 ")


def test_cuando_entran_los_leads_de_walcu_mandan_los_de_walcu(pg):
    _semana_real(pg)
    pg.execute("""INSERT INTO panel_leads_dia (dia, origen, modelo, tipo, campana_id, leads, citas, ventas)
                  VALUES (%s, 'real', 'T-Roc', 'VN', 'R0', 40, 2, 0)""", (SEMANA,))
    _, js = cr.juzgar(pg, SEMANA, "real", cargar_concesionario(), motor.cargar_criterios(), {})
    j = next(x for x in js if x.campana_id == "R0")
    assert j.fuente_leads == "walcu" and j.leads == 40
    assert "según Meta" not in j.reglas["c"].cifras_texto and "/ 40 leads = 6,11 €" in j.reglas["c"].cifras_texto
    assert j.reglas["a"].estado != "sin_dato"


def test_una_campana_de_ga4_repetida_sale_una_vez_con_sus_sesiones_sumadas(pg):
    """Caso de producción: la misma campaña de GA4 llega en dos filas (con y sin ID de campaña, otro medio y
    otro día). Antes rompía la clave (fuente, campana) de panel_campanas_sin_regla."""
    pg.execute("""INSERT INTO ga4_trafico_dia (descargado_at, run_id, origen, propiedad_id, dia, fuente, medio, campana,
                  pagina_destino, sesiones, sesiones_interaccion, eventos_clave, campana_id, canal_ga4, huella) VALUES
                  (now(), 'r', 'p', '1', %s, 'facebook', 'paid', '52581469922844', '/', 5, 4, 0, NULL, NULL, 'a'),
                  (now(), 'r', 'p', '1', %s, 'facebook', 'cpc', '52581469922844', '/', 7, 6, 0, '52581469922844',
                   'Paid Social', 'b')""", (HOY - timedelta(days=3), HOY))
    assert cmp.actualizar(pg)["campanas_sin_regla"] == 1
    assert pg.execute("SELECT fuente, campana, primer_dia, ultimo_dia, gasto_cents, sesiones "
                      "FROM panel_campanas_sin_regla").fetchall() == [
        ("ga4", "52581469922844", HOY - timedelta(days=3), HOY, None, 12)]
    # Y otra vez: se rehace entera, sin chocar con lo que ya había
    assert cmp.actualizar(pg)["campanas_sin_regla"] == 1


def test_sin_regla_unica_junta_por_fuente_y_campana():
    from datetime import date as d
    filas = [("ga4", "x", d(2026, 1, 5), d(2026, 1, 6), None, 3, "m1"),
             ("meta", "x", d(2026, 1, 1), d(2026, 1, 2), 100, None, "m"),
             ("ga4", "x", d(2026, 1, 1), d(2026, 1, 9), None, 4, "m2"),
             ("meta", "x", d(2026, 1, 3), d(2026, 1, 4), 50, None, "m")]
    assert sorted(cmp._sin_regla_unica(filas)) == [
        ("ga4", "x", d(2026, 1, 1), d(2026, 1, 9), None, 7, "m1"),
        ("meta", "x", d(2026, 1, 1), d(2026, 1, 4), 150, None, "m")]
