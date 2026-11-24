# Panel web (FastAPI + Jinja2 + htmx + ECharts, sin compilación de frontend).
# Se construye desde la raíz del repositorio (ver infra/docker-compose.yml). Corre sin privilegios.
FROM python:3.12-slim-bookworm

ENV TZ=Europe/Madrid \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/* \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
 && groupadd --system --gid 10001 panel \
 && useradd --system --uid 10001 --gid panel --no-create-home --home-dir /nonexistent --shell /usr/sbin/nologin panel

WORKDIR /app
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Solo lo que necesita el panel: su código, la configuración del concesionario y los módulos de
# ingesta y de analitica que importa (de analitica, solo la simulación de la previsión), también dentro de funciones e indirectamente (lo comprueba
# tests/test_infra_panel.py). El código es de root y de solo lectura.
COPY config/ /app/config/
COPY ingesta/__init__.py ingesta/provincias.py ingesta/concesionario.py ingesta/campanas.py ingesta/canales.py ingesta/referencias_meta.py ingesta/db.py /app/ingesta/
COPY analitica/__init__.py analitica/predictiva.py /app/analitica/
COPY panel/ /app/panel/

USER panel
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/salud', timeout=4).status == 200 else 1)"]
# Sin cabeceras de proxy de uvicorn: la IP del cliente la decide el panel, que solo se fía de Caddy
# (PANEL_PROXY). Sin registro de peticiones: el panel lleva su propio registro de accesos.
CMD ["uvicorn", "--factory", "panel.app:crear_app", "--host", "0.0.0.0", "--port", "8000", \
     "--no-proxy-headers", "--no-access-log", "--no-server-header", "--workers", "2"]
