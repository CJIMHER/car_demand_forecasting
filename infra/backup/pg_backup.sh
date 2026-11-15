#!/usr/bin/env bash
# pg_backup.sh — copia diaria de Postgres, cifrada, al bucket:
#   <remoto>:<BUCKET>/backups/postgres/cdf_AAAA-MM-DD.dump.gpg   (+ .sha256)
# Formato custom de pg_dump, cifrado simétrico AES-256 con gpg y BACKUP_PASSPHRASE.
# Retención: se borran del bucket las copias de más de 30 días.
# Corre en el contenedor cdf_jobs a las 09:30 (cron) y se puede lanzar a mano con  make copia.
#
# Variables: PGHOST, PGUSER, PGPASSWORD, PGDATABASE, BUCKET, BACKUP_PASSPHRASE,
#            RCLONE_REMOTE (hz por defecto), RETENCION_DIAS (30 por defecto),
#            TELEGRAM_BOT_TOKEN y TELEGRAM_CHAT_ID (aviso si falla), CDF_PYTHON (python3 por defecto).
set -euo pipefail

# Si la copia falla, el trap manda un aviso a Telegram (python -m ingesta.aviso_copia) y sale con el
# código de la copia aunque el aviso falle. Si va bien, no se envía nada.
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${CDF_PYTHON:-python3}"
PASO="variables de entorno"
TMP=""
GNUPGHOME=""

anotar() {  # deja el resultado en fuentes_estado (si Postgres responde)
  psql -X -q -v ON_ERROR_STOP=1 -v estado="$1" -v detalle="$2" <<'SQL' || true
INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle)
VALUES ('postgres', 'copia', current_date, :'estado', :'detalle'::jsonb);
SQL
}
terminar() {
  local rc=$?
  set +eu   # el trap tiene que llegar siempre a la limpieza
  if [ "$rc" -ne 0 ]; then
    echo "== $(date "+%F %T") la copia ha fallado en «${PASO}» (código $rc)"
    anotar error "{\"error\": \"pg_backup.sh terminó con código $rc\", \"paso\": \"$PASO\"}"
    PYTHONPATH="$RAIZ${PYTHONPATH:+:$PYTHONPATH}" "$PYTHON" -m ingesta.aviso_copia --codigo "$rc" --paso "$PASO" \
      || echo "== $(date "+%F %T") el aviso a Telegram no se pudo enviar (la copia sigue contando como fallida)"
  fi
  if [ -n "$GNUPGHOME" ]; then
    gpgconf --kill all >/dev/null 2>&1   # el gpg-agent que haya arrancado gpg en ese GNUPGHOME
  fi
  rm -rf ${TMP:+"$TMP"} ${GNUPGHOME:+"$GNUPGHOME"}
  exit "$rc"
}
trap terminar EXIT

# Comprobación explícita: con «${VAR:?}» algunas versiones de bash llegan al trap con código 0.
for v in BUCKET BACKUP_PASSPHRASE; do
  [ -n "${!v:-}" ] || { echo "falta $v"; exit 1; }
done
REMOTO="${RCLONE_REMOTE:-hz}"
RETENCION_DIAS="${RETENCION_DIAS:-30}"
DIA="$(date +%F)"
DESTINO="$REMOTO:$BUCKET/backups/postgres"
NOMBRE="cdf_${DIA}.dump.gpg"
# El remoto va por las variables RCLONE_CONFIG_<REMOTO>_*: sin fichero de configuración, y así
# rclone no avisa de «Config file ... rclone.conf not found».
export RCLONE_CONFIG=/dev/null

TMP="$(mktemp -d)"
# gpg necesita un directorio propio y HOME puede no ser escribible (en cdf_jobs no tiene por qué
# serlo). El cifrado es simétrico con BACKUP_PASSPHRASE: basta un GNUPGHOME temporal, que se borra al
# salir. En /tmp para que la ruta del socket del agente sea corta (límite de ~100 caracteres).
GNUPGHOME="$(mktemp -d /tmp/cdf_gnupg.XXXXXX)"
export GNUPGHOME
chmod 700 "$GNUPGHOME"

PASO="pg_dump y cifrado"
echo "== $(date "+%F %T") pg_dump de ${PGDATABASE:-?}"
# El pipefail hace fallar la copia si falla pg_dump, aunque gpg termine bien.
# Sin agente ni terminal: --batch y --pinentry-mode loopback; la frase entra por el descriptor 3
# (printf es interno de bash: no aparece en la lista de procesos ni en ningún log) y no se guarda
# en la caché del agente (--no-symkey-cache).
pg_dump -Fc --no-owner --no-privileges \
  | gpg --batch --yes --quiet --no-tty --pinentry-mode loopback --no-symkey-cache \
        --symmetric --cipher-algo AES256 \
        --passphrase-fd 3 -o "$TMP/$NOMBRE" 3< <(printf '%s' "$BACKUP_PASSPHRASE")

PASO="comprobación del fichero"
TAMANO="$(stat -c %s "$TMP/$NOMBRE" 2>/dev/null || stat -f %z "$TMP/$NOMBRE")"
[ "$TAMANO" -gt 1000 ] || { echo "Copia sospechosamente pequeña ($TAMANO bytes)"; exit 1; }
( cd "$TMP" && sha256sum "$NOMBRE" > "$NOMBRE.sha256" 2>/dev/null || shasum -a 256 "$NOMBRE" > "$NOMBRE.sha256" )

PASO="subida al bucket"
echo "== $(date "+%F %T") subida a $DESTINO/$NOMBRE ($TAMANO bytes)"
rclone copyto "$TMP/$NOMBRE" "$DESTINO/$NOMBRE"
rclone copyto "$TMP/$NOMBRE.sha256" "$DESTINO/$NOMBRE.sha256"

PASO="retención en el bucket"
echo "== $(date "+%F %T") retención: borro copias de más de $RETENCION_DIAS días"
rclone delete "$DESTINO" --min-age "${RETENCION_DIAS}d" --include 'cdf_*.dump.gpg*'

PASO="anotación en fuentes_estado"
anotar ok "{\"fichero\": \"$NOMBRE\", \"bytes\": $TAMANO}"
echo "== $(date "+%F %T") copia terminada"
