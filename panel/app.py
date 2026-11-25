"""Panel web: FastAPI + Jinja2 + htmx + ECharts, detrás de Caddy.

Se arranca con  uvicorn --factory panel.app:crear_app.
Variables de entorno:
  PGHOST, PGUSER (cdf_panel), PGPASSWORD, PGDATABASE   conexión (o PANEL_DATABASE_URL)
  PANEL_SESION_DIAS    caducidad de la sesión por inactividad (30 por defecto)
  PANEL_PROXY          nombre del proxy del que se aceptan cabeceras X-Forwarded-For (caddy)
  PANEL_EJEMPLO_VISIBLE_PARA  roles que ven las vistas con datos de ejemplo (tecnico por defecto)
  CDF_CONCESIONARIO    ruta de config/concesionario.yaml (opcional)
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Iterator
from datetime import date
from pathlib import Path
from urllib.parse import quote

import psycopg
from fastapi import Depends, FastAPI, Form, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.exceptions import HTTPException as StarletteHTTPException

from ingesta.concesionario import Concesionario, cargar as cargar_concesionario
from panel import alertas, contrato, direccion, formato, marketing, mercado, permisos, prevision, ventas
from panel import seguridad as seg
from panel.estaticos import Estaticos, Huellas

AQUI = Path(__file__).resolve().parent
LOG = logging.getLogger("panel")

MENSAJE_ACCESO = ("Usuario o contraseña incorrectos. Tras 5 intentos fallidos, el acceso se bloquea "
                  "durante 15 minutos.")
MENSAJE_CSRF = "El formulario ha caducado. Vuelve a cargar la página e inténtalo de nuevo."

CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; "
       "connect-src 'self'; form-action 'self'; frame-ancestors 'none'; base-uri 'none'")
CABECERAS = {
    "Strict-Transport-Security": "max-age=31536000",
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "X-Robots-Tag": "noindex, nofollow, noarchive",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}
PUBLICAS = ("/acceso", "/salud", "/robots.txt")


class Redireccion(Exception):
    def __init__(self, url: str):
        self.url = url


class Prohibido(Exception):
    pass


def crear_app(*, dsn: str | None = None, concesionario: Concesionario | None = None,
              hoy: Callable[[], date] | None = None, sesion_dias: int | None = None,
              proxy: str | None = None, ejemplo_para: str | None = None,
              estaticos: Path | None = None, modo_ejemplo: bool | None = None) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.dsn = dsn if dsn is not None else os.environ.get("PANEL_DATABASE_URL", "")
    app.state.conc = concesionario or cargar_concesionario()
    app.state.hoy = hoy or formato.hoy
    app.state.sesion_dias = sesion_dias or int(os.environ.get("PANEL_SESION_DIAS") or 30)
    app.state.proxy = seg.Proxy(proxy if proxy is not None else os.environ.get("PANEL_PROXY"))
    app.state.ejemplo_para = permisos.roles_ejemplo(
        ejemplo_para if ejemplo_para is not None else os.environ.get("PANEL_EJEMPLO_VISIBLE_PARA"))
    # Modo ejemplo explícito (PANEL_MODO_EJEMPLO=1): sin él, Dirección no muestra nunca filas de ejemplo
    app.state.modo_ejemplo = (modo_ejemplo if modo_ejemplo is not None
                              else os.environ.get("PANEL_MODO_EJEMPLO", "").strip() == "1")
    plantillas = Jinja2Templates(directory=str(AQUI / "templates"))
    huellas = Huellas(estaticos or AQUI / "static")   # `estaticos`: otra carpeta, solo en pruebas
    plantillas.env.globals.update(f=formato, TEXTO_DESACTIVADA=permisos.TEXTO_DESACTIVADA, estatico=huellas.url)
    app.mount("/static", Estaticos(huellas), name="static")

    # ------------------------------------------------------------------ utilidades

    def bd(request: Request) -> Iterator[psycopg.Connection]:
        with psycopg.connect(request.app.state.dsn, autocommit=True, connect_timeout=5) as conexion:
            yield conexion

    def es_htmx(request: Request) -> bool:
        return request.headers.get("HX-Request") == "true"

    def usuario_de(request: Request, pg) -> dict | None:
        return seg.leer_sesion(pg, request.cookies.get(seg.COOKIE_SESION), app.state.sesion_dias)

    def exigir(request: Request, pg, vista: str | None) -> dict:
        u = usuario_de(request, pg)
        if u is None:
            raise Redireccion("/acceso")
        if u["cambiar_clave"] and vista != "cambiar_clave":
            raise Redireccion("/cambiar-clave")
        if vista not in (None, "cambiar_clave") and not permisos.puede(u["rol"], vista):
            raise Prohibido()
        request.state.usuario = u
        return u

    def modos_de(request: Request, pg) -> dict:
        """Origen de las tablas del contrato, una vez por petición."""
        if not hasattr(request.state, "modos"):
            request.state.modos = contrato.modos(pg)
        return request.state.modos

    def estados_de(request: Request, pg) -> dict[str, str]:
        return contrato.estados(modos_de(request, pg), app.state.modo_ejemplo) if pg is not None else {}

    def poner_cookie(resp: Response, nombre: str, valor: str, max_age: int | None, samesite: str) -> None:
        # Sin Domain (cookie solo de este host), Secure, HttpOnly
        resp.set_cookie(nombre, valor, max_age=max_age, path="/", secure=True, httponly=True, samesite=samesite)

    def pintar(request: Request, plantilla: str, ctx: dict, pg=None, status: int = 200,
               usuario: dict | None = None) -> HTMLResponse:
        csrf = request.cookies.get(seg.COOKIE_CSRF) or seg.nuevo_csrf()
        conc = app.state.conc
        contexto = {"request": request, "csrf": csrf, "conc": conc, "usuario": usuario,
                    "rol": permisos.ROLES.get(usuario["rol"]) if usuario else None,
                    # El rol técnico ve el detalle (umbrales, reglas, método); los demás, qué pasa y qué hacer
                    "tecnico": bool(usuario) and usuario["rol"] == "tecnico",
                    "navegacion": (permisos.navegacion(usuario["rol"], estados_de(request, pg), app.state.ejemplo_para)
                                   if usuario else []),
                    "cabecera": mercado.estado_cabecera(pg, app.state.hoy()) if (usuario and pg) else None,
                    **ctx}
        resp = plantillas.TemplateResponse(request, plantilla, contexto, status_code=status)
        if request.cookies.get(seg.COOKIE_CSRF) != csrf:
            poner_cookie(resp, seg.COOKIE_CSRF, csrf, None, "strict")
        if usuario:  # la caducidad es por inactividad: cada página la renueva
            poner_cookie(resp, seg.COOKIE_SESION, request.cookies[seg.COOKIE_SESION],
                         app.state.sesion_dias * 86400, "lax")
        return resp

    def csrf_ok(request: Request, valor: str) -> bool:
        origen = request.headers.get("origin")
        if origen and origen.split("://", 1)[-1] != request.headers.get("host"):
            return False
        return seg.csrf_valido(request.cookies.get(seg.COOKIE_CSRF), valor)

    def redirigir(url: str) -> RedirectResponse:
        return RedirectResponse(url, status_code=303)

    # ------------------------------------------------------------------ middleware y errores

    @app.middleware("http")
    async def cabeceras(request: Request, call_next):
        par = request.client.host if request.client else None
        request.state.ip = app.state.proxy.ip_cliente(par, request.headers.get("x-forwarded-for"))
        resp = await call_next(request)
        for clave, valor in CABECERAS.items():
            resp.headers.setdefault(clave, valor)
        if request.url.path.startswith("/static/"):
            # Con la huella vigente, un año e immutable; sin ella, revalidar (panel/estaticos.py)
            resp.headers["Cache-Control"] = huellas.cache_control(request.url.path, request.query_params.get("v"))
        else:   # HTML y respuestas parciales de htmx: nunca se guardan (huellas viejas, equipos compartidos)
            resp.headers["Cache-Control"] = "no-store"
            resp.headers["Vary"] = "Cookie, HX-Request"
        return resp

    @app.exception_handler(Redireccion)
    async def _redireccion(request: Request, exc: Redireccion):
        if es_htmx(request):
            return Response(status_code=200, headers={"HX-Redirect": exc.url})
        return redirigir(exc.url)

    @app.exception_handler(Prohibido)
    def _prohibido(request: Request, exc: Prohibido):
        u = getattr(request.state, "usuario", None)
        return pintar(request, "error.html", {"titulo": "Sin acceso",
                                              "texto": "Tu perfil no tiene acceso a esta sección."},
                      status=403, usuario=u)

    @app.exception_handler(StarletteHTTPException)
    def _http(request: Request, exc: StarletteHTTPException):
        # Sin sesión, cualquier ruta que no sea pública lleva al acceso (también las que no existen).
        if not request.url.path.startswith("/static/") and request.url.path not in PUBLICAS:
            try:
                with psycopg.connect(app.state.dsn, autocommit=True, connect_timeout=5) as pg:
                    u = usuario_de(request, pg)
            except psycopg.Error:
                u = None
            if u is None:
                return redirigir("/acceso")
        return PlainTextResponse("No encontrado" if exc.status_code == 404 else "Petición no válida",
                                 status_code=exc.status_code)

    # ------------------------------------------------------------------ públicas

    @app.get("/salud", response_class=PlainTextResponse)
    def salud():
        try:
            with psycopg.connect(app.state.dsn, autocommit=True, connect_timeout=3) as pg:
                pg.execute("SELECT 1")
        except psycopg.Error:
            return PlainTextResponse("sin base de datos", status_code=503)
        return "ok"

    @app.get("/robots.txt", response_class=PlainTextResponse)
    def robots():
        return "User-agent: *\nDisallow: /\n"

    @app.get("/acceso")
    def acceso(request: Request, pg=Depends(bd)):
        if usuario_de(request, pg):
            return redirigir("/")
        return pintar(request, "acceso.html", {"mensaje": None})

    @app.post("/acceso")
    def acceder(request: Request, usuario: str = Form(""), clave: str = Form(""), csrf: str = Form(""),
                pg=Depends(bd)):
        if not csrf_ok(request, csrf):
            return pintar(request, "acceso.html", {"mensaje": MENSAJE_CSRF}, status=403)
        ip = request.state.ip
        seg.purgar(pg, app.state.sesion_dias)
        nombre = usuario.strip().lower()[:40]
        fila = pg.execute("SELECT usuario, clave_hash FROM panel_usuarios WHERE usuario = %s", (nombre,)).fetchone()
        existe = fila[0] if fila else None
        if seg.bloqueado(pg, existe, ip):
            seg.verificar(None, clave)  # mismo tiempo de respuesta
            seg.registrar(pg, existe, "bloqueado", ip)
            return pintar(request, "acceso.html", {"mensaje": MENSAJE_ACCESO}, status=401)
        if not seg.verificar(fila[1] if fila else None, clave):
            seg.registrar(pg, existe, "fallo", ip)
            return pintar(request, "acceso.html", {"mensaje": MENSAJE_ACCESO}, status=401)
        if seg.necesita_rehash(fila[1]):
            pg.execute("UPDATE panel_usuarios SET clave_hash = %s WHERE usuario = %s", (seg.hash_clave(clave), existe))
        seg.registrar(pg, existe, "ok", ip)
        resp = redirigir("/")
        poner_cookie(resp, seg.COOKIE_SESION, seg.crear_sesion(pg, existe), app.state.sesion_dias * 86400, "lax")
        poner_cookie(resp, seg.COOKIE_CSRF, seg.nuevo_csrf(), None, "strict")  # nuevo testigo tras entrar
        return resp

    # ------------------------------------------------------------------ sesión

    @app.post("/salir")
    def salir(request: Request, csrf: str = Form(""), pg=Depends(bd)):
        u = usuario_de(request, pg)
        if u is None:
            return redirigir("/acceso")
        if not csrf_ok(request, csrf):
            return pintar(request, "error.html", {"titulo": "Formulario caducado", "texto": MENSAJE_CSRF},
                          pg=pg, status=403, usuario=u)
        seg.cerrar_sesion(pg, request.cookies.get(seg.COOKIE_SESION))
        seg.registrar(pg, u["usuario"], "salida", request.state.ip)
        resp = redirigir("/acceso")
        resp.delete_cookie(seg.COOKIE_SESION, path="/", secure=True, httponly=True, samesite="lax")
        return resp

    @app.get("/cambiar-clave")
    def cambiar_clave(request: Request, pg=Depends(bd)):
        u = exigir(request, pg, "cambiar_clave")
        return pintar(request, "cambiar_clave.html", {"mensaje": None, "obligatorio": u["cambiar_clave"]},
                      pg=pg, usuario=u)

    @app.post("/cambiar-clave")
    def guardar_clave(request: Request, actual: str = Form(""), nueva: str = Form(""), repetida: str = Form(""),
                      csrf: str = Form(""), pg=Depends(bd)):
        u = exigir(request, pg, "cambiar_clave")
        ctx = {"obligatorio": u["cambiar_clave"]}
        if not csrf_ok(request, csrf):
            return pintar(request, "cambiar_clave.html", {**ctx, "mensaje": MENSAJE_CSRF}, pg=pg, status=403, usuario=u)
        ip = request.state.ip
        if seg.bloqueado(pg, u["usuario"], ip):
            seg.registrar(pg, u["usuario"], "bloqueado", ip)
            return pintar(request, "cambiar_clave.html", {**ctx, "mensaje": MENSAJE_ACCESO}, pg=pg, status=401, usuario=u)
        hash_actual = pg.execute("SELECT clave_hash FROM panel_usuarios WHERE usuario = %s",
                                 (u["usuario"],)).fetchone()[0]
        if not seg.verificar(hash_actual, actual):
            seg.registrar(pg, u["usuario"], "fallo", ip)
            return pintar(request, "cambiar_clave.html", {**ctx, "mensaje": "La contraseña actual no es correcta."},
                          pg=pg, status=401, usuario=u)
        try:
            if nueva != repetida:
                raise seg.ClaveNoValida("Las dos contraseñas nuevas no coinciden.")
            seg.comprobar_clave_nueva(nueva, u["usuario"], actual)
        except seg.ClaveNoValida as exc:
            return pintar(request, "cambiar_clave.html", {**ctx, "mensaje": str(exc)}, pg=pg, status=400, usuario=u)
        with pg.transaction():
            pg.execute("UPDATE panel_usuarios SET clave_hash = %s, cambiar_clave = false, clave_cambiada_at = now() "
                       "WHERE usuario = %s", (seg.hash_clave(nueva), u["usuario"]))
            pg.execute("DELETE FROM panel_sesiones WHERE usuario = %s", (u["usuario"],))
            seg.registrar(pg, u["usuario"], "cambio_clave", ip)
            testigo = seg.crear_sesion(pg, u["usuario"])
        resp = redirigir("/")
        poner_cookie(resp, seg.COOKIE_SESION, testigo, app.state.sesion_dias * 86400, "lax")
        return resp

    # ------------------------------------------------------------------ vistas

    @app.get("/")
    def inicio(request: Request, pg=Depends(bd)):
        u = exigir(request, pg, None)
        return redirigir(permisos.inicio(u["rol"], estados_de(request, pg), app.state.ejemplo_para))

    @app.get("/mercado")
    def vista_mercado(request: Request, zona: str = "provincia", tipo: str = "VN", pg=Depends(bd)):
        u = exigir(request, pg, "mercado")
        zona = zona if zona in mercado.ZONAS else "provincia"
        tipo = tipo if tipo in mercado.TIPOS else "VN"
        ctx = {"m": mercado.datos(pg, app.state.conc, tipo, zona, app.state.hoy()), "vista": "mercado"}
        return pintar(request, "_mercado.html" if es_htmx(request) else "mercado.html", ctx, pg=pg, usuario=u)

    @app.get("/mercado/modelo/{modelo}")
    def vista_modelo(request: Request, modelo: str, zona: str = "provincia", tipo: str = "VN", acabado: str = "",
                     combustible: str = "", motor: str = "", color: str = "", pg=Depends(bd)):
        u = exigir(request, pg, "mercado")
        zona = zona if zona in mercado.ZONAS else "provincia"
        tipo = tipo if tipo in mercado.TIPOS else "VN"
        pedido = tuple(x[:120] or None for x in (acabado, combustible, motor, color))
        d = mercado.detalle_modelo(pg, app.state.conc, tipo, zona, modelo[:80], app.state.hoy(), *pedido)
        if d["filtro"] != pedido:
            # Un filtro que no existe (en esa zona y tipo o con los otros dos) se descarta: la URL
            # queda con los que valen, para que la dirección sea siempre la de lo que se ve.
            if not es_htmx(request):
                return redirigir(d["url"])
        resp = pintar(request, "_mercado_modelo.html" if es_htmx(request) else "mercado_modelo.html",
                      {"d": d, "vista": "mercado"}, pg=pg, usuario=u)
        if es_htmx(request) and d["filtro"] != pedido:
            resp.headers["HX-Push-Url"] = d["url"]
        return resp

    def vista_contrato(request: Request, pg, clave: str, plantilla: str, parcial: str, ctx: Callable[[], dict]):
        """Dirección, Ventas y Marketing: abiertas según sus datos (permisos.visible); si no, «Se activa…»."""
        u = exigir(request, pg, clave)
        estados = estados_de(request, pg)
        if not permisos.visible(u["rol"], clave, estados, app.state.ejemplo_para):
            if es_htmx(request):
                raise Redireccion(permisos.POR_CLAVE[clave].ruta)
            return pintar(request, "pendiente.html", {"vista": clave, "titulo": permisos.POR_CLAVE[clave].titulo},
                          pg=pg, usuario=u)
        contexto = {"vista": clave, "banda_ejemplo": estados.get(clave) == "ejemplo", **ctx()}
        return pintar(request, parcial if es_htmx(request) else plantilla, contexto, pg=pg, usuario=u)

    @app.get("/direccion")
    def vista_direccion(request: Request, tipo: str = "todo", modelo_vo: str = "", pg=Depends(bd)):
        tipo = tipo if tipo in ("todo", "VN", "VO") else "todo"
        return vista_contrato(request, pg, "direccion", "direccion.html", "_direccion.html", lambda: {
            "d": direccion.datos(pg, app.state.conc, modos_de(request, pg), tipo, app.state.hoy(),
                                 modelo_vo[:80] or None, ejemplo=app.state.modo_ejemplo)})

    @app.get("/ventas")
    def vista_ventas(request: Request, tipo: str = "todo", riesgo: str = "", v: str = "", n: str = "",
                     modelo_vo: str = "", pg=Depends(bd)):
        tipo = tipo if tipo in ("todo", "VN", "VO") else "todo"
        mostrar = min(int(n), 10_000) if n.isdigit() else ventas.POR_PAGINA
        return vista_contrato(request, pg, "ventas", "ventas.html", "_ventas.html", lambda: {
            "d": ventas.datos(pg, app.state.conc, modos_de(request, pg), tipo, riesgo == "1", v[:40] or None,
                              app.state.hoy(), mostrar, modelo_vo[:80] or None, ejemplo=app.state.modo_ejemplo)})

    @app.post("/ventas/accion")
    def accion_ventas(request: Request, stock_id: str = Form(""), accion: str = Form(""), tipo: str = Form("todo"),
                      riesgo: str = Form(""), n: str = Form(""), csrf: str = Form(""), pg=Depends(bd)):
        """«Pedir campaña a marketing» y «Revisar precio»: anota la acción (tabla acciones); el aviso por
        Telegram lo envía cdf_jobs en el minuto siguiente. Nada se escribe en Walcu."""
        u = exigir(request, pg, "ventas")
        if not permisos.visible(u["rol"], "ventas", estados_de(request, pg), app.state.ejemplo_para):
            raise Prohibido()
        if not csrf_ok(request, csrf):
            return pintar(request, "error.html", {"titulo": "Formulario caducado", "texto": MENSAJE_CSRF},
                          pg=pg, status=403, usuario=u)
        stock_id = stock_id.strip()[:40]
        tipo = tipo if tipo in ("todo", "VN", "VO") else "todo"
        hecha, mensaje = ventas.pedir(pg, modos_de(request, pg), stock_id, accion, u["usuario"])
        LOG.info("Acción %s sobre %s pedida por %s: %s", accion[:20], stock_id, u["usuario"], "anotada" if hecha else "no")
        if not es_htmx(request):
            return redirigir(f"/ventas?tipo={tipo}{'&riesgo=1' if riesgo == '1' else ''}&v={quote(stock_id, safe='')}")
        mostrar = min(int(n), 10_000) if n.isdigit() else ventas.POR_PAGINA
        d = ventas.datos(pg, app.state.conc, modos_de(request, pg), tipo, riesgo == "1", stock_id, app.state.hoy(),
                         mostrar, ejemplo=app.state.modo_ejemplo)
        if d["detalle"].get("hay"):
            d["detalle"].update(mensaje=mensaje, mensaje_ok=hecha)
        return pintar(request, "_ventas.html", {"vista": "ventas", "d": d,
                                                "banda_ejemplo": estados_de(request, pg).get("ventas") == "ejemplo"},
                      pg=pg, usuario=u)

    @app.get("/marketing")
    def vista_marketing(request: Request, tipo: str = "todo", orden: str = "venta", gasto: str = "0",
                        citas_VN: str = "", citas_VO: str = "", pg=Depends(bd)):
        tipo = tipo if tipo in ("todo", "VN", "VO") else "todo"
        orden = orden if orden in ("venta", "gasto") else "venta"
        escenario = int(gasto) if gasto.lstrip("-").isdigit() and len(gasto) <= 4 else 0
        objetivos = {t: min(int(v), 100_000) for t, v in (("VN", citas_VN), ("VO", citas_VO))
                     if v.isdigit() and int(v) > 0}
        return vista_contrato(request, pg, "marketing", "marketing.html", "_marketing.html", lambda: {
            "d": marketing.datos(pg, app.state.conc, modos_de(request, pg), orden, app.state.hoy(), tipo, escenario,
                                 objetivos)})

    @app.get("/estado-fuentes")
    def estado(request: Request, pg=Depends(bd)):
        u = exigir(request, pg, "estado")
        return pintar(request, "estado_fuentes.html", {"e": mercado.estado_fuentes(pg), "vista": "estado",
                                                       "motor": alertas.estado_motor(pg),
                                                       "trafico": alertas.estado_trafico(pg),
                                                       "prevision": prevision.validacion(pg)}, pg=pg, usuario=u)

    return app
