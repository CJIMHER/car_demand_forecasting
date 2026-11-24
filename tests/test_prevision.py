"""Previsión bayesiana de leads, citas y ventas (analitica/prevision.py, analitica/predictiva.py, panel/prevision.py).

Con datos sintéticos de parámetros conocidos: el modelo los recupera; los intervalos cubren lo que deben; sin
gasto, 0; un modelo con pocos datos se queda cerca de su tipo; más gasto, más leads; el cálculo es rápido.
Y de punta a punta: tablas, validación, Google Trends, Marketing (escenarios recalculados con la posterior
guardada y «cuánto gasto para N citas»), Dirección y Estado de las fuentes. Sin red.
"""

from __future__ import annotations

import math
import re
import time
from datetime import date, timedelta

import numpy as np
import pytest

from analitica import predictiva as pd
from analitica import prevision as pv
from ingesta.concesionario import ErrorConfiguracion
from panel.app import crear_app
from sinteticos import RAIZ
from test_contrato_panel import ETIQUETA, bloques, personas  # noqa: F401
from test_panel import dentro

CFG = pv.cargar_config()
L0 = date(2025, 1, 6)                       # primer lunes de los datos sintéticos
VERDAD = {"a": 0.5, "b": 0.7, "sigma": 0.25, "antes": 0.4, "cita": 0.30, "venta": 0.10}
ESTACION = {m: 0.15 * math.sin(m) for m in range(1, 13)}


def _grupo(semanas: int, rng, tipo="VN", modelo=None, gasto=(200, 1500), escala=0.0, cita=None,
           desde: date = L0) -> pv.Grupo:
    g = pv.Grupo(tipo, modelo)
    for k in range(semanas):
        s = desde + timedelta(weeks=k)
        gasto_s = float(rng.uniform(*gasto))
        nivel = VERDAD["a"] + escala + (VERDAD["antes"] if s < CFG.regimen_desde else 0)
        y = nivel + VERDAD["b"] * math.log1p(gasto_s) + ESTACION[pd.mes_de(s)] + rng.normal(0, VERDAD["sigma"])
        leads = max(0, round(math.expm1(y)))
        citas = int(rng.binomial(leads, cita or VERDAD["cita"]))
        g.semanas[s] = pv.Semana(gasto_s, leads, citas, int(rng.binomial(citas, VERDAD["venta"])))
    return g


# ================================================================== configuración

def test_parametros_en_la_configuracion(tmp_path):
    assert CFG.semanas == 4 and CFG.intervalo == 0.8 and CFG.escenarios == (-50, 0, 50, 100)
    assert CFG.val_semanas == 12 and CFG.val_referencia == 4 and CFG.regimen_desde == date(2026, 1, 1)
    texto = (RAIZ / "analitica/prevision.py").read_text() + (RAIZ / "analitica/predictiva.py").read_text()
    for numero in ("0.8", "0.80", "2000", "12", "2026"):          # ni intervalo, ni simulaciones, ni semanas
        assert not re.search(rf"(?<![\w.]){re.escape(numero)}(?![\w.])", texto.split('"""', 2)[2]), numero
    f = tmp_path / "p.yaml"
    f.write_text((RAIZ / "config/prevision.yaml").read_text().replace("intervalo: 0.80", "intervalo: 1.5"))
    with pytest.raises(ErrorConfiguracion, match="intervalo"):
        pv.cargar_config(f)
    f.write_text((RAIZ / "config/prevision.yaml").read_text().replace("[-50, 0, 50, 100]", "[50, 100]"))
    with pytest.raises(ErrorConfiguracion, match="escenarios_gasto"):
        pv.cargar_config(f)


# ================================================================== el modelo

def test_recupera_los_parametros_conocidos():
    rng = np.random.default_rng(3)
    g = _grupo(96, rng)
    aj = pv.ajustar(g, CFG, L0 + timedelta(weeks=95), 14, 60)
    m = dict(zip(aj.post.columnas, aj.post.media))
    assert m["gasto"] == pytest.approx(VERDAD["b"], abs=0.1)              # elasticidad al gasto
    assert m["regimen"] + m["regimen_gasto"] * math.log1p(800) == pytest.approx(VERDAD["antes"], abs=0.15)
    assert math.sqrt(aj.post.b / (aj.post.a - 1)) == pytest.approx(VERDAD["sigma"], abs=0.05)
    assert aj.post.cita[0] / sum(aj.post.cita) == pytest.approx(VERDAD["cita"], abs=0.03)
    assert aj.post.venta[0] / sum(aj.post.venta) == pytest.approx(VERDAD["venta"], abs=0.03)
    assert not aj.pocos_datos
    # El nivel de leads con 800 € en una semana de 2026, frente al real (sin el ruido)
    s = date(2026, 6, 1)
    x = np.array(pd.fila(s, 800, CFG.regimen_desde, aj.post.columnas))
    real = VERDAD["a"] + VERDAD["b"] * math.log1p(800) + ESTACION[6]
    assert float(x @ np.array(aj.post.media)) == pytest.approx(real, abs=0.15)


def test_los_intervalos_cubren_lo_que_deben():
    """Prevén 4 semanas de datos nuevos de la misma realidad: el 80 % de las veces caen en el intervalo."""
    dentro_n = total = 0
    for semilla in range(30):
        rng = np.random.default_rng(100 + semilla)
        g = _grupo(100, rng)
        hasta = L0 + timedelta(weeks=95)
        aj = pv.ajustar(g, CFG, hasta, 14, 60)
        objetivos = [hasta + timedelta(weeks=j) for j in range(1, 5)]
        sim = pd.simular(aj.post, objetivos, [g.semanas[s].gasto for s in objetivos], CFG.regimen_desde, 2000, 1)
        for j, s in enumerate(objetivos):
            lo, _, hi = pd.cuantiles(sim.leads[:, j], 0.8)
            dentro_n += lo <= g.semanas[s].leads <= hi
            total += 1
    assert 0.7 <= dentro_n / total <= 0.92, dentro_n / total


def test_sin_gasto_prevision_cero():
    aj = pv.ajustar(_grupo(60, np.random.default_rng(1)), CFG, L0 + timedelta(weeks=59), 14, 60)
    semanas = [date(2026, 12, 7) + timedelta(weeks=j) for j in range(4)]
    sim = pd.simular(aj.post, semanas, [0, 500, 0, 0], CFG.regimen_desde, 500, 1)
    assert not sim.leads[:, [0, 2, 3]].any() and not sim.citas[:, 0].any() and not sim.ventas[:, 0].any()
    assert sim.leads[:, 1].mean() > 10
    filas = pd.filas_semana(aj.post, semanas, 0.0, CFG.escenarios, CFG.regimen_desde, 500, 1, 0.8)
    assert all(f["leads_p90"] == f["citas_p90"] == f["ventas_p90"] == f["gasto_cents"] == 0 for f in filas)


def test_un_modelo_con_pocos_datos_se_acerca_a_su_tipo():
    rng = np.random.default_rng(7)
    tipo = _grupo(96, rng)
    hasta = L0 + timedelta(weeks=95)
    base = pv.ajustar(tipo, CFG, hasta, 14, 60)
    p_tipo = base.post.cita[0] / sum(base.post.cita)
    # Un modelo con 3 semanas, leads mucho más baratos y una tasa de cita de 0,6
    poco = _grupo(3, rng, modelo="Golf", escala=1.0, cita=0.6, desde=hasta - timedelta(weeks=2))
    mucho = _grupo(80, rng, modelo="Golf", escala=1.0, cita=0.6, desde=hasta - timedelta(weeks=79))
    a_poco = pv.ajustar(poco, CFG, hasta, 14, 60, base)
    a_mucho = pv.ajustar(mucho, CFG, hasta, 14, 60, base)
    tasa = lambda a: a.post.cita[0] / sum(a.post.cita)  # noqa: E731
    assert abs(tasa(a_poco) - p_tipo) < abs(tasa(a_mucho) - p_tipo)
    assert tasa(a_mucho) == pytest.approx(0.6, abs=0.05)
    nivel = lambda a: dict(zip(a.post.columnas, a.post.media))["intercepto"]  # noqa: E731
    assert abs(nivel(a_poco) - nivel(base)) < abs(nivel(a_mucho) - nivel(base))
    assert a_poco.pocos_datos and "3 semanas con leads" in a_poco.motivos[0] and not a_mucho.pocos_datos


def test_mas_gasto_mas_leads_citas_y_ventas():
    aj = pv.ajustar(_grupo(96, np.random.default_rng(2)), CFG, L0 + timedelta(weeks=95), 14, 60)
    semanas = [date(2026, 12, 7) + timedelta(weeks=j) for j in range(4)]
    filas = [f for f in pd.filas_semana(aj.post, semanas, 600.0, CFG.escenarios, CFG.regimen_desde, 2000, 1, 0.8)
             if f["semana"] is None]
    for medida in ("leads", "citas", "ventas"):
        medianas = [f[f"{medida}_p50"] for f in filas]                  # −50 %, actual, +50 %, +100 %
        assert medianas == sorted(medianas) and medianas[0] < medianas[-1], (medida, medianas)
    assert [f["gasto_cents"] for f in filas] == [120_000, 240_000, 360_000, 480_000]
    # La inversa: más citas piden más gasto, y el intervalo envuelve la mediana
    a = pd.gasto_para_citas(aj.post, semanas, 60, CFG.regimen_desde, 1000, 1, 0.8, 50_000)
    b = pd.gasto_para_citas(aj.post, semanas, 120, CFG.regimen_desde, 1000, 1, 0.8, 50_000)
    assert a["bajo"] <= a["mediana"] <= a["alto"] and a["mediana"] < b["mediana"]
    assert pd.gasto_para_citas(aj.post, semanas, 10 ** 7, CFG.regimen_desde, 1000, 1, 0.8, 50_000) is None


def test_validacion_mide_error_y_cobertura_y_compara_con_la_referencia():
    rng = np.random.default_rng(5)
    g = _grupo(96, rng)
    ultima = L0 + timedelta(weeks=95)
    datos = pv.Datos({("VN", None): g}, ultima + timedelta(days=6), ultima, 14, 60)
    t = time.monotonic()
    filas, error = pv.validar(datos, CFG, {"VN": False})
    assert time.monotonic() - t < 5
    f = {(x["medida"], x["variante"]): x for x in filas}
    assert set(f) == {(m, v) for m in pv.MEDIDAS for v in ("gasto_real", "gasto_previsto")}
    real = f[("leads", "gasto_real")]
    assert real["n"] == 48 and real["desde_semana"] == ultima - timedelta(weeks=14)   # 12 orígenes × 4 semanas
    assert real["mae_modelo"] < real["mae_referencia"] and real["mejora"]     # con el gasto que hubo, mejora
    assert 0.6 <= real["cobertura"] <= 1.0
    assert error["VN"] == pytest.approx(real["mae_modelo"], abs=1e-3)


def test_sin_mirar_al_futuro_en_la_validacion():
    """En un origen t, las citas de una semana solo cuentan si ya estaban completas al final de t."""
    datos = pv.Datos({}, None, None, 14, 60)
    t = date(2026, 6, 1)
    assert pv._conocida(t - timedelta(weeks=2), "citas", t, datos)
    assert not pv._conocida(t - timedelta(weeks=1), "citas", t, datos)
    assert pv._conocida(t, "leads", t, datos) and not pv._conocida(t - timedelta(weeks=8), "ventas", t, datos)


# ================================================================== de punta a punta

HOY = date(2026, 11, 24)


def _cargar(pg, semanas=200, con_modelo=True, rng=None):
    """panel_campana_semana y walcu_campana_semana sintéticos: una campaña general de VN, una de modelo de VN
    (T-Roc) y una de VO (Golf), con leads de la realidad conocida."""
    rng = rng or np.random.default_rng(11)
    campanas = [("c-vn", "VW [VN] General", "VN", "general", None, (400, 1200)),
                ("c-vo", "VW [VO] Golf", "VO", "modelo", "Golf", (200, 700))]
    if con_modelo:
        campanas.append(("c-troc", "VW [VN] T-Roc", "VN", "modelo", "T-Roc", (150, 500)))
    dia = HOY - timedelta(days=1)
    for k in range(semanas):
        s = L0 + timedelta(weeks=k)
        if s + timedelta(days=6) > dia:
            break
        completas = (HOY - (s + timedelta(days=6))).days
        for cid, titulo, ambito, tc, modelo, rango in campanas:
            gasto = float(rng.uniform(*rango))
            y = VERDAD["a"] + (VERDAD["antes"] if s < CFG.regimen_desde else 0) + VERDAD["b"] * math.log1p(gasto) \
                + ESTACION[pd.mes_de(s)] + rng.normal(0, VERDAD["sigma"])
            leads = max(0, round(math.expm1(y)))
            citas = int(rng.binomial(leads, VERDAD["cita"]))
            ventas = int(rng.binomial(citas, VERDAD["venta"]))
            vn = ambito == "VN"
            pg.execute(
                """INSERT INTO panel_campana_semana (dia, origen, semana, campana_id, titulo, ambito, tipo_campana,
                       modelo, gasto_cents, walcu, cruce, leads, citas, ventas, leads_vn, leads_vo, citas_vn, citas_vo,
                       ventas_vn, ventas_vo, citas_completas, ventas_completas)
                   VALUES (%s, 'real', %s, %s, %s, %s, %s, %s, %s, true, 'nombre', %s, %s, %s, %s, %s, %s, %s, %s, %s,
                           %s, %s)""",
                (dia, s, cid, titulo, ambito, tc, modelo, round(gasto * 100), leads, citas, ventas,
                 leads if vn else 0, 0 if vn else leads, citas if vn else 0, 0 if vn else citas,
                 ventas if vn else 0, 0 if vn else ventas, completas >= 14, completas >= 60))
        pg.execute(
            """INSERT INTO walcu_campana_semana (fecha_carga, run_id, fichero_origen, huella_fichero, semana, origen,
                   campana, tipo, leads, leads_cercana, leads_fuera, leads_sin_zona, citas, ventas, num_inexistente)
               VALUES (%s, 'w1', 'walcu.csv', 'h', %s, 'Facebook', 'x', 'VN', 0, 0, 0, 0, 0, 0, 0)""",
            (HOY - timedelta(days=1), s))


def test_calculo_completo_tablas_validacion_y_tiempo(pg):
    _cargar(pg)
    t = time.monotonic()
    resumen = pv.calcular(pg, HOY, CFG)
    assert time.monotonic() - t < 30, resumen                               # segundos, no minutos
    assert resumen["grupos"] == 4                                          # VN, VN T-Roc, VO, VO Golf
    assert resumen["trends"] == "sin entregas de Google Trends cargadas: no se usa"
    post = {(t, m): (sem, pocos, ultima, primera, gasto) for t, m, sem, pocos, ultima, primera, gasto in pg.execute(
        """SELECT tipo, modelo, semanas, pocos_datos, ultima_semana, primera_semana, gasto_semanal_cents
           FROM panel_prevision_modelo""")}
    assert set(post) == {("VN", None), ("VN", "T-Roc"), ("VO", None), ("VO", "Golf")}
    assert post[("VN", None)][2] == date(2026, 11, 16) and post[("VN", None)][3] == date(2026, 11, 23)
    assert all(v[4] > 0 for v in post.values())                            # gasto previsto: las 2 últimas semanas
    # Previsión: 4 semanas + total, 4 escenarios, cada grupo
    assert pg.execute("SELECT count(*) FROM panel_prevision_semana").fetchone()[0] == 4 * 5 * 4
    # Validación: tipo y modelos, tres medidas, dos variantes
    val = pg.execute("""SELECT tipo, nivel, medida, variante, n, mejora, cobertura FROM panel_prevision_validacion
                        WHERE nivel = 'tipo' AND variante = 'gasto_real' AND medida = 'leads'""").fetchall()
    assert {v[0] for v in val} == {"VN", "VO"} and all(v[4] == 48 and v[5] for v in val)
    assert pg.execute("SELECT count(DISTINCT (nivel, medida, variante)) FROM panel_prevision_validacion"
                      ).fetchone()[0] == 12
    # Recalcular el día sustituye
    pv.calcular(pg, HOY, CFG)
    assert pg.execute("SELECT count(*) FROM panel_prevision_semana").fetchone()[0] == 4 * 5 * 4
    estado = pg.execute("SELECT count(*) FROM fuentes_estado WHERE fuente = 'panel_prevision'").fetchone()[0]
    assert estado == 0                                                     # calcular() no anota: lo hace ejecutar()


def test_google_trends_solo_si_mejora_la_validacion(pg):
    _cargar(pg, con_modelo=False)
    pg.execute("INSERT INTO gtrends_entrega (fecha_descarga, huella_manifiesto, carpeta, manifiesto) "
               "VALUES ('2026-11-01', 'h', 'c', '{}')")
    rng = np.random.default_rng(4)
    for k in range(100):                                                   # ruido: no explica nada
        pg.execute("""INSERT INTO gtrends_semana (fecha_descarga, comparacion, modelo, termino, es_modelo, semana,
                          valor, menor_que_1, huella_fichero) VALUES ('2026-11-01', 'c', 'Golf', 'golf', true, %s, %s,
                          false, 'h')""", (L0 + timedelta(weeks=k) - timedelta(days=1), float(rng.uniform(0, 100))))
    resumen = pv.calcular(pg, HOY, CFG)
    assert set(resumen["trends"]) == {"VN", "VO"}
    usa = pg.execute("SELECT tipo, bool_and(trends) FROM panel_prevision_validacion GROUP BY 1").fetchall()
    for tipo, con in usa:
        assert resumen["trends"][tipo].startswith("se usa" if con else "no se usa"), tipo
    assert dict(usa)["VN"] is False                                        # VN no tiene modelos con Trends


def test_sin_datos_no_preve_y_lo_dice(pg):
    resumen = pv.calcular(pg, HOY, CFG)
    assert "no se prevé" in resumen["motivo"]
    assert pg.execute("SELECT count(*) FROM panel_prevision_modelo").fetchone()[0] == 0


# ================================================================== panel

def _app(panel_url):
    return crear_app(dsn=panel_url, hoy=lambda: HOY, modo_ejemplo=False)


def _tabla(html):
    filas = {}
    for m in re.finditer(r'<tr class="[^"]*" data-tipo="(\w+)" data-modelo="([^"]*)">(.*?)</tr>', html, re.S):
        celdas = re.findall(r'class="c-(\w+)">([\d.]+)<span class="n">([\d.]+)–([\d.]+)</span>', m.group(3))
        filas[(m.group(1), m.group(2) or None)] = {c: tuple(int(x.replace(".", "")) for x in v) for c, *v in celdas}
    return filas


def test_marketing_previsión_escenarios_e_inversa(pg, panel_url, personas):
    _cargar(pg)
    pv.calcular(pg, HOY, CFG)
    tec = dentro(_app(panel_url), "tecnico")
    origen, b = bloques(tec.get("/marketing").text)["prevision"]
    assert origen == "real" and ETIQUETA not in b and "Previsión 4 semanas · 23/11–20/12" in b
    assert "Previsión con datos hasta la semana del 16/11/2026" in b
    assert "Solo lo que viene de las campañas de Meta" in b
    actual = _tabla(b)
    assert set(actual) == {("VN", None), ("VN", "T-Roc"), ("VO", None), ("VO", "Golf")}
    # El escenario actual, recalculado en el panel, es el guardado por el precálculo (misma semilla)
    guardado = {(t, m): {"leads": (l1, l2, l3), "citas": (c1, c2, c3), "ventas": (v1, v2, v3)}
                for t, m, l1, l2, l3, c1, c2, c3, v1, v2, v3 in pg.execute(
                    """SELECT tipo, modelo, leads_p50, leads_p10, leads_p90, citas_p50, citas_p10, citas_p90, ventas_p50,
                              ventas_p10, ventas_p90 FROM panel_prevision_semana WHERE semana IS NULL AND escenario = 0""")}
    assert actual == guardado
    # +100 % de gasto: más leads y más citas en cada grupo
    doble = _tabla(bloques(tec.get("/marketing?gasto=100").text)["prevision"][1])
    for clave in actual:
        assert doble[clave]["leads"][0] > actual[clave]["leads"][0], clave
        assert doble[clave]["citas"][0] >= actual[clave]["citas"][0], clave
    assert 'aria-current="true">+100 %</a>' in bloques(tec.get("/marketing?gasto=100").text)["prevision"][1]
    # La inversa, con el objetivo pedido
    b = bloques(tec.get("/marketing?citas_VN=40&tipo=VN").text)["prevision"][1]
    assert re.search(r"Para conseguir 40 citas en 4 semanas haría falta un gasto de unos [\d.]+ € a la semana "
                     r"\(entre [\d.]+ € y [\d.]+ €\); el previsto es [\d.]+ €\.", b), b[-800:]
    assert "Golf" not in b and 'name="citas_VN" min="1" max="100000" value="40"' in b
    # Un valor raro de escenario: el actual
    assert _tabla(bloques(tec.get("/marketing?gasto=37").text)["prevision"][1]) == actual


def test_direccion_ventas_de_meta_y_validacion_en_estado(pg, panel_url, personas):
    _cargar(pg)
    pv.calcular(pg, HOY, CFG)
    tec = dentro(_app(panel_url), "tecnico")
    origen, b = bloques(tec.get("/direccion").text)["prevision"]
    assert origen == "real" and "Ventas previstas de las campañas de Meta" in b
    assert "no el total del concesionario" in b and "sigue pendiente de la predicción de ventas" in b
    vn = pg.execute("""SELECT ventas_p10, ventas_p50, ventas_p90 FROM panel_prevision_semana WHERE tipo = 'VN'
                       AND modelo IS NULL AND semana IS NULL AND escenario = 0""").fetchone()
    assert f'<span class="kpi-valor">{vn[1]}</span><span class="kpi-sub">entre {vn[0]}–{vn[2]}' in b
    html = tec.get("/estado-fuentes").text
    trozo = html.split('data-bloque="prevision-validacion"')[1].split("</section>")[0]
    assert "validación hacia atrás" in trozo and trozo.count("<tr>") == 1 + 24    # 2 tipos × 2 niveles × 3 × 2
    assert "Gasto que hubo" in trozo and "Gasto previsto" in trozo and "Cobertura del 80 %" in trozo


def test_modelo_con_pocos_datos_marcado(pg, panel_url, personas):
    _cargar(pg)
    # Un modelo de VO con solo 3 semanas
    for k in range(3):
        s = date(2026, 11, 2) + timedelta(weeks=k)       # activa: con gasto la última semana
        pg.execute(
            """INSERT INTO panel_campana_semana (dia, origen, semana, campana_id, titulo, ambito, tipo_campana, modelo,
                   gasto_cents, walcu, cruce, leads, citas, ventas, leads_vn, leads_vo, citas_vn, citas_vo, ventas_vn,
                   ventas_vo, citas_completas, ventas_completas)
               VALUES (%s, 'real', %s, 'c-polo', 'VW [VO] Polo', 'VO', 'modelo', 'Polo', 30000, true, 'nombre', 12, 4, 0,
                       0, 12, 0, 4, 0, 0, %s, false)""", (HOY - timedelta(days=1), s, k == 0))
    pv.calcular(pg, HOY, CFG)
    b = bloques(dentro(_app(panel_url), "tecnico").get("/marketing?tipo=VO").text)["prevision"][1]
    fila = re.search(r'data-modelo="Polo">(.*?)</tr>', b, re.S).group(1)
    assert "Pocos datos" in fila and "3 semanas con leads" in fila


# ================================================================== regla del proyecto: si no bate a la referencia, no se publica

def _val(variante, mejora, mm=40.0, mr=60.0):
    return {"tipo": "VN", "nivel": "tipo", "medida": "leads", "variante": variante, "n": 48, "mae_modelo": mm,
            "mae_referencia": mr, "cobertura": 0.79, "mejora": mejora}


def test_publicacion_segun_la_validacion():
    ok = pv.publicacion([_val("gasto_real", True), _val("gasto_previsto", True)], "VN", "tipo", 0.8)
    assert ok["validada"] and ok["aviso_gasto"] is None
    assert ok["validacion"] == ("Validación: error medio de leads 40,0 por semana frente a 60,0 de la media de las "
                                "últimas semanas, en 48 semanas previstas; lo real cayó en el intervalo del 80 % el 79 % "
                                "de las veces).")
    no = pv.publicacion([_val("gasto_real", False, 70, 60), _val("gasto_previsto", False, 80, 60)], "VN", "tipo", 0.8)
    assert not no["validada"] and no["validacion"].startswith("No se publica: en la validación no mejora")
    aviso = pv.publicacion([_val("gasto_real", True), _val("gasto_previsto", False, 80, 60)], "VN", "tipo", 0.8)
    assert aviso["validada"] and "Con el gasto previsto" in aviso["aviso_gasto"]
    sin = pv.publicacion([], "VN", "tipo", 0.8)
    assert not sin["validada"] and sin["validacion"].startswith("Sin validación todavía")


def test_lo_que_no_bate_a_la_referencia_no_se_ve(pg, panel_url, personas):
    _cargar(pg)
    pv.calcular(pg, HOY, CFG)
    assert pg.execute("SELECT bool_and(validada) FROM panel_prevision_modelo").fetchone()[0]   # aquí, todo valida
    pg.execute("UPDATE panel_prevision_modelo SET validada = false, validacion = 'No se publica: x' "
               "WHERE tipo = 'VO' AND modelo IS NULL")
    tec = dentro(_app(panel_url), "tecnico")
    b = bloques(tec.get("/marketing").text)["prevision"][1]
    fila = re.search(r'data-tipo="VO" data-modelo="" data-publicada="no">(.*?)</tr>', b, re.S).group(1)
    assert "No se publica: x" in fila and "c-leads" not in fila
    assert ("VO", None) not in _tabla(b) and ("VN", None) in _tabla(b)
    assert 'name="citas_VO"' not in b and 'name="citas_VN"' in b
    d = bloques(tec.get("/direccion").text)["prevision"][1]
    assert re.search(r'data-tipo="VO" data-publicada="no">.*No se publica: x', d)
    # Sin validación (pocas semanas): nada publicado
    pg.execute("DELETE FROM panel_campana_semana WHERE semana < '2026-11-02'")
    resumen = pv.calcular(pg, HOY, CFG)
    assert not any(resumen["publicada"].values())
