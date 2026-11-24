#!/usr/bin/env bash
# tarea.sh cargar|comprobar|precalcular|alertas|avisos|meta|ga4|gtrends|copia — lo lanza cron como usuario cdf. Deja el registro en
# /logs/<tarea>_<día>.log (en el servidor, /opt/cdf/logs/jobs/) y borra los de más de 90 días.
set -uo pipefail

TAREA="${1:?uso: tarea.sh cargar|comprobar|precalcular|alertas|avisos|meta|ga4|gtrends|copia}"
[ -f /run/cdf-env/jobs.env ] && . /run/cdf-env/jobs.env
# rclone sin fichero de configuración (el remoto va por RCLONE_CONFIG_HZ_*): sin aviso de rclone.conf.
export RCLONE_CONFIG=/dev/null
cd /app
LOG="/logs/${TAREA}_$(date +%F).log"

# avisos corre cada minuto: solo deja registro si ha enviado algo o ha fallado
if [ "$TAREA" = avisos ]; then
  SALIDA="$(python -m alertas.envio acciones 2>&1)"; RC=$?
  [ -n "$SALIDA" ] && printf '%s\n' "$SALIDA" >>"$LOG"
  exit "$RC"
fi

exec >>"$LOG" 2>&1
echo "== $(date -Is) inicio $TAREA"

case "$TAREA" in
  cargar)    python -m ingesta.scrapers_vw cargar ;;
  comprobar) python -m ingesta.scrapers_vw comprobar ;;
  # Precálculo de Mercado y, después (Meta de las 06:00 ya cruzado con Walcu), la previsión de leads, citas
  # y ventas, antes de las alertas de las 09:15. Si falla uno, el otro se intenta igual.
  precalcular) python -m analitica.mercado --ultimos 8; R1=$?; python -m analitica.prevision; R2=$?
               [ "$R1" -eq 0 ] && [ "$R2" -eq 0 ] ;;
  alertas)   python -m alertas.motor ;;
  meta)      python -m ingesta.meta ;;
  ga4)       python -m ingesta.ga4 ;;
  gtrends)   python -m ingesta.gtrends cargar ;;
  copia)     /app/infra/backup/pg_backup.sh ;;
  *)         echo "tarea desconocida: $TAREA"; exit 2 ;;
esac
RC=$?
echo "== $(date -Is) fin $TAREA, código $RC"
find /logs -name '*.log' -mtime +90 -delete 2>/dev/null || true
exit "$RC"
