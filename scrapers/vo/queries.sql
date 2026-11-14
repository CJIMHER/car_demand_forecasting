-- Ejecutar contra data/vw_stock.sqlite3. Importes almacenados en céntimos.

-- Stock observado en cada captura completa (no incluye intentos fallidos).
SELECT snapshot_day, observed_count, started_at, finished_at
FROM runs WHERE status='success' ORDER BY snapshot_day;

-- Cambios del precio de venta, separados del PVP de referencia cuando era nuevo.
SELECT detected_at, vehicle_id,
       CAST(old_json AS REAL)/100 AS precio_anterior_eur,
       CAST(new_json AS REAL)/100 AS precio_nuevo_eur,
       (CAST(new_json AS REAL)-CAST(old_json AS REAL))/100 AS variacion_eur,
       100*(CAST(new_json AS REAL)/NULLIF(CAST(old_json AS REAL),0)-1) AS variacion_pct
FROM events
WHERE kind='changed' AND field='price_retail_cents'
  AND old_json<>'null' AND new_json<>'null'
ORDER BY detected_at, vehicle_id;

-- Anuncios ausentes; NO son ventas confirmadas.
SELECT vehicle_id, first_seen_at, last_seen_at, first_missing_at,
       missing_successful_days, status
FROM vehicles WHERE status IN ('missing','removed')
ORDER BY first_missing_at DESC;

-- Reapariciones del mismo identificador de anuncio.
SELECT detected_at, vehicle_id, old_json, new_json
FROM events WHERE kind='returned' ORDER BY detected_at;

-- Bastidores con varios anuncios en la última captura: revisar, no fusionar sin más.
WITH last_run AS (
  SELECT run_id FROM runs WHERE status='success' ORDER BY snapshot_day DESC LIMIT 1
)
SELECT json_extract(data_json, '$.business.vin') AS vin,
       COUNT(*) AS anuncios, GROUP_CONCAT(vehicle_id) AS identificadores
FROM observations WHERE run_id=(SELECT run_id FROM last_run)
  AND json_extract(data_json, '$.business.vin') IS NOT NULL
GROUP BY vin HAVING COUNT(*)>1;

-- Campos comerciales de la última captura, con el precio expresado en euros.
SELECT o.vehicle_id,
       json_extract(o.data_json, '$.business.model') AS modelo,
       json_extract(o.data_json, '$.business.version') AS version,
       json_extract(o.data_json, '$.business.mileage_km') AS km,
       json_extract(o.data_json, '$.business.price_retail_cents')/100.0 AS precio_publicado_eur,
       json_extract(o.data_json, '$.business.price_basis_label') AS condiciones_precio,
       json_extract(o.data_json, '$.business.dealer_name') AS concesionario
FROM observations o
WHERE o.run_id=(SELECT run_id FROM runs WHERE status='success' ORDER BY snapshot_day DESC LIMIT 1)
ORDER BY modelo, precio_publicado_eur;
