# Validación — Volkswagen VN 1.1.0

**Fecha: 13 de noviembre de 2026. Resultado: 57 pruebas correctas y captura real completa de 422 anuncios E/H.** No se ha accedido al servidor de producción ni se ha modificado su base o programación.

## Captura real con la configuración entregada

Comando ejecutado, sin modificar los parámetros de `config.otros.example.json`:

```bash
python3 vw_vn_stock.py run --config config.otros.example.json
```

| Comprobación | Resultado |
|---|---|
| Inicio y fin, Europe/Madrid | 13/11/2026, 09:39:47–09:42:24 CET |
| Inicio y fin, UTC | 08:39:47–08:42:24 UTC |
| Duración | 157 segundos |
| Versión del colector / aplicación fuente | 1.1.0 / GSL 3.127.1 |
| Ejecución | `bfda1103fd4d4fd5b191dc94ed8453e6` |
| Estado persistido | `success` |
| Ámbito | `volkswagen-es-new-BQ-N-fuel-E-H-passenger-v1` |
| Filtros | España, Volkswagen `BQ`, nuevo `N`, combustibles `E` y `H` |
| Total anunciado / IDs únicos capturados | **422 / 422** |
| Eléctricos `E` | **40** |
| Híbridos `H` | **382** |
| Páginas completas | 36: 35 × 12 anuncios y última de 2 |
| Relectura final de la primera página | Idéntica a la inicial, incluidos orden y contenido |
| Respuestas conservadas y hashes verificados | 37: 36 páginas + 1 relectura |
| VIN válidos / distintos | 422 / 422 |
| Matrículas publicadas | 0 |
| Anuncios con precio publicado positivo | 422 / 422, cobertura 100 % |
| Concesionarios distintos | 54 |
| Eventos en este primer día E/H | 422 `new` |
| SQLite `integrity_check` / errores de claves ajenas | `ok` / 0 |

Se reconstruyeron los 422 registros a partir de los JSON originales con el normalizador 1.1.0. Coinciden **íntegramente** con las observaciones guardadas, incluidos identificadores, fechas de observación, `business_hash` y `raw_record_hash`. Ningún registro de esta captura tiene combustible B o D.

Las cinco salidas tienen el mismo formato de 1.0.0: `stock_latest.csv`, `history.csv`, `changes.csv` y `vehicles.csv` contienen 422 filas cada una; `runs.csv` contiene una. Los ocho ejemplos de `queries.sql` se ejecutaron correctamente contra la base E/H de prueba.

El precio de los 422 anuncios se etiqueta como **«PVP Recomendado (con campaña)»**. Sus condiciones se conservan. Estados de entrega publicados: 331 código `70`, 73 código `40` y 18 código `60`; no se interpretan como venta o reserva.

### Robots y controles durante la captura

Se utilizó `robots_policy: respect`, sin excepción de autorización. El manifiesto registra las comprobaciones de `https://www.volkswagen.es/robots.txt` para página y configuración, ambas permitidas. El robots del host GSL respondió 401 y se registró como `unavailable`, siguiendo **la misma regla de 1.0.0** para 4xx de robots.txt (RFC 9309, §2.3.1.3). Un 401/403 de los datos sigue siendo un error que detiene la descarga.

Se mantuvieron página de 12, hasta dos solicitudes simultáneas, intervalo mínimo de 1,5 segundos, validación de `Date`/`Age`, reintentos limitados, control de totales/duplicados/primera página, cobertura de precios y transacción completa. No se relajaron controles para conseguir esta captura.

### Evidencia incluida

En `examples/validation_otros/`:

- `raw/manifest.json` y 37 respuestas JSON comprimidas originales.
- `exports/`: las cinco salidas CSV nuevas de E/H.
- `run.log`: progreso y confirmación de la captura.
- `validation_results.json`: resultados agregados de las comprobaciones.

Los CSV y manifiesto conservan las rutas de la máquina de validación cuando forman parte de la salida original. Son evidencia, no rutas de instalación ni instrucciones para restaurar una base. **No se incluyen archivos SQLite y no se carga esta captura automáticamente al ejecutar el programa.**

## Códigos de combustible

Se consultaron `/bff/setup` y `/bff/criteria/possible` de la aplicación oficial enlazada por el buscador, con Volkswagen VN en España. El catálogo publica `B` gasolina, `D` diésel, `E` eléctrico y `H` híbrido. La consulta sin filtro de combustible devolvió 5.237 anuncios: 4.300 B, 515 D, 40 E y 382 H.

`config.otros.example.json` selecciona exactamente **E/H**, todos los códigos publicados distintos de B/D. Se verificaron modelos ID bajo E y Golf GTE bajo H. No se encontró un código adicional de híbrido enchufable, gas, GLP, GNC o hidrógeno en ese catálogo. La clasificación se toma de `motor.fuel.code`, no del texto del modelo; los eTSI clasificados B permanecen en la selección existente.

`examples/fuel_catalog_20261113.json` conserva los nombres, códigos, recuentos, fecha, versión y hashes SHA-256 de las respuestas del catálogo. Estos recuentos describen la comprobación de ese día, no garantizan el stock futuro.

## Compatibilidad con 1.0.0

Se probó una **copia temporal de la base incluida en la entrega 1.0.0 del 08/11/2026**, con 4.780 vehículos y observaciones. No es una conexión a la base que actualmente corre en el servidor.

| Prueba | Resultado |
|---|---|
| Configuración 1.0.0 sin `fuel_codes` | Aceptada; selección B/D |
| Ámbito exacto de B/D | `volkswagen-es-new-BQ-N-fuel-B-D-passenger-v1` |
| Versión del esquema | `1`, sin cambios de SQL |
| Abrir y cerrar la copia con 1.1.0 | Archivo idéntico byte a byte |
| Intentar abrir esa base con configuración E/H | Rechazado; archivo sin cambios |
| Recuentos anteriores | 1 ejecución; 4.780 vehículos, observaciones y eventos, sin cambios |
| Renormalizar los 4.780 anuncios originales | Campos comerciales y hashes idénticos a 1.0.0 |

SHA-256 de la copia antes y después:

```text
9caa2407df8cd2ae93fdbeaf94454717df2cdf5ac47c47962c8620151d82aa02
```

El resultado agregado está en `examples/legacy_compatibility.json`. **No se incluyen los anuncios, exportaciones, JSON originales ni la base de esa entrega anterior.** Los fixtures B/D del paquete son sintéticos; el SQL congelado contiene solo el esquema.

## Pruebas automatizadas

```bash
python3 -m unittest discover -s tests -v
```

**57 pruebas correctas**, con resultado en `examples/test_results.txt`: las 42 comprobaciones previas, adaptadas a las muestras E/H de esta entrega, más 15 de configuración/compatibilidad. Cubren:

- Ámbito derivado de la configuración; igualdad exacta del ámbito y URL B/D antiguos; orden, mayúsculas, espacios y duplicados canónicos.
- Configuración antigua sin la clave nueva, valores inválidos y rechazo de anuncios cuyo combustible no figure en la selección.
- Filtros repetidos de la API, URL y `Referer` coherentes con E/H; ausencia de estado global mutable entre configuraciones.
- Base creada con el esquema congelado 1.0.0: apertura sin cambiar bytes, rechazo de otro ámbito sin reescritura y continuación del histórico sin falsas altas/cambios.
- Rechazo en `run` y `export` antes de llamadas HTTP, recuperación o exportación sobre una base de otro ámbito.
- Captura E/H completa con transporte simulado y repetición del día sin nuevas llamadas HTTP.
- Precios, identificadores, VIN, estados, controles de paginación/concurrencia, robots, errores HTTP, transacciones, bloqueo y evolución del histórico que ya existían.

La evidencia real cubre **un día** de E/H. Las ausencias, retiradas, reapariciones y cambios entre días se verifican mediante pruebas sintéticas; no se presentan como una observación real de varios días de E/H.

## Programación y paquete

Se generaron las tareas Linux B/D y E/H en una copia temporal: nombres distintos, configuraciones correctas y ambas parejas servicio/temporizador aceptadas por `systemd-analyze verify`. Se generaron y analizaron los plist de macOS con `plistlib`, comprobando etiquetas, argumentos, horas y rutas de logs independientes; no se ejecutó launchd en un Mac. No se ha activado ninguna tarea desde esta entrega.

El ZIP se construye con una lista explícita de código, configuraciones, documentación, pruebas y evidencia. Se excluyen carpetas de datos, bases SQLite, archivos WAL/SHM, cachés, configuraciones de investigación con credenciales de la API y tareas generadas para la máquina de validación. `SHA256SUMS.txt` permite verificar todos sus archivos, excepto el propio listado.
