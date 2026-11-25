"""Informe de las señales de intención de GA4 (analitica/ga4_intencion.py) con datos sintéticos de efecto conocido.

- La correlación cruzada encuentra el retardo plantado, y la parcial quita la que solo viene del gasto de Meta.
- La validación con origen móvil deja pasar una señal que adelanta los leads y no una de ruido.
- El informe de punta a punta: protocolo, datos, correlaciones, validación y resumen con PASA / NO PASA.
"""

from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pytest

from analitica import ga4_intencion as gi
from analitica import prevision as pv
from ingesta import ga4_series as gs

HOY = date(2026, 11, 25)
CFG_PREV = pv.cargar_config()


def _semanas(desde: date, n: int) -> list[date]:
    return [desde + timedelta(weeks=k) for k in range(n)]


def _ar(rng, n, phi=0.6, sd=0.4):
    x = np.zeros(n)
    for k in range(1, n):
        x[k] = phi * x[k - 1] + rng.normal(0, sd)
    return x


# ================================================================== correlación

def test_la_correlacion_encuentra_el_retardo_plantado():
    rng = np.random.default_rng(3)
    sem = _semanas(date(2024, 1, 1), 150)
    lx, g = _ar(rng, 150), np.exp(6 + rng.normal(0, 0.5, 150))
    senal = {s: float(np.exp(5 + lx[k])) for k, s in enumerate(sem)}
    leads = {s: float(round(np.exp(3 + 0.8 * (lx[k - 2] if k >= 2 else 0) + 0.3 * math.log(g[k])
                                   + rng.normal(0, 0.1)))) for k, s in enumerate(sem)}
    cs = gi.correlaciones(senal, leads, {s: float(g[k]) for k, s in enumerate(sem)}, 8, 26)
    assert [c.retardo for c in cs] == list(range(9))
    mejor = max(cs, key=lambda c: c.r_parcial)
    assert mejor.retardo == 2 and mejor.r_parcial > 0.8 and mejor.distinta()
    assert cs[2].n == 147                                   # 150 semanas, una diferencia y 2 de retardo


def test_la_parcial_quita_lo_que_solo_mueve_el_gasto_de_meta():
    rng = np.random.default_rng(3)
    sem = _semanas(date(2024, 1, 1), 150)
    g = np.exp(6 + rng.normal(0, 0.5, 150))
    pagado = {s: float(np.exp(2 + 0.9 * math.log(g[k]) + rng.normal(0, 0.1))) for k, s in enumerate(sem)}
    leads = {s: float(round(np.exp(1 + 0.7 * math.log(g[k]) + rng.normal(0, 0.1)))) for k, s in enumerate(sem)}
    c0 = gi.correlaciones(pagado, leads, {s: float(g[k]) for k, s in enumerate(sem)}, 0, 26)[0]
    assert c0.r > 0.9 and c0.distinta(parcial=False)        # tal cual: parece una señal estupenda
    assert not c0.distinta()                                 # sin Meta: no queda nada


def test_sin_gasto_conocido_no_hay_parcial_y_con_pocas_semanas_no_hay_correlacion():
    sem = _semanas(date(2024, 1, 1), 20)
    x = {s: float(k + 1) for k, s in enumerate(sem)}
    c = gi.correlaciones(x, dict(x), {}, 1, 10)
    assert c[0].r is not None and c[0].r_parcial is None and c[0].n_parcial == 0
    assert all(c.r is None for c in gi.correlaciones(x, dict(x), {}, 1, 26))


# ================================================================== validación

def _datos(rng, efecto=0.9, n=96):
    sem = _semanas(date(2025, 1, 6), n)
    lx = _ar(rng, n, 0.7, 0.5)
    gasto = np.exp(6 + 0.2 * np.cumsum(rng.normal(0, 0.1, n)))
    semanas = {}
    for k, s in enumerate(sem):
        ly = 1 + 0.5 * math.log1p(gasto[k]) + efecto * (lx[k - 1] if k else 0) + rng.normal(0, 0.15)
        semanas[s] = pv.Semana(gasto=float(gasto[k]), leads=int(round(math.expm1(ly))))
    datos = pv.Datos({("VN", None): pv.Grupo("VN", None, semanas)}, sem[-1] + timedelta(days=6), sem[-1], 14, 60)
    return datos, {s: float(np.exp(5 + lx[k])) for k, s in enumerate(sem)}, sem


def test_pasa_la_senal_que_adelanta_los_leads_y_no_el_ruido():
    rng = np.random.default_rng(3)
    datos, senal, sem = _datos(rng)
    v = gi.validar_senal(datos, CFG_PREV, "VN", senal)
    assert v.comparable and v.n == CFG_PREV.val_semanas * CFG_PREV.semanas
    minima = gi.protocolo().mejora_minima
    assert v.mejora("real") > 0.1 and v.mejora("previsto") > 0.1 and v.pasa(minima)
    ruido = {s: float(np.exp(5 + rng.normal(0, 0.5))) for s in sem}
    r = gi.validar_senal(datos, CFG_PREV, "VN", ruido)
    assert 0 < r.mejora("real") < minima and not r.pasa(minima)           # el ruido mejora algo por azar, no bastante
    assert gi.validar_senal(datos, CFG_PREV, "VO", senal) is None            # sin grupo de VO


def test_la_validacion_no_mira_al_futuro():
    """Una «señal» que es el propio lead de la semana NO puede ayudar: el modelo solo ve la de la semana anterior
    a cada previsión, y la mantiene en las 4 semanas previstas."""
    rng = np.random.default_rng(5)
    datos, _, sem = _datos(rng, efecto=0.0)
    trampa = {s: float(datos.grupos[("VN", None)].semanas[s].leads) for s in sem}
    v = gi.validar_senal(datos, CFG_PREV, "VN", trampa)
    assert v.mejora("real") < 0.2


def test_protocolo_fijado_en_la_configuracion(tmp_path):
    p = gi.protocolo()
    assert p.retardo_maximo == 8 and p.mejora_minima == pytest.approx(0.05)
    assert set(p.grupos_canal) == {"todos", "organico_directo", "pagado"} and p.grupos_canal["pagado"] == ("pagado",)
    assert p.senales["configurador"] == ("configurar", "color", "opcionales", "precio")
    texto = gs.ruta().read_text(encoding="utf-8")
    f = tmp_path / "i.yaml"
    f.write_text(texto.replace("    sesiones: [sesiones]\n", "    sesiones: [visitas]\n"))
    with pytest.raises(Exception, match="senales"):
        gi.protocolo(gs.cargar_config(f))


# ================================================================== de punta a punta

def _cargar(pg, rng):
    """GA4 (sesiones orgánicas de VN que adelantan una semana los leads de Meta), Meta y Walcu sintéticos."""
    dia = HOY - timedelta(days=1)
    sem = [s for s in _semanas(date(2025, 1, 6), 100) if s + timedelta(days=6) <= dia]
    n = len(sem)
    lx = _ar(rng, n, 0.9, 0.4)
    carga = datetime(2026, 11, 24, 8, tzinfo=timezone.utc)
    for k, s in enumerate(sem):
        run = f"r{k}"
        pg.execute("""INSERT INTO ga4_intencion_carga (run_id, cargado_at, origen, propiedad_id, semana, huella, filas,
                      version_paginas) VALUES (%s, %s, 'prueba', '1', %s, 'h', 2, 'v')""", (run, carga, s))
        pg.execute("""INSERT INTO ga4_intencion_semana (run_id, semana, tipo, canal, marca, sesiones, vistas_fichas)
                      VALUES (%s, %s, 'VN', 'organico', 'propia', %s, %s)""",
                   (run, s, round(math.exp(6 + lx[k])), round(math.exp(4 + lx[k]))))
        pg.execute("""INSERT INTO ga4_intencion_semana (run_id, semana, tipo, canal, marca, sesiones)
                      VALUES (%s, %s, 'VO', 'directo', 'sin_marca', %s)""", (run, s, int(rng.integers(50, 150))))
        gasto = float(np.exp(6 + rng.normal(0, 0.2)))
        ly = 1 + 0.5 * math.log1p(gasto) + 0.9 * (lx[k - 1] if k else 0) + rng.normal(0, 0.15)
        leads = max(0, round(math.expm1(ly)))
        pg.execute(
            """INSERT INTO panel_campana_semana (dia, origen, semana, campana_id, titulo, ambito, tipo_campana, modelo,
                   gasto_cents, walcu, cruce, leads, citas, ventas, leads_vn, leads_vo, citas_vn, citas_vo, ventas_vn,
                   ventas_vo, citas_completas, ventas_completas)
               VALUES (%s, 'real', %s, 'c-vn', 'VW [VN] General', 'VN', 'general', NULL, %s, true, 'nombre', %s, 0, 0,
                       %s, 0, 0, 0, 0, 0, true, true)""", (dia, s, round(gasto * 100), leads, leads))
        for origen, campana, tipo, n_leads in (("Facebook", "VW [VN] General", "VN", leads),
                                               ("Web", "(sin campaña)", "VN", 20 + k % 5),
                                               ("Web", "(sin campaña)", "VO", 10 + k % 3),
                                               ("Facebook", "AU [VN] Q3", "VN", 99)):      # otra marca: fuera
            pg.execute(
                """INSERT INTO walcu_campana_semana (fecha_carga, run_id, fichero_origen, huella_fichero, semana,
                       origen, campana, tipo, leads, leads_cercana, leads_fuera, leads_sin_zona, citas, ventas,
                       num_inexistente) VALUES (%s, 'w1', 'w.csv', 'h', %s, %s, %s, %s, %s, 0, 0, 0, 0, 0, 0)""",
                (carga, s, origen, campana, tipo, n_leads))
    pg.execute("""INSERT INTO walcu_ventas_carga (run_id, fuente, huella, desde, hasta)
                  VALUES ('v1', 'csv', 'h', %s, %s)""", (sem[0], sem[-1]))
    for k, s in enumerate(sem):
        pg.execute("INSERT INTO walcu_ventas_semana (run_id, semana, tipo, modelo, ventas) VALUES ('v1', %s, 'VN', "
                   "'T-Roc', %s)", (s, 3 + k % 4))
    return sem


def test_informe_de_punta_a_punta(pg):
    sem = _cargar(pg, np.random.default_rng(7))
    texto = gi.informe(pg, HOY)
    assert "Protocolo fijado de antemano" in texto and "retardos de 0 a 8 semanas" in texto
    assert f"GA4: {sem[0]:%d/%m/%Y} a {sem[-1]:%d/%m/%Y} ({len(sem)} semanas cargadas)" in texto
    # ventas: sin las 2 últimas semanas terminadas
    assert f"VN · ventas por fecha de venta: {sem[0]:%d/%m/%Y} a {sem[-3]:%d/%m/%Y}" in texto
    for t in ("VN", "VO"):
        for _, nombre in gi.OBJETIVOS:
            assert f"1. Correlación cruzada · {t} · {nombre}" in texto
    # la validación decide: las sesiones orgánicas de VN pasan; las de pago no tienen datos
    validacion = "2. Validación con origen móvil · "
    validacion_vn = texto.split(validacion + "VN")[1].split(validacion + "VO")[0]
    linea = next(x for x in validacion_vn.splitlines() if x.split()[:2] == ["sesiones", "organico_directo"])
    assert linea.endswith("PASA") and not linea.endswith("NO PASA")
    linea = next(x for x in validacion_vn.splitlines() if x.split()[:2] == ["sesiones", "pagado"])
    assert linea.endswith("NO PASA (sin datos de la señal)")
    assert "Sin semanas de Meta con leads de Walcu para este tipo" in texto.split("· VO · error absoluto")[1]
    resumen = texto.split("3. Resumen")[1]
    assert "sesiones (organico_directo)" in resumen.split("VN · PASAN")[1].split("VN · NO PASAN")[0]
    assert "VO · no se ha podido validar ninguna señal: sin semanas de Meta con leads de Walcu de VO." in resumen
    assert "configurador (todos)" in resumen.split("VN · Sin datos de la señal")[1].split("\n")[0]
    assert "sesiones_interaccion" not in texto.split("1. Correlación cruzada")[1].split("2. Validación")[0]
    # las correlaciones de las sesiones orgánicas de VN con los leads de Facebook adelantan una semana
    bloque = texto.split("1. Correlación cruzada · VN · leads de Walcu (Facebook)")[1].split("1. Correlación")[0]
    fila = bloque.splitlines()[bloque.splitlines().index(next(
        x for x in bloque.splitlines() if x.split()[:2] == ["sesiones", "organico_directo"])) + 1]
    assert fila.split()[:2] == ["sin", "Meta"] and fila.split()[3].endswith("*")      # k = 1


def test_informe_sin_series_cargadas(pg):
    texto = gi.informe(pg, HOY)
    assert "Sin semanas de GA4 cargadas: make ga4-series DESDE=… antes del informe." in texto
