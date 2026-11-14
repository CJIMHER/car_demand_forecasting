# Despliegue en el servidor cdf (hito 0, segunda parte)

Todo se ejecuta en el servidor de Hetzner como usuario `cdf`. Claude no se conecta al servidor:
estas órdenes las lanza Cayetano. Los scrapers y sus bases no se tocan: el contenedor `cdf_jobs`
solo las lee (`mode=ro`).

## Qué se despliega

| Contenedor | Qué hace |
| --- | --- |
| `cdf_postgres` | Postgres 16, volumen `cdf_pgdata`, red interna `cdf_net`, **sin ningún puerto publicado** |
| `cdf_jobs` | Python 3.12 con cron en hora de Madrid: carga 08:30, comprobación y resumen por Telegram 09:00, copia 09:30 |
| `cdf_panel`, `cdf_caddy` | Reservados para el hito 1b; no arrancan (perfil `hito1b`) |

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

Revisar el diff de la rama `hito-0`, subirla a GitHub y fusionarla en `main`:

```bash
git push -u origin hito-0       # y abrir/fusionar el PR en GitHub
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
con errores). Las cifras salen de Postgres. Si Telegram no responde, se reintenta (respetando la
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
las 09:30 y conserva 30 días en el bucket. La prueba de restauración es manual: una vez al mes
(ficha del hito 0: «una copia restaurada»).

## 7. Día a día

```bash
make estado            # ¿cargas, comprobaciones, avisos y copias en ok?
make logs              # registros de hoy de las tareas (/opt/cdf/logs/jobs/)
make hora              # hora del contenedor, usuario de las tareas y crontab
cdf-estado             # los scrapers, como hasta ahora
```

## Problemas frecuentes

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
