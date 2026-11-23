"""Conectores de Meta y GA4 con respuestas simuladas: nunca salen a la red.

Sin credenciales no hacen ninguna petición; paginación; límites de la API (429 y backoff); corrección
de días pasados sin duplicar; brutos al bucket antes de escribir; ningún campo personal en las
tablas; campañas sin regla en config/campanas.yaml.
"""

from __future__ import annotations

import base64
import gzip
import io
import json
import re
import urllib.error
from datetime import date
from pathlib import Path

import pytest

from ingesta import campanas as cmp
from ingesta import conectores as cx
from ingesta import ga4, meta

DESDE, HASTA = date(2026, 11, 13), date(2026, 11, 19)
TOKEN_META = "EAAB-token-de-meta-SECRETO"
ENTORNO_META = {"META_ACCESS_TOKEN": TOKEN_META, "META_AD_ACCOUNT_ID": "act_1234"}


class Respuesta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def http(codigo, cuerpo, cabeceras=None):
    return urllib.error.HTTPError("https://api.invalid/", codigo, "x", cabeceras or {},
                                  io.BytesIO(json.dumps(cuerpo).encode()))


class Red:
    """Sustituye a conectores._urlopen: responde por prefijo de URL, en orden, y guarda las peticiones."""

    def __init__(self, rutas: list[tuple[str, list]]):
        self.rutas, self.peticiones = rutas, []

    def __call__(self, peticion, timeout):
        self.peticiones.append(peticion)
        for prefijo, respuestas in self.rutas:
            if peticion.full_url.startswith(prefijo) and respuestas:
                r = respuestas.pop(0)
                if isinstance(r, Exception):
                    raise r
                return Respuesta(json.dumps(r).encode())
        raise AssertionError(f"petición inesperada: {peticion.full_url}")


def prohibida(peticion, timeout):
    raise AssertionError("Un conector sin credenciales ha intentado salir a la red")


class Bucket:
    def __init__(self, pg=None, falla=False):
        self.subidas, self.pg, self.falla, self.filas_al_subir = [], pg, falla, None

    def __call__(self, carpeta: Path, destino: str):
        if self.pg is not None:   # lo que había en Postgres cuando se subieron los brutos
            self.filas_al_subir = self.pg.execute("SELECT (SELECT count(*) FROM campanas_meta_dia) + "
                                                  "(SELECT count(*) FROM ga4_trafico_dia)").fetchone()[0]
        if self.falla:
            raise cx.ErrorDescarga("rclone terminó con código 1 al subir los brutos")
        self.subidas.append((destino, {p.name: gzip.decompress(p.read_bytes()) for p in sorted(carpeta.iterdir())}))


def esperas():
    lista = []
    return lista, lista.append


def _fila_meta(dia, campana="111", nombre="VN T-Roc Noviembre", gasto="12.34", **extra):
    return {"date_start": dia, "date_stop": dia, "account_currency": "EUR", "campaign_id": campana,
            "campaign_name": nombre, "spend": gasto, "impressions": "1000", "reach": "800", "frequency": "1.25",
            "clicks": "20", "cpm": "12.34",
            "actions": [{"action_type": "lead", "value": "3"}, {"action_type": "link_click", "value": "20"}], **extra}


def _respuestas_meta(gasto="12.34"):
    pagina2 = "https://graph.facebook.com/v24.0/act_1234/insights?after=abc"
    return [
        ("https://graph.facebook.com/v24.0/act_1234/insights?level=campaign", [
            http(429, {"error": {"message": "Too many calls", "code": 4}}, {"Retry-After": "7"}),
            {"data": [_fila_meta("2026-11-18", gasto=gasto)], "paging": {"next": pagina2}}]),
        (pagina2, [{"data": [_fila_meta("2026-11-19", campana="222", nombre="Marca general")]}]),
        ("https://graph.facebook.com/v24.0/act_1234/insights?level=adset", [
            http(400, {"error": {"message": "User request limit reached", "code": 17}}),
            {"data": [_fila_meta("2026-11-18", adset_id="31", adset_name="Conjunto 1")]}]),
        ("https://graph.facebook.com/v24.0/act_1234/insights?level=ad", [
            {"data": [_fila_meta("2026-11-18", adset_id="31", adset_name="Conjunto 1", ad_id="41", ad_name="Anuncio 1",
                                 user_id="99", email="persona@example.com", full_name="Persona de prueba")]}]),
    ]


# ================================================================== sin credenciales

@pytest.mark.parametrize("entorno", [{}, {"META_ACCESS_TOKEN": "x"}, {"META_AD_ACCOUNT_ID": "1"}])
def test_meta_sin_credenciales_no_hace_ninguna_peticion(pg, monkeypatch, entorno):
    monkeypatch.setattr(cx, "_urlopen", prohibida)
    assert meta.ejecutar(entorno=entorno, subir=Bucket()) == 0
    assert pg.execute("SELECT fuente, tipo, estado FROM fuentes_estado").fetchall() == [
        ("meta", "descarga", "sin_credenciales")]
    assert pg.execute("SELECT count(*) FROM campanas_meta_dia").fetchone() == (0,)


@pytest.mark.parametrize("entorno", [{}, {"GA4_PROPERTY_ID": "1"}, {"GA4_CREDENCIALES": "/secretos/x.json"}])
def test_ga4_sin_credenciales_no_hace_ninguna_peticion(pg, monkeypatch, entorno):
    monkeypatch.setattr(cx, "_urlopen", prohibida)
    assert ga4.ejecutar(entorno=entorno, subir=Bucket()) == 0
    assert pg.execute("SELECT fuente, estado FROM fuentes_estado").fetchall() == [("ga4", "sin_credenciales")]


# ================================================================== Meta

def test_meta_paginacion_limites_y_brutos_antes_de_escribir(pg, monkeypatch):
    red = Red(_respuestas_meta())
    monkeypatch.setattr(cx, "_urlopen", red)
    bucket = Bucket(pg)
    lista, dormir = esperas()
    assert meta.ejecutar(entorno=ENTORNO_META, desde=DESDE, hasta=HASTA, subir=bucket, dormir=dormir) == 0
    # 429 con Retry-After: 7 s; código 17 de Meta: la primera espera (5 s)
    assert lista == [7, 5]
    assert len(red.peticiones) == 6
    # El token, solo en la cabecera
    for p in red.peticiones:
        assert TOKEN_META not in p.full_url and p.get_header("Authorization") == f"Bearer {TOKEN_META}"
    primera = red.peticiones[0].full_url
    assert "time_increment=1" in primera and "%22since%22%3A+%222026-11-13%22" in primera
    # Brutos: las 4 respuestas buenas, comprimidas, subidas cuando aún no había nada escrito
    (destino, ficheros), = bucket.subidas
    assert re.fullmatch(r"meta/raw/\d{4}-\d\d-\d\d", destino) and len(ficheros) == 4
    assert all(n.endswith(".json.gz") for n in ficheros) and bucket.filas_al_subir == 0
    filas = pg.execute("SELECT nivel, dia, campana_id, conjunto_id, anuncio_id, gasto_cents, impresiones, alcance, "
                       "frecuencia, clics, cpm_cents, leads, acciones_lead FROM campanas_meta_dia ORDER BY nivel, dia").fetchall()
    assert [(f[0], str(f[1]), f[2], f[3], f[4]) for f in filas] == [
        ("anuncio", "2026-11-18", "111", "31", "41"), ("campana", "2026-11-18", "111", None, None),
        ("campana", "2026-11-19", "222", None, None), ("conjunto", "2026-11-18", "111", "31", None)]
    gasto, impresiones, alcance, frecuencia, clics, cpm, leads, acciones = filas[0][5:]
    assert (gasto, impresiones, alcance, float(frecuencia), clics, float(cpm), leads, acciones) == (
        1234, 1000, 800, 1.25, 20, 1234.0, 3, {"lead": 3.0})
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado").fetchone()
    assert estado == "ok" and detalle["filas_nuevas"] == 4 and detalle["paginas"] == 4


def test_meta_ningun_campo_personal_llega_a_la_tabla(pg, monkeypatch):
    monkeypatch.setattr(cx, "_urlopen", Red(_respuestas_meta()))
    meta.ejecutar(entorno=ENTORNO_META, desde=DESDE, hasta=HASTA, subir=Bucket(), dormir=lambda s: None)
    columnas = [c for (c,) in pg.execute("SELECT column_name FROM information_schema.columns "
                                         "WHERE table_name = 'campanas_meta_dia'")]
    assert not [c for c in columnas if re.search(r"user|usuario|email|correo|telefono|full_name|cliente|dni", c)]
    texto = " ".join(str(f) for f in pg.execute("SELECT * FROM campanas_meta_dia"))
    assert "persona@example.com" not in texto and "Persona de prueba" not in texto and "'99'" not in texto


def test_meta_correccion_de_dias_pasados_sin_duplicar(pg, monkeypatch):
    bucket = Bucket()
    for gasto in ("12.34", "12.34", "15.00"):
        monkeypatch.setattr(cx, "_urlopen", Red(_respuestas_meta(gasto)))
        meta.ejecutar(entorno=ENTORNO_META, desde=DESDE, hasta=HASTA, subir=bucket, dormir=lambda s: None)
    nuevas = [d["filas_nuevas"] for (d,) in pg.execute("SELECT detalle FROM fuentes_estado ORDER BY id")]
    # Segunda descarga igual: nada nuevo. Tercera: Meta corrige el gasto de la campaña 111 el día 18
    # (nivel campaña): una versión nueva de esa fila y nada más.
    assert nuevas == [4, 0, 1]
    assert pg.execute("SELECT count(*) FROM campanas_meta_dia").fetchone() == (5,)
    assert pg.execute("SELECT count(*) FROM campanas_meta_dia_vigente").fetchone() == (4,)
    assert pg.execute("SELECT gasto_cents FROM campanas_meta_dia_vigente WHERE nivel = 'campana' AND campana_id = '111'"
                      ).fetchone() == (1500,)
    assert len(bucket.subidas) == 3      # cada descarga deja sus brutos


def test_meta_sin_poder_guardar_los_brutos_no_escribe(pg, monkeypatch):
    monkeypatch.setattr(cx, "_urlopen", Red(_respuestas_meta()))
    assert meta.ejecutar(entorno=ENTORNO_META, desde=DESDE, hasta=HASTA, subir=Bucket(falla=True),
                         dormir=lambda s: None) == 1
    assert pg.execute("SELECT count(*) FROM campanas_meta_dia").fetchone() == (0,)
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado").fetchone()
    assert estado == "error" and "brutos" in detalle["error"]


def test_meta_error_sin_token_en_el_registro(pg, monkeypatch, caplog):
    monkeypatch.setattr(cx, "_urlopen", Red([("https://graph.facebook.com/", [
        http(400, {"error": {"message": f"Invalid OAuth access token {TOKEN_META}", "code": 190}})])]))
    assert meta.ejecutar(entorno=ENTORNO_META, desde=DESDE, hasta=HASTA, subir=Bucket(), dormir=lambda s: None) == 1
    detalle = pg.execute("SELECT detalle FROM fuentes_estado").fetchone()[0]
    assert TOKEN_META not in json.dumps(detalle) and "***" in detalle["error"]
    assert TOKEN_META not in caplog.text


def test_meta_no_sigue_una_paginacion_hacia_otro_dominio(pg, monkeypatch):
    monkeypatch.setattr(cx, "_urlopen", Red([("https://graph.facebook.com/", [
        {"data": [], "paging": {"next": "https://otro.example/robar"}}])]))
    assert meta.ejecutar(entorno=ENTORNO_META, desde=DESDE, hasta=HASTA, subir=Bucket(), dormir=lambda s: None) == 1


def test_limite_que_no_cede_acaba_en_error(monkeypatch):
    monkeypatch.setattr(cx, "_urlopen", Red([("https://api.invalid/", [http(429, {}) for _ in range(6)])]))
    lista, dormir = esperas()
    with pytest.raises(cx.ErrorDescarga, match="sin respuesta tras 6 intentos: HTTP 429"):
        cx.peticion_json("https://api.invalid/x", dormir=dormir)
    assert lista == [5, 15, 45, 90, 180]


# ================================================================== GA4

def _credenciales(tmp_path, permisos=0o600):
    f = tmp_path / "ga4.json"
    f.write_text(json.dumps({"client_email": "lector@proyecto.iam.gserviceaccount.com", "private_key": "-----x-----",
                             "private_key_id": "k1", "token_uri": "https://oauth2.googleapis.com/token"}))
    f.chmod(permisos)
    return {"GA4_PROPERTY_ID": "properties/4567", "GA4_CREDENCIALES": str(f)}


def _fila_ga4(dia, fuente, medio, campana, pagina, s, e, k):
    return {"dimensionValues": [{"value": v} for v in (dia, fuente, medio, campana, pagina)],
            "metricValues": [{"value": str(v)} for v in (s, e, k)], "userId": "u-123", "email": "a@b.example"}


def _respuestas_ga4():
    informe = "https://analyticsdata.googleapis.com/v1beta/properties/4567:runReport"
    return [
        ("https://oauth2.googleapis.com/token", [{"access_token": "ya29.token-de-google", "expires_in": 3599}]),
        (informe, [
            {"rows": [_fila_ga4("20261118", "facebook", "paid", "VN T-Roc Noviembre", "/t-roc?email=a@b.example", 10, 6, 2),
                      _fila_ga4("20261118", "facebook", "paid", "VN T-Roc Noviembre", "/t-roc?utm_x=1#f", 5, 3, 1)],
             "rowCount": 3},
            http(429, {"error": {"code": 429, "status": "RESOURCE_EXHAUSTED"}}),
            {"rows": [_fila_ga4("20261119", "(direct)", "(none)", "(not set)", "/", 40, 20, 0)], "rowCount": 3}]),
    ]


def test_ga4_paginacion_limite_y_solo_la_ruta_de_la_pagina(pg, monkeypatch, tmp_path):
    red = Red(_respuestas_ga4())
    monkeypatch.setattr(cx, "_urlopen", red)
    bucket = Bucket(pg)
    lista, dormir = esperas()
    entorno = _credenciales(tmp_path)
    assert ga4.ejecutar(entorno=entorno, desde=DESDE, hasta=HASTA, subir=bucket, dormir=dormir,
                        firmar=lambda cuenta, ahora: "jwt.firmado.falso") == 0
    assert lista == [5]
    cuerpos = [json.loads(p.data) for p in red.peticiones[1:]]
    assert [c["offset"] for c in cuerpos] == [0, 2, 2]
    assert [d["name"] for d in cuerpos[0]["dimensions"]] == ga4.DIMENSIONES
    assert red.peticiones[1].get_header("Authorization") == "Bearer ya29.token-de-google"
    filas = pg.execute("SELECT dia, fuente, medio, campana, pagina_destino, sesiones, sesiones_interaccion, eventos_clave "
                       "FROM ga4_trafico_dia ORDER BY dia").fetchall()
    # Las dos páginas de destino son la misma ruta: se suman. Sin parámetros (el correo no llega).
    assert [tuple(map(str, f[:5])) + tuple(f[5:7]) for f in filas] == [
        ("2026-11-18", "facebook", "paid", "VN T-Roc Noviembre", "/t-roc", 15, 9),
        ("2026-11-19", "(direct)", "(none)", "(not set)", "/", 40, 20)]
    assert float(filas[0][7]) == 3
    texto = " ".join(str(f) for f in pg.execute("SELECT * FROM ga4_trafico_dia"))
    assert "a@b.example" not in texto and "u-123" not in texto
    assert bucket.filas_al_subir == 0 and len(bucket.subidas[0][1]) == 2


def test_ga4_credenciales_con_permisos_abiertos_se_niega_sin_peticiones(pg, monkeypatch, tmp_path):
    monkeypatch.setattr(cx, "_urlopen", prohibida)
    assert ga4.ejecutar(entorno=_credenciales(tmp_path, 0o644), subir=Bucket()) == 1
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado").fetchone()
    assert estado == "error" and "permisos 600" in detalle["error"]


def test_ga4_correccion_sin_duplicar(pg, monkeypatch, tmp_path):
    entorno = _credenciales(tmp_path)
    for _ in range(2):
        monkeypatch.setattr(cx, "_urlopen", Red(_respuestas_ga4()))
        ga4.ejecutar(entorno=entorno, desde=DESDE, hasta=HASTA, subir=Bucket(), dormir=lambda s: None,
                     firmar=lambda cuenta, ahora: "jwt")
    assert pg.execute("SELECT count(*) FROM ga4_trafico_dia").fetchone() == (2,)


def test_jwt_de_la_cuenta_de_servicio_firmado_con_rs256():
    pytest.importorskip("cryptography")
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding, rsa

    clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = clave.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                              serialization.NoEncryption()).decode()
    jwt = ga4.jwt_firmado({"client_email": "lector@x.iam.gserviceaccount.com", "private_key": pem,
                           "private_key_id": "k1"}, 1_700_000_000)
    cab, carga, firma = jwt.split(".")

    def leer(x):
        return base64.urlsafe_b64decode(x + "=" * (-len(x) % 4))

    assert json.loads(leer(cab)) == {"alg": "RS256", "typ": "JWT", "kid": "k1"}
    assert json.loads(leer(carga)) == {"iss": "lector@x.iam.gserviceaccount.com", "scope": ga4.ALCANCE,
                                       "aud": ga4.TOKEN_URI, "iat": 1_700_000_000, "exp": 1_700_003_600}
    clave.public_key().verify(leer(firma), f"{cab}.{carga}".encode(), padding.PKCS1v15(), hashes.SHA256())


# ================================================================== campañas

def test_normalizar_y_union_por_utm_campaign(tmp_path):
    assert cmp.normalizar("T-Roc Ocasión · Noviembre") == "t_roc_ocasion_noviembre"
    f = tmp_path / "campanas.yaml"
    f.write_text("equivalencias:\n  troc-nov: VN T-Roc Noviembre\nreglas: {generales: [friday]}\n")
    c = cmp.cargar(f)
    assert c.clave_union("VN T-Roc Noviembre") == c.clave_union("vn_t_roc_noviembre") == "vn_t_roc_noviembre"
    assert c.clave_union("TROC-NOV") == "vn_t_roc_noviembre"


def test_campanas_sin_regla_para_estado_de_las_fuentes(pg, monkeypatch, tmp_path):
    """Tras descargar Meta y GA4: «VN T-Roc Noviembre» (sin año, no casa con las reglas) tiene asignación a
    mano; en GA4 se clasifica como su campaña de Meta. «Marca general» no casa: sale con su motivo."""
    f = tmp_path / "campanas.yaml"
    f.write_text("meta: {version_api: v24.0}\nreglas: {generales: [friday]}\nasignaciones:\n"
                 "  - {campana: VN T-Roc Noviembre, tipo: modelo, ambito: VN, modelo: troc}\n")
    monkeypatch.setenv("CDF_CAMPANAS", str(f))
    monkeypatch.setattr(cx, "_urlopen", Red(_respuestas_meta()))
    meta.ejecutar(entorno=ENTORNO_META, desde=DESDE, hasta=HASTA, subir=Bucket(), dormir=lambda s: None)
    monkeypatch.setattr(cx, "_urlopen", Red(_respuestas_ga4()))
    ga4.ejecutar(entorno=_credenciales(tmp_path), desde=DESDE, hasta=HASTA, subir=Bucket(), dormir=lambda s: None,
                 firmar=lambda cuenta, ahora: "jwt")
    filas = pg.execute("SELECT fuente, campana, gasto_cents, motivo FROM panel_campanas_sin_regla").fetchall()
    assert [f[:3] for f in filas] == [("meta", "Marca general", 1234)]
    assert filas[0][3].startswith("no es general (friday) ni lleva año")
    assert pg.execute("SELECT DISTINCT campana_id, titulo, tipo_campana, ambito, modelo, sin_mercado FROM panel_campana_dia "
                      "WHERE origen = 'real'").fetchall() == [("111", "VN T-Roc Noviembre", "modelo", "VN", "T-Roc", False)]
    estado = pg.execute("SELECT detalle FROM fuentes_estado WHERE fuente = 'meta' ORDER BY id DESC LIMIT 1").fetchone()[0]
    assert estado["campanas_clasificadas"] == 1 and estado["campanas_sin_regla"] == 1


def test_el_repositorio_no_trae_campanas_ni_credenciales():
    c = cmp.cargar()
    assert c.asignaciones == {} and c.equivalencias == {} and c.meta_version.startswith("v")
    texto = (Path(cmp.RAIZ) / "config" / "campanas.yaml").read_text()
    assert "EAA" not in texto and "private_key" not in texto
