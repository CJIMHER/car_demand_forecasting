"""Marketing solo con datos reales, sin explicar Google Trends, y cuándo lanzar una campaña general.

- Marketing sin datos de ejemplo con PANEL_MODO_EJEMPLO vacío; tarjetas de arriba con los datos de Walcu por semanas;
  campañas pasadas reales.
- Ninguna mención a Google Trends (ni «Google», «Trends», «búsqueda» o «cuota») para quien no es técnico.
- Campaña general (analitica/campana_general.py, config/campana_general.yaml): cada regla sola basta; VO sin
  octubre-noviembre; la regla del stock pendiente sin 30 días; ventas por fecha de venta (walcu_ventas_semana, con
  la API mandando sobre el CSV); el aviso de Telegram solo en el cambio de estado.
- Validación del CSV de ventas y la agregación desde la exportación de negociaciones.
"""

from __future__ import annotations

import re
from dataclasses import replace
from datetime import date, timedelta

import pytest

from alertas import envio, motor
from analitica import campana_general as cg
from ingesta import walcu_ventas as wv
from ingesta.walcu import ErrorWalcu
from panel import ejemplo
from panel.app import crear_app
from test_alertas import reglas
from test_contrato_panel import BANDA, ETIQUETA, a_real, bloques, conc, personas  # noqa: F401
from test_panel import HOY, _con_datos, dentro

CFG = cg.cargar()
LUNES = date(2026, 11, 16)                    # lunes


def _app(panel_url, modo_ejemplo=False):
    return crear_app(dsn=panel_url, hoy=lambda: HOY, modo_ejemplo=modo_ejemplo)


def _fin(hoy: date) -> date:
    """Donde terminan las ventanas de la regla de ventas: semanas_recientes_excluidas antes de la última completa."""
    return cg.lunes(hoy) - timedelta(weeks=1 + CFG.semanas_recientes_excluidas)


def _semanas(hasta: date, n: int) -> list[date]:
    return [hasta - timedelta(weeks=i) for i in range(n)]


def _ventas(ultima: date, actual: int, anterior: int, tipo="VN") -> tuple[dict, set]:
    """Ventas por semana: `actual` en cada una de las 8 últimas, `anterior` en las 8 de antes."""
    s = _semanas(ultima, 16)
    return ({(x, tipo): (actual if i < 8 else anterior) for i, x in enumerate(s)}, set(s))


def _stock(hoy: date, antes: int, ahora: int, dias=40) -> dict:
    return {hoy - timedelta(days=d): (ahora if d < 10 else antes) for d in range(dias)}


FUERA_DE_TEMPORADA = date(2026, 1, 14)        # enero: ni VN ni VO


# ================================================================== reglas de la campaña general (cada una sola)

def test_solo_la_caida_de_ventas_basta():
    hoy = FUERA_DE_TEMPORADA
    ventas, cub = _ventas(_fin(hoy), 1, 2)      # 8 frente a 16: −50 %
    f = cg.evaluar("VN", hoy, ventas, cub, _stock(hoy, 50, 50), "web", CFG)
    assert f["conviene"] and [m["regla"] for m in f["motivos"]] == ["ventas"]
    assert f["motivos"][0]["texto"] == "Las ventas han bajado en las últimas 8 semanas"
    v = f["cifras"]["ventas"]
    assert (v["actual"], v["anterior"], v["cambio_pct"]) == (8, 16, -50.0)


def test_una_caida_del_20_justo_no_salta_y_la_diferencia_minima_se_respeta():
    hoy = FUERA_DE_TEMPORADA
    ultima = _fin(hoy)
    s = _semanas(ultima, 16)
    ventas = {(x, "VN"): 1 for x in s}
    ventas.update({(s[0], "VN"): 0, (s[1], "VN"): 0})              # 6 frente a 8: −25 %
    assert cg.regla_ventas(ventas, set(s), "VN", hoy, replace(CFG, diferencia_minima=0))["estado"] == "si"
    assert cg.regla_ventas(ventas, set(s), "VN", hoy, replace(CFG, diferencia_minima=3))["estado"] == "no"
    ventas = {(x, "VN"): (4 if i < 8 else 5) for i, x in enumerate(s)}   # 32 frente a 40: −20 %, no más
    assert cg.regla_ventas(ventas, set(s), "VN", hoy, CFG)["estado"] == "no"


def test_solo_la_subida_del_stock_basta():
    hoy = FUERA_DE_TEMPORADA
    ventas, cub = _ventas(_fin(hoy), 2, 2)
    f = cg.evaluar("VO", hoy, ventas, cub, _stock(hoy, 40, 51), "web", CFG)   # +27,5 %
    assert f["conviene"] and [m["regla"] for m in f["motivos"]] == ["stock"]
    assert f["motivos"][0]["texto"] == "El stock ha subido mucho en el último mes"
    f = cg.evaluar("VO", hoy, ventas, cub, _stock(hoy, 40, 50), "web", CFG)   # +25 %: no más del 25
    assert not f["conviene"]


def test_solo_la_temporada_basta_y_vo_sin_octubre_noviembre():
    ventas, cub = _ventas(_fin(LUNES), 2, 2)
    for mes, vn, vo in ((3, "campaña (marzo-abril)", "campaña (marzo-abril)"),
                        (7, "campaña (junio-julio)", "campaña (junio-julio)"),
                        (10, "liquidación (octubre-noviembre)", None), (11, "liquidación (octubre-noviembre)", None),
                        (1, None, None), (5, None, None), (12, None, None)):
        hoy = date(2026, mes, 20)
        v, c = _ventas(_fin(hoy), 2, 2)
        for tipo, esperado in (("VN", vn), ("VO", vo)):
            f = cg.evaluar(tipo, hoy, v, c, _stock(hoy, 40, 40), "web", CFG)
            if esperado:
                assert [m["texto"] for m in f["motivos"]] == [f"Temporada de {esperado}"], (mes, tipo)
            else:
                assert not f["conviene"], (mes, tipo)


def test_varias_a_la_vez_se_dicen_todas():
    hoy = date(2026, 11, 18)
    ventas, cub = _ventas(_fin(hoy), 1, 3)
    f = cg.evaluar("VN", hoy, ventas, cub, _stock(hoy, 40, 60), "web", CFG)
    assert [m["regla"] for m in f["motivos"]] == ["ventas", "stock", "temporada"]


def test_ahora_no_hace_falta_con_la_proxima_temporada():
    hoy = date(2026, 12, 2)
    ventas, cub = _ventas(_fin(hoy), 2, 2)
    vn = cg.evaluar("VN", hoy, ventas, cub, _stock(hoy, 40, 40), "web", CFG)
    assert not vn["conviene"] and vn["proxima"] == "marzo-abril"
    assert cg.proxima(date(2026, 8, 1), "VN", CFG).texto_meses == "octubre-noviembre"
    assert cg.proxima(date(2026, 8, 1), "VO", CFG).texto_meses == "marzo-abril"      # VO: sin octubre-noviembre
    assert cg.proxima(date(2026, 3, 10), "VO", CFG).texto_meses == "junio-julio"     # no la de ahora


def test_regla_del_stock_pendiente_sin_30_dias():
    hoy = FUERA_DE_TEMPORADA
    ventas, cub = _ventas(_fin(hoy), 2, 2)
    f = cg.evaluar("VO", hoy, ventas, cub, _stock(hoy, 10, 100, dias=20), "web", CFG)
    assert not f["conviene"]
    assert f["pendientes"] == [{"regla": "stock", "texto": "Pendiente: hace falta un mes de datos del stock"}]
    assert f["cifras"]["stock"]["estado"] == "pendiente"


def test_regla_de_ventas_pendiente_sin_16_semanas_o_con_ventas_viejas():
    hoy = FUERA_DE_TEMPORADA
    ultima = _fin(hoy)
    s = _semanas(ultima, 15)
    assert cg.regla_ventas({}, set(s), "VN", hoy, CFG)["estado"] == "pendiente"
    viejas = set(_semanas(ultima - timedelta(weeks=5), 16))
    assert cg.regla_ventas({}, viejas, "VN", hoy, CFG)["estado"] == "pendiente"
    tolerado = set(_semanas(ultima - timedelta(weeks=4), 16))
    assert cg.regla_ventas({}, tolerado, "VN", hoy, CFG)["estado"] == "no"
    f = cg.evaluar("VN", hoy, {}, set(s), {}, None, CFG)
    assert {p["regla"] for p in f["pendientes"]} == {"ventas", "stock"}


def test_la_configuracion_no_lleva_nada_del_concesionario_en_el_codigo():
    from pathlib import Path
    fuente = Path(cg.__file__).read_text(encoding="utf-8")
    for texto in ("Las ventas han bajado", "liquidación", "Ahora no hace falta", "Temporada de"):
        assert texto not in fuente


# ================================================================== ventas por fecha de venta

CSV = b"semana,tipo,modelo,ventas\n2026-11-02,VN,Polo,2\n2026-11-02,VO,Golf,1\n2026-11-16,VN,T-Roc,1\n"


def test_validacion_del_csv_de_ventas():
    v = wv.validar(CSV)
    assert len(v.filas) == 3 and v.desde == date(2026, 11, 2) and v.hasta == date(2026, 11, 16)
    for malo, texto in (
            (b"semana,tipo,modelo\n2026-11-02,VN,Polo\n", "columnas admitidas"),
            (b"semana,tipo,modelo,ventas\n2026-11-03,VN,Polo,1\n", "fila 2, columna semana: no es lunes"),
            (b"semana,tipo,modelo,ventas\n2026-11-02,Km0,Polo,1\n", "columna tipo: debe ser VN o VO"),
            (b"semana,tipo,modelo,ventas\n2026-11-02,VN,juan@correo.invalid,1\n", "columna modelo: parece un correo"),
            (b"semana,tipo,modelo,ventas\n2026-11-02,VN,Polo 600123456,1\n", "parece un tel"),
            (b"semana,tipo,modelo,ventas\n2026-11-02,VN,Polo,-1\n", "no es un entero no negativo"),
            (b"semana,tipo,modelo,ventas\n2026-11-02,VN,Polo,1\n2026-11-02,VN,Polo,2\n", "repite semana, tipo y modelo"),
            (b"semana,tipo,modelo,ventas\n", "no tiene filas")):
        with pytest.raises(ErrorWalcu) as exc:
            wv.validar(malo)
        assert texto in str(exc.value), (malo, str(exc.value))
        assert "juan@" not in str(exc.value) and "600123456" not in str(exc.value)


def test_carga_por_fecha_de_venta_semanas_sin_ventas_y_la_api_manda(pg):
    subidos = []
    r = wv.cargar_con(pg, CSV, "/Users/alguien/ventas.csv", HOY, subir=lambda c, d: subidos.append(d))
    assert r["fichero"] == "ventas.csv" and subidos and r["desde"] == "2026-11-02" and r["hasta"] == "2026-11-16"
    assert "ya_cargado" in wv.cargar_con(pg, CSV, "ventas.csv", HOY, subir=lambda c, d: None)   # no duplica
    ventas, cub = wv.vigentes(pg)
    assert cub == {date(2026, 11, 2), date(2026, 11, 9), date(2026, 11, 16)}   # la del 9: cubierta, 0 ventas
    assert ventas == {(date(2026, 11, 2), "VN"): 2, (date(2026, 11, 2), "VO"): 1, (date(2026, 11, 16), "VN"): 1}
    # La API (las ventas del CRM por su fecha) manda sobre el CSV en las semanas que cubre
    wv.escribir(pg, [{"semana": date(2026, 11, 9), "tipo": "VO", "modelo": "Golf", "ventas": 3}], "api",
                date(2026, 11, 9), date(2026, 11, 16), "h-api")
    ventas, cub = wv.vigentes(pg)
    assert ventas == {(date(2026, 11, 2), "VN"): 2, (date(2026, 11, 2), "VO"): 1, (date(2026, 11, 9), "VO"): 3}
    # Un CSV posterior no pisa la API
    wv.cargar_con(pg, CSV.replace(b",1\n2026-11-16", b",5\n2026-11-16"), "otro.csv", HOY, subir=lambda c, d: None)
    assert wv.vigentes(pg)[0][(date(2026, 11, 9), "VO")] == 3


def test_la_regla_usa_la_fecha_de_venta_y_escribe_el_panel(pg):
    ultima = _fin(HOY)
    filas = [{"semana": s, "tipo": "VN", "modelo": "Polo", "ventas": 1 if i < 8 else 3}
             for i, s in enumerate(_semanas(ultima, 16))]
    wv.escribir(pg, filas, "csv", min(f["semana"] for f in filas), ultima, "h")
    resumen = cg.actualizar(pg, HOY)
    assert resumen["VN"].startswith("conviene: ventas")
    fila = pg.execute("SELECT conviene, motivos, pendientes, cifras FROM panel_campana_general "
                      "WHERE origen = 'real' AND dia = %s AND tipo = 'VN'", (HOY,)).fetchone()
    assert fila[0] and fila[1][0]["regla"] == "ventas" and fila[3]["ventas"]["actual"] == 8


def test_la_api_de_walcu_genera_las_ventas_por_fecha(pg, monkeypatch):
    """Con el mapeo, la carga de la API escribe las ventas del CRM por semana de la venta (fuente api)."""
    import test_walcu_api as t
    cfg = replace(t.wa.cargar(), mapeo=t.MAPEO)
    t._cargar(pg, monkeypatch, cfg, t.datos_base())
    fuente, desde = pg.execute("SELECT fuente, desde FROM walcu_ventas_carga").fetchone()
    assert fuente == "api" and desde == date(2026, 9, 7)
    ventas, _ = wv.vigentes(pg)
    # lead1: Firmado el 20-9 (semana del 14-9), VO; lead5: vendido el 17-9 (semana del 14-9), VN
    assert ventas == {(date(2026, 9, 14), "VO"): 1, (date(2026, 9, 14), "VN"): 1}


def test_negociaciones_de_walcu_a_ventas_por_semana(tmp_path):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from scripts import walcu_agregar as wa
    export = tmp_path / "negociaciones.csv"
    export.write_text(
        "Id;Cliente;Estado;Fecha de cierre;Tipo negociación;Modelo;Sede;Equipo;Vendedor;Teléfono\n"
        "1;Juan Pérez;Cerrada / Vendido;03/11/2026;Nuevo;Volkswagen T-ROC 1.5 TSI;Sevilla;VN;Ana;600123456\n"
        "2;María López;Cerrada / Vendido;05/11/2026 10:30;Km0;Polo Life;Sevilla;VO;Ana;611222333\n"
        "3;Luis Gil;Cerrada / Vendido;10/11/2026;VO;;Sevilla;VO;Pepe;622333444\n"
        "4;Eva Ruiz;En curso;10/11/2026;Nuevo;Golf;Sevilla;VN;Ana;633444555\n"
        "5;Raúl Díaz;Cerrada / Vendido;11/11/2026;Nuevo;Golf;Otra Marca SA;VN;Ana;644555666\n"
        "6;Sara Mol;Cerrada / Vendido;12/11/2026;Nuevo;Golf;Sevilla;VN;Excluido Comercial;655666777\n",
        encoding="utf-8")
    excluidos = tmp_path / "excluidos.txt"
    excluidos.write_text("Excluido Comercial\n", encoding="utf-8")
    cfg = wa.cargar_config()
    cfg["negociaciones"]["sedes_excluidas"] = ["otra marca"]
    neg, avisos = wa.agregar_negociaciones(export, cfg, excluidos=wa.leer_excluidos(excluidos))
    contenido = neg.csv().decode()
    assert contenido == ("semana,tipo,modelo,ventas\n2026-11-02,VN,T-Roc,1\n2026-11-02,VO,Polo,1\n"
                         "2026-11-09,VO,(sin modelo),1\n")
    assert neg.cuentas["excluidas"] == 1 and neg.cuentas["otra_sede"] == 1 and neg.cuentas["ventas"] == 3
    for personal in ("Juan", "600123456", "Ana", "Excluido"):
        assert personal not in contenido and personal not in neg.registro()
    wv.validar(neg.csv())                          # pasa la validación del servidor
    salida = tmp_path / "ventas.csv"
    assert wa.main(["--negociaciones", str(export), "--ventas", str(salida), "--excluidos", str(excluidos)]) == 0
    assert salida.read_bytes().startswith(b"semana,tipo,modelo,ventas\n")


# ================================================================== Telegram: solo en el cambio de estado

def _fila_cg(pg, dia, tipo, conviene):
    from psycopg.types.json import Jsonb
    pg.execute("""INSERT INTO panel_campana_general (dia, origen, tipo, conviene, motivos, pendientes, proxima, cifras,
                                                     version) VALUES (%s, 'real', %s, %s, %s, '[]', 'marzo-abril', %s, 'v')""",
               (dia, tipo, conviene, Jsonb([{"regla": "ventas", "texto": "Las ventas han bajado en las últimas 8 semanas"}]
                                           if conviene else []),
                Jsonb({"ventas": {"estado": "si" if conviene else "no", "semanas": 8, "caida_pct_umbral": 20.0,
                                  "diferencia_minima": 0, "actual": 8, "anterior": 16, "cambio_pct": -50.0,
                                  "actual_desde": "2026-09-28", "actual_hasta": "2026-11-16",
                                  "anterior_desde": "2026-08-03", "anterior_hasta": "2026-09-21"}})))


def test_el_aviso_de_telegram_solo_en_el_cambio_de_estado(pg):
    from ingesta.concesionario import cargar
    conc = cargar()
    entorno = {"TELEGRAM_CHAT_ID": "t", "TELEGRAM_CHAT_MARKETING": "m", "TELEGRAM_CHAT_DIRECCION": "d"}
    enviados = []

    def enviar(texto, chat_id):
        enviados.append((chat_id, texto))
        return 1
    dia0 = date(2026, 11, 16)
    plan = [False, True, True, True, False, True]          # no, pasa a conviene, sigue, sigue, no, otra vez
    por_dia = []
    for i, conviene in enumerate(plan):
        dia = dia0 + timedelta(days=i)
        _fila_cg(pg, dia, "VN", conviene)
        motor.evaluar(pg, dia, conc, reglas("campana_general"))
        antes = len(enviados)
        envio.enviar_dia(pg, dia, enviar=enviar, entorno=entorno)
        por_dia.append(sorted(c for c, _ in enviados[antes:]))
    assert por_dia == [[], ["d", "m"], [], [], [], ["d", "m"]]
    for chat, texto in enviados:
        assert "Campaña general VN: conviene lanzarla. Las ventas han bajado en las últimas 8 semanas." in texto
        assert not re.search(r"umbral|%|regla", texto, re.I), texto


# ================================================================== Marketing solo con datos reales

def test_marketing_sin_datos_de_ejemplo_sin_el_modo_ejemplo(pg, datos, panel_url, personas, conc):
    _con_datos(pg, datos, conc)
    ejemplo.cargar(pg, HOY, conc)
    a_real(pg, "panel_campana_dia", "panel_campana_semana")      # Meta y Walcu por semanas, reales
    for rol in ("tecnico", "marketing"):
        html = dentro(_app(panel_url), rol).get("/marketing").text
        assert ETIQUETA not in html and BANDA not in html, rol
        assert "Las fuentes de esta vista aún no están conectadas" not in html
        b = bloques(html)
        assert all(o in ("real", "") for o, _ in b.values()), {n: o for n, (o, _) in b.items()}
        # Tarjetas de arriba: Walcu por semanas (leads, costes por lead, cita y venta), no «Sin datos todavía»
        kpis = b["kpis"][1]
        assert b["kpis"][0] == "real" and "Sin datos todavía" not in kpis
        for etiqueta in ("Leads", "Coste por lead", "Coste por cita", "Coste por venta"):
            assert f'<span class="kpi-etiqueta">{etiqueta}</span>' in kpis, etiqueta
        assert "citas" in kpis and "ventas" in kpis
        # Campañas pasadas reales, sin «sin campaña» ni veredictos
        pas = b["pasadas"][1]
        assert b["pasadas"][0] == "real" and 'class="pasada"' in pas and "sin campaña" not in pas.lower()
        # Lo que todavía es de ejemplo: «Sin datos todavía»
        for nombre in ("recomendacion", "atencion"):
            assert b[nombre][0] == "" and "Sin datos todavía" in b[nombre][1], nombre


NO_TECNICO = re.compile(r"google|trends|b[uú]squeda|cuota", re.I)


def _sin_trafico(html: str) -> str:
    """El HTML visible sin los bloques de tráfico de la web: sus canales («Google Ads», «Búsqueda orgánica») son
    nombres de canal de config/canales.yaml, no Google Trends."""
    html = re.sub(r'<section[^>]*data-bloque="trafico".*?</section>', "", html, flags=re.S)
    return re.sub(r"<[^>]+>", " ", html)


def test_ninguna_mencion_a_google_trends_para_quien_no_es_tecnico(pg, datos, panel_url, personas, conc):
    _con_datos(pg, datos, conc)
    ejemplo.cargar(pg, HOY, conc)
    for rol, rutas in (("direccion", ("/direccion", "/marketing", "/ventas", "/mercado")),
                       ("marketing", ("/marketing", "/mercado")), ("ventas", ("/ventas", "/mercado"))):
        cl = dentro(_app(panel_url, modo_ejemplo=True), rol)
        for ruta in rutas:
            for tipo in ("todo", "VN", "VO"):
                texto = _sin_trafico(cl.get(f"{ruta}?tipo={tipo}").text)
                m = NO_TECNICO.search(texto)
                assert m is None, (rol, ruta, tipo, texto[max(m.start() - 80, 0):m.end() + 80])
    # El técnico lo sigue viendo todo, en «Detalle técnico»
    html = dentro(_app(panel_url, modo_ejemplo=True), "tecnico").get("/marketing").text
    at = bloques(html)["atencion"][1]
    assert "Interés frente a sus rivales" in at and "Detalle técnico" in at and "Búsquedas en Google" in at


def test_el_bloque_de_interes_solo_dice_sube_estable_o_baja(pg, panel_url, personas, conc):
    ejemplo.cargar(pg, HOY, conc)
    a_real(pg, "panel_atencion_relativa")
    at = bloques(dentro(_app(panel_url), "marketing").get("/marketing").text)["atencion"][1]
    textos = re.findall(r'<span class="atencion-texto">([^<]+)</span>', at)
    assert textos and set(textos) <= {"Sube", "Estable", "Baja"}
    visible = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", at)).strip()
    modelos = re.findall(r'<span class="atencion-modelo">([^<]+)</span>', at)
    # Nada más que el título, cada modelo y su sube, estable o baja
    assert visible == " ".join(["Interés frente a sus rivales"] + [f"{m} {t}" for m, t in zip(modelos, textos)])


def test_campana_general_en_marketing_y_direccion_con_detalle_solo_para_tecnico(pg, panel_url, personas, conc):
    _fila_cg(pg, HOY, "VN", True)
    _fila_cg(pg, HOY, "VO", False)
    for rol, ruta in (("marketing", "/marketing"), ("direccion", "/direccion"), ("tecnico", "/marketing")):
        b = bloques(dentro(_app(panel_url), rol).get(ruta).text)
        origen, html = b["campana_general"]
        assert origen == "real"
        assert "Conviene lanzar una campaña general" in html and "Las ventas han bajado en las últimas 8 semanas" in html
        assert "Ahora no hace falta" in html and "Próxima temporada: marzo-abril" in html
        if rol == "tecnico":
            assert "Detalle técnico" in html and "−50,0 %" in html and "umbral" in html
        else:
            assert "Detalle técnico" not in html and "umbral" not in html and "%" not in html


# ================================================================== diferencia mínima y semanas recientes excluidas

def test_la_configuracion_pide_5_coches_y_excluye_las_2_ultimas_semanas():
    assert (CFG.diferencia_minima, CFG.semanas_recientes_excluidas) == (5, 2)


def test_las_ventanas_terminan_dos_semanas_antes_de_la_ultima_completa():
    hoy = date(2026, 11, 25)                                          # miércoles: última completa, la del 16-11
    s = _semanas(date(2026, 11, 16), 18)
    # Las dos últimas semanas completas (16-11 y 9-11) sin ventas: Walcu aún no las ha cerrado. No cuentan.
    ventas = {(x, "VN"): (0 if i < 2 else 2) for i, x in enumerate(s)}
    r = cg.regla_ventas(ventas, set(s), "VN", hoy, CFG)
    assert r["estado"] == "no" and (r["cifras"]["actual"], r["cifras"]["anterior"]) == (16, 16)
    assert (r["cifras"]["actual_hasta"], r["cifras"]["actual_desde"]) == ("2026-11-02", "2026-09-14")
    assert r["cifras"]["anterior_desde"] == "2026-07-20"
    # Sin excluirlas (ni diferencia mínima), esas dos semanas a cero harían saltar la regla: 12 frente a 16, −25 %
    sin = replace(CFG, semanas_recientes_excluidas=0, diferencia_minima=0)
    assert cg.regla_ventas(ventas, set(s), "VN", hoy, sin)["estado"] == "si"


def test_hacen_falta_5_coches_de_diferencia():
    hoy = FUERA_DE_TEMPORADA
    s = _semanas(_fin(hoy), 16)
    cuatro = {(x, "VN"): 1 for x in s} | {(s[i], "VN"): 0 for i in range(4)}     # 4 frente a 8: −50 %, 4 coches
    assert cg.regla_ventas(cuatro, set(s), "VN", hoy, CFG)["estado"] == "no"
    cinco = {(x, "VN"): 1 for x in s[8:]} | {(x, "VN"): 2 for x in s[8:10]} | {(s[0], "VN"): 5}   # 5 frente a 10
    assert cg.regla_ventas(cinco, set(s), "VN", hoy, CFG)["estado"] == "si"
