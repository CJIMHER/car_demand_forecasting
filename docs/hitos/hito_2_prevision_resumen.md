# Hito 2 · Previsión bayesiana de leads, citas y ventas de Meta: resumen

Rama `hito-2-prevision`, sin push y sin conectarse al servidor. Parte de `9bfc33b` (salidas de stock), no de
`origin/main`: `origin/main` sigue en `0c9aecd`, sin las salidas. La migración nueva es la 0022 y va detrás de
la 0021 de las salidas.

## Resultados de la validación

**Con los datos reales no se ha podido validar desde aquí.**
- El gasto de Meta solo está en la base del servidor, y en local solo está el histórico de Walcu.
- La validación real sale con el primer `make prevision`, en «Estado de las fuentes» y en el resumen de
  `fuentes_estado` (`panel_prevision`: `publicada` y `leads_mejora`).
- Si un tipo no mejora a la referencia en leads, el panel no enseña su previsión y dice por qué (punto 4 de
  las decisiones).

Mientras tanto, la misma validación con datos sintéticos de parámetros conocidos. Las condiciones son:
- 96 semanas, de enero de 2025 a noviembre de 2026;
- leads = exp(0,5 + 0,7·log(gasto + 1) + mes + ruido de 0,25), con +0,4 antes de 2026;
- 30 % de lead a cita y 10 % de cita a venta;
- 12 orígenes × 4 semanas = 48 semanas previstas por fila.

**Gasto persistente** (cambia poco de una semana a otra, como suele pasar). Error absoluto medio por semana:

| Tipo | Medida | Gasto | Modelo | Referencia (media 4 semanas) | Cobertura del 80 % | Mejora |
| --- | --- | --- | --- | --- | --- | --- |
| VN | Leads | el que hubo | 39,9 | 60,7 | 71 % | Sí |
| VN | Leads | el previsto | 45,4 | 60,7 | 75 % | Sí |
| VN | Citas | el previsto | 14,4 | 21,0 | 73 % | Sí |
| VN | Ventas | el previsto | 1,7 | 1,7 | 79 % | No (empata) |
| VO | Leads | el que hubo | 22,3 | 37,6 | 77 % | Sí |
| VO | Leads | el previsto | 22,7 | 37,6 | 77 % | Sí |
| VO | Citas | el previsto | 8,0 | 15,3 | 81 % | Sí |
| VO | Ventas | el previsto | 0,8 | 1,1 | 100 % | Sí |

**Gasto al azar cada semana** (el caso difícil):
- Con el gasto que hubo, el modelo mejora mucho a la referencia: leads 43,8 frente a 74,0 en VN, y 43,8 frente
  a 67,2 en VO, con una cobertura del 79-81 %.
- **Con el gasto previsto no mejora a la referencia en leads**: 81,4 frente a 74,0 en VN y 72,2 frente a 67,2
  en VO. La cobertura baja al 48-56 %.
- El motivo: si el gasto real de la semana se parece poco a la media de las dos anteriores, prever con esa
  media falla. El intervalo no recoge la incertidumbre del gasto, solo la del modelo.
- Por eso el panel lo avisa cuando pasa: «Con el gasto previsto … la previsión no mejora a la media de leads …
  Mejor mirarla con el escenario de gasto que se vaya a hacer».

**Lectura**:
- El modelo responde bien a «cuántos leads, citas y ventas da este gasto», que es para lo que están los
  escenarios y la inversa.
- La previsión por defecto es tan buena como lo sea suponer que el gasto seguirá como en las dos últimas
  semanas.
- Las ventas semanales son tan pocas que la media de 4 semanas ya es difícil de mejorar.

## Qué hay

1. **Modelo** (`analitica/prevision.py`): numpy, fórmulas cerradas, sin MCMC ni scipy.
   - **Leads**: regresión bayesiana de log(leads + 1) sobre log(gasto en € + 1), 12 meses y el régimen. Antes
     de `regimen_desde` lleva nivel y pendiente propios, así que 2025 solo aporta la estacionalidad. Prior
     débil y posterior normal-gamma inversa conjugada. Cada mes tiene un prior centrado en 0 (sd 0,3): si es
     ruidoso, se encoge hacia 0.
   - **Por tipo y por modelo**: cada modelo parte de la posterior de su tipo, con la incertidumbre
     multiplicada por `modelo_inflar`. Con pocas semanas se queda cerca del tipo.
   - **Lead a cita y cita a venta**: Beta-binomiales.
     - El tipo usa sus semanas completas desde `regimen_desde`; si no hay ninguna, todo el histórico, y se
       marca «pocos datos».
     - Cada modelo usa la media de su tipo, que vale `peso_tipo` leads (o citas).
2. **Previsión** (`analitica/predictiva.py`). Para cada simulación:
   - σ² y los x·β de las 4 semanas, correlados porque comparten β, más el ruido de cada semana;
   - leads, y después citas ~ Binomial(leads, p) y ventas ~ Binomial(citas, q), con p y q de sus Beta.

   Da la mediana y el intervalo del 80 %. La semilla es fija por grupo, así que el panel y el precálculo dan
   las mismas cifras.
3. **Gasto previsto**: la media de las 2 últimas semanas terminadas de cada campaña activa (estado ACTIVE en
   Meta o, sin estado, con gasto la última semana), repartida entre VN y VO por sus leads. Sin gasto, 0.
4. **Validación** (obligatoria): 12 orígenes.
   - En cada uno se entrena con lo que se sabía al final de esa semana: las citas y ventas solo de las semanas
     ya completas (14 y 60 días).
   - Se prevén las 4 siguientes con el gasto que hubo y con el previsto.
   - Se mide el error absoluto medio y la cobertura, frente a la media de las últimas 4 semanas.
   - Los resultados se guardan en `panel_prevision_validacion` y se ven en «Estado de las fuentes».
5. **Google Trends**: solo si hay entregas cargadas. Se valida con y sin él, y por tipo se queda si baja el
   error de los leads. Si no hay entregas o no mejora, no se usa, y el resumen lo dice.
6. **Migración 0022**:
   - `panel_prevision_modelo` (posterior y publicación) y `panel_prevision_semana` (previsión por semana y
     total, por escenario, con p10, p50 y p90), las dos del contrato del panel;
   - `panel_prevision_validacion`.
7. **Panel**:
   - **Marketing**, «Previsión 4 semanas»:
     - por tipo y por modelo con campaña activa: gasto semanal, y leads, citas y ventas (mediana e intervalo);
     - el control de gasto (−50 %, Actual, +50 %, +100 %), recalculado en el momento con la posterior
       guardada, sin entrenar;
     - por tipo, «Para conseguir N citas en 4 semanas haría falta un gasto de unos X € a la semana (entre A y
       B €)», con un formulario para N;
     - los avisos «Previsión con datos hasta la semana del …», «Pocos datos» (con el motivo) y que es solo lo
       que viene de Meta.
   - **Dirección**: ventas previstas de las campañas de Meta en 4 semanas, por VN y VO, diciendo que no es el
     total del concesionario. «Ventas frente a lo previsto» sigue pendiente.
   - **Estado de las fuentes**: la tabla de validación. La cobertura fuera de 70-90 % y «no mejora» salen
     marcadas.
8. **Cálculo**:
   - cada noche, en la tarea `precalcular` (09:10): después de Mercado, con Meta de las 06:00 ya cruzada con
     Walcu, y antes de las alertas de las 09:15;
   - o con `make prevision`;
   - anota en `fuentes_estado` (`panel_prevision`).
9. **Parámetros** en `config/prevision.yaml`: régimen, semanas, intervalo, simulaciones, escenarios, objetivo,
   validación, priors y umbrales de «pocos datos».

## Decisiones tomadas sin preguntar

1. **Rama desde `9bfc33b`** (salidas de stock) y no desde `origin/main`.
   - El encargo pide hacerlo después de las salidas, y `origin/main` aún no las tiene.
   - Desde `origin/main`, mi migración chocaría con la 0021 de las salidas.
   - Si se integran las salidas antes, la rama encaja sin cambios.
2. **Fuente de datos**: `panel_campana_semana`, el cruce Meta-Walcu por semana que ya usan las reglas. Se
   rehace cada mañana con la descarga de Meta.
   - Entran solo las campañas que casan con leads de Walcu en alguna semana.
   - Entran las semanas terminadas cuya carga de Walcu se hizo después de acabar la semana.
   - Una semana con carga en la que la campaña no casa cuenta 0 leads.
3. **Leads = log(leads + 1)** sobre **log(gasto + 1)**, para admitir semanas con 0 leads. Se entrena solo con
   semanas con gasto.
4. **Publicación** (regla de CLAUDE.md: «si no la bate, no se despliega»).
   - Se publica un tipo (y, aparte, sus modelos) si en la validación el error de los leads **con el gasto que
     hubo** es menor que el de la referencia. Eso es lo que mide el modelo: cuántos leads da un gasto.
   - Si con el gasto previsto no mejora, se publica igual, con un aviso visible.
   - Sin validación (pocas semanas), no se publica.
   - Así todo se calcula y se guarda, pero el panel solo enseña lo validado.
5. **Gasto de las campañas de VN y VO**, y de las de VN con algún lead de VO: se reparte por sus leads de
   Walcu de toda su vida. Sin leads, por su ámbito; «ambos», a medias.
6. **Semanas previstas**: las 4 desde el lunes de la semana del cálculo, la semana en curso incluida.
7. **Modelos con campaña activa**: los que tienen gasto previsto. Los demás no salen en el panel.
8. **Los modelos no suman el tipo**: cada uno tiene su previsión y el panel lo dice.
9. **Inversa**:
   - el mismo gasto todas las semanas;
   - la mediana de las citas totales llega a N;
   - el intervalo: el gasto con el que llega el percentil alto (con suerte) y con el que llega el bajo;
   - se busca hasta 20 veces el gasto previsto (`objetivo_gasto_maximo`);
   - el objetivo por defecto es 1,5 veces las citas previstas (`objetivo_citas`);
   - se redondea a decenas de euros.
10. **Sin scipy**: con numpy basta, y no engorda más la imagen del panel. numpy entra en `requirements.txt` y
    lo instalan las dos imágenes. El panel solo copia `analitica/predictiva.py`, y la prueba del Dockerfile se
    amplía a analitica.
11. **Referencia de citas y ventas**: la media de las últimas 4 semanas que ya estaban completas en el origen,
    no la de las 4 últimas a secas, que aún no se conocían.
12. **Trends**: el índice de la semana anterior, el de la consulta del modelo; para el tipo, la media de sus
    modelos. Para las semanas previstas, el último conocido.

## Para revisar

- Tras `make deploy` y `make prevision`, «Estado de las fuentes»:
  - si VN y VO mejoran a la referencia en leads con el gasto que hubo (si no, no se publican);
  - la cobertura del 80 %, entre el 70 y el 90 %;
  - si sale el aviso del gasto previsto.
- Las ventas por semana son pocas. Si la validación de ventas no mejora a la referencia, es lo esperable; se ve
  en la tabla.

## Pruebas

`tests/test_prevision.py`, 16 pruebas:
- parámetros en la configuración, sin números en el código;
- recupera los parámetros conocidos: elasticidad, régimen, σ y las dos tasas;
- cobertura del 80 % entre el 70 y el 92 % en 120 semanas previstas;
- sin gasto, 0;
- un modelo con pocos datos se queda más cerca de su tipo que uno con muchos;
- más gasto, más leads, citas y ventas, y la inversa en la dirección correcta;
- validación: error, cobertura y mejora, en menos de 5 s;
- sin mirar al futuro;
- de punta a punta, en menos de 30 s (en local, alrededor de 1 s): tablas, recálculo y Trends que solo se usa
  si mejora;
- Marketing: el escenario actual recalculado en el panel coincide con el guardado; +100 % sube; la inversa;
  «pocos datos»;
- Dirección y la validación en «Estado de las fuentes»;
- la regla de publicación.

`tests/test_contrato_panel.py` y `tests/test_infra_panel.py` se adaptan: tablas nuevas, tarea nocturna, Makefile
y Dockerfile.
