# Prerregistro — atención relativa frente a competidores (fijado el 2026-11-08, antes de ejecutar)

## Idea
Al restar la media del segmento se eliminan los shocks comunes (COVID, chips, tipos, mercado). Pregunta: si un modelo gana atención
frente a sus rivales directos, ¿gana después matriculaciones frente a ellos?

## Segmentos (fijados por producto, no por datos)
- SUV-B: Arona, Captur, 2008, Stonic, Kona, T-Cross, Juke, Puma, Yaris Cross, C3 Aircross, MG ZS, Duster
- SUV-C: Tucson, Sportage, Qashqai, Ateca, 3008, RAV4, Tiguan, Kuga, CX-5, Karoq, T-Roc, C-HR, Niro, XCeed, CX-30, Arkana
- Compacto C: Golf, León, Corolla, Focus, Ceed, i30, Mégane, 308, C4
- Utilitario B: Sandero, Ibiza, Yaris, Clio, 208, i20, C3, Corsa, Polo, Fabia
- SUV premium: X1, XC40
(Tesla Model 3 queda fuera por no tener segmento comparable en el panel.)

## Medidas
YoY_k(x)_t = log(media x[t−k+1..t]) − log(media x[t−k−11..t−12]) (log1p en GT). Relativo: YoY del modelo − media del YoY del segmento
en ese mes (mínimo 2 modelos con dato). Señales: GT cat47, GT cat473, Wikipedia es, Wikipedia en. 2018–2025.

## Contraste principal (k=3, una prueba por señal, Holm sobre las 4)
Objetivo: YoY3 relativo de matriculaciones en t (trimestre t−2..t). Origen t−3 (trimestre siguiente, sin solapamiento).
Referencia: relS_{t−3}, relS_{t−6}. Aumentado: + relA_{t−3}. Regresión de Huber agrupada (todos los modelos), ventana expansiva,
meses objetivo 2023-01 a 2025-12.
Nulo: 200 permutaciones de las etiquetas de atención dentro de cada segmento (la media del segmento no cambia; solo se rompe la
correspondencia modelo-atención).
Regla: se admite si (1) mejora del MAE agrupado > 0 con p de permutación < 0,05 tras Holm, y (2) coeficiente de relA positivo en ≥ 80%
de las estimaciones móviles.

## Informativo (no decide)
Correlación agrupada relA_t frente a relS_{t+l}, l = −3..12, con k = 1, 3, 6 y su nulo de permutación; retardos de atención 3..9 como
placebos; detalle de los modelos VW frente a su segmento.
