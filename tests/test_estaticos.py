"""Estáticos con huella y cabeceras de caché (panel/estaticos.py).

Fallo que se evita: tras un despliegue, el navegador seguía usando el CSS y el JS de la versión
anterior (guardados un día) y las vistas nuevas salían sin estilos ni gráficos.
"""

from __future__ import annotations

import os
import re
import shutil
from html.parser import HTMLParser
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from panel import usuarios
from panel.app import crear_app
from panel.estaticos import LARGA, REVALIDAR, Huellas

ESTATICOS = Path(__file__).resolve().parent.parent / "panel" / "static"
SERVIDOS = [p.relative_to(ESTATICOS).as_posix() for p in sorted(ESTATICOS.rglob("*"))
            if p.is_file() and p.suffix in (".css", ".js", ".woff2", ".svg")]
CLAVE = "clave-temporal-123"


@pytest.fixture
def copia(tmp_path) -> Path:
    destino = tmp_path / "static"
    shutil.copytree(ESTATICOS, destino)
    return destino


def test_hay_estaticos_de_cada_clase():
    for parte in ("css/", "js/", "fonts/", "vendor/htmx/", "vendor/echarts/", "img/"):
        assert any(s.startswith(parte) for s in SERVIDOS), parte


@pytest.mark.parametrize("relativa", SERVIDOS)
def test_la_url_cambia_si_cambia_el_contenido(copia, relativa):
    h = Huellas(copia)
    antes = {s: h.url(s) for s in SERVIDOS}
    assert antes[relativa] == f"/static/{relativa}?v={h.huella(relativa)}"
    ruta = copia / relativa
    ruta.write_bytes(ruta.read_bytes() + (b"/* cambio */" if ruta.suffix in (".css", ".js") else b"\0"))
    despues = {s: h.url(s) for s in SERVIDOS}
    assert despues[relativa] != antes[relativa]
    cambiadas = {s for s in SERVIDOS if despues[s] != antes[s]}
    # Una fuente cambia también la huella del CSS, que la cita; nada más cambia
    esperadas = {relativa} | ({"css/panel.css"} if relativa.startswith("fonts/") else set())
    assert cambiadas == esperadas


def test_la_huella_sale_del_contenido_no_de_la_fecha(copia):
    h = Huellas(copia)
    ruta = copia / "js/panel.js"
    antes = h.url("js/panel.js")
    st = ruta.stat()
    os.utime(ruta, ns=(st.st_atime_ns, st.st_mtime_ns + 10**12))
    assert Huellas(copia).url("js/panel.js") == antes == h.url("js/panel.js")


def test_el_css_cita_las_fuentes_con_su_huella(copia):
    h = Huellas(copia)
    css = h.css("css/panel.css").decode()
    urls = re.findall(r'url\("([^"]+)"\)', css)
    assert urls and all(re.fullmatch(r"/static/fonts/[\w/.-]+\.woff2\?v=[0-9a-f]{12}", u) for u in urls)
    assert f'url("{h.url("fonts/inter/inter-latin-wght-normal.woff2")}")' in css


def test_nada_fuera_de_static(copia):
    with pytest.raises(ValueError):
        Huellas(copia).url("../secreto.txt")


# ================================================================== a través del panel

class Recursos(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for clave in ("src", "href"):
            if a.get(clave, "").startswith("/static/"):
                self.urls.append(a[clave])


def _cliente(app):
    return TestClient(app, base_url="https://testserver", follow_redirects=False)


def _dentro(app, pg):
    usuarios.alta(pg, "tecnico", "Persona técnica", "tecnico", CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    cl = _cliente(app)
    csrf = re.search(r'name="csrf" value="([^"]+)"', cl.get("/acceso").text).group(1)
    assert cl.post("/acceso", data={"usuario": "tecnico", "clave": CLAVE, "csrf": csrf}).status_code == 303
    return cl


def _recursos(html: str) -> list[str]:
    p = Recursos()
    p.feed(html)
    return p.urls


def test_cabeceras_de_cache(panel_url, pg, copia):
    app = crear_app(dsn=panel_url, estaticos=copia)
    cl = _dentro(app, pg)
    paginas = {ruta: cl.get(ruta) for ruta in ("/acceso", "/mercado", "/direccion", "/ventas", "/marketing")}
    paginas["/acceso"] = _cliente(app).get("/acceso")
    paginas["htmx"] = cl.get("/marketing?tipo=VO", headers={"HX-Request": "true"})
    paginas["error"] = cl.get("/no-existe")
    for nombre, r in paginas.items():
        assert r.headers["Cache-Control"] == "no-store", nombre
    # Todos los estáticos de las páginas llevan huella y se guardan un año, immutable
    urls = {u for r in paginas.values() for u in _recursos(r.text)}
    css = cl.get(next(u for u in urls if "/css/" in u))
    urls |= set(re.findall(r'url\("([^"]+)"\)', css.text))
    assert {u.split("?")[0].removeprefix("/static/") for u in urls} >= {
        "css/panel.css", "js/panel.js", "vendor/htmx/htmx.min.js", "vendor/echarts/echarts.min.js",
        "fonts/inter/inter-latin-wght-normal.woff2", "fonts/space-grotesk/space-grotesk-latin-wght-normal.woff2",
        "img/icono.svg"}
    for u in urls:
        assert re.search(r"\?v=[0-9a-f]{12}$", u), u
        r = cl.get(u)
        assert r.status_code == 200 and r.headers["Cache-Control"] == LARGA == "public, max-age=31536000, immutable", u
    # Sin huella, o con una que ya no es la del fichero: el navegador revalida
    for u in ("/static/css/panel.css", "/static/js/panel.js", "/static/js/panel.js?v=000000000000"):
        assert cl.get(u).headers["Cache-Control"] == REVALIDAR == "no-cache", u
    # El CSS se revalida con su ETag
    r = cl.get("/static/css/panel.css")
    assert cl.get("/static/css/panel.css", headers={"If-None-Match": r.headers["ETag"]}).status_code == 304


def test_tras_un_despliegue_las_paginas_apuntan_al_estatico_nuevo(panel_url, pg, copia):
    app = crear_app(dsn=panel_url, estaticos=copia)
    cl = _dentro(app, pg)
    antes = _recursos(cl.get("/mercado").text)
    viejo_js = next(u for u in antes if "/js/panel.js" in u)
    (copia / "js/panel.js").write_text((copia / "js/panel.js").read_text() + "\n// versión nueva\n")
    despues = _recursos(cl.get("/mercado").text)
    nuevo_js = next(u for u in despues if "/js/panel.js" in u)
    assert nuevo_js != viejo_js and cl.get(nuevo_js).text.endswith("// versión nueva\n")
    assert cl.get(nuevo_js).headers["Cache-Control"] == LARGA
    # La URL vieja ya no se guarda: su contenido no es el de su huella
    assert cl.get(viejo_js).headers["Cache-Control"] == REVALIDAR
    assert set(antes) - {viejo_js} == set(despues) - {nuevo_js}
