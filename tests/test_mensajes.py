"""Mensajes de acción sin «la cocina» (panel/mensajes.py, config/mensajes_marketing.yaml) y Ventas solo con datos
reales.

- Cada situación de la tabla del encargo: el texto que ve un usuario no técnico no lleva números de umbral, «regla»,
  «umbral», «cohorte» ni la fórmula; el técnico sí ve el detalle.
- «{ciudad}» sale de la configuración del concesionario.
- Telegram de marketing, sin detalle; el del técnico, con él.
- Ventas, sin datos de ejemplo cuando PANEL_MODO_EJEMPLO está vacío.
"""

from __future__ import annotations

import re

import pytest

from alertas import envio, motor
from panel import ejemplo
from panel import mensajes as msj
from panel.app import crear_app
from test_contrato_panel import BANDA, ETIQUETA, bloques, personas  # noqa: F401
from test_criterios_meta import HOY as HOY_C
from test_criterios_meta import META, _golf_lanzable, _referencia, campana, conc, reglas, segmento  # noqa: F401
from test_panel import HOY, _con_datos, dentro

PROHIBIDO = re.compile(r"regla|umbral|cohorte|percentil|mínimo|n = |\d|%|=", re.I)

# Una regla tal como la guardan los criterios (panel_campana_decision.reglas): con sus cifras y el texto técnico
R = {
    "no_atrae": {"regla": "b", "estado": "activa", "decision": "revisar",
                 "texto": "tasa de clic: 5 de 2000 impresiones = 0,25 % (umbral 1 %)",
                 "cifras": {"senales": ["no_atrae"], "tasa_clic_pct": 0.25, "umbral_tasa_clic_pct": 1.0}},
    "no_convence": {"regla": "b", "estado": "activa", "decision": "revisar",
                    "texto": "clic a lead: 1 de 80 clics = 1,25 % (umbral 3 %)",
                    "cifras": {"senales": ["no_convence"]}},
    "menos_leads": {"regla": "b", "estado": "activa", "decision": "revisar",
                    "texto": "leads por impresión: 0,1 % frente a 0,4 % (umbral 50 % de esa cifra)",
                    "cifras": {"senales": ["historico"]}},
    "lead_cita": {"regla": "a", "estado": "activa", "decision": "revisar",
                  "texto": "lead a cita: 1 de 40 = 2,5 % (umbral 5 %); cohorte de 14 días",
                  "cifras": {"lead_cita_pct": 2.5, "umbral_pct": 5.0, "leads": 40, "n_minimo_leads": 20}},
    "fuera_zona": {"regla": "a", "estado": "activa", "decision": "revisar",
                   "texto": "fuera de zona: 8 de 18 leads con código postal = 44 % (umbral 35 %)",
                   "cifras": {"lead_cita_pct": 40.0, "umbral_pct": 5.0, "leads": 20, "n_minimo_leads": 20,
                              "fuera_zona_pct": 44.4, "umbral_fuera_pct": 35.0}},
    "caro_parar": {"regla": "c", "estado": "activa", "decision": "parar",
                   "texto": "coste por lead: 500 € / 40 leads = 12,50 € (umbral 10 €); coste por cita 500 €",
                   "cifras": {}},
    "caro_continuar": {"regla": "c", "estado": "activa", "decision": "continuar",
                       "texto": "coste por lead: 12,50 € (umbral 10 €); coste por cita 40 € frente a 60 €",
                       "cifras": {}},
    "cita_venta": {"regla": "d", "estado": "activa", "decision": "revisar",
                   "texto": "tasa de venta: 1 de 10 citas = 10 % (umbral 33 %)", "cifras": {}},
    "cansancio": {"regla": "f", "estado": "activa", "decision": "revisar",
                  "texto": "leads por día: 1 frente a 3 (−66 %; umbral 30 %)", "cifras": {}},
    "subir": {"regla": "subir", "estado": "activa", "decision": "subir",
              "texto": "coste por lead 4,90 € (< 6 €); por cita 30 € (< 40 €)", "cifras": {}},
}
E = {   # regla e de un modelo sin campaña
    "sin_stock": ("no_lanzar", {"unidades": 2, "unidades_minimas": 3, "unidades_con_comparables": 0}, "activa"),
    "precio_alto": ("no_lanzar", {"unidades": 9, "unidades_minimas": 3, "unidades_con_comparables": 9}, "activa"),
    "lanzar": ("lanzar", {"unidades": 9, "unidades_minimas": 3, "unidades_con_comparables": 9}, "no"),
}
DIAGNOSTICO = {
    "no_atrae": "El anuncio no llama la atención.",
    "no_convence": "Hacen clic, pero pocos dejan sus datos.",
    "menos_leads": "Esta campaña trae menos leads que otras del mismo modelo.",
    "lead_cita": "Llegan leads, pero pocos acaban en cita.",
    "fuera_zona": "Aumentan los leads de fuera de la zona del concesionario.",
    "caro_parar": "Los leads y las citas están saliendo caros.",
    "caro_continuar": "Los leads salen caros, pero las citas están en precio.",
    "cita_venta": "Las citas no se convierten en ventas.",
    "cansancio": "El anuncio da señales de cansancio.",
    "subir": "La campaña va bien en coste, citas y ventas.",
    "sin_stock": "Hay poco stock de este modelo para lanzar una campaña.",
    "precio_alto": "Nuestro precio está por encima del mercado cercano.",
    "pocos_datos": "Aún hay pocos datos para juzgar la campaña.",
}


def _texto(m: msj.Mensaje) -> str:
    return " ".join(m.diagnosticos + m.acciones)


@pytest.mark.parametrize("clave", sorted(R))
def test_cada_situacion_de_campana_sin_la_cocina(clave):
    m = msj.de_campana([R[clave]], R[clave]["decision"], "Sevilla")
    assert m is not None and m.diagnosticos[0] == DIAGNOSTICO[clave], clave
    texto = _texto(m).replace("Sevilla", "")
    assert not PROHIBIDO.search(texto), (clave, PROHIBIDO.search(texto).group(0), texto)
    assert R[clave]["texto"] not in texto


@pytest.mark.parametrize("clave", sorted(E))
def test_lanzar_o_no_lanzar_sin_la_cocina(clave):
    decision, cifras, estado = E[clave]
    m = msj.componer(msj.situacion_e(decision, cifras, estado), "Sevilla")
    assert m.situaciones == (clave,) and m.decision == decision
    if clave in DIAGNOSTICO:
        assert m.diagnosticos[0] == DIAGNOSTICO[clave]
    assert not PROHIBIDO.search(_texto(m))


def test_pocos_datos():
    m = msj.de_campana([], None, "Sevilla")
    assert m.diagnosticos == (DIAGNOSTICO["pocos_datos"],) and m.acciones == ("Esperar unos días.",)
    m = msj.de_campana([{"regla": "a", "estado": "sin_volumen", "cifras": {}}], "continuar", "Sevilla")
    assert m.situaciones == ("pocos_datos",)


def test_varias_situaciones_por_importancia_y_sin_repetir_acciones():
    reglas = [R["cansancio"], R["caro_parar"], R["no_atrae"], R["menos_leads"]]
    m = msj.de_campana(reglas, "parar", "Sevilla")
    assert m.decision == "parar" and m.situaciones[0] == "caro_parar"
    assert m.diagnosticos[0] == DIAGNOSTICO["caro_parar"]
    assert len(m.acciones) == len(set(m.acciones))
    # b con las tres señales: tres situaciones
    b = {**R["no_atrae"], "cifras": {"senales": ["no_atrae", "no_convence", "historico"]}}
    assert msj.de_campana([b], "revisar", "X").situaciones == ("no_atrae", "no_convence", "menos_leads")


def test_la_ciudad_sale_de_la_configuracion(tmp_path):
    from ingesta.concesionario import cargar
    from test_panel import FICTICIO
    (tmp_path / "c.yaml").write_text(FICTICIO, encoding="utf-8")
    ciudad = cargar(tmp_path / "c.yaml").ciudad
    assert ciudad == "Valladolid"
    m = msj.de_campana([R["fuera_zona"]], "revisar", ciudad)
    texto = _texto(m)
    assert "{ciudad}" not in texto and "Sevilla" not in texto
    assert "El copy y las imágenes deben incluir la ciudad del concesionario (Valladolid)." in m.acciones
    assert "«¿Quieres venir a Valladolid a ver el vehículo?»" in texto
    # Sin ciudad en la configuración, la provincia
    sin = FICTICIO.replace("  ciudad: Valladolid\n", "")
    (tmp_path / "s.yaml").write_text(sin, encoding="utf-8")
    assert cargar(tmp_path / "s.yaml").ciudad == "Valladolid"     # provincia 47


def test_en_el_codigo_no_hay_textos_de_mensajes():
    """Los textos de los mensajes están en la configuración, no en el código."""
    from pathlib import Path
    fuente = (Path(msj.__file__)).read_text(encoding="utf-8")
    for d in DIAGNOSTICO.values():
        assert d not in fuente


def test_interes_de_busqueda():
    assert [msj.interes(x) for x in (3.2, 0.4, -0.2, -1.5, None)] == ["sube", "estable", "estable", "baja",
                                                                       "sin datos"]


# ================================================================== motor, panel y Telegram

def _escenario(pg):
    _golf_lanzable(pg)
    segmento(pg, "modelo", "Polo", 2)                                        # sin stock: no lanzar
    _referencia(pg)
    campana(pg, "MALA", leads=40, citas=1, gasto=50_000, modelo="T-Roc", titulo="T-Roc noviembre")   # a y c


def test_telegram_de_marketing_sin_detalle_y_el_tecnico_con_el(pg, conc):
    _escenario(pg)
    motor.evaluar(pg, HOY_C, conc, reglas(*META))
    mk = envio.mensaje_alertas(HOY_C, "marketing", envio.pendientes(pg, HOY_C, "marketing"), False)
    tec = envio.mensaje_alertas(HOY_C, "tecnico", envio.pendientes(pg, HOY_C, "tecnico"), False)
    assert "T-Roc noviembre" in mk and "Qué hacer:" in mk
    cuerpo = mk.split("\n", 2)[2]                       # sin la cabecera (fecha y número de alertas)
    assert not re.search(r"regla|umbral|cohorte|\d+ de \d+|%", cuerpo, re.I), cuerpo
    assert "(regla a)" in tec and "umbral" in tec


def _app(panel_url, modo_ejemplo=False, hoy=HOY):
    return crear_app(dsn=panel_url, hoy=lambda: hoy, modo_ejemplo=modo_ejemplo)


def test_marketing_ve_diagnostico_y_acciones_y_el_tecnico_ademas_el_detalle(pg, panel_url, personas, conc):
    _escenario(pg)
    motor.evaluar(pg, HOY_C, conc, reglas(*META))
    paginas = {rol: dentro(_app(panel_url, hoy=HOY_C), rol).get("/marketing").text for rol in ("marketing", "tecnico")}
    for rol, html in paginas.items():
        b = bloques(html)
        camp, reco = b["campanas"][1], b["recomendacion"][1]
        assert DIAGNOSTICO["caro_parar"] in camp and DIAGNOSTICO["lead_cita"] in camp, rol
        assert DIAGNOSTICO["sin_stock"] in reco, rol
        if rol == "tecnico":
            assert "Detalle técnico" in camp and "umbral" in camp and "regla e" in reco
        else:
            visible = re.sub(r"<[^>]+>", " ", camp + reco)
            assert "Detalle técnico" not in visible
            assert not re.search(r"regla|umbral|cohorte|percentil", visible, re.I), \
                re.search(r".{60}(regla|umbral|cohorte|percentil).{60}", visible, re.I | re.S).group(0)


def test_ninguna_pagina_de_negocio_explica_el_metodo(pg, datos, panel_url, personas, conc):
    """Con todos los bloques llenos (modo ejemplo), ni dirección, ni ventas ni marketing ven el método."""
    _con_datos(pg, datos, conc)
    ejemplo.cargar(pg, HOY, conc)
    for rol, rutas in (("direccion", ("/direccion", "/marketing", "/ventas")), ("marketing", ("/marketing",)),
                       ("ventas", ("/ventas",))):
        cl = dentro(_app(panel_url, modo_ejemplo=True), rol)
        for ruta in rutas:
            html = re.sub(r"<[^>]+>", " ", cl.get(ruta).text)
            for palabra in ("umbral", "cohorte", "regla a", "regla b", "regla c", "regla d", "regla e", "regla f",
                            "criterios_meta", "percentil", "Detalle técnico", "config/"):
                assert palabra not in html, (rol, ruta, palabra)


# ================================================================== Ventas: solo datos reales

def test_ventas_sin_datos_de_ejemplo_sin_el_modo_ejemplo(pg, datos, panel_url, personas, conc):
    _con_datos(pg, datos, conc)                    # Mercado real: stock publicado y precio provisional
    ejemplo.cargar(pg, HOY, conc)                  # filas de ejemplo en todas las tablas del contrato
    html = dentro(_app(panel_url), "ventas").get("/ventas").text
    assert ETIQUETA not in html and BANDA not in html
    b = bloques(html)
    assert all(origen in ("real", "") for origen, _ in b.values()), {n: o for n, (o, _) in b.items()}
    # Probabilidad de venta: pendiente, sin ejemplos
    assert "Pendiente: modelo de probabilidad de venta (necesita el stock del CRM)" in b["lista"][1]
    assert "de venderse en 90 días" not in html and "Solo los que tienen menos del 50 %" not in html
    # Tarjetas de arriba: de fuentes reales o «Pendiente de conectar»
    resumen = b["resumen"][1]
    assert b["resumen"][0] == "real" and "publicado en la web" in resumen.lower()
    assert "VO con el precio a bajar o revisar frente al mercado" in resumen
    assert "Pendiente de conectar: leads y citas de Walcu por semanas" in resumen
    # Con el modo ejemplo explícito, el ejemplo sigue (para el técnico)
    con = dentro(_app(panel_url, modo_ejemplo=True), "tecnico").get("/ventas").text
    assert BANDA in con and "de venderse en 90 días" in con


def test_ventas_inventario_de_bloques():
    """El inventario del resumen (docs/hitos/hito_1b_mensajes_resumen.md) cubre todos los bloques de Ventas."""
    from pathlib import Path

    from panel import contrato
    texto = (Path(__file__).resolve().parent.parent / "docs" / "hitos" / "hito_1b_mensajes_resumen.md").read_text(
        encoding="utf-8")
    for bloque in contrato.BLOQUES["ventas"]:
        assert f"`{bloque}`" in texto, bloque


def test_mensajes_no_cambia_la_decision(pg, conc):
    """Los mensajes solo traducen: la decisión de cada campaña es la de los criterios."""
    _escenario(pg)
    motor.evaluar(pg, HOY_C, conc, reglas(*META))
    for cid, decision, reglas_j in pg.execute("SELECT campana_id, decision, reglas FROM panel_campana_decision"):
        m = msj.de_campana(reglas_j, decision, conc.ciudad)
        assert m.decision == decision, cid


def test_situacion_desconocida_en_la_configuracion_falla(tmp_path, monkeypatch):
    (tmp_path / "m.yaml").write_text("situaciones:\n  x:\n    decision: borrar\n    diagnostico: a\n    acciones: [b]\n",
                                     encoding="utf-8")
    monkeypatch.setenv("CDF_MENSAJES_MARKETING", str(tmp_path / "m.yaml"))
    with pytest.raises(msj.ErrorMensajes):
        msj.config()

