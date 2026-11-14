# Señales externas de demanda: resultados de los tests (8-nov-2026)

Objetivo: matriculaciones nacionales (2018-01 a 2026-04). Protocolo: prerregistro, predicción directa, Huber, ventana expansiva, Clark-West con HAC, surrogates de fase o permutaciones, placebos de retardo.

| Señal | Resultado | Papel en el producto |
|---|---|---|
| Google Trends absoluto (cat. 47 y 473) | Coincidente (lag 0, por el COVID); el lag 4 prerregistrado no se confirma | Contexto |
| **Google Trends relativo al segmento (cat. 47)** | **Admitido (prerregistro de competidores).** YoY3 de atención frente a la media del segmento → YoY3 relativo de matriculaciones del trimestre siguiente: mejora del MAE +0,8%, p de permutación 0,005 (Holm 0,02), coeficiente positivo en el 100% de las estimaciones. Placebos decrecientes (lag 3 +0,8 … lag 9 0,0). Correlación agrupada 0,25 (umbral nulo 0,07) | Cuota de atención frente a rivales como KPI e indicador adelantado de la cuota de ventas en el segmento. Efecto pequeño: sirve como señal direccional, no como predicción de unidades |
| GT relativo cat. 473 | No admitido (+0,1%, p 0,20) | — |
| Wikipedia es/en, absoluto y relativo | No admitida; en.wikipedia con lag 9 refutada en ene–abr 2026 (−8,0%) | Descartada |
| Tipo de crédito al consumo a 1–5 años (BdE 19.4) | No admitido; a 12 meses, signo al revés (recuperación tras los chips) | Contexto macro |
| Expectativas del ICC (CIS) | No admitida (Δ mensual: signo correcto, MAE −0,7%, Holm 0,38) | Contexto; revisar con más ciclos |

Segmentos fijados a priori: SUV-B (incluye T-Cross), SUV-C (Tiguan, T-Roc), compacto C (Golf), utilitario B (Polo), SUV premium.
Pendiente: confirmar fuera de muestra el GT relativo con datos de 2026 de la API oficial.
Scripts y logs: scratchpad golf/ (competidores.py, out_comp/, prerregistro_competidores.md, wiki2026.py, macro_test.py, out_2026/).
