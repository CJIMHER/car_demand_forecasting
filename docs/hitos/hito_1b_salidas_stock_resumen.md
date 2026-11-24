# Hito 1b · Salidas de nuestro stock como estimación de ventas: resumen

Rama `hito-1b-salidas-stock`, desde `origin/main` en `0c9aecd`. Sin push y sin conectarse al servidor.

## Qué hay

1. **Cálculo** (`analitica/salidas.py`, dentro de `make precalcular`, cron 09:10).
   - **Solo nuestros anuncios** (`es_propio`, los de «Nuestros» en Mercado). Los de otros concesionarios ni se
     leen: para ellos sigue valiendo «desaparecer no es vender».
   - Un anuncio nuestro visto el día D (fuente + id) tiene una salida el D+1 si:
     1. no aparece en los K días siguientes con captura buena de su fuente; un día sin captura completa no
        cuenta y la ventana se alarga;
     2. no reaparece antes de R días desde la salida (si reaparece, se anula); las de los últimos R días son
        provisionales;
     3. no es una republicación: otro anuncio nuestro nuevo del mismo coche en la ventana de 7 días centrada
        en la salida.
        - VO: modelo, acabado, motor, año de matriculación, color y km a menos de 1.000.
        - VN: modelo, acabado, motor y color.
   - **Arranque**: nada sale antes del primer día con captura buena de la fuente más K días.
   - **Cada salida guarda**:
     - tipo, modelo, acabado, motor, año, tramo de km y color;
     - último precio publicado;
     - días publicado, con «al menos» como en Mercado;
     - fecha de salida y si es provisional;
     - la mediana de su segmento.
   - **Mediana del segmento**: la del último día que se vio, con los niveles del precio provisional. En VN, sin
     tramos de antigüedad ni de km.
   - **Recálculo**:
     - cada noche, la ventana de los últimos R + K días;
     - `make precalcular` sin argumentos, todo el histórico.
2. **Umbrales** en `config/concesionario.yaml`, `salidas_stock`:
   - `dias_ausente: 3` (K);
   - `dias_reaparicion: 14` (R);
   - `republicacion_dias: 7`;
   - `republicacion_km: 1000`.

   Ninguno está escrito en el código: lo comprueba una prueba. Sin la sección, no se calcula y el resumen del
   precálculo lo dice.
3. **Migración 0021**:
   - tabla `panel_salidas_stock`, del contrato del panel (`dia` = fecha de salida, `origen`, `calculado_at`);
   - `GRANT SELECT` a `cdf_panel`;
   - sin matrícula ni bastidor;
   - documentada en `docs/contrato_panel.md`.
4. **Dirección**:
   - **Tarjeta «Salidas del mes»** (antes «Ventas del mes»): mes en curso, VN · VO, cuántas son provisionales
     y «estimación de ventas».
   - **Bloque «Salidas de nuestro stock (estimación de ventas)»**:
     - mes anterior y mes en curso, por VN y VO y por modelo;
     - las provisionales en gris, «+ N provisionales» junto a las confirmadas;
     - la nota del encargo.
   - **«Ventas acumuladas frente a lo previsto»**: «Pendiente de conectar: predicción de ventas».
5. **Ventas**: «Salidas de nuestro stock: últimas 4 semanas».
   - Un coche por fila, de la más reciente a la más antigua.
   - Fecha de salida; las provisionales, en gris.
   - Último precio y días publicado.
   - Mediana del segmento y diferencia del precio frente a ella.
6. **Datos de ejemplo**: once salidas del mes en curso y del anterior, VN y VO, con provisionales.

## Decisiones tomadas sin preguntar

1. **Captura buena**:
   - Es la que la comprobación diaria da por buena: una pasada success cargada en `scraper_runs`, por fuente.
   - No he añadido otro criterio de captura incompleta, como un recuento de anuncios bajo: hoy no existe en la
     comprobación diaria.
   - El scraper ya rechaza las caídas anómalas, y la ventana de K días buenos absorbe el resto.
2. **Por fuente**, no por tipo: un anuncio de `vn` necesita capturas buenas de `vn`, aunque `vn_otros` falle
   ese día. Así un fallo de una fuente no frena las salidas de la otra.
3. **Reaparición**: se anula si vuelve a verse menos de R días después de la fecha de salida. A los R días o
   más, la salida vale, y el anuncio empieza otra vida.
4. **Provisional**: cuando entre la salida y el día del cálculo hay menos de R días.
5. **Ventana de republicación**: «los 7 días alrededor» son 3 días antes y 3 después de la salida (7 días
   centrados). En la configuración va el 7, como dice el encargo.
6. **Republicación uno a uno**:
   - Cada anuncio nuevo anula como mucho una salida: la más cercana en fechas.
   - Así, si salen dos VN iguales y entra uno, sigue contando una salida.
   - Las reglas de VN son amplias (modelo, acabado, motor y color), y podrían anular ventas reales al entrar
     coches nuevos iguales.
7. **Mismo coche sin catálogo**:
   - Acabado sin clasificar: se compara el texto de versión.
   - Color o km desconocidos: solo casan con otro desconocido.
8. **Mediana del segmento**:
   - Es la del último día que se vio, el día de su último precio, no la del día de la salida, en que ya no
     estaba publicado.
   - Se calcula como el precio provisional, también para VN.
   - Sin la sección `precio_provisional`, o si ese día no es válido para el tipo, queda sin mediana.
9. **Días publicado**: desde el primer día en que se vio hasta el último, con «al menos» si no se vio
   aparecer.
10. **Dirección, tarjeta y bloque**:
    - La tarjeta cuenta todas las del mes, las provisionales incluidas, y dice cuántas lo son.
    - En el bloque, las confirmadas y aparte, en gris, «+ N provisionales».
    - El mes en curso llega hasta hoy.
11. **«Sin datos todavía»** en la tarjeta y el bloque mientras el precálculo no haya escrito salidas: la fuente
    existe, así que no es «Pendiente de conectar».
12. **Ventas**: el bloque de salidas cuenta para el estado de la vista como los demás. Como el precio
    provisional, si hay datos de ejemplo de Ventas cargados, la vista sigue siendo «de ejemplo» para el rol de
    ventas (regla de siempre, 7.7 de `infra/deploy.md`).

## Para revisar

- Tras `make deploy` y `make precalcular`, cuántas salidas salen por mes frente a las ventas que conocéis:
  - de más: traspasos o retiradas largas;
  - de menos: republicaciones de VN que anulan ventas.
- Las provisionales: cuántas se anulan al reaparecer en los 14 días.

## Pruebas

`tests/test_salidas_stock.py`:
- umbrales de la configuración (ausentes o mal escritos) y ninguno escrito en el código;
- salida normal;
- K días buenos;
- hueco de capturas (no es salida);
- reaparición dentro de R (se anula) y a los R días (vale);
- provisionales;
- arranque (los primeros K días no cuentan);
- republicación en VO (km, ventana, cada dato) y en VN, con un anuncio nuevo que anula una sola salida;
- precálculo con capturas sintéticas escritas por el código real del scraper:
  - cada regla;
  - un competidor que desaparece no sale;
  - la mediana sin nuestros coches;
  - la ventana nocturna;
- en el escenario de Mercado, ningún competidor genera salidas;
- Dirección por mes, tipo y modelo, con provisionales;
- Ventas con la diferencia frente a la mediana.

`tests/test_contrato_panel.py`, `tests/test_direccion_real.py`, `tests/test_precio_provisional.py` y
`tests/test_acciones.py` se adaptan a la tabla y los bloques nuevos. Suite completa en verde.
