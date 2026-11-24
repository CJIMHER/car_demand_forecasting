# Hito 1b · Precio provisional de nuestros VO: resumen

Rama `hito-1b-precio-provisional`, desde `origin/main` en `4ce1365`. Sin push y sin conectarse al servidor.

## Qué hay

1. **Cálculo** (`analitica/precio.py`, dentro de `make precalcular`, cron 09:10).
   - Nuestros VO: los anuncios propios del scraper VO cada día válido de VO (los mismos de «Nuestros» en
     Mercado).
   - Días publicado: desde el primer día en que vimos el anuncio (fuente + id). «Al menos N días» si no lo
     vimos aparecer.
   - Segmento: mismo modelo, acabado, motor, tramo de antigüedad y tramo de km, en la zona cercana, sin
     nuestros coches y solo anuncios con precio. p25, mediana y p75 como en Mercado (`percentile_cont`).
   - Ampliación con menos de 5: sin el tramo de km, luego sin el de antigüedad, luego zona cercana más la
     comunidad. Con menos de 3 en el último nivel: «sin comparables suficientes», sin recomendación.
   - Reglas, la primera que se cumple: muy por encima (> p75 y ≥ 30 días), por encima y parado (> mediana y
     ≥ 60 días), en precio pero no sale (≤ mediana y ≥ 90 días), barato (< p25), en línea.
2. **Umbrales** en `config/concesionario.yaml`, `precio_provisional`: `min_comparables: 5`,
   `min_comparables_amplio: 3`, `dias_muy_por_encima: 30`, `dias_por_encima: 60`, `dias_en_precio: 90`.
   Ningún número de estos en el código (lo comprueba una prueba).
3. **Migración 0020**: `panel_precio_vo`, del contrato del panel (`dia`, `origen`, `calculado_at`), con
   `GRANT SELECT` a `cdf_panel`. Documentada en `docs/contrato_panel.md`.
4. **Panel** (`panel/precio.py`, macro `precio_vo` de `_bloques.html`).
   - «Precio de nuestros VO» en Ventas y en Dirección; Marketing no lo tiene.
   - Un coche por fila: precio, mediana, p25–p75, comparables con el nivel usado, días publicado y la
     recomendación. Orden: reglas 1 y 2, la 3, la 4 y el resto; dentro, de más a menos días.
   - Filtro por modelo (`?modelo_vo=`), que se conserva al cambiar de tipo.
   - El aviso «Recomendación provisional basada en el mercado actual; no es una probabilidad de venta».
5. **Datos de ejemplo**: diez coches que cubren las siete salidas y los cuatro niveles, con un «al menos».
   Una prueba comprueba que sus reglas y textos son los que daría `analitica/precio.py`.

## Decisiones tomadas sin preguntar

1. **Dónde se calcula**: en el precálculo de Mercado, como los segmentos, y se guarda en una tabla del
   contrato. El panel solo lee; no importa nada nuevo de `ingesta` (la prueba del Dockerfile sigue en verde)
   ni de `analitica`.
2. **«Al menos N días»**: la misma regla que Mercado (sin alta observada). Cubre el caso del encargo (primer
   día de datos) y también el de un anuncio que aparece tras un hueco de capturas, cuya edad real tampoco se
   sabe. Las reglas usan esa cota inferior: nunca piden bajar por días que no constan.
3. **Ampliación a la comunidad**: zona cercana más la comunidad (no solo la comunidad), para que sea siempre
   una ampliación aunque otra instalación tenga una provincia cercana de otra comunidad. El nombre de la
   comunidad sale de la configuración.
4. **Comparables solo con precio**: un anuncio sin precio no aporta a los percentiles, así que no cuenta.
5. **Sin acabado reconocido o sin dato de motor**, el coche no se compara: «Sin comparables suficientes:
   acabado sin clasificar» (o «sin dato de motor»). Compararlo con otros sin clasificar no tendría sentido.
6. **Sin km o sin matriculación**, se empieza en el primer nivel que no necesita ese dato.
7. **Sin precio publicado**: «Sin precio publicado: sin recomendación», al final de la tabla.
8. **Bordes**: «por encima» es estrictamente mayor (en el p75 o en la mediana no lo está); «por debajo del
   p25», estrictamente menor; «en la mediana o por debajo», menor o igual.
9. **Sin la sección `precio_provisional`** en la configuración, no se calcula y el resto del precálculo
   sigue; el resumen lo dice. Un valor mal escrito sí es un error.
10. **Tipo de la vista**: el bloque sale en Todo, VN y VO (es solo de VO y lo dice su título), igual que en
    Dirección.
11. **Textos** en el código (`analitica/precio.py`); los umbrales, en la configuración.

## Para revisar

- Tras `make deploy` y `make precalcular`, cuántos de nuestros VO quedan «sin comparables suficientes» y
  cuántos por acabado sin clasificar: completar `config/versiones.yaml` sube la cobertura.
- Si en el servidor hay datos de ejemplo de Ventas (`make ejemplo-cargar`), la vista sigue siendo de
  ejemplo y el rol de ventas no la ve hasta `make ejemplo-borrar` (regla de siempre: una vista con algún
  bloque de ejemplo solo la ven los roles de `PANEL_EJEMPLO_VISIBLE_PARA`).

## Pruebas

`tests/test_precio_provisional.py`:
- cada regla con sus bordes y su prioridad;
- textos y orden;
- la ampliación nivel a nivel y que se dice;
- «sin comparables suficientes», también por acabado o motor;
- coche sin km o sin matriculación;
- umbrales de la configuración (ausentes o mal escritos);
- precálculo con capturas sintéticas: nuestros coches y los de fuera de zona no cuentan; «al menos N
  días» frente a alta observada; recalcular sustituye sin tocar el ejemplo;
- datos de ejemplo coherentes;
- el bloque en Ventas y Dirección y no en Marketing, el orden, el filtro por modelo y la vista abierta a
  ventas con datos reales.

`tests/test_contrato_panel.py` y `tests/test_acciones.py`: el bloque nuevo en las pruebas de origen (con Ventas real, también el precio). Suite completa en verde.
