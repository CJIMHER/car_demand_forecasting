"""Salidas de nuestro stock publicado como estimación de ventas (analitica/salidas.py, panel/salidas.py,
tabla panel_salidas_stock).

Las reglas una a una (salida normal, hueco de capturas, reaparición, republicación, provisionales, arranque),
que los competidores nunca generan salidas, el precálculo con capturas sintéticas escritas por el código real
del scraper, y los bloques de Dirección y Ventas. Sin red.
"""

from __future__ import annotations

import re
from datetime import date, timedelta

import pytest

from analitica import mercado as am
from analitica import salidas as sl
from ingesta import scrapers_vw as sv
from ingesta.concesionario import ErrorConfiguracion, cargar as cargar_concesionario
from panel import salidas as ps
from panel.app import crear_app
from sinteticos import RAIZ, capturar, coche, fallar
from test_contrato_panel import ETIQUETA, bloques, personas  # noqa: F401
from test_panel import HOY, dentro

REGLAS = sl.Reglas(dias_ausente=3, dias_reaparicion=14, republicacion_dias=7, republicacion_km=1000)
D0 = date(2026, 10, 1)
UN_DIA = timedelta(days=1)


def _d(n: int) -> date:
    return D0 + n * UN_DIA


def _dias(*tramos) -> list[date]:
    """_dias((0, 5), (9, 12)) → días 0..5 y 9..12."""
    return [_d(n) for a, b in tramos for n in range(a, b + 1)]


BUENOS = {"vo": _dias((0, 40))}


def _detectar(vistos, buenos=None, hasta=_d(40)):
    return sl.detectar({("vo", k): v for k, v in vistos.items()}, buenos or BUENOS, hasta, REGLAS)


# ================================================================== configuración

def test_umbrales_salen_de_la_configuracion():
    assert sl.cargar_reglas() == REGLAS                  # los de config/concesionario.yaml
    texto = (RAIZ / "analitica/salidas.py").read_text()
    for numero in ("3", "7", "14", "1000", "1_000", "1\\.000"):   # ningún umbral escrito en el código
        assert not re.search(rf"(?<![\w.]){numero}(?![\w.])", texto.split('"""', 2)[2]), numero
    assert REGLAS.margen_republicacion == 3              # ventana de 7 días centrada: 3 antes y 3 después


def test_configuracion_ausente_o_mal_escrita(tmp_path):
    f = tmp_path / "c.yaml"
    f.write_text("propio: {}\n")
    assert sl.cargar_reglas(f) is None                   # sin la sección, no se calcula
    f.write_text("salidas_stock: {dias_ausente: 3, dias_reaparicion: 14, republicacion_dias: 7}\n")
    with pytest.raises(ErrorConfiguracion, match="republicacion_km"):
        sl.cargar_reglas(f)
    f.write_text("salidas_stock: {dias_ausente: 0, dias_reaparicion: 14, republicacion_dias: 7, "
                 "republicacion_km: 1000}\n")
    with pytest.raises(ErrorConfiguracion, match="dias_ausente"):
        sl.cargar_reglas(f)


# ================================================================== reglas

def test_salida_normal_al_dia_siguiente_del_ultimo_visto():
    [s] = _detectar({"A": _dias((0, 10))})
    assert (s.ultimo, s.fecha, s.provisional) == (_d(10), _d(11), False)


def test_hace_falta_k_dias_buenos_sin_verlo():
    assert _detectar({"A": _dias((0, 10))}, hasta=_d(12)) == []        # dos días buenos: aún nada
    [s] = _detectar({"A": _dias((0, 10))}, hasta=_d(13))               # el tercero: salida (provisional)
    assert s.fecha == _d(11) and s.provisional


def test_hueco_de_capturas_no_es_salida():
    """Tres días sin verlo, pero dos sin captura buena: la ventana se alarga y vuelve a aparecer."""
    buenos = {"vo": _dias((0, 11), (14, 40))}                          # 12 y 13: captura fallida
    assert _detectar({"A": _dias((0, 10), (14, 40))}, buenos) == []
    # Con los mismos huecos, si no vuelve: la salida espera al tercer día bueno y es el día siguiente al último
    [s] = _detectar({"A": _dias((0, 10))}, buenos, hasta=_d(15))
    assert s.fecha == _d(11)
    assert _detectar({"A": _dias((0, 10))}, buenos, hasta=_d(14)) == []


def test_reaparicion_dentro_de_r_anula_la_salida():
    assert _detectar({"A": _dias((0, 10), (24, 40))}) == []            # sale el 11, vuelve el 24: 13 días
    [s] = _detectar({"A": _dias((0, 10), (25, 40))})                   # vuelve a los 14: la salida vale
    assert s.fecha == _d(11)


def test_salidas_de_los_ultimos_r_dias_son_provisionales():
    hasta = _d(40)
    [b, a] = _detectar({"A": _dias((0, 26)), "B": _dias((0, 25))}, hasta=hasta)
    assert (b.fecha, b.provisional) == (_d(26), False)                 # 14 días: definitiva
    assert (a.fecha, a.provisional) == (_d(27), True)                  # 13 días: aún puede reaparecer


def test_arranque_los_primeros_k_dias_no_cuentan():
    assert _detectar({"A": _dias((0, 0)), "B": _dias((0, 1))}) == []   # saldrían el 1 y el 2
    [s] = _detectar({"C": _dias((0, 2))})                              # sale el 3 = primer día + K
    assert s.fecha == _d(3)
    # El primer día es el de la fuente, no el del anuncio
    assert _detectar({"D": _dias((20, 20))})[0].fecha == _d(21)


def _coche(**kw) -> sl.Coche:
    base = dict(tipo="VO", modelo="Golf", acabado="Life", motor="TSI 150 CV · Gasolina", anio=2022, color="blanco",
                km=30_000)
    return sl.Coche(**{**base, **kw})


def test_republicacion_no_es_salida():
    salida = sl.Salida(("vo", "A"), _d(10), _d(11), False)
    for dia_nuevo, km, anulada in ((_d(14), 30_999, True), (_d(8), 29_001, True), (_d(15), 30_000, False),
                                   (_d(7), 30_000, False), (_d(12), 31_000, False)):
        coches = {("vo", "A"): _coche(), ("vo", "N"): _coche(km=km)}
        primero = {("vo", "A"): _d(0), ("vo", "N"): dia_nuevo}
        quedan = sl.sin_republicaciones([salida], coches, primero, REGLAS)
        assert (quedan == []) == anulada, (dia_nuevo, km)
    # Cualquier otro dato distinto: otro coche
    for cambio in ({"modelo": "Polo"}, {"acabado": "Style"}, {"motor": "TDI 150 CV · Diésel"}, {"anio": 2021},
                   {"color": "gris"}):
        coches = {("vo", "A"): _coche(), ("vo", "N"): _coche(**cambio)}
        assert sl.sin_republicaciones([salida], coches, {("vo", "A"): _d(0), ("vo", "N"): _d(12)}, REGLAS) == [salida]


def test_republicacion_en_vn_sin_anio_ni_km_y_un_anuncio_nuevo_anula_una_sola_salida():
    vn = dict(tipo="VN", anio=None, km=None)
    a, b = sl.Salida(("vn", "A"), _d(10), _d(11), False), sl.Salida(("vn", "B"), _d(11), _d(12), False)
    coches = {("vn", "A"): _coche(**vn), ("vn", "B"): _coche(**vn), ("vn", "N"): _coche(**vn)}
    primero = {("vn", "A"): _d(0), ("vn", "B"): _d(0), ("vn", "N"): _d(12)}
    # Dos coches iguales salen y entra uno: solo una es republicación (la más cercana, la de B)
    assert sl.sin_republicaciones([a, b], coches, primero, REGLAS) == [a]


# ================================================================== precálculo con capturas sintéticas

PERMISIVO = {"max_count_drop_fraction": 0.99, "max_missing_fraction": 0.99}
VERSION = "Volkswagen Golf Life 1.5 TSI 110 kW (150 CV)"


def _vo(vid, dealer, precio=2_000_000, **kw):
    return coche(vid, dealer_id=dealer, dealer=f"Concesionario {dealer}", precio=precio, version=VERSION,
                 color=kw.pop("color", "Blanco Puro"), **kw)


def test_precalculo_solo_nuestros_anuncios_y_cada_regla(pg, datos):
    conc = cargar_concesionario()
    propio = conc.dealer_ids[0]
    comp = [_vo(f"ESP1000000{i:02d}", "ESP99999", 1_800_000 + 100_000 * i) for i in range(5)]  # mediana 20.000 €
    # Cada anuncio nuestro: días en que se ve
    nuestros = {
        "ESP700000001": ((0, 5),),                 # salida normal el 6
        "ESP700000002": ((0, 11), (14, 30)),       # 12 y 13 sin captura buena: no sale
        "ESP700000003": ((0, 8), (16, 30)),        # sale el 9 y vuelve el 16: anulada
        "ESP700000004": ((0, 7),),                 # republicado el 9 como ESP700000005
        "ESP700000005": ((9, 30),),
        "ESP700000006": ((0, 24),),                # sale el 25: provisional
        "ESP700000007": ((0, 0),),                 # solo el primer día: no cuenta
        "ESP700000008": ((0, 28),),                # aún sin tres días buenos sin él
    }
    competidor = {"ESP100000099": ((0, 5),)}       # desaparece igual que el primero: nunca es salida
    for n in range(31):
        if n in (12, 13):
            fallar(datos, "vo", _d(n).isoformat())
            continue
        coches = [_vo(c["vehicle_id"], "ESP99999", c["business"]["price_retail_cents"]) for c in comp]
        for vid, tramos in nuestros.items():
            if any(a <= n <= b for a, b in tramos):
                km = 15_500 if vid == "ESP700000005" else 15_000
                coches.append(_vo(vid, propio, 2_100_000, km=km))
        for vid, tramos in competidor.items():
            if any(a <= n <= b for a, b in tramos):
                coches.append(_vo(vid, "ESP99999", 2_000_000))
        capturar(datos, "vo", _d(n).isoformat(), coches, PERMISIVO)
    sv.cargar(pg, dia=_d(30))
    resumen = am.precalcular(pg, hasta=_d(30), conc=conc)
    filas = {r[0]: r[1:] for r in pg.execute(
        """SELECT id_anuncio, dia, ultimo_dia, provisional, tipo, modelo, acabado, anio, color, precio_cents,
                  dias_publicado, dias_cota, nivel, n_comparables, mediana_cents, origen
           FROM panel_salidas_stock""")}
    assert resumen["filas_salidas"] == len(filas) == 2
    assert set(filas) == {"ESP700000001", "ESP700000006"}               # ni el competidor ni los demás
    a = filas["ESP700000001"]
    assert a[:4] == (_d(6), _d(5), False, "VO") and a[4:8] == ("Golf", "Life", 2024, "Blanco Puro")
    assert a[8:11] == (2_100_000, 5, True)                              # «al menos 5 días»: desde el primer día
    assert a[11:] == ("segmento", 6, 2_000_000, "real")    # mediana sin nuestros coches (el competidor, sí)
    assert filas["ESP700000006"][:3] == (_d(25), _d(24), True)
    # Recalcular sustituye; la ventana nocturna (últimos R + K días) no toca las de antes
    pg.execute("UPDATE panel_salidas_stock SET precio_cents = 1 WHERE id_anuncio = 'ESP700000001'")
    am.precalcular(pg, desde=_d(30), hasta=_d(30), conc=conc)
    assert pg.execute("SELECT precio_cents FROM panel_salidas_stock WHERE id_anuncio = 'ESP700000001'"
                      ).fetchone() == (1,)                              # el 6 queda fuera de la ventana (13–30)
    assert pg.execute("SELECT count(*) FROM panel_salidas_stock WHERE id_anuncio = 'ESP700000006'"
                      ).fetchone() == (1,)


def test_sin_configuracion_el_precalculo_sigue(pg, datos, tmp_path, monkeypatch):
    f = tmp_path / "concesionario.yaml"
    texto = (RAIZ / "config/concesionario.yaml").read_text()
    f.write_text(texto.split("salidas_stock:")[0] + "modelos:" + texto.split("\nmodelos:")[1])
    monkeypatch.setenv("CDF_CONCESIONARIO", str(f))
    conc = cargar_concesionario()
    capturar(datos, "vo", D0.isoformat(), [_vo("ESP700000001", conc.dealer_ids[0])], PERMISIVO)
    sv.cargar(pg, dia=D0)
    resumen = am.precalcular(pg, hasta=D0, conc=conc)
    assert resumen["filas_salidas"] == 0 and "salidas" in resumen and resumen["filas_modelo"] > 0


def test_los_competidores_nunca_generan_salidas_en_el_escenario(pg, datos):
    """En el escenario de Mercado desaparecen muchos anuncios de otros concesionarios: ninguno es una salida."""
    from test_panel import _con_datos
    conc = _con_datos(pg, datos)
    propios = {i for (i,) in pg.execute("SELECT DISTINCT id_anuncio FROM stock_externo_snapshot WHERE es_propio")}
    salidas = {i for (i,) in pg.execute("SELECT id_anuncio FROM panel_salidas_stock")}
    ajenos_ausentes = pg.execute(
        """SELECT count(DISTINCT id_anuncio) FROM stock_externo_snapshot s WHERE NOT es_propio
           AND snapshot_day < (SELECT max(snapshot_day) - 3 FROM stock_externo_snapshot)
           AND NOT EXISTS (SELECT 1 FROM stock_externo_snapshot t WHERE t.fuente = s.fuente
                           AND t.id_anuncio = s.id_anuncio AND t.snapshot_day > s.snapshot_day + 3)""").fetchone()[0]
    assert ajenos_ausentes > 0 and salidas and salidas <= propios, conc.provincia


# ================================================================== panel

def _app(panel_url):
    return crear_app(dsn=panel_url, hoy=lambda: HOY, modo_ejemplo=False)


def _salida(pg, id_anuncio, dia, tipo="VO", modelo="Golf", provisional=False, precio=2_100_000, mediana=2_000_000):
    pg.execute(
        """INSERT INTO panel_salidas_stock (dia, origen, tipo, fuente, id_anuncio, modelo, acabado, motor, anio, km,
               color, precio_cents, dias_publicado, dias_cota, ultimo_dia, provisional, nivel, n_comparables,
               mediana_cents)
           VALUES (%s, 'real', %s, %s, %s, %s, 'Life', 'TSI 150 CV · Gasolina', %s, %s, 'Blanco', %s, 40, false,
                   %s, %s, %s, 6, %s)""",
        (dia, tipo, "vo" if tipo == "VO" else "vn", id_anuncio, modelo, 2022 if tipo == "VO" else None,
         "Hasta 30.000 km" if tipo == "VO" else None, precio, dia - UN_DIA, provisional,
         None if mediana is None else "segmento", mediana))


def test_direccion_salidas_por_mes_tipo_y_modelo(pg, panel_url, personas):
    for i, (dia, prov) in enumerate(((date(2026, 10, 3), False), (date(2026, 10, 20), False),
                                     (date(2026, 11, 2), False), (date(2026, 11, 5), False),
                                     (date(2026, 11, 10), True))):
        _salida(pg, f"G{i}", dia, provisional=prov)
    _salida(pg, "T1", date(2026, 11, 12), tipo="VN", modelo="T-Roc", provisional=True)
    _salida(pg, "VIEJA", date(2026, 9, 30))                                # mes anterior al anterior: no cuenta
    tec = dentro(_app(panel_url), "tecnico")
    b = bloques(tec.get("/direccion").text)
    origen, kpi = b["ventas_kpi"]
    assert origen == "real" and "Salidas del mes" in kpi and 'class="kpi-valor">4<' in kpi
    assert "VN 1 · VO 3" in kpi and "2 provisionales" in kpi and "estimación de ventas" in kpi
    origen, t = b["salidas"]
    assert origen == "real" and "Salidas de nuestro stock (estimación de ventas)" in t and ETIQUETA not in t
    assert ">Octubre</th>" in t and ">Noviembre (hasta el 14/11)</th>" in t
    golf = re.search(r'<th scope="row" class="izq">Golf</th><td>(.*?)</td><td>(.*?)</td>', t).groups()
    assert golf == ("2", '2 <span class="provisional">+ 1 provisional</span>')
    troc = re.search(r'<th scope="row" class="izq">T-Roc</th><td>(.*?)</td><td>(.*?)</td>', t).groups()
    assert troc == ("0", '0 <span class="provisional">+ 1 provisional</span>')
    assert "Total</th><td>2</td>" in t
    assert ("Estimación: un coche que deja de estar publicado. Incluye coches traspasados a otras sedes o empresas "
            "del grupo. Se validará con las ventas del CRM.") in t
    # La predicción de ventas sigue pendiente
    assert "Pendiente de conectar: predicción de ventas" in b["ventas_mes"][1]
    # Por tipo
    b = bloques(tec.get("/direccion?tipo=VN").text)
    assert "Salidas VN del mes" in b["ventas_kpi"][1] and 'class="kpi-valor">1<' in b["ventas_kpi"][1]
    assert "Golf" not in b["salidas"][1] and "T-Roc" in b["salidas"][1] and "<tfoot>" not in b["salidas"][1]


def test_ventas_salidas_de_las_ultimas_4_semanas(pg, panel_url, personas):
    _salida(pg, "ESP700000001", date(2026, 11, 12), provisional=True, precio=2_100_000, mediana=2_000_000)
    _salida(pg, "ESP700000002", date(2026, 10, 20), precio=1_900_000, mediana=2_000_000)
    _salida(pg, "ESP700000003", date(2026, 10, 25), precio=1_900_000, mediana=None)
    _salida(pg, "ESP700000004", date(2026, 10, 16))                       # hace más de 4 semanas
    html = dentro(_app(panel_url), "tecnico").get("/ventas").text
    origen, t = bloques(html)["salidas"]
    assert origen == "real" and "Salidas de nuestro stock: últimas 4 semanas" in t
    filas = re.findall(r"<tr( class=\"provisional\")? data-tipo=\"VO\">(.*?)</tr>", t, re.S)
    ids = [re.search(r"ESP7\d+", f).group(0) for _, f in filas]
    assert ids == ["ESP700000001", "ESP700000003", "ESP700000002"]       # de la más reciente a la más antigua
    prov, primera = filas[0]
    assert prov and "12/11 · provisional" in primera and "21.000 €" in primera and "+5,0 %" in primera
    assert "Golf Life" in primera and "2022" in primera and "Blanco" in primera and ">40<" in primera
    assert "−5,0 %" in filas[2][1] and not filas[2][0]
    assert "Sin comparables suficientes" in filas[1][1] and "—" in filas[1][1]
    assert "3 coches, 1 provisional<" in t


def test_diferencia_frente_a_la_mediana():
    assert ps.diferencia(2_100_000, 2_000_000) == pytest.approx(5.0)
    assert ps.diferencia(None, 2_000_000) is None and ps.diferencia(2_000_000, None) is None
