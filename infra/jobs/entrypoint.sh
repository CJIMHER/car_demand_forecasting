#!/usr/bin/env bash
# Arranque del contenedor cdf_jobs.
#
# - Con «cron» (lo normal): comprueba la zona horaria, prepara el entorno de las tareas y arranca
#   el demonio de cron. Es lo ÚNICO que corre como root (cron lo necesita para cambiar de usuario);
#   cada tarea de /etc/cron.d/cdf se ejecuta como cdf.
# - Cualquier otra orden (alembic, pytest, python -m ...) se ejecuta como cdf: si llega como root,
#   se baja a cdf con runuser. Así ningún fichero -shm/-wal de SQLite queda a nombre de root.
set -euo pipefail

if [ "${1:-}" = "cron" ]; then
  [ "$(id -u)" -eq 0 ] || { echo "cron debe arrancar como root (las tareas corren como cdf)"; exit 1; }

  # Hora de Madrid para cron (usa la hora local del sistema) y para las tareas (TZ).
  zona="$(readlink -f /etc/localtime)"
  if [ "$zona" != "/usr/share/zoneinfo/Europe/Madrid" ] || [ "${TZ:-}" != "Europe/Madrid" ]; then
    echo "Zona horaria incorrecta (localtime=$zona, TZ=${TZ:-}); se esperaba Europe/Madrid"; exit 1
  fi
  case "$(date +%Z)" in CET|CEST) ;; *) echo "date no da hora de Madrid: $(date)"; exit 1 ;; esac
  echo "cdf_jobs: $(date '+%F %T %Z'), tareas a las 06:00, 06:10, 08:30, 09:00, 09:10, 09:15 y 09:30 (y avisos cada minuto) hora de Madrid como $(id -u cdf):$(id -g cdf)"

  install -d -m 700 -o cdf -g cdf /run/cdf-env
  /app/infra/jobs/exportar_entorno.sh /run/cdf-env/jobs.env
  chown cdf:cdf /run/cdf-env/jobs.env
  exec "$@"
fi

if [ "$(id -u)" -eq 0 ]; then
  exec runuser -u cdf -- "$@"
fi
exec "$@"
