#!/usr/bin/env bash
# run_scraper.sh vo|vn|vn_otros
# Ejecuta un scraper de Volkswagen, guarda una copia comprimida de su base,
# sube al bucket las respuestas originales y la copia, y deja una línea de estado.
# Lo lanza systemd cada madrugada; también se puede lanzar a mano.

set -uo pipefail

NOMBRE="${1:-}"
BASE="${CDF_BASE:-/opt/cdf}"
PY="${CDF_PYTHON:-/usr/bin/python3}"
case "$NOMBRE" in
  vo) SCRIPT=vw_stock.py;    DB=vw_stock.sqlite3 ;;
  vn|vn_otros) SCRIPT=vw_vn_stock.py; DB=vw_vn_stock.sqlite3 ;;
  *)  echo "uso: $0 vo|vn|vn_otros" >&2; exit 2 ;;
esac

CODIGO="$BASE/app/scrapers/$NOMBRE"
DATOS="$BASE/data/$NOMBRE"
HOY="$(date +%F)"
LOG="$BASE/logs/scraper_${NOMBRE}_${HOY}.log"
COPIA="$BASE/backups/${NOMBRE}_ultima.sqlite3.gz"
mkdir -p "$BASE/logs" "$BASE/backups"

if [ -f "$BASE/.env" ]; then set -a; . "$BASE/.env"; set +a; fi

avisar() {  # aviso por Telegram, solo si TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID están en .env
  [ -n "${TELEGRAM_BOT_TOKEN:-}" ] && [ -n "${TELEGRAM_CHAT_ID:-}" ] || return 0
  curl -fsS -m 20 "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
    --data-urlencode "chat_id=${TELEGRAM_CHAT_ID}" --data-urlencode "text=$1" >/dev/null || true
}

exec >>"$LOG" 2>&1
echo "== $(date -Is) inicio scraper $NOMBRE"

# 1. El scraper: descarga, comprueba y confirma la captura del día (o se niega y falla).
#    La paginación no es instantánea: si un concesionario publica o retira un anuncio mientras
#    se recorre, el total cambia y el scraper descarta la pasada. Por eso se reintenta.
INTENTOS="${CDF_INTENTOS:-4}"
ESPERA_MIN="${CDF_ESPERA_MIN:-15}"
RC=1
if cd "$CODIGO"; then
  for n in $(seq 1 "$INTENTOS"); do
    echo "== $(date -Is) intento $n de $INTENTOS"
    "$PY" "$SCRIPT" run --config "$CODIGO/config.json"
    RC=$?
    [ "$RC" -eq 1 ] || break            # 0 = bien; 3 = robots.txt lo impide: no se reintenta
    if [ "$n" -lt "$INTENTOS" ]; then
      echo "== intento $n fallido; nuevo intento en $ESPERA_MIN minutos"
      sleep $((ESPERA_MIN * 60))
    fi
  done
else
  echo "No existe $CODIGO"
fi
echo "== $(date -Is) scraper terminado, código $RC (0 bien, 1 error o revisión, 3 robots.txt lo impide)"

# 2. Copia consistente de la base, comprimida (el scraper ya ha terminado y soltado su bloqueo).
SUBIDA=0
if [ -f "$DATOS/$DB" ]; then
  "$PY" - "$DATOS/$DB" "$COPIA" <<'PY' || SUBIDA=1
import gzip, os, shutil, sqlite3, sys, tempfile
origen, destino = sys.argv[1], sys.argv[2]
fd, tmp = tempfile.mkstemp(suffix=".sqlite3", dir=os.path.dirname(destino)); os.close(fd)
try:
    src, dst = sqlite3.connect(origen), sqlite3.connect(tmp)
    with dst:
        src.backup(dst)
    dst.close(); src.close()
    with open(tmp, "rb") as f, gzip.open(destino + ".part", "wb", compresslevel=6) as g:
        shutil.copyfileobj(f, g, 1 << 20)
    os.replace(destino + ".part", destino)
finally:
    os.remove(tmp)
PY
else
  echo "Todavía no existe $DATOS/$DB"; SUBIDA=1
fi

# 3. Subida al bucket. copy, nunca sync: lo que ya está en el bucket no se borra.
if [ -n "${BUCKET:-}" ] && command -v rclone >/dev/null; then
  DESTINO="hz:$BUCKET/$NOMBRE"
  rclone copy "$DATOS/raw" "$DESTINO/raw" || SUBIDA=1
  if [ -f "$COPIA" ]; then
    rclone copyto "$COPIA" "$DESTINO/sqlite/ultima.sqlite3.gz" || SUBIDA=1
    if [ "$(date +%u)" = "7" ]; then  # los domingos, además, una copia con fecha
      rclone copyto "$COPIA" "$DESTINO/sqlite/semanal_${HOY}.sqlite3.gz" || SUBIDA=1
      rclone delete "$DESTINO/sqlite/" --include "semanal_*" --min-age 60d || true
    fi
  fi
  [ -f "$DATOS/exports/runs.csv" ] && { rclone copyto "$DATOS/exports/runs.csv" "$DESTINO/runs.csv" || SUBIDA=1; }
else
  echo "Falta BUCKET en .env o rclone: no se sube nada"; SUBIDA=1
fi
echo "== $(date -Is) subida al bucket: $([ "$SUBIDA" -eq 0 ] && echo bien || echo con errores)"

# 4. Línea de estado y aviso si algo fue mal.
echo "$(date -Is) $NOMBRE scraper=$RC subida=$SUBIDA" >> "$BASE/logs/estado.log"
find "$BASE/logs" -name "scraper_${NOMBRE}_*.log" -mtime +90 -delete 2>/dev/null || true
if [ "$RC" -ne 0 ] || [ "$SUBIDA" -ne 0 ]; then
  avisar "CDF: el scraper $NOMBRE ha tenido problemas el $HOY (scraper=$RC, subida=$SUBIDA). Log: $LOG"
fi
if [ "$RC" -ne 0 ]; then exit "$RC"; fi
exit "$SUBIDA"
