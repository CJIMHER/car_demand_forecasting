# Cambios

## 2026-11-14 · Hito 0: make test en el contenedor (rama hito-0)

- `ingesta/db.py`: `url_sqlalchemy()` convierte una cadena libpq «clave=valor» en una URL
  `postgresql+psycopg://` completa (host, puerto, usuario, contraseña escapada, base y parámetros
  extra), completando con las variables PG*. Arregla los 17 errores de `make test` en el servidor
  (Alembic recibía `dbname=cdf_test_...`). La conexión de producción no cambia.
- `tests/test_db.py`: la conversión en los dos formatos (libpq del contenedor y URL del Mac).

## 2026-11-14 · Hito 0: resumen por Telegram y ajustes de despliegue (rama hito-0)

- Resumen diario por Telegram tras la comprobación de las 09:00 (`ingesta/telegram.py`): texto
  plano con una línea por scraper y la carga a Postgres; reintentos ante 429 y fallos de red; el
  token nunca sale en logs ni errores; un fallo de envío se anota en `fuentes_estado` sin hacer
  fallar el job. Migración `0002`: tipo `aviso` en `fuentes_estado`.
- rclone por variables `RCLONE_CONFIG_HZ_*` del `.env` (sin `rclone.conf`): el contenedor recibe el
  `.env` como `env_file` y cron solo la lista de `exportar_entorno.sh`.
- Hora de Madrid comprobada al arrancar `cdf_jobs` (tzdata, `/etc/localtime`, CET/CEST).
- `cdf_jobs` con el UID/GID de `cdf` obligatorio (sin valor por defecto); todo salvo el demonio de
  cron corre como `cdf`, y la carga se niega a abrir las bases de los scrapers como root.
- Postgres sin puertos, comprobado en pruebas. `make hora` y comprobaciones nuevas en
  `make comprobar-entorno`.

## 2026-11-14 · Hito 0, segunda parte (rama hito-0)

- `CLAUDE.md` con las reglas comunes; estructura de módulos con su README (`ingesta`, `analitica`,
  `alertas`, `panel`, `infra`).
- `infra/docker-compose.yml`: `cdf_postgres` (Postgres 16, sin puerto publicado) y `cdf_jobs`
  (Python 3.12 con cron); `cdf_panel` y `cdf_caddy` reservados con el perfil `hito1b`. `.env.example`.
- Migración Alembic `0001`: `scraper_runs`, `stock_externo_snapshot` (con `es_propio`),
  `eventos_externos`, `anuncios_externos` y `fuentes_estado`; disparadores que impiden UPDATE,
  DELETE y TRUNCATE en las tablas de hechos.
- `ingesta/scrapers_vw.py`: carga idempotente a las 08:30 de las bases SQLite de VO, VN y VN otros en
  solo lectura; provincia desde el código postal; comprobación a las 09:00 en `fuentes_estado`.
- `config/concesionario.yaml`: AVISA (ESP00710) como propio; Sevilla Wagen (ESP00713) y Utrewal
  (ESP00714) como competencia.
- Copias: `pg_dump` diario cifrado a `backups/postgres/` del bucket con 30 días de retención y
  `probar_restauracion.sh` en un contenedor aparte.
- `Makefile` e `infra/deploy.md` para desplegar en el servidor.
- Pruebas con bases SQLite sintéticas escritas con el código real de los scrapers.
