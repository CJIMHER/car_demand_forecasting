"""Histórico de Meta, carga agregada de Walcu, cruce por nombre, referencias de coste y reglas con y sin
Walcu (hito 1b, walcu-meta-hist). Sin red: Meta responde con respuestas simuladas.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import date
from pathlib import Path

import pytest

from alertas import criterios as cr
from alertas import motor
from ingesta import campanas as cmp
from ingesta import conectores as cx
from ingesta import meta, walcu
from ingesta import referencias_meta as ref
from ingesta.concesionario import cargar as cargar_concesionario
from test_conectores import ENTORNO_META, Bucket, Red, Respuesta, _fila_meta, http

HOY = date(2026, 11, 20)
CABECERA = ",".join(walcu.COLUMNAS)


def csv_walcu(*filas: str, cabecera: str = CABECERA) -> bytes:
    return ("\n".join([cabecera, *filas]) + "\n").encode("utf-8")


FILAS_BUENAS = (
    "2025-03,Facebook,VW [VN][2025] T-Roc marzo,VN,20,14,4,2,4,1,2",
    "2025-03,Facebook,VW [VN][2025] T-Roc marzo,VO,5,5,0,0,1,1,0",
    "2025-03,AiVORA,(sin campaña),VN,30,30,0,0,10,5,0",
    "2026-11,Facebook,\"Sin título 22/1/25, 15:18\",VN,3,1,1,1,0,0,0",
)


# ================================================================== validador de walcu-cargar

def test_valida_y_carga_un_fichero_correcto(pg):
    bucket = Bucket()
    r = walcu.cargar_con(pg, csv_walcu(*FILAS_BUENAS), "/Users/alguien/Descargas/walcu.csv", HOY, subir=bucket)
    assert r["filas"] == 4 and r["desde"] == "2025-03" and r["hasta"] == "2026-11"
    filas = pg.execute("SELECT fichero_origen, mes, origen, campana, tipo, leads, citas, ventas FROM walcu_campana_mes "
                       "ORDER BY mes, origen, tipo").fetchall()
    assert len(filas) == 4 and {f[0] for f in filas} == {"walcu.csv"}           # sin la ruta del Mac
    assert filas[0][1] == date(2025, 3, 1)
    assert bucket.subidas[0][0] == f"walcu/agregado/{HOY}"                         # al bucket antes de escribir


def test_rechaza_un_fichero_con_un_correo_y_no_carga_nada(pg):
    contenido = csv_walcu(FILAS_BUENAS[0], "2025-04,Facebook,Promo ana.perez@correo.es,VN,1,1,0,0,0,0,0")
    with pytest.raises(walcu.ErrorWalcu) as exc:
        walcu.cargar_con(pg, contenido, "walcu.csv", HOY, subir=Bucket())
    texto = str(exc.value)
    assert "fila 3, columna campana: parece un correo (@)" in texto and "ana.perez" not in texto
    assert pg.execute("SELECT count(*) FROM walcu_campana_mes").fetchone()[0] == 0


def test_rechaza_una_columna_de_mas_y_no_carga_nada(pg):
    contenido = csv_walcu(FILAS_BUENAS[0] + ",600123456", cabecera=CABECERA + ",telefono")
    with pytest.raises(walcu.ErrorWalcu) as exc:
        walcu.cargar_con(pg, contenido, "walcu.csv", HOY, subir=Bucket())
    assert "columnas que no están en la lista cerrada: «telefono»" in str(exc.value)
    assert pg.execute("SELECT count(*) FROM walcu_campana_mes").fetchone()[0] == 0


@pytest.mark.parametrize("campana, que", [
    ("Llamar 612345678", "9 dígitos seguidos"), ("Llamar 612 34 56 78", "un teléfono"),
    ("Cliente 12345678Z", "un DNI"), ("Cliente X1234567L", "un NIE"),
])
def test_rechaza_telefonos_dni_y_nie(campana, que):
    with pytest.raises(walcu.ErrorWalcu) as exc:
        walcu.validar(csv_walcu(f"2025-04,Facebook,{campana},VN,1,1,0,0,0,0,0"))
    assert que in str(exc.value) and campana.split()[-1] not in str(exc.value)


@pytest.mark.parametrize("fila, motivo", [
    ("2025-13,Facebook,X,VN,1,1,0,0,0,0,0", "columna mes: no es AAAA-MM"),
    ("2025-04,Facebook,X,KM0,1,1,0,0,0,0,0", "columna tipo: debe ser VN o VO"),
    ("2025-04,Facebook,X,VN,-1,1,0,0,0,0,0", "columna leads: no es un entero no negativo"),
    ("2025-04,Facebook,,VN,1,1,0,0,0,0,0", "columna campana: vacía"),
])
def test_rechaza_formatos(fila, motivo):
    with pytest.raises(walcu.ErrorWalcu) as exc:
        walcu.validar(csv_walcu(fila))
    assert motivo in str(exc.value)


def test_falta_una_columna_o_se_repite_una_clave():
    with pytest.raises(walcu.ErrorWalcu, match="faltan columnas: num_inexistente"):
        walcu.validar(csv_walcu("2025-04,Facebook,X,VN,1,1,0,0,0,0", cabecera=CABECERA.rsplit(",", 1)[0]))
    with pytest.raises(walcu.ErrorWalcu, match="repite mes, origen, campaña y tipo de la fila 2"):
        walcu.validar(csv_walcu(FILAS_BUENAS[0], FILAS_BUENAS[0]))


def test_zonas_que_no_suman_es_aviso_no_rechazo():
    v = walcu.validar(csv_walcu("2025-04,Facebook,X,VN,22,17,0,1,1,0,2"))
    assert v.avisos == ["1 filas en que leads_cercana + leads_fuera + leads_sin_zona no suma leads"]


def test_el_mismo_fichero_no_se_carga_dos_veces_y_recargar_un_mes_lo_sustituye(pg):
    walcu.cargar_con(pg, csv_walcu(*FILAS_BUENAS), "a.csv", HOY, subir=Bucket())
    assert "ya_cargado" in walcu.cargar_con(pg, csv_walcu(*FILAS_BUENAS), "a.csv", HOY, subir=Bucket())
    assert pg.execute("SELECT count(*) FROM walcu_campana_mes").fetchone()[0] == 4
    # Nueva carga de marzo de 2025 con otras cifras: sustituye a marzo; noviembre sigue
    walcu.cargar_con(pg, csv_walcu("2025-03,Facebook,VW [VN][2025] T-Roc marzo,VN,25,20,4,1,5,2,0"), "b.csv", HOY,
                     subir=Bucket())
    vig = pg.execute("SELECT mes, campana, tipo, leads FROM walcu_campana_mes_vigente ORDER BY mes, tipo").fetchall()
    assert vig == [(date(2025, 3, 1), "VW [VN][2025] T-Roc marzo", "VN", 25),
                   (date(2026, 11, 1), "Sin título 22/1/25, 15:18", "VN", 3)]
    assert pg.execute("SELECT count(*) FROM walcu_campana_mes").fetchone()[0] == 5   # nada se borra


# ================================================================== normalización de nombres

@pytest.mark.parametrize("walcu_nombre, meta_nombre", [
    ("SK [VN] [2026] Gama Ahorro-copy", "SK [VN] [2026] Gama Ahorro"),
    ("SK [VN] [2026] Škoda Nuevo ELROQ-copy", "sk [vn] [2026] skoda nuevo elroq"),
    ("VW [VN][2026]  Taigo   Noviembre - Copia", "VW [VN][2026] Taigo noviembre"),
    ("T-Roc noviembre-OK", "T-Roc Noviembre"),
    ("T-Roc noviembre (copy 2)", "t-roc noviembre"),
    ("Promo Ocasión copia", "PROMO OCASION"),
])
def test_normalizacion_de_nombres(walcu_nombre, meta_nombre):
    c = ref.cargar()
    assert c.normalizar(walcu_nombre) == c.normalizar(meta_nombre)


def test_la_normalizacion_no_quita_palabras_que_solo_terminan_igual():
    c = ref.cargar()
    assert c.normalizar("Taigo Look") == "taigo look"           # «look» no es el sufijo «ok»
    assert c.normalizar("Golf Book") == "golf book"


def test_equivalencias_de_config(tmp_path):
    f = tmp_path / "campanas_walcu.yaml"
    f.write_text("origenes_meta: [Facebook]\nsufijos: [copy]\n"
                 "equivalencias: {'Leads Taigo': 'VW [VN][2026] Taigo noviembre', 'Otra': 'id:999'}\n", encoding="utf-8")
    c = ref.cargar(f)
    assert c.equivalencias == {"leads taigo": "vw [vn][2026] taigo noviembre", "otra": "id:999"}


# ================================================================== cruce y referencias

def _meta(pg, dia, cid, nombre, gasto_euros, leads=3, impresiones=1000, clics=20):
    fila = meta.leer_fila(_fila_meta(str(dia), campana=cid, nombre=nombre, gasto=str(gasto_euros)), "campana", "1234")
    fila.update(impresiones=impresiones, clics=clics, leads=leads)
    cx.insertar_versionado(pg, "campanas_meta_dia", "campanas_meta_dia_vigente", meta.CLAVE, meta.VALORES, [fila],
                           {"descargado_at": cx.ahora(), "run_id": "prueba", "origen": "prueba"})


def _walcu(pg, *filas, nombre="w.csv"):
    walcu.cargar_con(pg, csv_walcu(*filas), nombre, HOY, subir=Bucket())


def _escenario(pg):
    # 2025: una de VN (T-Roc) y una de ambos (Black Friday) cuyo gasto se reparte por sus leads de Walcu
    _meta(pg, date(2025, 3, 10), "1", "VW [VN][2025] T-Roc marzo", 300, leads=30)
    _meta(pg, date(2025, 3, 11), "2", "VW [2025] Black Friday", 200, leads=20)
    _meta(pg, date(2025, 3, 12), "3", "Campaña sin leads en Walcu", 50, leads=1)
    # 2026: la de VO, con «-copy» en Walcu
    _meta(pg, date(2026, 11, 10), "4", "VW [VO][2026] Golf noviembre", 400, leads=40)
    _walcu(pg,
           "2025-03,Facebook,VW [VN][2025] T-Roc marzo,VN,20,14,4,2,4,1,2",
           "2025-03,Facebook,VW [2025] Black Friday,VN,15,10,5,0,3,1,0",
           "2025-03,Facebook,VW [2025] Black Friday,VO,5,5,0,0,2,1,0",
           "2025-03,Facebook,Leads sin campaña de Meta,VN,7,7,0,0,0,0,0",
           "2025-03,AiVORA,(sin campaña),VN,100,100,0,0,50,20,0",
           "2026-11,Facebook,VW [VO][2026] Golf noviembre-copy,VO,10,8,2,0,2,1,1")


def test_cruce_y_referencias_por_anio_y_tipo(pg):
    _escenario(pg)
    r = ref.leer_referencias(pg)
    # 2025 · VN: los leads de Walcu de Facebook de VN (sin AiVORA): 20 + 15 + 7; gasto: T-Roc entero y ¾ de Black Friday
    assert (r[(2025, "VN")]["leads"], r[(2025, "VN")]["citas"], r[(2025, "VN")]["ventas"]) == (42, 7, 2)
    assert r[(2025, "VN")]["gasto_cents"] == 30_000 + 15_000
    assert r[(2025, "VO")]["gasto_cents"] == 5_000 and r[(2025, "VO")]["leads"] == 5
    # «todo»: todo el gasto (también la campaña que no casa) y todos los leads de Facebook
    assert r[(2025, "todo")]["gasto_cents"] == 55_000 and r[(2025, "todo")]["leads"] == 47
    assert r[(2025, "todo")]["leads_meta"] == 51
    assert r[(2026, "VO")]["gasto_cents"] == 40_000 and r[(2026, "VO")]["citas"] == 2
    m = ref.metricas(r[(2025, "VN")])
    assert m["coste_cita"] == pytest.approx(450 / 7) and m["coste_venta"] == pytest.approx(225)
    assert m["lead_cita"] == pytest.approx(7 / 42 * 100) and m["cita_venta"] == pytest.approx(2 / 7 * 100)
    assert m["fuera_zona"] == pytest.approx(9 / 40 * 100) and m["inexistente"] == pytest.approx(2 / 42 * 100)
    # Panel por campaña y mes: el «-copy» casa; la de Walcu sin campaña de Meta va con campana_id NULL
    golf = pg.execute("SELECT cruce, leads, citas, ventas, gasto_cents FROM panel_campana_mes WHERE campana_id = '4'"
                      ).fetchone()
    assert golf == ("nombre", 10, 2, 1, 40_000)
    assert pg.execute("SELECT leads FROM panel_campana_mes WHERE campana_id IS NULL AND titulo = 'Leads sin campaña de Meta'"
                      ).fetchone() == (7,)
    assert pg.execute("SELECT leads, cruce FROM panel_campana_mes WHERE campana_id = '3'").fetchone() == (None, None)
    informe = dict(pg.execute("SELECT campana, motivo FROM panel_walcu_sin_cruce").fetchall())
    assert set(informe) == {"Leads sin campaña de Meta", "Campaña sin leads en Walcu"}
    texto = ref.informe(pg)
    assert "Coste por cita" in texto and "Diferencia" in texto and "Sin cruce entre Meta y Walcu: 2" in texto


def test_equivalencia_resuelve_lo_que_no_casa(pg, tmp_path, monkeypatch):
    f = tmp_path / "campanas_walcu.yaml"
    f.write_text("origenes_meta: [Facebook]\nsufijos: [copy, copia, ok]\n"
                 "equivalencias: {'Leads sin campaña de Meta': 'Campaña sin leads en Walcu'}\n", encoding="utf-8")
    monkeypatch.setenv("CDF_CAMPANAS_WALCU", str(f))
    _escenario(pg)
    assert pg.execute("SELECT cruce, leads FROM panel_campana_mes WHERE campana_id = '3'").fetchone() == ("equivalencia", 7)
    assert pg.execute("SELECT count(*) FROM panel_walcu_sin_cruce").fetchone()[0] == 0


# ================================================================== reglas con y sin Walcu

@pytest.fixture
def conc():
    return cargar_concesionario()


def _campana_real(pg, cid, titulo, gasto_dia, dias, *, leads_meta=5, impresiones=10_000, clics=500, ambito="VN"):
    inicio = HOY.replace(day=HOY.day - dias + 1)
    for i in range(dias):
        _meta(pg, inicio.replace(day=inicio.day + i), cid, titulo, gasto_dia, leads=leads_meta,
              impresiones=impresiones, clics=clics)
    cmp.actualizar(pg)          # panel_campana_dia y el cruce, como tras la descarga de Meta


def test_reglas_sin_walcu_dicen_sin_datos_de_walcu(pg, conc):
    _campana_real(pg, "9", "VW [VN][2026] Golf noviembre", 100, 5)
    cmp.actualizar(pg)
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    j = js[0]
    assert j.fuente_leads == cr.META
    for k in ("a", "d", "subir"):
        assert j.reglas[k].estado == "sin_dato" and "sin datos de Walcu: no se aplica" in j.reglas[k].cifras_texto
    assert "no hay carga de Walcu de noviembre de 2026" in j.reglas["a"].cifras_texto
    # c con los leads de Meta: 500 € / 25 leads = 20 € > 10 €: revisar, sin coste por cita
    assert j.reglas["c"].decision == "revisar" and "25 leads según Meta" in j.reglas["c"].cifras_texto
    assert "Sin datos de Walcu" in j.reglas["c"].revisar


def test_reglas_con_walcu_del_mes_y_coste_por_cita_frente_a_2025(pg, conc):
    crit = motor.cargar_criterios()
    # Referencia de 2025 (VN): 450 € / 7 citas = 64,29 € por cita
    _escenario(pg)
    # Campaña de noviembre de 2026: 5 días a 100 € = 500 €; en Walcu 20 leads (25 € por lead > 10 €) y 8 citas
    _campana_real(pg, "9", "VW [VN][2026] Golf noviembre", 100, 5)
    _walcu(pg, "2026-11,Facebook,VW [VN][2026] Golf noviembre,VN,20,10,8,2,8,1,0",
           "2026-11,Facebook,VW [VO][2026] Golf noviembre-copy,VO,10,8,2,0,2,1,1", nombre="nov.csv")
    _, js = cr.juzgar(pg, HOY, "real", conc, crit, {})
    j = next(x for x in js if x.campana_id == "9")
    assert j.fuente_leads == cr.WALCU_MES and (j.leads, j.citas, j.ventas) == (20, 8, 1)
    # a: fuera de zona 8 de 18 con código postal = 44,44 % > 35 %
    assert j.reglas["a"].activa and "fuera de zona: 8 de 18 leads con código postal = 44,44 % (umbral 35 %)" \
        in j.reglas["a"].cifras_texto
    # c: coste por cita 62,50 € ≤ 64,29 € de 2025 (VN): continuar
    c = j.reglas["c"]
    assert c.decision == "continuar" and "frente a la referencia de 2025 (VN) de 64,29 €" in c.cifras_texto
    assert "leads según Meta" not in c.cifras_texto
    # d: 1 de 8 citas = 12,5 % < 33 % con coste por lead (25 €) por encima de 2025 (450/42 = 10,71 €): no salta
    assert j.reglas["d"].estado == "no" and "por encima de la referencia de 2025 (VN)" in j.reglas["d"].cifras_texto
    # f compara días: con la carga mensual usa los leads de Meta por día
    assert j.reglas["f"].cifras.get("fuente_leads", "meta") == "meta"


def test_regla_c_parar_si_el_coste_por_cita_supera_la_referencia(pg, conc):
    _escenario(pg)
    _campana_real(pg, "9", "VW [VN][2026] Golf noviembre", 100, 5)
    _walcu(pg, "2026-11,Facebook,VW [VN][2026] Golf noviembre,VN,20,20,0,0,7,1,0", nombre="nov.csv")
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    c = next(x for x in js if x.campana_id == "9").reglas["c"]
    assert c.decision == "parar" and "coste por cita: 71,43 € frente a la referencia de 2025 (VN) de 64,29 €" \
        in c.cifras_texto


def test_regla_d_dice_que_es_el_optimo(pg):
    _, reglas = motor.cargar_reglas()
    d = next(r for r in reglas if r.id == "meta_d")
    assert "El 33 % es el óptimo: esta alerta saltará a menudo." in d.texto(None)


def test_campana_sin_cruce_con_walcu_usa_los_leads_de_meta(pg, conc):
    _escenario(pg)
    _campana_real(pg, "9", "VW [VN][2026] Golf noviembre", 100, 5)
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    j = next(x for x in js if x.campana_id == "9")
    assert j.fuente_leads == cr.META and "la campaña no casa con ningún lead de Walcu" in j.reglas["a"].cifras_texto


# ================================================================== histórico de Meta

def test_tramos_de_un_mes():
    assert meta.tramos(date(2025, 1, 15), date(2025, 3, 2)) == [
        (date(2025, 1, 15), date(2025, 1, 31)), (date(2025, 2, 1), date(2025, 2, 28)), (date(2025, 3, 1), date(2025, 3, 2))]


@pytest.mark.parametrize("uso, espera", [
    ({}, 0), ({"1": [{"type": "ads_insights", "call_count": 10, "total_cputime": 5, "total_time": 3}]}, 0),
    ({"1": [{"call_count": 80, "total_cputime": 5, "total_time": 3}]}, meta.ESPERA_FRENAR),
    ({"1": [{"call_count": 10, "total_cputime": 95, "total_time": 3}]}, meta.ESPERA_PARAR),
    ({"1": [{"call_count": 100, "estimated_time_to_regain_access": 7}]}, 420),
])
def test_pausa_por_el_uso_de_la_api(uso, espera):
    assert meta.pausa_por_uso({"x-business-use-case-usage": json.dumps(uso)}) == espera


class ConCabeceras(Respuesta):
    headers = {"X-Business-Use-Case-Usage": json.dumps({"1": [{"call_count": 92}]})}


def test_historico_por_meses_con_archivadas_sin_duplicar_y_esperando_al_limite(pg, monkeypatch):
    base = "https://graph.facebook.com/v24.0/act_1234/"

    def respuestas():
        return [
            (base + "campaigns", [{"data": [{"id": "111", "name": "VN T-Roc Noviembre", "status": "ARCHIVED",
                                             "effective_status": "ARCHIVED"}]}]),
            (base + "insights", [http(429, {"error": {"message": "x", "code": 4}}),
                                 {"data": [_fila_meta("2025-01-31")]},
                                 {"data": [_fila_meta("2025-02-01", campana="222", nombre="Borrada")]}]),
        ]
    red = Red(respuestas())
    original = red.__call__

    def con_uso(peticion, timeout):
        r = original(peticion, timeout)
        return ConCabeceras(r.getvalue()) if "insights" in peticion.full_url else r
    monkeypatch.setattr(cx, "_urlopen", con_uso)
    esperas = []
    bucket = Bucket(pg)
    resumen = meta.ejecutar_historico_con(pg, HOY, ENTORNO_META, date(2025, 1, 20), date(2025, 2, 10), subir=bucket,
                                          dormir=esperas.append)
    assert [t["mes"] for t in resumen["tramos"]] == ["2025-01", "2025-02"]
    urls = [p.full_url for p in red.peticiones]
    insights = [u for u in urls if "insights" in u]
    assert all("ARCHIVED" in u and "DELETED" in u and "level=campaign" in u for u in insights)
    assert all("Authorization" in dict(p.header_items()) for p in red.peticiones)
    assert all(TOKEN not in u for u in urls for TOKEN in [ENTORNO_META["META_ACCESS_TOKEN"]])
    assert meta.ESPERA_PARAR in esperas                         # 92 % del límite: espera antes de seguir
    assert len(bucket.subidas) == 3                              # estados y un tramo por mes, antes de escribir
    assert pg.execute("SELECT estado_efectivo FROM campanas_meta_estado_vigente").fetchone() == ("ARCHIVED",)
    n = pg.execute("SELECT count(*) FROM campanas_meta_dia").fetchone()[0]
    assert n == 2
    # Relanzar: las mismas filas no se duplican
    monkeypatch.setattr(cx, "_urlopen", Red([(base + "campaigns", respuestas()[0][1]),
                                             (base + "insights", respuestas()[1][1][1:])]))
    meta.ejecutar_historico_con(pg, HOY, ENTORNO_META, date(2025, 1, 20), date(2025, 2, 10), subir=Bucket(),
                                dormir=lambda s: None)
    assert pg.execute("SELECT count(*) FROM campanas_meta_dia").fetchone()[0] == n
    assert pg.execute("SELECT count(*) FROM campanas_meta_estado").fetchone()[0] == 1


def test_historico_sin_desde_o_sin_credenciales_falla_claro(monkeypatch):
    monkeypatch.setattr(cx, "_urlopen", lambda *a: (_ for _ in ()).throw(AssertionError("red")))
    assert meta.ejecutar(entorno={}, historico=True) == 1


# ================================================================== script del Mac

EXPORTACION = ["Nombre", "Teléfono", "Email", "Origen", "Detalle campaña", "Tipo de vehículo solicitado", "Fecha Lead",
               "Id negociación creada", "Resultado última actividad", "Estado", "Motivo fracaso", "Código postal",
               "Vendedor"]


def _leads(conc):
    cerca, lejos = conc.zona_cercana[0] + "001", ("28" if conc.zona_cercana[0] != "28" else "08") + "001"
    return [
        ["Ana Pérez", "612345678", "ana@correo.es", "Facebook", "VW [VN][2026] Golf noviembre", "Nuevo",
         "03/11/2026 10:15", "N-1", "Visita en exposición", "Negociación creada (Cerrada / Vendido)", "", cerca,
         "Comercial Uno"],
        ["Luis Gómez", "699111222", "luis@correo.es", "facebook", "VW [VN][2026] Golf noviembre", "Nuevo",
         "04/11/2026 11:00", "", "", "Perdido", "Número inexistente", lejos, "Comercial Dos"],
        ["Eva Ruiz", "600000001", "eva@correo.es", "AiVORA", "", "VO", "2026-10-30 09:00", "N-2", "", "En curso",
         "", "", "Comercial Uno"],
        ["Raro", "600000002", "r@correo.es", "Portal raro", "Llamar al 612345678", "Km0", "30/10/2026", "", "", "",
         "", "1234", "Comercial Tres"],
        # Negociación creada, pero la última actividad es un presupuesto por teléfono: no es cita (ni venta)
        ["Pepe Mora", "600000003", "p@correo.es", "Facebook", "VW [VN][2026] Golf noviembre", "Nuevo",
         "05/11/2026 12:00", "N-3", "Presupuesto por teléfono", "En curso (Cerrada / Vendido)", "", cerca,
         "Comercial Dos"],
        # Lead de un comercial excluido (cuando lo está): se descarta antes de agregar
        ["Sara Gil", "600000004", "s@correo.es", "Facebook", "VW [VN][2026] Golf noviembre", "Nuevo",
         "06/11/2026 12:00", "N-4", "Visita en exposición", "En curso (Cerrada / Vendido)", "", cerca,
         "Comercial Excluido"],
    ]


@pytest.fixture
def wa(tmp_path, monkeypatch):
    """El script, sin leer nunca el fichero de comerciales excluidos real del Mac."""
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
    import walcu_agregar
    monkeypatch.setattr(walcu_agregar, "EXCLUIDOS", tmp_path / "no_existe" / "comerciales_excluidos.txt")
    return walcu_agregar


def _exportacion(tmp_path, conc, formato="csv"):
    entrada = tmp_path / f"leads.{formato}"
    if formato == "csv":
        with entrada.open("w", encoding="utf-8", newline="") as f:
            csv.writer(f, delimiter=";").writerows([EXPORTACION, *_leads(conc)])
    else:
        _xlsx(entrada, [EXPORTACION, *_leads(conc)])
    return entrada


def _filas(texto):
    return {(f["mes"], f["origen"], f["campana"], f["tipo"]): f for f in csv.DictReader(io.StringIO(texto))}


PERSONALES = ("Ana", "Pérez", "Sara", "612345678", "699111222", "correo", "Comercial", "Excluido", "N-1")


def _xlsx(ruta: Path, filas: list[list[str]]) -> None:
    """Un .xlsx mínimo con cadenas compartidas (como los del CRM)."""
    compartidas, indice = [], {}

    def s(v):
        if v not in indice:
            indice[v] = len(compartidas)
            compartidas.append(v)
        return indice[v]
    letras = "ABCDEFGHIJKLMNOP"
    xml_filas = "".join(
        f'<row r="{i}">' + "".join(f'<c r="{letras[j]}{i}" t="s"><v>{s(v)}</v></c>' for j, v in enumerate(fila) if v)
        + "</row>" for i, fila in enumerate(filas, 1))
    ns = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
    rel = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
    from xml.sax.saxutils import escape
    with zipfile.ZipFile(ruta, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook {ns} {rel}><sheets><sheet name="Leads" sheetId="1" r:id="rId1"/>'
                                      "</sheets></workbook>")
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Target="worksheets/sheet1.xml" Type="x"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f"<sst {ns}>" + "".join(f"<si><t>{escape(v)}</t></si>" for v in compartidas)
                   + "</sst>")
        z.writestr("xl/worksheets/sheet1.xml", f"<worksheet {ns}><sheetData>{xml_filas}</sheetData></worksheet>")


@pytest.mark.parametrize("formato", ["csv", "xlsx"])
def test_script_agrega_sin_datos_personales(tmp_path, conc, capsys, wa, formato):
    entrada = _exportacion(tmp_path, conc, formato)
    salida = tmp_path / "walcu.csv"
    assert wa.main([str(entrada), str(salida)]) == 0
    texto = salida.read_text(encoding="utf-8")
    registro = capsys.readouterr().out
    for personal in PERSONALES:
        assert personal not in texto and personal not in registro, personal
    filas = _filas(texto)
    assert list(csv.reader(io.StringIO(texto)))[0] == list(walcu.COLUMNAS)
    golf = filas[("2026-11", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]
    # Sin excluidos: 4 leads (Ana, Luis, Pepe, Sara); citas: Ana y Sara (Pepe, presupuesto por teléfono, no)
    assert (golf["leads"], golf["leads_cercana"], golf["leads_fuera"], golf["citas"], golf["ventas"],
            golf["num_inexistente"]) == ("4", "3", "1", "2", "2", "1")
    assert "de comerciales excluidos (no cuentan): 0" in registro
    assert "no cuenta como cita por su última actividad: 1" in registro
    assert filas[("2026-10", "AiVORA", "(sin campaña)", "VO")]["leads_sin_zona"] == "1"
    raro = filas[("2026-10", "otros", "(campaña omitida)", "VO")]
    assert raro["leads_fuera"] == "1"                       # «1234» → 01234: provincia 01, fuera de la zona
    walcu.validar(salida.read_bytes())                      # lo que escribe lo acepta el servidor


@pytest.mark.parametrize("formato", ["csv", "xlsx"])
def test_script_descarta_los_leads_de_comerciales_excluidos(tmp_path, conc, capsys, wa, formato):
    excluidos = tmp_path / "fuera_del_repo" / "comerciales_excluidos.txt"
    excluidos.parent.mkdir()
    excluidos.write_text("# uno por línea\n  comercial excluido \n\nOtro Que No Está\n", encoding="utf-8")
    salida = tmp_path / "walcu.csv"
    assert wa.main([str(_exportacion(tmp_path, conc, formato)), str(salida), "--excluidos", str(excluidos)]) == 0
    texto, registro = salida.read_text(encoding="utf-8"), capsys.readouterr().out
    golf = _filas(texto)[("2026-11", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]
    assert (golf["leads"], golf["citas"], golf["ventas"]) == ("3", "1", "1")      # sin el lead de Sara
    assert "de comerciales excluidos (no cuentan): 1" in registro
    for personal in PERSONALES + ("Otro Que No Está",):     # ni los nombres del fichero de excluidos
        assert personal not in texto and personal not in registro, personal


def test_fichero_de_excluidos_por_defecto(tmp_path, conc, capsys, wa, monkeypatch):
    por_defecto = tmp_path / "cdf_walcu" / "comerciales_excluidos.txt"
    por_defecto.parent.mkdir()
    por_defecto.write_text("Comercial Excluido\n", encoding="utf-8")
    monkeypatch.setattr(wa, "EXCLUIDOS", por_defecto)
    assert wa.EXCLUIDOS.name == "comerciales_excluidos.txt"
    salida = tmp_path / "walcu.csv"
    assert wa.main([str(_exportacion(tmp_path, conc)), str(salida)]) == 0
    assert "de comerciales excluidos (no cuentan): 1" in capsys.readouterr().out


def test_por_defecto_en_cdf_walcu_y_sin_el_fichero_no_excluye_nada(wa):
    import importlib
    original = importlib.reload(wa)
    assert original.EXCLUIDOS == Path.home() / "cdf_walcu" / "comerciales_excluidos.txt"


def test_fichero_de_excluidos_indicado_que_no_existe_no_escribe_nada(tmp_path, conc, capsys, wa):
    salida = tmp_path / "walcu.csv"
    assert wa.main([str(_exportacion(tmp_path, conc)), str(salida), "--excluidos", str(tmp_path / "nada.txt")]) == 1
    assert not salida.exists() and "comerciales excluidos" in capsys.readouterr().err


def test_sin_excluidos_la_columna_del_comercial_no_se_lee(tmp_path, conc, wa):
    cols = [c for c in EXPORTACION if c != "Vendedor"]
    entrada = tmp_path / "leads.csv"
    with entrada.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f).writerows([cols, *[fila[:-1] for fila in _leads(conc)]])
    assert wa.main([str(entrada), str(tmp_path / "walcu.csv")]) == 0
    assert wa.main([str(entrada), str(tmp_path / "otra.csv"), "--excluidos", str(_vacio(tmp_path))]) == 0


def _vacio(tmp_path):
    f = tmp_path / "vacio.txt"
    f.write_text("", encoding="utf-8")
    return f


def test_actividades_que_no_cuentan_como_cita_en_la_configuracion(wa):
    assert wa.cargar_config()["actividades_no_cita"] == ["PRESUPUESTO POR TELÉFONO"]


# ---- valores reales de la exportación y otras marcas

def _lead(campana="VW [VN][2026] Golf noviembre", tipo="Nuevo", negociacion="N-9", estado="", motivo="",
          actividad="Visita en exposición"):
    return {"origen": "Facebook", "campana": campana, "tipo": tipo, "fecha": "10/11/2026 10:00",
            "negociacion": negociacion, "resultado_actividad": actividad, "estado": estado, "motivo_fracaso": motivo,
            "codigo_postal": "", "comercial": ""}


def _agregar(wa, conc, *leads):
    ag = wa.Agregador(wa.cargar_config(), conc)
    for lead in leads:
        ag.anadir(lead)
    return ag, _filas(ag.csv().decode())


@pytest.mark.parametrize("estado, venta", [
    ("Negociación creada (Cerrada / Vendido)", 1), ("En curso (Cerrada / Vendido)", 1),
    ("En curso", 0), ("Negociación creada (Cerrada / Perdido)", 0), ("", 0),
])
def test_venta_es_un_estado_que_contiene_vendido(wa, conc, estado, venta):
    _, filas = _agregar(wa, conc, _lead(estado=estado))
    assert filas[("2026-11", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]["ventas"] == str(venta)


@pytest.mark.parametrize("motivo, n", [("Número inexistente", 1), ("NUMERO INEXISTENTE", 1), ("No contesta", 0)])
def test_numero_inexistente(wa, conc, motivo, n):
    _, filas = _agregar(wa, conc, _lead(motivo=motivo, negociacion=""))
    assert filas[("2026-11", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]["num_inexistente"] == str(n)


@pytest.mark.parametrize("tipo, esperado", [("Nuevo", "VN"), ("VO", "VO"), ("Km0", "VO"), ("", "VN")])
def test_tipo_de_vehiculo_solicitado(wa, conc, tipo, esperado):
    ag, filas = _agregar(wa, conc, _lead(tipo=tipo))
    assert list(filas) == [("2026-11", "Facebook", "VW [VN][2026] Golf noviembre", esperado)]
    assert ag.cuentas["tipo_por_defecto"] == (1 if tipo == "" else 0)


def test_valores_reales_en_la_configuracion(wa):
    cfg = wa.cargar_config()
    assert cfg["estados_venta"] == ["Vendido"] and cfg["motivos_num_inexistente"] == ["Número inexistente"]
    assert cfg["tipos"] == {"VN": ["Nuevo"], "VO": ["VO", "Km0"]} and cfg["tipo_por_defecto"] == "VN"


OTRAS = ["SK [VN] [2026] Mayo", "AU[VO][2026] ABRIL", "SE [VN] [2025] Gama ahorro", "CU [VN] [2026] Marzo Cuota",
         "SEAT [VN] [2026] Arona", "sk [vo][2025] Boutique ocasion"]
NUESTRAS = ["VW [VN][2026] Golf noviembre", "Semana del SUV", "Special Days", "Seminuevos garantía", "Auto Days",
            "Skyline promo"]


def test_prefijos_de_otras_marcas_en_la_configuracion(conc):
    assert conc.prefijos_excluidos == ("sk", "au", "se", "cu", "seat")
    for nombre in OTRAS:
        assert conc.otra_marca(nombre), nombre
    for nombre in NUESTRAS:                   # como palabra entera: «Semana», «Skyline» o «Auto» no son prefijos
        assert not conc.otra_marca(nombre), nombre


def test_sin_prefijos_no_se_excluye_nada(tmp_path):
    texto = (Path(__file__).resolve().parent.parent / "config" / "concesionario.yaml").read_text(encoding="utf-8")
    f = tmp_path / "concesionario.yaml"
    f.write_text(texto.replace("  prefijos_excluidos: [SK, AU, SE, CU, SEAT]\n", ""), encoding="utf-8")
    otro = cargar_concesionario(f)
    assert otro.prefijos_excluidos == () and not any(otro.otra_marca(n) for n in OTRAS)


def test_script_excluye_otras_marcas_cuenta_y_avisa(tmp_path, conc, capsys, wa):
    entrada = tmp_path / "leads.csv"
    filas = _leads(conc) + [["X", "", "", "Facebook", nombre, "Nuevo", "07/11/2026", "", "", "", "", "", "C"]
                            for nombre in OTRAS]
    with entrada.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter=";").writerows([EXPORTACION, *filas])
    salida = tmp_path / "walcu.csv"
    assert wa.main([str(entrada), str(salida)]) == 0
    registro = capsys.readouterr().out
    texto = salida.read_text(encoding="utf-8")
    assert not any(n in texto for n in OTRAS)
    assert "de campañas de otras marcas (no cuentan): 6" in registro
    assert "Aviso: 6 leads de campañas de otras marcas excluidos" in registro
    golf = _filas(texto)[("2026-11", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]
    assert golf["leads"] == "4"                              # las nuestras no se tocan


def test_referencias_excluyen_otras_marcas_y_lo_cuentan(pg):
    _escenario(pg)
    _meta(pg, date(2025, 3, 15), "70", "SK [VN] [2025] Gama ahorro", 999, leads=50)
    _walcu(pg, "2025-03,Facebook,SK [VN] [2025] Gama ahorro,VN,60,60,0,0,9,3,0",
           "2025-03,Facebook,AU [VO][2025] Boutique ocasion,VO,8,8,0,0,1,0,0",
           "2025-03,Facebook,VW [VN][2025] T-Roc marzo,VN,20,14,4,2,4,1,2",
           "2025-03,Facebook,VW [2025] Black Friday,VN,15,10,5,0,3,1,0",
           "2025-03,Facebook,VW [2025] Black Friday,VO,5,5,0,0,2,1,0",
           "2025-03,Facebook,Leads sin campaña de Meta,VN,7,7,0,0,0,0,0",
           "2026-11,Facebook,VW [VO][2026] Golf noviembre-copy,VO,10,8,2,0,2,1,1", nombre="con_otras.csv")
    resumen = ref.actualizar(pg)
    assert resumen["otras_marcas"] == {"meta_campanas": 1, "meta_gasto_cents": 99_900, "walcu_filas": 2,
                                       "walcu_leads": 68}
    r = ref.leer_referencias(pg)
    # Las mismas cifras que sin las otras marcas
    assert r[(2025, "todo")]["gasto_cents"] == 55_000 and r[(2025, "todo")]["leads"] == 47
    assert r[(2025, "VN")]["citas"] == 7
    assert not pg.execute("SELECT count(*) FROM panel_campana_mes WHERE titulo LIKE 'SK %' OR titulo LIKE 'AU %'"
                          ).fetchone()[0]
    assert not pg.execute("SELECT count(*) FROM panel_walcu_sin_cruce WHERE campana LIKE 'SK %' OR campana LIKE 'AU %'"
                          ).fetchone()[0]


def test_los_ficheros_de_excluidos_no_se_versionan():
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "comerciales_excluidos*.txt" in gitignore


# ================================================================== panel

def test_marketing_ultimo_mes_y_referencias(pg, panel_url, conc):
    from test_contrato_panel import ROLES, CLAVE, app_con, dentro
    from panel import usuarios
    for rol in ROLES:
        usuarios.alta(pg, rol, f"Persona {rol}", rol, CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    _escenario(pg)
    _campana_real(pg, "9", "VW [VN][2026] Golf noviembre", 100, 5)
    _walcu(pg, "2026-11,Facebook,VW [VN][2026] Golf noviembre,VN,20,10,8,2,8,1,0", nombre="nov.csv")
    motor.evaluar(pg, HOY, conc)
    html = dentro(app_con(panel_url), "marketing").get("/marketing").text
    assert "Campañas del último mes: noviembre de 2026" in html and "Referencias históricas" in html
    bloque = html.split('id="t-mes"')[1].split("</section>")[0]
    assert "VW [VN][2026] Golf noviembre" in bloque and "62,50 €" in bloque and "500 €" in bloque
    refs = html.split('id="t-referencias"')[1].split("</section>")[0]
    assert "Coste por cita" in refs and "Diferencia" in refs and "Número inexistente" in refs
