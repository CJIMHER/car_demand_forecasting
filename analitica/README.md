# analitica

Cálculos sobre las tablas de hechos. Hoy: el precálculo de la vista Mercado del panel. Supervivencia
del stock, atención relativa y predicción de ventas llegan en el hito 2.

## mercado.py · precálculo de Mercado (cron 09:10)

Lee `stock_externo_snapshot`, `eventos_externos` y `scraper_runs` y rellena las tablas derivadas
`panel_mercado_dia` (por día, tipo y zona), `panel_mercado_modelo` (además, por modelo) y
`panel_mercado_capturas` (por scraper y día: captura completa o no, hora, anuncios y anuncios sin
provincia). Recalcular un día sustituye ese día; las tablas de hechos no se tocan.

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

## Cómo se prueba

`tests/test_mercado.py`: una prueba por regla, con capturas sintéticas escritas por el código real
de los scrapers (huecos, una fuente que empieza más tarde, retiradas). `tests/test_panel.py`
comprueba además que las cifras de la vista cuadran con una consulta SQL directa.
