"""Conector de la API de Walcu con respuestas de ejemplo: nunca sale a la red (no hay credencial ni entorno de pruebas).

Privacidad (lista blanca, seudónimos, nada personal en la base, el bucket ni el registro), solo GET, paginación por
página y por cursor, incremental con marca de agua e idempotencia, 401/403, estados y location sin mapear,
traspaso de sede, venta anulada, HMAC estable y Walcu por semanas generado desde la API con cita por showed_up.
"""

from __future__ import annotations

import dataclasses
import gzip
import io
import json
import logging
import re
import urllib.error
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from ingesta import conectores as cx
from ingesta import walcu_api as wa
from ingesta import walcu_crm as crm

RAIZ = Path(__file__).resolve().parent.parent
CLAVE_API = "clave-api-de-walcu-SECRETA-123"
DEALER = "d0000000000000000000beef"
SEUD = "c" * 64
ENTORNO = {"WALCU_API_KEY": CLAVE_API, "WALCU_DEALER_ID": DEALER, "WALCU_SEUDONIMO_CLAVE": SEUD}
DESDE = date(2026, 9, 1)
MOMENTO = datetime(2026, 11, 20, 5, 30, tzinfo=timezone.utc)
HOY = date(2026, 11, 20)

# Datos personales falsos: ninguno puede aparecer en la base, el bucket ni el registro
PERSONALES = ["Juan Pérez García", "juan.perez@correo.invalid", "600123456", "+34 611 22 33 44", "12345678Z",
              "Calle Falsa 123", "41001", "Le interesa financiar, llamar a María López", "X1234567L",
              "Comentario libre de Ana Ruiz"]
# Identificadores que solo pueden ir seudonimizados
EN_CLARO = ["1234 BCD", "WVWZZZ1KZAW000001", "WVWZZZ1KZAW000002", "c1c1c1c1c1c1c1c1c1c1c1c1", "c2c2c2c2c2c2c2c2c2c2c2c2",
            "u1u1u1u1u1u1u1u1u1u1u1u1", "u9u9u9u9u9u9u9u9u9u9u9u9"]


def _cliente(cid: str) -> dict:
    """Lo que trae computed de un lead: todo personal."""
    return {"client_primary_contact": {"name": {"first_name": "Juan", "last_name": "Pérez García",
                                                "raw_name": "Juan Pérez García"},
                                       "phones": ["600123456"], "emails": ["juan.perez@correo.invalid"],
                                       "government_id": "12345678Z"},
            "client_phones": ["+34 611 22 33 44"], "client_emails": ["juan.perez@correo.invalid"],
            "client_address": {"route": "Calle Falsa 123", "postal_code": "41001"},
            "notes": ["Le interesa financiar, llamar a María López"], "client_id_original": cid}


def coche(i: str, tipo="used", modelo="Golf", location="SEDE-VO", estado="Disponible", publicado=True,
          precio=18500, actualizado="2026-09-05T10:00:00Z", vin="WVWZZZ1KZAW000001", compra="2026-09-05T08:00:00Z",
          historia=None) -> dict:
    return {"_id": i, "type": tipo, "make": "Marca", "model": modelo, "version": "1.5 TSI Life", "fuel": "Gasolina",
            "provider_codes": {"eurotax": "EUR123", "_id": "x"}, "location": location, "purchase_date": compra,
            "created_at": "2026-09-04T08:00:00Z", "updated_at": actualizado, "published": publicado,
            "unpublished_at": None if publicado else actualizado,
            "prices": {"retail": {"current_price": {"price": precio, "currency": "EUR"},
                                  "previous_prices": [{"price": precio + 1000}]}},
            "mileage": 42000, "year": 2022, "color": "Blanco", "license_plate": "1234 BCD", "vin": vin,
            "status": {"status": estado, "assigned_at": actualizado, "user": "u1u1u1u1u1u1u1u1u1u1u1u1"},
            "status_history": historia or [{"status": "Entrada", "assigned_at": "2026-09-05T08:00:00Z",
                                            "user": "u1u1u1u1u1u1u1u1u1u1u1u1"},
                                           {"status": estado, "assigned_at": actualizado,
                                            "user": "u1u1u1u1u1u1u1u1u1u1u1u1"}],
            "description": "Coche de Juan Pérez García, llamar al 600123456", "custom": "Comentario libre de Ana Ruiz",
            "computed": {"car_main_image": "https://img.invalid/1.jpg", "price": precio},
            "external_ids": [{"type": "dni", "value": "X1234567L"}]}


def lead(i: str, alta: str, campana: str, location: str, estado: str, historia: list, car_id="car1", tipo="used",
         modelo="Golf", comercial="u1u1u1u1u1u1u1u1u1u1u1u1", cliente="c1c1c1c1c1c1c1c1c1c1c1c1",
         actualizado=None, source="facebook") -> dict:
    return {"_id": i, "client_id": cliente, "created_at": alta, "updated_at": actualizado or alta,
            "origin": {"source": source, "medium": "paid", "campaign": campana}, "location": location,
            "status": {"status": estado, "assigned_at": historia[-1][1], "user": comercial},
            "status_history": [{"status": e, "assigned_at": f, "user": comercial,
                                "substatus_history": [{"substatus": "Llamada a Juan Pérez García"}]}
                               for e, f in historia],
            "current_assigned": {"to": comercial, "at": alta, "by": "u9u9u9u9u9u9u9u9u9u9u9u9"},
            "car_list": [{"car_id": car_id, "car": {"type": tipo, "make": "Marca", "model": modelo,
                                                    "license_plate": "1234 BCD", "vin": "WVWZZZ1KZAW000002"}}],
            "inquiry": "Hola, soy Juan Pérez García (juan.perez@correo.invalid)",
            "provider": {"name": "Portal", "from": "juan.perez@correo.invalid", "subject": "Juan Pérez García"},
            "notification": {"reason": "Comentario libre de Ana Ruiz"}, "computed": _cliente(cliente),
            "custom": "Comentario libre de Ana Ruiz", "finance": {"down_payment": 3000}}


def cita(i: str, lead_id: str, asistio: bool, inicio="2026-09-10T09:00:00Z", actualizado="2026-09-10T12:00:00Z") -> dict:
    return {"_id": i, "title": "Cita con Juan Pérez García", "start_date": inicio, "end_date": inicio,
            "client_id": "c1c1c1c1c1c1c1c1c1c1c1c1", "lead_id": lead_id, "lead_type": "salelead",
            "salelead": {"car_id": "car1", "offer_id": "o1"}, "showed_up": asistio, "status": "Realizada",
            "created_at": inicio, "updated_at": actualizado, "computed": {"lead_location": "SEDE-VO"},
            "notification": {"reason": "Llamar a Juan Pérez García al 600123456"}}


def datos_base() -> dict[str, list[dict]]:
    return {
        "cars": [coche("car1"),
                 coche("car2", tipo="new", modelo="T-Roc", location="SEDE-VN", precio=31000, vin="WVWZZZ1KZAW000002",
                       actualizado="2026-10-01T10:00:00Z", compra=None),
                 coche("car3", modelo="Ibiza", location="OTRA-MARCA", vin=None, actualizado="2026-10-02T10:00:00Z")],
        "saleleads": [
            lead("lead1", "2026-09-07T09:00:00Z", "VO Golf ocasión septiembre", "SEDE-VO", "Vendido",
                 [("Nuevo", "2026-09-07T09:00:00Z"), ("Firmado", "2026-09-20T10:00:00Z"),
                  ("Vendido", "2026-09-25T10:00:00Z")], actualizado="2026-09-25T10:00:00Z"),
            lead("lead2", "2026-09-08T09:00:00Z", "[VN] T-Roc", "SEDE-VN", "En curso",
                 [("Nuevo", "2026-09-08T09:00:00Z"), ("En curso", "2026-09-09T09:00:00Z")], car_id="car2",
                 tipo="new", modelo="T-Roc", cliente="c2c2c2c2c2c2c2c2c2c2c2c2"),
            lead("lead3", "2026-09-09T09:00:00Z", "SK Fabia septiembre", "SEDE-VN", "Vendido",
                 [("Vendido", "2026-09-15T09:00:00Z")], tipo="new", modelo="Fabia"),
            lead("lead4", "2026-09-09T10:00:00Z", "Campaña general", "OTRA-MARCA", "Vendido",
                 [("Vendido", "2026-09-16T09:00:00Z")]),
            lead("lead5", "2026-09-10T10:00:00Z", "[VN] T-Roc", "SEDE-VN", "Vendido",
                 [("Vendido", "2026-09-17T09:00:00Z")], comercial="u9u9u9u9u9u9u9u9u9u9u9u9", tipo="new",
                 modelo="T-Roc")],
        "dates": [cita("date1", "lead1", True), cita("date2", "lead2", False)],
    }


class Walcu:
    """API de Walcu simulada: filtra por updated_at, pagina (página o cursor) y guarda cada petición."""

    def __init__(self, datos: dict[str, list[dict]], cfg: wa.Config, ignora_filtro=False, errores=None):
        self.datos, self.cfg, self.ignora_filtro = datos, cfg, ignora_filtro
        self.peticiones: list = []
        self.errores = list(errores or [])

    def __call__(self, peticion, timeout):
        self.peticiones.append(peticion)
        if self.errores:
            codigo = self.errores.pop(0)
            raise urllib.error.HTTPError(peticion.full_url, codigo, "x", {},
                                         io.BytesIO(json.dumps({"error": {"message": "Juan Pérez García"}}).encode()))
        partes = urlsplit(peticion.full_url)
        assert partes.path.startswith(f"/dealers/{DEALER}/")
        recurso = partes.path.split("/")[3]
        q = {k: v[0] for k, v in parse_qs(partes.query).items()}
        elementos = sorted(self.datos[recurso], key=lambda e: e["_id"])
        if not self.ignora_filtro:
            desde, hasta = wa.instante(q[self.cfg.filtro_desde]), wa.instante(q[self.cfg.filtro_hasta])
            elementos = [e for e in elementos if desde <= wa.instante(e["updated_at"]) < hasta]
        p = self.cfg.paginacion
        if p["tipo"] == "pagina":
            n, tam = int(q[p["pagina"]["parametro"]]), int(q[p["pagina"]["parametro_tamano"]])
            ini = (n - int(p["pagina"]["primera"])) * tam
            cuerpo = elementos[ini:ini + tam]
        else:
            tam = int(q[p["cursor"]["parametro_tamano"]])
            ini = int(q.get(p["cursor"]["parametro"], 0))
            trozo = elementos[ini:ini + tam]
            sig = str(ini + tam) if ini + tam < len(elementos) else ""
            cuerpo = {p["elementos"]: trozo, p["cursor"]["siguiente"]: sig, "total": len(elementos)}
        return _Respuesta(json.dumps(cuerpo).encode())


class _Respuesta(io.BytesIO):
    headers: dict = {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class Bucket:
    def __init__(self):
        self.ficheros: dict[str, bytes] = {}

    def __call__(self, carpeta: Path, destino: str):
        for f in carpeta.iterdir():
            self.ficheros[f"{destino}/{f.name}"] = gzip.decompress(f.read_bytes())


MAPEO = {"estados_vehiculo": {"vendido": ["Vendido"], "reservado": ["Reservado"], "baja": ["Baja"],
                              "publicado": ["Disponible"], "finales": ["Vendido", "Baja"]},
         "estados_lead": {"venta": ["Vendido"], "hitos_venta": {"firmado": ["Firmado"], "matriculado": [],
                                                                "entregado": []},
                          "descartado": ["Perdido"], "finales": [], "num_inexistente": ["Número inexistente"]},
         "locations": {"incluidas": [], "excluidas": ["OTRA-MARCA"], "vo": ["SEDE-VO"]},
         "tipos": {"VN": ["new"], "VO": ["used", "km0", "demo"]}, "semanal": {"generar": True}}


@pytest.fixture
def cfg():
    return wa.cargar()


@pytest.fixture
def cfg_mapeado(cfg):
    return dataclasses.replace(cfg, mapeo=MAPEO)


def _cargar(pg, monkeypatch, cfg, datos, momento=MOMENTO, desde=DESDE, entorno=ENTORNO, bucket=None, **red):
    w = Walcu(datos, cfg, **red)
    monkeypatch.setattr(cx, "_urlopen", w)
    bucket = bucket if bucket is not None else Bucket()
    resumen = wa.ejecutar_con(pg, momento.date(), entorno, cfg, desde=desde, subir=bucket, dormir=lambda s: None,
                              momento=momento)
    return resumen, w, bucket


def _todo_el_texto(pg) -> str:
    """Todas las filas de todas las tablas de la base, como texto."""
    tablas = [t for (t,) in pg.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' "
                                       "AND table_type = 'BASE TABLE'")]
    return "\n".join(str(fila) for t in tablas for fila in pg.execute(f'SELECT * FROM "{t}"'))


# ================================================================== privacidad

def test_nada_personal_en_la_base_el_bucket_ni_el_registro(pg, monkeypatch, cfg_mapeado, caplog):
    caplog.set_level(logging.DEBUG)
    resumen, w, bucket = _cargar(pg, monkeypatch, cfg_mapeado, datos_base())
    assert pg.execute("SELECT count(*) FROM walcu_api_lead").fetchone()[0] == 5
    assert bucket.ficheros, "el JSON filtrado va al bucket"
    texto_base = _todo_el_texto(pg)
    texto_bucket = "\n".join(b.decode("utf-8") for b in bucket.ficheros.values())
    registro = caplog.text + json.dumps(resumen, default=str, ensure_ascii=False)
    for valor in PERSONALES + EN_CLARO + [CLAVE_API, SEUD]:
        for donde, texto in (("base", texto_base), ("bucket", texto_bucket), ("registro", registro)):
            assert valor not in texto, (valor, donde)
    # El bucket guarda solo la lista blanca: ningún campo fuera de ella
    for nombre, contenido in bucket.ficheros.items():
        for e in json.loads(contenido):
            for k in e:
                assert k in {c.split(".")[0].removesuffix("[]") for r in cfg_mapeado.recursos.values()
                             for c in r.campos}, (nombre, k)
            assert "computed" not in e and "custom" not in e and "description" not in e
    # Seudónimos: el mismo cliente, el mismo seudónimo; HMAC, no el valor
    seud = dict(pg.execute("SELECT id_walcu, cliente_seudo FROM walcu_api_lead_vigente"))
    assert seud["lead1"] == wa.seudonimo(SEUD, "c1c1c1c1c1c1c1c1c1c1c1c1") != seud["lead2"]
    assert re.fullmatch(r"[0-9a-f]{64}", seud["lead1"])
    b, m = pg.execute("SELECT bastidor_seudo, matricula_seudo FROM walcu_api_vehiculo_vigente "
                      "WHERE id_walcu = 'car1'").fetchone()
    assert b == wa.seudonimo(SEUD, "WVWZZZ1KZAW000001") and m == wa.seudonimo(SEUD, "1234BCD")
    usuarios = {u for (u,) in pg.execute("SELECT usuario_seudo FROM walcu_api_lead_estado")}
    assert wa.seudonimo(SEUD, "u1u1u1u1u1u1u1u1u1u1u1u1") in usuarios
    # El error de la API no copia su mensaje (podría llevar datos personales)
    monkeypatch.setattr(cx, "_urlopen", Walcu({}, cfg_mapeado, errores=[400]))
    with pytest.raises(cx.ErrorDescarga) as exc:
        wa.Cliente(cfg_mapeado, CLAVE_API, DEALER, lambda s: None).get("/cars/all", {})
    assert "Juan" not in str(exc.value) and "400" in str(exc.value)


def test_la_lista_blanca_no_admite_campos_personales(tmp_path):
    texto = (RAIZ / "config" / "walcu_api.yaml").read_text(encoding="utf-8")
    for campo in ("computed.client_phones", "client_primary_contact.name", "notes", "custom",
                  "car_list[].car.license_plate.owner_name", "inquiry", "provider.from", "title"):
        malo = texto.replace("      - origin.campaign\n", f"      - origin.campaign\n      - {campo}\n", 1)
        (tmp_path / "w.yaml").write_text(malo, encoding="utf-8")
        with pytest.raises(wa.ErrorConfiguracion, match="campos personales"):
            wa.cargar(tmp_path / "w.yaml")
    # Un campo personalizado confirmado como no personal sí se admite
    bueno = texto.replace("      - origin.campaign\n", "      - origin.campaign\n      - custom.canal\n", 1).replace(
        "personalizados_no_personales: []", "personalizados_no_personales: [custom.canal]")
    (tmp_path / "w.yaml").write_text(bueno, encoding="utf-8")
    assert "custom.canal" in wa.cargar(tmp_path / "w.yaml").recursos["saleleads"].campos


def test_filtro_descarta_en_memoria_y_vacia_textos_con_aspecto_personal(cfg):
    f = wa.Filtro(cfg.recursos["saleleads"], SEUD)
    e = lead("l", "2026-09-07T09:00:00Z", "Campaña de juan.perez@correo.invalid", "S", "Nuevo",
             [("Nuevo", "2026-09-07T09:00:00Z")])
    salida = f(e)
    assert salida["origin"]["campaign"] is None and f.cuentas["vaciados_por_parecer_personales"] == 1
    assert set(salida) <= {"_id", "client_id", "created_at", "updated_at", "origin", "location", "status",
                           "status_history", "current_assigned", "car_list"}
    assert set(salida["car_list"][0]["car"]) == {"type", "make", "model"}          # ni matrícula ni bastidor
    assert set(salida["status_history"][0]) == {"status", "assigned_at", "user"}    # sin substatus_history
    assert f.cuentas["descartados"] > 0
    with pytest.raises(wa.ErrorConfiguracion):
        wa.Filtro(cfg.recursos["cars"], "")


def test_hmac_estable_y_con_clave():
    a = wa.seudonimo(SEUD, "WVWZZZ1KZAW000001")
    assert a == wa.seudonimo(SEUD, "wvwzzz1kzaw000001") == wa.seudonimo(SEUD, " WVW-ZZZ 1KZAW000001 ")
    assert re.fullmatch(r"[0-9a-f]{64}", a)
    assert a == wa.seudonimo(SEUD, "WVWZZZ1KZAW000001")                    # siempre el mismo
    assert a != wa.seudonimo("d" * 64, "WVWZZZ1KZAW000001")                # otra clave, otro seudónimo
    assert a != wa.seudonimo(SEUD, "WVWZZZ1KZAW000002")
    import hashlib
    assert a != hashlib.sha256(b"WVWZZZ1KZAW000001").hexdigest()          # no es un hash sin clave


def test_sin_clave_de_seudonimo_no_guarda_nada(pg, monkeypatch, cfg):
    w = Walcu(datos_base(), cfg)
    monkeypatch.setattr(cx, "_urlopen", w)
    with pytest.raises(wa.ErrorConfiguracion):
        wa.ejecutar_con(pg, HOY, {**ENTORNO, "WALCU_SEUDONIMO_CLAVE": ""}, cfg, desde=DESDE, subir=Bucket(),
                        momento=MOMENTO)
    assert not w.peticiones and pg.execute("SELECT count(*) FROM walcu_api_lead").fetchone()[0] == 0


# ================================================================== solo GET

def test_el_cliente_solo_hace_get(pg, monkeypatch, cfg):
    for metodo in ("post", "put", "patch", "delete", "request"):
        assert not hasattr(wa.Cliente, metodo)
    _, w, _ = _cargar(pg, monkeypatch, cfg, datos_base())
    assert w.peticiones and {p.get_method() for p in w.peticiones} == {"GET"}
    assert all(p.data is None for p in w.peticiones)
    assert all(p.get_header("Authorization") == CLAVE_API for p in w.peticiones)
    # En el código del conector no hay otro método HTTP
    fuente = (RAIZ / "ingesta" / "walcu_api.py").read_text(encoding="utf-8")
    assert re.findall(r'metodo="(\w+)"', fuente) == ["GET"]
    assert not re.search(r"\b(POST|PUT|PATCH|DELETE)\b", fuente)


def test_sin_credenciales_no_sale_a_la_red(pg, monkeypatch, cfg):
    def prohibida(*a, **k):
        raise AssertionError("sin credenciales no se sale a la red")
    monkeypatch.setattr(cx, "_urlopen", prohibida)
    monkeypatch.setattr("ingesta.scrapers_vw.hoy_madrid", lambda: HOY)
    assert wa.ejecutar({}) == 0
    assert pg.execute("SELECT estado FROM fuentes_estado WHERE fuente = 'walcu_api'").fetchone()[0] == "sin_credenciales"


# ================================================================== paginación

def test_paginacion_por_pagina(pg, monkeypatch, cfg):
    cfg = dataclasses.replace(cfg, paginacion={**cfg.paginacion, "tamano": 2})
    datos = datos_base()
    datos["cars"] = [coche(f"car{i:03d}", actualizado=f"2026-09-{1 + i % 20:02d}T10:00:00Z") for i in range(5)]
    _, w, _ = _cargar(pg, monkeypatch, cfg, datos)
    assert pg.execute("SELECT count(*) FROM walcu_api_vehiculo_vigente").fetchone()[0] == 5
    paginas = [parse_qs(urlsplit(p.full_url).query) for p in w.peticiones if "/cars/" in p.full_url]
    # Septiembre (5 coches): páginas 1, 2 y 3; octubre y noviembre, sin coches: una página cada uno
    assert [(q["page"][0], q["limit"][0]) for q in paginas] == [("1", "2"), ("2", "2"), ("3", "2"), ("1", "2"),
                                                                 ("1", "2")]


def test_paginacion_por_cursor(pg, monkeypatch, cfg):
    pag = {**cfg.paginacion, "tipo": "cursor", "tamano": 2, "elementos": "data"}
    cfg = dataclasses.replace(cfg, paginacion=pag)
    datos = datos_base()
    datos["saleleads"] = [lead(f"lead{i}", f"2026-09-{7 + i:02d}T09:00:00Z", "[VN] T-Roc", "SEDE-VN", "Nuevo",
                               [("Nuevo", f"2026-09-{7 + i:02d}T09:00:00Z")]) for i in range(5)]
    _, w, _ = _cargar(pg, monkeypatch, cfg, datos)
    assert pg.execute("SELECT count(*) FROM walcu_api_lead_vigente").fetchone()[0] == 5
    leads = [parse_qs(urlsplit(p.full_url).query) for p in w.peticiones if "/saleleads/" in p.full_url]
    assert [q.get("cursor", [None])[0] for q in leads] == [None, "2", "4", None, None]


def test_si_la_api_no_aplica_el_filtro_para(pg, monkeypatch, cfg):
    with pytest.raises(cx.ErrorDescarga, match="filtro por updated_at no se aplica"):
        _cargar(pg, monkeypatch, cfg, datos_base(), ignora_filtro=True)


# ================================================================== incremental e idempotencia

def test_incremental_con_marca_de_agua_e_idempotente(pg, monkeypatch, cfg):
    datos = datos_base()
    r, w, _ = _cargar(pg, monkeypatch, cfg, datos)
    assert r["modo"] == "inicial" and len([p for p in w.peticiones if "/cars/" in p.full_url]) == 3   # sep, oct, nov
    filas = {t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
             for t in ("walcu_api_vehiculo", "walcu_api_lead", "walcu_api_cita", "walcu_api_lead_estado")}
    assert wa.ultima_marca(pg, "cars") == MOMENTO
    # Al día siguiente, nada nuevo: no se duplica nada y la marca avanza; se pide desde la marca menos el solape
    manana = MOMENTO + timedelta(days=1)
    r, w, _ = _cargar(pg, monkeypatch, cfg, datos, momento=manana, desde=None)
    assert r["modo"] == "incremental"
    q = parse_qs(urlsplit(w.peticiones[0].full_url).query)
    assert wa.instante(q["updated_at_from"][0]) == MOMENTO - timedelta(minutes=60)
    assert {t: pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in filas} == filas
    assert wa.ultima_marca(pg, "cars") == manana
    # Baja el precio de un coche: versión nueva, la vigente con el precio nuevo, la anterior se conserva
    datos["cars"][0] = coche("car1", precio=17900, actualizado="2026-11-21T06:00:00Z")
    _cargar(pg, monkeypatch, cfg, datos, momento=manana + timedelta(hours=1), desde=None)
    assert [p for (p,) in pg.execute("SELECT precio_cents FROM walcu_api_vehiculo WHERE id_walcu = 'car1' ORDER BY id")] \
        == [1850000, 1790000]
    # Volver a hacer la carga inicial entera tampoco duplica
    _cargar(pg, monkeypatch, cfg, datos, momento=manana + timedelta(hours=2))
    assert pg.execute("SELECT count(*) FROM walcu_api_vehiculo WHERE id_walcu = 'car1'").fetchone()[0] == 2
    # Foto diaria del precio: una por coche publicado y día
    assert pg.execute("SELECT count(*) FROM walcu_api_vehiculo_precio_dia WHERE vehiculo_id = 'car1'").fetchone()[0] == 2


def test_incremental_sin_carga_inicial_lo_dice(pg, monkeypatch, cfg):
    with pytest.raises(cx.ErrorDescarga, match="sin carga inicial"):
        _cargar(pg, monkeypatch, cfg, datos_base(), desde=None)


# ================================================================== 401 y 403

@pytest.mark.parametrize("codigo", [401, 403])
def test_credencial_rechazada_para_y_lo_dice(pg, monkeypatch, cfg, codigo, caplog):
    w = Walcu(datos_base(), cfg, errores=[codigo] * 10)
    monkeypatch.setattr(cx, "_urlopen", w)
    monkeypatch.setattr("ingesta.scrapers_vw.hoy_madrid", lambda: HOY)
    assert wa.ejecutar(ENTORNO, desde=DESDE, subir=Bucket(), dormir=lambda s: None) == 1
    assert len(w.peticiones) == 1                               # sin reintentos
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE fuente = 'walcu_api'").fetchone()
    assert estado == "error" and f"HTTP {codigo}" in detalle["error"] and "rechazó la credencial" in detalle["error"]
    assert CLAVE_API not in json.dumps(detalle) and CLAVE_API not in caplog.text and "Juan" not in caplog.text
    assert pg.execute("SELECT count(*) FROM walcu_api_marca").fetchone()[0] == 0


def test_error_de_servidor_se_reintenta(pg, monkeypatch, cfg):
    _, w, _ = _cargar(pg, monkeypatch, cfg, datos_base(), errores=[503, 502])
    assert pg.execute("SELECT count(*) FROM walcu_api_vehiculo_vigente").fetchone()[0] == 3


def test_la_alerta_de_fuente_caida_incluye_walcu():
    from alertas import reglas
    assert "walcu_api" in reglas.CONECTORES


# ================================================================== mapeo pendiente

def test_sin_mapeo_descarga_pero_no_deduce_ventas_ni_excluye_sedes(pg, monkeypatch, cfg):
    resumen, _, _ = _cargar(pg, monkeypatch, cfg, datos_base())
    assert "mapeo pendiente" in resumen["crm"]["estado"]
    assert resumen["crm"]["sin_mapear"]["estados_lead"] > 0 and resumen["crm"]["sin_mapear"]["locations"] == 3
    for t in ("panel_ventas_crm", "panel_stock_vehiculo"):
        assert pg.execute(f"SELECT count(*) FROM {t}").fetchone()[0] == 0
    assert pg.execute("SELECT count(*) FROM walcu_campana_semana").fetchone()[0] == 0
    inv = crm.inventario(pg, cfg)
    assert ("Vendido", 4, None) in inv["estados_lead"]
    assert {lo for lo, *_ in inv["locations"]} == {"SEDE-VO", "SEDE-VN", "OTRA-MARCA"}
    texto = "\n".join(crm.texto_inventario(inv))
    assert "«Vendido»: 4 leads · SIN MAPEAR" in texto and "«OTRA-MARCA»: 1 coches, 1 leads · SIN MAPEAR" in texto
    assert "Mapeo pendiente" in texto
    for valor in PERSONALES + EN_CLARO:
        assert valor not in texto


# ================================================================== con el mapeo: semanas, ventas y stock

def test_walcu_por_semanas_desde_la_api_con_cita_por_showed_up(pg, monkeypatch, cfg_mapeado, caplog):
    entorno = {**ENTORNO, "WALCU_COMERCIALES_EXCLUIDOS": "u9u9u9u9u9u9u9u9u9u9u9u9"}
    resumen, _, _ = _cargar(pg, monkeypatch, cfg_mapeado, datos_base(), entorno=entorno)
    c = resumen["crm"]
    assert c["semanal"]["escrito"] and c["exclusiones"] == {
        "leidos": 5, "comerciales_excluidos": 1, "otra_sede": 1, "otra_empresa": 0, "otra_marca": 1, "sin_fecha": 0}
    filas = pg.execute("""SELECT semana, origen, campana, tipo, leads, leads_sin_zona, citas, ventas, fuente
                          FROM walcu_campana_semana_vigente ORDER BY campana""").fetchall()
    assert filas == [
        (date(2026, 9, 7), "Facebook", "[VN] T-Roc", "VN", 1, 1, 0, 0, "api"),     # cita sin asistir: no es cita
        (date(2026, 9, 7), "Facebook", "VO Golf ocasión septiembre", "VO", 1, 1, 1, 1, "api"),
    ]
    assert c["acuerdo_tipo_pct"] == 100.0 and c["tipo_por"] == {"campana": 2}
    assert "u9u9u9u9u9u9u9u9u9u9u9u9" not in caplog.text
    # Sin cambios, al día siguiente no se escribe otra carga
    r2, _, _ = _cargar(pg, monkeypatch, cfg_mapeado, datos_base(), momento=MOMENTO + timedelta(days=1), desde=None,
                       entorno=entorno)
    assert r2["crm"]["semanal"]["escrito"] is False
    assert pg.execute("SELECT count(DISTINCT run_id) FROM walcu_campana_semana").fetchone()[0] == 1
    # Ventas del CRM: la de lead1, en septiembre (el mes del Firmado), VO Golf
    assert pg.execute("SELECT mes, tipo, modelo, ventas FROM panel_ventas_crm").fetchall() == [
        (date(2026, 9, 1), "VO", "Golf", 1)]


def test_acuerdo_del_tipo_con_car_list(pg, monkeypatch, cfg_mapeado):
    datos = datos_base()
    # El nombre dice VO, pero car_list dice nuevo: el tipo sigue la regla y el acuerdo baja
    datos["saleleads"][1] = lead("lead2", "2026-09-08T09:00:00Z", "VO Ocasión", "SEDE-VN", "En curso",
                                 [("Nuevo", "2026-09-08T09:00:00Z")], tipo="new")
    resumen, _, _ = _cargar(pg, monkeypatch, cfg_mapeado, datos)
    assert resumen["crm"]["acuerdo_tipo_pct"] == pytest.approx(100 * 2 / 3, abs=0.1)


def test_traspaso_de_sede_no_es_venta(pg, monkeypatch, cfg_mapeado):
    datos = datos_base()
    _cargar(pg, monkeypatch, cfg_mapeado, datos)
    stock = {s for (s,) in pg.execute("SELECT stock_id FROM panel_stock_vehiculo WHERE origen = 'real'")}
    assert stock == {"car1", "car2"}                         # car3 es de una location excluida
    ventas = pg.execute("SELECT sum(ventas) FROM panel_ventas_crm").fetchone()[0]
    # car2 pasa a otra sede que cuenta: mismo id, sigue en stock; después, a una excluida: sale sin ser venta
    datos["cars"][1] = {**datos["cars"][1], "location": "SEDE-VN-2", "updated_at": "2026-11-20T20:00:00Z"}
    m1 = MOMENTO + timedelta(days=1)
    _cargar(pg, monkeypatch, cfg_mapeado, datos, momento=m1, desde=None)
    assert pg.execute("SELECT count(*) FROM walcu_api_vehiculo WHERE id_walcu = 'car2'").fetchone()[0] == 2
    assert {s for (s,) in pg.execute("SELECT stock_id FROM panel_stock_vehiculo WHERE dia = %s", (m1.date(),))} == \
        {"car1", "car2"}
    datos["cars"][1] = {**datos["cars"][1], "location": "OTRA-MARCA", "updated_at": "2026-11-21T20:00:00Z"}
    m2 = MOMENTO + timedelta(days=2)
    _cargar(pg, monkeypatch, cfg_mapeado, datos, momento=m2, desde=None)
    assert {s for (s,) in pg.execute("SELECT stock_id FROM panel_stock_vehiculo WHERE dia = %s", (m2.date(),))} == {"car1"}
    assert pg.execute("SELECT sum(ventas) FROM panel_ventas_crm").fetchone()[0] == ventas
    # Los días en stock salen de la fecha de entrada real (purchase_date; sin ella, created_at)
    dias = dict(pg.execute("SELECT stock_id, dias_exposicion FROM panel_stock_vehiculo WHERE dia = %s", (m1.date(),)))
    assert dias == {"car1": (m1.date() - date(2026, 9, 5)).days, "car2": (m1.date() - date(2026, 9, 4)).days}


def test_venta_anulada_vuelve_al_stock_y_deja_de_contar(pg, monkeypatch, cfg_mapeado):
    datos = datos_base()
    datos["cars"][0] = coche("car1", estado="Vendido", publicado=False, actualizado="2026-09-25T10:00:00Z")
    _cargar(pg, monkeypatch, cfg_mapeado, datos)
    assert "car1" not in {s for (s,) in pg.execute("SELECT stock_id FROM panel_stock_vehiculo")}
    vo = "SELECT coalesce(sum(ventas), 0) FROM panel_ventas_crm WHERE tipo = 'VO'"
    assert pg.execute(vo).fetchone()[0] == 1
    # Se anula: el coche vuelve a publicarse y el lead sale del estado de venta
    datos["cars"][0] = coche("car1", estado="Disponible", publicado=True, actualizado="2026-11-20T20:00:00Z",
                             historia=[{"status": "Vendido", "assigned_at": "2026-09-25T10:00:00Z"},
                                       {"status": "Disponible", "assigned_at": "2026-11-20T20:00:00Z"}])
    datos["saleleads"][0] = lead("lead1", "2026-09-07T09:00:00Z", "VO Golf ocasión septiembre", "SEDE-VO", "En curso",
                                 [("Nuevo", "2026-09-07T09:00:00Z"), ("Firmado", "2026-09-20T10:00:00Z"),
                                  ("Vendido", "2026-09-25T10:00:00Z"), ("En curso", "2026-11-20T20:00:00Z")],
                                 actualizado="2026-11-20T20:00:00Z")
    m1 = MOMENTO + timedelta(days=1)
    _cargar(pg, monkeypatch, cfg_mapeado, datos, momento=m1, desde=None)
    assert "car1" in {s for (s,) in pg.execute("SELECT stock_id FROM panel_stock_vehiculo WHERE dia = %s", (m1.date(),))}
    assert pg.execute(vo).fetchone()[0] == 0
    assert pg.execute("SELECT ventas FROM walcu_campana_semana_vigente WHERE tipo = 'VO'").fetchone()[0] == 0
    # La historia de estados del coche se conserva entera
    assert [e for (e,) in pg.execute("SELECT estado FROM walcu_api_vehiculo_estado WHERE vehiculo_id = 'car1' "
                                     "ORDER BY fecha")] == ["Entrada", "Vendido", "Disponible"]


def test_ventas_del_crm_en_direccion(pg, monkeypatch, cfg_mapeado):
    from panel import salidas as sal
    _cargar(pg, monkeypatch, cfg_mapeado, datos_base())
    tarjeta, bloque = sal.crm(pg, "real", "todo", date(2026, 10, 5))     # septiembre es el mes anterior
    assert tarjeta["etiqueta"] == "Ventas del mes" and tarjeta["valor"] == "0" and "ventas del CRM" in tarjeta["sub"]
    # Septiembre: la VO de lead1 y la VN de lead5 (aquí sin comerciales excluidos)
    assert bloque["total_anterior"]["n"] == "2" and bloque["grupos"][1]["filas"][0]["modelo"] == "Golf"
    assert bloque["grupos"][0]["anterior"]["n"] == "1" and bloque["grupos"][1]["anterior"]["n"] == "1"
    tarjeta, _ = sal.crm(pg, None, "todo", HOY)
    assert not tarjeta["hay"]


def test_precio_provisional_con_la_fecha_de_entrada_del_crm(pg, monkeypatch, cfg_mapeado):
    from analitica import precio
    _cargar(pg, monkeypatch, cfg_mapeado, datos_base())
    assert precio.enlace_crm(pg, {}) is None                            # sin la clave: como hasta ahora
    entrada = precio.enlace_crm(pg, ENTORNO)
    assert entrada("wvwzzz1kzaw000001") == date(2026, 9, 5) and entrada("OTRO") is None and entrada(None) is None


# ================================================================== probar

def test_probar_solo_nombres_tipos_y_recuentos(pg, monkeypatch, cfg):
    datos = datos_base()
    for recurso in datos.values():               # que estén en los últimos 7 días
        for e in recurso:
            e["updated_at"] = "2026-11-18T10:00:00Z"
    w = Walcu(datos, cfg)
    monkeypatch.setattr(cx, "_urlopen", w)
    lineas: list[str] = []
    assert wa.probar(ENTORNO, cfg, lineas.append, ahora=MOMENTO) == 0
    texto = "\n".join(lineas)
    assert len(w.peticiones) == 3 and {p.get_method() for p in w.peticiones} == {"GET"}
    assert "computed.client_primary_contact.phones: lista 5; en 5 de 5; se descarta" in texto
    assert "client_id: texto 5; en 5 de 5; seudónimo" in texto
    assert "status_history[].status: texto" in texto and "showed_up: booleano 2; en 2 de 2; se guarda" in texto
    assert "actualizados antes de lo pedido: 0" in texto and "No se ha guardado nada" in texto
    for valor in PERSONALES + EN_CLARO + ["Golf", "SEDE-VO", "Vendido", "18500"]:
        assert valor not in texto, valor
    assert pg.execute("SELECT count(*) FROM walcu_api_lead").fetchone()[0] == 0


# ================================================================== replicable

def test_nada_del_concesionario_en_el_codigo_del_conector():
    prohibidas = re.compile(r"\b(volkswagen|avisa|sevilla)\b", re.IGNORECASE)
    for p in (RAIZ / "ingesta" / "walcu_api.py", RAIZ / "ingesta" / "walcu_crm.py"):
        for i, linea in enumerate(p.read_text(encoding="utf-8").splitlines(), 1):
            assert not prohibidas.search(linea), f"{p.name}:{i}"
    texto = (RAIZ / "config" / "walcu_api.yaml").read_text(encoding="utf-8")
    assert "a definir con AVISA" in texto.replace("A definir", "a definir")   # el mapeo lo da cada concesionario


def test_ejecutar_anota_el_resumen_en_fuentes_estado(pg, monkeypatch, cfg):
    monkeypatch.setattr(cx, "_urlopen", Walcu(datos_base(), cfg))
    monkeypatch.setattr("ingesta.scrapers_vw.hoy_madrid", lambda: HOY)
    monkeypatch.setattr(wa, "cargar", lambda fichero=None: dataclasses.replace(cfg, mapeo=MAPEO))
    assert wa.ejecutar(ENTORNO, desde=DESDE, subir=Bucket(), dormir=lambda s: None) == 0
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE fuente = 'walcu_api'").fetchone()
    assert estado == "ok" and detalle["recursos"]["saleleads"]["elementos"] == 5
    assert "pendiente de confirmar" in detalle["paginacion"] and detalle["crm"]["estado"].startswith("activo")
    for valor in PERSONALES + EN_CLARO + [CLAVE_API]:
        assert valor not in json.dumps(detalle, ensure_ascii=False)
