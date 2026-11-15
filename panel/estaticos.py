"""Estáticos con huella: cada despliegue sirve el CSS, el JS, las fuentes y los recursos de terceros nuevos.

Las plantillas no escriben /static/... a mano: piden `estatico("css/panel.css")`, que devuelve
/static/css/panel.css?v=<huella>, con la huella sacada del contenido del fichero (SHA-256, 12
caracteres), nunca de la fecha. Si el fichero cambia, cambia la URL y el navegador la pide de nuevo.

El CSS se sirve con sus url(...) reescritas a la URL con huella del fichero al que apuntan (las
fuentes), así que la huella del CSS cambia también si cambia una fuente.

Cabeceras (las pone el middleware de panel/app.py con `cache_control`):
  - URL con la huella actual del fichero: un año e immutable.
  - Sin huella o con una huella que ya no es la del fichero: no-cache (el navegador revalida con
    ETag). Así una URL vieja nunca queda guardada con un contenido que no es el suyo.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from starlette.responses import Response
from starlette.staticfiles import StaticFiles

PREFIJO = "/static/"
LARGA = "public, max-age=31536000, immutable"
REVALIDAR = "no-cache"
_URL_CSS = re.compile(r"""url\(\s*(["']?)([^"')]+)\1\s*\)""")


class Huellas:
    """Huella del contenido de cada estático. Se recalcula si cambia el fichero (tamaño o mtime),
    para que el panel en el Mac sirva la nueva sin reiniciar; la huella sale siempre del contenido."""

    def __init__(self, raiz: Path):
        self.raiz = raiz.resolve()
        self._cache: dict[str, tuple[tuple, str, bytes | None, tuple[str, ...]]] = {}

    def _ruta(self, relativa: str) -> Path:
        ruta = (self.raiz / relativa).resolve()
        if self.raiz not in ruta.parents:
            raise ValueError(f"fuera de static: {relativa}")
        return ruta

    def _firma(self, relativa: str) -> tuple:
        """Tamaño y mtime del fichero y, si es un CSS, de los que cita: solo deciden cuándo releer."""
        st = self._ruta(relativa).stat()
        propia = (relativa, st.st_size, st.st_mtime_ns)
        guardado = self._cache.get(relativa)
        if guardado and guardado[3]:
            return (propia, *(self._firma(d) for d in guardado[3]))
        return (propia,)

    def _leer(self, relativa: str) -> tuple[str, bytes | None]:
        guardado = self._cache.get(relativa)
        if guardado and guardado[0] == self._firma(relativa):
            return guardado[1], guardado[2]
        ruta = self._ruta(relativa)
        datos = ruta.read_bytes()
        cuerpo, citados = None, []
        if ruta.suffix == ".css":   # primero las url() a su versión con huella; la huella del CSS sale del resultado
            texto, citados = self._reescribir_css(relativa, datos.decode("utf-8"))
            cuerpo = datos = texto.encode("utf-8")
        huella = hashlib.sha256(datos).hexdigest()[:12]
        self._cache[relativa] = ((), huella, cuerpo, tuple(citados))   # para que _firma vea los citados
        self._cache[relativa] = (self._firma(relativa), huella, cuerpo, tuple(citados))
        return huella, cuerpo

    def _reescribir_css(self, relativa: str, texto: str) -> tuple[str, list[str]]:
        carpeta = Path(relativa).parent
        citados: list[str] = []

        def cambiar(m: re.Match) -> str:
            destino = m.group(2)
            if destino.startswith(("data:", "http:", "https:", "//", "#")):
                return m.group(0)
            if destino.startswith(PREFIJO):
                rel = destino[len(PREFIJO):]
            else:
                rel = (self.raiz / carpeta / destino).resolve().relative_to(self.raiz).as_posix()
            rel = rel.split("?")[0]
            citados.append(rel)
            return f'url("{self.url(rel)}")'

        return _URL_CSS.sub(cambiar, texto), citados

    def huella(self, relativa: str) -> str:
        return self._leer(relativa)[0]

    def url(self, relativa: str) -> str:
        """URL con huella de un estático; `relativa` es la ruta dentro de panel/static."""
        return f"{PREFIJO}{relativa}?v={self.huella(relativa)}"

    def css(self, relativa: str) -> bytes:
        return self._leer(relativa)[1]

    def cache_control(self, ruta_url: str, v: str | None) -> str:
        """Cabecera Cache-Control de una petición a /static/…"""
        relativa = ruta_url[len(PREFIJO):]
        try:
            vigente = v is not None and v == self.huella(relativa)
        except (OSError, ValueError):
            return REVALIDAR
        return LARGA if vigente else REVALIDAR


class Estaticos(StaticFiles):
    """StaticFiles que sirve los .css con sus url() ya con huella."""

    def __init__(self, huellas: Huellas, **kw):
        super().__init__(directory=str(huellas.raiz), **kw)
        self.huellas = huellas

    async def get_response(self, path: str, scope) -> Response:
        resp = await super().get_response(path, scope)
        if resp.status_code == 200 and path.endswith(".css"):
            cuerpo = self.huellas.css(path)
            etag = f'"{self.huellas.huella(path)}"'
            if _etag_coincide(scope, etag):
                return Response(status_code=304, headers={"ETag": etag})
            return Response(cuerpo, media_type="text/css; charset=utf-8", headers={"ETag": etag})
        return resp


def _etag_coincide(scope, etag: str) -> bool:
    for nombre, valor in scope.get("headers", []):
        if nombre == b"if-none-match":
            return etag in [x.strip() for x in valor.decode("latin-1").split(",")]
    return False
