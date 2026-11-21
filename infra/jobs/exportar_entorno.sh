#!/usr/bin/env bash
# exportar_entorno.sh DESTINO — escribe en DESTINO (permisos 600) las variables que necesitan las
# tareas de cron, que no heredan el entorno del contenedor. Solo esta lista; nada más del .env.
set -euo pipefail

DESTINO="${1:?uso: exportar_entorno.sh DESTINO}"
PERMITIDAS='^(TZ|PGHOST|PGUSER|PGPASSWORD|PGDATABASE|CDF_SCRAPERS_DATA|BUCKET|BACKUP_PASSPHRASE|RCLONE_REMOTE|RCLONE_CONFIG_[A-Z0-9_]+|TELEGRAM_BOT_TOKEN|TELEGRAM_CHAT_ID|TELEGRAM_CHAT_DIRECCION|TELEGRAM_CHAT_VENTAS|TELEGRAM_CHAT_MARKETING|META_ACCESS_TOKEN|META_AD_ACCOUNT_ID|GA4_PROPERTY_ID|GA4_CREDENCIALES|ALERTAS_ENVIAR_EJEMPLO)$'

umask 077
: > "$DESTINO"
chmod 600 "$DESTINO"
for v in $(compgen -e | sort); do
  if [[ "$v" =~ $PERMITIDAS ]]; then
    printf 'export %s=%q\n' "$v" "${!v}" >> "$DESTINO"
  fi
done
