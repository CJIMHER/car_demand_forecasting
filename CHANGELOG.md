# Cambios

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
