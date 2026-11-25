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
           ("/ocasion/volkswagen-t-roc-sport-67890/", 7000), ("/nuevos/t-roc", 8000), ("/nuevos", 3000),
           ("/configurador/id-4", 2500), ("/ofertas/black-friday", 2000), ("/contacto", 1500),
           ("/gracias", 400), ("/blog/consejos-itv-2025", 300), ("/blog/consejos-invierno-2024", 200),
           ("/Ocasión/Polo-99887", 100)]
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
        if dims == ["pagePath"]:
            assert mets == ["screenPageViews"] and c["orderBys"][0]["desc"] is True
            return self._r({"rows": [_fila([p], [n]) for p, n in PAGINAS], "rowCount": len(PAGINAS)})
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
    return next(l for l in texto.splitlines() if l.strip().startswith(empieza))


def test_eventos_con_recuento_de_12_meses_y_primer_dia(monkeypatch, entorno, sin_bd):
    api = Api(paginar_eventos=True)
    codigo, texto = _explorar(monkeypatch, entorno, api)
    assert codigo == 0
    assert "Últimos 12 meses: del 25/11/2025 al 24/11/2026" in texto
    assert _linea(texto, "page_view").split() == ["page_view", "120.000", "01/03/2023", "24/11/2026"]
    assert _linea(texto, "generate_lead").split() == ["generate_lead", "340", "15/01/2024", "20/11/2026"]
    # un evento que ya no se usa sale con 0 en los últimos 12 meses y sus fechas
    assert _linea(texto, "formulario_antiguo").split() == ["formulario_antiguo", "0", "10/05/2023", "31/12/2023"]
    orden = [l.split()[0] for l in texto.split("== 1.")[1].split("== 2.")[0].splitlines()[2:] if l.strip()]
    assert orden == ["page_view", "session_start", "generate_lead", "click_telefono", "formulario_antiguo"]
    # las 10 filas de evento y día, en 3 páginas
    assert [c["offset"] for c in api.informes() if [d["name"] for d in c["dimensions"]] == ["eventName", "date"]] \
        == [0, 4, 8]


def test_tipos_de_pagina_por_patron_y_por_forma(monkeypatch, entorno, sin_bd):
    _, texto = _explorar(monkeypatch, entorno, Api())
    bloque = texto.split("== 2.")[1].split("== 3.")[0]
    assert "13 rutas en 10 tipos" in bloque
    # las dos fichas de ocasión y la de «/Ocasión/Polo-99887» (mayúsculas y tilde) juntas
    assert _linea(bloque, "Ficha de vehículo de ocasión").split()[-3:] == ["16.100", "19,2", "3"]
    assert _linea(bloque, "Listado de ocasión").split()[-3:] == ["20.000", "23,8", "1"]
    assert _linea(bloque, "Inicio").split()[-3:] == ["30.000", "35,7", "1"]
    for tipo in ("Ficha de nuevo", "Listado de nuevos", "Configurador", "Ofertas", "Contacto",
                 "Gracias (formulario enviado)"):
        assert _linea(bloque, tipo)
    # lo que no casa, agrupado por su forma, para añadirle una regla
    assert _linea(bloque, "(sin tipo) /blog/*").split()[-3:] == ["500", "0,6", "2"]
    assert "el 0,6 % de las vistas" in bloque
    tipos = [l.strip().rsplit(None, 3)[0] for l in bloque.splitlines()[3:] if l.strip()]
    assert tipos[:3] == ["Inicio", "Listado de ocasión", "Ficha de vehículo de ocasión"]


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
    assert [len(l) for l in lotes] == [10, 1] and "newUsers" not in sum(lotes, [])


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
    assert pedidas == {"eventName", "date", "pagePath"} and not pedidas & USUARIO
    assert TOKEN not in texto and "jwt-firmado" not in texto
    assert all(p.headers.get("Authorization") == f"Bearer {TOKEN}" for p in api.peticiones
               if p.full_url != "https://oauth2.googleapis.com/token")


def test_propiedad_sin_datos(monkeypatch, entorno, sin_bd):
    codigo, texto = _explorar(monkeypatch, entorno, Api(vacia=True))
    assert codigo == 0
    assert "Ningún evento." in texto and "0 rutas en 0 tipos" in texto
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
            raise urllib.error.HTTPError(INFORME, 403, "x", {}, io.BytesIO(b'{"error": {"status": "PERMISSION_DENIED"}}'))
        return api(peticion, timeout)

    monkeypatch.setattr(cx, "_urlopen", falla)
    lineas = []
    assert gx.main([], entorno=entorno, escribir=lineas.append, dormir=lambda s: None,
                   firmar=lambda c, t: "jwt-firmado") == 1
    assert lineas[-1].startswith("GA4: ") and "403" in lineas[-1] and TOKEN not in "\n".join(lineas)


def test_config_de_tipos_de_pagina(tmp_path):
    tipos = gx.cargar_tipos()
    assert tipos and all(t.nombre for t in tipos)
    assert gx.tipo_pagina("/ocasion/golf-12345?email=a@b.example", tipos) == "Ficha de vehículo de ocasión"
    assert gx.forma("/a/b-123/" + "x" * 41) == "/a/*/*"
    assert gx.forma("/") == "/"
    mal = tmp_path / "mal.yaml"
    mal.write_text("tipos:\n  - nombre: Roto\n    patron: '('\n")
    with pytest.raises(gx.ErrorPaginas, match="patrón de «Roto» no válido"):
        gx.cargar_tipos(mal)
    mal.write_text("tipos:\n  - patron: 'x'\n")
    with pytest.raises(gx.ErrorPaginas, match="necesita «nombre» y «patron»"):
        gx.cargar_tipos(mal)
