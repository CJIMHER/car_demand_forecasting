"""Panel: acceso, sesiones, CSRF, permisos, cabeceras, usuario de Postgres y la vista Mercado.

Sin red: la aplicación se prueba con el TestClient de Starlette contra un Postgres local, conectada
como cdf_panel (el usuario de Postgres del panel), igual que en producción.
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from html.parser import HTMLParser

import psycopg
import pytest
from starlette.testclient import TestClient

from analitica import mercado as am
from ingesta import scrapers_vw as sv
from ingesta.concesionario import cargar as cargar_concesionario
from panel import seguridad as seg
from panel import usuarios
from panel.app import CABECERAS, MENSAJE_ACCESO, crear_app
from panel.permisos import ROLES
from sinteticos import RAIZ

CLAVE = "clave-temporal-123"
NUEVA = "clave-definitiva-456"
HOY = date(2026, 11, 14)
RUTAS_VISTAS = ["/direccion", "/ventas", "/marketing", "/mercado", "/estado-fuentes"]
# Matriz esperada, escrita a mano (no derivada de panel/permisos.py, para comprobarlo)
MATRIZ = {
    "direccion": {"/direccion", "/ventas", "/marketing", "/mercado"},
    "ventas": {"/ventas", "/mercado"},
    "marketing": {"/marketing", "/mercado"},
    "tecnico": set(RUTAS_VISTAS),
}


class ConIP:
    """Fija la IP de la conexión (el TestClient siempre usa «testclient»)."""

    def __init__(self, app, ip):
        self.app, self.ip = app, ip

    async def __call__(self, scope, receive, send):
        if scope["type"] in ("http", "websocket"):
            scope = dict(scope, client=(self.ip, 50000))
        await self.app(scope, receive, send)


def cliente(app, ip="203.0.113.5") -> TestClient:
    return TestClient(ConIP(app, ip), base_url="https://testserver", follow_redirects=False)


def token(cl, ruta="/acceso") -> str:
    r = cl.get(ruta)
    return re.search(r'name="csrf" value="([^"]+)"', r.text).group(1)


def entrar(cl, usuario, clave):
    return cl.post("/acceso", data={"usuario": usuario, "clave": clave, "csrf": token(cl)})


@pytest.fixture
def app(panel_url):
    return crear_app(dsn=panel_url, hoy=lambda: HOY)


@pytest.fixture
def personas(pg):
    """Un usuario por rol, ya con su contraseña definitiva."""
    for rol in ROLES:
        usuarios.alta(pg, rol, f"Persona {rol}", rol, CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    return list(ROLES)


def dentro(app, usuario, ip="203.0.113.5") -> TestClient:
    cl = cliente(app, ip)
    assert entrar(cl, usuario, CLAVE).status_code == 303
    return cl


# ================================================================== acceso

def test_acceso_correcto_con_cambio_obligatorio(app, pg):
    usuarios.alta(pg, "ana", "Ana", "ventas", CLAVE)
    cl = cliente(app)
    r = entrar(cl, "ana", CLAVE)
    assert r.status_code == 303 and r.headers["location"] == "/"
    for ruta in ("/", "/mercado", "/ventas"):
        assert cl.get(ruta).headers["location"] == "/cambiar-clave"
    assert "contraseña temporal" in cl.get("/cambiar-clave").text

    def cambiar(actual, nueva, repetida=None):
        return cl.post("/cambiar-clave", data={"actual": actual, "nueva": nueva, "repetida": repetida or nueva,
                                               "csrf": token(cl, "/cambiar-clave")})

    assert cambiar("otra-cosa-distinta", NUEVA).status_code == 401
    assert cambiar(CLAVE, "corta").status_code == 400
    assert cambiar(CLAVE, NUEVA, NUEVA + "x").status_code == 400
    assert cambiar(CLAVE, CLAVE).status_code == 400            # tiene que ser distinta
    r = cambiar(CLAVE, NUEVA)
    assert r.status_code == 303
    assert cl.get("/").headers["location"] == "/mercado"
    assert cl.get("/mercado").status_code == 200
    assert pg.execute("SELECT cambiar_clave FROM panel_usuarios WHERE usuario = 'ana'").fetchone() == (False,)
    # La temporal ya no vale; la nueva sí
    assert entrar(cliente(app), "ana", CLAVE).status_code == 401
    assert entrar(cliente(app), "ana", NUEVA).status_code == 303


def test_acceso_incorrecto_mismo_mensaje_exista_o_no(app, pg, personas):
    r1 = entrar(cliente(app, "198.51.100.1"), "ventas", "no-es-la-clave")
    r2 = entrar(cliente(app, "198.51.100.2"), "no-existe", "no-es-la-clave")
    assert r1.status_code == r2.status_code == 401
    assert MENSAJE_ACCESO in r1.text and MENSAJE_ACCESO in r2.text
    sin_token = lambda t: re.sub(r'name="csrf" value="[^"]+"', "", t)  # noqa: E731
    assert sin_token(r1.text) == sin_token(r2.text)                # la misma página, carácter a carácter
    filas = pg.execute("SELECT usuario, resultado, ip FROM panel_accesos ORDER BY id").fetchall()
    # Del usuario inexistente no se guarda lo que se escribió
    assert filas == [("ventas", "fallo", "198.51.100.1"), (None, "fallo", "198.51.100.2")]


def test_bloqueo_por_usuario_15_minutos(app, pg, personas):
    for i in range(5):  # cinco fallos desde IP distintas
        assert entrar(cliente(app, f"192.0.2.{i}"), "ventas", "mal").status_code == 401
    r = entrar(cliente(app, "192.0.2.50"), "ventas", CLAVE)     # la buena, pero bloqueado
    assert r.status_code == 401 and MENSAJE_ACCESO in r.text
    assert entrar(cliente(app, "192.0.2.51"), "marketing", CLAVE).status_code == 303   # otros usuarios, no
    pg.execute("UPDATE panel_accesos SET registrado_at = registrado_at - interval '14 minutes'")
    assert entrar(cliente(app, "192.0.2.52"), "ventas", CLAVE).status_code == 401
    pg.execute("UPDATE panel_accesos SET registrado_at = registrado_at - interval '2 minutes'")
    assert entrar(cliente(app, "192.0.2.53"), "ventas", CLAVE).status_code == 303
    assert "bloqueado" in {r[0] for r in pg.execute("SELECT resultado FROM panel_accesos")}


def test_bloqueo_por_ip(app, pg, personas):
    cl = cliente(app, "192.0.2.77")
    for i in range(5):
        entrar(cl, f"inventado{i}", "mal")
    assert entrar(cliente(app, "192.0.2.77"), "ventas", CLAVE).status_code == 401
    assert entrar(cliente(app, "192.0.2.78"), "ventas", CLAVE).status_code == 303


def test_caducidad_por_inactividad(panel_url, pg, personas):
    app = crear_app(dsn=panel_url, hoy=lambda: HOY, sesion_dias=3)
    cl = dentro(app, "ventas")
    galleta = next(c for c in cl.get("/mercado").headers.get_list("set-cookie") if c.startswith("__Host-cdf_sesion="))
    assert "max-age=259200" in galleta.lower()
    pg.execute("UPDATE panel_sesiones SET ultimo_uso_at = now() - interval '2 days'")
    assert cl.get("/mercado").status_code == 200                # cada página renueva el último uso
    pg.execute("UPDATE panel_sesiones SET ultimo_uso_at = now() - interval '3 days 1 minute'")
    assert cl.get("/mercado").headers["location"] == "/acceso"


def test_caducidad_por_defecto_30_dias(app, pg, personas):
    cl = dentro(app, "ventas")
    pg.execute("UPDATE panel_sesiones SET ultimo_uso_at = now() - interval '29 days'")
    assert cl.get("/mercado").status_code == 200
    pg.execute("UPDATE panel_sesiones SET ultimo_uso_at = now() - interval '31 days'")
    assert cl.get("/mercado").status_code == 303


def test_cookies_solo_del_host(app, personas):
    cl = cliente(app)
    r = entrar(cl, "ventas", CLAVE)
    galletas = r.headers.get_list("set-cookie")
    sesion = next(c for c in galletas if c.startswith("__Host-cdf_sesion="))
    atributos = {a.strip().split("=")[0].lower() for a in sesion.split(";")[1:]}
    assert {"secure", "httponly", "samesite", "path", "max-age"} <= atributos
    assert "domain" not in atributos and "samesite=lax" in sesion.lower() and "max-age=2592000" in sesion.lower()
    assert all("domain" not in c.lower() for c in galletas)


def test_cierre_de_sesion(app, pg, personas):
    cl = dentro(app, "ventas")
    galleta = cl.cookies.get("__Host-cdf_sesion")
    r = cl.post("/salir", data={"csrf": token(cl, "/mercado")})
    assert r.status_code == 303 and r.headers["location"] == "/acceso"
    assert pg.execute("SELECT count(*) FROM panel_sesiones").fetchone() == (0,)
    otro = cliente(app)
    otro.cookies.set("__Host-cdf_sesion", galleta, domain="testserver")
    assert otro.get("/mercado").headers["location"] == "/acceso"
    assert ("ventas", "salida") in pg.execute("SELECT usuario, resultado FROM panel_accesos").fetchall()


def test_csrf_en_todo_post(app, pg, personas):
    cl = cliente(app)
    token(cl)
    assert cl.post("/acceso", data={"usuario": "ventas", "clave": CLAVE}).status_code == 403
    assert cl.post("/acceso", data={"usuario": "ventas", "clave": CLAVE, "csrf": "falso"}).status_code == 403
    assert cl.post("/acceso", data={"usuario": "ventas", "clave": CLAVE, "csrf": token(cl)},
                   headers={"Origin": "https://otro.example"}).status_code == 403
    assert pg.execute("SELECT count(*) FROM panel_sesiones").fetchone() == (0,)
    # Origin del propio sitio (lo que manda un navegador con Referrer-Policy same-origin): vale.
    # Con no-referrer, Chrome manda «Origin: null» en los formularios y el acceso fallaría.
    assert cl.post("/acceso", data={"usuario": "ventas", "clave": CLAVE, "csrf": token(cl)},
                   headers={"Origin": "https://testserver"}).status_code == 303
    assert cliente(app).post("/acceso", data={"usuario": "ventas", "clave": CLAVE, "csrf": "x"},
                             headers={"Origin": "null"}).status_code == 403
    assert CABECERAS["Referrer-Policy"] == "same-origin"
    pg.execute("DELETE FROM panel_sesiones")
    cl = dentro(app, "ventas")
    assert cl.post("/salir", data={"csrf": "falso"}).status_code == 403
    assert cl.post("/cambiar-clave", data={"actual": CLAVE, "nueva": NUEVA, "repetida": NUEVA}).status_code == 403
    assert cl.get("/mercado").status_code == 200                # la sesión sigue: nada se ha hecho
    assert pg.execute("SELECT count(*) FROM panel_sesiones").fetchone() == (1,)


def test_proxy_solo_se_fia_de_caddy(panel_url, pg, personas):
    app = crear_app(dsn=panel_url, hoy=lambda: HOY, proxy="10.0.0.9")
    entrar(TestClient(ConIP(app, "10.0.0.9"), base_url="https://testserver", follow_redirects=False,
                      headers={"X-Forwarded-For": "1.1.1.1, 198.51.100.7"}), "ventas", "mal")
    entrar(TestClient(ConIP(app, "10.0.0.10"), base_url="https://testserver", follow_redirects=False,
                      headers={"X-Forwarded-For": "198.51.100.8"}), "ventas", "mal")
    ips = [r[0] for r in pg.execute("SELECT ip FROM panel_accesos ORDER BY id")]
    assert ips == ["198.51.100.7", "10.0.0.10"]


# ================================================================== permisos

@pytest.mark.parametrize("rol", list(ROLES))
def test_matriz_de_permisos(app, personas, rol):
    cl = dentro(app, rol)
    for ruta in RUTAS_VISTAS:
        esperado = 200 if ruta in MATRIZ[rol] else 403
        assert cl.get(ruta).status_code == esperado, (rol, ruta)
    assert cl.get("/").headers["location"] == "/mercado"
    nav = cl.get("/mercado").text
    # Navegación: solo lo permitido; Dirección, Ventas y Marketing desactivadas con su texto
    assert ('href="/estado-fuentes"' in nav) == (rol == "tecnico")
    for clave, titulo in (("/direccion", "Dirección"), ("/ventas", "Ventas"), ("/marketing", "Marketing")):
        assert f'href="{clave}"' not in nav
        assert (f'{titulo}<span class="oculto"> (Se activa al conectar sus datos)' in nav) == (clave in MATRIZ[rol])


def test_sin_sesion_todo_lleva_al_acceso(app):
    cl = cliente(app)
    for ruta in ["/", *RUTAS_VISTAS, "/cambiar-clave", "/salir", "/no-existe", "/admin", "/mercado?zona=x"]:
        r = cl.get(ruta)
        assert r.status_code == 303 and r.headers["location"] == "/acceso", ruta
    for ruta in ("/salir", "/cambiar-clave", "/mercado"):
        r = cl.post(ruta, data={})
        assert r.status_code == 303 and r.headers["location"] == "/acceso", ruta
    r = cl.get("/mercado", headers={"HX-Request": "true"})
    assert r.headers["HX-Redirect"] == "/acceso"
    # Lo público
    assert cl.get("/acceso").status_code == 200
    assert cl.get("/salud").text == "ok"
    assert cl.get("/robots.txt").text == "User-agent: *\nDisallow: /\n"
    for estatico in ("/static/css/panel.css", "/static/js/panel.js", "/static/vendor/htmx/htmx.min.js",
                     "/static/vendor/echarts/echarts.min.js", "/static/fonts/inter/inter-latin-wght-normal.woff2"):
        assert cl.get(estatico).status_code == 200, estatico


# ================================================================== contraseñas fuera de logs y respuestas

def test_clave_y_hash_no_aparecen_en_logs_ni_respuestas(app, pg, panel_url, monkeypatch, caplog, capsys):
    caplog.set_level(logging.DEBUG)
    monkeypatch.setenv("PANEL_DATABASE_URL", panel_url)
    respuestas = []
    entradas = iter([CLAVE, CLAVE])
    assert usuarios.main(["alta", "luis", "--nombre", "Luis", "--rol", "ventas"], leer=lambda _: next(entradas)) == 0
    cl = cliente(app)
    respuestas.append(entrar(cl, "luis", "equivocada-del-todo"))
    respuestas.append(entrar(cl, "luis", CLAVE))
    respuestas.append(cl.get("/cambiar-clave"))
    respuestas.append(cl.post("/cambiar-clave", data={"actual": CLAVE, "nueva": NUEVA, "repetida": NUEVA,
                                                      "csrf": token(cl, "/cambiar-clave")}))
    respuestas.append(cl.get("/mercado"))
    hash_ = pg.execute("SELECT clave_hash FROM panel_usuarios WHERE usuario = 'luis'").fetchone()[0]
    salida = capsys.readouterr()
    todo = caplog.text + salida.out + salida.err + "".join(r.text + str(r.headers) for r in respuestas)
    for secreto in (CLAVE, NUEVA, hash_, hash_.split("$")[-1]):
        assert secreto not in todo
    accesos = str(pg.execute("SELECT * FROM panel_accesos").fetchall())
    assert CLAVE not in accesos and NUEVA not in accesos and "equivocada" not in accesos


def test_hash_argon2id(pg):
    usuarios.alta(pg, "eva", "Eva", "marketing", CLAVE)
    h = pg.execute("SELECT clave_hash FROM panel_usuarios").fetchone()[0]
    assert h.startswith("$argon2id$") and seg.verificar(h, CLAVE)


# ================================================================== órdenes de usuarios

def test_ordenes_de_usuarios(pg, panel_url, monkeypatch, capsys):
    monkeypatch.setenv("PANEL_DATABASE_URL", panel_url)   # con el usuario del panel, como en producción

    def teclado(*claves):
        it = iter(claves)
        return lambda _: next(it)

    assert usuarios.main(["alta", "Mar", "--nombre", "Mar", "--rol", "ventas"], leer=teclado("corta", "corta")) == 1
    assert "12 caracteres" in capsys.readouterr().err
    assert usuarios.main(["alta", "mar", "--nombre", "Mar", "--rol", "ventas"], leer=teclado(CLAVE, CLAVE + "x")) == 1
    assert usuarios.main(["alta", "mar", "--nombre", "Mar", "--rol", "ventas"], leer=teclado(CLAVE, CLAVE)) == 0
    assert usuarios.main(["alta", "mar", "--nombre", "Mar", "--rol", "ventas"], leer=teclado(CLAVE, CLAVE)) == 1
    assert usuarios.main(["rol", "mar", "marketing"]) == 0
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    pg.execute("INSERT INTO panel_sesiones (id_hash, usuario) VALUES ('x', 'mar')")
    assert usuarios.main(["restablecer", "mar"], leer=teclado(NUEVA, NUEVA)) == 0
    assert pg.execute("SELECT rol, cambiar_clave FROM panel_usuarios").fetchone() == ("marketing", True)
    assert pg.execute("SELECT count(*) FROM panel_sesiones").fetchone() == (0,)
    assert usuarios.main(["lista"]) == 0
    assert usuarios.main(["baja", "mar"]) == 0
    assert pg.execute("SELECT count(*) FROM panel_usuarios").fetchone() == (0,)
    salida = capsys.readouterr()
    assert CLAVE not in salida.out + salida.err and NUEVA not in salida.out + salida.err


def test_la_clave_nunca_es_un_argumento(monkeypatch):
    import argparse
    with pytest.raises(SystemExit):
        usuarios.main(["alta", "x1", "--nombre", "X", "--rol", "ventas", "--clave", CLAVE])
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(usuarios.ErrorUsuarios, match="terminal"):
        usuarios.pedir_clave()
    assert argparse  # noqa


# ================================================================== usuario de Postgres del panel

HECHOS = ["stock_externo_snapshot", "scraper_runs", "eventos_externos", "anuncios_externos", "alembic_version"]


def test_usuario_del_panel_no_toca_las_tablas_de_hechos(panel_url):
    with psycopg.connect(panel_url, autocommit=True) as c:
        assert c.execute("SELECT current_user").fetchone() == ("cdf_panel",)
        for tabla in HECHOS:
            for sql in (f"SELECT 1 FROM {tabla} LIMIT 1", f"DELETE FROM {tabla}"):
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    c.execute(sql)
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO fuentes_estado (fuente, tipo, dia, estado) VALUES ('x', 'carga', now(), 'ok')")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("INSERT INTO scraper_runs (fuente, run_id, tipo_stock, snapshot_day, started_at, observed_count, "
                      "scope) VALUES ('vo', 'x', 'VO', now(), now(), 1, 'x')")
        for tabla in ("panel_mercado_dia", "panel_mercado_modelo", "panel_mercado_capturas"):
            c.execute(f"SELECT count(*) FROM {tabla}")
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                c.execute(f"DELETE FROM {tabla}")
        c.execute("SELECT count(*) FROM fuentes_estado")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            c.execute("CREATE TABLE intrusa (x int)")
        # Sus tablas sí
        c.execute("INSERT INTO panel_usuarios (usuario, nombre, rol, clave_hash) VALUES ('t1', 'T', 'ventas', '$argon2id$x')")
        c.execute("INSERT INTO panel_accesos (usuario, resultado, ip) VALUES ('t1', 'ok', '192.0.2.1')")
        c.execute("DELETE FROM panel_usuarios WHERE usuario = 't1'")


def test_contrasena_del_rol_como_verificador_scram():
    from panel import rol_bd
    v = rol_bd.verificador_scram("una-clave-larga-de-prueba-123", sal=b"0123456789abcdef")
    assert v.startswith("SCRAM-SHA-256$4096:") and "una-clave" not in v
    with pytest.raises(ValueError):
        rol_bd.activar(None, "corta")


# ================================================================== cabeceras, HTML propio y htmx

class Enlaces(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls, self.scripts_en_linea, self.estilos, self.eventos = [], 0, 0, 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        for clave in ("src", "href", "action", "formaction", "data", "poster"):
            if a.get(clave):
                self.urls.append(a[clave])
        for clave in ("hx-get", "hx-post"):
            if a.get(clave):
                self.urls.append(a[clave])
        if tag == "script" and not a.get("src"):
            self.scripts_en_linea += 1
        if tag == "style" or "style" in a:
            self.estilos += 1
        self.eventos += sum(1 for k in a if k.startswith("on") or k.startswith("hx-on"))


def _con_datos(pg, datos, conc=None, hasta=HOY):
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent))
    from escenario_mercado import escenario
    conc = conc or cargar_concesionario()
    escenario(datos, hasta, conc.provincia_codigo, conc.provincias_comunidad, conc.dealer_ids[0], dias=20)
    sv.cargar(pg, dia=hasta)
    am.precalcular(pg, hasta=hasta, conc=conc)
    return conc


def test_cabeceras_de_seguridad_y_nada_de_terceros(app, pg, datos, personas):
    _con_datos(pg, datos)
    anonimo = cliente(app)
    cl = dentro(app, "tecnico")
    paginas = [anonimo.get("/acceso"), anonimo.get("/salud"), anonimo.get("/robots.txt"),
               anonimo.get("/static/css/panel.css"), cl.get("/mercado"), cl.get("/estado-fuentes"),
               cl.get("/direccion"), cl.get("/cambiar-clave"), cl.get("/mercado?zona=espana&tipo=VO",
                                                                         headers={"HX-Request": "true"})]
    for r in paginas:
        for clave, valor in CABECERAS.items():
            assert r.headers.get(clave) == valor, (r.url, clave)
    csp = CABECERAS["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp and "script-src 'self'" in csp and "unsafe" not in csp
    assert "noindex" in CABECERAS["X-Robots-Tag"] and "max-age" in CABECERAS["Strict-Transport-Security"]
    for r in paginas:
        if "text/html" not in r.headers.get("content-type", ""):
            continue
        p = Enlaces()
        p.feed(r.text)
        externas = [u for u in p.urls if not (u.startswith("/") and not u.startswith("//")) and not u.startswith("#")]
        assert externas == [], (r.url, externas)
        assert p.scripts_en_linea == 0 and p.estilos == 0 and p.eventos == 0, r.url
        assert "http://" not in r.text and "https://" not in r.text
    css = anonimo.get("/static/css/panel.css").text
    assert "@import" not in css and all(u.startswith(("../", "/")) or u.startswith('"../')
                                        for u in re.findall(r"url\(([^)]+)\)", css))


def test_htmx_devuelve_solo_la_vista(app, pg, datos, personas):
    _con_datos(pg, datos)
    cl = dentro(app, "ventas")
    r = cl.get("/mercado?zona=comunidad&tipo=VO", headers={"HX-Request": "true"})
    assert "<html" not in r.text and r.text.lstrip().startswith('<div id="mercado"')
    assert 'aria-current="true">Andalucía' in r.text and 'aria-current="true">Ocasión (VO)' in r.text


# ================================================================== la vista con datos: cuadra con SQL

def _kpis(html):
    return dict(re.findall(r'kpi-etiqueta">([^<]+)</span>\s*<span class="kpi-valor">([^<]+)<', html))


def _filas(html):
    filas = {}
    for fila in re.findall(r"<tr>\s*<th scope=\"row\" class=\"izq\">(.*?)</tr>", html, re.S):
        modelo = fila.split("<", 1)[0]
        celdas = [re.sub(r"<[^>]+>", "", c).strip() for c in re.findall(r"<td[^>]*>(.*?)</td>", fila, re.S)]
        filas[modelo] = celdas
    return filas


def _num(texto):
    return int(texto.replace(".", "").replace("€", "").strip())


ZONA_SQL = {"provincia": "provincia_codigo = %(prov)s", "comunidad": "provincia_codigo = ANY(%(ccaa)s)",
            "espana": "true"}


def test_la_vista_cuadra_con_sql_directo(app, pg, datos, personas):
    conc = _con_datos(pg, datos)
    cl = dentro(app, "tecnico")
    for tipo, fuentes in (("VO", ["vo"]), ("VN", ["vn", "vn_otros"])):
        dia = pg.execute("SELECT max(snapshot_day) FROM scraper_runs GROUP BY snapshot_day "
                         "HAVING count(*) FILTER (WHERE fuente = ANY(%s)) = %s ORDER BY 1 DESC LIMIT 1",
                         (fuentes, len(fuentes))).fetchone()[0]
        ayer_ok = pg.execute("SELECT count(*) FROM scraper_runs WHERE snapshot_day = %s AND fuente = ANY(%s)",
                             (dia - timedelta(days=1), fuentes)).fetchone()[0] == len(fuentes)
        for zona in ("provincia", "comunidad", "espana"):
            html = cl.get(f"/mercado?zona={zona}&tipo={tipo}").text
            p = {"dia": dia, "f": fuentes, "prov": conc.provincia_codigo, "ccaa": list(conc.provincias_comunidad)}
            cond = ZONA_SQL[zona]
            k = _kpis(html)
            anuncios, propios, concesionarios = pg.execute(
                f"""SELECT count(*), count(*) FILTER (WHERE es_propio), count(DISTINCT dealer_id)
                    FROM stock_externo_snapshot WHERE snapshot_day = %(dia)s AND fuente = ANY(%(f)s) AND {cond}""",
                p).fetchone()
            assert _num(k["Anuncios publicados"]) == anuncios, (tipo, zona)
            if zona == "provincia":
                pct = f"{propios / anuncios * 100:.1f}".replace(".", ",") + " %"
                assert k[f"Nuestro peso en {conc.provincia}"] == pct
            else:
                assert _num(k["Concesionarios con stock"]) == concesionarios
            altas = pg.execute(
                f"""SELECT count(*) FROM stock_externo_snapshot s WHERE snapshot_day = %(dia)s AND fuente = ANY(%(f)s)
                    AND {cond} AND NOT EXISTS (SELECT 1 FROM stock_externo_snapshot p WHERE p.fuente = s.fuente
                        AND p.id_anuncio = s.id_anuncio AND p.snapshot_day < s.snapshot_day)""", p).fetchone()[0]
            ausencias = pg.execute(
                f"""SELECT count(*) FROM stock_externo_snapshot s WHERE snapshot_day = %(dia)s - 1 AND fuente = ANY(%(f)s)
                    AND {cond} AND NOT EXISTS (SELECT 1 FROM stock_externo_snapshot h WHERE h.fuente = s.fuente
                        AND h.id_anuncio = s.id_anuncio AND h.snapshot_day = %(dia)s)""", p).fetchone()[0]
            assert ayer_ok
            assert _num(k["Altas hoy"]) == altas and _num(k["Ausencias hoy"]) == ausencias, (tipo, zona)
            sql_modelos = {m: (n, precio) for m, n, precio in pg.execute(
                f"""SELECT modelo, count(*), round(percentile_cont(0.5) WITHIN GROUP (ORDER BY precio_cents) / 100)
                    FROM stock_externo_snapshot WHERE snapshot_day = %(dia)s AND fuente = ANY(%(f)s) AND {cond}
                    GROUP BY modelo""", p)}
            vista = _filas(html)
            assert set(vista) == set(sql_modelos)
            for modelo, (n, precio) in sql_modelos.items():
                celdas = vista[modelo]
                assert _num(celdas[0]) == n
                assert _num(celdas[4].split("n =")[0]) == int(precio), (modelo, celdas)
            orden = [_num(vista[m][0]) for m in vista]
            assert orden == sorted(orden, reverse=True)           # ordenada por anuncios


def test_la_vista_respeta_las_reglas_en_pantalla(app, pg, datos, personas):
    # Sin captura de hoy (HOY): datos del último día completo y se dice
    _con_datos(pg, datos, hasta=HOY - timedelta(days=1))
    cl = dentro(app, "ventas")
    html = cl.get("/mercado?zona=espana&tipo=VO").text
    assert "sin captura completa hoy" in html and "Falta la captura completa de hoy" in html
    vista = cl.get("/mercado?zona=espana&tipo=VO", headers={"HX-Request": "true"}).text  # solo la vista
    assert not re.search(r"vendid|\bventas\b", vista, re.I)        # ausencias, nunca ventas
    assert "Ausencias del 13/11" in vista
    serie = re.search(r"data-serie='([^']+)'", html).group(1)
    import json
    valores = json.loads(serie)["valores"]
    assert None in valores and 0 not in valores                    # el hueco del día 12 de VO, sin interpolar
    assert "VN incluye gasolina, diésel, eléctricos e híbridos." in cl.get("/mercado?tipo=VN").text
    assert "Faltan los eléctricos" not in cl.get("/mercado?tipo=VN").text


def test_sin_cifra_se_muestra_raya_con_motivo(app, pg, datos, personas):
    from sinteticos import capturar, flota
    capturar(datos, "vo", "2026-11-12", flota("VO", 3))
    capturar(datos, "vo", "2026-11-14", flota("VO", 3))           # sin captura el 13
    sv.cargar(pg, dia=HOY)
    am.precalcular(pg, hasta=HOY)
    cl = dentro(app, "ventas")
    html = cl.get("/mercado?zona=espana&tipo=VO").text
    k = _kpis(html)
    assert k["Altas hoy"] == "—" and k["Ausencias hoy"] == "—"
    assert "Sin cifra: falta la captura completa del día anterior" in html
    assert "«—» Frente a hace 7 días: falta la captura completa de hace 7 días (07/11)." in html
    fila = _filas(html)["Golf"]
    assert fila[1:4] == ["—", "—", "—"]
    assert "n = 3" in fila[4] and "≥" in fila[5]                    # pocos anuncios: atenuado con su n
    assert 'class="linea2 atenuado"' in html


# ================================================================== variación a 7 días

def _poner_mercado(pg, dia, modelos):
    """Filas panel_mercado_* a mano (como las dejaría el precálculo): [(modelo, anuncios, hace7)]."""
    pg.execute("INSERT INTO panel_mercado_dia (dia, tipo, zona, zona_nombre, anuncios, propios, concesionarios, "
               "altas_hoy, ausencias_hoy, bajas_7, ayer_completo, hace7_completo, ventana_completa) VALUES "
               "(%s, 'VO', 'espana', 'España', 1, 0, 1, 0, 0, 0, true, true, true)", (dia,))
    for modelo, n, hace7 in modelos:
        pg.execute("INSERT INTO panel_mercado_modelo (dia, tipo, zona, modelo, anuncios, anuncios_hace7, altas_7, "
                   "ausencias_7, precio_mediano_cents, n_precio, dias_mediana, dias_cota) VALUES "
                   "(%s, 'VO', 'espana', %s, %s, %s, 0, 0, 2000000, %s, 10, false)", (dia, modelo, n, hace7, n))


def test_variacion_en_unidades_y_porcentaje_desde_10(app, pg, personas):
    _poner_mercado(pg, HOY, [("Uno", 12, 10), ("Dos", 7, 9), ("Tres", 3, 3), ("Cuatro", 3, 0), ("Cinco", 30, 40)])
    html = dentro(app, "ventas").get("/mercado?zona=espana&tipo=VO").text
    celdas = {m: re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip()
              for m, c in re.findall(r'<th scope="row" class="izq">([^<]+)</th>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>(.*?)</td>',
                                     html, re.S)}
    assert celdas == {"Uno": "+2 +20 %", "Dos": "−2", "Tres": "0", "Cuatro": "+3", "Cinco": "−10 −25 %"}
    assert '<span class="pct">+20 %</span>' in html                 # el porcentaje va atenuado
    assert "solo si hace 7 días había al menos 10 anuncios" in html


def test_umbral_del_porcentaje_desde_la_configuracion(pg, panel_url, personas, tmp_path, monkeypatch):
    fichero = tmp_path / "c.yaml"
    fichero.write_text((RAIZ / "config/concesionario.yaml").read_text()
                       .replace("n_minimo_porcentaje: 10", "n_minimo_porcentaje: 5"))
    monkeypatch.setenv("CDF_CONCESIONARIO", str(fichero))
    app = crear_app(dsn=panel_url, hoy=lambda: HOY)
    _poner_mercado(pg, HOY, [("Seis", 8, 6)])
    html = dentro(app, "ventas").get("/mercado?zona=espana&tipo=VO").text
    assert '+2 <span class="pct">+33 %</span>' in html


def test_sin_cifra_de_hace_7_dias_no_cambia(app, pg, personas):
    _poner_mercado(pg, HOY, [("Siete", 5, None)])
    html = dentro(app, "ventas").get("/mercado?zona=espana&tipo=VO").text
    assert 'title="Sin cifra: falta la captura completa de hace 7 días">—</td>' in html


# ================================================================== sin «scraper» en lo que ve el usuario de Mercado

def test_mercado_sin_la_palabra_scraper(app, pg, datos, personas):
    cl = dentro(app, "tecnico")
    textos = [cl.get(f"/mercado?tipo={t}").text for t in ("VN", "VO")]          # sin datos todavía
    _con_datos(pg, datos, hasta=HOY - timedelta(days=1))                         # y sin captura de hoy
    for zona in ("provincia", "comunidad", "espana"):
        for tipo in ("VN", "VO"):
            textos.append(cl.get(f"/mercado?zona={zona}&tipo={tipo}").text)
            textos.append(cl.get(f"/mercado?zona={zona}&tipo={tipo}", headers={"HX-Request": "true"}).text)
    for html in textos:
        assert "scraper" not in html.lower()
    assert "anuncios que llevan tres capturas seguidas sin verse" in textos[-1]
    assert "La serie empieza el día de la primera captura" in textos[-1]
    assert "scraper" in cl.get("/estado-fuentes").text.lower()                  # la página técnica sí puede


# ================================================================== replicable: otro concesionario

FICTICIO = """
propio:
  nombre: Concesionario Ficticio
  nombre_panel: Marca Valladolid
  marca: Volkswagen
  ciudad: Valladolid
  provincia_codigo: "47"
  dealer_ids: [ESP99999]
  nombres_en_scrapers: [FICTICIO]
panel:
  n_minimo_medianas: 4
"""


def test_otro_concesionario_sin_rastro_del_propio(pg, datos, panel_url, tmp_path, monkeypatch):
    fichero = tmp_path / "concesionario.yaml"
    fichero.write_text(FICTICIO, encoding="utf-8")
    monkeypatch.setenv("CDF_CONCESIONARIO", str(fichero))
    conc = cargar_concesionario()
    assert (conc.provincia, conc.comunidad) == ("Valladolid", "Castilla y León")
    _con_datos(pg, datos, conc)
    for rol in ROLES:
        usuarios.alta(pg, rol, f"Persona {rol}", rol, CLAVE)
    pg.execute("UPDATE panel_usuarios SET cambiar_clave = false")
    usuarios.alta(pg, "nuevo", "Nuevo", "ventas", CLAVE)
    app = crear_app(dsn=panel_url, hoy=lambda: HOY)                 # lee CDF_CONCESIONARIO
    paginas = [cliente(app).get("/acceso").text, entrar(cliente(app), "x", "y").text]
    c_nuevo = dentro(app, "nuevo")
    paginas.append(c_nuevo.get("/cambiar-clave").text)
    tec = dentro(app, "tecnico")
    for ruta in RUTAS_VISTAS:
        paginas.append(tec.get(ruta).text)
    for zona in ("provincia", "comunidad", "espana"):
        for tipo in ("VN", "VO"):
            paginas.append(tec.get(f"/mercado?zona={zona}&tipo={tipo}").text)
            paginas.append(tec.get(f"/mercado?zona={zona}&tipo={tipo}", headers={"HX-Request": "true"}).text)
    paginas.append(dentro(app, "ventas").get("/estado-fuentes").text)  # página de «sin acceso»
    for html in paginas:
        assert "avisa" not in html.lower() and "sevilla" not in html.lower()
    mercado = tec.get("/mercado").text
    assert "Nuestro peso en Valladolid" in mercado and "Castilla y León" in mercado and "Marca Valladolid" in mercado
