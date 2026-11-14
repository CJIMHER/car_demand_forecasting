# Volkswagen VN — combustibles configurables

Versión **1.1.0** · 13 de noviembre de 2026 · Python 3.11 o posterior.

Amplía el colector de vehículos nuevos de Volkswagen España manteniendo el histórico de la 1.0.0. **No hay migración de esquema, cambio de identificadores ni renombrado de la base.** El comando y las salidas siguen siendo los mismos. El ZIP no contiene la base ni los vehículos de la instalación anterior.

## Actualizar la instalación que ya funciona

1. Descomprime el paquete en una carpeta aparte y ejecuta allí `python3 -m unittest discover -s tests -v`.
2. Actualiza cuando el colector del servidor no esté ejecutándose. Conserva una copia consistente de `data/`; con el proceso detenido puedes copiar la carpeta completa.
3. Sustituye **solo `vw_vn_stock.py`** en la instalación existente. **Conserva tu `config.json`, la carpeta `data/` y la programación diaria actual.** No copies el `config.json` de ejemplo sobre tus ajustes del servidor.
4. Comprueba la configuración sin capturar anuncios con `python3 vw_vn_stock.py check --config config.json`. La tarea diaria seguirá utilizando el mismo comando:

```bash
python3 vw_vn_stock.py run --config config.json
```

Si tu configuración no contiene `fuel_codes`, se aplica **`["B", "D"]`**. El ámbito resultante es exactamente:

```text
volkswagen-es-new-BQ-N-fuel-B-D-passenger-v1
```

Se mantiene `data/vw_vn_stock.sqlite3`, con `schema_version=1`. No se reescriben los ámbitos ni las observaciones anteriores. Los hashes comerciales tampoco cambian por la nueva versión. Una captura ya confirmada ese día continúa siendo idempotente.

No se ha accedido a la base de tu servidor. La compatibilidad se ha probado con una copia de la base de la entrega 1.0.0 y con casos sintéticos; consulta `VALIDATION.md`.

## Configuración y ámbito

`fuel_codes` es una lista no vacía. Puedes añadir a tu configuración existente:

```json
"fuel_codes": ["B", "D"]
```

Se eliminan espacios exteriores, se convierten los códigos a mayúsculas, se eliminan duplicados y se ordenan. `["D", "B"]` y `[" b ", "D", "B"]` conservan el mismo ámbito B/D. Una lista vacía, `null`, una cadena como `"B,D"` o códigos con separadores producen un error. **Vacío no significa todos los combustibles.** Deben emplearse códigos publicados por el servicio.

`scope_from_config(cfg)` calcula:

```text
volkswagen-es-new-BQ-N-fuel-<CÓDIGOS_ORDENADOS_SEPARADOS_POR_GUION>-passenger-v1
```

La URL pública, los parámetros HTTP, la validación de cada anuncio y el ámbito utilizan la misma selección normalizada. No hay filtros ni ámbitos globales mutables. En HTTP se conservan los parámetros repetidos: `t_petr=E&t_petr=H`.

Si una base existente tiene otro ámbito, `run` y `export` se detienen **antes de cambiar su esquema, recuperar ejecuciones o exportar datos**. No se cambia `metadata.scope` automáticamente. Para otra selección, utiliza otra carpeta de datos. El sufijo `passenger-v1` identifica el ámbito y se conserva aunque el programa sea 1.1.0.

## Códigos publicados por la web

Verificados el **13/11/2026** en la aplicación **GSL 3.127.1**, ruta pública `/bff/setup`, criterio `allCriterias.criterias.t_petr` y sus valores. Recuentos de `/bff/criteria/possible`, Volkswagen VN en España sin filtro de combustible:

| Código | Nombre publicado | Anuncios | Selección |
|---|---|---:|---|
| `B` | Gasolina | 4.300 | Histórico actual |
| `D` | Diésel | 515 | Histórico actual |
| `E` | Eléctrico | 40 | Otros combustibles |
| `H` | Híbrido | 382 | Otros combustibles |

El catálogo expone **solo estos cuatro códigos**. La muestra incluye modelos ID bajo `E` y Golf GTE bajo `H`. No aparece otro código llamado «híbrido enchufable»: los GTE/eHybrid se siguen según el código que publica cada anuncio, sin inventar un código PHEV adicional. Tampoco se publica un código de gas, GLP, GNC, hidrógeno u otros en este catálogo; **no se les asigna una letra supuesta**.

Los eTSI que la fuente clasifica como `B` permanecen en B/D. No se cambian de grupo por su denominación comercial. Las selecciones disjuntas B/D y E/H cubren los cuatro códigos publicados. Son códigos de este servicio, no una clasificación universal de combustibles.

`examples/fuel_catalog_20261113.json` conserva códigos, nombres, recuentos, versión y hashes de las respuestas consultadas. La selección es explícita: los futuros códigos de la web no se incorporan silenciosamente. Si el catálogo cambia, revisa la configuración y crea un ámbito nuevo cuando corresponda.

## Segunda copia para otros combustibles

`config.otros.example.json` incluye todos los códigos publicados distintos de B y D, más los mismos parámetros de control que el ejemplo principal. Sus dos diferencias son:

```json
{
  "data_dir": "data_otros",
  "fuel_codes": ["E", "H"]
}
```

En la segunda copia del colector:

```bash
python3 vw_vn_stock.py check --config config.otros.example.json
python3 vw_vn_stock.py run --config config.otros.example.json
```

La base será `data_otros/vw_vn_stock.sqlite3` y el ámbito:

```text
volkswagen-es-new-BQ-N-fuel-E-H-passenger-v1
```

`data_dir` se resuelve respecto al archivo de configuración, no al directorio desde el que se lanza Python. Puedes mantener ambas configuraciones en una carpeta o usar dos copias en carpetas diferentes; sus rutas de datos deben ser distintas. **No cambies los combustibles de la configuración que apunta a la base B/D para ampliar esa misma base.** El programa rechazará la mezcla.

La prueba real de E/H se documenta en `VALIDATION.md`. Sus CSV y manifiesto están en `examples/validation_otros/`, como evidencia separada: **no se importan ni se utilizan automáticamente como histórico inicial**. La primera captura propia comienza en tu carpeta `data_otros/`.

## Mismas salidas e identificación

En cada carpeta de datos:

| Ruta | Contenido |
|---|---|
| `vw_vn_stock.sqlite3` | Ejecuciones, anuncios, observaciones y eventos |
| `exports/stock_latest.csv` | Última captura completa |
| `exports/history.csv` | Todas las observaciones confirmadas |
| `exports/changes.csv` | Altas, cambios, ausencias y reapariciones |
| `exports/vehicles.csv` | Primera/última observación, primera ausencia y estado |
| `exports/runs.csv` | Intentos completos, fallidos e interrumpidos |
| `raw/FECHA_INTENTO/` | JSON comprimidos y manifiesto con fechas y SHA-256 |

Se conservan los campos de 1.0.0: ID (`carid`, contrastado con `key`), VIN, matrícula si se publica, comisión, modelo, versión, año, precios, condiciones, combustible, datos técnicos, concesionario y estado de entrega. El esquema SQLite y las columnas comerciales de CSV no cambian.

Divide los campos monetarios `_cents` entre 100 para obtener euros. El tipo de precio y sus condiciones se conservan; un precio recomendado con campaña no se interpreta como precio al contado garantizado. Las cuotas mensuales nunca sustituyen al precio del coche. Los CSV usan UTF-8 con BOM y protección frente a fórmulas en textos publicados.

Los campos ausentes siguen siendo desconocidos: `initialreg="libre de"` no se convierte en fecha y los kilómetros ausentes no se convierten en cero. `shouldFetchReserved` no significa reservado. `emis` se conserva como `emission_label_source`, sin deducir un distintivo DGT.

Para reconstruir los CSV sin consultar la web, indica la configuración correspondiente:

```bash
python3 vw_vn_stock.py export --config config.otros.example.json
```

## Histórico y controles conservados

- `new`: primera observación de un ID. `changed`: cambio comercial con valor anterior/nuevo. `not_seen`: primera ausencia en captura completa. `removed`: tres ausencias en días de captura completa sin reaparecer. `returned`: reaparece el mismo ID.
- Se compara con la última captura completa. Los fallos y las repeticiones del mismo día no suman ausencias. **Retirada no acredita venta.** No se fusionan automáticamente IDs distintos que compartan VIN.
- Una confirmación por día de Madrid, bloqueo por carpeta y transacción SQLite para aplicar la captura completa. Un error posterior de exportación no invalida la captura confirmada.
- Se comprueban página, tamaño, total, orden, duplicados, ámbito y secuencia de IDs de la primera página al terminar. Se rechazan catálogos vacíos/truncados y combustibles ajenos a la configuración.
- Cobertura mínima de precios positivos del 95 %. Caída máxima del total y ausencia máxima de IDs previos del 25 %, con los mismos parámetros configurables de 1.0.0.
- Por defecto: hasta 2 solicitudes simultáneas y 1,5 segundos entre inicios dentro del proceso; reintentos limitados, respeto de `Retry-After`, comprobación de `Date`/`Age` y mismas reglas de robots.txt.
- `robots_policy: respect` sigue siendo el valor inicial. Un 4xx de robots.txt, salvo 429, se registra como no disponible conforme a RFC 9309. Esa excepción no se aplica a los datos: sus 401/403 detienen la captura. `authorized` sigue requiriendo una referencia a un permiso real del titular.

La paginación no es perfectamente simultánea. Los controles detectan muchas variaciones, pero no todas las que mantengan el mismo total. No se reconstruyen cambios intradía ni días anteriores al inicio de cada histórico.

## Programación en paralelo

La tarea B/D existente conserva comando y nombres. No se ha modificado la programación de tu servidor desde esta entrega.

El generador conserva `vw-vn-stock` para B/D. Para E/H utiliza **`vw-vn-stock-e-h`**, evitando sobrescribir la primera tarea. Ejemplo en la máquina definitiva, con una hora que debes adaptar a tu servidor:

```bash
python3 prepare_schedule.py --platform linux --config config.otros.example.json --time 08:15
```

Genera `vw-vn-stock-e-h.service` y `vw-vn-stock-e-h.timer` y muestra cómo instalarlos; no los activa. Usa `Europe/Madrid`. `Persistent=true` recupera una ejecución pendiente, no los días históricos perdidos.

En Mac utiliza `--platform macos`: genera `es.local.vw-vn-stock-e-h.plist`. Usa la hora local y requiere sesión iniciada/equipo disponible. En Windows crea una segunda tarea con nombre propio y argumentos `vw_vn_stock.py run --config RUTA_ABSOLUTA_CONFIG_OTROS`. Evita solapar innecesariamente ambas descargas.

No requiere Playwright, Selenium ni paquetes de scraping. En Windows puedes usar `py -3`; si falta la base de zonas horarias, instala `tzdata`. Códigos de salida: 0 correcto/ya capturado; 1 error/revisión; 3 robots.txt impide el rastreo.

## Contenido del paquete

- `SHA256SUMS.txt`: hashes de todos los archivos, excepto el propio listado.
- `VALIDATION.md`: captura real E/H, compatibilidad y pruebas.
- `tests/fixtures/legacy_v100_*`: esquema congelado y registros **sintéticos**, sin vehículos reales del histórico anterior.
- `examples/legacy_compatibility.json`: comprobación agregada de una copia de la base 1.0.0; no contiene sus registros.
- `examples/validation_otros/`: evidencia nueva E/H, separada de la base B/D.

Verifica el paquete con `sha256sum -c SHA256SUMS.txt` en Linux o `shasum -a 256 -c SHA256SUMS.txt` en Mac.

Fuentes: [buscador oficial de Volkswagen España](https://www.volkswagen.es/es/stock-resultados.html), su configuración `/es.global-config.json` y las rutas `/bff/setup`, `/bff/criteria/possible` y `/bff/car/search` de la aplicación GSL enlazada por la página.
