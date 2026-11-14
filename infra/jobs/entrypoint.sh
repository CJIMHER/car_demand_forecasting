#!/usr/bin/env bash
# Arranque del contenedor jobs. cron no hereda el entorno del contenedor: se guardan las variables
# que necesitan las tareas en un fichero solo legible por cdf, que tarea.sh carga.
set -euo pipefail

install -d -m 700 -o cdf -g cdf /run/cdf-env
: > /run/cdf-env/jobs.env
chmod 600 /run/cdf-env/jobs.env
for v in TZ PGHOST PGUSER PGPASSWORD PGDATABASE BUCKET RCLONE_REMOTE RCLONE_CONFIG BACKUP_PASSPHRASE CDF_SCRAPERS_DATA; do
  if [ -n "${!v+x}" ]; then printf 'export %s=%q\n' "$v" "${!v}" >> /run/cdf-env/jobs.env; fi
done
chown cdf:cdf /run/cdf-env/jobs.env
install -d -o cdf -g cdf /logs 2>/dev/null || true

exec "$@"
