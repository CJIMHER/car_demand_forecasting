"""Botones de Ventas: «Pedir campaña a marketing» y «Revisar precio».

POST con CSRF, permisos por rol, bloqueo de 7 días, anotación en acciones y aviso por Telegram (simulado)
al rol que toca; con datos de ejemplo, solo al técnico y con «[EJEMPLO]» delante. Nada se escribe en Walcu.
"""

from __future__ import annotations

import re

import psycopg
import pytest

from alertas import envio, motor
from test_alertas import CHATS, Telegram, reglas
from test_contrato_panel import a_real, app_con, con_ejemplo, conc, personas  # noqa: F401 (fixtures)
from test_panel import HOY, cliente, dentro, token


def _real(pg, conc):
    """Ventas con datos reales: el riesgo, las curvas, el precio de nuestros VO y el motor evaluado (bloque de
    alertas real)."""
    a_real(pg, "panel_riesgo_vehiculo", "panel_curva_grupo", "panel_precio_vo")
    motor.evaluar(pg, HOY, conc, reglas("fuente_caida"))
    return pg.execute("SELECT stock_id FROM panel_riesgo_vehiculo WHERE origen = 'real' ORDER BY stock_id LIMIT 2"
                      ).fetchall()


def _pedir(cl, stock_id, accion="campana", htmx=True, csrf=None, **extra):
    csrf = csrf if csrf is not None else token(cl, f"/ventas?v={stock_id}")
    cab = {"HX-Request": "true"} if htmx else {}
    return cl.post("/ventas/accion", data={"stock_id": stock_id, "accion": accion, "tipo": "todo", "csrf": csrf},
                   headers={**cab, **extra})


def _acciones(pg):
    return pg.execute("SELECT stock_id, accion, usuario, origen FROM acciones ORDER BY id").fetchall()


def test_pedir_campana_anota_avisa_a_marketing_y_lo_muestra(pg, panel_url, personas, con_ejemplo):
    (s1,), _ = _real(pg, con_ejemplo)
    cl = dentro(app_con(panel_url), "ventas")
    r = _pedir(cl, s1)
    assert r.status_code == 200
    assert "Campaña pedida a marketing: el aviso sale por Telegram a marketing en un minuto." in r.text
    assert _acciones(pg) == [(s1, "campana", "ventas", "real")]
    # La ficha lo muestra y el botón no deja repetir
    html = cl.get(f"/ventas?v={s1}").text
    assert re.search(r'data-accion="campana" disabled', html)
    assert not re.search(r'data-accion="revisar_precio" disabled', html)
    assert re.search(r"Campaña pedida a marketing el \d\d/\d\d/\d{4} \d\d:\d\d por ventas · aviso en cola", html)
    # El aviso lo envía cdf_jobs: al chat de marketing, con el vehículo, los días y la probabilidad
    t = Telegram()
    assert envio.enviar_acciones(pg, enviar=t, entorno=CHATS) == 1
    (chat, texto), = t.enviados
    v = pg.execute("SELECT modelo, version, dias_exposicion, p_venta90_p50 FROM panel_riesgo_vehiculo "
                   "WHERE origen = 'real' AND stock_id = %s", (s1,)).fetchone()
    assert chat == "-400" and texto.startswith(f"Pedir campaña a marketing · {v[0]} {v[1]} (stock {s1},")
    assert f"{v[2]} días en stock" in texto.replace(".", "") and f"{round(float(v[3]) * 100)} % de venderse" in texto
    assert "Lo pide ventas desde el panel" in texto and "[EJEMPLO]" not in texto
    assert pg.execute("SELECT que, rol, destino, estado FROM alertas_salida").fetchall() == [
        ("accion", "marketing", "marketing", "enviado")]
    assert "aviso enviado" in cl.get(f"/ventas?v={s1}").text
    assert envio.enviar_acciones(pg, enviar=t, entorno=CHATS) == 0      # no se envía dos veces


def test_revisar_precio_va_al_jefe_de_ventas_o_al_tecnico(pg, panel_url, personas, con_ejemplo):
    (s1,), (s2,) = _real(pg, con_ejemplo)
    cl = dentro(app_con(panel_url), "direccion")      # dirección también ve Ventas
    assert _pedir(cl, s1, "revisar_precio").status_code == 200
    t = Telegram()
    envio.enviar_acciones(pg, enviar=t, entorno=CHATS)
    assert t.enviados[0][0] == "-300" and t.enviados[0][1].startswith("Revisar precio · ")
    assert _pedir(cl, s2, "revisar_precio").status_code == 200
    envio.enviar_acciones(pg, enviar=t, entorno={"TELEGRAM_CHAT_ID": "-100"})
    assert t.enviados[1][0] == "-100" and t.enviados[1][1].startswith("[Para ventas] Revisar precio · ")


def test_la_misma_accion_no_se_repite_en_7_dias(pg, panel_url, personas, con_ejemplo):
    (s1,), (s2,) = _real(pg, con_ejemplo)
    cl = dentro(app_con(panel_url), "ventas")
    _pedir(cl, s1)
    r = _pedir(cl, s1)
    assert "no se repite hasta pasados 7 días" in r.text and len(_acciones(pg)) == 1
    assert _pedir(cl, s1, "revisar_precio").status_code == 200       # la otra acción sí
    assert len(_acciones(pg)) == 2
    # Pedida hace 8 días ya no cuenta; hace 6, sí
    _anterior(pg, s2, "campana", 8)
    _anterior(pg, s2, "revisar_precio", 6)
    html = cl.get(f"/ventas?v={s2}").text
    assert not re.search(r'data-accion="campana" disabled', html)
    assert re.search(r'data-accion="revisar_precio" disabled', html)
    assert "no se repite" in _pedir(cl, s2, "revisar_precio").text
    assert "Campaña pedida a marketing:" in _pedir(cl, s2).text


def _anterior(pg, stock_id, accion, dias):
    pg.execute("""INSERT INTO acciones (pedido_at, stock_id, accion, usuario, origen, tipo, vehiculo, dias_exposicion,
                  p_venta90_p10, p_venta90_p50, p_venta90_p90)
                  VALUES (now() - make_interval(days => %s), %s, %s, 'otra', 'real', 'VN', 'x', 1, 0.1, 0.2, 0.3)""",
               (dias, stock_id, accion))


def test_csrf_y_origen(pg, panel_url, personas, con_ejemplo):
    (s1,), _ = _real(pg, con_ejemplo)
    cl = dentro(app_con(panel_url), "ventas")
    assert _pedir(cl, s1, csrf="").status_code == 403
    assert _pedir(cl, s1, csrf="falso").status_code == 403
    assert _pedir(cl, s1, Origin="https://otro.example").status_code == 403
    assert _acciones(pg) == []
    assert _pedir(cl, s1, Origin="https://testserver").status_code == 200
    assert len(_acciones(pg)) == 1


def test_permisos_por_rol(pg, panel_url, personas, con_ejemplo):
    (s1,), _ = _real(pg, con_ejemplo)
    app = app_con(panel_url)
    # Marketing no tiene Ventas
    cl = dentro(app, "marketing")
    assert _pedir(cl, s1, csrf=token(cl, "/mercado")).status_code == 403
    # Sin sesión, al acceso
    anonimo = cliente(app)
    assert anonimo.post("/ventas/accion", data={"stock_id": s1, "accion": "campana", "csrf": "x"}).status_code == 303
    assert _acciones(pg) == []
    # Con Ventas en datos de ejemplo, solo quien ve el ejemplo (por defecto, técnico)
    pg.execute("DELETE FROM panel_riesgo_vehiculo WHERE origen = 'real'")
    cl = dentro(app, "ventas")
    assert _pedir(cl, s1, csrf=token(cl, "/mercado")).status_code == 403
    assert _acciones(pg) == []


def test_con_datos_de_ejemplo_el_aviso_solo_va_al_tecnico(pg, panel_url, personas, con_ejemplo):
    stock = pg.execute("SELECT stock_id FROM panel_riesgo_vehiculo WHERE origen = 'ejemplo' ORDER BY stock_id LIMIT 1"
                       ).fetchone()[0]
    cl = dentro(app_con(panel_url), "tecnico")
    r = _pedir(cl, stock)
    assert "al grupo técnico (datos de ejemplo)" in r.text
    assert _acciones(pg) == [(stock, "campana", "tecnico", "ejemplo")]
    t = Telegram()
    envio.enviar_acciones(pg, enviar=t, entorno=CHATS)
    (chat, texto), = t.enviados
    assert chat == "-100" and texto.startswith("[EJEMPLO] [Para marketing] Pedir campaña a marketing · ")
    assert pg.execute("SELECT rol, destino FROM alertas_salida").fetchall() == [("tecnico", "tecnico")]


def test_sin_htmx_vuelve_a_la_ficha_y_vehiculo_desconocido(pg, panel_url, personas, con_ejemplo):
    (s1,), _ = _real(pg, con_ejemplo)
    cl = dentro(app_con(panel_url), "ventas")
    r = _pedir(cl, s1, htmx=False)
    assert r.status_code == 303 and r.headers["location"] == f"/ventas?tipo=todo&v={s1}"
    assert len(_acciones(pg)) == 1
    r = _pedir(cl, "NO-EXISTE")
    assert len(_acciones(pg)) == 1
    assert _pedir(cl, s1, accion="borrar_de_walcu").status_code == 200 and len(_acciones(pg)) == 1


def test_fallo_de_telegram_en_un_aviso_se_anota(pg, panel_url, personas, con_ejemplo):
    (s1,), _ = _real(pg, con_ejemplo)
    _pedir(dentro(app_con(panel_url), "ventas"), s1)
    from ingesta import telegram
    assert envio.enviar_acciones(pg, enviar=Telegram(falla=telegram.ErrorTelegram("caído")), entorno=CHATS) == 1
    assert pg.execute("SELECT estado FROM alertas_salida").fetchall() == [("error",)]
    assert pg.execute("SELECT count(*) FROM fuentes_estado WHERE fuente = 'telegram' AND estado = 'error'").fetchone() == (1,)


def test_cdf_panel_solo_anade_acciones(panel_url):
    with psycopg.connect(panel_url, autocommit=True) as c:
        c.execute("""INSERT INTO acciones (stock_id, accion, usuario, origen, tipo, vehiculo, dias_exposicion,
                     p_venta90_p10, p_venta90_p50, p_venta90_p90) VALUES ('S', 'campana', 'u', 'real', 'VN', 'x', 1,
                     0.1, 0.2, 0.3)""")
        for orden in ("UPDATE acciones SET usuario = 'otro'", "DELETE FROM acciones", "DELETE FROM alertas",
                      "INSERT INTO alertas_salida (fecha, que, rol, destino, texto, estado) VALUES (now(), 'alertas', "
                      "'tecnico', 'tecnico', 'x', 'enviado')", "SELECT 1 FROM campanas_meta_dia",
                      "SELECT 1 FROM ga4_trafico_dia"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(orden)
        for tabla in ("alertas", "alertas_evaluacion", "alertas_salida", "acciones", "panel_mercado_competidor",
                      "panel_campanas_sin_regla"):
            c.execute(f"SELECT count(*) FROM {tabla}")


def test_acciones_de_solo_anadir(pg):
    pg.execute("""INSERT INTO acciones (stock_id, accion, usuario, origen, tipo, vehiculo, dias_exposicion,
                  p_venta90_p10, p_venta90_p50, p_venta90_p90) VALUES ('S', 'campana', 'u', 'real', 'VN', 'x', 1,
                  0.1, 0.2, 0.3)""")
    for orden in ("UPDATE acciones SET usuario = 'otro'", "DELETE FROM acciones"):
        with pytest.raises(psycopg.errors.RaiseException):
            pg.execute(orden)
    with pytest.raises(psycopg.Error):      # la clave ajena de alertas_salida lo para antes que el trigger
        pg.execute("TRUNCATE acciones")
    assert pg.execute("SELECT usuario FROM acciones").fetchall() == [("u",)]
