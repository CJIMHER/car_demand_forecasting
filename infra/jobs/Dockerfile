# Contenedor de tareas programadas: Python 3.12 con cron dentro.
# Se construye desde la raíz del repositorio (ver infra/docker-compose.yml).
FROM python:3.12-slim-bookworm

# UID/GID del usuario cdf del servidor: obligatorios (el Makefile los pasa con  id -u cdf / id -g cdf).
ARG CDF_UID
ARG CDF_GID
ENV TZ=Europe/Madrid \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# cron, gpg y rclone para las copias; cliente de Postgres 16 del repositorio oficial (pg_dump
# debe ser de la misma versión mayor que el servidor).
RUN apt-get update \
 && apt-get install -y --no-install-recommends cron tzdata ca-certificates curl gnupg rclone \
 && install -d /usr/share/postgresql-common/pgdg \
 && curl -fsSL -o /usr/share/postgresql-common/pgdg/apt.postgresql.org.asc https://www.postgresql.org/media/keys/ACCC4CF8.asc \
 && echo "deb [signed-by=/usr/share/postgresql-common/pgdg/apt.postgresql.org.asc] https://apt.postgresql.org/pub/repos/apt bookworm-pgdg main" \
      > /etc/apt/sources.list.d/pgdg.list \
 && apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client-16 \
 && rm -rf /var/lib/apt/lists/* \
 && ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone \
 && test "$(date +%Z)" = CET -o "$(date +%Z)" = CEST \
 && command -v runuser >/dev/null

# Mismo UID/GID que el usuario cdf del servidor, para que los ficheros compartidos sean suyos.
RUN test -n "$CDF_UID" -a -n "$CDF_GID" -a "$CDF_UID" != 0 || { echo "Faltan CDF_UID/CDF_GID (no root)"; exit 1; } \
 && groupadd -o -g "$CDF_GID" cdf && useradd -o -u "$CDF_UID" -g cdf -M -d /app -s /usr/sbin/nologin cdf

WORKDIR /app
COPY requirements.txt requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . /app
RUN install -m 644 infra/jobs/crontab /etc/cron.d/cdf \
 && chmod 755 infra/jobs/entrypoint.sh infra/jobs/exportar_entorno.sh infra/jobs/tarea.sh infra/backup/pg_backup.sh

ENTRYPOINT ["/app/infra/jobs/entrypoint.sh"]
CMD ["cron", "-f"]
