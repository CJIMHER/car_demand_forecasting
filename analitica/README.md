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
de los tres); modelo+familia de color, modelo+acabado+color (`acabado_color`) y
modelo+acabado+motor+color (`motor_color`), en VN y VO, para el filtro de color de la página y el color bajo
cada motor; y, en VO, esos seis niveles de la matriz también con la familia de color. Nunca el cruce
completo. Por segmento: anuncios, propios, con color (`con_color`, para
la cobertura), precio p25/p50/p75 de los que tienen precio (como `percentile_cont`) y días publicado
con la regla «≥ N».

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
- La potencia y la cilindrada salen de columnas generadas de `stock_externo_snapshot`
  (migración `0007`), que Postgres rellena desde `datos` sin tocar la carga.
- **Color** (`colores.py`, migración `0011`): el color exterior de cada anuncio sale de `color_anuncio`
  (la exportación del componente de fichas, `ingesta/colores.py`), unido por fuente + id del anuncio.
  Vale el último color con estado `ok` o `listing_color` sabido ese día (por `fetched_at`, en Madrid: sin
  mirar al futuro); si la ficha trae un VIN distinto del bastidor del anuncio, el color se anula. Sin
  ficha, el color del propio anuncio si la web lo publica (hoy no). La familia (blanco, negro, gris,
  plata, azul, rojo, verde, amarillo, naranja, marrón/beige, otros o «sin especificar») sale de
  `config/colores.yaml`: palabras clave sin mayúsculas ni tildes, gana la primera que aparece en el
  texto; aparte, el acabado de la pintura (metalizado, perla, mate). `panel_mercado_color_fuente` (por
  fuente y estado de la ficha: anuncios, con color y anulados por VIN) y
  `panel_mercado_color_sin_familia` (textos sin familia, España) alimentan «Estado de las fuentes».

## Cómo se prueba

`tests/test_mercado.py`: una prueba por regla, con capturas sintéticas escritas por el código real
de los scrapers (huecos, una fuente que empieza más tarde, retiradas). `tests/test_panel.py`
comprueba además que las cifras de la vista cuadran con una consulta SQL directa.
`tests/test_segmentos.py`: lectura de versiones reales de VO y VN, «Sin clasificar», cortes de
antigüedad y km en sus bordes, percentiles y n frente a SQL directo, migración, cobertura y la vista
(con n < 3 nunca se muestra un precio), y cada combinación de los filtros de la matriz (acabado,
combustible, motor) frente a SQL directo. `tests/test_color.py`: familias con nombres de color reales,
carga de la exportación (solo añadir, sin tocar los hechos, sin fichero no falla), VIN distinto, sin
mirar al futuro, niveles de color y cobertura frente a SQL directo, la nota de cobertura en su borde
(89 % / 90 %), el filtro de color combinable y «Estado de las fuentes». El resumen del precálculo (`fuentes_estado`) lleva las
filas de segmentos por nivel y los segundos, total y de segmentos.
