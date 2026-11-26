"""make ga4-explorar con respuestas de ejemplo de la GA4 Data API: nunca sale a la red ni toca la base de datos.

Eventos con su recuento y primer día, tipos de página agrupados por config/ga4_paginas.yaml (y por su
forma los que no casan), métricas de interacción de los metadatos, primer día con datos, solo
peticiones agregadas (ninguna dimensión de usuario) y nada guardado.
"""

from __future__ import annotations

import io
import json
from datetime import date

import pytest

from ingesta import conectores as cx
from ingesta import db
from ingesta import ga4_explorar as gx

HOY = date(2026, 11, 25)
TOKEN = "ya29.token-de-google-SECRETO"
INFORME = "https://analyticsdata.googleapis.com/v1beta/properties/4567:runReport"
METADATOS = "https://analyticsdata.googleapis.com/v1beta/properties/4567/metadata"
USUARIO = {"userId", "user_pseudo_id", "clientId", "customUser:email", "userEmail"}


class Respuesta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _fila(dims, mets):
    return {"dimensionValues": [{"value": v} for v in dims], "metricValues": [{"value": str(v)} for v in mets]}


EVENTOS_12M = [("page_view", 120000), ("session_start", 50000), ("generate_lead", 340), ("click_telefono", 75)]
EVENTOS_DIAS = [("page_view", "20230301"), ("page_view", "20261124"), ("session_start", "20230301"),
                ("session_start", "20261124"), ("generate_lead", "20240115"), ("generate_lead", "20261120"),
                ("click_telefono", "20250610"), ("click_telefono", "20261101"), ("formulario_antiguo", "20230510"),
                ("formulario_antiguo", "20231231")]
PAGINAS = [("/", 30000), ("/ocasion", 20000), ("/ocasion/volkswagen-golf-life-12345", 9000),
           ("/ocasion/volkswagen-t-roc-sport-67890/", 7000),
           ("/coches/nuevos/volkswagen/t-roc/suv-5-puertas/2025/1000012345", 8000), ("/nuevos", 3000),
           ("/configurador/id-4", 2500), ("/ofertas/black-friday", 2000), ("/contacto", 1500),
           ("/gracias", 400), ("/vehiculos-especiales/serie-2025", 300), ("/vehiculos-especiales/serie-2024", 200),
           ("/Ocasión/Polo-99887", 100)]
VW = "volkswagen.avisa.es"
# (dominio, ruta, vistas): un subdominio por marca; las peticiones, en un dominio ajeno al grupo
OTRAS = [("audi.avisa.es", "/landing-audi-q3", 600), ("audi.avisa.es", "/landing-t-roc", 40),
         ("peticiones.ejemplo.org", "/peticion/una-causa", 250), ("(not set)", "/landing-seat-arona", 30),
         ("(not set)", "/financiacion", 20)]
# (evento, dominio, recuento): page_view no es de intención y no se pide
EVENTOS_DOMINIO = [("Configurar", VW, 1200), ("Configurar", "audi.avisa.es", 300), ("Lead - Nuevos", VW, 40),
                   ("Lead - Nuevos", "(not set)", 2), ("Lead - Otros", VW, 10), ("form_start", "www.cear.es", 900),
                   ("form_start", VW, 80), ("page_view", VW, 99999)]
METRICAS_META = ["sessions", "engagedSessions", "engagementRate", "userEngagementDuration", "averageSessionDuration",
                 "screenPageViews", "screenPageViewsPerSession", "eventCount", "keyEvents", "activeUsers",
                 "totalUsers"]                                                    # sin newUsers
TOTALES = {"sessions": 60000, "engagedSessions": 36000, "engagementRate": 0.6, "userEngagementDuration": 3_600_000,
           "averageSessionDuration": 95.5, "screenPageViews": 120000, "screenPageViewsPerSession": 2.0,
           "eventCount": 400000, "keyEvents": 415, "activeUsers": 40000, "totalUsers": 41000}


class Api:
    """Sustituye a conectores._urlopen: responde según la URL y el cuerpo, y guarda las peticiones."""

    def __init__(self, vacia=False, paginar_eventos=False):
        self.peticiones, self.vacia, self.paginar_eventos = [], vacia, paginar_eventos

    def __call__(self, peticion, timeout):
        self.peticiones.append(peticion)
        url = peticion.full_url
        if url.startswith("https://oauth2.googleapis.com/token"):
            return self._r({"access_token": TOKEN, "expires_in": 3599})
        if url == METADATOS:
            return self._r({"dimensions": [{"apiName": n, "uiName": n, "category": "Page / screen"} for n in
                                           ("date", "eventName", "pagePath", "landingPage", "deviceCategory")]
                            + [{"apiName": "customEvent:tipo_vehiculo", "uiName": "Tipo de vehículo",
                                "category": "Custom", "customDefinition": True}],
                            "metrics": [{"apiName": n, "uiName": n, "category": "Session"} for n in METRICAS_META]})
        assert url == INFORME, url
        c = json.loads(peticion.data)
        dims = [d["name"] for d in c["dimensions"]]
        mets = [m["name"] for m in c["metrics"]]
        rango = c["dateRanges"][0]
        if self.vacia:
            return self._r({"rowCount": 0})
        if dims == ["eventName"]:
            assert rango == {"startDate": "2025-11-25", "endDate": "2026-11-24"}
            return self._r({"rows": [_fila([e], [n]) for e, n in EVENTOS_12M], "rowCount": len(EVENTOS_12M)})
        if dims == ["eventName", "date"]:
            assert rango == {"startDate": "2015-08-14", "endDate": "2026-11-24"}
            filas = [_fila([e, d], [1]) for e, d in EVENTOS_DIAS]
            if self.paginar_eventos:
                filas = filas[c["offset"]:c["offset"] + 4]
            return self._r({"rows": filas, "rowCount": len(EVENTOS_DIAS)})
        if dims == ["hostName", "pagePath"]:
            assert mets == ["screenPageViews"] and c["orderBys"][0]["desc"] is True
            filas = [(VW, p, n) for p, n in PAGINAS] + OTRAS
            return self._r({"rows": [_fila([h, p], [n]) for h, p, n in filas], "rowCount": len(filas)})
        if dims == ["eventName", "hostName"]:
            permitidos = c["dimensionFilter"]["filter"]["inListFilter"]["values"]
            filas = [f for f in EVENTOS_DOMINIO if f[0] in permitidos]
            return self._r({"rows": [_fila([e, h], [n]) for e, h, n in filas], "rowCount": len(filas)})
        if dims == ["date"]:
            assert c["limit"] == 1 and c["orderBys"] == [{"dimension": {"dimensionName": "date"}}]
            return self._r({"rows": [_fila(["20230301"], [500])], "rowCount": 1360})
        if dims == []:
            assert len(mets) <= gx.MAX_METRICAS and set(mets) <= set(METRICAS_META)
            return self._r({"rows": [_fila([], [TOTALES[m] for m in mets])], "rowCount": 1})
        raise AssertionError(f"informe inesperado: {dims}")

    @staticmethod
    def _r(datos):
        return Respuesta(json.dumps(datos).encode())

    def informes(self):
        return [json.loads(p.data) for p in self.peticiones if p.full_url == INFORME]


@pytest.fixture
def entorno(tmp_path):
    f = tmp_path / "ga4.json"
    f.write_text(json.dumps({"client_email": "lector@proyecto.iam.gserviceaccount.com", "private_key": "-----x-----",
                             "token_uri": "https://oauth2.googleapis.com/token"}))
    f.chmod(0o600)
    return {"GA4_PROPERTY_ID": "properties/4567", "GA4_CREDENCIALES": str(f)}


@pytest.fixture
def sin_bd(monkeypatch):
    def prohibida(*a, **k):
        raise AssertionError("ga4-explorar no debe conectarse a la base de datos")

    monkeypatch.setattr(db, "conectar", prohibida)
    monkeypatch.setattr(cx, "guardar_brutos", prohibida)
    monkeypatch.setattr(cx, "subir_rclone", prohibida)


def _explorar(monkeypatch, entorno, api, **k):
    monkeypatch.setattr(cx, "_urlopen", api)
    lineas = []
    codigo = gx.explorar(entorno, HOY, lineas.append, dormir=lambda s: None, firmar=lambda c, t: "jwt-firmado", **k)
    return codigo, "\n".join(lineas)


def _linea(texto, empieza):
    return next(x for x in texto.splitlines() if x.strip().startswith(empieza))


def test_eventos_con_recuento_de_12_meses_y_primer_dia(monkeypatch, entorno, sin_bd):
    api = Api(paginar_eventos=True)
    codigo, texto = _explorar(monkeypatch, entorno, api)
    assert codigo == 0
    assert "Últimos 12 meses: del 25/11/2025 al 24/11/2026" in texto
    assert _linea(texto, "page_view").split() == ["page_view", "120.000", "01/03/2023", "24/11/2026"]
    assert _linea(texto, "generate_lead").split() == ["generate_lead", "340", "15/01/2024", "20/11/2026"]
    # un evento que ya no se usa sale con 0 en los últimos 12 meses y sus fechas
    assert _linea(texto, "formulario_antiguo").split() == ["formulario_antiguo", "0", "10/05/2023", "31/12/2023"]
    orden = [x.split()[0] for x in texto.split("== 1.")[1].split("== 2.")[0].splitlines()[2:] if x.strip()]
    assert orden == ["page_view", "session_start", "generate_lead", "click_telefono", "formulario_antiguo"]
    # las 10 filas de evento y día, en 3 páginas
    assert [c["offset"] for c in api.informes() if [d["name"] for d in c["dimensions"]] == ["eventName", "date"]] \
        == [0, 4, 8]


def test_tipos_de_pagina_por_patron_y_por_forma(monkeypatch, entorno, sin_bd):
    _, texto = _explorar(monkeypatch, entorno, Api())
    bloque = texto.split("== 2.")[1].split("== 3.")[0]
    assert "18 páginas en 13 tipos" in bloque
    # las dos fichas de ocasión y la de «/Ocasión/Polo-99887» (mayúsculas y tilde) juntas
    assert _linea(bloque, "Ficha de ocasión").split()[-3:] == ["16.100", "19,0", "3"]
    assert _linea(bloque, "Listado de ocasión").split()[-3:] == ["20.000", "23,5", "1"]
    assert _linea(bloque, "Inicio").split()[-3:] == ["30.000", "35,3", "1"]
    for tipo in ("Ficha de nuevo", "Listado de nuevos", "Configurador", "Ofertas", "Contacto",
                 "Gracias (formulario enviado)"):
        assert _linea(bloque, tipo)
    # lo que no casa, agrupado por su forma, para añadirle una regla
    assert _linea(bloque, "(sin tipo) /vehiculos-especiales/*").split()[-3:] == ["500", "0,6", "2"]
    # lo que no cuenta: otro subdominio del grupo (aunque la ruta sea de un modelo propio), la ruta de otra marca
    # sin dominio, y un dominio ajeno al grupo
    assert _linea(bloque, "(fuera: otra marca)").split()[-3:] == ["670", "0,8", "3"]
    assert _linea(bloque, "(fuera: dominio ajeno)").split()[-3:] == ["250", "0,3", "1"]
    assert "el 0,6 % de las vistas; fuera (dominio, no comercial u otra marca), el 1,1 %" in bloque
    tipos = [x.strip().rsplit(None, 3)[0] for x in bloque.splitlines()[3:] if x.strip()]
    assert tipos[:3] == ["Inicio", "Listado de ocasión", "Ficha de ocasión"]
    assert "/ocasion/" not in bloque                     # sin ejemplos si no se piden


def test_ejemplos_de_paginas_con_dominio_marca_y_modelo(monkeypatch, entorno, sin_bd):
    _, texto = _explorar(monkeypatch, entorno, Api(), ejemplos=True)
    bloque = texto.split("== 2.")[1].split("== 3.")[0]
    lineas = [x.strip() for x in bloque.splitlines()]
    i = lineas.index(_linea(bloque, "Ficha de ocasión").strip())
    assert lineas[i + 1:i + 4] == ["9.000  volkswagen.avisa.es/ocasion/volkswagen-golf-life-12345  [propia; Golf]",
                                   "7.000  volkswagen.avisa.es/ocasion/volkswagen-t-roc-sport-67890  [propia; T-Roc]",
                                   "100  volkswagen.avisa.es/ocasion/polo-99887  [propia; Polo]"]
    assert "40  audi.avisa.es/landing-t-roc  [otra]" in lineas
    assert "30  (not set)/landing-seat-arona  [otra]" in lineas          # sin dominio: por la ruta
    assert "20  (not set)/financiacion  [sin_marca]" in lineas


def test_dominios_con_sus_vistas_las_peticiones_y_aviso_de_los_ajenos(monkeypatch, entorno, sin_bd):
    _, texto = _explorar(monkeypatch, entorno, Api())
    bloque = texto.split("== 5.")[1].split("Peticiones a la API")[0]
    assert [x.split()[:3] for x in bloque.splitlines()[2:6]] == [
        ["volkswagen.avisa.es", "84.000", "0"], ["audi.avisa.es", "640", "0"],
        ["peticiones.ejemplo.org", "250", "250"], ["(not", "set)", "50"]]
    assert _linea(bloque, "volkswagen.avisa.es").endswith("marca propia: cuenta")
    assert _linea(bloque, "audi.avisa.es").endswith("otra marca del grupo: fuera")
    assert _linea(bloque, "peticiones.ejemplo.org").endswith("AJENO AL GRUPO: fuera")
    assert _linea(bloque, "(not set)").endswith("sin dominio en GA4: la marca sale de la ruta")
    assert "Las rutas /peticion/* aparecen en: peticiones.ejemplo.org" in bloque
    assert "¡AVISO! Dominios ajenos al grupo en la propiedad (excluidos de las señales): peticiones.ejemplo.org" \
        in bloque


def test_metricas_y_dimensiones_de_interaccion(monkeypatch, entorno, sin_bd):
    api = Api()
    _, texto = _explorar(monkeypatch, entorno, api)
    bloque = texto.split("== 3.")[1].split("== 4.")[0]
    assert _linea(bloque, "engagedSessions").endswith("36.000")
    assert _linea(bloque, "activeUsers").endswith("40.000")
    assert _linea(bloque, "averageSessionDuration").endswith("95,50")
    assert _linea(bloque, "newUsers").endswith("no disponible")
    assert "tiempo medio de interacción por usuario" in bloque and _linea(bloque, "(calculada)").endswith("90 s")
    assert _linea(bloque, "deviceCategory").endswith("disponible")
    assert _linea(bloque, "newVsReturning").endswith("no disponible")
    assert "customEvent:tipo_vehiculo  (Tipo de vehículo; dimensión)" in bloque
    assert "Métricas en total: 11 (Session 11)" in bloque
    # 11 métricas: dos peticiones (máximo 10 por petición) y solo de las que hay
    lotes = [[m["name"] for m in c["metrics"]] for c in api.informes() if not c["dimensions"]]
    assert [len(x) for x in lotes] == [10, 1] and "newUsers" not in sum(lotes, [])


def test_primer_dia_con_datos_y_nada_guardado(monkeypatch, entorno, sin_bd):
    api = Api()
    _, texto = _explorar(monkeypatch, entorno, api)
    assert "Primer día con datos: 01/03/2023" in texto
    assert texto.rstrip().endswith(f"Peticiones a la API: {len(api.peticiones) - 1}. No se ha guardado nada.")
    assert all(p.get_method() == "GET" for p in api.peticiones if p.full_url == METADATOS)


def test_solo_agregados_sin_dimensiones_de_usuario_y_sin_el_token(monkeypatch, entorno, sin_bd):
    api = Api()
    _, texto = _explorar(monkeypatch, entorno, api)
    pedidas = {d["name"] for c in api.informes() for d in c["dimensions"]}
    assert pedidas == {"eventName", "date", "hostName", "pagePath"} and not pedidas & USUARIO
    assert TOKEN not in texto and "jwt-firmado" not in texto
    assert all(p.headers.get("Authorization") == f"Bearer {TOKEN}" for p in api.peticiones
               if p.full_url != "https://oauth2.googleapis.com/token")


def test_propiedad_sin_datos(monkeypatch, entorno, sin_bd):
    codigo, texto = _explorar(monkeypatch, entorno, Api(vacia=True))
    assert codigo == 0
    assert "Ningún evento." in texto and "0 páginas en 0 tipos" in texto and "Ningún dominio." in texto
    assert "La propiedad no tiene datos." in texto


def test_sin_credenciales_no_hace_ninguna_peticion(monkeypatch, sin_bd):
    def prohibida(*a, **k):
        raise AssertionError("petición sin credenciales")

    monkeypatch.setattr(cx, "_urlopen", prohibida)
    lineas = []
    assert gx.main([], entorno={}, escribir=lineas.append) == 1
    assert lineas == ["GA4 sin credenciales: faltan GA4_PROPERTY_ID o GA4_CREDENCIALES en el .env. "
                      "No se ha hecho ninguna petición."]


def test_un_error_de_la_api_sale_sin_el_token(monkeypatch, entorno, sin_bd):
    import urllib.error

    api = Api()

    def falla(peticion, timeout):
        if peticion.full_url == INFORME:
            cuerpo = io.BytesIO(b'{"error": {"status": "PERMISSION_DENIED"}}')
            raise urllib.error.HTTPError(INFORME, 403, "x", {}, cuerpo)
        return api(peticion, timeout)

    monkeypatch.setattr(cx, "_urlopen", falla)
    lineas = []
    assert gx.main([], entorno=entorno, escribir=lineas.append, dormir=lambda s: None,
                   firmar=lambda c, t: "jwt-firmado") == 1
    assert lineas[-1].startswith("GA4: ") and "403" in lineas[-1] and TOKEN not in "\n".join(lineas)


def test_forma_de_las_rutas_sin_tipo():
    assert gx.forma("/a/b-123/" + "x" * 41) == "/a/*/*"
    assert gx.forma("/") == "/"


def test_eventos_de_intencion_por_dominio(monkeypatch, entorno, sin_bd):
    api = Api()
    _, texto = _explorar(monkeypatch, entorno, api)
    bloque = texto.split("== 6.")[1].split("Peticiones a la API")[0]
    lineas = [x.split("  ") for x in bloque.splitlines()[2:] if x.strip()]
    filas = [[c.strip() for c in x if c.strip()] for x in lineas]
    assert ["Configurar", "volkswagen.avisa.es", "1.200", "sí"] in filas
    assert ["audi.avisa.es", "300", "no (otra marca del grupo)"] in filas
    assert ["Lead - Nuevos", "volkswagen.avisa.es", "40", "sí"] in filas
    assert ["(not set)", "2", "no (sin dominio en GA4)"] in filas            # el tipo fijo no salta el dominio
    assert ["Lead - Otros", "volkswagen.avisa.es", "10", "no (solo exploración)"] in filas
    assert ["form_start", "www.cear.es", "900", "no (AJENO AL GRUPO)"] in filas
    assert ["COLOR", "(ninguno)", "0"] in filas
    assert "page_view" not in bloque
    pedidos = next(c for c in api.informes() if [d["name"] for d in c["dimensions"]] == ["eventName", "hostName"])
    assert set(pedidos["dimensionFilter"]["filter"]["inListFilter"]["values"]) == {
        "Configurar", "COLOR", "Opcionales", "Precio", "Lead - Nuevos", "Lead - Segunda mano", "Lead - Otros",
        "Click llamada o email", "Solicitar información", "form_start", "form_submit"}
