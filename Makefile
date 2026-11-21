# Órdenes de la plataforma en el servidor (usuario cdf, carpeta /opt/cdf/app/repo).
# Guía completa: infra/deploy.md

ENV_FILE ?= /opt/cdf/.env
export CDF_ENV_FILE := $(ENV_FILE)
# UID/GID del usuario cdf del servidor (dueño de /opt/cdf/data/*), no del que lanza make.
export CDF_UID ?= $(shell id -u cdf 2>/dev/null)
export CDF_GID ?= $(shell id -g cdf 2>/dev/null)
COMPOSE = docker compose -p cdf --env-file $(ENV_FILE) -f infra/docker-compose.yml
JOBS = $(COMPOSE) exec -T -u cdf jobs
# Órdenes sueltas en un contenedor nuevo, siempre como cdf
RUN = $(COMPOSE) run --rm --no-deps -u cdf
# Órdenes en el contenedor del panel (usuario de Postgres cdf_panel); con terminal para pedir contraseñas
PANEL_RUN = $(COMPOSE) --profile panel run --rm --no-deps panel python -m panel.usuarios
# Python del Mac para make panel-dev
PYTHON ?= .venv/bin/python

.PHONY: help comprobar-entorno deploy migrar cargar comprobar recuento concesionarios remarcar-propio \
        estado copia restaurar-prueba test hora logs ps psql precalcular panel-deploy panel-rol \
        panel-comprobar panel-logs usuario-alta usuario-baja usuario-rol usuario-restablecer usuarios panel-dev \
        ejemplo-cargar ejemplo-borrar alertas meta ga4

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

comprobar-entorno: ## Comprueba .env, rclone, usuario cdf y carpetas antes de desplegar
	@test -f $(ENV_FILE) || { echo "Falta $(ENV_FILE)"; exit 1; }
	@test "$$(stat -c %a $(ENV_FILE))" = 600 || { echo "$(ENV_FILE) debe tener permisos 600"; exit 1; }
	@for v in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB BUCKET BACKUP_PASSPHRASE TELEGRAM_BOT_TOKEN TELEGRAM_CHAT_ID; do \
	  grep -q "^$$v=." $(ENV_FILE) || { echo "Falta $$v en $(ENV_FILE)"; exit 1; }; done
	@set -a; . $(ENV_FILE); r="$$(echo "$${RCLONE_REMOTE:-hz}" | tr a-z A-Z)"; \
	  grep -q "^RCLONE_CONFIG_$${r}_TYPE=." $(ENV_FILE) || { echo "Falta RCLONE_CONFIG_$${r}_TYPE (remoto del bucket) en $(ENV_FILE)"; exit 1; }
	@test -n "$(CDF_UID)" -a -n "$(CDF_GID)" -a "$(CDF_UID)" != 0 || { echo "No encuentro el usuario cdf (id -u cdf)"; exit 1; }
	@for d in /opt/cdf/data/vo /opt/cdf/data/vn /opt/cdf/data/vn_otros; do \
	  test -d $$d || { echo "No existe $$d"; exit 1; }; \
	  test "$$(stat -c %u:%g $$d)" = "$(CDF_UID):$(CDF_GID)" || { echo "$$d no es de cdf ($(CDF_UID):$(CDF_GID))"; exit 1; }; done
	@install -d -m 750 /opt/cdf/logs/jobs
	@echo "Entorno correcto (cdf = $(CDF_UID):$(CDF_GID))"

deploy: comprobar-entorno ## Descarga el repositorio, aplica migraciones y reinicia los contenedores
	git pull --ff-only
	$(COMPOSE) build jobs
	$(COMPOSE) up -d --wait postgres
	$(RUN) jobs alembic upgrade head
	$(COMPOSE) up -d --force-recreate jobs
	@$(MAKE) --no-print-directory panel-deploy
	$(COMPOSE) ps
	@$(MAKE) --no-print-directory hora

panel-deploy: ## Panel y Caddy, solo si PANEL_DOMINIO tiene valor en el .env (si no, no se arrancan)
	@set -a; . $(ENV_FILE); set +a; \
	if [ -z "$${PANEL_DOMINIO:-}" ]; then \
	  echo "PANEL_DOMINIO vacío en $(ENV_FILE): el panel y Caddy no se arrancan."; \
	  $(COMPOSE) --profile panel stop panel caddy >/dev/null 2>&1 || true; \
	else \
	  test -n "$${PANEL_DB_PASSWORD:-}" || { echo "Falta PANEL_DB_PASSWORD en $(ENV_FILE)"; exit 1; }; \
	  $(MAKE) --no-print-directory panel-rol && \
	  $(COMPOSE) --profile panel build panel && \
	  $(COMPOSE) --profile panel up -d --wait --force-recreate panel caddy && \
	  echo "Panel en https://$${PANEL_DOMINIO}"; \
	fi

panel-rol: ## Activa el usuario de Postgres del panel (cdf_panel) con PANEL_DB_PASSWORD del .env
	$(RUN) jobs python -m panel.rol_bd

panel-comprobar: ## Certificado, cabeceras y puertos publicados del panel
	@set -a; . $(ENV_FILE); set +a; \
	echo "== Certificado de $$PANEL_DOMINIO"; \
	echo | openssl s_client -connect "$$PANEL_DOMINIO:443" -servername "$$PANEL_DOMINIO" 2>/dev/null \
	  | openssl x509 -noout -subject -issuer -dates; \
	echo "== Cabeceras de https://$$PANEL_DOMINIO/acceso"; \
	curl -sS -o /dev/null -D - "https://$$PANEL_DOMINIO/acceso" | grep -iE '^(HTTP|strict-transport|content-security|x-content-type|x-frame|referrer-policy|x-robots|set-cookie)'; \
	echo "== http:// redirige a https://"; \
	curl -sS -o /dev/null -D - "http://$$PANEL_DOMINIO/" | grep -iE '^(HTTP|location)'; \
	echo "== Puertos publicados (solo cdf_caddy: 80 y 443)"; \
	docker ps --format '{{.Names}}\t{{.Ports}}' | grep '^cdf_'

panel-logs: ## Registros del panel y de Caddy
	$(COMPOSE) --profile panel logs --tail 100 panel caddy

precalcular: ## Precálculo del panel; sin argumentos rehace todo el histórico, DIA=AAAA-MM-DD solo ese día
	$(JOBS) python -m analitica.mercado $${DIA:+--dia $$DIA}

# Datos de ejemplo de Dirección, Ventas y Marketing (docs/contrato_panel.md). Solo con
# PANEL_PERMITE_EJEMPLO=1 en el .env; solo tocan las filas origen = 'ejemplo' de las tablas del contrato.
ejemplo-cargar: ## Rellena las tablas del contrato con datos de ejemplo (requiere PANEL_PERMITE_EJEMPLO=1)
	@set -a; . $(ENV_FILE); set +a; \
	test "$${PANEL_PERMITE_EJEMPLO:-}" = 1 || { echo "No se cargan datos de ejemplo: falta PANEL_PERMITE_EJEMPLO=1 en $(ENV_FILE)."; exit 1; }; \
	$(COMPOSE) exec -T -u cdf -e PANEL_PERMITE_EJEMPLO=1 jobs python -m panel.ejemplo cargar

ejemplo-borrar: ## Quita los datos de ejemplo (requiere PANEL_PERMITE_EJEMPLO=1)
	@set -a; . $(ENV_FILE); set +a; \
	test "$${PANEL_PERMITE_EJEMPLO:-}" = 1 || { echo "No se borran datos de ejemplo: falta PANEL_PERMITE_EJEMPLO=1 en $(ENV_FILE)."; exit 1; }; \
	$(COMPOSE) exec -T -u cdf -e PANEL_PERMITE_EJEMPLO=1 jobs python -m panel.ejemplo borrar

usuario-alta: ## Alta en el panel: make usuario-alta USUARIO=ana NOMBRE="Ana" ROL=ventas (pide la contraseña)
	@test -n "$(USUARIO)" -a -n "$(NOMBRE)" -a -n "$(ROL)" || \
	  { echo 'Uso: make usuario-alta USUARIO=ana NOMBRE="Ana R." ROL=direccion|ventas|marketing|tecnico'; exit 1; }
	$(PANEL_RUN) alta "$(USUARIO)" --nombre "$(NOMBRE)" --rol "$(ROL)"

usuario-baja: ## Baja en el panel: make usuario-baja USUARIO=ana
	@test -n "$(USUARIO)" || { echo 'Uso: make usuario-baja USUARIO=ana'; exit 1; }
	$(PANEL_RUN) baja "$(USUARIO)"

usuario-rol: ## Cambio de rol: make usuario-rol USUARIO=ana ROL=marketing
	@test -n "$(USUARIO)" -a -n "$(ROL)" || { echo 'Uso: make usuario-rol USUARIO=ana ROL=marketing'; exit 1; }
	$(PANEL_RUN) rol "$(USUARIO)" "$(ROL)"

usuario-restablecer: ## Contraseña temporal nueva: make usuario-restablecer USUARIO=ana (la pide por teclado)
	@test -n "$(USUARIO)" || { echo 'Uso: make usuario-restablecer USUARIO=ana'; exit 1; }
	$(PANEL_RUN) restablecer "$(USUARIO)"

usuarios: ## Usuarios del panel
	$(PANEL_RUN) lista

panel-dev: ## (Mac) Panel en http://127.0.0.1:8000 con datos sintéticos y un usuario de prueba por rol
	$(PYTHON) -m panel.dev

migrar: ## Aplica las migraciones pendientes
	$(RUN) jobs alembic upgrade head

cargar: ## Carga ahora lo nuevo de los scrapers (lo mismo que el cron de las 08:30)
	$(JOBS) python -m ingesta.scrapers_vw cargar

alertas: ## Motor de alertas y envío por Telegram por rol (cron 09:15); SIN_TELEGRAM=1 calcula sin enviar
	$(JOBS) python -m alertas.motor $${SIN_TELEGRAM:+--sin-telegram} $${DIA:+--dia $$DIA}

meta: ## Descarga de Meta (cron 06:00): los últimos 7 días; sin credenciales no hace nada
	$(JOBS) python -m ingesta.meta

ga4: ## Descarga de GA4 (cron 06:10): los últimos 7 días; sin credenciales no hace nada
	$(JOBS) python -m ingesta.ga4

comprobar: ## Comprobación del día y resumen por Telegram (cron 09:00); SIN_TELEGRAM=1 no envía
	$(JOBS) python -m ingesta.scrapers_vw comprobar $${SIN_TELEGRAM:+--sin-telegram}

recuento: ## Anuncios cargados por día y fuente frente a lo que dijo el scraper
	$(JOBS) python -m ingesta.scrapers_vw recuento --dias $${DIAS:-14}

concesionarios: ## Cómo aparece el concesionario propio en los datos
	$(JOBS) python -m ingesta.scrapers_vw concesionarios

remarcar-propio: ## Recalcula es_propio tras cambiar config/concesionario.yaml (requiere deploy antes)
	$(JOBS) python -m ingesta.scrapers_vw remarcar-propio

estado: ## Últimas anotaciones de fuentes_estado
	$(COMPOSE) exec -T postgres sh -c 'psql -X -U "$$POSTGRES_USER" -d "$$POSTGRES_DB" -c \
	  "SELECT registrado_at::timestamp(0), fuente, tipo, dia, estado, left(detalle::text, 90) AS detalle \
	   FROM fuentes_estado ORDER BY id DESC LIMIT 20"'

copia: ## Copia cifrada de Postgres al bucket ahora mismo
	$(JOBS) /app/infra/backup/pg_backup.sh

restaurar-prueba: ## Restaura la última copia (o DIA=AAAA-MM-DD) en un contenedor aparte y la compara
	ENV_FILE=$(ENV_FILE) bash infra/backup/probar_restauracion.sh $${DIA:-}

test: ## pytest en el contenedor jobs, como cdf, contra un Postgres desechable (no toca cdf_postgres)
	docker rm -f cdf_postgres_test >/dev/null 2>&1 || true
	docker run -d --name cdf_postgres_test --network cdf_net -e POSTGRES_PASSWORD=prueba \
	  --tmpfs /var/lib/postgresql/data postgres:16 >/dev/null
	until docker exec cdf_postgres_test pg_isready -q -h 127.0.0.1 -U postgres; do sleep 1; done
	$(RUN) -e CDF_TEST_PG=1 -e PGHOST=cdf_postgres_test -e PGUSER=postgres \
	  -e PGPASSWORD=prueba -e PGDATABASE=postgres jobs python -m pytest -p no:cacheprovider; \
	  rc=$$?; docker rm -f cdf_postgres_test >/dev/null; exit $$rc

hora: ## Hora y zona del contenedor jobs, usuario de las tareas y su crontab
	@docker exec cdf_jobs date '+cdf_jobs: %F %T %Z (%z)'
	@docker exec -u cdf cdf_jobs id
	@docker exec cdf_jobs grep -E '^[0-9]' /etc/cron.d/cdf

logs: ## Registros de las tareas de hoy
	@tail -n 50 /opt/cdf/logs/jobs/*_$$(date +%F).log 2>/dev/null || echo "Sin registros hoy"

ps: ## Contenedores
	$(COMPOSE) ps

psql: ## Consola de Postgres
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
