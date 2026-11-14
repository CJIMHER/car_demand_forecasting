# infra

Todo lo que corre en el servidor de Hetzner del proyecto `cdf`.

| Ruta | Qué es |
| --- | --- |
| `provision.sh` | Preparación inicial del Ubuntu (usuario cdf, SSH solo con clave, ufw, Docker). Hecho. |
| `cdf_scrapers/` | Instalación de los scrapers VO, VN y VN otros con systemd. Hecho; no se modifica aquí. |
| `docker-compose.yml` | `cdf_postgres` (sin puerto publicado) y `cdf_jobs` (Python 3.12 con cron); `cdf_panel` y `cdf_caddy` reservados. Red `cdf_net`, volumen `cdf_pgdata`. |
| `jobs/` | Imagen de `cdf_jobs` (UID/GID de `cdf`, hora de Madrid), su crontab (08:30 carga, 09:00 comprobación y resumen por Telegram, 09:30 copia), el lanzador `tarea.sh` y `exportar_entorno.sh`, que da a cron solo las variables que necesita (incluidas las `RCLONE_CONFIG_HZ_*`). |
| `migraciones/` | Migraciones Alembic (`alembic.ini` en la raíz). |
| `backup/pg_backup.sh` | `pg_dump` diario cifrado (gpg AES-256) a `backups/postgres/` del bucket, 30 días de retención. |
| `backup/probar_restauracion.sh` | Restaura una copia en un contenedor aparte sin red y la compara con producción. |
| `deploy.md` | Pasos de despliegue y operación para el servidor. |

Las órdenes se lanzan con el `Makefile` de la raíz (`make help`).

## Cómo se prueba

- `make test` en el servidor: pytest dentro de `cdf_jobs` contra un Postgres desechable.
- Las migraciones se aplican en cada prueba sobre una base vacía (`tests/conftest.py`).
- `tests/test_copias.py` ejecuta `pg_backup.sh` de verdad (pg_dump + gpg) con un `rclone` simulado,
  descifra la copia y la restaura en otra base. La parte Docker de `probar_restauracion.sh` solo se
  prueba en el servidor con `make restaurar-prueba`.
