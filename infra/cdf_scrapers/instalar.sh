#!/usr/bin/env bash
# instalar.sh — instala o actualiza los scrapers de Volkswagen en el servidor cdf
# y programa su ejecución diaria (hora de Madrid):
#   vo        ocasión, todos los combustibles          04:00
#   vn        nuevos, gasolina y diésel (B, D)         05:30
#   vn_otros  nuevos, eléctricos e híbridos (E, H)     06:45   (si el paquete VN trae config.otros.example.json)
#
# Uso, desde la carpeta cdf_scrapers, con uno o varios paquetes en cualquier orden:
#   sudo bash instalar.sh ZIP [ZIP ...]
#
# - Comprueba las huellas SHA-256 de cada paquete antes de instalar nada.
# - Código en /opt/cdf/app/scrapers/<instancia>; datos en /opt/cdf/data/<instancia>.
# - Nunca pisa un histórico: si ya hay base de datos, solo se actualiza el código.
# - Se puede repetir sin miedo, por ejemplo para instalar una versión nueva del código.

set -euo pipefail

AQUI="$(cd "$(dirname "$0")" && pwd)"
BASE="${CDF_BASE:-/opt/cdf}"
USUARIO="${CDF_USER:-cdf}"
SIN_SYSTEMD="${SIN_SYSTEMD:-0}"   # solo para pruebas

[ $# -ge 1 ] || { echo "Uso: sudo bash instalar.sh ZIP [ZIP ...]"; exit 2; }
[ "$(id -u)" -eq 0 ] || { echo "Ejecuta con sudo"; exit 1; }
for z in "$@"; do [ -f "$z" ] || { echo "No encuentro el fichero: $z"; exit 1; }; done
command -v unzip >/dev/null || { apt-get update -q && apt-get -yq install unzip; }
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
  || { echo "Hace falta Python 3.11 o posterior"; exit 1; }

TMP="$(mktemp -d)"
chmod 711 "$TMP"   # el usuario cdf necesita entrar para pasar las pruebas
trap 'rm -rf "$TMP"' EXIT
VISTOS=""

# Instala una instancia: código del paquete, su configuración y su carpeta de datos.
instalar_instancia() {
  local nombre="$1" dir="$2" config_origen="$3" db="$4"
  local codigo="$BASE/app/scrapers/$nombre" datos="$BASE/data/$nombre"
  mkdir -p "$codigo"
  # Código: todo menos data/ (los datos de ejemplo del paquete no se mezclan con el código).
  (cd "$dir" && tar --exclude=./data -cf - .) | (cd "$codigo" && tar -xf -)

  if [ -f "$datos/$db" ]; then
    echo "== $nombre: ya hay histórico en $datos; no lo toco"
  else
    mkdir -p "$datos"
    if [ -d "$dir/data" ] && [ "$nombre" != "vn_otros" ]; then
      cp -a "$dir/data/." "$datos/"
      echo "== $nombre: copiada la captura inicial del paquete a $datos"
    else
      echo "== $nombre: carpeta de datos nueva en $datos (empieza con la primera captura)"
    fi
  fi

  # config.json de la instancia, con los datos fuera del código.
  python3 - "$dir/$config_origen" "$codigo/config.json" "$datos" <<'PY'
import json, sys
origen, destino, datos = sys.argv[1], sys.argv[2], sys.argv[3]
cfg = json.load(open(origen, encoding="utf-8"))
cfg["data_dir"] = datos
open(destino, "w", encoding="utf-8").write(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n")
PY
  chown -R "$USUARIO:$USUARIO" "$codigo" "$datos"
}

instalar_paquete() {
  local zip="$1" i="$2" raiz dir tipo
  raiz="$TMP/paquete$i"
  mkdir -p "$raiz"
  unzip -q "$zip" -d "$raiz"
  dir="$(dirname "$(find "$raiz" -maxdepth 3 -name SHA256SUMS.txt | head -n 1)")"
  if   [ -f "$dir/vw_vn_stock.py" ]; then tipo=vn
  elif [ -f "$dir/vw_stock.py" ];    then tipo=vo
  else echo "$zip no parece un paquete de scraper (no tiene vw_vn_stock.py ni vw_stock.py)"; exit 1
  fi
  case " $VISTOS " in *" $tipo "*) echo "Hay dos paquetes del mismo scraper ($tipo)"; exit 1;; esac
  VISTOS="$VISTOS $tipo"

  (cd "$dir" && sha256sum -c --quiet SHA256SUMS.txt) || { echo "Las huellas SHA-256 de $zip no cuadran: no instalo"; exit 1; }
  echo "== $tipo: paquete íntegro ($(grep -m1 '^VERSION' "$dir"/vw_*stock.py | cut -d'"' -f2))"

  echo "== $tipo: pruebas del propio scraper"
  local prueba="$TMP/prueba$i"
  cp -a "$dir" "$prueba"
  chown -R "$USUARIO:$USUARIO" "$prueba"
  (cd "$prueba" && runuser -u "$USUARIO" -- python3 -m unittest discover -s tests -q)

  if [ "$tipo" = vo ]; then
    instalar_instancia vo "$dir" config.json vw_stock.sqlite3
  else
    instalar_instancia vn "$dir" config.json vw_vn_stock.sqlite3
    if [ -f "$dir/config.otros.example.json" ]; then
      instalar_instancia vn_otros "$dir" config.otros.example.json vw_vn_stock.sqlite3
    fi
  fi
}

i=0
for z in "$@"; do i=$((i + 1)); instalar_paquete "$z" "$i"; done

echo "== Programas de ejecución y estado"
install -d -o "$USUARIO" -g "$USUARIO" "$BASE/app/bin" "$BASE/logs" "$BASE/backups"
install -m 755 -o "$USUARIO" -g "$USUARIO" "$AQUI/bin/run_scraper.sh" "$AQUI/bin/estado.sh" "$BASE/app/bin/"

if [ "$SIN_SYSTEMD" != "1" ]; then
  ln -sf "$BASE/app/bin/estado.sh" /usr/local/bin/cdf-estado
  install -m 644 "$AQUI"/systemd/cdf-scraper@.service "$AQUI"/systemd/cdf-scraper-*.timer /etc/systemd/system/
  systemctl daemon-reload
  for inst in vo vn vn_otros; do
    if [ -d "$BASE/app/scrapers/$inst" ]; then
      systemctl enable --now "cdf-scraper-${inst/_/}.timer"
    fi
  done
  echo
  systemctl list-timers 'cdf-scraper-*' --no-pager
fi

echo
echo "Instalado. Para lanzar una captura ahora mismo, sin esperar a la madrugada (ejemplo con vn_otros):"
echo "  sudo systemctl start --no-block cdf-scraper@vn_otros.service"
echo "  cdf-estado"
