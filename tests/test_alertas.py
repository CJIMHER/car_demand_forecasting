"""Motor de alertas (alertas/) y su envío por Telegram. Sin red: Telegram siempre simulado.

Cada regla con casos que la disparan y que no (n insuficiente, falta de la captura de hace 7 días),
la deduplicación (tres días con la misma condición, un solo envío), las alertas sobre filas de
ejemplo (solo al técnico) y los bloques de alertas de las vistas.
"""

from __future__ import annotations

from datetime import date, timedelta

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from alertas import envio, motor
from alertas.reglas import CALCULOS
from ingesta import telegram
from ingesta.concesionario import cargar as cargar_concesionario
from panel import ejemplo

HOY = date(2026, 11, 20)
H7 = HOY - timedelta(days=7)
COMPETIDOR = "ESP00713"
PROPIO = "ESP00710"


@pytest.fixture
def conc():
    return cargar_concesionario()


def reglas(*ids):
    _, todas = motor.cargar_reglas()
    return [r for r in todas if r.id in ids]


def evaluar(pg, conc, *ids, dia=HOY):
    return motor.evaluar(pg, dia, conc, reglas(*ids))


def alertas(pg, regla=None, dia=HOY, estados=("nueva", "abierta")):
    cols = ("regla", "rol", "clave", "texto", "cifras", "origen", "estado", "abierta_desde", "tipo")
    filtro = "" if regla is None else "AND regla = %(regla)s"
    return [dict(zip(cols, f)) for f in pg.execute(
        f"SELECT {', '.join(cols)} FROM alertas WHERE fecha = %(dia)s AND estado = ANY(%(e)s) {filtro} "
        "ORDER BY regla, clave, rol", {"dia": dia, "e": list(estados), "regla": regla})]


def anotar(pg, fuente, tipo, estado, dia=HOY, **detalle):
    pg.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle) VALUES (%s, %s, %s, %s, %s)",
               (fuente, tipo, dia, estado, Jsonb(detalle)))


def todo_en_orden(pg, dia=HOY):
    for f in ("vo", "vn", "vn_otros"):
        anotar(pg, f, "comprobacion", "ok", dia)
        anotar(pg, f, "carga", "ok", dia)
    anotar(pg, "panel_mercado", "precalculo", "ok", dia)
    anotar(pg, "postgres", "copia", "ok", dia - timedelta(days=1))
    # Google Trends descargado hace poco (la descarga es manual y mensual)
    pg.execute("INSERT INTO gtrends_entrega (fecha_descarga, huella_manifiesto, carpeta, manifiesto) "
               "VALUES (%s, %s, %s, '{}') ON CONFLICT DO NOTHING", (dia - timedelta(days=3), "h", "c"))


def fila(pg, tabla, **cols):
    cols = {"dia": HOY, "origen": "real", **cols}
    pg.execute(sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
        sql.Identifier(tabla), sql.SQL(", ").join(map(sql.Identifier, cols)),
        sql.SQL(", ").join(sql.Placeholder() * len(cols))),
        [Jsonb(v) if isinstance(v, dict) or (isinstance(v, list) and v and isinstance(v[0], dict)) else v
         for v in cols.values()])


class Telegram:
    """Sustituye a telegram.enviar: guarda (chat, texto)."""

    def __init__(self, falla=None):
        self.enviados, self.falla = [], falla

    def __call__(self, texto, chat_id=None):
        if self.falla:
            raise self.falla
        self.enviados.append((chat_id, texto))
        return 1


CHATS = {"TELEGRAM_CHAT_ID": "-100", "TELEGRAM_CHAT_DIRECCION": "-200", "TELEGRAM_CHAT_VENTAS": "-300",
         "TELEGRAM_CHAT_MARKETING": "-400"}


# ================================================================== reglas.yaml

def test_reglas_en_yaml_con_todo_lo_necesario():
    version, todas = motor.cargar_reglas()
    assert version
    assert {r.id for r in todas} == {
        "fuente_caida", "competidor_stock", "competidor_precio", "perdida_peso", "vehiculo_envejecido",
        "riesgo_inmovilizacion", "ventas_fuera_banda", "caida_leads", "meta_a", "meta_b", "meta_c", "meta_d",
        "meta_f", "meta_subir", "ventana_favorable", "gtrends_pendiente", "campana_general"}
    for r in todas:
        assert r.calculo in CALCULOS and r.roles and r.fuente and (r.datos.get("texto") or r.datos.get("textos"))
    assert {r.id: r.roles for r in todas}["fuente_caida"] == ("tecnico",)
    assert {r.id: r.roles for r in todas}["gtrends_pendiente"] == ("tecnico",)
    assert set({r.id: r.roles for r in todas}["competidor_stock"]) == {"ventas", "direccion"}
    assert set({r.id: r.roles for r in todas}["perdida_peso"]) == {"ventas", "direccion"}


def test_reglas_mal_escritas_no_arrancan(tmp_path):
    f = tmp_path / "reglas.yaml"
    f.write_text("reglas:\n  - {id: x, nombre: X, calculo: inventado, roles: [ventas], fuente: [t], tema: datos, "
                 "nivel: aviso, texto: hola}\n")
    with pytest.raises(motor.ErrorReglas, match="cálculo desconocido"):
        motor.cargar_reglas(f)
    f.write_text("reglas:\n  - {id: x, nombre: X, calculo: fuente_caida, roles: [gerente], fuente: [t], tema: datos, "
                 "nivel: aviso, texto: hola}\n")
    with pytest.raises(motor.ErrorReglas, match="roles desconocidos"):
        motor.cargar_reglas(f)


def test_los_umbrales_salen_del_yaml(pg, conc, tmp_path):
    """Con otro umbral en el YAML, la misma situación da otro resultado: nada está fijado en el código."""
    _mercado(pg)
    _competidor(pg, H7, "Golf", 10)
    _competidor(pg, HOY, "Golf", 14)       # +4 anuncios, +40 %
    motor.evaluar(pg, HOY, conc, reglas("competidor_stock"))
    assert alertas(pg, "competidor_stock") == []
    texto = (motor.AQUI / "reglas.yaml").read_text().replace("subida_unidades: 5", "subida_unidades: 4")
    (tmp_path / "reglas.yaml").write_text(texto)
    _, otras = motor.cargar_reglas(tmp_path / "reglas.yaml")
    motor.evaluar(pg, HOY, conc, [r for r in otras if r.id == "competidor_stock"])
    assert len(alertas(pg, "competidor_stock")) == 2


# ================================================================== fuente caída

def test_fuente_caida_no_salta_si_todo_va_bien(pg, conc):
    todo_en_orden(pg)
    anotar(pg, "meta", "descarga", "sin_credenciales", motivo="faltan")
    anotar(pg, "ga4", "descarga", "sin_credenciales", motivo="faltan")
    evaluar(pg, conc, "fuente_caida")
    assert alertas(pg) == []


def test_fuente_caida_cada_caso(pg, conc):
    anotar(pg, "vo", "comprobacion", "sin_captura", ultima_captura="2026-11-18")
    anotar(pg, "vn", "comprobacion", "ok")
    anotar(pg, "vn_otros", "comprobacion", "error", error="No existe la base")
    anotar(pg, "vn", "carga", "error", error="el scraper dice 10 y hay 9")
    anotar(pg, "panel_mercado", "precalculo", "error", paso="precálculo", error="ValueError: x")
    anotar(pg, "postgres", "copia", "error", HOY - timedelta(days=1), paso="subida al bucket")
    anotar(pg, "meta", "descarga", "error", error="la API rechazó la petición: HTTP 400")
    anotar(pg, "ga4", "descarga", "sin_credenciales", motivo="faltan")   # sin credenciales no es un error
    evaluar(pg, conc, "fuente_caida")
    a = {x["clave"]: x for x in alertas(pg)}
    assert set(a) == {"scraper|vo", "scraper|vn_otros", "carga|vn", "precalculo", "copia", "descarga|meta"}
    assert {x["rol"] for x in a.values()} == {"tecnico"}
    assert a["scraper|vo"]["texto"] == "Scraper VO: sin captura completa del 20/11; la última, el 18/11."
    assert a["scraper|vn_otros"]["texto"] == ("Scraper VN eléctricos e híbridos: la comprobación del 20/11 terminó "
                                             "con error (No existe la base).")
    assert a["carga|vn"]["texto"] == "Carga de VN a Postgres del 20/11 con error: el scraper dice 10 y hay 9."
    assert a["precalculo"]["texto"] == "El precálculo del panel del 20/11 falló en «precálculo»: ValueError: x."
    assert a["copia"]["texto"] == "La última copia de Postgres (19/11) falló en «subida al bucket»."
    assert a["descarga|meta"]["texto"].startswith("Descarga de Meta del 20/11 con error")
    assert a["scraper|vo"]["cifras"]["caso"] == "scraper_sin_captura"


def test_fuente_caida_sin_tareas_anotadas_y_copia_vieja(pg, conc):
    anotar(pg, "postgres", "copia", "ok", HOY - timedelta(days=4))
    evaluar(pg, conc, "fuente_caida")
    a = {x["clave"]: x["texto"] for x in alertas(pg)}
    assert a == {
        "comprobacion": "Sin comprobación de los scrapers del 20/11: la tarea de las 09:00 no ha anotado nada.",
        "precalculo": "Sin precálculo del panel del 20/11: la tarea de las 09:10 no ha anotado nada.",
        "copia": "Sin copia correcta de Postgres desde el 16/11 (4 días)."}


# ================================================================== mercado

def _mercado(pg, tipo="VO", hoy_completo=True, hace7=True):
    for dia, h7 in ((H7, True), (HOY, hace7)):
        if dia == HOY and not hoy_completo:
            continue
        pg.execute("""INSERT INTO panel_mercado_dia (dia, tipo, zona, zona_nombre, anuncios, propios, concesionarios,
                      ayer_completo, hace7_completo, ventana_completa)
                      VALUES (%s, %s, 'provincia', 'Provincia', 100, 10, 5, true, %s, true)""", (dia, tipo, h7))


def _competidor(pg, dia, modelo, anuncios, *, dealer=COMPETIDOR, nombre="Competidor Uno", tipo="VO", acabado=None,
                n_precio=0, precio_euros=None):
    pg.execute("""INSERT INTO panel_mercado_competidor (dia, tipo, dealer_id, concesionario, es_propio, modelo, acabado,
                  anuncios, n_precio, precio_mediano_cents) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
               (dia, tipo, dealer, nombre, dealer == PROPIO, modelo, acabado, anuncios, n_precio,
                None if precio_euros is None else precio_euros * 100))


def test_competidor_sube_stock_salta_para_ventas_y_direccion(pg, conc):
    _mercado(pg)
    _competidor(pg, H7, "Golf", 10)
    _competidor(pg, HOY, "Golf", 16)
    resumen = evaluar(pg, conc, "competidor_stock")
    a = alertas(pg, "competidor_stock")
    assert [x["rol"] for x in a] == ["direccion", "ventas"]
    assert a[0]["texto"] == ("Competidor Uno sube su stock de Golf de ocasión en Sevilla: de 10 a 16 anuncios en "
                             "7 días (+60 %).")
    assert a[0]["cifras"] == {"dealer_id": COMPETIDOR, "modelo": "Golf", "antes": 10, "ahora": 16,
                              "subida_pct": 60.0, "n_minimo": conc.n_minimo_porcentaje}
    assert a[0]["clave"] == f"VO|{COMPETIDOR}|Golf" and a[0]["tipo"] == "VO"
    assert resumen["reglas"]["competidor_stock"] == "real: 1 disparadas (sin evaluar: VN: sin captura completa de hoy)"


def test_competidor_stock_no_salta(pg, conc):
    _mercado(pg)
    _competidor(pg, H7, "Golf", conc.n_minimo_porcentaje - 1, dealer="ESP09991")        # n insuficiente
    _competidor(pg, HOY, "Golf", conc.n_minimo_porcentaje + 20, dealer="ESP09991")
    _competidor(pg, H7, "Polo", 10, dealer="ESP09992")                                   # +4: pocas unidades
    _competidor(pg, HOY, "Polo", 14, dealer="ESP09992")
    _competidor(pg, H7, "Tiguan", 40, dealer="ESP09993")                                 # +6 = 15 %: poco %
    _competidor(pg, HOY, "Tiguan", 46, dealer="ESP09993")
    _competidor(pg, H7, "Golf", 10, dealer=PROPIO)                                       # el propio no compite
    _competidor(pg, HOY, "Golf", 30, dealer=PROPIO)
    _competidor(pg, HOY, "Taigo", 30, dealer="ESP09994")                                 # no estaba hace 7 días
    evaluar(pg, conc, "competidor_stock")
    assert alertas(pg, "competidor_stock") == []


@pytest.mark.parametrize("caso", ["sin_hace7", "sin_hoy"])
def test_sin_captura_de_hace_7_dias_ninguna_alerta_de_mercado(pg, conc, caso):
    _mercado(pg, hace7=caso != "sin_hace7", hoy_completo=caso != "sin_hoy")
    _competidor(pg, H7, "Golf", 10)
    _competidor(pg, HOY, "Golf", 30)
    _competidor(pg, H7, "Golf", 10, acabado="Life", n_precio=10, precio_euros=20000)
    _competidor(pg, HOY, "Golf", 10, acabado="Life", n_precio=10, precio_euros=15000)
    _modelo(pg, H7, "Golf", 40, 20)
    _modelo(pg, HOY, "Golf", 40, 2)
    evaluar(pg, conc, "competidor_stock", "competidor_precio", "perdida_peso")
    assert alertas(pg) == []
    motivo = "falta la captura de hace 7 días" if caso == "sin_hace7" else "sin captura completa de hoy"
    assert pg.execute("SELECT DISTINCT estado, motivo FROM alertas_evaluacion").fetchall() == [
        ("sin_evaluar", f"VN: sin captura completa de hoy; VO: {motivo}")]


def test_competidor_baja_precio_de_un_segmento(pg, conc):
    _mercado(pg)
    n = conc.n_minimo_medianas
    _competidor(pg, H7, "T-Roc", 8, acabado="Life", n_precio=n, precio_euros=24000)
    _competidor(pg, HOY, "T-Roc", 8, acabado="Life", n_precio=n + 1, precio_euros=22800)    # −5 %
    _competidor(pg, H7, "T-Roc", 8, acabado="Style", n_precio=n - 1, precio_euros=24000)     # n insuficiente
    _competidor(pg, HOY, "T-Roc", 8, acabado="Style", n_precio=n, precio_euros=20000)
    _competidor(pg, H7, "Golf", 8, acabado="Life", n_precio=n, precio_euros=20000)          # −2 %: poco
    _competidor(pg, HOY, "Golf", 8, acabado="Life", n_precio=n, precio_euros=19600)
    evaluar(pg, conc, "competidor_precio")
    a = alertas(pg, "competidor_precio")
    assert {x["clave"] for x in a} == {f"VO|{COMPETIDOR}|T-Roc|Life"}
    assert a[0]["texto"] == ("Competidor Uno baja el precio mediano de T-Roc Life de ocasión: de 24.000 € a "
                             "22.800 € en 7 días (−5,0 %, 6 anuncios con precio).")


def _modelo(pg, dia, modelo, anuncios, propios, tipo="VO"):
    pg.execute("""INSERT INTO panel_mercado_modelo (dia, tipo, zona, modelo, anuncios, propios, n_precio)
                  VALUES (%s, %s, 'provincia', %s, %s, %s, 0)""", (dia, tipo, modelo, anuncios, propios))


def test_perdida_de_peso(pg, conc):
    _mercado(pg)
    n = conc.n_minimo_porcentaje
    _modelo(pg, H7, "T-Roc", 40, 10)        # 25 % → 15 %: −10 puntos
    _modelo(pg, HOY, "T-Roc", 40, 6)
    _modelo(pg, H7, "Golf", 40, 10)         # 25 % → 20 %: −5 puntos, no «más de» 5
    _modelo(pg, HOY, "Golf", 40, 8)
    _modelo(pg, H7, "Polo", n - 1, 5)       # base insuficiente
    _modelo(pg, HOY, "Polo", n + 5, 0)
    evaluar(pg, conc, "perdida_peso")
    a = alertas(pg, "perdida_peso")
    assert {(x["clave"], x["rol"]) for x in a} == {("VO|T-Roc", "ventas"), ("VO|T-Roc", "direccion")}
    assert a[0]["texto"] == ("Nuestro peso en T-Roc de ocasión en Sevilla cae 10,0 puntos en 7 días: del 25,0 % "
                             "al 15,0 % (6 de 40 anuncios).")


# ================================================================== preparadas (contrato del panel)

def _riesgo(pg, stock_id, dias, p50, origen="real", grupo="G1", tipo="VN"):
    fila(pg, "panel_riesgo_vehiculo", origen=origen, stock_id=stock_id, tipo=tipo, modelo="T-Roc", version="Life",
         detalle="x", dias_exposicion=dias, grupo=grupo, p_venta90_p10=max(p50 - 0.1, 0), p_venta90_p50=p50,
         p_venta90_p90=min(p50 + 0.1, 1), precio_mercado_pct=None, similares_n=0, campana_activa=False,
         sugerencia="mantener", leads_14=None, leads_14_previos=None, modelo_version="v", n=30)


def _curva(pg, origen="real", grupo="G1"):
    fila(pg, "panel_curva_grupo", origen=origen, grupo=grupo, descripcion="T-Roc nuevos", t_dias=[0, 30, 60, 90, 120],
         s_p10=[1.0, 0.6, 0.3, 0.15, 0.1], s_p50=[1.0, 0.7, 0.4, 0.25, 0.15], s_p90=[1.0, 0.8, 0.5, 0.35, 0.2],
         mediana_p10=40, mediana_p50=50, mediana_p90=60, n=30, n_ventas=20, modelo_version="v")


def test_preparadas_inactivas_sin_filas_reales(pg, conc):
    resumen = evaluar(pg, conc, "vehiculo_envejecido", "riesgo_inmovilizacion", "ventas_fuera_banda", "caida_leads",
                      "meta_a", "meta_b", "meta_c", "meta_d", "meta_f", "meta_subir", "ventana_favorable")
    assert all(t.startswith("inactiva: sin filas reales en panel_") for t in resumen["reglas"].values())
    assert alertas(pg) == [] and pg.execute("SELECT count(*) FROM alertas_evaluacion").fetchone() == (0,)


def test_vehiculo_envejecido(pg, conc):
    _curva(pg)                               # el 75 % ha salido a los 90 días
    _riesgo(pg, "S1", 100, 0.6)
    _riesgo(pg, "S2", 90, 0.6)
    _riesgo(pg, "S3", 200, 0.6, grupo="SIN_CURVA")
    evaluar(pg, conc, "vehiculo_envejecido")
    a = alertas(pg, "vehiculo_envejecido")
    assert [(x["clave"], x["rol"]) for x in a] == [("S1", "ventas")]
    # Ventas: qué pasa y qué hacer, sin el percentil ni el límite (eso, en el texto técnico)
    assert a[0]["texto"] == ("T-Roc Life (S1): 100 días en stock, más que la mayoría de coches parecidos. "
                             "Revisar el precio o pedir una campaña.")


def test_riesgo_de_inmovilizacion(pg, conc):
    _riesgo(pg, "S1", 50, 0.3)
    _riesgo(pg, "S2", 50, 0.5)
    evaluar(pg, conc, "riesgo_inmovilizacion")
    a = alertas(pg, "riesgo_inmovilizacion")
    assert {(x["clave"], x["rol"]) for x in a} == {("S1", "ventas"), ("S1", "direccion")}
    assert a[0]["texto"] == "T-Roc Life (S1): 30 % de venderse en 90 días (entre 20 y 40 %), con 50 días en stock."


def test_ventas_fuera_de_la_banda(pg, conc):
    for tipo, real in (("VN", 5), ("VO", 12), ("todo", 30)):
        fila(pg, "panel_ventas_mes", tipo=tipo, dia_mes=HOY.day, ventas_acumuladas=real, previsto_p10=10,
             previsto_p50=15, previsto_p90=20, modelo_version="ref", n=24)
    evaluar(pg, conc, "ventas_fuera_banda")
    a = {x["clave"]: x for x in alertas(pg, "ventas_fuera_banda")}
    assert set(a) == {"VN", "todo"} and a["VN"]["rol"] == "direccion"
    assert a["VN"]["texto"] == "Ventas de nuevos del mes por debajo de lo previsto: 5 a día 20, frente a 10–20."
    assert a["todo"]["texto"].startswith("Ventas totales del mes por encima")


def test_caida_de_leads(pg, conc):
    for i in range(28):
        d = HOY - timedelta(days=i)
        reciente = i < 14
        fila(pg, "panel_leads_dia", dia=d, modelo="Golf", tipo="VN", campana_id=None, leads=1 if reciente else 2,
             citas=0, ventas=0)                                       # 14 frente a 28: −50 %
        fila(pg, "panel_leads_dia", dia=d, modelo="Polo", tipo="VN", campana_id=None, leads=0 if reciente else (1 if i < 23 else 0),
             citas=0, ventas=0)                                       # base 9: insuficiente
    evaluar(pg, conc, "caida_leads")
    a = alertas(pg, "caida_leads")
    assert [(x["clave"], x["rol"]) for x in a] == [("VN|Golf", "marketing")]
    assert a[0]["texto"] == "Leads de Golf nuevos: 14 en los últimos 14 días frente a 28 en los 14 anteriores (−50 %)."


def _campana(pg, cid, titulo, gasto_por_dia, dias=35, inicio=None, ambito="VN"):
    for i in range(dias):
        fila(pg, "panel_campana_dia", dia=HOY - timedelta(days=i), campana_id=cid, titulo=titulo, tipo_campana="modelo",
             modelo="Golf", ambito=ambito, inicio=inicio or HOY - timedelta(days=dias - 1),
             gasto_cents=gasto_por_dia(i), impresiones=1000, clics=10)


def test_ventana_favorable(pg, conc):
    lunes = HOY - timedelta(days=HOY.weekday())
    for k, nivel in enumerate(("favorable", "favorable", "normal", "favorable")):
        fila(pg, "panel_semana_campana", tipo="todo", semana=lunes + timedelta(days=7 * k), nivel=nivel,
             indice_p10=0.9, indice_p50=1.2, indice_p90=1.4, motivos=[{"tipo": "calendario", "texto": "Puente"}],
             modelo_version="v", n=3)
    evaluar(pg, conc, "ventana_favorable")
    a = alertas(pg, "ventana_favorable")
    # La de esta semana ya ha empezado y la cuarta queda fuera de las dos siguientes
    assert [x["clave"] for x in a] == [f"todo|{lunes + timedelta(days=7)}"]
    assert a[0]["texto"] == ("Semana del 23/11 favorable para una campaña general (VN y VO): 1,20 veces una semana "
                             "media (0,90–1,40). Puente.")


# ================================================================== ejemplo: solo al técnico

def test_con_filas_de_ejemplo_solo_va_al_tecnico_y_con_ejemplo_delante(pg, conc):
    ejemplo.cargar(pg, HOY, conc)
    resumen = motor.evaluar(pg, HOY, conc)
    origenes = dict(pg.execute("SELECT regla, origen FROM alertas_evaluacion").fetchall())
    for r in ("vehiculo_envejecido", "riesgo_inmovilizacion", "ventas_fuera_banda", "caida_leads", "meta_a",
              "meta_b", "meta_c", "meta_d", "meta_f", "meta_subir", "ventana_favorable"):
        assert origenes[r] == "ejemplo", (r, resumen["reglas"][r])
    de_ejemplo = [a for a in alertas(pg) if a["origen"] == "ejemplo"]
    assert len(de_ejemplo) > 5
    assert {a["rol"] for a in de_ejemplo} == {"tecnico"}
    assert all(a["texto"].startswith("[EJEMPLO] ") for a in de_ejemplo)
    # Por Telegram, solo con ALERTAS_ENVIAR_EJEMPLO=1, y entonces solo al chat del técnico
    t = Telegram()
    envio.enviar_dia(pg, HOY, enviar=t, entorno={**CHATS, "ALERTAS_ENVIAR_EJEMPLO": "1"})
    assert {c for c, _ in t.enviados} == {"-100"} and any("[EJEMPLO]" in x for _, x in t.enviados)
    # La base no admite una alerta de ejemplo para otro rol
    with pytest.raises(psycopg.errors.CheckViolation):
        pg.execute("""INSERT INTO alertas (fecha, regla, rol, clave, tema, nivel, texto, origen, estado, abierta_desde)
                      VALUES (%s, 'x', 'ventas', 'k', 'stock', 'aviso', 't', 'ejemplo', 'nueva', %s)""", (HOY, HOY))


def test_con_fuente_real_la_regla_deja_el_ejemplo(pg, conc):
    ejemplo.cargar(pg, HOY, conc)
    _riesgo(pg, "REAL1", 40, 0.2)            # primera fila real de panel_riesgo_vehiculo
    evaluar(pg, conc, "riesgo_inmovilizacion")
    a = alertas(pg, "riesgo_inmovilizacion")
    assert {(x["clave"], x["origen"], x["rol"]) for x in a} == {("REAL1", "real", "ventas"),
                                                                ("REAL1", "real", "direccion")}


# ================================================================== deduplicación y cierre

def test_tres_dias_con_la_misma_condicion_un_solo_envio(pg, conc):
    t = Telegram()
    for k in range(3):
        dia = HOY + timedelta(days=k)
        anotar(pg, "postgres", "copia", "ok", dia - timedelta(days=1))
        evaluar(pg, conc, "fuente_caida", dia=dia)             # sin comprobación ni precálculo, tres días
        envio.enviar_dia(pg, dia, enviar=t, entorno=CHATS)
        assert [x["estado"] for x in alertas(pg, dia=dia)] == ["nueva" if k == 0 else "abierta"] * 2
        assert {x["abierta_desde"] for x in alertas(pg, dia=dia)} == {HOY}
    assert len(t.enviados) == 1
    chat, texto = t.enviados[0]
    assert chat == "-100" and texto.startswith("CDF 20/11 · 2 alertas nuevas para técnico")
    # Recalcular el mismo día no reenvía
    evaluar(pg, conc, "fuente_caida", dia=HOY + timedelta(days=2))
    envio.enviar_dia(pg, HOY + timedelta(days=2), enviar=t, entorno=CHATS)
    assert len(t.enviados) == 1
    # El cuarto día todo va bien: se cierran (una fila «cerrada» cada una) y no se envía nada
    dia4 = HOY + timedelta(days=3)
    todo_en_orden(pg, dia4)
    evaluar(pg, conc, "fuente_caida", dia=dia4)
    envio.enviar_dia(pg, dia4, enviar=t, entorno=CHATS)
    assert [x["estado"] for x in alertas(pg, dia=dia4, estados=("nueva", "abierta", "cerrada"))] == ["cerrada"] * 2
    assert len(t.enviados) == 1
    # El quinto, ni rastro
    dia5 = dia4 + timedelta(days=1)
    todo_en_orden(pg, dia5)
    evaluar(pg, conc, "fuente_caida", dia=dia5)
    assert alertas(pg, dia=dia5, estados=("nueva", "abierta", "cerrada")) == []
    # Si vuelve a caer, es una alerta nueva y se envía
    dia6 = dia5 + timedelta(days=1)
    anotar(pg, "postgres", "copia", "ok", dia6 - timedelta(days=1))
    evaluar(pg, conc, "fuente_caida", dia=dia6)
    envio.enviar_dia(pg, dia6, enviar=t, entorno=CHATS)
    assert len(t.enviados) == 2


def test_sin_captura_la_alerta_abierta_sigue_abierta(pg, conc):
    _mercado(pg)
    _competidor(pg, H7, "Golf", 10)
    _competidor(pg, HOY, "Golf", 20)
    evaluar(pg, conc, "competidor_stock")
    manana = HOY + timedelta(days=1)
    pg.execute("""INSERT INTO panel_mercado_dia (dia, tipo, zona, zona_nombre, anuncios, propios, concesionarios,
                  ayer_completo, hace7_completo, ventana_completa)
                  VALUES (%s, 'VO', 'provincia', 'Provincia', 100, 10, 5, true, false, false)""", (manana,))
    evaluar(pg, conc, "competidor_stock", dia=manana)
    assert [x["estado"] for x in alertas(pg, "competidor_stock", dia=manana)] == ["abierta", "abierta"]


# ================================================================== envío por rol

def _dos_roles(pg, conc):
    _mercado(pg)
    _competidor(pg, H7, "Golf", 10)
    _competidor(pg, HOY, "Golf", 16)
    _modelo(pg, H7, "T-Roc", 40, 10)
    _modelo(pg, HOY, "T-Roc", 40, 4)
    evaluar(pg, conc, "competidor_stock", "perdida_peso")


def test_un_mensaje_por_rol_y_dia(pg, conc):
    _dos_roles(pg, conc)
    t = Telegram()
    resultado = envio.enviar_dia(pg, HOY, enviar=t, entorno=CHATS)
    assert resultado == {"direccion": "enviado: 2", "ventas": "enviado: 2"}
    por_chat = dict(t.enviados)
    assert set(por_chat) == {"-200", "-300"}
    lineas = por_chat["-300"].splitlines()
    assert lineas[0] == "CDF 20/11 · 2 alertas nuevas para ventas" and lineas[1] == ""
    assert all(l.startswith("• ") for l in lineas[2:]) and len(lineas) == 4
    salidas = pg.execute("SELECT rol, destino, estado, jsonb_array_length(alertas) FROM alertas_salida ORDER BY rol").fetchall()
    assert salidas == [("direccion", "direccion", "enviado", 2), ("ventas", "ventas", "enviado", 2)]


def test_sin_chat_del_rol_va_al_tecnico_con_el_rol_delante(pg, conc):
    _dos_roles(pg, conc)
    t = Telegram()
    envio.enviar_dia(pg, HOY, enviar=t, entorno={"TELEGRAM_CHAT_ID": "-100", "TELEGRAM_CHAT_VENTAS": "-300"})
    por_chat = {}
    for chat, texto in t.enviados:
        por_chat.setdefault(chat, []).append(texto)
    assert por_chat["-300"][0].startswith("CDF 20/11 · 2 alertas nuevas para ventas")
    assert por_chat["-100"][0].startswith("[Para dirección] CDF 20/11 · 2 alertas nuevas para dirección")
    assert pg.execute("SELECT destino FROM alertas_salida WHERE rol = 'direccion'").fetchone() == ("tecnico",)


def test_fallo_de_telegram_se_anota_no_rompe_y_se_reintenta(pg, conc, monkeypatch, caplog):
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "999:TOKEN-SECRETO")
    _dos_roles(pg, conc)
    fallo = Telegram(falla=telegram.ErrorTelegram("no se pudo enviar: https://api.telegram.org/bot999:TOKEN-SECRETO/x"))
    resultado = envio.enviar_dia(pg, HOY, enviar=fallo, entorno=CHATS)
    assert set(resultado) == {"direccion", "ventas"} and all(r.startswith("error") for r in resultado.values())
    errores = pg.execute("SELECT error FROM alertas_salida WHERE estado = 'error'").fetchall()
    assert len(errores) == 2 and all("TOKEN-SECRETO" not in e and "***" in e for (e,) in errores)
    anotado = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE fuente = 'telegram' AND tipo = 'aviso'").fetchall()
    assert len(anotado) == 2 and anotado[0][0] == "error" and "TOKEN-SECRETO" not in str(anotado)
    assert "TOKEN-SECRETO" not in caplog.text
    # Otra pasada con Telegram ya bien: lo que no salió, sale
    t = Telegram()
    envio.enviar_dia(pg, HOY, enviar=t, entorno=CHATS)
    assert len(t.enviados) == 2
    envio.enviar_dia(pg, HOY, enviar=t, entorno=CHATS)
    assert len(t.enviados) == 2


def test_sin_telegram_calcula_sin_enviar(pg, conc, capsys):
    _dos_roles(pg, conc)
    t = Telegram()
    resultado = envio.enviar_dia(pg, HOY, enviar=t, entorno=CHATS, sin_telegram=True)
    assert t.enviados == [] and pg.execute("SELECT count(*) FROM alertas_salida").fetchone() == (0,)
    assert set(resultado) == {"direccion", "ventas"}
    assert "2 alertas nuevas para ventas" in capsys.readouterr().out


def test_mensaje_largo_no_pierde_la_cuenta():
    filas = [{"texto": "x" * 300} for _ in range(30)]
    texto = envio.mensaje_alertas(HOY, "ventas", filas, para_otro=False)
    assert len(texto) <= telegram.LIMITE_TEXTO and texto.rstrip().endswith("más en el panel.")


def test_la_tarea_entera_anota_y_un_fallo_de_telegram_no_la_rompe(pg, conc, monkeypatch):
    monkeypatch.setattr(motor, "cargar_concesionario", lambda: conc)
    fallo = Telegram(falla=telegram.ErrorTelegram("caído"))
    assert motor.ejecutar(HOY, enviar=fallo, entorno=CHATS) == 0
    estado, detalle = pg.execute("SELECT estado, detalle FROM fuentes_estado WHERE fuente = 'alertas'").fetchone()
    assert estado == "ok" and detalle["nuevas"] >= 2 and detalle["envio"]["tecnico"].startswith("error")
    assert "inactiva" in detalle["reglas"]["meta_c"]


def test_sin_ningun_modelo_de_lenguaje():
    """Determinista: el motor no importa ni llama a ningún modelo de lenguaje."""
    from pathlib import Path
    codigo = " ".join(p.read_text() for p in Path(motor.__file__).parent.glob("*.py")).lower()
    for palabra in ("anthropic", "openai", "llm", "claude", "gpt"):
        assert palabra not in codigo


# ================================================================== precálculo: competidores

def test_competidores_cuadran_con_el_mercado_y_con_sql_directo(pg, datos):
    import statistics

    from analitica import mercado as am
    from test_panel import HOY as DIA_ESCENARIO, _con_datos
    conc = _con_datos(pg, datos)
    dias = [d for (d,) in pg.execute("SELECT DISTINCT dia FROM panel_mercado_competidor ORDER BY dia")]
    assert len(dias) > 5
    for dia in (dias[-1], dias[len(dias) // 2]):
        for tipo, fuentes in am.TIPOS.items():
            comp = pg.execute("""SELECT modelo, sum(anuncios) FROM panel_mercado_competidor
                                 WHERE dia = %s AND tipo = %s AND acabado IS NULL GROUP BY modelo""", (dia, tipo)).fetchall()
            if not comp:
                continue
            modelos = dict(pg.execute("SELECT modelo, anuncios FROM panel_mercado_modelo WHERE dia = %s AND tipo = %s "
                                      "AND zona = 'provincia'", (dia, tipo)).fetchall())
            assert dict(comp) == modelos
            por_dealer = dict(pg.execute("SELECT dealer_id, sum(anuncios) FROM panel_mercado_competidor WHERE dia = %s "
                                         "AND tipo = %s AND acabado IS NULL GROUP BY 1", (dia, tipo)).fetchall())
            assert por_dealer == dict(pg.execute("SELECT dealer_id, anuncios FROM panel_mercado_concesionario "
                                                 "WHERE dia = %s AND tipo = %s", (dia, tipo)).fetchall())
            # Precio mediano de cada concesionario y modelo, desde stock_externo_snapshot
            precios: dict = {}
            for dealer, modelo, precio in pg.execute(
                    """SELECT dealer_id, modelo, precio_cents FROM stock_externo_snapshot WHERE snapshot_day = %s
                       AND fuente = ANY(%s) AND provincia_codigo = %s AND precio_cents IS NOT NULL""",
                    (dia, list(fuentes), conc.provincia_codigo)):
                precios.setdefault((dealer, am.normalizar_modelo(modelo, conc.marca)), []).append(precio)
            for dealer, modelo, n, p in pg.execute(
                    """SELECT dealer_id, modelo, n_precio, precio_mediano_cents FROM panel_mercado_competidor
                       WHERE dia = %s AND tipo = %s AND acabado IS NULL""", (dia, tipo)):
                lista = precios.get((dealer, modelo), [])
                assert n == len(lista) and p == (round(statistics.median(lista)) if lista else None)
    # Los segmentos (modelo + acabado) nunca suman más que su modelo
    assert pg.execute("""SELECT count(*) FROM (SELECT dia, tipo, dealer_id, modelo, sum(anuncios) s
                         FROM panel_mercado_competidor WHERE acabado IS NOT NULL GROUP BY 1, 2, 3, 4) a
                         JOIN panel_mercado_competidor m USING (dia, tipo, dealer_id, modelo)
                         WHERE m.acabado IS NULL AND a.s > m.anuncios""").fetchone() == (0,)
    # Y el motor entero corre sobre el escenario sintético
    motor.evaluar(pg, DIA_ESCENARIO, conc)
    estados = dict(pg.execute("SELECT regla, estado FROM alertas_evaluacion WHERE origen = 'real'").fetchall())
    # (la campaña general, del precálculo de Mercado: también real)
    assert set(estados) == {"fuente_caida", "gtrends_pendiente", "competidor_stock", "competidor_precio", "perdida_peso",
                            "campana_general"}


# ================================================================== vistas

def test_las_vistas_leen_sus_alertas_de_la_tabla(pg, panel_url, conc):
    from test_contrato_panel import app_con, bloques
    from test_panel import dentro
    from panel import usuarios
    for rol in ("direccion", "ventas", "marketing", "tecnico"):
        usuarios.alta(pg, rol, f"Persona {rol}", rol, "clave-temporal-123")
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    ejemplo.cargar(pg, HOY, conc)
    ejemplo_texto = pg.execute("SELECT texto FROM panel_alertas WHERE origen = 'ejemplo' LIMIT 1").fetchone()[0]
    _dos_roles(pg, conc)
    motor.evaluar(pg, HOY, conc, reglas("fuente_caida"))
    cl = dentro(app_con(panel_url), "tecnico")
    b = bloques(cl.get("/direccion").text)
    origen, trozo = b["alertas"]
    assert origen == "real" and ejemplo_texto not in trozo and "Datos de ejemplo" not in trozo
    assert "Competidor Uno sube su stock de Golf" in trozo and "para dirección y jefe de ventas" in trozo
    assert "Sin comprobación de los scrapers" in trozo and "para técnico" in trozo
    assert b["alertas_kpi"][0] == "real" and 'class="kpi-valor">4<' in b["alertas_kpi"][1]
    origen, trozo = bloques(cl.get("/ventas").text)["alertas"]
    assert origen == "real" and "Competidor Uno" in trozo and "Nuestro peso en T-Roc" in trozo
    assert "Sin comprobación" not in trozo
    origen, trozo = bloques(cl.get("/marketing").text)["alertas"]
    assert origen == "real" and "Ninguna alerta abierta." in trozo
    html = cl.get("/estado-fuentes").text
    assert "Motor de alertas" in html and "Sin comprobación de los scrapers del 20/11" in html
    assert "Campañas sin regla en config/campanas.yaml" in html


# ================================================================== migración

def test_migracion_0009_ida_y_vuelta(pg_url, pg):
    from alembic import command
    from alembic.config import Config
    from sinteticos import RAIZ
    cfg = Config(str(RAIZ / "alembic.ini"))
    command.downgrade(cfg, "0008")
    assert pg.execute("SELECT to_regclass('alertas'), to_regclass('acciones')").fetchone() == (None, None)
    command.upgrade(cfg, "head")
    assert pg.execute("SELECT to_regclass('alertas') IS NOT NULL").fetchone() == (True,)
    # Con filas de tipos nuevos en fuentes_estado (que es de solo añadir), no se puede bajar
    anotar(pg, "alertas", "alertas", "ok")
    with pytest.raises(Exception):
        command.downgrade(cfg, "0008")


def test_alertas_de_ejemplo_no_se_envian_por_defecto(pg, conc):
    """Se guardan y se ven en el panel, pero no salen por Telegram sin ALERTAS_ENVIAR_EJEMPLO=1."""
    ejemplo.cargar(pg, HOY, conc)
    todo_en_orden(pg)
    motor.evaluar(pg, HOY, conc)
    de_ejemplo = [a for a in alertas(pg) if a["origen"] == "ejemplo"]
    assert de_ejemplo
    for valor in (None, "", "0", "si"):
        t = Telegram()
        entorno = dict(CHATS) if valor is None else {**CHATS, "ALERTAS_ENVIAR_EJEMPLO": valor}
        envio.enviar_dia(pg, HOY, enviar=t, entorno=entorno)
        assert t.enviados == [], valor
    assert pg.execute("SELECT count(*) FROM alertas_salida").fetchone() == (0,)
    # Con una alerta real el mismo día, esa sí sale, y sin las de ejemplo
    anotar(pg, "vo", "comprobacion", "sin_captura", ultima_captura="2026-11-18")
    motor.evaluar(pg, HOY, conc, reglas("fuente_caida"))
    t = Telegram()
    envio.enviar_dia(pg, HOY, enviar=t, entorno=CHATS)
    (chat, texto), = t.enviados
    assert chat == "-100" and "[EJEMPLO]" not in texto and "Scraper VO" in texto
    salida = pg.execute("SELECT alertas FROM alertas_salida").fetchone()[0]
    assert {a["origen"] for a in salida} == {"real"}
    # Y con la variable a 1, salen las de ejemplo que faltaban
    t = Telegram()
    envio.enviar_dia(pg, HOY, enviar=t, entorno={**CHATS, "ALERTAS_ENVIAR_EJEMPLO": "1"})
    assert len(t.enviados) == 1 and "[EJEMPLO]" in t.enviados[0][1]
