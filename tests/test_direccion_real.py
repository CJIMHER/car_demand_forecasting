"""Dirección sin el modo ejemplo explícito: solo datos reales, nunca mezclados con los de ejemplo.

Lo que ya tiene fuente se ve con sus filas reales: Mercado y nuestro stock publicado, el precio provisional,
Meta con Walcu por semanas (últimas 4 semanas y referencias por año), GA4 por canal y las alertas del motor.
Lo que todavía no tiene fuente (stock interno, ventas del CRM y su predicción) dice «Pendiente de conectar:
<fuente>», aunque haya filas de ejemplo cargadas. Las filas de ejemplo solo se ven con PANEL_MODO_EJEMPLO=1.
"""

from __future__ import annotations

import re

from panel import contrato, ejemplo
from panel.app import crear_app
from test_contrato_panel import BANDA, ETIQUETA, PENDIENTE, _ve, a_real, bloques, conc, personas  # noqa: F401
from test_panel import HOY, _con_datos, dentro


def _app(panel_url, modo_ejemplo=False):
    return crear_app(dsn=panel_url, hoy=lambda: HOY, modo_ejemplo=modo_ejemplo)


def test_sin_modo_ejemplo_direccion_no_muestra_ejemplo_y_dice_lo_pendiente(pg, datos, panel_url, personas, conc):
    _con_datos(pg, datos, conc)                    # Mercado real: tarjeta del mercado, stock publicado y precio VO
    ejemplo.cargar(pg, HOY, conc)                  # filas de ejemplo en todas las tablas del contrato
    html = dentro(_app(panel_url), "tecnico").get("/direccion").text
    assert ETIQUETA not in html and BANDA not in html
    b = bloques(html)
    assert all(origen in ("real", "") for origen, _ in b.values()), {n: o for n, (o, _) in b.items()}
    # Sin stock interno: la tarjeta es nuestro stock publicado (Mercado), y lo dice
    origen, stock = b["stock"]
    publicados = sum(n for (n,) in pg.execute(
        """SELECT DISTINCT ON (tipo) propios FROM panel_mercado_dia WHERE zona = 'espana'
           ORDER BY tipo, dia DESC"""))
    assert origen == "real" and "Stock publicado" in stock and f'class="kpi-valor">{publicados}<' in stock
    assert "stock interno: pendiente de conectar" in stock
    for nombre, fuente in (("antiguedad", "stock interno (Walcu o DMS)"), ("edad", "stock interno (Walcu o DMS)"),
                           ("ventas_kpi", "ventas del CRM"), ("ventas_mes", "ventas del CRM y predicción de ventas")):
        origen, trozo = b[nombre]
        assert origen == "" and f"Pendiente de conectar: {fuente}" in trozo, nombre
    # Lo que tiene fuente pero aún no ha escrito nada real: «Sin datos todavía», sin el ejemplo
    for nombre in ("marketing", "referencias", "trafico", "alertas", "alertas_kpi"):
        origen, trozo = b[nombre]
        assert origen == "" and "Sin datos todavía" in trozo and "Pendiente" not in trozo, nombre
    # Mercado y el precio provisional, reales
    assert b["mercado"][0] == "real" and "El mercado en Sevilla" in b["mercado"][1]
    assert b["precio"][0] == "real" and "Precio de nuestros VO" in b["precio"][1]
    # La vista se abre a dirección (tiene bloques reales y ninguno de ejemplo)
    assert _ve(dentro(_app(panel_url), "direccion"), "/direccion")


def test_con_fuentes_reales_direccion_muestra_meta_walcu_referencias_y_ga4(pg, panel_url, personas, conc):
    ejemplo.cargar(pg, HOY, conc)
    a_real(pg, "panel_campana_semana", "panel_referencia_meta", "panel_trafico_canal_dia")
    ejemplo.borrar(pg)                             # quedan solo las filas reales
    tec = dentro(_app(panel_url), "tecnico")
    b = bloques(tec.get("/direccion").text)
    for nombre in ("marketing", "referencias", "trafico"):
        origen, trozo = b[nombre]
        assert origen == "real" and ETIQUETA not in trozo and "Sin datos todavía" not in trozo, nombre
    mk = b["marketing"][1]
    for etiqueta in ("Gasto", "Leads", "Citas", "Ventas", "€ / lead:", "€ / cita:", "€ / venta:"):
        assert etiqueta in mk, etiqueta
    # Las mismas cifras que Marketing (últimas 4 semanas, fila de total)
    total = bloques(tec.get("/marketing").text)["mes"][1].split("<tfoot>")[1].split("</tr>")[0]
    gasto_mk = re.search(r'<td data-etiqueta="Gasto">([^<]+)</td>', total).group(1)
    assert f'<span class="kpi-valor">{gasto_mk}</span>' in mk
    refs = b["referencias"][1]
    assert re.search(r"<th scope=\"col\">\d{4} \(\d+ semanas\)</th>.*<th scope=\"col\">\d{4} \(\d+ semanas\)</th>",
                     refs, re.S) and "Diferencia" in refs
    for medida in ("Gasto", "Coste por lead", "Coste por cita", "Coste por venta"):
        assert f'<th scope="row" class="izq">{medida}</th>' in refs, medida
    assert "Sesiones" in b["trafico"][1] and "GA4" in b["trafico"][1]


def test_el_modo_ejemplo_explicito_sigue_mostrando_el_ejemplo(pg, panel_url, personas, conc):
    ejemplo.cargar(pg, HOY, conc)
    html = dentro(_app(panel_url, modo_ejemplo=True), "tecnico").get("/direccion").text
    assert BANDA in html and ETIQUETA in html and "Pendiente de conectar" not in html
    # Sin él, con solo filas de ejemplo, Dirección no tiene nada real: desactivada para todos
    assert not _ve(dentro(_app(panel_url), "tecnico"), "/direccion")


def test_ventas_y_marketing_no_cambian(pg, panel_url, personas, conc):
    """Solo Dirección no muestra nunca el ejemplo; Ventas y Marketing siguen con su banda."""
    ejemplo.cargar(pg, HOY, conc)
    tec = dentro(_app(panel_url), "tecnico")
    for ruta in ("/ventas", "/marketing"):
        assert BANDA in tec.get(ruta).text, ruta


def test_el_modo_sale_de_la_variable(pg, panel_url, monkeypatch):
    monkeypatch.delenv("PANEL_MODO_EJEMPLO", raising=False)
    assert crear_app(dsn=panel_url).state.modo_ejemplo is False
    monkeypatch.setenv("PANEL_MODO_EJEMPLO", "1")
    assert crear_app(dsn=panel_url).state.modo_ejemplo is True
    monkeypatch.setenv("PANEL_MODO_EJEMPLO", "si")
    assert crear_app(dsn=panel_url).state.modo_ejemplo is False


def test_estados_sin_modo_ejemplo():
    M = contrato.Modo
    modos = {t: M(False, True) for t in contrato.TABLAS + contrato.MOTOR}
    assert contrato.estados(modos, modo_ejemplo=False) == {"direccion": "vacia", "ventas": "ejemplo",
                                                          "marketing": "ejemplo"}
    modos["panel_precio_vo"] = M(True, True)
    assert contrato.estados(modos, modo_ejemplo=False)["direccion"] == "real"
    assert contrato.estados(modos)["direccion"] == "ejemplo"
