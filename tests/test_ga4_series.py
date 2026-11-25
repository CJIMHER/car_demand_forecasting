"""Señales semanales de intención de GA4 con respuestas de ejemplo: nunca salen a la red.

Rutas: marca (propia, sin marca, otras marcas del grupo y sus sedes fuera), web comercial, tipos y modelo de las
fichas. Series: sesiones por página de destino, vistas de fichas y eventos por página, por tipo, grupo de canal y
marca; vistas por modelo; brutos al bucket antes de escribir; recargar sin cambios no añade nada y con cambios
sustituye la semana sin borrar; la descarga diaria las pone al día; solo agregados.
"""

from __future__ import annotations

import io
import json
from datetime import date, timedelta

import pytest

from ingesta import conectores as cx
from ingesta import ga4
from ingesta import ga4_paginas as gp
from ingesta import ga4_series as gs
from ingesta.ga4_explorar import Cliente

HOY = date(2026, 11, 25)                     # miércoles: la última semana terminada es la del 16/11
S1, S2, S3 = date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)
INFORME = "https://analyticsdata.googleapis.com/v1beta/properties/4567:runReport"
TOKEN = "ya29.token-SECRETO"
USUARIO = {"userId", "user_pseudo_id", "clientId", "customUser:email", "userEmail"}


# ================================================================== rutas

@pytest.mark.parametrize("ruta, excluida, marca, tipo_pagina, tipo, modelo", [
    ("/", None, "sin_marca", "Inicio", None, None),
    ("/vehiculos-ocasion-en-stock", None, "sin_marca", "Listado de ocasión", "VO", None),
    ("/vehiculos-km0", None, "sin_marca", "Listado de km 0", "VO", None),
    ("/vehiculos-nuevos-en-stock", None, "sin_marca", "Listado de nuevos", "VN", None),
    ("/landing-volkswagen-taigo", None, "propia", "Landing de modelo", "VN", "Taigo"),
    ("/landing-t-roc", None, "propia", "Landing de modelo", "VN", "T-Roc"),
    ("/vehiculos-comerciales/volkswagen/caddy/monovolumen-5-puertas/x1", None, "propia", "Ficha de nuevo", "VN",
     "Caddy"),
    ("/vehiculos-comerciales/volkswagen/id-buzz", None, "propia", "Ficha de nuevo", "VN", "ID. Buzz"),
    ("/vehiculos-comerciales/volkswagen", None, "propia", "Listado de nuevos", "VN", None),
    ("/ocasion/volkswagen-golf-life-12345?email=a@b.example", None, "propia", "Ficha de ocasión", "VO", "Golf"),
    ("/configurador/id-4", None, "propia", "Configurador", "VN", None),
    ("/ofertas/black-friday", None, "sin_marca", "Ofertas", None, None),
    ("/financiacion", None, "sin_marca", "Financiación y renting", None, None),
    ("/tasamos-tu-coche", None, "sin_marca", "Tasación", None, None),
    ("/contacto", None, "sin_marca", "Contacto", None, None),
    ("/peticion/corredor-ayuda-humanitaria-gaza", "no comercial", "sin_marca", None, None, None),
    ("/concesionarios/avisa-cordoba", "no comercial", "sin_marca", None, None, None),
    ("/accesorios-originales-comerciales", "no comercial", "sin_marca", None, None, None),
    ("/landing-audi-q3", "otra marca", "otra", None, None, None),
    ("/landing-kamiq", "otra marca", "otra", None, None, None),
    ("/landing-seat-arona", "otra marca", "otra", None, None, None),
    ("/coches/todos-tipos/todas-ciudades/todas-marcas/q3", "otra marca", "otra", None, None, None),
    ("/sede/cartuja-motor-huelva/ofertas", "otra marca", "otra", None, None, None),
])
def test_rutas_reales(ruta, excluida, marca, tipo_pagina, tipo, modelo):
    p = gp.cargar().clasificar(ruta)
    assert (p.excluida, p.marca, p.tipo_pagina, p.tipo, p.modelo) == (excluida, marca, tipo_pagina, tipo, modelo)


def test_hosts_y_errores_de_configuracion(tmp_path):
    f = tmp_path / "p.yaml"
    f.write_text("hosts: [web.ejemplo.es]\ntipos:\n  - {nombre: Inicio, patron: '^/$'}\n")
    p = gp.cargar(f)
    assert p.clasificar("/", "web.ejemplo.es").cuenta and p.clasificar("/", "otra.ejemplo.es").excluida == "dominio"
    for texto, error in (("tipos:\n  - {nombre: Roto, patron: '('}\n", "patrón de «Roto» no válido"),
                         ("tipos:\n  - {patron: x}\n", "necesita «nombre» y «patron»"),
                         ("tipos:\n  - {nombre: X, patron: x, tipo: KM0}\n", "debe ser VN, VO o nada"),
                         ("tipos: []\n", "«tipos» debe ser una lista con algún valor")):
        f.write_text(texto)
        with pytest.raises(gp.ErrorPaginas, match=error):
            gp.cargar(f)


# ================================================================== API simulada

class Respuesta(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _sem(s: date) -> str:
    y, w, _ = s.isocalendar()
    return f"{y}{w:02d}"


FB = ("facebook", "paid", "Paid Social")           # Meta → pagado
GOOGLE = ("google", "organic", "Organic Search")    # → organico
DIRECTO = ("(direct)", "(none)", "Direct")         # → directo
BING = ("bing", "cpc", "Paid Search")              # sin regla propia, agrupación de pago de GA4 → pagado
REF = ("foro.example", "referral", "Referral")     # → otros


def datos_ga4(factor=1):
    """Por semana: filas de sesiones, de vistas y de eventos (sin la semana)."""
    sesiones = [("/landing-t-roc", *GOOGLE, 100 * factor, 60, 3000.5), ("/landing-t-roc", *FB, 50, 20, 800),
                ("/vehiculos-ocasion-en-stock", *DIRECTO, 80, 50, 4000), ("/", *BING, 30, 10, 200),
                ("/landing-audi-q3", *GOOGLE, 999, 999, 9999), ("/peticion/causa", *REF, 500, 100, 100),
                ("/ofertas", *REF, 10, 5, 90)]
    vistas = [("/ocasion/volkswagen-golf-life-12345", *GOOGLE, 40), ("/ocasion/volkswagen-polo-1234", *FB, 15),
              ("/vehiculos-comerciales/volkswagen/caddy/furgon/x", *DIRECTO, 12), ("/landing-t-roc", *GOOGLE, 9),
              ("/vehiculos-ocasion-en-stock", *GOOGLE, 500),          # listado: no es ficha
              ("/landing-audi-q3", *GOOGLE, 300)]                     # otra marca: fuera
    eventos = [("Configurar", "/configurador/t-roc", *GOOGLE, 7), ("COLOR", "/configurador/t-roc", *GOOGLE, 5),
               ("Lead - Nuevos", "/gracias", *FB, 3), ("Lead - Segunda mano", "/gracias", *DIRECTO, 2),
               ("form_start", "/ocasion/volkswagen-golf-life-12345", *GOOGLE, 6),
               ("form_submit", "/landing-t-roc", *FB, 4), ("Solicitar información", "/contacto", *DIRECTO, 1),
               ("Click llamada o email", "/landing-audi-q3", *GOOGLE, 50)]     # otra marca: fuera
    return sesiones, vistas, eventos


class Api:
    def __init__(self, semanas=(S1, S2, S3), datos=None, por_semana=None):
        self.peticiones, self.semanas = [], set(semanas)
        self.datos, self.por_semana = datos or datos_ga4(), por_semana or {}

    def __call__(self, peticion, timeout):
        self.peticiones.append(peticion)
        if peticion.full_url.startswith("https://oauth2.googleapis.com/token"):
            return Respuesta(json.dumps({"access_token": TOKEN}).encode())
        assert peticion.full_url == INFORME, peticion.full_url
        c = json.loads(peticion.data)
        dims = [d["name"] for d in c["dimensions"]]
        r = c["dateRanges"][0]
        desde, hasta = date.fromisoformat(r["startDate"]), date.fromisoformat(r["endDate"])
        filas = []
        for s in sorted(self.semanas):
            if not desde <= s <= hasta:
                continue
            sesiones, vistas, eventos = self.por_semana.get(s, self.datos)
            if dims[1] == "landingPage":
                filas += [([_sem(s), *f[:4]], f[4:]) for f in sesiones]
            elif dims[1] == "pagePath":
                filas += [([_sem(s), *f[:4]], f[4:]) for f in vistas]
            else:
                assert dims[1] == "eventName"
                permitidos = c["dimensionFilter"]["filter"]["inListFilter"]["values"]
                filas += [([_sem(s), *f[:5]], f[5:]) for f in eventos if f[0] in permitidos]
        filas = filas[c["offset"]:c["offset"] + 5]                  # páginas de 5 filas
        cuerpo = {"rows": [{"dimensionValues": [{"value": v} for v in d],
                            "metricValues": [{"value": str(v)} for v in m]} for d, m in filas],
                  "rowCount": sum(1 for _ in self._todas(c, dims, desde, hasta))}
        return Respuesta(json.dumps(cuerpo).encode())

    def _todas(self, c, dims, desde, hasta):
        for s in self.semanas:
            if desde <= s <= hasta:
                sesiones, vistas, eventos = self.por_semana.get(s, self.datos)
                yield from {"landingPage": sesiones, "pagePath": vistas}.get(dims[1], eventos)

    def informes(self):
        return [json.loads(p.data) for p in self.peticiones if p.full_url == INFORME]


class Bucket:
    def __init__(self, pg=None):
        self.subidas, self.pg, self.semanas_al_subir = [], pg, []

    def __call__(self, carpeta, destino):
        if self.pg is not None:
            self.semanas_al_subir.append(self.pg.execute("SELECT count(*) FROM ga4_intencion_carga").fetchone()[0])
        self.subidas.append((destino, sorted(p.name for p in carpeta.iterdir())))


def _cliente(api, monkeypatch):
    monkeypatch.setattr(cx, "_urlopen", api)
    return Cliente(TOKEN, "4567", [TOKEN], dormir=lambda s: None)


def _cfg(**cambios):
    from dataclasses import replace
    return replace(gs.cargar_config(), **cambios)


def _vigente(pg):
    cols = ["tipo", "canal", "marca", *gs.COLUMNAS]
    return {(r[0], r[1], r[2]): dict(zip(cols[3:], r[3:])) for r in pg.execute(
        f"SELECT {', '.join(cols)} FROM ga4_intencion_semana_vigente WHERE semana = %s", (S3,))}


# ================================================================== carga

def test_series_por_tipo_canal_y_marca(pg, monkeypatch):
    api = Api()
    bucket = Bucket(pg)
    resumen = gs.cargar(pg, _cliente(api, monkeypatch), "4567", S1, HOY, HOY, _cfg(semanas_por_tramo=2), subir=bucket)
    assert resumen | {"peticiones": 0} == {"desde": "2026-11-02", "hasta": "2026-11-16", "semanas_nuevas": 3,
                                           "semanas_iguales": 0, "peticiones": 0}
    f = _vigente(pg)
    # sesiones por la página de destino: la landing de T-Roc es VN de la marca propia; Google orgánico, Meta pagado
    assert (f[("VN", "organico", "propia")]["sesiones"], f[("VN", "organico", "propia")]["sesiones_interaccion"],
            f[("VN", "organico", "propia")]["tiempo_interaccion"]) == (100, 60, 3000.5)
    assert f[("VN", "pagado", "propia")]["sesiones"] == 50
    assert f[("VO", "directo", "sin_marca")]["sesiones"] == 80
    assert f[("sin_tipo", "pagado", "sin_marca")]["sesiones"] == 30          # Bing de pago, por la agrupación de GA4
    assert f[("sin_tipo", "otros", "sin_marca")]["sesiones"] == 10           # la petición (500) no cuenta
    assert sum(v["sesiones"] for v in f.values()) == 270                     # ni la landing de Audi (999)
    # vistas de fichas (no de listados ni de otras marcas)
    assert f[("VO", "organico", "propia")]["vistas_fichas"] == 40
    assert f[("VO", "pagado", "propia")]["vistas_fichas"] == 15
    assert f[("VN", "directo", "propia")]["vistas_fichas"] == 12
    assert f[("VN", "organico", "propia")]["vistas_fichas"] == 9             # landing de modelo
    assert sum(v["vistas_fichas"] for v in f.values()) == 76
    # eventos: el configurador y «Lead - Nuevos», VN; «Lead - Segunda mano», VO; el resto, por su página
    assert (f[("VN", "organico", "propia")]["configurar"], f[("VN", "organico", "propia")]["color"]) == (7, 5)
    assert f[("VN", "pagado", "sin_marca")]["lead_nuevos"] == 3
    assert f[("VO", "directo", "sin_marca")]["lead_segunda_mano"] == 2
    assert f[("VO", "organico", "propia")]["form_start"] == 6
    assert f[("VN", "pagado", "propia")]["form_submit"] == 4
    assert f[("sin_tipo", "directo", "sin_marca")]["solicitar_informacion"] == 1
    assert sum(v["click_llamada_email"] for v in f.values()) == 0            # en una página de Audi: fuera
    # vistas por modelo
    assert pg.execute("SELECT tipo, modelo, vistas FROM ga4_fichas_modelo_semana_vigente WHERE semana = %s "
                      "ORDER BY 1, 2", (S3,)).fetchall() == [("VN", "Caddy", 12), ("VN", "T-Roc", 9),
                                                             ("VO", "Golf", 40), ("VO", "Polo", 15)]
    # dos tramos: brutos al bucket antes de escribir cada uno
    assert [d for d, _ in bucket.subidas] == ["ga4_series/raw/2026-11-25"] * 2 and bucket.semanas_al_subir == [0, 2]
    assert all(n.endswith(".json.gz") for _, ns in bucket.subidas for n in ns)


def test_solo_agregados_y_eventos_filtrados(pg, monkeypatch):
    api = Api()
    gs.cargar(pg, _cliente(api, monkeypatch), "4567", S1, HOY, HOY, subir=Bucket())
    informes = api.informes()
    pedidas = {d["name"] for c in informes for d in c["dimensions"]}
    assert not pedidas & USUARIO and "hostName" not in pedidas               # sin hosts en la configuración
    assert {d["name"] for c in informes for d in c["dimensions"]} >= {"isoYearIsoWeek", "landingPage", "pagePath"}
    filtro = next(c["dimensionFilter"] for c in informes if "dimensionFilter" in c)
    assert sorted(filtro["filter"]["inListFilter"]["values"]) == sorted(
        e for e, _ in gs.cargar_config().eventos.values())
    # cada tramo pide de lunes a domingo
    assert {(c["dateRanges"][0]["startDate"], c["dateRanges"][0]["endDate"]) for c in informes} == {
        ("2026-11-02", "2026-11-22")}


def test_recargar_sin_cambios_no_anade_y_con_cambios_sustituye(pg, monkeypatch):
    cli = _cliente(Api(), monkeypatch)
    gs.cargar(pg, cli, "4567", S1, HOY, HOY, subir=Bucket())
    filas = pg.execute("SELECT count(*) FROM ga4_intencion_semana").fetchone()[0]
    r = gs.cargar(pg, cli, "4567", S1, HOY, HOY, subir=Bucket())
    assert (r["semanas_nuevas"], r["semanas_iguales"]) == (0, 3)
    assert pg.execute("SELECT count(*) FROM ga4_intencion_semana").fetchone()[0] == filas
    # GA4 corrige la última semana: solo esa se añade, y la vigente es la nueva
    cambiada = datos_ga4(factor=2)
    cli = _cliente(Api(por_semana={S3: cambiada}), monkeypatch)
    r = gs.cargar(pg, cli, "4567", S1, HOY, HOY, subir=Bucket())
    assert (r["semanas_nuevas"], r["semanas_iguales"]) == (1, 2)
    assert _vigente(pg)[("VN", "organico", "propia")]["sesiones"] == 200
    assert pg.execute("SELECT count(*) FROM ga4_intencion_carga WHERE semana = %s", (S3,)).fetchone()[0] == 2
    with pytest.raises(Exception, match="solo"):                             # de solo añadir
        pg.execute("DELETE FROM ga4_intencion_semana")


def test_no_carga_la_semana_en_curso(pg, monkeypatch):
    r = gs.cargar(pg, _cliente(Api(semanas=(S3, S3 + timedelta(days=7))), monkeypatch), "4567", S3,
                  date(2026, 12, 31), HOY, subir=Bucket())
    assert r["hasta"] == "2026-11-16"
    assert [s for (s,) in pg.execute("SELECT semana FROM ga4_intencion_carga")] == [S3]


def test_configuracion_de_eventos(tmp_path):
    cfg = gs.cargar_config()
    assert cfg.eventos["lead_nuevos"] == ("Lead - Nuevos", "VN") and cfg.eventos["form_start"] == ("form_start", None)
    f = tmp_path / "i.yaml"
    texto = (gs.ruta()).read_text(encoding="utf-8")
    f.write_text(texto.replace("inicio: 2023-06-05", "inicio: 2023-06-06"))
    with pytest.raises(Exception, match="lunes"):
        gs.cargar_config(f)
    f.write_text(texto.replace("  precio: {evento: Precio, tipo: VN}\n", ""))
    with pytest.raises(Exception, match="exactamente las columnas"):
        gs.cargar_config(f)


# ================================================================== descarga diaria

def _credenciales(tmp_path):
    f = tmp_path / "ga4.json"
    f.write_text(json.dumps({"client_email": "lector@x.iam.gserviceaccount.com", "private_key": "x",
                             "token_uri": "https://oauth2.googleapis.com/token"}))
    f.chmod(0o600)
    return {"GA4_PROPERTY_ID": "4567", "GA4_CREDENCIALES": str(f)}


def _red_diaria(api):
    """La descarga diaria (ga4_trafico_dia) responde vacía; las series, con la API simulada."""
    def red(peticion, timeout):
        if peticion.full_url == INFORME:
            dims = [d["name"] for d in json.loads(peticion.data)["dimensions"]]
            if dims[0] == "date":
                api.peticiones.append(peticion)
                return Respuesta(json.dumps({"rowCount": 0}).encode())
        return api(peticion, timeout)
    return red


def test_la_descarga_diaria_pone_al_dia_las_series(pg, monkeypatch, tmp_path):
    monkeypatch.setattr(gs, "ultima_semana_terminada", lambda hoy: S3)
    # Sin carga inicial, la diaria no pide nada de las series
    api = Api()
    monkeypatch.setattr(cx, "_urlopen", _red_diaria(api))
    resumen = ga4.ejecutar_con(pg, HOY, _credenciales(tmp_path), subir=Bucket(), dormir=lambda s: None,
                               firmar=lambda c, t: "jwt")
    assert resumen["series"].startswith("sin carga inicial") and not any(
        [d["name"] for d in c["dimensions"]][0] == "isoYearIsoWeek" for c in api.informes())
    # Con la carga inicial hasta S1, la diaria trae S2 y S3 (las que faltan y las 2 últimas)
    gs.cargar(pg, _cliente(Api(semanas=(S1,)), monkeypatch), "4567", S1, S1, HOY, subir=Bucket())
    monkeypatch.setattr(cx, "_urlopen", _red_diaria(Api()))
    resumen = ga4.ejecutar_con(pg, HOY, _credenciales(tmp_path), subir=Bucket(), dormir=lambda s: None,
                               firmar=lambda c, t: "jwt")
    assert resumen["series"]["desde"] == "2026-11-09" and resumen["series"]["semanas_nuevas"] == 2
    assert [s for (s,) in pg.execute("SELECT semana FROM ga4_intencion_carga_vigente ORDER BY 1")] == [S1, S2, S3]


def test_un_fallo_de_las_series_no_rompe_la_descarga(pg, monkeypatch, tmp_path):
    monkeypatch.setattr(gs, "ultima_semana_terminada", lambda hoy: S3)
    gs.cargar(pg, _cliente(Api(semanas=(S1,)), monkeypatch), "4567", S1, S1, HOY, subir=Bucket())

    def red(peticion, timeout):
        if peticion.full_url == INFORME and "isoYearIsoWeek" in peticion.data.decode():
            raise cx.ErrorDescarga(f"fallo con {TOKEN}")
        return _red_diaria(Api())(peticion, timeout)

    monkeypatch.setattr(cx, "_urlopen", red)
    assert ga4.ejecutar(entorno=_credenciales(tmp_path), subir=Bucket(), dormir=lambda s: None,
                        firmar=lambda c, t: "jwt") == 0
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE fuente = 'ga4'").fetchone()
    assert estado == "ok" and "aviso_series" in detalle and TOKEN not in json.dumps(detalle)
