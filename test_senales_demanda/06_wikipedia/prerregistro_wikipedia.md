# Prerregistro — Wikipedia como señal de demanda (fijado el 2026-11-08, antes de ejecutar)

## Datos
Vistas mensuales de usuarios (sin bots identificados), es.wikipedia (principal) y en.wikipedia (contraste),
2018-01 a 2025-12, mismos 50 modelos del panel de Google Trends. Objetivo: matriculaciones nacionales.

## Pruebas (las mismas tres vías que con Google Trends, sin cambiar ningún parámetro)
1. Panel YoY en log (ventanas 1/3/6; objetivos YoY1 a 1 mes y YoY3 a 3 meses; todo y sin COVID), con
   referencia de surrogates conjunta (200 réplicas).
2. Prewhitening: Δlog, AR por AIC, correlación cruzada −3..+12 con Pearson, Spearman, Kendall y dCor;
   permutaciones (300) sobre el máximo de los lags.
3. Predicción directa a 4 meses con Huber (2023–2025), retardos 4..12, más horizonte 1 con retardo 4.

## Regla de decisión
Wikipedia se admite como predictor solo si, en la especificación principal
(es.wikipedia, YoY de 3 meses → YoY de matriculaciones del trimestre siguiente, todo el periodo):
- la mejora media del MAE del panel supera el percentil 95 de los surrogates, y
- la mediana de la mejora es positiva.
Si no, queda como señal de atención (contexto, ratio de captura y cuota de atención), igual que Google Trends.
Las vías 2 y 3 se informan como apoyo; un retardo que destaque en ellas solo será hipótesis para datos de 2026.
