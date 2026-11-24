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

## Google Trends (gtrends.py), descarga manual

Google Trends se descarga a mano, una vez al mes, desde el perfil de Chrome del proyecto. El código
nunca accede a Google (ni pytrends, ni navegadores automáticos, ni peticiones a trends.google.com);
automatiza el resto. Configuración en `config/gtrends.yaml` (zona, periodo, categoría 47 «Autos y
vehículos», semanas esperadas); comparaciones de `data/competidores.csv`: el modelo propio y hasta 4
competidores (5 términos, el límite de Trends); con más, varias comparaciones con el modelo como ancla.

| Orden | Dónde | Qué hace |
| --- | --- | --- |
| `make gtrends-enlaces` | Mac | `salidas/gtrends_enlaces.html`: un enlace por comparación, casilla de descargada y cuántas faltan |
| `make gtrends-subir CARPETA=…` | Mac | Identifica cada CSV por sus términos y comprueba categoría, zona, semanal y unas 52 semanas. Avisa de las que faltan, las repetidas y los ficheros que no casan. Si todo cuadra, sube los originales (como `<comparación>.csv`) y `manifiesto.json` con scp a `/opt/cdf/entrada/gtrends/<fecha>/` (clave `~/.ssh/cdf_hetzner`, usuario cdf; IP de `IP=` o de `CDF_SERVIDOR_IP` del `.env` del Mac). Primero a `<fecha>.subiendo` y luego se renombra |
| `make gtrends` (y cron 07:00) | Servidor | Carga la última entrega: comprueba las huellas del manifiesto, sube los originales comprimidos al bucket (`gtrends/raw/<fecha>/`), los añade a `gtrends_entrega` y `gtrends_semana` (solo añadir; repetir no duplica) y rehace `panel_atencion_relativa` (real) |

«<1» se guarda como 0,5 con `menor_que_1`. Si la última entrega tiene más de 35 días, el técnico recibe
«Toca descargar Google Trends» (regla `gtrends_pendiente`). Pruebas: `tests/test_gtrends.py`.

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

- ámbito por la etiqueta: [VN] → VN, [VO] → VO; sin etiqueta, por las palabras enteras «ocasión»,
  «ocasion» o «VO» (→ VO) y «nuevos» o «VN» (→ VN); sin ninguna (o con las de los dos), ambos;
- general si el nombre contiene una palabra de `reglas.generales` (days, friday, liquidación, boutique),
  con su ámbito («VW [VO][2026] Black Friday»: general de VO);
- si no, de modelo: lo que va entre el año y el mes («VW [VN][2026] Taigo Noviembre 2» → Taigo; lo que
  sigue al mes se ignora), normalizado con el catálogo de `config/versiones.yaml`. Un modelo fuera del
  catálogo (Caravelle) se asigna igual, marcado «sin datos de mercado»;
- las asignaciones a mano de `asignaciones` mandan sobre las reglas.

Tras cada descarga de Meta o GA4 (y con `make campanas`) rehace `panel_campana_dia` (origen real) con
las campañas de Meta clasificadas (con los leads que cuenta Meta en `leads_meta`), y `panel_campanas_sin_regla` con las de Meta y GA4 que no casan y
el motivo, para «Estado de las fuentes». De GA4 solo se intentan clasificar las campañas de Meta: las
filas del canal Meta o con el nombre o el ID de una campaña descargada de Meta. Los valores reservados
de GA4 ((direct), (not set), (cross-network)…) no son campañas. Con `config/canales.yaml`
(`canales.py`) rehace también el tráfico por canal y tipo de página (`panel_trafico_canal_dia`), las
fuentes y medios que caen en «otros» (`panel_trafico_otros`) y las campañas de Google Ads, con su nombre
y sin reglas (`panel_campanas_google_ads`). GA4 da además el ID de campaña y su agrupación de canales
por defecto (`campana_id`, `canal_ga4`), respaldo de las reglas. Una campaña de GA4 que se une a una de Meta por
`utm_campaign` se clasifica como ella. En `panel_campanas_sin_regla` hay una fila por fuente y campaña: si
GA4 trae la misma campaña con otro ID, fuente o medio, se juntan (días extremos y sesiones sumadas). La
descarga de Meta o GA4 se confirma antes de rehacer estas tablas: si rehacerlas falla, la descarga queda
«ok» con `aviso_campanas` en su detalle y un aviso en el log. Pruebas: `tests/test_campanas.py`.

    make meta      make ga4      (lo mismo que el cron; DESDE/HASTA a mano: python -m ingesta.meta --desde … --hasta …)

### Histórico de Meta (meta.py --historico)

`make meta-historico DESDE=2025-01-01 [HASTA=AAAA-MM-DD]` (por defecto, hasta ayer): mismo token y misma
cuenta (`ads_read`, solo lectura). Solo el nivel de campaña, por día, en tramos de un mes, con las campañas
en cualquier estado (filtro `campaign.effective_status` con todos, también ARCHIVED y DELETED). Leads: la
acción `lead` o, si no está, `onsite_conversion.lead_grouped` (la mayor de las de lead), como la carga
diaria. Nombre y estado de cada campaña en `campanas_meta_estado` (solo añadir; una fila nueva si cambia).
Límites: además del 429, lee `x-business-use-case-usage` y espera 60 s desde el 75 % de uso, 300 s desde el
90 % o los minutos de `estimated_time_to_regain_access`. Cada tramo sube sus brutos al bucket y se guarda
antes de pedir el siguiente, en `campanas_meta_dia` con la misma huella que la carga diaria: relanzarlo
(entero o desde donde se cortó) no duplica nada; una cifra corregida por Meta queda como versión nueva.

## Walcu agregado (walcu.py) y referencias de Meta (referencias_meta.py)

- `scripts/walcu_agregar.py` (Mac) agrega la exportación de leads de Walcu por semana (lunes de la semana ISO
  del lead), origen, campaña y tipo
  (ver `scripts/README.md`).
- `make walcu-cargar FICHERO=/ruta.csv` (servidor): el CSV llega por la entrada estándar al contenedor.
  Columnas exactamente `semana, origen, campana, tipo, leads, leads_cercana, leads_fuera, leads_sin_zona, citas,
  ventas, num_inexistente`. Rechaza el fichero entero (no carga nada) si sobra o falta una columna, si un
  valor tiene «@», 9 dígitos seguidos, un teléfono con espacios o forma de DNI/NIE, o si un valor no tiene
  formato. Los mensajes dicen fila y columna, nunca el valor. Zonas que no suman leads: aviso. Va comprimido
  al bucket (`walcu/semanal/<día>/`) y después a `walcu_campana_semana` (solo añadir; recargar una semana
  la sustituye en `walcu_campana_semana_vigente`; el mismo fichero dos veces no se carga). `semana` en
  AAAA-MM-DD y lunes; si no, se rechaza el fichero. Si no se cumple ventas ≤ citas ≤ leads o las zonas no
  suman los leads, se avisa.
- `referencias_meta.py` deja fuera las campañas de otras marcas (`propio.prefijos_excluidos` y
  `propio.palabras_excluidas`) y las de empresas del grupo con su propio marketing (`propio.empresas_excluidas`,
  si el nombre lleva ese texto), contadas y con aviso, y une Meta y Walcu (origen Facebook; AiVORA fuera) por
  el nombre normalizado (minúsculas, sin tildes, espacios simplificados, sin espacios entre corchetes y sin
  `-copy`, `copia`, `-OK`, ` Campaña`, ` - leadgen`, ` - landing`; equivalencias en
  `config/campanas_walcu.yaml`; las de `sin_equivalencia` salen en el informe como campañas sin Meta) y rehace `panel_campana_semana`, `panel_referencia_meta` y
  `panel_walcu_sin_cruce`. Lo llaman `campanas.py` (tras cada descarga de Meta y GA4) y la carga de Walcu.
  `make referencias-meta` lo rehace y escribe la tabla por año y tipo (gasto, leads según Meta y Walcu,
  citas, ventas, costes por lead, cita y venta, lead a cita, cita a venta, % fuera de zona y % de número
  inexistente, con la diferencia entre años) y lo que no casa.
- Pruebas: `tests/test_walcu_meta_hist.py`.

## Concesionario de la instancia (concesionario.py)

`ingesta/concesionario.py` lee `config/concesionario.yaml` (o el fichero de `CDF_CONCESIONARIO`):
nombre, nombre del panel, marca, `dealer_ids`, provincia (código INE), zona cercana, web propia (origen de
los leads en Walcu) y umbrales del panel. La
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
