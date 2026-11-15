#!/usr/bin/env bash
# tarea.sh cargar|comprobar|precalcular|copia — lo lanza cron como usuario cdf. Deja el registro en
# /logs/<tarea>_<día>.log (en el servidor, /opt/cdf/logs/jobs/) y borra los de más de 90 días.
set -uo pipefail

TAREA="${1:?uso: tarea.sh cargar|comprobar|precalcular|copia}"
[ -f /run/cdf-env/jobs.env ] && . /run/cdf-env/jobs.env
# rclone sin fichero de configuración (el remoto va por RCLONE_CONFIG_HZ_*): sin aviso de rclone.conf.
export RCLONE_CONFIG=/dev/null
cd /app
LOG="/logs/${TAREA}_$(date +%F).log"
exec >>"$LOG" 2>&1
echo "== $(date -Is) inicio $TAREA"

case "$TAREA" in
  cargar)    python -m ingesta.scrapers_vw cargar ;;
  comprobar) python -m ingesta.scrapers_vw comprobar ;;
  precalcular) python -m analitica.mercado --ultimos 8 ;;
  copia)     /app/infra/backup/pg_backup.sh ;;
  *)         echo "tarea desconocida: $TAREA"; exit 2 ;;
esac
RC=$?
echo "== $(date -Is) fin $TAREA, código $RC"
find /logs -name '*.log' -mtime +90 -delete 2>/dev/null || true
exit "$RC"
