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
ls -ln /opt/cdf/data/vw_details/exports   # exportación de colores del componente de fichas (se monta :ro)
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

### 7.7 Datos de ejemplo de Dirección, Ventas y Marketing (sesión 2)

Hasta que se conecten Walcu, Meta, GA4 y los modelos, estas tres vistas no tienen datos y aparecen
desactivadas («Se activa al conectar sus datos»). Para verlas con datos de ejemplo, marcados como
tales (`docs/contrato_panel.md`):

```bash
echo "PANEL_PERMITE_EJEMPLO=1" >> /opt/cdf/.env      # sin esto, make ejemplo-* se niega
# Opcional: roles que ven las vistas con datos de ejemplo (por defecto, solo tecnico)
# echo "PANEL_EJEMPLO_VISIBLE_PARA=tecnico,direccion" >> /opt/cdf/.env
make deploy                                          # migración 0005; el panel lee la variable nueva
make precalcular                                     # Mercado: modelos sin la marca delante (rehace el histórico)
make ejemplo-cargar                                  # filas origen = 'ejemplo' en las tablas del contrato
```

Con el usuario técnico se ven las tres vistas con la banda «Datos de ejemplo» y la etiqueta en cada
bloque. Dirección, ventas y marketing siguen viéndolas desactivadas salvo que estén en
`PANEL_EJEMPLO_VISIBLE_PARA`. Para quitarlas: `make ejemplo-borrar` (también pide
`PANEL_PERMITE_EJEMPLO=1`). Cuando una fuente escribe sus primeras filas reales, los bloques que la
leen dejan de mostrar las de ejemplo sin hacer nada más.

### 7.8 Marketing por tipo y estáticos con huella (ajustes de la sesión 2)

```bash
make deploy              # migración 0006: ámbito de campaña y tipo VN/VO en Marketing
make ejemplo-cargar      # solo si se usan datos de ejemplo: 0006 quita las filas de ejemplo
                         # de las cinco tablas que cambian (no tenían tipo) y hay que volver a escribirlas
```

La migración `0006` se detiene sin tocar nada si alguna de esas tablas tiene filas reales.

Tras cada despliegue el navegador usa el CSS, el JS, las fuentes y htmx/ECharts nuevos sin forzar la
recarga: sus URL llevan la huella del contenido (`?v=…`) y cambian cuando cambia el fichero. Las
páginas y las respuestas de htmx van con `Cache-Control: no-store`. Para comprobarlo:

```bash
curl -sI https://$PANEL_DOMINIO/acceso | grep -i cache-control              # no-store
curl -s https://$PANEL_DOMINIO/acceso | grep -o '/static/css/panel.css?v=[0-9a-f]*'
curl -sI "https://$PANEL_DOMINIO/static/css/panel.css?v=<huella>" | grep -i cache-control
                                                    # public, max-age=31536000, immutable
```

### 7.9 Alertas, botones de Ventas y conectores (sesión 3)

```bash
# 1. Chats de Telegram por rol (el bot tiene que estar en cada grupo). Sin uno, sus mensajes van al
#    chat del técnico (TELEGRAM_CHAT_ID) con el rol delante.
nano /opt/cdf/.env        # TELEGRAM_CHAT_DIRECCION, TELEGRAM_CHAT_VENTAS, TELEGRAM_CHAT_MARKETING

# 2. Carpeta de credenciales en fichero (GA4), aunque aún no haya ninguna: se monta en cdf_jobs
install -d -m 700 -o cdf -g cdf /opt/cdf/secretos

# 3. Desplegar (migración 0009) y rehacer el precálculo (rellena panel_mercado_competidor)
make deploy
make precalcular

# 4. Primera pasada del motor, sin enviar, y luego de verdad
SIN_TELEGRAM=1 make alertas
make alertas
make estado               # fuente «alertas» en ok
```

Desde ese momento el cron de `cdf_jobs` lanza `meta` (06:00), `ga4` (06:10), `alertas` (09:15) y
`avisos` (cada minuto: los botones de Ventas). Sin credenciales de Meta y GA4, sus tareas anotan
`sin_credenciales` y no hacen nada.

**Meta** (cuando se quiera activar):
1. En el Business Manager del concesionario: Configuración del negocio → Usuarios del sistema →
   añadir uno (rol Empleado), asignarle la cuenta publicitaria con permiso de solo ver
   rendimiento (no administrar) y la app del proyecto.
2. Generar su token con el permiso **`ads_read`** (ningún otro: ni `ads_management` ni
   `business_management`).
3. En `/opt/cdf/.env`: `META_ACCESS_TOKEN=<token>` y `META_AD_ACCOUNT_ID=<id de la cuenta>`. Comprobar
   la versión de la API en `config/campanas.yaml` (`meta.version_api`) y `make meta`.

**GA4**:
1. En Google Cloud (proyecto del concesionario): activar la **Google Analytics Data API** y crear una
   cuenta de servicio sin roles de proyecto; crear una clave JSON.
2. En GA4 → Administrar → Gestión de acceso a la propiedad: añadir el correo de la cuenta de servicio
   con el rol **Lector** (sin permisos de datos de coste ni de ingresos si no hacen falta).
3. Copiar la clave al servidor: `install -m 600 -o cdf -g cdf clave.json /opt/cdf/secretos/ga4.json`
   (y borrarla del Mac). En `/opt/cdf/.env`: `GA4_PROPERTY_ID=<id numérico>` y
   `GA4_CREDENCIALES=/secretos/ga4.json`. Luego `make ga4`.

Las campañas se clasifican por su nombre con las reglas de `config/campanas.yaml` («VW [VN][2026] Taigo
noviembre», «VW [VO][2026] Black Friday»…). Las que salgan en «Estado de las fuentes» → «Campañas sin
regla», con su motivo: renombrarlas en Meta o añadir su asignación a mano en `config/campanas.yaml`
(`asignaciones`), desplegar y `make campanas`.

Solo salen las campañas de Meta: de GA4, las que llegan desde Meta o con el nombre o el ID de una
campaña de Meta. El resto del tráfico de GA4 se cuenta por canal con `config/canales.yaml` (Marketing →
«Tráfico de la web por canal»). Las fuentes y medios que caen en «Otros» salen en «Estado de las
fuentes»: añadirlas a su canal, desplegar y `make campanas`. Tras desplegar la migración `0015`, las
filas de GA4 ya descargadas no tienen el ID de campaña ni la agrupación de canales de GA4 hasta que
se recargan: `make ga4` recarga los últimos 7 días y rehace las tablas (lo mismo hace el cron cada
noche). Para días más antiguos, `python -m ingesta.ga4 --desde … --hasta …` dentro de `cdf_jobs`.

### 7.10 Criterios de las campañas de Meta

```bash
make deploy          # migración 0010: zona cercana, decisión de cada campaña, recuento de código postal
make precalcular     # rellena la zona cercana y las medianas propia y de la competencia (regla e)
make alertas         # primera recomendación por modelo con la regla e
```

Las provincias de la zona cercana están en `config/concesionario.yaml` (`propio.provincias_cercanas`).
Para recibir por Telegram también las alertas calculadas sobre datos de ejemplo: `ALERTAS_ENVIAR_EJEMPLO=1`
en `/opt/cdf/.env` (por defecto, no).

### 7.11 Google Trends (descarga manual, una vez al mes)

Google Trends se descarga **a mano** desde el perfil de Chrome del proyecto: el código nunca accede a
Google. Zona (España), periodo (12 meses, datos semanales) y categoría (Autos y vehículos, 47) están en
`config/gtrends.yaml`; las comparaciones salen de `data/competidores.csv`.

Una vez, en el servidor:

```bash
make deploy          # migración 0016, carpeta /opt/cdf/entrada/gtrends y cron de carga a las 07:00
```

Una vez, en el Mac: en el `.env` del repositorio (no se sube), `CDF_SERVIDOR_IP=<ip del servidor>`; la
clave `~/.ssh/cdf_hetzner` debe entrar como `cdf` (`ssh -i ~/.ssh/cdf_hetzner cdf@<ip>`).

Cada mes (el técnico recibe «Toca descargar Google Trends» si la última descarga tiene más de 35 días):

1. En el Mac: `make gtrends-enlaces`. Abre `salidas/gtrends_enlaces.html`.
2. En el perfil de Chrome del proyecto, para cada comparación: «Abrir en Google Trends» → en «Interés a
   lo largo del tiempo», el botón de descarga. Todos los CSV a una misma carpeta (los nombres dan igual)
   y marcar la casilla. La página dice cuántas faltan.
3. En el Mac: `make gtrends-subir CARPETA=~/Downloads/trends-noviembre`. Comprueba cada fichero (por sus
   términos, no por el nombre): categoría, zona, semanal y unas 52 semanas. Si algo falla, dice qué
   comparación volver a descargar y no sube nada. `SOLO_COMPROBAR=1` comprueba sin subir. No borra nada
   de la carpeta.
4. En el servidor, la carga es automática a las 07:00; para verla ya: `make gtrends`. Repetirla no
   duplica. Marketing → «Atención frente a sus rivales» pasa a real, con la fecha de la descarga.

### 7.12 Histórico de Meta, Walcu agregado por semanas y referencias de coste

Una vez, en el servidor:

```bash
make deploy                                  # migraciones 0017, 0018 y 0019 (Walcu por semanas)
make meta-historico DESDE=2025-01-01         # hasta ayer; por meses, con archivadas y borradas. Se puede relanzar
```

Si se corta (límite de la API, red), relanzar la misma orden: no duplica filas. `make estado` muestra la
anotación de `meta` con los tramos.

La 0019 borra `walcu_campana_mes` (vacía: la carga mensual no llegó a usarse) y la sustituye por
`walcu_campana_semana`; `panel_campana_mes` pasa a `panel_campana_semana`. Su `downgrade` recrea las
mensuales, vacías. Después, la carga del histórico semanal (una vez, desde el Mac):

```bash
scp -i ~/.ssh/cdf_hetzner ~/cdf_walcu/walcu_semanal_historico.csv cdf@<ip>:/opt/cdf/entrada/
# en el servidor
make walcu-cargar FICHERO=/opt/cdf/entrada/walcu_semanal_historico.csv     # 1.373 filas, 100 semanas
```

`walcu_agregado_historico.csv` (mensual) ya no se carga: `make walcu-cargar` lo rechaza (columna «mes»).

Cada semana:

1. En el Mac: exportar los leads de Walcu (xlsx o csv) y
   `make walcu-agregar ENTRADA=~/Descargas/leads.xlsx SALIDA=~/Descargas/walcu_semanal.csv`. Agrega por
   semana del lead (lunes de la semana ISO). Exportar al menos los últimos 60 días: las citas y las ventas
   de un lead cuentan en la semana del lead y llegan después; recargar una semana la sustituye. Descarta los
   leads de los comerciales de `~/cdf_walcu/comerciales_excluidos.txt` (solo en el Mac; nunca en el
   repositorio ni en el servidor). La primera vez, revisar los estados, tipos y motivos que escribe y
   ajustar `config/walcu_export.yaml`.
2. Copiar el CSV al servidor: `scp -i ~/.ssh/cdf_hetzner ~/Descargas/walcu_semanal.csv cdf@<ip>:/opt/cdf/entrada/`.
3. En el servidor: `make walcu-cargar FICHERO=/opt/cdf/entrada/walcu_semanal.csv`. Si el fichero trae otra
   columna, una semana que no es lunes o algo con aspecto de correo, teléfono o DNI, lo rechaza entero y no
   carga nada. Si no se cumple ventas ≤ citas ≤ leads o las zonas no suman los leads, avisa y carga.
4. `make referencias-meta`: tabla por año y tipo y lo que no casa entre Meta y Walcu. Para lo que no casa,
   equivalencias en `config/campanas_walcu.yaml` (commit, `make deploy`) y otra vez `make referencias-meta`.
5. `SIN_TELEGRAM=1 make alertas` para ver las alertas recalculadas sin enviarlas (o esperar al cron de las
   09:15). Marketing → «Campañas de las últimas 4 semanas» (con las semanas incompletas marcadas) y
   «Referencias históricas».

### 7.13 Precio provisional de nuestros VO

Una vez, en el servidor:

```bash
make deploy          # migración 0020 (panel_precio_vo) y la imagen del panel con el bloque nuevo
make precalcular     # rehace Mercado y rellena el precio de nuestros VO de todo el histórico
```

Después lo mantiene el cron del precálculo (09:10). Los umbrales están en `config/concesionario.yaml`
(`precio_provisional`); tras cambiarlos, commit, `make deploy` y `make precalcular`. Ventas y Dirección →
«Precio de nuestros VO». Si hay datos de ejemplo cargados en Ventas (`make ejemplo-cargar`), la vista sigue
siendo «de ejemplo» y el rol de ventas no la ve hasta `make ejemplo-borrar` (7.7).

## 8. Día a día

```bash
make estado            # ¿cargas, comprobaciones, avisos y copias en ok?
make logs              # registros de hoy de las tareas (/opt/cdf/logs/jobs/)
make hora              # hora del contenedor, usuario de las tareas y crontab
make panel-logs        # registros del panel y de Caddy
cdf-estado             # los scrapers, como hasta ahora
```

## Problemas frecuentes

- **Tras desplegar, una vista sale sin estilos o sin gráficos.** Pasó tras la sesión 2: el CSS y el
  JS se guardaban un día con la misma URL. Desde la 7.8 sus URL llevan huella. Si vuelve a pasar,
  comprobar con los `curl` de la 7.8 que la página cita `?v=` y que Caddy no añade cabeceras de caché
  (`infra/caddy/Caddyfile`: solo `header -Server`).

- **El panel no arranca y `make deploy` dice «PANEL_DOMINIO vacío»**: falta `PANEL_DOMINIO` en el `.env` (7.3).
- **Caddy no consigue el certificado** (`make panel-logs`): el registro DNS no apunta aún al servidor
  o el 80/443 están cerrados en el cortafuegos de Hetzner (7.1 y 7.2).
- **Mercado dice «Todavía no hay datos»**: falta el precálculo; `make precalcular` (o esperar a las 09:10).
- **«Nuestros» sale con «—» en todos los modelos** justo después de desplegar la migración 0004: las
  filas son anteriores a la columna; `make precalcular` las rehace (o el cron de las 09:10, solo 8 días).
- **Dirección, Ventas o Marketing salen desactivadas para el usuario técnico**: no hay datos (ni reales
  ni de ejemplo); `make ejemplo-cargar` (7.7). Para el resto de roles es lo esperado mientras sean de ejemplo.
- **«make ejemplo-cargar» dice que falta PANEL_PERMITE_EJEMPLO=1**: añádelo al `.env` (7.7).
- **En Mercado sigue saliendo «Volkswagen T-Cross»** tras desplegar: las filas son del precálculo
  anterior; `make precalcular` rehace el histórico.
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
