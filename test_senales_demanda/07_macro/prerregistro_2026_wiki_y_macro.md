# Prerregistro — fijado el 2026-11-08, antes de ejecutar

## A. Comprobación fuera de muestra de en.wikipedia con lag 9 (ene–abr 2026)
- Hipótesis (salida de 2023–2025): Δlog(1+vistas en.wikipedia) con 9 meses de adelanto mejora la predicción directa a 4 meses de Δlog matriculaciones.
- Especificación idéntica a la de 2023–2025: referencia y_{t−4}, y_{t−5}, y_{t−12}; aumentado + g_{t−9}; Huber; ventana expansiva desde 2018; origen t−4.
- Meses objetivo: 2026-01 a 2026-04 (4 por modelo); mismos modelos elegibles.
- Regla (las tres): (1) mejora del MAE agregado (todas las predicciones modelo-mes) > 0; (2) mediana de la mejora por modelo > 0;
  (3) coeficiente de g positivo en la mayoría de modelos (signo, p < 0,05).
- Potencia: con 4 meses la prueba sirve sobre todo para refutar. Si se cumple, la hipótesis sigue viva, no queda confirmada.

## B. Tipos de interés al consumo y confianza del consumidor
Series: (r) tipo TEDR de nuevas operaciones de crédito al consumo a más de 1 y hasta 5 años, BdE 19.4 (DN_1TI2T0009);
(e) expectativas del ICC del CIS (agosto con LOCF; la bandera no es regresor). Control común: días laborables (calendario, conocido de antemano).
Disponibilidad en tiempo real en el origen t−4: r hasta t−6 (el BdE publica unas 5–6 semanas después), e hasta t−5.

Objetivo principal: matriculaciones totales del mercado (fila TOTAL). Evaluación: meses objetivo 2023-01 a 2026-04 (40), predicción directa a h=4, Huber, ventana expansiva.

Cuatro contrastes principales (Holm sobre los 4):
- B1 corto plazo: y = Δlog Y_t; referencia y_{t−4}, y_{t−5}, y_{t−12}, Δlaborables_t; aumentado + Δr_{t−6} (B1r) o + Δe_{t−5} (B1e).
- B2 ciclo: y = YoY3_t = log(ΣY_{t−2..t}) − log(ΣY_{t−14..t−12}); referencia YoY3_{t−4}, YoY3_{t−5}, Δ12 laborables (suma de 3 meses);
  aumentado + (r_{t−6} − r_{t−18}) (B2r) o + (e_{t−5} − e_{t−17}) (B2e).
Regla para admitir una variable (las tres): (1) mejora del MAE > 0 y Clark-West (HAC, h+2 lags) significativo al 5% tras Holm;
(2) signo esperado (r negativo, e positivo) en al menos el 80% de las estimaciones móviles; (3) mejora por encima del percentil 95 de 200 surrogates de fase de la serie macro en nivel.
Placebos informativos: retardos r 6..12 y e 5..12.
Secundario (no decide): el mismo diseño en el panel de modelos elegibles; los modelos comparten la serie macro y no son independientes, así que solo se describe.
