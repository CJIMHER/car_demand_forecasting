# Verificación de entrega — Volkswagen stock diario v1.0.0

Fecha: 1 de noviembre de 2026. Entorno de pruebas: Linux, Python 3.12; código compatible con Python 3.11 o posterior.

## Resultado real

- Captura nacional completa de **2922 anuncios únicos** con `t_manuf=BQ`, `t_cartype=U`, España, mercado `passenger`.
- **244 páginas de datos**, más una nueva consulta de la primera página: **245 respuestas** de datos.
- Inicio UTC: `2026-11-01T09:07:14+00:00`. Final UTC: `2026-11-01T09:14:47+00:00`.
- Precio principal positivo: **2922/2922**. VIN con formato válido: **2922**. VIN distintos: **2921**.
- Concesionarios con identificador distinto: **56**.
- Aplicación pública descubierta: GSL **3.126.1**.
- Se validaron todos los SHA-256 de los JSON originales, el número de filas del CSV, la integridad SQLite y las claves foráneas. Todas las consultas de `queries.sql` se ejecutaron correctamente.
- La captura inicial está en `data/`, lista para continuar. El histórico conserva también un intento previo interrumpido sin observaciones confirmadas; se reinició para validar la descarga con concurrencia limitada. Ese intento no genera altas ni bajas.

La prueba completa utilizó **4 solicitudes simultáneas como máximo**, con 1,5 segundos entre inicios de solicitudes en todo el proceso. La configuración entregada establece 2 como máximo; utiliza los mismos filtros, validaciones y paginación.

## Pruebas automatizadas

**37 pruebas superadas**. Registro: `examples/test_results.txt`.

Cubren el contrato de dos respuestas reales, el límite efectivo de 12 registros por página, identidad y ámbito, separación de precios y financiación, reservas desconocidas, paginación completa/incompleta, duplicados, cambios de total, comprobación final, concurrencia y fallos de páginas, cambios de precio, ausencia en días completos, reaparición, transacciones, anomalías de stock, exclusión mutua, repetición del mismo día, exportaciones, configuración, gzip, fechas HTTP y reglas de robots.txt.

Las pruebas de evolución del histórico usan secuencias simuladas: no se afirma haber observado varios días reales de cambios.

## Programación y límites comprobados

- `systemd-analyze verify` validó los archivos de servicio y temporizador generados.
- `systemd-analyze calendar` validó el ejemplo diario de las 06:45 en Europe/Madrid.
- El LaunchAgent de Mac se generó y validó como XML/plist, incluidos comando, etiqueta y hora. No se ejecutó en macOS.
- La receta de Windows no se ejecutó en Windows.
- No se ha instalado ni activado ninguna tarea diaria. Los archivos de programación se regeneran en la máquina definitiva para incorporar sus rutas.
- No se acredita venta por desaparición, reserva por ausencia de señal, ausencia de todo cambio intradía ni simultaneidad perfecta del catálogo vivo.

## Muestras diagnósticas separadas

`examples/sample_manifest.json` identifica dos páginas observadas el 31/10/2026 (12 anuncios por página; total comunicado 2921), y la última página comprobada el 01/11/2026. Son muestras parciales y no se importan automáticamente. Las cifras no se confunden con la captura completa de entrega.
