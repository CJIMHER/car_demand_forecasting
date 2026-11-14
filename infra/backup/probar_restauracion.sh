#!/usr/bin/env bash
# probar_restauracion.sh [AAAA-MM-DD] — prueba de restauración de una copia de Postgres.
#
# Se ejecuta EN EL SERVIDOR, como cdf (make restaurar-prueba). Descarga del bucket la copia del
# día indicado (o la última), comprueba su huella, la descifra y la restaura en un contenedor
# aparte (cdf_restore_test, postgres:16, SIN red), y compara el recuento por día y fuente con la
# base de producción para los días que contiene la copia. Al terminar borra el contenedor y los
# ficheros descifrados. No toca cdf_postgres salvo para leer recuentos y anotar el resultado.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/opt/cdf/.env}"
set -a; . "$ENV_FILE"; set +a
: "${BUCKET:?falta BUCKET en $ENV_FILE}"
: "${BACKUP_PASSPHRASE:?falta BACKUP_PASSPHRASE en $ENV_FILE}"
REMOTO="${RCLONE_REMOTE:-hz}"
ORIGEN="$REMOTO:$BUCKET/backups/postgres"
PG_DB="${POSTGRES_DB:-cdf}"
CONT=cdf_restore_test

if [ -n "${1:-}" ]; then
  NOMBRE="cdf_$1.dump.gpg"
else
  NOMBRE="$(rclone lsf "$ORIGEN" --include 'cdf_*.dump.gpg' | sort | tail -n 1)"
  [ -n "$NOMBRE" ] || { echo "No hay copias en $ORIGEN"; exit 1; }
fi

TMP="$(mktemp -d)"
chmod 700 "$TMP"
limpiar() { docker rm -f "$CONT" >/dev/null 2>&1 || true; rm -rf "$TMP"; }
trap limpiar EXIT

prod() { docker exec -i cdf_postgres psql -X -q -At -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$PG_DB" "$@"; }
anotar() {
  prod -v estado="$1" -v detalle="$2" <<'SQL' || true
INSERT INTO fuentes_estado (fuente, tipo, dia, estado, detalle)
VALUES ('postgres_restauracion', 'copia', current_date, :'estado', :'detalle'::jsonb);
SQL
}

echo "== Descargo $ORIGEN/$NOMBRE"
rclone copyto "$ORIGEN/$NOMBRE" "$TMP/$NOMBRE"
rclone copyto "$ORIGEN/$NOMBRE.sha256" "$TMP/$NOMBRE.sha256"
(cd "$TMP" && sha256sum -c --quiet "$NOMBRE.sha256")

echo "== Descifro"
gpg --batch --quiet --pinentry-mode loopback --decrypt --passphrase-fd 3 -o "$TMP/cdf.dump" "$TMP/$NOMBRE" 3< <(printf '%s' "$BACKUP_PASSPHRASE")

echo "== Contenedor aparte $CONT (postgres:16, sin red)"
docker rm -f "$CONT" >/dev/null 2>&1 || true
docker run -d --name "$CONT" --network none \
  -e POSTGRES_PASSWORD="$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')" \
  -e POSTGRES_DB=restaurada postgres:16 >/dev/null
# Se espera al servidor definitivo (escucha en 127.0.0.1), no al temporal de la inicialización.
for _ in $(seq 1 90); do
  docker exec "$CONT" pg_isready -q -h 127.0.0.1 -U postgres -d restaurada && break
  sleep 1
done
docker exec "$CONT" pg_isready -q -h 127.0.0.1 -U postgres -d restaurada

echo "== pg_restore"
docker cp "$TMP/cdf.dump" "$CONT:/tmp/cdf.dump"
docker exec "$CONT" pg_restore -U postgres -d restaurada --no-owner --no-privileges --exit-on-error /tmp/cdf.dump

rest() { docker exec -i "$CONT" psql -X -q -At -v ON_ERROR_STOP=1 -U postgres -d restaurada "$@"; }
ULTIMO="$(rest -c "SELECT coalesce(max(snapshot_day)::text, '1900-01-01') FROM scraper_runs")"
RECUENTO="SELECT snapshot_day, fuente, count(*) FROM stock_externo_snapshot
          WHERE snapshot_day <= '$ULTIMO'::date GROUP BY 1, 2 ORDER BY 1, 2"
rest -c "$RECUENTO" > "$TMP/restaurada.txt"
prod -c "$RECUENTO" > "$TMP/produccion.txt"

echo "== Tablas en la copia"
for t in alembic_version scraper_runs stock_externo_snapshot eventos_externos anuncios_externos fuentes_estado; do
  printf '  %-24s %s\n' "$t" "$(rest -c "SELECT count(*) FROM $t")"
done

if [ ! -s "$TMP/restaurada.txt" ]; then
  echo "La copia no tiene filas en stock_externo_snapshot"
  anotar error "{\"fichero\": \"$NOMBRE\", \"error\": \"copia sin filas\"}"
  exit 1
fi
if diff -u "$TMP/produccion.txt" "$TMP/restaurada.txt"; then
  DIAS="$(wc -l < "$TMP/restaurada.txt" | tr -d ' ')"
  echo "== BIEN: $NOMBRE restaurada; $DIAS filas día×fuente iguales a producción hasta $ULTIMO"
  anotar ok "{\"fichero\": \"$NOMBRE\", \"hasta\": \"$ULTIMO\", \"filas_dia_fuente\": $DIAS}"
else
  echo "== MAL: la copia no cuadra con producción (diferencias arriba)"
  anotar error "{\"fichero\": \"$NOMBRE\", \"error\": \"recuento distinto de produccion\"}"
  exit 1
fi
