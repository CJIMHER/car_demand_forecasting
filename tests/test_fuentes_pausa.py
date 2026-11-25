"""Fuentes de la web de la marca en pausa (fuentes_en_pausa en config/concesionario.yaml).

Con una fuente en pausa: no se carga ni se comprueba (fuentes_estado: «en_pausa»), el resumen diario no la
cuenta como problema, «Fuente caída» no salta, el precálculo sigue con los últimos datos y el panel avisa en
Mercado, Ventas y Dirección de hasta cuándo hay datos (sin el motivo, salvo al técnico en Estado de las fuentes).
Reactivar es quitarla de la lista: la carga siguiente trae lo pendiente.
"""

from __future__ import annotations

import re
from datetime import date

import pytest

from analitica import mercado as am
from conftest import sin_pausas_texto
from ingesta import colores
from ingesta import scrapers_vw as sv
from ingesta.concesionario import ErrorConfiguracion, Pausa, fuentes_en_pausa
from ingesta.concesionario import cargar as cargar_concesionario
from panel import ejemplo
from panel import mercado as pm
from panel.app import crear_app
from sinteticos import RAIZ, capturar, coche, flota
from test_alertas import alertas, anotar, evaluar, todo_en_orden
from test_panel import HOY, _con_datos, dentro, personas  # noqa: F401

REAL = RAIZ / "config" / "concesionario.yaml"
AVISO = "Datos de la web de la marca hasta el {}: descarga en pausa"
D1, D2 = date(2026, 11, 10), date(2026, 11, 11)


def con_pausas(tmp_path, monkeypatch, pausas: dict[str, str]) -> None:
    """config/concesionario.yaml (sin sus pausas) con estas: {fuente: desde}; motivo «revisión jurídica»."""
    bloque = "fuentes_en_pausa:\n" + "".join(f"  {f}: {{desde: {d}, motivo: \"revisión jurídica\"}}\n"
                                              for f, d in pausas.items())
    fichero = tmp_path / "pausa.yaml"
    fichero.write_text(sin_pausas_texto(REAL.read_text(encoding="utf-8")) + "\n" + bloque, encoding="utf-8")
    monkeypatch.setenv("CDF_CONCESIONARIO", str(fichero))


def _tres(datos, dia):
    capturar(datos, "vo", dia.isoformat(), flota("VO", 5) + [coche("ESPPROPIO0001", dealer_id="ESP00710")])
    capturar(datos, "vn", dia.isoformat(), flota("VN", 4, fuente="vn"))
    capturar(datos, "vn_otros", dia.isoformat(), flota("EH", 3, fuente="vn_otros", cp="28045"))


# ================================================================== configuración

def test_la_configuracion_real_lleva_las_cuatro_en_pausa():
    pausas = fuentes_en_pausa(REAL)
    assert set(pausas) == {"vo", "vn", "vn_otros", "colores"}
    assert {(p.desde, p.motivo) for p in pausas.values()} == {(date(2026, 11, 25), "revisión jurídica")}
    conc = cargar_concesionario(REAL)
    assert conc.pausa("vo") == Pausa("vo", date(2026, 11, 25), "revisión jurídica") and conc.pausa("meta") is None


def test_sin_lista_ninguna_en_pausa():
    assert cargar_concesionario().pausas == () and sv.pausas() == {}


@pytest.mark.parametrize("bloque, error", [
    ("fuentes_en_pausa:\n  meta: {desde: 2026-11-25, motivo: x}\n", "no es una fuente que se pueda pausar"),
    ("fuentes_en_pausa:\n  vo: {desde: 2026-11-25}\n", "lleva «desde»"),
    ("fuentes_en_pausa:\n  vo: {desde: ayer, motivo: x}\n", "lleva «desde»"),
    ("fuentes_en_pausa: [vo]\n", "tiene que ser"),
])
def test_configuracion_mal_escrita(tmp_path, bloque, error):
    f = tmp_path / "c.yaml"
    f.write_text(sin_pausas_texto(REAL.read_text(encoding="utf-8")) + bloque, encoding="utf-8")
    with pytest.raises(ErrorConfiguracion, match=error):
        fuentes_en_pausa(f)


# ================================================================== carga y comprobación diaria

def test_comprobacion_con_fuentes_en_pausa(pg, datos, tmp_path, monkeypatch):
    _tres(datos, D1)
    sv.cargar(pg, dia=D1)
    _tres(datos, D2)                         # el scraper de VO aún dejó una pasada antes de parar el temporizador
    con_pausas(tmp_path, monkeypatch, {"vo": "2026-11-11", "colores": "2026-11-11"})
    enviados = []
    res = sv.comprobar(pg, base=datos, dia=D2, avisar=lambda texto: enviados.append(texto) or 1)
    assert res["vo"] == {"estado": "en_pausa", "desde": "2026-11-11", "motivo": "revisión jurídica"}
    assert res["vn"]["estado"] == res["vn_otros"]["estado"] == "ok"
    # VO no se carga mientras está en pausa; las demás, sí
    assert pg.execute("SELECT fuente, count(*) FROM scraper_runs GROUP BY 1 ORDER BY 1").fetchall() == \
        [("vn", 2), ("vn_otros", 2), ("vo", 1)]
    estados = dict(((f, t), e) for f, t, e in pg.execute(
        "SELECT DISTINCT ON (fuente, tipo) fuente, tipo, estado FROM fuentes_estado WHERE dia = %s "
        "ORDER BY fuente, tipo, id DESC", (D2,)))
    assert estados[("vo", "carga")] == estados[("vo", "comprobacion")] == "en_pausa"
    # El resumen diario: en pausa, con su motivo (va al grupo técnico), y no es un problema
    texto, = enviados
    assert texto.splitlines()[0] == "CDF 11/11 · todo correcto"
    assert "VO: en pausa desde el 11/11 (revisión jurídica)" in texto
    assert "Carga a Postgres: correcta" in texto
    # Las fichas de color tampoco se leen
    assert colores.ejecutar(pg, D2, datos) == {"estado": "en_pausa", "desde": "2026-11-11", "motivo": "revisión jurídica"}
    assert pg.execute("SELECT estado FROM fuentes_estado WHERE fuente = 'colores' ORDER BY id DESC LIMIT 1"
                      ).fetchone() == ("en_pausa",)
    # La orden de las 09:00 termina bien (el temporizador no se marca como fallido)
    assert sv.main(["comprobar", "--dia", D2.isoformat(), "--sin-telegram"]) == 0


def test_reactivar_carga_lo_pendiente(pg, datos, tmp_path, monkeypatch):
    con_pausas(tmp_path, monkeypatch, {"vo": "2026-11-10"})
    _tres(datos, D1)
    _tres(datos, D2)
    sv.cargar(pg, dia=D2)
    assert pg.execute("SELECT count(*) FROM scraper_runs WHERE fuente = 'vo'").fetchone() == (0,)
    monkeypatch.setenv("CDF_CONCESIONARIO", str(tmp_path / "otra.yaml"))
    (tmp_path / "otra.yaml").write_text(sin_pausas_texto(REAL.read_text(encoding="utf-8")), encoding="utf-8")
    res = sv.cargar(pg, dia=D2)
    assert res["vo"]["runs_nuevos"] == 2 and res["vo"]["dias"] == [D1.isoformat(), D2.isoformat()]


# ================================================================== motor de alertas

def test_fuente_caida_no_salta_con_la_fuente_en_pausa(pg, tmp_path, monkeypatch):
    todo_en_orden(pg)
    anotar(pg, "vo", "comprobacion", "sin_captura", ultima_captura="2026-11-18")   # anotada antes de la pausa
    anotar(pg, "vo", "carga", "error", error="No existe la base")
    anotar(pg, "vn", "comprobacion", "sin_captura", ultima_captura="2026-11-18")
    con_pausas(tmp_path, monkeypatch, {"vo": "2026-11-20", "colores": "2026-11-20"})
    evaluar(pg, cargar_concesionario(), "fuente_caida")
    assert [a["clave"] for a in alertas(pg)] == ["scraper|vn"]      # VN no está en pausa: sí avisa


def test_todas_en_pausa_no_falta_la_comprobacion(pg, tmp_path, monkeypatch):
    """Con los tres scrapers en pausa no hay comprobación que esperar: no salta «sin comprobación»."""
    anotar(pg, "panel_mercado", "precalculo", "ok")
    anotar(pg, "postgres", "copia", "ok", date(2026, 11, 19))
    evaluar(pg, cargar_concesionario(), "fuente_caida")
    assert [a["clave"] for a in alertas(pg)] == ["comprobacion"]            # sin pausa, sí salta
    con_pausas(tmp_path, monkeypatch, {"vo": "2026-11-20", "vn": "2026-11-20", "vn_otros": "2026-11-20"})
    evaluar(pg, cargar_concesionario(), "fuente_caida")
    assert alertas(pg) == []


# ================================================================== precálculo

def test_el_precalculo_sigue_con_los_ultimos_datos(pg, datos, tmp_path, monkeypatch):
    _tres(datos, D1)
    sv.cargar(pg, dia=D1)
    con_pausas(tmp_path, monkeypatch, {"vo": "2026-11-11", "vn": "2026-11-11", "vn_otros": "2026-11-11"})
    sv.comprobar(pg, base=datos, dia=D2, avisar=False)
    resumen = am.precalcular(pg, hasta=D2, conc=cargar_concesionario())
    assert resumen["filas_modelo"] > 0
    assert pg.execute("SELECT DISTINCT dia FROM panel_mercado_dia").fetchall() == [(D1,)]


# ================================================================== panel

def _app_en_pausa(panel_url, pg, datos, tmp_path, monkeypatch, pausas):
    conc = _con_datos(pg, datos)                   # capturas hasta HOY, cargadas antes de la pausa
    ejemplo.cargar(pg, HOY, conc)
    con_pausas(tmp_path, monkeypatch, pausas)
    return crear_app(dsn=panel_url, hoy=lambda: HOY, ejemplo_para="tecnico,direccion,ventas,marketing",
                     modo_ejemplo=True)


def test_panel_avisa_sin_motivo(pg, panel_url, datos, personas, tmp_path, monkeypatch):  # noqa: F811
    app = _app_en_pausa(panel_url, pg, datos, tmp_path, monkeypatch,
                        {f: "2026-11-25" for f in ("vo", "vn", "vn_otros", "colores")})
    aviso = AVISO.format(HOY.strftime("%d/%m"))     # la última captura completa, no el «desde» de la pausa
    paginas = {"/mercado": 1, "/mercado?zona=espana&tipo=VO": 1, "/mercado/modelo/T-Roc?tipo=VO&zona=espana": 1,
               "/ventas": 2, "/direccion": 1}       # Ventas: en «Precio de nuestros VO» y en «Salidas»
    vistas = {"direccion": ["/mercado", "/ventas", "/direccion"], "ventas": ["/mercado", "/ventas"],
              "marketing": ["/mercado"], "tecnico": list(paginas)}
    for rol, rutas in vistas.items():
        cl = dentro(app, rol)
        for ruta in [r for r in paginas if "/" + r.split("?")[0].split("/")[1] in rutas]:
            for htmx in (False, True):
                r = cl.get(ruta, headers={"HX-Request": "true"} if htmx else {})
                assert r.status_code == 200, (rol, ruta)
                avisos = re.findall(r'<p class="aviso-pausa" role="note" data-aviso="pausa">([^<]*)</p>', r.text)
                assert avisos == [aviso] * paginas[ruta], (rol, ruta, htmx)
                if rol != "tecnico":
                    assert "jurídic" not in r.text and "scraper" not in r.text.lower(), (rol, ruta)
                    assert "Falta la captura" not in r.text, (rol, ruta)
        # La píldora de la cabecera no da las fuentes por caídas
        assert "Descarga en pausa" in cl.get("/mercado").text


def test_estado_de_las_fuentes_con_el_motivo(pg, panel_url, datos, personas, tmp_path, monkeypatch):  # noqa: F811
    app = _app_en_pausa(panel_url, pg, datos, tmp_path, monkeypatch, {"vo": "2026-11-25", "colores": "2026-11-25"})
    sv.comprobar(pg, base=datos, dia=HOY, cargar_antes=False, avisar=False)
    html = dentro(app, "tecnico").get("/estado-fuentes").text
    filas = dict(re.findall(r'<tr data-fuente="([a-z_]+)"><th scope="row" class="izq">[^<]+</th>'
                            r'<td class="izq estado-pausa">en pausa</td><td>25/11/2026</td>'
                            r'<td class="izq detalle">([^<]+)</td></tr>', html))
    assert filas == {"vo": "revisión jurídica", "colores": "revisión jurídica"}
    assert '<td class="izq estado-pausa">en_pausa</td>' in html          # la anotación de la comprobación


def test_solo_las_fichas_de_color_en_pausa(pg, panel_url, datos, personas, tmp_path, monkeypatch):  # noqa: F811
    """Sin scrapers en pausa, la fecha del aviso es el «desde» de la pausa; la cabecera, la normal."""
    app = _app_en_pausa(panel_url, pg, datos, tmp_path, monkeypatch, {"colores": "2026-11-12"})
    html = dentro(app, "ventas").get("/mercado").text
    assert AVISO.format("12/11") in html and "Fuentes al día" in html


def test_sin_pausa_no_hay_aviso(pg, panel_url, datos, personas):  # noqa: F811
    _con_datos(pg, datos)
    app = crear_app(dsn=panel_url, hoy=lambda: HOY)
    cl = dentro(app, "tecnico")
    for ruta in ("/mercado", "/ventas", "/direccion", "/estado-fuentes"):
        assert 'data-aviso="pausa"' not in cl.get(ruta).text and "Fuentes en pausa" not in cl.get(ruta).text
    assert pm.pausa_web(pg, cargar_concesionario()) is None
