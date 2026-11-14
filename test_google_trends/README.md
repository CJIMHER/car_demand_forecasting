# Tests de Google Trends sobre matriculaciones nacionales

Cinco tests para comprobar si Google Trends (GT) anticipa las matriculaciones nacionales por modelo
(España, 2018–2025). Fecha: 8 de noviembre de 2026.

**Conclusión:** GT se mueve con las matriculaciones en el mismo mes (lag 0), pero no las anticipa una vez se
descuentan los shocks comunes (COVID, mercado). Ninguna transformación, ningún retardo y ninguna medida, lineal
o no lineal, supera de forma robusta a la referencia de series artificiales (surrogates). El lag 4,
prerregistrado como último candidato, no se confirma.

## Estructura

| Carpeta | Test |
|---|---|
| `datos/` | Matriculaciones nacionales 2018-01 a 2026-04 (`matriculaciones_raw_2018_2026.csv`), GT top-50 en categorías 47 y 473 (`google_trends_top50_2cat.xlsx`) y extracción larga de GT, categoría 47, para 5 familias (`google_trends_raw.csv`) |
| `01_golf_yoy/` | VW Golf: YoY en log, ventanas de 1, 3 y 6 meses; correlación y predicción; robustez (BH, HAC, surrogates) |
| `02_panel_yoy/` | El mismo test YoY en 37 modelos del top-50, con referencia de surrogates conjunta |
| `03_troc_tcross/` | VW T-Roc y T-Cross (solo categoría 47 utilizable) |
| `04_prewhitening/` | Logs, estacionariedad, prewhitening (Box-Jenkins), correlación cruzada de −3 a +12 meses: Pearson, Spearman, Kendall, distance correlation |
| `05_confirmacion_lag4/` | Confirmación prerregistrada del lag 4 (ver `prerregistro_lag4.md`) |

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
```

El último argumento numérico es el número de réplicas de surrogates o permutaciones. Las semillas son fijas.

## Resultados

### 01 · Golf (YoY en log)
- Categoría 47: correlación alta en todo el periodo (Pearson 0,71, ventana de 1 mes, lag 0; p de surrogates = 0,002),
  que cae a 0,07 sin el COVID. Es estructura compartida.
- Categoría 473: la ventana de 3 meses mejora un 13,6% el error al predecir el trimestre siguiente (CW p = 0,005; surrogates p = 0,03),
  con **coeficiente negativo**. La señal se concentra en 2023–2025 (ciclo del modelo y oferta). 8 de 40 contrastes superan BH al 10%.
- La ventana de 1 mes, la aceleración y el cambio del YoY no aportan nada.

### 02 · Panel YoY (37 modelos)
- En las 24 especificaciones, la mediana de la mejora del MAE es negativa.
- Referencia de surrogates conjunta (200 réplicas): ninguna especificación se separa (p entre 0,32 y 0,81).
- 20 de 863 contrastes superan BH, casi todos con 13–20 evaluaciones y varios con el MAE empeorando. El signo no es consistente.

### 03 · T-Roc y T-Cross (categoría 47)
- En la categoría 473 la mediana es 0 (T-Roc: 51/96 meses a cero; T-Cross: 68/96). No es utilizable.
- T-Roc: la única correlación significativa (Pearson 0,40, lag 0) es la del COVID. Fuera de muestra, empeora el error entre un 2% y un 20%.
- T-Cross: correlación alta sin desfase (Spearman 0,68), propia del lanzamiento en 2019–2021. No predice.

### 04 · Prewhitening (41 modelos en categoría 47, 36 en la 473)
- Todo el periodo: mejor lag = 0 en 35 de 41 modelos; correlación media en el lag 0 de 0,41 (cat. 47) y 0,37 (cat. 473).
- Sin COVID: la correlación media en el lag 0 baja a 0,05 y 0,01, y el mejor lag se reparte entre −3 y +12.
- Con todo el periodo, Pearson queda muy por encima de Spearman: la sostienen valores extremos. Ninguna medida no lineal aporta una relación que la lineal no vea.
- Lags de panel "significativos" sin COVID (−2, 4, 8, 11, 12): signo alterno y lags negativos, coherente con shocks comunes. Los p-valores de panel suponen modelos independientes.

### 05 · Confirmación prerregistrada del lag 4
- Comprobación de selección: el lag 4 ya era el primero en 2018–2022 (r medio 0,10), así que evaluarlo en 2023–2025 es una prueba confirmatoria.
- Predicción directa a 4 meses (Huber, ventana expansiva, meses objetivo 2023–2025):
  mejora media −1,0%, mediana −0,7%, mejoran el 29% de los modelos, CW significativo en 2/41, coeficiente positivo en el 56% (p = 0,27).
- El mejor lag entre 4 y 12 es el 6 (−0,2%). Ninguno mejora.
- Regla de decisión: no se cumple ninguna de las tres condiciones → **NO CONFIRMADO**.
- Nota: la ejecución guardada usó 2 réplicas de surrogates. La decisión no depende de ellas, porque la condición 1 exige
  además que la mediana sea positiva y es negativa.

## Limitaciones
- GT viene de una sola extracción por palabra clave ("volkswagen golf"), sin topic ni varias descargas promediadas.
- La categoría 473 tiene muchos ceros en modelos de volumen medio.
- Matriculaciones nacionales: reflejan también la oferta (chips 2021–2022) y las ventas a flotas, no solo la intención de compra.
- Con la API oficial de Google Trends (escala consistente), la confirmación del lag 4 puede repetirse con datos de 2026.
