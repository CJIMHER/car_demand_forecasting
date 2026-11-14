# Señales externas de demanda sobre matriculaciones nacionales

Ocho experimentos para comprobar si las señales externas (Google Trends, Wikipedia, tipos de interés al consumo y confianza
del consumidor) anticipan las matriculaciones nacionales por modelo (España, 2018–2026). Fecha: 8 de noviembre de 2026.

## Resumen de todo
| # | Experimento | Resultado |
|---|---|---|
| 01–05 | Google Trends absoluto (YoY, prewhitening, lag 4 prerregistrado) | Coincidente, no anticipa |
| 06 | Wikipedia es/en (las tres vías + fuera de muestra 2026) | No admitida; en.wikipedia lag 9 refutada en 2026 |
| 07 | Tipo de crédito al consumo (BdE) y expectativas del ICC (CIS) | No admitidos |
| **08** | **Atención relativa al segmento (competidores)** | **GT cat. 47 admitido: +0,8% MAE, p perm. 0,005 (Holm 0,02)** |

---
## Parte 1 · Google Trends absoluto (01–05)

**Conclusión:** GT se mueve con las matriculaciones en el mismo mes (lag 0), pero no las anticipa una vez se
descuentan los shocks comunes (COVID, mercado). Ninguna transformación, ningún retardo y ninguna medida, lineal
o no lineal, supera de forma robusta a la referencia de series artificiales (surrogates). El lag 4,
prerregistrado como último candidato, no se confirma.

### Estructura

| Carpeta | Test |
|---|---|
| `datos/` | Matriculaciones nacionales 2018-01 a 2026-04 (`matriculaciones_raw_2018_2026.csv`), GT top-50 en categorías 47 y 473 (`google_trends_top50_2cat.xlsx`) y extracción larga de GT, categoría 47, para 5 familias (`google_trends_raw.csv`) |
| `01_golf_yoy/` | VW Golf: YoY en log, ventanas de 1, 3 y 6 meses; correlación y predicción; robustez (BH, HAC, surrogates) |
| `02_panel_yoy/` | El mismo test YoY en 37 modelos del top-50, con referencia de surrogates conjunta |
| `03_troc_tcross/` | VW T-Roc y T-Cross (solo categoría 47 utilizable) |
| `04_prewhitening/` | Logs, estacionariedad, prewhitening (Box-Jenkins), correlación cruzada de −3 a +12 meses: Pearson, Spearman, Kendall, distance correlation |
| `05_confirmacion_lag4/` | Confirmación prerregistrada del lag 4 (ver `prerregistro_lag4.md`) |
| `06_wikipedia/` | Wikipedia es y en con los scripts 02, 04 y 05, más la comprobación fuera de muestra de 2026 |
| `07_macro/` | Tipo de crédito al consumo a 1–5 años y expectativas del ICC sobre el mercado total |
| `08_competidores/` | Atención relativa a la media del segmento frente a matriculaciones relativas |

Datos añadidos en `datos/`: `wikipedia_pageviews.csv` (53 modelos, 2016-01 a 2026-04), `wikipedia_como_gt.xlsx` (Wikipedia en el formato de GT),
`be1903.csv` y `be1904.csv` (BdE, tipos de nuevas operaciones), `icc_cis_raw.csv` e `icc_cis.csv` (ICC del CIS, agosto con LOCF), `laborables_es.csv`.

## Cómo ejecutarlo

```bash
pip install -r requirements.txt
# desde esta carpeta:
M=datos/matriculaciones_raw_2018_2026.csv
X=datos/google_trends_top50_2cat.xlsx
python 01_golf_yoy/golf_yoy_test.py   $M $X 01_golf_yoy/resultados
python 01_golf_yoy/golf_robustez.py   $M $X 01_golf_yoy/resultados
python 02_panel_yoy/panel50_test.py   $M $X 02_panel_yoy/resultados 200          # ~10 min
python 03_troc_tcross/modelo_test.py  $M $X datos/google_trends_raw.csv 03_troc_tcross/resultados 300   # ~3 min
python 04_prewhitening/prewhite_panel.py $M $X 04_prewhitening/resultados 300  # ~10 min
python 05_confirmacion_lag4/confirm_lag4.py $M $X 05_confirmacion_lag4/resultados 200   # 200 réplicas: varias horas

# 06 · Wikipedia (el xlsx ya está en datos/; se reconstruye así)
W=datos/wikipedia_como_gt.xlsx
python 06_wikipedia/construir_wikipedia_como_gt.py datos/wikipedia_pageviews.csv $W
python 02_panel_yoy/panel50_test.py   $M $W 06_wikipedia/resultados/panel_yoy 200
python 04_prewhitening/prewhite_panel.py $M $W 06_wikipedia/resultados/prewhitening 300
python 05_confirmacion_lag4/confirm_lag4.py $M $W 06_wikipedia/resultados/prediccion_lag4_12 2
python 06_wikipedia/wiki2026.py $M datos/wikipedia_pageviews.csv 05_confirmacion_lag4      # ~1 min

# 07 · Macro (~5 min)
python 07_macro/macro_test.py $M datos/be1904.csv datos/icc_cis.csv datos/laborables_es.csv 07_macro/resultados 200 05_confirmacion_lag4

# 08 · Competidores (~20 min)
python 08_competidores/competidores.py $M $X $W 08_competidores/resultados 200 05_confirmacion_lag4
```
Los scripts 06–08 leen la tabla de nombres de modelos (`NAME_MAP`) de `05_confirmacion_lag4/confirm_lag4.py`.
En `06_wikipedia/`, la salida de `confirm_lag4.py` aplicada a Wikipedia está en `prediccion_lag4_12/` (en ese caso cat47 = es y cat473 = en).
`06_wikipedia/descargar_wikipedia.py` es el script de Colab que generó `wikipedia_pageviews.csv`; `07_macro/construir_icc.py` y
`dias_laborables.py` son tus scripts de preparación del ICC y del calendario.

El último argumento numérico es el número de réplicas de surrogates o permutaciones. Las semillas son fijas.

### Resultados

#### 01 · Golf (YoY en log)
- Categoría 47: correlación alta en todo el periodo (Pearson 0,71, ventana de 1 mes, lag 0; p de surrogates = 0,002),
  que cae a 0,07 sin el COVID. Es estructura compartida.
- Categoría 473: la ventana de 3 meses mejora un 13,6% el error al predecir el trimestre siguiente (CW p = 0,005; surrogates p = 0,03),
  con **coeficiente negativo**. La señal se concentra en 2023–2025 (ciclo del modelo y oferta). 8 de 40 contrastes superan BH al 10%.
- La ventana de 1 mes, la aceleración y el cambio del YoY no aportan nada.

#### 02 · Panel YoY (37 modelos)
- En las 24 especificaciones, la mediana de la mejora del MAE es negativa.
- Referencia de surrogates conjunta (200 réplicas): ninguna especificación se separa (p entre 0,32 y 0,81).
- 20 de 863 contrastes superan BH, casi todos con 13–20 evaluaciones y varios con el MAE empeorando. El signo no es consistente.

#### 03 · T-Roc y T-Cross (categoría 47)
- En la categoría 473 la mediana es 0 (T-Roc: 51/96 meses a cero; T-Cross: 68/96). No es utilizable.
- T-Roc: la única correlación significativa (Pearson 0,40, lag 0) es la del COVID. Fuera de muestra, empeora el error entre un 2% y un 20%.
- T-Cross: correlación alta sin desfase (Spearman 0,68), propia del lanzamiento en 2019–2021. No predice.

#### 04 · Prewhitening (41 modelos en categoría 47, 36 en la 473)
- Todo el periodo: mejor lag = 0 en 35 de 41 modelos; correlación media en el lag 0 de 0,41 (cat. 47) y 0,37 (cat. 473).
- Sin COVID: la correlación media en el lag 0 baja a 0,05 y 0,01, y el mejor lag se reparte entre −3 y +12.
- Con todo el periodo, Pearson queda muy por encima de Spearman: la sostienen valores extremos. Ninguna medida no lineal aporta una relación que la lineal no vea.
- Lags de panel "significativos" sin COVID (−2, 4, 8, 11, 12): signo alterno y lags negativos, coherente con shocks comunes. Los p-valores de panel suponen modelos independientes.

#### 05 · Confirmación prerregistrada del lag 4
- Comprobación de selección: el lag 4 ya era el primero en 2018–2022 (r medio 0,10), así que evaluarlo en 2023–2025 es una prueba confirmatoria.
- Predicción directa a 4 meses (Huber, ventana expansiva, meses objetivo 2023–2025):
  mejora media −1,0%, mediana −0,7%, mejoran el 29% de los modelos, CW significativo en 2/41, coeficiente positivo en el 56% (p = 0,27).
- El mejor lag entre 4 y 12 es el 6 (−0,2%). Ninguno mejora.
- Regla de decisión: no se cumple ninguna de las tres condiciones → **NO CONFIRMADO**.
- Nota: la ejecución guardada usó 2 réplicas de surrogates. La decisión no depende de ellas, porque la condición 1 exige
  además que la mediana sea positiva y es negativa.

### Limitaciones de la parte 1
- GT viene de una sola extracción por palabra clave ("volkswagen golf"), sin topic ni varias descargas promediadas.
- La categoría 473 tiene muchos ceros en modelos de volumen medio.
- Matriculaciones nacionales: reflejan también la oferta (chips 2021–2022) y las ventas a flotas, no solo la intención de compra.
- Con la API oficial de Google Trends (escala consistente), la confirmación del lag 4 puede repetirse con datos de 2026.

---
## 06 · Wikipedia (prerregistro: `06_wikipedia/prerregistro_wikipedia.md`)
- Especificación principal (es.wikipedia, YoY3 → YoY de matriculaciones del trimestre siguiente): mejora media −5,9%, p de surrogates 0,54;
  mediana −4,4% → **no admitida**.
- Prewhitening sin COVID: 2 de 39 modelos significativos. Predicción directa h=4 con lags 4..12 en es: todos negativos.
- en.wikipedia con lag 9 destacó en 2023–2025 (+1,1% de media, 61% de modelos mejoran, 95% de coeficientes positivos) y quedó como hipótesis.
- **Comprobación fuera de muestra, ene–abr 2026** (prerregistro: `prerregistro_2026_wiki_y_macro.md`, parte A; 42 modelos):
  MAE agregado −8,0%, mediana −7,5%, mejora el 31% → **refutada**. En 2026 los lags 4 (+0,3%) y 11 (+3,1%) salen positivos: ruido esperable entre 9 retardos.

## 07 · Tipos de interés al consumo y confianza del consumidor (prerregistro: `prerregistro_2026_wiki_y_macro.md`, parte B)
Series: tipo TEDR de nuevas operaciones de crédito al consumo a 1–5 años (BdE 19.4, DN_1TI2T0009) con retardo 6 (publicación);
expectativas del ICC del CIS con retardo 5. Control: días laborables. Objetivo: mercado total; meses objetivo 2023-01 a 2026-04; h = 4.

| Contraste | Mejora MAE | CW p (Holm) | Signo esperado | Surrogates p |
|---|---|---|---|---|
| Δ tipo consumo | −0,8% | 1,00 | 10% | 0,47 |
| Δ expectativas ICC | −0,7% | 0,38 (sin ajustar 0,10) | 100% | 0,41 |
| Tipo, variación a 12 meses | −13,9% | 0,78 | 2,5% | 0,68 |
| Expectativas, variación a 12 meses | −6,2% | 1,00 | 5% | 0,52 |

Ninguno admitido. El tipo sale con el signo al revés: en 2022–23 subieron los tipos a la vez que las matriculaciones se recuperaban tras la
escasez de chips. 2018–2026 contiene un solo ciclo de tipos mezclado con shocks de oferta: identificación débil. Panel de 35 modelos (descriptivo):
mediana negativa en los cuatro contrastes.

## 08 · Atención relativa a los competidores (prerregistro: `08_competidores/prerregistro_competidores.md`)
Idea: restar la media del segmento elimina los shocks comunes (COVID, chips, tipos, mercado). Segmentos fijados a priori: SUV-B, SUV-C, compacto C,
utilitario B, SUV premium. Objetivo: YoY3 relativo de matriculaciones del trimestre siguiente; referencia: su propia inercia; Huber agrupado;
meses objetivo 2023–2025; nulo: 200 permutaciones de etiquetas dentro del segmento.

| Señal | Mejora MAE | p perm. (Holm) | Coef. positivo | Admitida |
|---|---|---|---|---|
| **GT cat. 47** | **+0,8%** | **0,005 (0,02)** | 100% | **Sí** |
| GT cat. 473 | +0,1% | 0,20 (0,61) | 100% | No |
| Wikipedia es | −0,1% | 0,56 | 100% | No |
| Wikipedia en | −0,5% | 0,995 | 100% | No |

- Placebos de retardo en GT cat. 47: +0,8 (lag 3), +0,6 (4–6), +0,5 (7), +0,2 (8), 0,0 (9): decaimiento ordenado, propio de un adelanto real.
- Correlación agrupada relativa ≈ 0,25 en lags 0–6 (umbral nulo 0,07); en absoluto y sin COVID era 0,05.
- VW (k=3, r lag 0 / lag 3): T-Cross 0,34 / 0,38; Golf 0,34 / 0,30; Polo 0,12 / 0,19; T-Roc 0,17 / −0,08; Tiguan 0,02 / 0,17.

**Lectura:** señal real y pequeña. Útil como indicador direccional de la cuota frente a los rivales (*share of search*), no para predecir unidades.

**Cautelas:** la correlación plana entre los lags 0 y 6 sugiere un componente común persistente (ciclo de vida: lanzamientos y renovaciones), no
controlado del todo. El prerregistro se fijó en la sesión tras varias pruebas fallidas: la confirmación con 2026 (API oficial de GT) debe
registrarse públicamente (OSF) antes de descargar los datos, con precisión direccional como métrica de decisión.
