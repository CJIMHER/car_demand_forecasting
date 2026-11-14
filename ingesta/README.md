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

`es_propio` marca los anuncios de los `dealer_id` de `config/concesionario.yaml` (AVISA, ESP00710).

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

## Cómo se prueba

Con bases SQLite sintéticas que se escriben con el **propio código de los scrapers** (sus funciones
`connect_db`, `start_run` y `commit_snapshot`, importadas sin modificarlas): mismos esquemas,
eventos y ausencias que en el servidor, sin ningún dato real.

```bash
# En el Mac (Postgres 16 embebido con pgserver)
uv venv --python 3.12 .venv && uv pip install --python .venv/bin/python -r requirements-dev.txt pgserver==0.1.4
.venv/bin/python -m pytest

# En el servidor (Postgres desechable cdf_postgres_test)
make test
```
