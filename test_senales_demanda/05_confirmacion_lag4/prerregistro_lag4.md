# Prerregistro — confirmación del lag 4 (fijado el 2026-11-08, antes de ejecutar)

## Hipótesis
H1: la variación mensual del interés de búsqueda (Δlog(1+GT), categoría 47) con 4 meses de adelanto
mejora la predicción directa a 4 meses de la variación mensual de matriculaciones (Δlog), frente a un
modelo que solo usa la propia historia de matriculaciones.

## Especificación principal (única que decide)
- Muestra: los 41 modelos elegibles en categoría 47 (mismos criterios que el análisis de prewhitening).
- Objetivo: y_t = Δlog matriculaciones del mes t. Predicción directa desde el origen t−4.
- Referencia: y_t ~ const + y_{t−4} + y_{t−5} + y_{t−12}.
- Aumentado: referencia + g_{t−4}, con g = Δlog(1+GT).
- Estimación: regresión robusta de Huber (rebaja el peso de los meses COVID sin eliminarlos), ventana expansiva.
- Evaluación: meses objetivo 2023-01 a 2025-12 (36 por modelo), cada predicción solo con datos hasta su origen.
- Métricas: mejora del MAE (%), Clark-West con HAC de 6 lags, signo del coeficiente de g.

## Regla de decisión (las tres deben cumplirse)
1. Mejora media del MAE del panel por encima del percentil 95 de 200 réplicas de surrogates de fase
   (mismo lag 4 en cada réplica) y mediana de la mejora > 0.
2. El lag 4 es el que mejor mejora media da entre los lags admisibles 4..12 (placebos).
3. Coeficiente de g positivo en la mayoría de modelos (test de signo, p < 0.05), coherente con la correlación observada.

## Comprobación de selección
El lag 4 se eligió mirando 2018–2025 completo. Se recalcula la correlación media del panel en el lag 4 solo con
2018–2022. Si allí no destaca, la hipótesis procede de 2023–2025 y la evaluación en ese periodo es circular:
el resultado se tomará como no confirmatorio y la confirmación real quedará para los datos de 2026.

## Secundarias (no deciden)
Categoría 473; horizonte 1 (y_t con origen t−1, referencia y_{t−1}, y_{t−2}, y_{t−12}, aumentado + g_{t−4}).
