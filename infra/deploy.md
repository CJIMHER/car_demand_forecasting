# Despliegue en el servidor cdf (hito 0 y panel del hito 1b)

Todo se ejecuta en el servidor de Hetzner como usuario `cdf`. Claude no se conecta al servidor:
estas órdenes las lanza Cayetano. Los scrapers y sus bases no se tocan: el contenedor `cdf_jobs`
solo las lee (`mode=ro`).

## Qué se despliega

| Contenedor | Qué hace |
| --- | --- |
| `cdf_postgres` | Postgres 16, volumen `cdf_pgdata`, red interna `cdf_net`, **sin ningún puerto publicado** |
| `cdf_jobs` | Python 3.12 con cron en hora de Madrid: carga 08:30, comprobación y resumen por Telegram 09:00, precálculo del panel 09:10, copia 09:30 |
| `cdf_panel` | Panel web (FastAPI), sin privilegios ni puertos publicados; Postgres como `cdf_panel`. Solo si `PANEL_DOMINIO` tiene valor |
| `cdf_caddy` | HTTPS automático para `PANEL_DOMINIO`; el **único** que publica puertos (80 y 443). Solo si `PANEL_DOMINIO` tiene valor |

Tres reglas de este despliegue:

- **Secretos**: solo en `/opt/cdf/.env`. El contenedor `cdf_jobs` lo recibe como `env_file`; las
  tareas de cron solo ven la lista de `infra/jobs/exportar_entorno.sh` (Postgres, bucket, rclone,
  frase de las copias y Telegram).
- **rclone sin fichero**: el remoto `hz` se define con las variables `RCLONE_CONFIG_HZ_*` del `.env`,
  igual que en `run_scraper.sh`. No hay `rclone.conf` ni dentro ni fuera del contenedor.
- **Usuario**: la imagen se construye con el UID/GID del usuario `cdf` del servidor (`id -u cdf`,
  `id -g cdf`, los pone el Makefile). Solo el demonio de cron corre como root; cada tarea, y
  cualquier `make` que lance algo en el contenedor, corre como `cdf`. La carga se niega a abrir las
  bases de los scrapers como root, para que ningún `-shm`/`-wal` quede a nombre de root.

## 0. Antes de empezar (en el Mac)

Revisar el diff de la rama del hito, subirla a GitHub y fusionarla en `main`:

```bash
git push -u origin <rama-del-hito>   # p. ej. hito-1b, y abrir/fusionar el PR en GitHub
```

## 1. Paquetes del host (una vez)

```bash
sudo apt-get update && sudo apt-get install -y make git
docker compose version          # instalado por provision.sh
id -u cdf; id -g cdf            # el Makefile construye la imagen con estos UID/GID
ls -ln /opt/cdf/data            # vo, vn y vn_otros deben ser de ese UID/GID
```

## 2. Clave de despliegue de solo lectura (una vez)

```bash
ssh-keygen -t ed25519 -N "" -C "cdf-deploy" -f ~/.ssh/cdf_repo_deploy
cat ~/.ssh/cdf_repo_deploy.pub
```

En GitHub: repositorio `car_demand_forecasting` → Settings → Deploy keys → Add deploy key, pegar la
clave pública y **dejar sin marcar** «Allow write access».

```bash
cat >> ~/.ssh/config <<'EOF'
Host github-cdf
  HostName github.com
  User git
  IdentityFile ~/.ssh/cdf_repo_deploy
  IdentitiesOnly yes
EOF
chmod 600 ~/.ssh/config
ssh -T github-cdf                # debe saludar con el nombre del repositorio
git clone git@github-cdf:CUENTA/car_demand_forecasting.git /opt/cdf/app/repo   # CUENTA = dueño del repo
```

## 3. Secretos en /opt/cdf/.env (una vez)

Ya están `BUCKET`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` y las `RCLONE_CONFIG_HZ_*` de los
scrapers. Se añaden los de Postgres y la frase de las copias, con valores hexadecimales: el fichero
lo leen a la vez `run_scraper.sh` (con `.`) y docker compose (como `env_file`), y compose
interpretaría un `$` dentro de un valor.

```bash
cat >> /opt/cdf/.env <<EOF
POSTGRES_USER=cdf
POSTGRES_PASSWORD=$(openssl rand -hex 32)
POSTGRES_DB=cdf
BACKUP_PASSPHRASE=$(openssl rand -hex 32)
RCLONE_REMOTE=hz
EOF
chmod 600 /opt/cdf/.env
grep BACKUP_PASSPHRASE /opt/cdf/.env    # cópiala a tu gestor de contraseñas: sin ella las copias no se descifran
```

Comprobaciones:

```bash
grep -c '^RCLONE_CONFIG_HZ_' /opt/cdf/.env       # las variables del remoto «hz» (sin fichero rclone.conf)
grep -E '^[A-Z0-9_]+=.*\$' /opt/cdf/.env | cut -d= -f1   # no debe salir nada: valores con «$»
set -a; . /opt/cdf/.env; set +a
rclone lsd "hz:$BUCKET"                          # deben aparecer vo, vn y vn_otros
```

## 4. Desplegar

```bash
cd /opt/cdf/app/repo
make deploy        # comprueba el entorno, git pull, construye cdf_jobs, migra, arranca y muestra la hora
make test          # pytest en el contenedor (como cdf) contra un Postgres desechable (cdf_postgres_test)
```

`make deploy` termina con `make hora`, que debe mostrar algo así:

```
cdf_jobs: 2026-11-16 10:12:03 CET (+0100)
uid=1000(cdf) gid=1000(cdf) groups=1000(cdf)     <- el mismo UID/GID que  id cdf  en el host
30 8 * * * cdf /app/infra/jobs/tarea.sh cargar
0 9 * * * cdf /app/infra/jobs/tarea.sh comprobar
30 9 * * * cdf /app/infra/jobs/tarea.sh copia
```

El cron del contenedor usa la hora local del sistema, que es `Europe/Madrid` (`tzdata` y
`/etc/localtime` en la imagen; el contenedor no arranca si no es así). Con el cambio de hora las
tareas siguen a las 08:30, 09:00 y 09:30 de Madrid: CET en invierno, CEST en verano.

Comprobar que Postgres no publica ningún puerto:

```bash
docker port cdf_postgres          # no debe mostrar nada
sudo ss -ltnp | grep 5432         # no debe mostrar nada
```

`make deploy` se repite igual en cada actualización: descarga el repositorio, aplica las migraciones
pendientes y reinicia los contenedores. Los datos de Postgres viven en el volumen `cdf_pgdata` y no
se pierden al reiniciar.

## 5. Primera carga y comprobaciones

La primera carga copia todo el histórico de los scrapers desde su primer día (VO desde el 1 de
noviembre). Tarda unos minutos.

```bash
make cargar            # carga todo lo pendiente; repetirla no duplica nada
make recuento DIAS=30  # anuncios por día: «scraper» y «cargados» deben coincidir
make concesionarios    # cómo aparece AVISA (ESP00710); si el nombre no es «AVISA», corrígelo en
                       # config/concesionario.yaml (nombres_en_scrapers), despliega y: make remarcar-propio
make comprobar         # anota en fuentes_estado y ENVÍA el resumen al grupo de Telegram
                       # (SIN_TELEGRAM=1 make comprobar  para no enviarlo)
make estado            # últimas anotaciones de cargas, comprobaciones, avisos y copias
sudo find /opt/cdf/data -user root     # no debe salir nada: ningún fichero de SQLite a nombre de root
```

### Resumen diario por Telegram

Cada día a las 09:00, después de anotar la comprobación, llega al grupo un mensaje de texto plano:

```
CDF 14/11 · todo correcto
VO: 3.076 anuncios
VN: 4.798 anuncios
VN eléctricos e híbridos: 419 anuncios
Carga a Postgres: correcta (8.293 filas)
```

Si algo falla, la primera línea lo cuenta («CDF 14/11 · 1 problema») y la línea afectada dice qué
pasa (sin captura de hoy y el último intento, capturado pero sin cargar, error al comprobar, o carga
con errores). Las cifras salen de Postgres: cada scraper, los anuncios de la captura del día; «Carga
a Postgres», las filas cargadas ese día sumando todas las cargas (la de las 08:30, la que repite la
comprobación de las 09:00 y cualquier `make cargar` a mano). El día de la primera carga incluye el
histórico. Si Telegram no responde, se reintenta (respetando la
espera que pide con un 429) y, si aun así no se puede, queda anotado en `fuentes_estado` (fuente
`telegram`, tipo `aviso`, estado `error`) sin que la comprobación falle por ello. El token nunca se
escribe en logs ni en `fuentes_estado`.

Contraste con el CSV del scraper para un día (revisión de la ficha):

```bash
# El recuento de hoy en Postgres...
make recuento DIAS=1
# ...frente a las filas de la última captura del scraper (con csv: hay textos con saltos de línea)
for n in vo vn vn_otros; do
  python3 -c 'import csv,sys; print(sys.argv[2], sum(1 for _ in csv.DictReader(open(sys.argv[1], encoding="utf-8-sig"))))' \
    /opt/cdf/data/$n/exports/stock_latest.csv $n
done
```

## 6. Copias y prueba de restauración

```bash
make copia             # pg_dump cifrado a hz:$BUCKET/backups/postgres/cdf_AAAA-MM-DD.dump.gpg
make restaurar-prueba  # restaura la última copia en cdf_restore_test (sin red) y la compara con producción
                       # o una concreta: make restaurar-prueba DIA=2026-11-20
```

gpg trabaja con un `GNUPGHOME` temporal en `/tmp` que se borra al terminar (el cifrado es simétrico
con `BACKUP_PASSPHRASE`; no hay llavero), sin agente ni terminal y con la frase por un descriptor,
nunca en la línea de órdenes. En `cdf_jobs` el usuario `cdf` tiene `HOME=/home/cdf`, escribible.
Las dos usan el remoto `hz` de las variables `RCLONE_CONFIG_HZ_*`: `make copia` dentro del
contenedor, `make restaurar-prueba` en el host (carga el `.env`). La copia diaria la hace el cron a
las 09:30 y conserva 30 días en el bucket. Si falla, llega un aviso al grupo de Telegram con el paso
en el que falló (por ejemplo «CDF 15/11 · la copia de Postgres ha fallado / Paso: subida al bucket
(código 1)»), con el mismo envío que el resumen; si va bien, no llega nada. El aviso se envía aunque
Postgres no responda, y si no se puede enviar la copia sigue contando como fallida (código de salida
y `fuentes_estado`). rclone corre con `RCLONE_CONFIG=/dev/null`: sin fichero ni aviso de
`rclone.conf`, el remoto sale de las `RCLONE_CONFIG_HZ_*`. La prueba de restauración es manual: una vez al mes
(ficha del hito 0: «una copia restaurada»).

## 7. Panel (hito 1b)

El panel solo arranca si `PANEL_DOMINIO` tiene valor en el `.env`; si está vacío, `make deploy` lo
dice («el panel y Caddy no se arrancan») y todo lo demás sigue igual. Los pasos, en orden:

### 7.1 Registro DNS

En el proveedor del dominio, un registro **A** del subdominio del panel (por ejemplo
`panel.ejemplo.es`; el dominio principal no se toca) a la IPv4 del servidor, y si el servidor
tiene IPv6, un **AAAA** a esa IPv6. Comprobar desde el Mac:

```bash
dig +short panel.ejemplo.es A       # la IP del servidor
dig +short panel.ejemplo.es AAAA    # vacío o la IPv6 del servidor (nunca otra)
```

### 7.2 Puertos 80 y 443

- **Cortafuegos de Hetzner** (consola de Hetzner Cloud → Firewalls → el del servidor cdf → Inbound
  rules): añadir TCP 80, TCP 443 y UDP 443 (HTTP/3) desde cualquier origen. El 80 hace falta para
  el certificado de Let's Encrypt y para redirigir a HTTPS.
- **ufw** en el servidor:

```bash
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw allow 443/udp
sudo ufw status numbered
```

Ojo: Docker publica sus puertos por delante de ufw, así que el filtro que de verdad manda es el
cortafuegos de Hetzner. Por eso solo Caddy publica puertos (lo comprueban las pruebas y
`make panel-comprobar`).

### 7.3 Variables del .env

```bash
cat >> /opt/cdf/.env <<EOF
PANEL_DOMINIO=panel.ejemplo.es
PANEL_DB_PASSWORD=$(openssl rand -hex 32)
EOF
chmod 600 /opt/cdf/.env
```

`PANEL_SESION_DIAS` es opcional (días sin uso tras los que caduca una sesión; 30 si no está). El
nombre, la marca, la provincia y los umbrales del panel salen de `config/concesionario.yaml`.

### 7.4 Desplegar y precalcular

```bash
cd /opt/cdf/app/repo
make deploy            # migración 0003, rol cdf_panel, imagen del panel, arranque de panel y Caddy
make precalcular       # primera vez, y tras una migración que cambie las panel_*: rehace todo el histórico
                       # (cada día lo hace el cron a las 09:10 con los 8 últimos días)
make test              # las pruebas, también las del panel
```

`make deploy` termina con «Panel en https://panel.ejemplo.es». Caddy pide el certificado en el
primer arranque (unos segundos; `make panel-logs` lo cuenta).

### 7.5 Alta del primer usuario

```bash
make usuario-alta USUARIO=cayetano NOMBRE="Cayetano" ROL=tecnico
```

Pide dos veces una contraseña temporal (12 caracteres como mínimo; no se ve al escribirla). Al
entrar por primera vez, el panel obliga a cambiarla. Para el resto de personas, lo mismo con su
rol (`direccion`, `ventas`, `marketing` o `tecnico`), y la temporal se les da en persona o por
teléfono, nunca por el grupo de Telegram. Otras órdenes:

```bash
make usuarios                                          # lista
make usuario-rol USUARIO=ana ROL=marketing             # cambio de rol (inmediato)
make usuario-restablecer USUARIO=ana                   # contraseña temporal nueva; cierra sus sesiones
make usuario-baja USUARIO=ana                          # baja; cierra sus sesiones
```

### 7.6 Comprobaciones

```bash
make panel-comprobar
```

Debe mostrar:

- **Certificado**: `issuer` de Let's Encrypt, `subject` con el dominio y fechas vigentes.
- **Cabeceras** de `/acceso`: `strict-transport-security`, `content-security-policy` (con
  `frame-ancestors 'none'`), `x-content-type-options: nosniff`, `referrer-policy: same-origin`,
  `x-robots-tag: noindex…`, y las cookies `__Host-…` con `Secure; HttpOnly` y sin `Domain`.
- **http://** redirige (308) a **https://**.
- **Puertos publicados**: solo `cdf_caddy` con `0.0.0.0:80`, `0.0.0.0:443` (y su IPv6); `cdf_panel`,
  `cdf_postgres` y `cdf_jobs` sin ninguno.

Y además:

```bash
sudo ss -ltnp | grep -E ':(5432|8000)\b'      # no debe salir nada
curl -s https://panel.ejemplo.es/robots.txt   # User-agent: *  Disallow: /
curl -s https://panel.ejemplo.es/salud        # ok
make estado                                   # panel_mercado · precalculo · ok
```

Por último, entrar con el usuario técnico, cambiar la contraseña y abrir Mercado y «Estado de las
fuentes», en el ordenador y en el móvil.

## 8. Día a día

```bash
make estado            # ¿cargas, comprobaciones, avisos y copias en ok?
make logs              # registros de hoy de las tareas (/opt/cdf/logs/jobs/)
make hora              # hora del contenedor, usuario de las tareas y crontab
make panel-logs        # registros del panel y de Caddy
cdf-estado             # los scrapers, como hasta ahora
```

## Problemas frecuentes

- **El panel no arranca y `make deploy` dice «PANEL_DOMINIO vacío»**: falta `PANEL_DOMINIO` en el `.env` (7.3).
- **Caddy no consigue el certificado** (`make panel-logs`): el registro DNS no apunta aún al servidor
  o el 80/443 están cerrados en el cortafuegos de Hetzner (7.1 y 7.2).
- **Mercado dice «Todavía no hay datos»**: falta el precálculo; `make precalcular` (o esperar a las 09:10).
- **«Nuestros» sale con «—» en todos los modelos** justo después de desplegar la migración 0004: las
  filas son anteriores a la columna; `make precalcular` las rehace (o el cron de las 09:10, solo 8 días).
- **Un usuario bloqueado** tras 5 intentos: espera 15 minutos o, si ha olvidado la contraseña,
  `make usuario-restablecer USUARIO=...`.

- **`make deploy` dice que falta `RCLONE_CONFIG_HZ_TYPE`**: el remoto del bucket no está definido en
  el `.env` con variables. Copia las mismas `RCLONE_CONFIG_HZ_*` que usa `run_scraper.sh`.
- **`no es de cdf` en `make deploy`**: alguna carpeta de `/opt/cdf/data` no pertenece a `cdf`.
  `sudo chown -R cdf:cdf /opt/cdf/data/<carpeta>` con los scrapers parados.
- **`unable to open database file` al cargar**: el contenedor necesita poder crear el `-shm` de
  SQLite junto a la base. Comprueba con `make hora` que el UID/GID del contenedor es el de `id cdf`;
  si no, `make deploy` (reconstruye la imagen con el UID correcto).
- **No llega el resumen de Telegram**: `make estado` muestra la fila `telegram · aviso · error` con el
  motivo (por ejemplo, «HTTP 400 Bad Request: chat not found» si el `TELEGRAM_CHAT_ID` no es el del
  grupo, o «HTTP 403» si el bot no está en el grupo).
- **`sin_captura` en la comprobación**: el scraper no tiene pasada success de hoy. Mirar `cdf-estado`
  y `/opt/cdf/logs/scraper_<nombre>_<día>.log`. Cuando el scraper capture, `make cargar`.
- **Recuento que no cuadra**: no debería pasar, la carga se niega a cargar una pasada incompleta y lo
  anota en `fuentes_estado` como error. Revisar `make estado` y `make logs`.
