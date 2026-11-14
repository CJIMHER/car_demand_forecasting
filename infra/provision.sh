#!/usr/bin/env bash
# provision.sh — prepara un Ubuntu 24.04 o 26.04 recién creado en Hetzner para la plataforma (proyecto cdf).
#
# Uso, una sola vez y como root, en la primera conexión:
#   scp provision.sh root@IP:/root/  &&  ssh root@IP 'bash /root/provision.sh'
#
# Requisito: el servidor se creó con tu clave SSH (está en /root/.ssh/authorized_keys).
# Al terminar, root ya no puede entrar por SSH: se entra como "cdf" con la misma clave.
# NO cierres la sesión de root hasta comprobar en otra terminal que  ssh cdf@IP  funciona.

set -euo pipefail

USUARIO=cdf
BASE=/opt/cdf

[ "$(id -u)" -eq 0 ] || { echo "Ejecuta como root"; exit 1; }
. /etc/os-release
case "${VERSION_ID:-}" in
  24.04|26.04) echo "Sistema: ${PRETTY_NAME}" ;;
  *) echo "Aviso: pensado para Ubuntu 24.04 o 26.04; esto es ${PRETTY_NAME:-desconocido}" ;;
esac
[ -s /root/.ssh/authorized_keys ] || { echo "No hay clave en /root/.ssh/authorized_keys: me paro para no dejarte fuera"; exit 1; }

echo "== Zona horaria y paquetes"
timedatectl set-timezone Europe/Madrid
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get -yq upgrade
apt-get -yq install ca-certificates curl gnupg ufw fail2ban unattended-upgrades rclone jq

echo "== Usuario $USUARIO (sin contraseña, entra solo con clave)"
id "$USUARIO" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$USUARIO"
install -d -m 700 -o "$USUARIO" -g "$USUARIO" "/home/$USUARIO/.ssh"
install -m 600 -o "$USUARIO" -g "$USUARIO" /root/.ssh/authorized_keys "/home/$USUARIO/.ssh/authorized_keys"
echo "$USUARIO ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-$USUARIO"
chmod 440 "/etc/sudoers.d/90-$USUARIO"
# Ubuntu 26.04 trae sudo-rs: si su visudo no admite la comprobación, se avisa y se sigue
visudo -cf "/etc/sudoers.d/90-$USUARIO" || echo "Aviso: visudo no pudo comprobar el fichero; revisa luego  sudo -l  como $USUARIO"

echo "== SSH: solo clave, sin root"
# En sshd gana el primer valor leído: el fichero empieza por 01 para ir antes que 50-cloud-init.conf.
cat > /etc/ssh/sshd_config.d/01-cdf.conf <<EOF
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
AllowUsers $USUARIO
EOF
sshd -t
systemctl try-reload-or-restart ssh

echo "== Cortafuegos local (además del firewall de Hetzner)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw --force enable

echo "== Actualizaciones de seguridad automáticas"
cat > /etc/apt/apt.conf.d/20auto-upgrades <<EOF
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF

echo "== fail2ban para SSH"
systemctl enable --now fail2ban || echo "Aviso: fail2ban no arrancó; no es imprescindible, seguimos"

echo "== Docker (repositorio oficial)"
install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
  > /etc/apt/sources.list.d/docker.list
apt-get update -q
apt-get -yq install docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
usermod -aG docker "$USUARIO"
# Logs de contenedores acotados para que no llenen el disco
cat > /etc/docker/daemon.json <<EOF
{ "log-driver": "json-file", "log-opts": { "max-size": "20m", "max-file": "5" } }
EOF
systemctl restart docker

echo "== Swap de 4 GB"
if ! swapon --show | grep -q /swapfile; then
  fallocate -l 4G /swapfile
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

echo "== Carpetas del proyecto"
for d in "$BASE" "$BASE/app" "$BASE/data" "$BASE/data/raw/vn" "$BASE/data/raw/vo" "$BASE/logs" "$BASE/backups"; do
  install -d -m 750 -o "$USUARIO" -g "$USUARIO" "$d"
done
[ -f "$BASE/.env" ] || install -m 600 -o "$USUARIO" -g "$USUARIO" /dev/null "$BASE/.env"

echo
echo "Listo. Antes de cerrar esta sesión, en OTRA terminal comprueba:"
echo "  ssh $USUARIO@<IP>            (debe entrar)"
echo "  docker run --rm hello-world  (ya como $USUARIO, tras volver a entrar)"
echo "  ssh root@<IP>                (debe fallar)"
