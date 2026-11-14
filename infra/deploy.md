# Despliegue en el servidor cdf (hito 0, segunda parte)

Todo se ejecuta en el servidor de Hetzner como usuario `cdf`. Claude no se conecta al servidor:
estas órdenes las lanza Cayetano. Los scrapers y sus bases no se tocan: el contenedor `cdf_jobs`
solo las lee (`mode=ro`).

## Qué se despliega

| Contenedor | Qué hace |
| --- | --- |
| `cdf_postgres` | Postgres 16, volumen `cdf_pgdata`, red `cdf_net`, **sin puerto publicado** |
| `cdf_jobs` | Python 3.12 con cron: carga 08:30, comprobación 09:00, copia 09:30 (hora de Madrid) |
| `cdf_panel`, `cdf_caddy` | Reservados para el hito 1b; no arrancan (perfil `hito1b`) |

## 0. Antes de empezar (en el Mac)

Revisar el diff de la rama `hito-0`, subirla a GitHub y fusionarla en `main`:

```bash
git push -u origin hito-0       # y abrir/fusionar el PR en GitHub
```

## 1. Paquetes del host (una vez)

```bash
sudo apt-get update && sudo apt-get install -y make git
docker compose version          # instalado por provision.sh
id -u cdf; id -g cdf            # el Makefile los usa para que el contenedor escriba como cdf
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

`BUCKET` (y Telegram, si lo usas) ya están. Se añaden los de Postgres y la frase de las copias,
con valores hexadecimales para que el fichero siga siendo válido para `run_scraper.sh`:

```bash
cat >> /opt/cdf/.env <<EOF
POSTGRES_USER=cdf
POSTGRES_PASSWORD=$(openssl rand -hex 32)
POSTGRES_DB=cdf
BACKUP_PASSPHRASE=$(openssl rand -hex 32)
RCLONE_REMOTE=hz
RCLONE_CONF_HOST=$(rclone config file | tail -n 1)
EOF
chmod 600 /opt/cdf/.env
grep BACKUP_PASSPHRASE /opt/cdf/.env    # cópiala a tu gestor de contraseñas: sin ella las copias no se descifran
```

Comprobar que el remoto del bucket es el mismo que usan los scrapers (`hz`):

```bash
set -a; . /opt/cdf/.env; set +a
rclone lsd "hz:$BUCKET"          # deben aparecer vo, vn y vn_otros
```

## 4. Desplegar

```bash
cd /opt/cdf/app/repo
make deploy        # comprueba el entorno, git pull, construye cdf_jobs, migra y arranca
make test          # pytest en el contenedor contra un Postgres desechable (cdf_postgres_test)
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
make comprobar         # anota en fuentes_estado si a algún scraper le falta la captura de hoy
make estado            # últimas anotaciones de cargas, comprobaciones y copias
```

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

La copia diaria la hace el cron a las 09:30 y conserva 30 días en el bucket. La prueba de
restauración es manual: una vez al mes (ficha del hito 0: «una copia restaurada»).

## 7. Día a día

```bash
make estado            # ¿cargas, comprobaciones y copias en ok?
make logs              # registros de hoy de las tareas (/opt/cdf/logs/jobs/)
cdf-estado             # los scrapers, como hasta ahora
```

## Problemas frecuentes

- **`make deploy` dice que falta la configuración de rclone**: `rclone config file` muestra dónde
  está; ponlo en `RCLONE_CONF_HOST`. Si no existe, Docker crearía una carpeta en su lugar: por eso
  se comprueba antes.
- **`unable to open database file` al cargar**: el contenedor necesita poder crear el fichero `-shm`
  de SQLite junto a la base. Comprueba que `/opt/cdf/data/{vo,vn,vn_otros}` son de `cdf` y que
  `CDF_UID` coincide con `id -u cdf`.
- **`sin_captura` en la comprobación**: el scraper no tiene pasada success de hoy. Mirar `cdf-estado`
  y `/opt/cdf/logs/scraper_<nombre>_<día>.log`. Cuando el scraper capture, `make cargar`.
- **Recuento que no cuadra**: no debería pasar, la carga se niega a cargar una pasada incompleta y lo
  anota en `fuentes_estado` como error. Revisar `make estado` y `make logs`.
