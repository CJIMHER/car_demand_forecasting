"""Histórico de Meta, carga semanal agregada de Walcu, cruce por nombre, referencias de coste, cohortes y
reglas con y sin Walcu (hito 1b, walcu-meta-hist y walcu-semanal). Sin red: Meta responde con respuestas
simuladas. Las cargas de Walcu se hacen «el» CARGA (cx.ahora fijo), que decide qué semanas están completas.
"""

from __future__ import annotations

import csv
import io
import json
import re
import zipfile
from datetime import date, datetime, timedelta, timezone
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
CARGA = datetime(2026, 11, 20, 9, 0, tzinfo=timezone.utc)      # día de las cargas de Walcu de las pruebas
CABECERA = ",".join(walcu.COLUMNAS)


@pytest.fixture(autouse=True)
def reloj(monkeypatch):
    """Cargas de Walcu siempre el 20-11-2026 (cada llamada, un microsegundo después: run_id distintos)."""
    n = iter(range(10**6))
    monkeypatch.setattr(cx, "ahora", lambda: CARGA + timedelta(microseconds=next(n)))


def csv_walcu(*filas: str, cabecera: str = CABECERA) -> bytes:
    return ("\n".join([cabecera, *filas]) + "\n").encode("utf-8")


FILAS_BUENAS = (
    "2025-03-10,Facebook,VW [VN][2025] T-Roc marzo,VN,20,14,4,2,4,1,2",
    "2025-03-10,Facebook,VW [VN][2025] T-Roc marzo,VO,5,5,0,0,1,1,0",
    "2025-03-10,AiVORA,(sin campaña),VN,30,30,0,0,10,5,0",
    "2026-11-09,Facebook,\"Sin título 22/1/25, 15:18\",VN,3,1,1,1,0,0,0",
)


# ================================================================== validador de walcu-cargar

def test_valida_y_carga_un_fichero_correcto(pg):
    bucket = Bucket()
    r = walcu.cargar_con(pg, csv_walcu(*FILAS_BUENAS), "/Users/alguien/Descargas/walcu.csv", HOY, subir=bucket)
    assert r["filas"] == 4 and r["semanas"] == 2 and r["desde"] == "2025-03-10" and r["hasta"] == "2026-11-09"
    filas = pg.execute("SELECT fichero_origen, semana, origen, campana, tipo, leads, citas, ventas "
                       "FROM walcu_campana_semana ORDER BY semana, origen, tipo").fetchall()
    assert len(filas) == 4 and {f[0] for f in filas} == {"walcu.csv"}           # sin la ruta del Mac
    assert filas[0][1] == date(2025, 3, 10)
    assert bucket.subidas[0][0] == f"walcu/semanal/{HOY}"                          # al bucket antes de escribir


def test_rechaza_un_fichero_con_un_correo_y_no_carga_nada(pg):
    contenido = csv_walcu(FILAS_BUENAS[0], "2025-04-07,Facebook,Promo ana.perez@correo.es,VN,1,1,0,0,0,0,0")
    with pytest.raises(walcu.ErrorWalcu) as exc:
        walcu.cargar_con(pg, contenido, "walcu.csv", HOY, subir=Bucket())
    texto = str(exc.value)
    assert "fila 3, columna campana: parece un correo (@)" in texto and "ana.perez" not in texto
    assert pg.execute("SELECT count(*) FROM walcu_campana_semana").fetchone()[0] == 0


def test_rechaza_una_columna_de_mas_y_no_carga_nada(pg):
    contenido = csv_walcu(FILAS_BUENAS[0] + ",600123456", cabecera=CABECERA + ",telefono")
    with pytest.raises(walcu.ErrorWalcu) as exc:
        walcu.cargar_con(pg, contenido, "walcu.csv", HOY, subir=Bucket())
    assert "columnas que no están en la lista cerrada: «telefono»" in str(exc.value)
    assert pg.execute("SELECT count(*) FROM walcu_campana_semana").fetchone()[0] == 0


@pytest.mark.parametrize("campana, que", [
    ("Llamar 612345678", "9 dígitos seguidos"), ("Llamar 612 34 56 78", "un teléfono"),
    ("Cliente 12345678Z", "un DNI"), ("Cliente X1234567L", "un NIE"),
])
def test_rechaza_telefonos_dni_y_nie(campana, que):
    with pytest.raises(walcu.ErrorWalcu) as exc:
        walcu.validar(csv_walcu(f"2025-04-07,Facebook,{campana},VN,1,1,0,0,0,0,0"))
    assert que in str(exc.value) and campana.split()[-1] not in str(exc.value)


@pytest.mark.parametrize("fila, motivo", [
    ("2025-04,Facebook,X,VN,1,1,0,0,0,0,0", "columna semana: no es AAAA-MM-DD"),
    ("2025-02-30,Facebook,X,VN,1,1,0,0,0,0,0", "columna semana: no es una fecha válida"),
    ("2025-04-08,Facebook,X,VN,1,1,0,0,0,0,0", "columna semana: no es lunes"),
    ("2025-04-07,Facebook,X,KM0,1,1,0,0,0,0,0", "columna tipo: debe ser VN o VO"),
    ("2025-04-07,Facebook,X,VN,-1,1,0,0,0,0,0", "columna leads: no es un entero no negativo"),
    ("2025-04-07,Facebook,,VN,1,1,0,0,0,0,0", "columna campana: vacía"),
])
def test_rechaza_formatos(fila, motivo):
    with pytest.raises(walcu.ErrorWalcu) as exc:
        walcu.validar(csv_walcu(fila))
    assert motivo in str(exc.value)


def test_falta_una_columna_o_se_repite_una_clave():
    with pytest.raises(walcu.ErrorWalcu, match="faltan columnas: num_inexistente"):
        walcu.validar(csv_walcu("2025-04-07,Facebook,X,VN,1,1,0,0,0,0", cabecera=CABECERA.rsplit(",", 1)[0]))
    with pytest.raises(walcu.ErrorWalcu, match="repite semana, origen, campaña y tipo de la fila 2"):
        walcu.validar(csv_walcu(FILAS_BUENAS[0], FILAS_BUENAS[0]))


def test_un_lunes_que_no_es_de_su_semana_no_existe_y_el_mes_ya_no_vale():
    # La cabecera mensual de antes no vale: «mes» no está en la lista cerrada
    with pytest.raises(walcu.ErrorWalcu, match="columnas que no están en la lista cerrada: «mes»"):
        walcu.validar(csv_walcu("2025-04,Facebook,X,VN,1,1,0,0,0,0,0", cabecera=CABECERA.replace("semana", "mes")))
    # 30-12-2024 es lunes (semana 1 de 2025): vale
    assert walcu.validar(csv_walcu("2024-12-30,Facebook,X,VN,1,1,0,0,0,0,0")).filas[0]["semana"] == date(2024, 12, 30)


def test_un_domingo_rechaza_el_fichero_entero(pg):
    with pytest.raises(walcu.ErrorWalcu, match="fila 3, columna semana: no es lunes"):
        walcu.cargar_con(pg, csv_walcu(FILAS_BUENAS[0], "2025-03-16,Facebook,X,VN,1,1,0,0,0,0,0"), "w.csv", HOY,
                         subir=Bucket())
    assert pg.execute("SELECT count(*) FROM walcu_campana_semana").fetchone()[0] == 0


def test_zonas_que_no_suman_es_aviso_no_rechazo():
    v = walcu.validar(csv_walcu("2025-04-07,Facebook,X,VN,22,17,0,1,1,0,2"))
    assert v.avisos == ["1 filas en que leads_cercana + leads_fuera + leads_sin_zona no suma leads"]


@pytest.mark.parametrize("fila", ["2025-04-07,Facebook,X,VN,3,3,0,0,4,0,0", "2025-04-07,Facebook,X,VN,3,3,0,0,1,2,0"])
def test_ventas_citas_leads_fuera_de_orden_es_aviso_no_rechazo(fila):
    assert walcu.validar(csv_walcu(fila)).avisos == ["1 filas en que no se cumple ventas ≤ citas ≤ leads"]


def test_el_mismo_fichero_no_se_carga_dos_veces_y_recargar_una_semana_la_sustituye(pg):
    walcu.cargar_con(pg, csv_walcu(*FILAS_BUENAS), "a.csv", HOY, subir=Bucket())
    assert "ya_cargado" in walcu.cargar_con(pg, csv_walcu(*FILAS_BUENAS), "a.csv", HOY, subir=Bucket())
    assert pg.execute("SELECT count(*) FROM walcu_campana_semana").fetchone()[0] == 4
    # Nueva carga de la semana del 10-03-2025 con otras cifras: la sustituye entera; la de noviembre sigue
    walcu.cargar_con(pg, csv_walcu("2025-03-10,Facebook,VW [VN][2025] T-Roc marzo,VN,25,20,4,1,5,2,0"), "b.csv", HOY,
                     subir=Bucket())
    vig = pg.execute("SELECT semana, campana, tipo, leads FROM walcu_campana_semana_vigente ORDER BY semana, tipo"
                     ).fetchall()
    assert vig == [(date(2025, 3, 10), "VW [VN][2025] T-Roc marzo", "VN", 25),
                   (date(2026, 11, 9), "Sin título 22/1/25, 15:18", "VN", 3)]
    assert pg.execute("SELECT count(*) FROM walcu_campana_semana").fetchone()[0] == 5   # nada se borra


def test_la_tabla_semanal_es_de_solo_anadir_y_la_mensual_ya_no_existe(pg):
    walcu.cargar_con(pg, csv_walcu(*FILAS_BUENAS), "a.csv", HOY, subir=Bucket())
    with pytest.raises(Exception, match="solo"):
        pg.execute("DELETE FROM walcu_campana_semana")
    assert pg.execute("SELECT to_regclass('walcu_campana_mes'), to_regclass('panel_campana_mes')").fetchone() \
        == (None, None)


@pytest.mark.parametrize("domingo_mas, citas, ventas", [(13, False, False), (14, True, False), (59, True, False),
                                                         (60, True, True)])
def test_semanas_completas_desde_su_domingo(domingo_mas, citas, ventas):
    c = ref.cargar()
    assert (c.dias_cita, c.dias_venta) == (14, 60)                 # config/campanas_walcu.yaml
    semana = date(2026, 9, 7)
    assert c.completas(semana, semana + timedelta(days=6 + domingo_mas)) == (citas, ventas)


def test_cohorte_mal_configurada_es_un_error(tmp_path):
    f = tmp_path / "campanas_walcu.yaml"
    f.write_text("origenes_meta: [Facebook]\ncohorte: {lead_a_cita_dias: 70, cita_a_venta_dias: 60}\n", encoding="utf-8")
    with pytest.raises(ref.ErrorCruce, match="lead_a_cita_dias"):
        ref.cargar(f)
    f.write_text("origenes_meta: [Facebook]\n", encoding="utf-8")
    with pytest.raises(ref.ErrorCruce, match="cohorte"):
        ref.cargar(f)


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
    f.write_text("origenes_meta: [Facebook]\nsufijos: [copy]\n" + COHORTE +
                 "equivalencias: {'Leads Taigo': 'VW [VN][2026] Taigo noviembre', 'Otra': 'id:999'}\n", encoding="utf-8")
    c = ref.cargar(f)
    assert c.equivalencias == {"leads taigo": "vw [vn][2026] taigo noviembre", "otra": "id:999"}


# ================================================================== cruce y referencias

COHORTE = "cohorte: {lead_a_cita_dias: 14, cita_a_venta_dias: 60}\n"

def _meta(pg, dia, cid, nombre, gasto_euros, leads=3, impresiones=1000, clics=20):
    fila = meta.leer_fila(_fila_meta(str(dia), campana=cid, nombre=nombre, gasto=str(gasto_euros)), "campana", "1234")
    fila.update(impresiones=impresiones, clics=clics, leads=leads)
    cx.insertar_versionado(pg, "campanas_meta_dia", "campanas_meta_dia_vigente", meta.CLAVE, meta.VALORES, [fila],
                           {"descargado_at": cx.ahora(), "run_id": "prueba", "origen": "prueba"})


def _walcu(pg, *filas, nombre="w.csv"):
    walcu.cargar_con(pg, csv_walcu(*filas), nombre, HOY, subir=Bucket())


ESCENARIO = (
    "2025-03-10,Facebook,VW [VN][2025] T-Roc marzo,VN,20,14,4,2,4,1,2",
    "2025-03-10,Facebook,VW [2025] Black Friday,VN,15,10,5,0,3,1,0",
    "2025-03-10,Facebook,VW [2025] Black Friday,VO,5,5,0,0,2,1,0",
    "2025-03-10,Facebook,Leads sin campaña de Meta,VN,7,7,0,0,0,0,0",
    "2025-03-10,AiVORA,(sin campaña),VN,100,100,0,0,50,20,0",
    # Semana del 9-11-2026, cargada el 20-11: su domingo tenía 5 días, citas y ventas incompletas
    "2026-11-09,Facebook,VW [VO][2026] Golf noviembre-copy,VO,10,8,2,0,2,1,1",
)


def _escenario(pg):
    # 2025 (semana del 10 de marzo): una de VN (T-Roc) y una de ambos (Black Friday) cuyo gasto se reparte
    _meta(pg, date(2025, 3, 10), "1", "VW [VN][2025] T-Roc marzo", 300, leads=30)
    _meta(pg, date(2025, 3, 11), "2", "VW [2025] Black Friday", 200, leads=20)
    _meta(pg, date(2025, 3, 12), "3", "Campaña sin leads en Walcu", 50, leads=1)
    # 2026: la de VO, con «-copy» en Walcu
    _meta(pg, date(2026, 11, 10), "4", "VW [VO][2026] Golf noviembre", 400, leads=40)
    _walcu(pg, *ESCENARIO)


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
    # 2026: la semana del 9-11 aún no tiene las citas ni las ventas completas: sin tasas ni costes de cita y venta
    assert (r[(2026, "VO")]["semanas"], r[(2026, "VO")]["semanas_citas"], r[(2026, "VO")]["semanas_ventas"]) == (1, 0, 0)
    m26 = ref.metricas(r[(2026, "VO")])
    assert m26["coste_lead"] == pytest.approx(40) and m26["coste_cita"] is None and m26["lead_cita"] is None
    assert m26["cita_venta"] is None and m26["coste_venta"] is None
    assert (r[(2025, "VN")]["semanas"], r[(2025, "VN")]["desde_semana"]) == (1, date(2025, 3, 10))
    m = ref.metricas(r[(2025, "VN")])
    assert m["coste_cita"] == pytest.approx(450 / 7) and m["coste_venta"] == pytest.approx(225)
    assert m["lead_cita"] == pytest.approx(7 / 42 * 100) and m["cita_venta"] == pytest.approx(2 / 7 * 100)
    assert m["fuera_zona"] == pytest.approx(9 / 40 * 100) and m["inexistente"] == pytest.approx(2 / 42 * 100)
    # Panel por campaña y semana: el «-copy» casa; la de Walcu sin campaña de Meta va con campana_id NULL
    golf = pg.execute("SELECT semana, cruce, leads, citas, ventas, gasto_cents, citas_completas, ventas_completas "
                      "FROM panel_campana_semana WHERE campana_id = '4'").fetchone()
    assert golf == (date(2026, 11, 9), "nombre", 10, 2, 1, 40_000, False, False)
    assert pg.execute("SELECT citas_completas, ventas_completas FROM panel_campana_semana WHERE campana_id = '1'"
                      ).fetchone() == (True, True)
    assert pg.execute("SELECT leads FROM panel_campana_semana WHERE campana_id IS NULL "
                      "AND titulo = 'Leads sin campaña de Meta'").fetchone() == (7,)
    assert pg.execute("SELECT leads, cruce FROM panel_campana_semana WHERE campana_id = '3'").fetchone() == (None, None)
    informe = dict(pg.execute("SELECT campana, motivo FROM panel_walcu_sin_cruce").fetchall())
    assert set(informe) == {"Leads sin campaña de Meta", "Campaña sin leads en Walcu"}
    texto = ref.informe(pg)
    assert "Coste por cita" in texto and "Diferencia" in texto and "Sin cruce entre Meta y Walcu: 2" in texto
    assert "2026: 1 semanas; 0 con las citas completas y 0 con las ventas completas." in texto
    assert "semanas del 10-03-2025 al 10-03-2025" in texto


def test_el_anio_de_una_semana_es_el_iso_y_la_semana_en_curso_no_entra_en_las_referencias(pg):
    # 29-12-2025 (lunes) es la semana 1 de 2026; la del 16-11-2026 no había acabado al cargarse (20-11)
    _meta(pg, date(2025, 12, 30), "5", "VW [VN][2026] Golf enero", 100, leads=5)
    _meta(pg, date(2026, 11, 17), "6", "VW [VN][2026] Golf noviembre", 70, leads=5)
    _walcu(pg, "2025-12-29,Facebook,VW [VN][2026] Golf enero,VN,10,10,0,0,3,1,0",
           "2026-11-16,Facebook,VW [VN][2026] Golf noviembre,VN,4,4,0,0,1,0,0")
    r = ref.leer_referencias(pg)
    assert set(r) == {(2026, "VN"), (2026, "todo")}
    assert (r[(2026, "VN")]["gasto_cents"], r[(2026, "VN")]["leads"], r[(2026, "VN")]["semanas"]) == (10_000, 10, 1)
    assert (r[(2026, "VN")]["citas_citas"], r[(2026, "VN")]["ventas_ventas"]) == (3, 1)        # completas
    # En el panel por semana sí están las dos
    assert pg.execute("SELECT count(DISTINCT semana) FROM panel_campana_semana WHERE walcu").fetchone()[0] == 2


def test_equivalencia_resuelve_lo_que_no_casa(pg, tmp_path, monkeypatch):
    f = tmp_path / "campanas_walcu.yaml"
    f.write_text("origenes_meta: [Facebook]\nsufijos: [copy, copia, ok]\n" + COHORTE +
                 "equivalencias: {'Leads sin campaña de Meta': 'Campaña sin leads en Walcu'}\n", encoding="utf-8")
    monkeypatch.setenv("CDF_CAMPANAS_WALCU", str(f))
    _escenario(pg)
    assert pg.execute("SELECT cruce, leads FROM panel_campana_semana WHERE campana_id = '3'").fetchone() \
        == ("equivalencia", 7)
    assert pg.execute("SELECT count(*) FROM panel_walcu_sin_cruce").fetchone()[0] == 0


# ================================================================== reglas con y sin Walcu

@pytest.fixture
def conc():
    return cargar_concesionario()


def _campana_real(pg, cid, titulo, gasto_dia, dias, *, leads_meta=5, impresiones=10_000, clics=500, ambito="VN"):
    inicio = HOY - timedelta(days=dias - 1)
    for i in range(dias):
        _meta(pg, inicio + timedelta(days=i), cid, titulo, gasto_dia, leads=leads_meta,
              impresiones=impresiones, clics=clics)
    cmp.actualizar(pg)          # panel_campana_dia y el cruce, como tras la descarga de Meta


GOLF = "VW [VN][2026] Golf noviembre"
# Carga semanal del 20-11-2026: semanas del 26-10 al 16-11 (todas «con carga»: AiVORA en cada una). La del 26-10
# (domingo 1-11, 19 días) tiene las citas completas; ninguna, las ventas (60 días).
SEMANAS_NOV = ("2026-10-26", "2026-11-02", "2026-11-09", "2026-11-16")


def _walcu_nov(pg, *golf: str, nombre="nov.csv"):
    _walcu(pg, *ESCENARIO[-1:], *golf, *(f"{s},AiVORA,(sin campaña),VN,5,5,0,0,1,0,0" for s in SEMANAS_NOV), nombre=nombre)


def test_reglas_sin_walcu_dicen_sin_datos_de_walcu(pg, conc):
    _campana_real(pg, "9", GOLF, 100, 5)
    cmp.actualizar(pg)
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    j = js[0]
    assert j.fuente_leads == cr.META
    for k in ("a", "d", "subir"):
        assert j.reglas[k].estado == "sin_dato" and "sin datos de Walcu: no se aplica" in j.reglas[k].cifras_texto
    assert "no hay ninguna carga de Walcu" in j.reglas["a"].cifras_texto
    # c con los leads de Meta: 500 € / 25 leads = 20 € > 10 €: revisar, sin coste por cita
    assert j.reglas["c"].decision == "revisar" and "25 leads según Meta" in j.reglas["c"].cifras_texto
    assert "Sin datos de Walcu" in j.reglas["c"].revisar


def test_carga_de_walcu_antigua_no_cuenta(pg, conc):
    _escenario(pg)                                  # última semana cargada: la del 9-11; hoy, semana del 16-11
    _campana_real(pg, "9", GOLF, 100, 5)
    _meta(pg, HOY + timedelta(days=7), "9", GOLF, 100)
    cmp.actualizar(pg)
    _, js = cr.juzgar(pg, HOY + timedelta(days=7), "real", conc, motor.cargar_criterios(), {})
    j = next(x for x in js if x.campana_id == "9")
    assert j.fuente_leads == cr.META and "la última carga de Walcu es de la semana del" in j.reglas["a"].cifras_texto


def test_reglas_con_walcu_semanal_y_coste_por_cita_de_las_semanas_completas(pg, conc):
    crit = motor.cargar_criterios()
    _escenario(pg)                                  # referencia de 2025 (VN): 450 € / 7 citas = 64,29 € por cita
    # 26 días (26-10 a 20-11) a 50 € = 1.300 €; en Walcu, 20 leads en la semana del 26-10 (8 citas, 1 venta) y 10
    # en la del 9-11 (5 citas, aún incompletas)
    _campana_real(pg, "9", GOLF, 50, 26)
    _walcu_nov(pg, f"2026-10-26,Facebook,{GOLF},VN,20,10,8,2,8,1,0", f"2026-11-09,Facebook,{GOLF},VN,10,10,0,0,5,0,0")
    _, js = cr.juzgar(pg, HOY, "real", conc, crit, {})
    j = next(x for x in js if x.campana_id == "9")
    assert j.fuente_leads == cr.WALCU_SEMANA and (j.leads, j.citas, j.ventas) == (30, 8, 0)
    assert (j.leads_cita, j.gasto_cita, j.cohorte.semanas, j.cohorte.semanas_citas) == (20, 35_000, 4, 1)
    # a: lead a cita con las semanas completas (8 de 20) y fuera de zona con todos (8 de 28 = 28,57 %)
    a = j.reglas["a"].cifras_texto
    assert "lead a cita: 8 de 20 = 40 % (umbral 5 %) (semanas con las citas completas, de 14 días o más: 1 de 4)" in a
    assert "fuera de zona: 8 de 28 leads con código postal" in a
    # c: 1.300 € / 30 leads = 43,33 € > 10 €; coste por cita de la semana completa: 350 € / 8 = 43,75 € ≤ 64,29 €
    c = j.reglas["c"]
    assert "coste por lead: 1300 € / 30 leads = 43,33 €" in c.cifras_texto.replace(".", "")
    assert c.decision == "continuar" and "coste por cita: 43,75 € (semanas con las citas completas" in c.cifras_texto
    assert "frente a la referencia de 2025 (VN) de 64,29 €" in c.cifras_texto
    # d: ninguna semana con 60 días: no se aplica
    assert j.reglas["d"].estado == "sin_dato" and \
        j.reglas["d"].cifras_texto == "ventas aún incompletas: ninguna semana de leads con 60 días o más"
    assert j.reglas["f"].cifras.get("fuente_leads", "meta") == "meta"


def test_regla_c_parar_si_el_coste_por_cita_supera_la_referencia(pg, conc):
    _escenario(pg)
    _campana_real(pg, "9", GOLF, 100, 26)          # semana del 26-10: 700 € / 8 citas = 87,50 € > 64,29 €
    _walcu_nov(pg, f"2026-10-26,Facebook,{GOLF},VN,20,20,0,0,8,1,0")
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    c = next(x for x in js if x.campana_id == "9").reglas["c"]
    assert c.decision == "parar" and "coste por cita: 87,50 €" in c.cifras_texto


def test_sin_semanas_con_las_citas_completas_no_se_para_ni_se_continua(pg, conc):
    _escenario(pg)
    _campana_real(pg, "9", GOLF, 100, 5)           # 16 a 20-11: solo la semana del 16-11, recién cargada
    _walcu_nov(pg, f"2026-11-16,Facebook,{GOLF},VN,20,20,0,0,2,0,0")
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    j = next(x for x in js if x.campana_id == "9")
    assert j.reglas["c"].decision == "revisar" and "Citas aún incompletas" in j.reglas["c"].revisar
    assert "citas aún incompletas: ninguna semana de leads con 14 días o más" in j.reglas["a"].cifras_texto
    assert j.reglas["subir"].estado == "no"


def test_regla_d_dice_que_es_el_optimo(pg):
    _, reglas = motor.cargar_reglas()
    d = next(r for r in reglas if r.id == "meta_d")
    assert "El 33 % es el óptimo: esta alerta saltará a menudo." in d.texto(None)


def test_campana_sin_cruce_con_walcu_usa_los_leads_de_meta(pg, conc):
    _escenario(pg)
    _campana_real(pg, "9", GOLF, 100, 5)
    _walcu_nov(pg)
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
               "Sede", "Equipo", "Vendedor"]


def _leads(conc):
    cerca, lejos = conc.zona_cercana[0] + "001", ("28" if conc.zona_cercana[0] != "28" else "08") + "001"
    return [
        ["Ana Pérez", "612345678", "ana@correo.es", "Facebook", "VW [VN][2026] Golf noviembre", "Nuevo",
         "03/11/2026 10:15", "N-1", "Visita en exposición", "Negociación creada (Cerrada / Vendido)", "", cerca,
         "Sede central", "Ventas VN", "Comercial Uno"],
        ["Luis Gómez", "699111222", "luis@correo.es", "facebook", "VW [VN][2026] Golf noviembre", "Nuevo",
         "04/11/2026 11:00", "", "", "Perdido", "Número inexistente", lejos, "Sede central", "Ventas VN", "Comercial Dos"],
        ["Eva Ruiz", "600000001", "eva@correo.es", "AiVORA", "", "VO", "2026-10-30 09:00", "N-2", "", "En curso",
         "", "", "Sede central", "Ventas VN", "Comercial Uno"],
        ["Raro", "600000002", "r@correo.es", "Portal raro", "Llamar al 612345678", "Km0", "30/10/2026", "", "", "",
         "", "1234", "Sede central", "Ventas VN", "Comercial Tres"],
        # Negociación creada, pero la última actividad es un presupuesto por teléfono: no es cita (ni venta)
        ["Pepe Mora", "600000003", "p@correo.es", "Facebook", "VW [VN][2026] Golf noviembre", "Nuevo",
         "05/11/2026 12:00", "N-3", "Presupuesto por teléfono", "En curso (Cerrada / Vendido)", "", cerca,
         "Sede central", "Ventas VN", "Comercial Dos"],
        # Lead de un comercial excluido (cuando lo está): se descarta antes de agregar
        ["Sara Gil", "600000004", "s@correo.es", "Facebook", "VW [VN][2026] Golf noviembre", "Nuevo",
         "06/11/2026 12:00", "N-4", "Visita en exposición", "En curso (Cerrada / Vendido)", "", cerca,
         "Sede central", "Ventas VN", "Comercial Excluido"],
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
    return {(f["semana"], f["origen"], f["campana"], f["tipo"]): f for f in csv.DictReader(io.StringIO(texto))}


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
    golf = filas[("2026-11-02", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]
    # Sin excluidos: 4 leads (Ana, Luis, Pepe, Sara); citas: Ana y Sara (Pepe, presupuesto por teléfono, no)
    assert (golf["leads"], golf["leads_cercana"], golf["leads_fuera"], golf["citas"], golf["ventas"],
            golf["num_inexistente"]) == ("4", "3", "1", "2", "2", "1")
    assert "de comerciales excluidos (no cuentan): 0" in registro
    assert "no cuenta como cita por su última actividad: 1" in registro
    assert filas[("2026-10-26", "AiVORA", "(sin campaña)", "VO")]["leads_sin_zona"] == "1"
    raro = filas[("2026-10-26", "otros", "(campaña omitida)", "VO")]
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
    golf = _filas(texto)[("2026-11-02", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]
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
            "codigo_postal": "", "sede": "", "equipo": "", "comercial": ""}


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
    assert filas[("2026-11-09", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]["ventas"] == str(venta)


@pytest.mark.parametrize("motivo, n", [("Número inexistente", 1), ("NUMERO INEXISTENTE", 1), ("No contesta", 0)])
def test_numero_inexistente(wa, conc, motivo, n):
    _, filas = _agregar(wa, conc, _lead(motivo=motivo, negociacion=""))
    assert filas[("2026-11-09", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]["num_inexistente"] == str(n)


@pytest.mark.parametrize("tipo, esperado", [("Nuevo", "VN"), ("VO", "VO"), ("Km0", "VO"), ("", "VN")])
def test_tipo_de_vehiculo_solicitado(wa, conc, tipo, esperado):
    """Con una campaña que no dice el tipo y sin sede ni equipo de VO, manda el campo."""
    ag, filas = _agregar(wa, conc, _lead(campana="Lanzamiento", tipo=tipo))
    assert list(filas) == [("2026-11-09", "Facebook", "Lanzamiento", esperado)]
    assert ag.cuentas["tipo_por_defecto"] == (1 if tipo == "" else 0)


def test_valores_reales_en_la_configuracion(wa):
    cfg = wa.cargar_config()
    assert cfg["estados_venta"] == ["Vendido"] and cfg["motivos_num_inexistente"] == ["Número inexistente"]
    assert cfg["tipos"] == {"VN": ["Nuevo"], "VO": ["VO", "Km0"]} and cfg["tipo_por_defecto"] == "VN"


OTRAS = ["SK [VN] [2026] Mayo", "AU[VO][2026] ABRIL", "SE [VN] [2025] Gama ahorro", "CU [VN] [2026] Marzo Cuota",
         "SEAT [VN] [2026] Arona", "sk [vo][2025] Boutique ocasion",
         # palabras en cualquier posición (las del encargo)
         "EXT SK [2026] SKODA DAYS", "VN [2025] SEAT Ibiza/Ateca febrero", "Avisa Days marzo 2025-SK",
         "3-dias-de-ocasion-audi", "SK [VN] [2026] Škoda Nuevo ELROQ", "Promo_CUPRA_Born", "[AUDI] Q3"]
NUESTRAS = ["VW [VN][2026] Golf noviembre", "Semana del SUV", "Seminuevos", "Se acaba el stock", "Special Days",
            "Seminuevos garantía", "Auto Days", "Skyline promo", "Audiencias amplias", "Cuota cero", "SE ACABA YA",
            "Cuprum promo"]


def test_otras_marcas_en_la_configuracion(conc):
    assert conc.prefijos_excluidos == ("au", "se", "cu")
    assert conc.palabras_excluidas == ("sk", "skoda", "seat", "audi", "cupra", "pv")
    for nombre in OTRAS:
        assert conc.otra_marca(nombre), nombre
    # Palabra entera; el prefijo, solo como primera palabra seguida de su etiqueta entre corchetes
    for nombre in NUESTRAS:
        assert not conc.otra_marca(nombre), nombre


def _conc_con(tmp_path, quitar: list[str]):
    texto = (Path(__file__).resolve().parent.parent / "config" / "concesionario.yaml").read_text(encoding="utf-8")
    for linea in quitar:
        assert linea in texto
        texto = texto.replace(linea, "")
    f = tmp_path / "concesionario.yaml"
    f.write_text(texto, encoding="utf-8")
    return cargar_concesionario(f)


def test_sin_listas_no_se_excluye_nada(tmp_path):
    otro = _conc_con(tmp_path, ["  prefijos_excluidos: [AU, SE, CU]\n", "  palabras_excluidas: [SK, SKODA, SEAT, AUDI, CUPRA, PV]\n"])
    assert otro.prefijos_excluidos == otro.palabras_excluidas == () and not any(otro.otra_marca(n) for n in OTRAS)


def test_solo_prefijos_o_solo_palabras(tmp_path):
    solo_prefijos = _conc_con(tmp_path, ["  palabras_excluidas: [SK, SKODA, SEAT, AUDI, CUPRA, PV]\n"])
    assert solo_prefijos.otra_marca("AU [VN] x") and not solo_prefijos.otra_marca("EXT SK [2026] SKODA DAYS")
    solo_palabras = _conc_con(tmp_path, ["  prefijos_excluidos: [AU, SE, CU]\n"])
    assert solo_palabras.otra_marca("EXT SK [2026] SKODA DAYS") and not solo_palabras.otra_marca("AU [VN] x")


def test_una_entrada_que_no_es_una_palabra_es_un_error(tmp_path):
    from ingesta.concesionario import ErrorConfiguracion
    texto = (Path(__file__).resolve().parent.parent / "config" / "concesionario.yaml").read_text(encoding="utf-8")
    f = tmp_path / "concesionario.yaml"
    f.write_text(texto.replace("[SK, SKODA, SEAT, AUDI, CUPRA, PV]", "[SK, ALFA ROMEO]"), encoding="utf-8")
    with pytest.raises(ErrorConfiguracion, match="palabras_excluidas"):
        cargar_concesionario(f)


def test_script_excluye_otras_marcas_cuenta_y_avisa(tmp_path, conc, capsys, wa):
    entrada = tmp_path / "leads.csv"
    filas = _leads(conc) + [["X", "", "", "Facebook", nombre, "Nuevo", "07/11/2026", "", "", "", "", "", "", "", "C"]
                            for nombre in OTRAS]
    with entrada.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter=";").writerows([EXPORTACION, *filas])
    salida = tmp_path / "walcu.csv"
    assert wa.main([str(entrada), str(salida)]) == 0
    registro = capsys.readouterr().out
    texto = salida.read_text(encoding="utf-8")
    assert not any(n in texto for n in OTRAS)
    assert f"de campañas de otras marcas (no cuentan): {len(OTRAS)}" in registro
    assert f"Aviso: {len(OTRAS)} leads de campañas de otras marcas excluidos" in registro
    golf = _filas(texto)[("2026-11-02", "Facebook", "VW [VN][2026] Golf noviembre", "VN")]
    assert golf["leads"] == "4"                              # las nuestras no se tocan


def test_referencias_excluyen_otras_marcas_y_lo_cuentan(pg):
    _escenario(pg)
    _meta(pg, date(2025, 3, 15), "70", "SK [VN] [2025] Gama ahorro", 999, leads=50)
    _walcu(pg, "2025-03-10,Facebook,SK [VN] [2025] Gama ahorro,VN,60,60,0,0,9,3,0",
           "2025-03-10,Facebook,AU [VO][2025] Boutique ocasion,VO,8,8,0,0,1,0,0",
           *(f for f in ESCENARIO if "AiVORA" not in f), nombre="con_otras.csv")
    resumen = ref.actualizar(pg)
    assert resumen["otras_marcas"] == {"meta_campanas": 1, "meta_gasto_cents": 99_900, "walcu_filas": 2,
                                       "walcu_leads": 68}
    r = ref.leer_referencias(pg)
    # Las mismas cifras que sin las otras marcas
    assert r[(2025, "todo")]["gasto_cents"] == 55_000 and r[(2025, "todo")]["leads"] == 47
    assert r[(2025, "VN")]["citas"] == 7
    assert not pg.execute("SELECT count(*) FROM panel_campana_semana WHERE titulo LIKE 'SK %' OR titulo LIKE 'AU %'"
                          ).fetchone()[0]
    assert not pg.execute("SELECT count(*) FROM panel_walcu_sin_cruce WHERE campana LIKE 'SK %' OR campana LIKE 'AU %'"
                          ).fetchone()[0]


def test_referencias_excluyen_palabras_de_otras_marcas(pg):
    _escenario(pg)
    _meta(pg, date(2025, 3, 16), "71", "Avisa Days marzo 2025-SK", 500, leads=10)
    _meta(pg, date(2025, 3, 17), "72", "3-dias-de-ocasion-audi", 300, leads=5)
    resumen = ref.actualizar(pg)
    assert resumen["otras_marcas"]["meta_campanas"] == 2 and resumen["otras_marcas"]["meta_gasto_cents"] == 80_000
    assert ref.leer_referencias(pg)[(2025, "todo")]["gasto_cents"] == 55_000


def test_marketing_y_criterios_sin_las_campanas_de_otras_marcas(pg, panel_url, conc):
    from test_contrato_panel import ROLES, CLAVE, app_con, dentro
    from panel import usuarios
    for rol in ROLES:
        usuarios.alta(pg, rol, f"Persona {rol}", rol, CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    _campana_real(pg, "9", "VW [VN][2026] Golf noviembre", 100, 5)
    _campana_real(pg, "80", "EXT SK [2026] SKODA DAYS", 40, 5)          # 5 días a 40 € = 200 €
    _campana_real(pg, "81", "VN [2025] SEAT Ibiza/Ateca febrero", 25, 4)  # 4 días a 25 € = 100 €
    # Fuera de panel_campana_dia (tarjetas, campañas y criterios) y de «Campañas sin regla»
    assert pg.execute("SELECT DISTINCT campana_id FROM panel_campana_dia").fetchall() == [("9",)]
    assert pg.execute("SELECT count(*) FROM panel_campanas_sin_regla WHERE campana ~ 'SK|SEAT'").fetchone()[0] == 0
    assert pg.execute("SELECT count(DISTINCT campana_id), sum(gasto_cents) FROM panel_campana_otra_marca "
                      "WHERE origen = 'real'").fetchone() == (2, 30_000)
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    assert [j.campana_id for j in js] == ["9"]
    motor.evaluar(pg, HOY, conc)
    assert pg.execute("SELECT DISTINCT campana_id FROM panel_campana_decision").fetchall() == [("9",)]
    html = dentro(app_con(panel_url), "marketing").get("/marketing").text
    bloque = html.split('id="t-campanas"')[1].split("</section>")[0]
    assert "SKODA DAYS" not in bloque and "SEAT Ibiza" not in bloque and "Golf noviembre" in bloque
    assert "Campañas de otras marcas en esta cuenta: 2, gasto 300 € en 28 días." in bloque
    assert "SKODA" not in html.split('id="t-mes"')[1].split("</section>")[0]


def test_sin_campanas_de_otras_marcas_no_hay_linea(pg, panel_url, conc):
    from test_contrato_panel import ROLES, CLAVE, app_con, dentro
    from panel import usuarios
    for rol in ROLES:
        usuarios.alta(pg, rol, f"Persona {rol}", rol, CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    _campana_real(pg, "9", "VW [VN][2026] Golf noviembre", 100, 5)
    html = dentro(app_con(panel_url), "marketing").get("/marketing").text
    assert "Campañas de otras marcas" not in html


def test_los_ficheros_de_excluidos_no_se_versionan():
    gitignore = (Path(__file__).resolve().parent.parent / ".gitignore").read_text(encoding="utf-8")
    assert "comerciales_excluidos*.txt" in gitignore


# ================================================================== panel

def test_marketing_ultimas_4_semanas_marca_las_incompletas_y_referencias(pg, panel_url, conc):
    from test_contrato_panel import ROLES, CLAVE, app_con, dentro
    from panel import usuarios
    for rol in ROLES:
        usuarios.alta(pg, rol, f"Persona {rol}", rol, CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    _escenario(pg)
    _campana_real(pg, "9", GOLF, 50, 26)
    _walcu_nov(pg, f"2026-10-26,Facebook,{GOLF},VN,20,10,8,2,8,1,0", f"2026-11-09,Facebook,{GOLF},VN,10,10,0,0,5,0,0")
    motor.evaluar(pg, HOY, conc)
    html = dentro(app_con(panel_url), "marketing").get("/marketing").text
    # Hoy, 20-11 (viernes): las 4 semanas completas son del 19-10 al 15-11
    assert "Campañas de las últimas 4 semanas: 19/10 – 15/11" in html
    bloque = html.split('id="t-mes"')[1].split("</section>")[0]
    # Golf: 21 días de las 4 semanas (26-10 a 15-11) a 50 € = 1.050 €; leads 30, citas 13; € / cita solo con la
    # semana del 26-10 (350 € / 8 = 43,75 €); € / venta sin semanas completas: «—»
    assert GOLF in bloque and "1.050 €" in bloque and "43,75 €" in bloque and "35 €" in bloque
    filas_sem = bloque.split("tabla-semanas-walcu")[1].split("</table>")[0]
    assert filas_sem.count('class="incompleta"') == 3          # 26-10: ventas; 2-11 y 9-11: citas y ventas
    assert "Sin carga de Walcu" in filas_sem                     # la del 19-10
    assert "Ventas incompletas" in filas_sem and "Citas y ventas incompletas" in filas_sem
    assert "Cohorte: las citas y las ventas cuentan en la semana del lead" in bloque
    refs = html.split('id="t-referencias"')[1].split("</section>")[0]
    assert "Coste por cita" in refs and "Diferencia" in refs and "Número inexistente" in refs
    assert "2025 (1 semanas)" in refs and "con las citas completas" in refs


# ================================================================== tipo VN/VO, empresas excluidas y equivalencias

def _lead_tipo(campana, sede="", equipo="", tipo="Nuevo", origen="Facebook"):
    return {**_lead(campana=campana, tipo=tipo), "sede": sede, "equipo": equipo, "origen": origen}


@pytest.mark.parametrize("campana, sede, equipo, tipo, esperado, por", [
    # 1. El nombre de la campaña manda, aunque Walcu diga «Nuevo» o la sede sea de VO
    ("VW [VO][2025] Boutique ocasion-copy", "", "", "Nuevo", "VO", "campana"),
    ("VW [VN][2026] Golf noviembre", "Carpa VO", "VO", "VO", "VN", "campana"),
    ("VO [2025] Avisa Days marzo - leadgen", "", "", "Nuevo", "VO", "campana"),
    ("VN [2025] T-ROC febrero", "Carpa VO", "", "VO", "VN", "campana"),
    ("Seminuevos con garantía", "", "", "Nuevo", "VO", "campana"),
    ("Boutique", "", "", "Nuevo", "VO", "campana"),
    ("Ocasión Black Friday", "", "", "Nuevo", "VO", "campana"),
    # 2. Si el nombre no lo dice: sede o equipo de VO
    ("Lanzamiento", "Carpa VO", "", "Nuevo", "VO", "sede_equipo"),
    ("Lanzamiento", "CARPA  vo", "", "Nuevo", "VO", "sede_equipo"),
    ("Avisa Days marzo 2025-VW", "Sede central", "VO", "Nuevo", "VO", "sede_equipo"),
    ("VW [VN] y [VO] Feria", "Carpa VO", "", "Nuevo", "VO", "sede_equipo"),     # dos etiquetas: no dice
    # 3. El campo «Tipo de vehículo solicitado»
    ("Lanzamiento", "Sede central", "Ventas VN", "Km0", "VO", "campo"),
    ("Volkswagen Days", "", "", "Nuevo", "VN", "campo"),          # «Volkswagen» no empieza por VO
    # 4. Por defecto
    ("Lanzamiento Julio", "", "", "", "VN", "defecto"),
])
def test_tipo_por_campana_sede_equipo_y_campo(wa, conc, campana, sede, equipo, tipo, esperado, por):
    ag, filas = _agregar(wa, conc, _lead_tipo(campana, sede, equipo, tipo))
    assert [k[3] for k in filas] == [esperado]
    assert ag.tipo_por == {por: 1}


def test_tipo_sale_de_la_configuracion(wa):
    cfg = wa.cargar_config()
    assert cfg["sedes_vo"] == ["Carpa VO"] and cfg["equipos_vo"] == ["VO"]
    assert {"ocasión", "boutique", "seminuevo"} <= set(cfg["palabras_vo"])
    assert {"sede": "Sede", "equipo": "Equipo"}.items() <= cfg["columnas"].items()
    texto = (Path(__file__).resolve().parent.parent / "scripts" / "walcu_agregar.py").read_text(encoding="utf-8")
    assert "Carpa" not in texto and "Covey" not in texto


def test_empresas_del_grupo_se_excluyen_cuentan_y_avisan(tmp_path, conc, capsys, wa):
    assert conc.empresas_excluidas == ("covey",)
    entrada = tmp_path / "leads.csv"
    covey = [["X", "", "", origen, "VW [VO][2026] Boutique", "VO", "07/11/2026", "N-7", "", "", "", "", sede, equipo,
              "C"] for origen, sede, equipo in (("Facebook", "Covey Autocasión", ""), ("Facebook", "", "Equipo COVEY"),
                                                ("Web Covey", "", ""))]
    with entrada.open("w", encoding="utf-8", newline="") as f:
        csv.writer(f, delimiter=";").writerows([EXPORTACION, *_leads(conc), *covey])
    salida = tmp_path / "walcu.csv"
    assert wa.main([str(entrada), str(salida)]) == 0
    registro = capsys.readouterr().out
    texto = salida.read_text(encoding="utf-8")
    assert "de empresas del grupo con su propio marketing (no cuentan): 3" in registro
    assert "Aviso: 3 leads de empresas del grupo con su propio marketing excluidos" in registro
    assert "Boutique" not in texto
    # Sede y equipo solo sirven para el tipo y la exclusión: nunca salen
    for valor in ("Sede central", "Ventas VN", "Covey", "Carpa"):
        assert valor not in texto and valor not in registro


def test_posventa_es_otra_marca(conc):
    for nombre in ("PV [2025] Revisión invierno", "VW [PV] [2025] Neumáticos", "Campaña PV"):
        assert conc.otra_marca(nombre), nombre
    for nombre in ("VW [VN][2026] PVP especial", "Polo VIVE"):
        assert not conc.otra_marca(nombre), nombre


PARES_ENCARGO = [
    ("VW [VO][2025] Boutique ocasion-copy", "VW [VO] [2025] Boutique Ocasión", False),
    ("VW [VN] [2025] Gama ahorro", "VW [VN] [2026] Gama ahorro", True),
    ("VN [2025] T-ROC febrero", "VN [2025] T-Roc febrero - leadgen", False),
    ("Avisa Days marzo 2025-VW", "VO [2025] Avisa Days marzo - leadgen", True),
    ("Avisa Days junio 2025-VW", "VO [2025] Avisa Days junio Campaña", True),
    ("VW [VN] [2025] Caddy eHybrid-OK", "VW [VN] [2025] Caddy eHybrid Campaña", False),
    ("VW [VN] [2025] Test Drive ID3-ID4", "VW [VN] [2025] Test Drive ID3-ID4 Campaña", False),
    ("VW [VN][2026] ID Polo B", "Test - VW [VN][2026] ID Polo", True),
    ("VW [VN][2026] ABRIL 2", "VW [VN][2026] ABRIL", True),
    ("Sin título 22/1/25, 15:18", "VN [2025] Polo Match enero Campaña", True),
]


@pytest.mark.parametrize("walcu_nombre, meta_nombre, a_mano", PARES_ENCARGO)
def test_equivalencias_del_encargo(walcu_nombre, meta_nombre, a_mano):
    c = ref.cargar()
    n = c.normalizar(walcu_nombre)
    assert c.equivalencias.get(n, n) == c.normalizar(meta_nombre)
    assert (n in c.equivalencias) == a_mano              # las demás, por regla


def test_sufijos_por_regla_y_corchetes():
    c = ref.cargar()
    for nombre in ("VW [VN] [2025] Taigo marzo Campaña", "VW [VN][2025] Taigo marzo - leadgen",
                   "VW [VN] [2025] Taigo marzo - landing", "VW [VN] [2025] Taigo marzo-OK", "vw [vn][2025] taigo marzo"):
        assert c.normalizar(nombre) == "vw [vn][2025] taigo marzo", nombre
    assert c.normalizar("VW [VN][2026] ID Polo B") != c.normalizar("VW [VN][2026] ID Polo")   # « B», a mano


def test_lanzamiento_general_de_vn_sin_campana_de_meta(pg):
    _escenario(pg)
    _walcu(pg, "2025-03-10,Facebook,Lanzamiento,VN,5,5,0,0,1,0,0",
           "2025-03-10,Facebook,Lanzamiento Julio,VO,2,2,0,0,0,0,0",
           *(f for f in ESCENARIO if "AiVORA" not in f), nombre="lanzamiento.csv")
    ref.actualizar(pg)
    motivos = dict(pg.execute("SELECT campana, motivo FROM panel_walcu_sin_cruce WHERE campana LIKE 'Lanzamiento%'"))
    assert set(motivos) == {"Lanzamiento", "Lanzamiento Julio"}
    assert all("sin campaña de Meta" in m and "añadir" not in m for m in motivos.values())


def test_referencias_excluyen_empresas_del_grupo(pg):
    _escenario(pg)
    _meta(pg, date(2025, 3, 15), "71", "Covey Autocasión [2025] Marzo", 500, leads=20)
    _walcu(pg, "2025-03-10,Facebook,Covey Autocasión [2025] Marzo,VO,20,20,0,0,3,1,0",
           *(f for f in ESCENARIO if "AiVORA" not in f), nombre="con_covey.csv")
    resumen = ref.actualizar(pg)
    assert resumen["otras_marcas"] == {"meta_campanas": 1, "meta_gasto_cents": 50_000, "walcu_filas": 1,
                                       "walcu_leads": 20}
    r = ref.leer_referencias(pg)
    assert r[(2025, "todo")]["gasto_cents"] == 55_000 and r[(2025, "todo")]["leads"] == 47   # como sin Covey


# ================================================================== tarjetas de Marketing con Walcu semanal

def _tarjeta(html: str, titulo: str) -> dict[str, str]:
    bloque = html.split('id="t-campanas"')[1].split("</section>")[0]
    fila = bloque.split(f'<span class="campana-nombre">{titulo}</span>')[1].split("</tr>")[0]
    celdas = dict(re.findall(r'<td data-etiqueta="([^"]+)"[^>]*>(.*?)</td>', fila, re.S))
    diag = bloque.split(f'<span class="campana-nombre">{titulo}</span>')[1].split('class="fila-diagnostico"')
    celdas["criterios"] = diag[1].split("</tr>")[0] if len(diag) > 1 else ""
    return celdas


def _marketing(pg, panel_url):
    from test_contrato_panel import ROLES, CLAVE, app_con, dentro
    from panel import usuarios
    for rol in ROLES:
        usuarios.alta(pg, rol, f"Persona {rol}", rol, CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    return dentro(app_con(panel_url), "marketing").get("/marketing").text


def test_tarjeta_y_regla_con_los_mismos_leads_y_citas(pg, panel_url, conc):
    """Solo la semana del 26-10 con leads (citas completas): la tarjeta y la regla a dicen lo mismo."""
    _escenario(pg)
    _campana_real(pg, "9", GOLF, 50, 26)            # 26 días a 50 € = 1.300 €
    _walcu_nov(pg, f"2026-10-26,Facebook,{GOLF},VN,20,10,8,2,8,1,0")
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    j = next(x for x in js if x.campana_id == "9")
    assert (j.leads, j.leads_cita, j.citas) == (20, 20, 8)
    assert "lead a cita: 8 de 20" in j.reglas["a"].cifras_texto
    motor.evaluar(pg, HOY, conc)
    t = _tarjeta(_marketing(pg, panel_url), GOLF)
    assert t["Gasto"] == "1.300 €"
    assert t["Leads"] == "20" and t["Citas"] == "8"                 # los de la regla, sin «provisional»
    assert t["€ / lead"] == "65 €" and t["€ / cita"] == "162 €"      # 1.300 € / 20 y / 8 = 162,5 (al euro, como el panel)
    assert "provisional" not in t["Citas"] and "provisional" not in t["€ / cita"]
    # La venta: la semana aún no tiene 60 días
    assert t["Ventas"].startswith("1") and "provisional" in t["Ventas"] and "provisional" in t["€ / venta"]
    # Regla a (fuera de zona: 8 de 18 = 44 % > 35 %): «Revisar:» antes de lo que hay que revisar
    assert "(umbral 35 %). Revisar: la localización" in t["criterios"]


def test_tarjeta_con_citas_provisionales_y_sin_walcu(pg, panel_url, conc):
    _escenario(pg)
    _campana_real(pg, "9", GOLF, 50, 26)
    _campana_real(pg, "10", "VW [VN][2026] Polo noviembre", 20, 5, leads_meta=3)   # sin leads de Walcu que casen
    _walcu_nov(pg, f"2026-10-26,Facebook,{GOLF},VN,20,10,8,2,8,1,0", f"2026-11-09,Facebook,{GOLF},VN,10,10,0,0,5,0,0")
    _, js = cr.juzgar(pg, HOY, "real", conc, motor.cargar_criterios(), {})
    j = next(x for x in js if x.campana_id == "9")
    motor.evaluar(pg, HOY, conc)
    html = _marketing(pg, panel_url)
    t = _tarjeta(html, GOLF)
    # Leads: todos, como la regla c; citas: todas hasta hoy (13), provisionales (la semana del 9-11 aún no tiene 14 días)
    assert t["Leads"] == str(j.leads) == "30" and "coste por lead: 1.300 € / 30 leads" in j.reglas["c"].cifras_texto
    assert t["Citas"].startswith("13") and "provisional" in t["Citas"] and "provisional" in t["€ / cita"]
    assert j.citas == 8                                             # la regla, solo con las semanas completas
    # Sin Walcu que case: los leads según Meta (5 días × 3) y citas y ventas «—»
    p = _tarjeta(html, "VW [VN][2026] Polo noviembre")
    assert p["Leads"].startswith("15") and "según Meta" in p["Leads"]
    assert p["Citas"] == "—" and p["€ / cita"] == "—" and p["€ / lead"] == "7 €"
