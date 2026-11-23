# ingesta

Carga de las fuentes a Postgres. En el hito 0, solo los scrapers de Volkswagen.

## scrapers_vw.py

Lee las tres bases SQLite de los scrapers **en solo lectura** (`mode=ro`) y copia a Postgres lo
nuevo desde la última carga.

| Fuente | Base en el servidor | tipo_stock | Ámbito esperado |
| --- | --- | --- | --- |
| `vo` | `/opt/cdf/data/vo/vw_stock.sqlite3` | VO | `volkswagen-es-used-BQ-U-passenger-v1` |
| `vn` | `/opt/cdf/data/vn/vw_vn_stock.sqlite3` | VN | `volkswagen-es-new-BQ-N-fuel-B-D-passenger-v1` |
| `vn_otros` | `/opt/cdf/data/vn_otros/vw_vn_stock.sqlite3` | VN | `volkswagen-es-new-BQ-N-fuel-E-H-passenger-v1` |

Por cada pasada `success` que aún no está en `scraper_runs`, en una sola transacción:

1. anota la pasada en `scraper_runs` (clave `fuente, run_id`);
2. copia sus observaciones a `stock_externo_snapshot` (clave única `fuente, run_id, id_anuncio`);
3. copia sus eventos a `eventos_externos` (clave `fuente, event_id`);
4. comprueba que las filas cargadas son exactamente las `observed_count` del scraper; si no, no carga nada.

Después recalcula `anuncios_externos` (primer y último avistamiento, ausente desde, estado y
reapariciones) a partir de snapshots y eventos. Los estados son `presente`, `ausente` (evento
`not_seen`) y `retirado` (evento `removed`, tres ausencias): **desaparecer no es vender**, y no
existe el estado vendido.

Columnas de `stock_externo_snapshot`: modelo, versión, combustible, año (de modelo), fecha de
matriculación, km (vacío si no se publica, nunca cero), precio en céntimos y su etiqueta, estado de
entrega (VN), bastidor, concesionario (`dealer_id`, nombre, ciudad, código postal) y la provincia
derivada del código postal (`provincias.py`). El resto del registro va en `datos` (jsonb), salvo las
URL de las fotos, que siguen en la base del scraper y en los brutos del bucket.

`es_propio` marca los anuncios de los `dealer_id` de `config/concesionario.yaml` (el concesionario de esta instancia).

Las tablas de hechos (`scraper_runs`, `stock_externo_snapshot`, `eventos_externos`,
`fuentes_estado`) rechazan UPDATE, DELETE y TRUNCATE con un disparador. La única excepción es
`es_propio`, que se recalcula con `remarcar-propio` si cambia el YAML.

Órdenes (en el servidor, a través del Makefile):

```bash
make cargar          # python -m ingesta.scrapers_vw cargar         (cron 08:30)
make comprobar       # python -m ingesta.scrapers_vw comprobar      (cron 09:00)
make recuento        # anuncios por día y fuente, frente al observed_count del scraper
make concesionarios  # cómo aparece el concesionario propio en los datos
make remarcar-propio
```

La comprobación de las 09:00 primero carga lo que falte (por si un reintento terminó después de las
08:30) y luego escribe en `fuentes_estado`, por fuente: `ok`, `sin_captura` (el scraper no tiene
pasada success hoy), `sin_cargar` o `error` (base ausente, ámbito equivocado...).

## Color de los anuncios (colores.py)

El componente de fichas (independiente de los scrapers) deja el color de cada anuncio en
`/opt/cdf/data/vw_details/exports/colors_latest.jsonl.gz`, una línea JSON por anuncio (`source_name`,
`vehicle_id`, `vin`, `status`, `exterior_color`, `interior_color`, `fetched_at`…). Tras la carga del stock
de las 08:30 (`make cargar`), `colores.py` lee esa exportación **en solo lectura** (el compose monta solo la
carpeta `exports`, `:ro`; la caché `colors.sqlite3` no se monta) y añade a `color_anuncio` las líneas cuyo
estado, VIN, colores o `fetched_at` no estaban ya para ese anuncio, con los colores tal cual. Tabla de solo
añadir; el panel no la lee.

No toca `stock_externo_snapshot`, `eventos_externos` ni `anuncios_externos`: el color no es un cambio del
anuncio. La unión con el stock se hace en el precálculo (`analitica/mercado.py`). Se anota en
`fuentes_estado` (fuente `colores`, tipo `carga`): `ok`, `sin_captura` si no existe el fichero o `error` si
no se puede leer; en los dos últimos casos la carga del stock sigue y su resultado no cambia. El resumen de
Telegram de las 09:00 cuenta solo las cargas de los scrapers.

## Resumen diario por Telegram (telegram.py)

Al terminar la comprobación de las 09:00 se envía siempre, vaya bien o mal, un mensaje de texto
plano al grupo (`sendMessage` de la Bot API, sin `parse_mode`, sin ningún modelo por medio):

```
CDF 14/11 · todo correcto
VO: 3.076 anuncios
VN: 4.798 anuncios
VN eléctricos e híbridos: 419 anuncios
Carga a Postgres: correcta (8.293 filas)
```

Las cifras salen de Postgres: por scraper, los anuncios de la captura del día
(`stock_externo_snapshot`); en «Carga a Postgres», las filas cargadas ese día sumando todas las
anotaciones `carga` correctas del día en `fuentes_estado` (08:30, la recarga de las 09:00 y las
manuales); el estado de cada carga, de su última anotación del día. Con problemas, la cabecera los cuenta
(«1 problema», «2 problemas») y cada línea afectada dice cuál.

- `TELEGRAM_BOT_TOKEN` y `TELEGRAM_CHAT_ID` salen del entorno (del `/opt/cdf/.env`).
- Reintentos con espera ante 429 (respetando `retry_after`), 5xx o fallos de red; un 400/401/403 no
  se reintenta.
- El token va en la URL de la Bot API: los errores se relanzan con un mensaje propio y sin la
  excepción original, nunca se escribe una traza, y `telegram.limpiar()` lo borra de cualquier texto.
- Si no se puede enviar, se anota en `fuentes_estado` (`telegram`, `aviso`, `error`) y la
  comprobación no falla por ello. Si se envía, la fila `ok` guarda el texto enviado.
- `make comprobar` también lo envía; `SIN_TELEGRAM=1 make comprobar` no.

## Aviso de copia fallida (aviso_copia.py)

`infra/backup/pg_backup.sh` lo llama desde su trap solo si la copia falla
(`python -m ingesta.aviso_copia --codigo N --paso "..."`). Mismo envío que el resumen
(`telegram.enviar`). Primero envía y después intenta anotar en `fuentes_estado` (`telegram`,
`aviso`, con `"aviso": "copia"` en el detalle), así que el aviso llega aunque Postgres esté caído.
Nunca lanza; el script sale siempre con el código de la copia.

## Conectores de Meta y GA4 (meta.py, ga4.py, conectores.py, campanas.py)

Inactivos hasta tener credenciales: sin ellas no hacen ninguna petición y anotan `sin_credenciales`
en `fuentes_estado` (tipo `descarga`), que no es un error y no dispara ninguna alerta.

| | Meta Marketing API (`meta.py`, cron 06:00) | GA4 Data API (`ga4.py`, cron 06:10) |
| --- | --- | --- |
| Acceso | Solo lectura: token con `ads_read` en `META_ACCESS_TOKEN`, cuenta en `META_AD_ACCOUNT_ID` | Solo lectura: cuenta de servicio con rol Lector; propiedad en `GA4_PROPERTY_ID`, fichero JSON en `GA4_CREDENCIALES` (fuera del repositorio, permisos 600) |
| Qué | Insights diarios por campaña, conjunto y anuncio: gasto, impresiones, alcance, frecuencia, clics, CPM y acciones de lead | Por día, fuente, medio, campaña y página de destino (solo la ruta): sesiones, sesiones con interacción y eventos clave |
| Tabla | `campanas_meta_dia` (vista `campanas_meta_dia_vigente`) | `ga4_trafico_dia` (vista `ga4_trafico_dia_vigente`) |

Comunes (`conectores.py`):
- **Recarga de 7 días cada noche**, porque Meta y GA4 corrigen los días pasados. Las tablas son de
  solo añadir: una fila se añade solo si su clave es nueva o su contenido (huella) ha cambiado. Así,
  una corrección queda como versión nueva sin duplicar lo que no cambia; la vista `*_vigente` da la
  última de cada clave. Cada fila lleva `descargado_at`, `run_id` y `origen` (API y versión).
- **Brutos**: las respuestas, comprimidas, al bucket (`<fuente>/raw/<día>/`) antes de leerlas. Si no
  se pueden subir, no se escribe nada (la noche siguiente recarga los 7 días).
- **Límites**: un 429 (o el código de límite de cada API) espera `Retry-After` o 5, 15, 45, 90 y
  180 s, y reintenta; otro error no se reintenta. El token nunca va en una URL ni en un error o un log.
- **Nada personal**: lista blanca de campos; de la página de destino, solo la ruta (sin parámetros).
- GA4 firma el JWT de la cuenta de servicio con `cryptography` (RS256), que solo se carga con credenciales.

`campanas.py` aplica `config/campanas.yaml`: la regla de unión (Meta, GA4 y los leads de Walcu por
`utm_campaign`, normalizado) y las reglas que leen el nombre de cada campaña (sin mayúsculas, tildes ni
espacios de más):

- ámbito por la etiqueta: [VN] → VN, [VO] → VO, sin etiqueta → ambos;
- general si el nombre contiene una palabra de `reglas.generales` (days, friday, liquidación, boutique),
  con su ámbito («VW [VO][2026] Black Friday»: general de VO);
- si no, de modelo: lo que va entre el año y el mes («VW [VN][2026] Taigo Noviembre 2» → Taigo; lo que
  sigue al mes se ignora), normalizado con el catálogo de `config/versiones.yaml`. Un modelo fuera del
  catálogo (Caravelle) se asigna igual, marcado «sin datos de mercado»;
- las asignaciones a mano de `asignaciones` mandan sobre las reglas.

Tras cada descarga de Meta o GA4 (y con `make campanas`) rehace `panel_campana_dia` (origen real) con
las campañas de Meta clasificadas, y `panel_campanas_sin_regla` con las de Meta y GA4 que no casan y
el motivo, para «Estado de las fuentes». Una campaña de GA4 que se une a una de Meta por
`utm_campaign` se clasifica como ella. Pruebas: `tests/test_campanas.py`.

    make meta      make ga4      (lo mismo que el cron; DESDE/HASTA a mano: python -m ingesta.meta --desde … --hasta …)

## Concesionario de la instancia (concesionario.py)

`ingesta/concesionario.py` lee `config/concesionario.yaml` (o el fichero de `CDF_CONCESIONARIO`):
nombre, nombre del panel, marca, `dealer_ids`, provincia (código INE) y umbrales del panel. La
comunidad autónoma sale de la provincia (`provincias.COMUNIDADES`). Una instancia, un
concesionario: nada de esto va escrito en el código.

## Cómo se prueba

Con bases SQLite sintéticas que se escriben con el **propio código de los scrapers** (sus funciones
`connect_db`, `start_run` y `commit_snapshot`, importadas sin modificarlas): mismos esquemas,
eventos y ausencias que en el servidor, sin ningún dato real. Telegram nunca se llama en las
pruebas: `tests/conftest.py` borra las credenciales y bloquea la salida a la red del módulo, y las
pruebas de envío usan un Telegram simulado (429, red caída, 400...).

```bash
# En el Mac (Postgres 16 embebido con pgserver)
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt pgserver==0.1.4
.venv/bin/python -m pytest

# En el servidor (Postgres desechable cdf_postgres_test)
make test
```
