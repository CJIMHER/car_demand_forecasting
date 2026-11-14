#!/usr/bin/env bash
# pg_backup.sh — copia diaria de Postgres, cifrada, al bucket:
#   <remoto>:<BUCKET>/backups/postgres/cdf_AAAA-MM-DD.dump.gpg   (+ .sha256)
# Formato custom de pg_dump, cifrado simétrico AES-256 con gpg y BACKUP_PASSPHRASE.
# Retención: se borran del bucket las copias de más de 30 días.
# Corre en el contenedor cdf_jobs a las 09:30 (cron) y se puede lanzar a mano con  make copia.
#
# Variables: PGHOST, PGUSER, PGPASSWORD, PGDATABASE, BUCKET, BACKUP_PASSPHRASE,
#            RCLONE_REMOTE (hz por defecto), RETENCION_DIAS (30 por defecto).
set -euo pipefail

: "${BUCKET:?falta BUCKET}"
: "${BACKUP_PASSPHRASE:?falta BACKUP_PASSPHRASE}"
REMOTO="${RCLONE_REMOTE:-hz}"
RETENCION_DIAS="${RETENCION_DIAS:-30}"
DIA="$(date +%F)"
DESTINO="$REMOTO:$BUCKET/backups/postgres"
NOMBRE="cdf_${DIA}.dump.gpg"

TMP="$(mktemp -d)"

anotar() {  # deja el resultado en fuentes_estado (si Postgres responde)
  psql -X -q -v ON_ERROR_STOP=1 -v estado="$1" -v detalle="$2" <<'SQL' || true
INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle)
VALUES ('postgres', 'copia', current_date, :'estado', :'detalle'::jsonb);
SQL
}
terminar() {
  local rc=$?
  if [ "$rc" -ne 0 ]; then anotar error "{\"error\": \"pg_backup.sh terminó con código $rc\"}"; fi
  rm -rf "$TMP"
  exit "$rc"
}
trap terminar EXIT

echo "== $(date -Is) pg_dump de ${PGDATABASE:-?}"
# El pipefail hace fallar la copia si falla pg_dump, aunque gpg termine bien.
pg_dump -Fc --no-owner --no-privileges \
  | gpg --batch --yes --quiet --pinentry-mode loopback --symmetric --cipher-algo AES256 \
        --passphrase-fd 3 -o "$TMP/$NOMBRE" 3< <(printf '%s' "$BACKUP_PASSPHRASE")

TAMANO="$(stat -c %s "$TMP/$NOMBRE" 2>/dev/null || stat -f %z "$TMP/$NOMBRE")"
[ "$TAMANO" -gt 1000 ] || { echo "Copia sospechosamente pequeña ($TAMANO bytes)"; exit 1; }
( cd "$TMP" && sha256sum "$NOMBRE" > "$NOMBRE.sha256" 2>/dev/null || shasum -a 256 "$NOMBRE" > "$NOMBRE.sha256" )

echo "== $(date -Is) subida a $DESTINO/$NOMBRE ($TAMANO bytes)"
rclone copyto "$TMP/$NOMBRE" "$DESTINO/$NOMBRE"
rclone copyto "$TMP/$NOMBRE.sha256" "$DESTINO/$NOMBRE.sha256"

echo "== $(date -Is) retención: borro copias de más de $RETENCION_DIAS días"
rclone delete "$DESTINO" --min-age "${RETENCION_DIAS}d" --include 'cdf_*.dump.gpg*'

anotar ok "{\"fichero\": \"$NOMBRE\", \"bytes\": $TAMANO}"
echo "== $(date -Is) copia terminada"
