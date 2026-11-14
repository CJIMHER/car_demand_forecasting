# Histórico diario de Volkswagen España — ocasión

Versión 1.0.0 · 1 de noviembre de 2026 · Python 3.11 o posterior.

Colector independiente del de Audi, con el mismo esquema de histórico: **SQLite, CSV y respuestas originales comprimidas**. Consulta la selección nacional de ocasión de la URL facilitada: `t_manuf=BQ`, `t_cartype=U`, mercado `passenger`, España. No se ha activado ninguna tarea en tu ordenador ni en un servidor.

**Incluye la captura completa inicial del 2026-11-01: 2.922 anuncios** en `data/`, con sus CSV y respuestas originales. Puedes continuar ese histórico usando la misma carpeta. La primera ejecución del mismo día reutilizará la captura ya confirmada.

## Empezar

Descomprime el paquete en una carpeta permanente. Abre una terminal dentro de `volkswagen_stock_daily` y ejecuta:

```bash
python3 --version
python3 -m unittest discover -s tests -v
python3 vw_stock.py check
python3 vw_stock.py run
```

`check` comprueba la configuración pública y robots.txt. No descarga anuncios ni certifica por sí solo que una captura completa vaya a terminar. `run` recorre todas las páginas, valida el resultado y actualiza el histórico. Si ese día ya tiene una captura completa, regenera los CSV sin repetir las consultas.

El colector no necesita cuenta de Volkswagen, navegador, Selenium ni paquetes de scraping. Obtiene en cada ejecución la versión del buscador y su configuración pública; no lleva una clave API fija. En Windows usa `py -3` en lugar de `python3`; si falta la base de zonas horarias, instala `tzdata` con `py -3 -m pip install tzdata`.

## Qué conserva

| Datos | Tratamiento |
|---|---|
| Identificador del anuncio | `carid`, contrastado con su `key`; clave primaria del histórico |
| Bastidor | Se conserva cuando tiene un formato válido; permite estudiar posibles republicaciones |
| Modelo y versión | Descripción publicada, código de modelo, año y familia |
| Precio principal | `parsedPrice.value`, en céntimos enteros, con la etiqueta de condiciones que publica la web |
| Precio de referencia | `parsedPrice.list.value`, separado del precio principal; puede no estar visible en la interfaz |
| Financiación | Cuota, entrada, cuota final, TAE y otros términos, como textos separados del precio del coche |
| Vehículo | Kilómetros numéricos, matriculación, combustible, potencia, cilindrada, colores y etiqueta cuando están disponibles |
| Concesionario | Identificador, nombre, ciudad y código postal |
| Otros | Campañas, indicador Approved, IVA, enlace a ficha, URLs de imágenes y fechas del proveedor |

La etiqueta observada del precio principal es «al contado PVP Concesionario», pero sus condiciones generales pueden mencionar financiación. Se conserva la etiqueta original; **no se garantiza que todos los anuncios tengan condiciones comerciales idénticas**. Una cuota mensual nunca se usa como precio de venta. La transmisión no aparece como campo independiente en las respuestas inspeccionadas; puede figurar dentro del texto de la versión y no se inventa un valor.

La reserva se registra solo si el listado devuelve un `isReserved` explícito. `shouldFetchReserved` significa que la interfaz podría consultar otra ruta; **no significa reservado ni disponible**. En su ausencia, `reservation` queda vacío/desconocido. Este colector no hace una consulta individual de reservas por cada coche.

No se descargan fotografías ni se visitan miles de fichas. Los JSON originales incluyen los demás campos entregados públicamente por el servicio, que pueden estudiarse después.

## Archivos de trabajo

| Archivo | Contenido |
|---|---|
| `data/vw_stock.sqlite3` | Fuente canónica: ejecuciones, anuncios, observaciones y eventos |
| `data/raw/FECHA_INTENTO/` | Respuestas JSON comprimidas y manifiesto con fechas y SHA-256 |
| `data/exports/stock_latest.csv` | Última captura completa |
| `data/exports/history.csv` | Todas las observaciones de capturas completas |
| `data/exports/changes.csv` | Altas, cambios por campo, ausencias y reapariciones |
| `data/exports/vehicles.csv` | Primera/última observación y estado de cada anuncio |
| `data/exports/runs.csv` | Éxitos, fallos e interrupciones |

Los CSV están en UTF-8 con BOM para facilitar su apertura en Excel. Los campos monetarios terminados en `_cents` deben dividirse por 100 para obtener euros. Las condiciones de financiación y algunas colecciones se exportan como JSON dentro de una celda.

Los CSV pueden reconstruirse sin acceder a la web:

```bash
python3 vw_stock.py export
```

`config.json` y `config.example.json` tienen los valores iniciales. `data_dir` se interpreta con relación al archivo de configuración, independientemente de la carpeta desde la que se ejecute el programa. Mantén la misma carpeta de datos de un día a otro.

## Altas, cambios y bajas

| Evento | Significado |
|---|---|
| `new` | Primera observación del identificador; todos lo son en la primera captura |
| `changed` | Cambió un campo normalizado; conserva valor anterior y nuevo |
| `not_seen` | Ausencia en una captura completa; pasa a estado `missing` |
| `removed` | Ausencia en tres capturas completas de días distintos |
| `returned` | Reaparece el mismo identificador |

**Un anuncio retirado no es una venta confirmada.** Puede haber una venta, traslado, retirada temporal o republicación. El tiempo entre primera y última observación es tiempo observado en la web, no necesariamente días reales en stock. Los coches presentes al iniciar el seguimiento tienen antigüedad previa desconocida.

El bastidor se conserva como dato auxiliar, pero no se fusionan automáticamente anuncios con el mismo VIN. Un coche republicado con otro `carid` produce un anuncio nuevo. `queries.sql` incluye una consulta para detectar VIN presentes en varios anuncios y revisarlos.

Las ausencias cuentan únicamente días con captura completa; un fallo no suma una confirmación y dos ejecuciones el mismo día tampoco. Se conserva el intervalo entre última presencia y primera ausencia, sin atribuir una fecha exacta de venta. Una captura diaria no recoge todos los cambios intradía.

Las fechas `dateoffer` y `dateproduction` se guardan como datos del proveedor. No sustituyen la hora de observación ni se interpretan como fecha cierta de incorporación o de cambio de precio. Cambios de URLs de fotos o de esas fechas no generan por sí solos un evento comercial.

## Programarlo cada día

La hora **06:45 es un ejemplo modificable**, no una tarea ya activada. Genera los archivos en la máquina que vaya a ejecutar el colector: contendrán las rutas reales de esa máquina. Si mueves la carpeta o cambias de Python, regénéralos.

### Linux con systemd

```bash
python3 prepare_schedule.py --platform linux --time 06:45
```

El generador muestra los comandos exactos para instalar y activar `vw-stock.service` y `vw-stock.timer`. La zona del temporizador es `Europe/Madrid`. `Persistent=true` recupera una ejecución pendiente al reactivarse; no reconstruye días perdidos. Para ejecutar sin sesión abierta, un administrador puede habilitar la permanencia con `sudo loginctl enable-linger NOMBRE_USUARIO`.

```bash
journalctl --user -u vw-stock.service
systemctl --user list-timers vw-stock.timer
systemctl --user disable --now vw-stock.timer
```

El último comando desactiva el temporizador; no lo ejecutes si quieres mantenerlo activo.

### macOS

```bash
python3 prepare_schedule.py --platform macos --time 06:45
```

Genera el LaunchAgent `es.local.vw-stock.plist` y muestra cómo instalarlo. `launchd` utiliza la zona local del Mac: configúrala en Madrid. Necesita la sesión iniciada y el equipo disponible. Los registros se escriben en `data/logs/`.

Para detenerlo:

```bash
launchctl bootout "gui/$(id -u)" ~/Library/LaunchAgents/es.local.vw-stock.plist
```

### Windows

En el Programador de tareas crea una tarea diaria con la ruta absoluta del `python.exe` que has utilizado para probar el script. Argumentos:

```text
"C:\ruta\volkswagen_stock_daily\vw_stock.py" run --config "C:\ruta\volkswagen_stock_daily\config.json"
```

Configura la hora local, las credenciales de ejecución, «Ejecutar lo antes posible si no hubo un inicio programado» y no iniciar otra instancia si una sigue activa. El Programador de Windows y el LaunchAgent deben validarse en sus respectivos sistemas; esta entrega se ha desarrollado en Linux.

Audi y Volkswagen usan distintos archivos SQLite y nombres de tarea. Pueden convivir en carpetas separadas.

## Integridad y límites

- Bloqueo del sistema operativo: una sola ejecución por carpeta de datos.
- Una captura confirmada por día de Madrid. Las horas de observación se guardan en UTC; el día se fija al comenzar el intento.
- Páginas de **12**: el servicio inspeccionado devuelve 12 incluso al pedir 24. Se exige que los metadatos confirmen tamaño, página, orden, número de páginas y total.
- Se rechazan duplicados, páginas truncadas, stock vacío, cambios del total, anuncios de otro ámbito y menos del 95 % de precios principales positivos.
- Se vuelve a consultar la primera página al terminar y se contrasta la secuencia de identificadores.
- Una caída del total superior al 25 % o la ausencia de más del 25 % de los identificadores de la captura previa exige revisión. Los umbrales son configurables; también una caída real puede detener el proceso.
- Solo se incorporan observaciones y bajas tras validar todo el recorrido, dentro de una transacción SQLite. Un fallo de exportación posterior no invalida una captura ya guardada.
- Por defecto hay hasta **2 solicitudes simultáneas**, con **1,5 segundos entre inicios en todo el proceso**, no por hilo. Se permite configurar de 1 a 4. La compresión gzip reduce transferencia. El total de solicitudes no cambia.
- Las respuestas 429 y ciertos errores temporales tienen reintentos limitados; se respeta `Retry-After`. Los 401/403 del origen de datos detienen la captura. No hay rotación de proxies, resolución de CAPTCHAs ni elusión de barreras.
- La comprobación de `Date` y `Age` HTTP detecta respuestas antiguas o problemas de reloj, pero no acredita la fecha exacta de actualización de cada coche.
- El catálogo está vivo y no ofrece un token de captura simultánea. Los controles reducen el riesgo de cambios durante la paginación; no excluyen todas las variaciones que mantengan el mismo total.

Códigos de salida: **0** correcto o ya capturado; **1** error o revisión de integridad; **3** robots.txt bloquea la extracción. Los fallos quedan en SQLite y en la salida del proceso. No se envían mensajes ni correos; el servidor puede vigilar esos códigos.

Haz copias de seguridad de `data/`. Usa la API de backup de SQLite o detén el colector antes de copiar la base; copiar solo el archivo principal mientras está escribiendo puede omitir el WAL. Las exportaciones históricas crecen con el tiempo y se regeneran completas.

## Acceso al servicio

La política inicial es `robots_policy: respect`. Las reglas observadas en `www.volkswagen.es` no bloquean las rutas que utiliza este colector. En el host de la aplicación, `/robots.txt` devolvió HTTP 401, mientras que la ruta pública de búsqueda respondió HTTP 200.

Para robots.txt se trata un 4xx, salvo 429, como archivo no disponible conforme al apartado 2.3.1.3 de RFC 9309; el resultado queda registrado. Esto **no equivale a una autorización contractual** y no modifica el tratamiento de un 401/403 en la consulta de datos. Los errores de red o 5xx al comprobar robots.txt detienen el proceso.

Si en el futuro aparece una prohibición expresa en robots.txt, el modo normal se detiene. Solo para una extracción que cuente con permiso del titular se admite `robots_policy: authorized` y una `authorization_reference` no vacía. El programa registra esa referencia, pero no verifica su validez.

## Comprobaciones y mantenimiento

`VALIDATION.md` recoge la evidencia de la entrega. `examples/` contiene muestras parciales separadas del histórico; no se importan automáticamente. Si cambia el dominio o el esquema del buscador, el colector se detiene para revisar el adaptador.

Fuentes técnicas inspeccionadas:

- [Buscador oficial](https://www.volkswagen.es/es/stock-resultados.html/.app?pageIndex-app=1&t_manuf-app=BQ&t_cartype-app=U&sort-app=DATE_OFFER&sortdirection-app=DESC).
- Configuración pública `/es.global-config.json` y aplicación GSL 3.126.1 enlazada por la página el 31/10/2026.
- [Robots de Volkswagen España](https://www.volkswagen.es/robots.txt).
- [RFC 9309, tratamiento de robots.txt no disponible](https://www.rfc-editor.org/rfc/rfc9309.html#section-2.3.1.3).
