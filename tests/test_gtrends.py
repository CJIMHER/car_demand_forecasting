"""Google Trends (ingesta/gtrends.py): comparaciones y enlaces desde data/competidores.csv, lectura de los
CSV en el formato real de Trends, comprobación de una carpeta (faltan, repetidas, desconocidos,
categoría, zona, frecuencia y semanas), subida con scp (simulada), carga sin duplicar, atención relativa
en Marketing y aviso de los 35 días. Sin red y sin acceder nunca a Google.
"""

from __future__ import annotations

import gzip
import json
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

from ingesta import gtrends as gt

DESCARGA = date(2026, 11, 20)             # viernes
ULTIMA_SEMANA = date(2026, 11, 15)        # domingo: la semana de la descarga, a medias
SEMANAS = [ULTIMA_SEMANA - timedelta(days=7 * i) for i in range(51, -1, -1)]   # 52 semanas

COMPETIDORES = """modelo_vw,segmento,consulta_vw,competidor,consulta_competidor,uso,nota
Golf,Compacto C,volkswagen golf,Seat León,seat leon,señal,
Golf,Compacto C,volkswagen golf,Ford Focus,ford focus,señal,
Golf,Compacto C,volkswagen golf,Opel Astra,opel astra,señal,
Golf,Compacto C,volkswagen golf,Toyota Corolla,toyota corolla,señal,
Golf,Compacto C,volkswagen golf,Kia Ceed,kia ceed,señal,
Golf,Compacto C,volkswagen golf,Peugeot 308,peugeot 308,señal,
Polo,Utilitario B,volkswagen polo,Seat Ibiza,seat ibiza,señal,
Polo,Utilitario B,volkswagen polo,Renault Clio,renault clio,señal,
"""


@pytest.fixture
def cfg():
    return gt.cargar_config()


@pytest.fixture
def comps(cfg, tmp_path):
    f = tmp_path / "competidores.csv"
    f.write_text(COMPETIDORES, encoding="utf-8")
    return gt.comparaciones(cfg, f)


def csv_trends(terminos, valores=None, *, categoria="Autos y vehículos", zona="España", frecuencia="Semana",
               semanas=SEMANAS, bom=True) -> bytes:
    """Un multiTimeline.csv como lo da Trends: categoría, línea vacía, cabecera y una fila por semana.
    `valores`: función (término, índice de semana) → texto («<1», «45»…)."""
    valores = valores or (lambda t, i: str(10 + (len(t) + i) % 80))
    lineas = [f"Categoría: {categoria}" if categoria else None, "",
              ",".join([frecuencia] + [f"{t}: ({zona})" for t in terminos])]
    for i, s in enumerate(semanas):
        lineas.append(",".join([s.isoformat()] + [valores(t, i) for t in terminos]))
    texto = "\n".join(l for l in lineas if l is not None) + "\n"
    return (("﻿" if bom else "") + texto).encode("utf-8")


def poner(carpeta: Path, nombre: str, datos: bytes) -> Path:
    carpeta.mkdir(exist_ok=True)
    (carpeta / nombre).write_bytes(datos)
    return carpeta / nombre


def por_id(comps):
    return {c.id: c for c in comps}


# ================================================================== comparaciones y enlaces

def test_comparaciones_de_cinco_terminos_con_el_modelo_como_ancla(comps):
    assert [(c.id, c.terminos) for c in comps] == [
        ("golf-1", ("volkswagen golf", "seat leon", "ford focus", "opel astra", "toyota corolla")),
        ("golf-2", ("volkswagen golf", "kia ceed", "peugeot 308")),
        ("polo-1", ("volkswagen polo", "seat ibiza", "renault clio"))]


def test_comparaciones_del_fichero_real_nunca_pasan_de_cinco(cfg):
    comps = gt.comparaciones(cfg)
    assert comps and all(2 <= len(c.terminos) <= 5 for c in comps)
    polo = [c for c in comps if c.modelo == "Polo"]
    # 10 competidores repartidos 4 + 4 + 2 y, aparte, los chinos de la comparación 4
    assert [c.id for c in polo] == ["polo-1", "polo-2", "polo-3", "polo-4"]
    assert all(c.terminos[0] == "volkswagen polo" for c in polo)
    assert sum(len(c.competidores) for c in polo) == 12
    assert len({c.id for c in comps}) == len(comps)


def test_url_con_zona_periodo_y_categoria_del_yaml(comps, cfg):
    assert por_id(comps)["polo-1"].url(cfg) == (
        "https://trends.google.com/trends/explore?cat=47&date=today%2012-m&geo=ES&hl=es"
        "&q=volkswagen%20polo,seat%20ibiza,renault%20clio")


def test_la_url_sale_del_yaml(comps, tmp_path):
    f = tmp_path / "gtrends.yaml"
    f.write_text("geo: PT\nzona: Portugal\nperiodo: today 5-y\ncategoria: 12\ncategoria_nombre: Otra\n"
                 "frecuencia: Semana\nsemanas: {minimo: 50, maximo: 54}\n")
    url = comps[2].url(gt.cargar_config(f))
    assert "cat=12&date=today%205-y&geo=PT" in url


def test_pagina_de_enlaces(comps, cfg):
    pagina = gt.html_enlaces(comps, cfg, DESCARGA)
    for c in comps:
        assert f'data-id="{c.id}"' in pagina and c.url(cfg).replace("&", "&amp;") in pagina
    assert pagina.count('type="checkbox"') == 3 and 'id="faltan"' in pagina and "Faltan " in pagina
    assert "localStorage" in pagina and "Automóviles y vehículos" in pagina
    assert "make gtrends-subir" in pagina


def test_el_codigo_nunca_accede_a_google():
    import re
    fuente = Path(gt.__file__).read_text(encoding="utf-8")
    importa = re.findall(r"^\s*(?:import|from)\s+([\w.]+)", fuente, re.M)
    prohibidos = ("pytrends", "selenium", "playwright", "requests", "urllib.request", "http", "httpx", "aiohttp",
                  "socket")
    assert not [m for m in importa if m.split(".")[0] in prohibidos or m in prohibidos], importa
    assert "urlopen" not in fuente and "webbrowser" not in fuente


# ================================================================== lectura del CSV

def test_lee_el_formato_real_de_trends():
    datos = csv_trends(["volkswagen polo", "seat ibiza"], lambda t, i: "<1" if (t, i) == ("seat ibiza", 3) else "42")
    s = gt.leer_csv(datos)
    assert s.categoria == "Autos y vehículos" and s.frecuencia == "Semana"
    assert s.terminos == ["volkswagen polo", "seat ibiza"] and s.zonas == ["España", "España"]
    assert len(s.semanas) == 52 and s.semanas[-1] == ULTIMA_SEMANA
    assert s.valores["seat ibiza"][3] == (0.5, True) and s.valores["seat ibiza"][4] == (42.0, False)


@pytest.mark.parametrize("datos", [b"modelo,unidades\nGolf,3\n", b"", b"\xff\xfe\x00raro"])
def test_un_csv_que_no_es_de_trends_da_error(datos):
    with pytest.raises(gt.ErrorGtrends):
        gt.leer_csv(datos)


# ================================================================== comprobación de la carpeta

def _todo(carpeta, comps):
    for i, c in enumerate(comps):
        poner(carpeta, "multiTimeline.csv" if i == 0 else f"multiTimeline ({i}).csv", csv_trends(c.terminos))


def test_todo_cuadra_aunque_los_nombres_no_digan_nada(tmp_path, comps, cfg):
    _todo(tmp_path / "d", comps)
    inf = gt.comprobar(tmp_path / "d", comps, cfg)
    assert inf.ok and set(inf.asignados) == {"golf-1", "golf-2", "polo-1"}
    assert inf.asignados["polo-1"].nombre == "multiTimeline (2).csv"
    assert "Todo cuadra." in inf.texto(3)


def test_terminos_en_otro_orden_y_con_mayusculas_casan(tmp_path, comps, cfg):
    poner(tmp_path / "d", "x.csv", csv_trends(["Renault Clio", "volkswagen polo", "SEAT IBIZA"]))
    assert "polo-1" in gt.comprobar(tmp_path / "d", comps, cfg).asignados


def test_faltan_repetidas_y_desconocidos(tmp_path, comps, cfg):
    d = tmp_path / "d"
    golf1 = csv_trends(por_id(comps)["golf-1"].terminos)
    poner(d, "multiTimeline.csv", golf1)
    poner(d, "multiTimeline (1).csv", golf1)                                       # repetida, mismo contenido
    poner(d, "multiTimeline (2).csv", csv_trends(["volkswagen polo", "seat ibiza", "renault clio"]))
    poner(d, "multiTimeline (3).csv", csv_trends(["volkswagen polo", "seat ibiza", "renault clio"],
                                                  lambda t, i: "7"))              # repetida, distinta
    poner(d, "multiTimeline (4).csv", csv_trends(["volkswagen polo", "kia picanto"]))   # no casa
    poner(d, "ventas.csv", b"modelo,unidades\nGolf,3\n")                            # no es de Trends
    poner(d, "notas.txt", b"no se mira")
    inf = gt.comprobar(d, comps, cfg)
    assert not inf.ok
    assert [c.id for c in inf.faltan] == ["golf-2"]
    assert inf.repetidas == {"golf-1": ["multiTimeline (1).csv", "multiTimeline.csv"],
                             "polo-1": ["multiTimeline (2).csv", "multiTimeline (3).csv"]}
    assert any("golf-1 está repetida con el mismo contenido" in a for a in inf.avisos)
    assert any("polo-1 está repetida con contenidos distintos" in e for e in inf.errores)
    desconocidos = dict(inf.desconocidos)
    assert "kia picanto" in desconocidos["multiTimeline (4).csv"]
    assert "no es un CSV de Google Trends" in desconocidos["ventas.csv"] and "notas.txt" not in desconocidos
    texto = inf.texto(3)
    assert "Faltan 1:" in texto and "golf-2 (volkswagen golf, kia ceed, peugeot 308)" in texto
    assert "No se sube nada" in texto


def test_categoria_distinta_se_rechaza_y_dice_que_comparacion_volver_a_descargar(tmp_path, comps, cfg):
    _todo(tmp_path / "d", comps)
    poner(tmp_path / "d", "multiTimeline (2).csv", csv_trends(por_id(comps)["polo-1"].terminos,
                                                             categoria="Todas las categorías"))
    inf = gt.comprobar(tmp_path / "d", comps, cfg)
    assert not inf.ok
    (error,) = inf.errores
    assert error.startswith("polo-1 (multiTimeline (2).csv): categoría «Todas las categorías» en lugar de "
                            "«Automóviles y vehículos» o «Autos y vehículos» (47). Vuelve a descargar polo-1: https://trends.google.com/trends/explore?cat=47")


def test_sin_linea_de_categoria_se_rechaza(tmp_path, comps, cfg):
    _todo(tmp_path / "d", comps)
    poner(tmp_path / "d", "multiTimeline.csv", csv_trends(por_id(comps)["golf-1"].terminos, categoria=None))
    (error,) = gt.comprobar(tmp_path / "d", comps, cfg).errores
    assert "golf-1" in error and "sin la línea «Categoría: Automóviles y vehículos»" in error


@pytest.mark.parametrize("categoria", ["Automóviles y vehículos", "Autos y vehículos", "AUTOMOVILES Y VEHICULOS",
                                       "47", "Automóviles y vehículos (47)"])
def test_categoria_por_codigo_o_por_cualquiera_de_sus_nombres(tmp_path, comps, cfg, categoria):
    """Trends ha pasado a escribir «Automóviles y vehículos» la que antes era «Autos y vehículos» (cat=47)."""
    _todo(tmp_path / "d", comps)
    poner(tmp_path / "d", "multiTimeline.csv", csv_trends(por_id(comps)["golf-1"].terminos, categoria=categoria))
    inf = gt.comprobar(tmp_path / "d", comps, cfg)
    assert inf.ok, inf.texto(len(comps))


def test_codigo_de_otra_categoria_se_rechaza(tmp_path, comps, cfg):
    _todo(tmp_path / "d", comps)
    poner(tmp_path / "d", "multiTimeline.csv", csv_trends(por_id(comps)["golf-1"].terminos, categoria="Otra (12)"))
    (error,) = gt.comprobar(tmp_path / "d", comps, cfg).errores
    assert "categoría «Otra (12)» en lugar de" in error


def test_los_nombres_de_la_categoria_salen_del_yaml(tmp_path):
    base = gt.ruta_config().read_text(encoding="utf-8")
    import yaml
    c = yaml.safe_load(base)
    c["categoria_nombres"] = ["Vehículos"]
    f = tmp_path / "gtrends.yaml"
    f.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    cfg = gt.cargar_config(f)
    assert cfg.es_categoria("vehiculos") and cfg.es_categoria("47")
    assert not cfg.es_categoria("Automóviles y vehículos")
    # Formato anterior, con un solo nombre, sigue valiendo
    del c["categoria_nombres"]
    c["categoria_nombre"] = "Autos y vehículos"
    f.write_text(yaml.safe_dump(c, allow_unicode=True), encoding="utf-8")
    assert gt.cargar_config(f).categoria_nombres == ("Autos y vehículos",)


@pytest.mark.parametrize("cambio, texto", [
    (dict(zona="Portugal"), "zona «Portugal» en lugar de «España»"),
    (dict(frecuencia="Mes"), "datos por «Mes» en lugar de por «Semana»"),
    (dict(semanas=SEMANAS[-30:]), "30 semanas en lugar de unas 52"),
    (dict(semanas=SEMANAS[:20] + SEMANAS[21:] + [ULTIMA_SEMANA + timedelta(days=7)]), "semanas no consecutivas"),
])
def test_zona_frecuencia_y_semanas(tmp_path, comps, cfg, cambio, texto):
    _todo(tmp_path / "d", comps)
    poner(tmp_path / "d", "multiTimeline.csv", csv_trends(por_id(comps)["golf-1"].terminos, **cambio))
    (error,) = gt.comprobar(tmp_path / "d", comps, cfg).errores
    assert error.startswith("golf-1") and texto in error and "Vuelve a descargar golf-1" in error


# ================================================================== subida (scp simulado)

class Ordenes:
    def __init__(self):
        self.ordenes, self.subido = [], {}

    def __call__(self, orden):
        self.ordenes.append(orden)
        if orden[0] == "scp":
            for f in orden[6:-1]:
                self.subido[Path(f).name] = Path(f).read_bytes()


def test_subida_con_manifiesto_y_nombres_por_comparacion(tmp_path, comps, cfg):
    _todo(tmp_path / "d", comps)
    inf = gt.comprobar(tmp_path / "d", comps, cfg)
    o = Ordenes()
    destino = gt.subir(inf, comps, cfg, DESCARGA, "192.0.2.10", ejecutar=o)
    assert destino == "/opt/cdf/entrada/gtrends/2026-11-20"
    ssh1, scp, ssh2 = o.ordenes
    assert ssh1[:5] == ["ssh", "-i", str(Path("~/.ssh/cdf_hetzner").expanduser()), "-o", "BatchMode=yes"]
    assert ssh1[5] == "cdf@192.0.2.10" and "test ! -e /opt/cdf/entrada/gtrends/2026-11-20" in ssh1[6]
    assert "mkdir -p /opt/cdf/entrada/gtrends/2026-11-20.subiendo" in ssh1[6]
    assert scp[-1] == "cdf@192.0.2.10:/opt/cdf/entrada/gtrends/2026-11-20.subiendo/"
    assert ssh2[-1] == "mv /opt/cdf/entrada/gtrends/2026-11-20.subiendo /opt/cdf/entrada/gtrends/2026-11-20"
    assert set(o.subido) == {"golf-1.csv", "golf-2.csv", "polo-1.csv", "manifiesto.json"}
    assert o.subido["polo-1.csv"] == (tmp_path / "d" / "multiTimeline (2).csv").read_bytes()   # el original
    man = json.loads(o.subido["manifiesto.json"])
    assert man["fecha_descarga"] == "2026-11-20" and man["categoria"] == 47 and man["geo"] == "ES"
    polo = next(c for c in man["comparaciones"] if c["id"] == "polo-1")
    assert polo["fichero_original"] == "multiTimeline (2).csv" and polo["semanas"] == 52
    assert polo["huella"] == inf.asignados["polo-1"].huella
    # La carpeta local queda intacta
    assert sorted(p.name for p in (tmp_path / "d").iterdir()) == ["multiTimeline (1).csv", "multiTimeline (2).csv",
                                                                   "multiTimeline.csv"]


def test_con_errores_no_se_sube_nada(tmp_path, comps, cfg, monkeypatch, capsys):
    monkeypatch.setattr(gt, "comparaciones", lambda cfg, fichero=None: comps)
    poner(tmp_path / "d", "multiTimeline.csv", csv_trends(comps[0].terminos))
    o = Ordenes()
    assert gt.orden_subir(tmp_path / "d", "192.0.2.10", DESCARGA, False, ejecutar=o) == 1
    assert o.ordenes == [] and "Faltan 2:" in capsys.readouterr().out


def test_solo_comprobar_no_sube(tmp_path, comps, monkeypatch):
    monkeypatch.setattr(gt, "comparaciones", lambda cfg, fichero=None: comps)
    _todo(tmp_path / "d", comps)
    o = Ordenes()
    assert gt.orden_subir(tmp_path / "d", None, DESCARGA, True, ejecutar=o) == 0 and o.ordenes == []


def test_ip_del_parametro_del_entorno_o_del_env_del_mac(tmp_path):
    env = tmp_path / ".env"
    env.write_text("OTRA=1\nCDF_SERVIDOR_IP='192.0.2.30'\n")
    assert gt.ip_servidor("192.0.2.10", {"CDF_SERVIDOR_IP": "192.0.2.20"}, env) == "192.0.2.10"
    assert gt.ip_servidor(None, {"CDF_SERVIDOR_IP": "192.0.2.20"}, env) == "192.0.2.20"
    assert gt.ip_servidor(None, {}, env) == "192.0.2.30"
    with pytest.raises(gt.ErrorGtrends, match="falta la IP"):
        gt.ip_servidor(None, {}, tmp_path / "no_existe")


# ================================================================== carga en el servidor

def entrega(tmp_path, comps, cfg, valores_por_comp=None, fecha=DESCARGA, categoria="Autos y vehículos") -> Path:
    """Una entrega como la deja la subida: <comparación>.csv y manifiesto.json."""
    d = tmp_path / "local"
    for c in comps:
        v = (valores_por_comp or {}).get(c.id)
        poner(d, f"{c.id}-descarga.csv", csv_trends(c.terminos, v, categoria=categoria))
    inf = gt.comprobar(d, comps, cfg)
    assert inf.ok, inf.texto(len(comps))
    raiz = tmp_path / "entrada"
    carpeta = raiz / fecha.isoformat()
    carpeta.mkdir(parents=True)
    for cid, f in inf.asignados.items():
        (carpeta / f"{cid}.csv").write_bytes(f.datos)
    (carpeta / "manifiesto.json").write_text(json.dumps(gt.manifiesto(inf, comps, cfg, fecha)), encoding="utf-8")
    return carpeta


class Bucket:
    def __init__(self):
        self.subidas = []

    def __call__(self, carpeta: Path, destino: str):
        self.subidas.append((destino, {p.name: gzip.decompress(p.read_bytes()) for p in carpeta.iterdir()}))


def test_carga_sin_duplicar_y_originales_al_bucket(pg, tmp_path, comps, cfg):
    menor = {"polo-1": lambda t, i: "<1" if t == "renault clio" and i < 3 else "20"}
    carpeta = entrega(tmp_path, comps, cfg, menor)
    b = Bucket()
    r = gt.cargar(pg, carpeta, cfg, b)
    n = (5 + 3 + 3) * 52
    assert r["filas"] == n and r["filas_nuevas"] == n and r["brutos"] == "gtrends/raw/2026-11-20"
    (destino, ficheros), = b.subidas
    assert set(ficheros) == {"golf-1.csv.gz", "golf-2.csv.gz", "polo-1.csv.gz", "manifiesto.json.gz"}
    assert ficheros["polo-1.csv.gz"] == (carpeta / "polo-1.csv").read_bytes()
    assert pg.execute("SELECT count(*) FROM gtrends_semana").fetchone()[0] == n
    v, m = pg.execute("""SELECT valor, menor_que_1 FROM gtrends_semana WHERE comparacion = 'polo-1'
                         AND termino = 'renault clio' AND semana = %s""", (SEMANAS[0],)).fetchone()
    assert float(v) == 0.5 and m is True
    assert pg.execute("SELECT count(*) FROM gtrends_semana WHERE es_modelo").fetchone()[0] == 3 * 52
    # Otra vez la misma entrega: ni filas nuevas ni otra subida
    r = gt.cargar(pg, carpeta, cfg, b)
    assert r["filas_nuevas"] == 0 and r["ya_cargada"] and len(b.subidas) == 1
    assert pg.execute("SELECT count(*) FROM gtrends_semana").fetchone()[0] == n
    assert pg.execute("SELECT count(*) FROM gtrends_entrega").fetchone()[0] == 1


def test_la_carga_es_de_solo_anadir(pg, tmp_path, comps, cfg):
    import psycopg
    gt.cargar(pg, entrega(tmp_path, comps, cfg), cfg, Bucket())
    with pytest.raises(psycopg.Error):
        pg.execute("UPDATE gtrends_semana SET valor = 1")


def test_carga_con_la_categoria_escrita_automoviles_y_vehiculos(pg, tmp_path, comps, cfg):
    carpeta = entrega(tmp_path, comps, cfg, categoria="Automóviles y vehículos")
    r = gt.cargar(pg, carpeta, cfg, Bucket())
    assert r["filas"] == (5 + 3 + 3) * 52 and not r["ya_cargada"]


def test_entrega_de_otro_codigo_de_categoria_no_se_carga(pg, tmp_path, comps, cfg):
    carpeta = entrega(tmp_path, comps, cfg)
    man = json.loads((carpeta / "manifiesto.json").read_text(encoding="utf-8"))
    man["categoria"] = 12
    (carpeta / "manifiesto.json").write_text(json.dumps(man), encoding="utf-8")
    b = Bucket()
    with pytest.raises(gt.ErrorGtrends, match="categoría 12"):
        gt.cargar(pg, carpeta, cfg, b)
    assert b.subidas == [] and pg.execute("SELECT count(*) FROM gtrends_semana").fetchone()[0] == 0


def test_un_fichero_cambiado_no_se_carga(pg, tmp_path, comps, cfg):
    carpeta = entrega(tmp_path, comps, cfg)
    (carpeta / "golf-2.csv").write_bytes(csv_trends(por_id(comps)["golf-2"].terminos, lambda t, i: "1"))
    b = Bucket()
    with pytest.raises(gt.ErrorGtrends, match="huella"):
        gt.cargar(pg, carpeta, cfg, b)
    assert b.subidas == [] and pg.execute("SELECT count(*) FROM gtrends_semana").fetchone()[0] == 0


def test_la_ultima_entrega_y_no_una_a_medias(tmp_path):
    raiz = tmp_path / "entrada"
    for n in ("2026-09-20", "2026-10-20", "2026-11-20.subiendo", "otra"):
        (raiz / n).mkdir(parents=True)
    assert gt.ultima_entrega(raiz).name == "2026-10-20"
    assert gt.ultima_entrega(tmp_path / "no_existe") is None


# ================================================================== atención relativa

def _cuotas():
    """Índices de semana: 51 es la de la descarga (a medias); 47–50, las 4 últimas completas; 43–46, las 4
    anteriores. Golf, en dos comparaciones: golf-1 del 20 % al 30 %, golf-2 del 50 % al 60 %: media de
    35 % a 45 %, +10 puntos. Polo: del 50 % al 25 %, −25 puntos. La semana a medias no cuenta."""
    def golf1(t, i):
        if i == 51:
            return "100" if t == "volkswagen golf" else "0"
        if t == "volkswagen golf":
            return "30" if i >= 47 else "20"
        if t == "seat leon":
            return ("35" if i >= 47 else "40") if i >= 43 else "<1"
        if t == "ford focus":
            return "35" if i >= 47 else "40"
        return "0"

    def golf2(t, i):
        if t == "volkswagen golf":
            return "60" if i >= 47 else "50"
        return "20" if i >= 47 else "25"

    def polo(t, i):
        if t == "volkswagen polo":
            return "25" if i >= 47 else "50"
        if i >= 47:
            return "40" if t == "seat ibiza" else "35"
        return "25"
    return {"golf-1": golf1, "golf-2": golf2, "polo-1": polo}


def test_cuota_en_su_comparacion_y_cambio_en_puntos(pg, tmp_path, comps, cfg):
    gt.cargar(pg, entrega(tmp_path, comps, cfg, _cuotas()), cfg, Bucket())
    filas = {m: (float(p), n, e, s, float(c), float(a), k) for m, p, n, e, s, c, a, k in pg.execute(
        """SELECT modelo, relativo_puntos, n_competidores, entrega, semana_fin, cuota_pct, cuota_anterior_pct,
                  n_comparaciones FROM panel_atencion_relativa WHERE origen = 'real' AND dia = %s""", (DESCARGA,))}
    assert filas["Golf"][0] == pytest.approx(10) and filas["Golf"][4:] == (pytest.approx(45), pytest.approx(35), 2)
    assert filas["Golf"][1] == 6 and filas["Golf"][2] == DESCARGA
    assert filas["Golf"][3] == ULTIMA_SEMANA - timedelta(days=7)          # la última semana completa
    assert filas["Polo"][0] == pytest.approx(-25) and filas["Polo"][4:6] == (pytest.approx(25), pytest.approx(50))


def test_marketing_muestra_la_atencion_real_con_la_fecha_de_descarga(pg, tmp_path, comps, cfg, panel_url):
    from panel import usuarios
    from test_contrato_panel import app_con, bloques
    from test_panel import dentro
    usuarios.alta(pg, "tecnico", "T", "tecnico", "clave-temporal-123")
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    gt.cargar(pg, entrega(tmp_path, comps, cfg, _cuotas()), cfg, Bucket())
    origen, html = bloques(dentro(app_con(panel_url), "tecnico").get("/marketing").text)["atencion"]
    assert origen == "real"
    assert "descarga de Google Trends del 20/11/2026" in html and "hasta el 14/11" in html
    # Para todos, sube, estable o baja; los puntos, en el detalle técnico
    assert ">Sube<" in html and ">Baja<" in html and "Detalle técnico" in html
    assert "+10,0 puntos" in html and "−25,0 puntos" in html
    assert html.index("Golf") < html.index("Polo")
    assert "Es una señal direccional" in html and "Cuota de búsquedas: 45,0 %, frente a 35,0 %" in html


# ================================================================== aviso de los 35 días

@pytest.mark.parametrize("dias, salta", [(None, "sin_entrega"), (36, "caducada"), (35, None), (3, None)])
def test_aviso_de_los_35_dias(pg, dias, salta):
    from alertas import motor
    from test_alertas import alertas, reglas
    hoy = date(2026, 11, 20)
    if dias is not None:
        pg.execute("INSERT INTO gtrends_entrega (fecha_descarga, huella_manifiesto, carpeta, manifiesto) "
                   "VALUES (%s, 'h', 'c', '{}')", (hoy - timedelta(days=dias),))
    motor.evaluar(pg, hoy, None, reglas("gtrends_pendiente"))
    a = alertas(pg, "gtrends_pendiente", dia=hoy)
    if salta is None:
        assert a == []
        return
    (x,) = a
    assert x["rol"] == "tecnico" and x["origen"] == "real" and x["texto"].startswith("Toca descargar Google Trends")
    assert x["cifras"]["caso"] == salta
    if salta == "caducada":
        assert "la última descarga es del 15/10, hace 36 días" in x["texto"]


# ================================================================== competidores chinos en comparaciones nuevas

RAIZ = Path(__file__).resolve().parent.parent


def _viejas(cfg, tmp_path):
    """Las comparaciones del fichero de antes de añadir los chinos (sin la columna «comparacion»)."""
    lineas = (RAIZ / "data" / "competidores.csv").read_text(encoding="utf-8").splitlines()
    cab = lineas[0].split(",")
    i = cab.index("comparacion")
    viejas = [",".join(cab[:i])] + [l.rsplit(",", 1)[0] for l in lineas[1:] if not l.endswith(",4")]
    f = tmp_path / "viejo.csv"
    f.write_text("\n".join(viejas) + "\n", encoding="utf-8")
    return gt.comparaciones(cfg, f)


def test_los_chinos_van_en_comparaciones_nuevas_y_las_28_no_cambian(cfg, tmp_path):
    nuevas = {(c.id, c.terminos) for c in gt.comparaciones(cfg)}
    viejas = {(c.id, c.terminos) for c in _viejas(cfg, tmp_path)}
    assert len(viejas) == 28 and viejas <= nuevas
    assert sorted(nuevas - viejas) == [
        ("polo-4", ("volkswagen polo", "mg3", "byd dolphin surf")),
        ("t-roc-4", ("volkswagen t-roc", "mg hs", "byd seal u")),
        ("tiguan-4", ("volkswagen tiguan", "mg hs", "byd seal u"))]


def test_enlaces_con_la_ultima_descarga_marca_pendientes_solo_las_nuevas(cfg, tmp_path, capsys):
    carpeta = tmp_path / "descarga"
    for i, c in enumerate(_viejas(cfg, tmp_path)):        # la descarga del mes pasado: las 28
        poner(carpeta, f"multiTimeline ({i}).csv", csv_trends(c.terminos))
    salida = tmp_path / "enlaces.html"
    assert gt.main(["enlaces", "--salida", str(salida), "--carpeta", str(carpeta)]) == 0
    assert "Pendientes: 3: polo-4, t-roc-4, tiguan-4" in capsys.readouterr().out
    pagina = salida.read_text(encoding="utf-8")
    marcadas = set(re.findall(r'<tr data-id="([^"]+)"[^>]* data-descargada>', pagina))
    todas = set(re.findall(r'<tr data-id="([^"]+)"', pagina))
    assert len(todas) == 31 and todas - marcadas == {"polo-4", "t-roc-4", "tiguan-4"}


def test_las_casillas_se_guardan_por_comparacion(comps, cfg):
    """La clave del navegador ya no depende de la lista entera: añadir comparaciones no desmarca las demás."""
    pagina = gt.html_enlaces(comps, cfg, DESCARGA)
    assert '"cdf-gtrends-hechas"' in pagina and 'data-firma="polo-1:volkswagen polo,seat ibiza,renault clio"' in pagina
    assert pagina == gt.html_enlaces(comps, cfg, DESCARGA)
    otra = gt.html_enlaces(comps + [gt.Comparacion("polo-9", "Polo", "volkswagen polo", ("mg3",))], cfg, DESCARGA)
    assert '"cdf-gtrends-hechas"' in otra


@pytest.mark.parametrize("filas, error", [
    ("Polo,B,volkswagen polo,MG3,mg3,señal,,1\n", "ya tiene la comparación 1"),
    ("Polo,B,volkswagen polo,MG3,mg3,señal,,x\n", "no es un número"),
    ("".join(f"Polo,B,volkswagen polo,R{i},r{i},señal,,5\n" for i in range(5)), "más de 4 competidores"),
])
def test_comparacion_fija_mal_puesta(cfg, tmp_path, filas, error):
    f = tmp_path / "c.csv"
    f.write_text("modelo_vw,segmento,consulta_vw,competidor,consulta_competidor,uso,nota,comparacion\n"
                 "Polo,B,volkswagen polo,Seat Ibiza,seat ibiza,señal,,\n" + filas, encoding="utf-8")
    with pytest.raises(gt.ErrorGtrends, match=error):
        gt.comparaciones(cfg, f)


def test_sin_la_columna_comparacion_todo_sigue_igual(comps):
    assert [c.id for c in comps] == ["golf-1", "golf-2", "polo-1"]

