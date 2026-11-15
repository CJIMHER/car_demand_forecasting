# Cambios

## 2026-11-15 · Hito 1b, sesión 2: contrato de datos y vistas Dirección, Ventas y Marketing (rama hito-1b)

- Encargo guardado en `docs/hitos/hito_1b_sesion_2.md`.
- Contrato de datos del panel en `docs/contrato_panel.md`, con la migración `0005`. Son 12 tablas
  `panel_*` (stock, ventas del mes, alertas, riesgo por vehículo, curvas de grupo, leads, gasto por
  campaña, salud de campañas, recomendación, atención relativa, semanas y campañas pasadas).
  - Todas llevan `dia`, `origen` ('real' o 'ejemplo') y `calculado_at`.
  - Ninguna tiene datos personales.
  - Las salidas de modelo llevan p10/p50/p90, `modelo_version` y `n`.
  - `cdf_panel` solo puede leerlas.
- `panel/contrato.py`: un bloque no mezcla orígenes.
  - Si cualquiera de sus tablas tiene una fila real, el bloque solo lee filas reales.
  - Si no, lee las de ejemplo; sin filas, dice «Sin datos todavía».
- `panel/permisos.py`: una vista con algún bloque de ejemplo solo la ven los roles de
  `PANEL_EJEMPLO_VISIBLE_PARA` (por defecto, `tecnico`). Los demás la ven desactivada; sin datos,
  está desactivada para todos.
- Vistas nuevas, con banda «Datos de ejemplo» arriba y la etiqueta en cada bloque de ejemplo:
  - **Dirección**: selector VN, VO o Todo; tarjetas; ventas acumuladas con la banda p10–p90;
    alertas con tema y destinatario; stock por días en exposición; tarjeta del mercado con datos
    reales.
  - **Ventas**: vehículos por riesgo con su intervalo, filtros y detalle con la curva de su grupo.
    Los botones están desactivados.
  - **Marketing**: tarjetas de 28 días, recomendación por modelo, campañas con su salud, atención
    frente a los rivales, cuándo lanzar una campaña general y qué dieron las campañas pasadas.
  - En el móvil, sin desplazamiento horizontal.
- `panel/ejemplo.py` y `make ejemplo-cargar` / `make ejemplo-borrar`: datos de ejemplo deterministas
  y coherentes entre vistas, con los modelos de `config/concesionario.yaml`.
  - Solo funcionan con `PANEL_PERMITE_EJEMPLO=1`.
  - Nunca tocan tablas de hechos, `panel_mercado_*` ni filas reales.
  - `make panel-dev` los carga.
- `config/concesionario.yaml`: `panel.objetivo_antiguedad_dias` (60) y `modelos` de la marca.
- Mercado:
  - el modelo sale sin la marca delante: se quita en el precálculo, y `make precalcular` rehace el
    histórico sin duplicar modelos;
  - fuera de la provincia propia, «Nuestros» muestra solo las unidades.
- `.env.example`: `PANEL_PERMITE_EJEMPLO` y `PANEL_EJEMPLO_VISIBLE_PARA`, sin valor. El compose pasa
  la segunda al panel; la primera, nunca.
- Pruebas: `tests/test_contrato_panel.py`, Makefile `ejemplo-*` y marca en el precálculo.
  `tests/navegador/capturas.mjs` revisa las tres vistas a 390 px y hace sus capturas.

## 2026-11-15 · Hito 1b: «Nuestros» y «Por concesionario» en Mercado (rama hito-1b)

- Tabla por modelo: columna «Nuestros» (anuncios del concesionario propio y, atenuado, su peso sobre
  el modelo si este tiene al menos `panel.n_minimo_porcentaje` anuncios). En el móvil, en la
  primera línea.
- Bloque «Por concesionario», solo en la provincia del concesionario propio: anuncios, variación a
  7 días en unidades (porcentaje con base ≥ umbral), peso sobre la provincia y precio mediano; el
  propio resaltado en ámbar. Nombre tal como lo publica la web (el más reciente). En el móvil, dos
  líneas por concesionario.
- Migración `0004`: `panel_mercado_modelo.propios` y tabla `panel_mercado_concesionario`
  (precálculo en `analitica/mercado.py`; `cdf_panel` solo la lee). Tras desplegar: `make precalcular`.
- Escenario sintético con un concesionario que no publica un día y otro que aparece nuevo.
- `tests/test_infra_panel.py`: las cuatro pruebas del Makefile y de `.env.example` se saltan, con
  su motivo, donde no hay make o no está el fichero (contenedor cdf_jobs); en el Mac se ejecutan.
- `tests/navegador/capturas.mjs` revisa a 390 px las seis combinaciones de zona y tipo y las dos tablas.

## 2026-11-15 · Hito 1b: ajustes de la vista Mercado (rama hito-1b)

- Móvil: en pantallas estrechas, cada modelo de la tabla ocupa dos líneas sin desplazamiento
  horizontal (arriba modelo, anuncios y variación; debajo, atenuado y con etiquetas, altas,
  ausencias, precio y días). En escritorio la tabla no cambia.
- «Frente a hace 7 días»: diferencia en anuncios con signo; el porcentaje, atenuado, solo si hace 7
  días había al menos `panel.n_minimo_porcentaje` anuncios (10, en `config/concesionario.yaml`).
- Sin la palabra «scraper» en la vista Mercado: «captura» en su lugar.
- `tests/navegador/capturas.mjs`: comprobación en Chrome a 390 px y capturas de `docs/capturas/`.

## 2026-11-15 · Hito 1b, sesión 1: base del panel y vista Mercado (rama hito-1b)

- Encargo guardado en `docs/hitos/hito_1b_sesion_1.md` (sustituye a la ficha de `docs/desarrollo.md`).
  Regla nueva en `CLAUDE.md`: replicable, una instancia por concesionario, nada propio de AVISA en
  el código ni en las plantillas.
- `config/concesionario.yaml` + `ingesta/concesionario.py`: nombre del panel, marca, provincia
  (zona por defecto) y umbral de medianas; comunidad autónoma desde la provincia (`provincias.COMUNIDADES`).
- Migración `0003`: tablas derivadas `panel_mercado_dia`, `panel_mercado_modelo` y
  `panel_mercado_capturas`; `panel_usuarios`, `panel_sesiones` y `panel_accesos`; rol `cdf_panel`
  (lee `panel_mercado_*` y `fuentes_estado`, escribe solo sus tablas, sin acceso a las de hechos);
  tipo `precalculo` en `fuentes_estado`.
- `analitica/mercado.py`: precálculo de Mercado (cron 09:10, `make precalcular`), con las reglas
  de capturas completas, altas observadas, ausencias y bajas, ventanas de 7 días y cotas
  inferiores; anota en `fuentes_estado` y avisa por Telegram si falla (`aviso_copia.avisar_texto`,
  generalizado sin cambiar el aviso de la copia).
- `panel/`: FastAPI + Jinja2 + htmx + ECharts (servidos desde el propio panel, con versión fija y
  licencia), plantillas y CSS traducidos del prototipo, escritorio y móvil con barra inferior.
  Acceso con argon2id, contraseña temporal con cambio obligatorio, sesión en Postgres con cookie
  `__Host-`, CSRF, bloqueo de 15 minutos tras 5 fallos por usuario y por IP, registro de accesos de
  90 días, permisos en `panel/permisos.py`. Vistas: Mercado (datos reales), Estado de las fuentes
  (técnico), Dirección/Ventas/Marketing desactivadas; `/salud`, `robots.txt` y cabeceras de seguridad.
- Despliegue: servicios `panel` y `caddy` (perfil `panel`, red `cdf_web`), Caddyfile, imagen del
  panel sin privilegios; `make deploy` solo los arranca con `PANEL_DOMINIO`; `make panel-rol`,
  `panel-comprobar`, `panel-logs`, `precalcular`, `usuario-*`, `usuarios` y `panel-dev` (Mac).
  Variables nuevas en `.env.example`. Guía en `infra/deploy.md` (sección 7).
- Pruebas: reglas estadísticas, acceso, matriz de permisos, CSRF, secretos fuera de logs, permisos
  de `cdf_panel`, cabeceras y recursos propios, cifras frente a SQL directo, concesionario ficticio
  de otra provincia, despliegue sin `PANEL_DOMINIO`. Capturas en `docs/capturas/`.

## 2026-11-15 · Hito 0: aviso de copia fallida, rclone sin aviso y total de carga (rama hito-0)

- `pg_backup.sh`: si la copia falla, aviso al grupo de Telegram (`ingesta/aviso_copia.py`, con
  `telegram.enviar`: texto plano, reintentos, token fuera de logs) con el paso y el código. Se envía
  antes de anotar, para que llegue aunque Postgres no responda; el script sale siempre con el código
  de la copia. Si va bien no se envía nada. Las variables obligatorias se comprueban dentro del trap.
- `RCLONE_CONFIG=/dev/null` en `pg_backup.sh`, `probar_restauracion.sh`, `tarea.sh` y la imagen: sin
  el aviso «Config file ... rclone.conf not found». El remoto sigue en `RCLONE_CONFIG_HZ_*`.
- Resumen diario: «Carga a Postgres» suma las filas de todas las cargas del día en `fuentes_estado`.

## 2026-11-14 · Hito 0: gpg sin HOME escribible (rama hito-0)

- `pg_backup.sh` y `probar_restauracion.sh`: `GNUPGHOME` temporal (`/tmp/cdf_gnupg.XXXXXX`, 700) que
  se borra al salir junto con su gpg-agent; `--batch --no-tty --pinentry-mode loopback
  --no-symkey-cache` y la frase por `--passphrase-fd`. Arregla `gpg: Fatal: can't create directory
  '/app/.gnupg'`, que habría hecho fallar la copia de las 09:30.
- Imagen `cdf_jobs`: el usuario `cdf` tiene `HOME=/home/cdf` (creado, suyo, 700) en vez de `/app`.
- Pruebas: `pg_backup.sh` con HOME de solo lectura y conexión solo por variables PG*; la frase no
  aparece en la salida y no queda nada en HOME ni en `/tmp`.

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
