# analitica

Cálculos sobre las tablas de hechos. Hoy: el precálculo de la vista Mercado del panel. Supervivencia
del stock, atención relativa y predicción de ventas llegan en el hito 2.

## mercado.py · precálculo de Mercado (cron 09:10)

Lee `stock_externo_snapshot`, `eventos_externos` y `scraper_runs` y rellena las tablas derivadas
`panel_mercado_dia` (por día, tipo y zona), `panel_mercado_modelo` (además, por modelo, con los anuncios propios),
`panel_mercado_concesionario` (por concesionario, solo en la provincia del concesionario propio, con
el nombre que publica la web, el más reciente si cambia) y `panel_mercado_capturas` (por scraper y día: captura completa o no, hora, anuncios y anuncios sin
provincia). Recalcular un día sustituye ese día; las tablas de hechos no se tocan.

El modelo es el que da el scraper sin la marca delante («T-Cross», no «Volkswagen T-Cross»; la marca
sale de `config/concesionario.yaml`). Tras cambiar la normalización, `make precalcular` rehace el
histórico y sustituye las filas viejas, sin duplicar modelos.

- `python -m analitica.mercado` (`make precalcular`): todo el histórico.
- `--ultimos 8` (cron): los 8 últimos días, para recoger cargas tardías.
- `--dia AAAA-MM-DD` (`make precalcular DIA=...`): un día.

Anota cada pasada en `fuentes_estado` (fuente `panel_mercado`, tipo `precalculo`). Si falla, avisa
por Telegram con el mismo mecanismo que la copia fallida (`ingesta/aviso_copia.py`).

Reglas: las del docstring del módulo (un día cuenta solo con captura completa, y para VN solo si la
tienen vn y vn_otros; alta observada; «—» en vez de cero cuando falta una captura; ausencias, nunca
ventas, y baja solo si el scraper la retira; días publicado como cota inferior «≥ N»; precio
mediano solo con precio). Zonas: la provincia del concesionario, su comunidad y España, según
`config/concesionario.yaml`.

**Zona cercana** (`cercana`): la provincia propia y las de `propio.provincias_cercanas` de
`config/concesionario.yaml`. Es una zona más de todo el precálculo (y del selector de Mercado). En los
segmentos, además, la mediana de nuestro precio y la de la competencia por separado, con su n: las
usan los criterios de Meta (regla e y contexto de la d).

## competidores.py · por concesionario y modelo (en el mismo precálculo)

`panel_mercado_competidor`: para cada día válido y tipo, en la provincia propia, los anuncios y el
precio mediano de cada concesionario por modelo y por modelo + acabado (acabado del catálogo; sin
acabado reconocido, solo en el modelo). La usa la alerta de presión de un competidor
(`alertas/reglas.yaml`). Cuadra con `panel_mercado_modelo` (por modelo) y con
`panel_mercado_concesionario` (por concesionario); se prueba en `tests/test_alertas.py`. Tras
desplegar, `make precalcular` la rellena hacia atrás.

## segmentos.py y versiones.py · detalle de un modelo (en el mismo precálculo)

Para cada día, tipo y zona, `panel_mercado_segmento` guarda solo los niveles que muestra el detalle
de un modelo: modelo; modelo+acabado; modelo+acabado+motor; en VO, además, la matriz antigüedad×km
de modelo, modelo+acabado, modelo+combustible, modelo+motor, modelo+acabado+combustible y
modelo+acabado+motor (los filtros de la matriz; el motor implica su combustible, así que no hay cruce
de los tres); y modelo+color (vacío mientras la web publique el color vacío). Por
segmento: anuncios, propios, precio p25/p50/p75 de los que tienen precio (como `percentile_cont`) y
días publicado con la regla «≥ N».

- **Acabado y motor** (`versiones.py`): se leen del texto de versión con el catálogo
  `config/versiones.yaml` (acabados por modelo con sinónimos; comillas, tildes, mayúsculas y espacios
  normalizados). Un acabado que no está en el catálogo es «Sin clasificar»: nunca se adivina. El motor
  es tecnología (TSI, eTSI, TDI, eHybrid…) + CV del campo `power_cv` + combustible + cambio cuando el
  texto lo dice. Los textos sin clasificar van a `panel_mercado_sin_clasificar` y salen en «Estado de
  las fuentes» con la cobertura por modelo: se añaden al catálogo y `make precalcular`.
- **Combustible**: el grupo del filtro (Gasolina, Diésel, Híbrido, Eléctrico) sale de
  `combustibles` en `config/versiones.yaml`; uno que no esté allí se muestra tal cual. El motor se
  identifica por su combustible y su texto (migración `0008`).
- **Antigüedad y km**: años cumplidos desde la primera matriculación hasta el día de la captura, y
  km del anuncio; los tramos, en `config/concesionario.yaml` (`segmentos`). Sin tramos en la
  configuración no se calcula la matriz.
- La potencia, la cilindrada y el color salen de columnas generadas de `stock_externo_snapshot`
  (migración `0007`), que Postgres rellena desde `datos` sin tocar la carga.

## Cómo se prueba

`tests/test_mercado.py`: una prueba por regla, con capturas sintéticas escritas por el código real
de los scrapers (huecos, una fuente que empieza más tarde, retiradas). `tests/test_panel.py`
comprueba además que las cifras de la vista cuadran con una consulta SQL directa.
`tests/test_segmentos.py`: lectura de versiones reales de VO y VN, «Sin clasificar», cortes de
antigüedad y km en sus bordes, percentiles y n frente a SQL directo, migración, cobertura y la vista
(con n < 3 nunca se muestra un precio), y cada combinación de los filtros de la matriz (acabado,
combustible, motor) frente a SQL directo. El resumen del precálculo (`fuentes_estado`) lleva las
filas de segmentos por nivel y los segundos, total y de segmentos.
