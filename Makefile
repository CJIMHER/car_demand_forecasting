# Órdenes de la plataforma en el servidor (usuario cdf, carpeta /opt/cdf/app/repo).
# Guía completa: infra/deploy.md

ENV_FILE ?= /opt/cdf/.env
export CDF_UID ?= $(shell id -u)
export CDF_GID ?= $(shell id -g)
COMPOSE = docker compose -p cdf --env-file $(ENV_FILE) -f infra/docker-compose.yml
JOBS = $(COMPOSE) exec -T -u cdf jobs

.PHONY: help comprobar-entorno deploy migrar cargar comprobar recuento concesionarios remarcar-propio \
        estado copia restaurar-prueba test logs ps psql

help:
	@grep -E '^[a-z-]+:.*?## ' $(MAKEFILE_LIST) | sed 's/:.*## /\t/'

comprobar-entorno: ## Comprueba .env, rclone y carpetas antes de desplegar
	@test -f $(ENV_FILE) || { echo "Falta $(ENV_FILE)"; exit 1; }
	@test "$$(stat -c %a $(ENV_FILE))" = 600 || { echo "$(ENV_FILE) debe tener permisos 600"; exit 1; }
	@for v in POSTGRES_USER POSTGRES_PASSWORD POSTGRES_DB BUCKET BACKUP_PASSPHRASE; do \
	  grep -q "^$$v=." $(ENV_FILE) || { echo "Falta $$v en $(ENV_FILE)"; exit 1; }; done
	@set -a; . $(ENV_FILE); f="$${RCLONE_CONF_HOST:-/home/cdf/.config/rclone/rclone.conf}"; \
	  test -f "$$f" || { echo "No existe $$f (configuración de rclone del bucket)"; exit 1; }
	@for d in /opt/cdf/data/vo /opt/cdf/data/vn /opt/cdf/data/vn_otros; do \
	  test -d $$d || { echo "No existe $$d"; exit 1; }; done
	@install -d -m 750 /opt/cdf/logs/jobs
	@echo "Entorno correcto"

deploy: comprobar-entorno ## Descarga el repositorio, aplica migraciones y reinicia los contenedores
	git pull --ff-only
	$(COMPOSE) build jobs
	$(COMPOSE) up -d --wait postgres
	$(COMPOSE) run --rm --no-deps jobs alembic upgrade head
	$(COMPOSE) up -d --force-recreate jobs
	$(COMPOSE) ps

migrar: ## Aplica las migraciones pendientes
	$(COMPOSE) run --rm --no-deps jobs alembic upgrade head

cargar: ## Carga ahora lo nuevo de los scrapers (lo mismo que el cron de las 08:30)
	$(JOBS) python -m ingesta.scrapers_vw cargar

comprobar: ## Comprobación del día (lo mismo que el cron de las 09:00)
	$(JOBS) python -m ingesta.scrapers_vw comprobar

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

test: ## pytest en el contenedor jobs, contra un Postgres desechable (no toca cdf_postgres)
	docker rm -f cdf_postgres_test >/dev/null 2>&1 || true
	docker run -d --name cdf_postgres_test --network cdf_net -e POSTGRES_PASSWORD=prueba \
	  --tmpfs /var/lib/postgresql/data postgres:16 >/dev/null
	until docker exec cdf_postgres_test pg_isready -q -h 127.0.0.1 -U postgres; do sleep 1; done
	$(COMPOSE) run --rm --no-deps -e CDF_TEST_PG=1 -e PGHOST=cdf_postgres_test -e PGUSER=postgres \
	  -e PGPASSWORD=prueba -e PGDATABASE=postgres jobs python -m pytest; \
	  rc=$$?; docker rm -f cdf_postgres_test >/dev/null; exit $$rc

logs: ## Registros de las tareas de hoy
	@tail -n 50 /opt/cdf/logs/jobs/*_$$(date +%F).log 2>/dev/null || echo "Sin registros hoy"

ps: ## Contenedores
	$(COMPOSE) ps

psql: ## Consola de Postgres
	$(COMPOSE) exec postgres sh -c 'psql -U "$$POSTGRES_USER" -d "$$POSTGRES_DB"'
