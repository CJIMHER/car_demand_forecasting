# Hito 1b · Dirección con datos reales: resumen e inventario

Rama `hito-1b-walcu-tipo`, commit aparte. Sin push y sin conectarse al servidor.

## Inventario de los bloques

El estado en producción es lo que se deduce del código y de los encargos ya desplegados: no me he conectado
al servidor.

- **Antes**: cada bloque leía su tabla con origen real si tenía alguna fila real y, si no, con las filas de
  ejemplo. En producción se mezclaban.
- **Ahora**: sin `PANEL_MODO_EJEMPLO=1`, solo filas reales.

| Bloque | Tabla | Quién la escribe | Antes, en producción | Ahora, sin modo ejemplo |
| --- | --- | --- | --- | --- |
| Stock (tarjeta) | `panel_stock_vehiculo` | Nadie todavía (stock interno de Walcu o del DMS) | Ejemplo, si se cargó | Stock publicado en la web (Mercado, real) y «stock interno: pendiente de conectar» |
| Antigüedad media (tarjeta) | `panel_stock_vehiculo` | Nadie todavía | Ejemplo | «Pendiente de conectar: stock interno (Walcu o DMS)» |
| Stock por días en exposición | `panel_stock_vehiculo` | Nadie todavía | Ejemplo | «Pendiente de conectar: stock interno (Walcu o DMS)» |
| Ventas del mes (tarjeta) | `panel_ventas_mes` | Nadie todavía (ventas totales del CRM) | Ejemplo | «Pendiente de conectar: ventas del CRM» |
| Ventas acumuladas frente a lo previsto | `panel_ventas_mes` | Nadie todavía (ventas del CRM y predicción) | Ejemplo | «Pendiente de conectar: ventas del CRM y predicción de ventas» |
| Alertas abiertas (tarjeta) y alertas de hoy | `alertas_evaluacion` y `alertas` (motor); `panel_alertas` (ejemplo) | `alertas/motor.py` (cron 09:15) | Real en cuanto el motor evalúa con datos reales | Real; sin evaluación, «Sin datos todavía» |
| El mercado en la provincia | `panel_mercado_dia` | `analitica/mercado.py` (scrapers VN y VO, cron 09:10) | Real | Real (sin cambios) |
| Precio de nuestros VO | `panel_precio_vo` | `analitica/precio.py` (mismo precálculo) | Real tras desplegar la 0020 y `make precalcular` | Real |
| **Nuevo**: Campañas de Meta con Walcu (últimas 4 semanas) | `panel_campana_semana`, `panel_campana_decision` | `ingesta/referencias_meta.py` (Meta + `make walcu-cargar`) y el motor | No existía | Real tras desplegar la 0019 y cargar Walcu; si no, «Sin datos todavía» |
| **Nuevo**: Referencias por año (2025 frente a 2026) | `panel_referencia_meta` | `make referencias-meta` | No existía | Igual que el anterior |
| **Nuevo**: Tráfico de la web por canal | `panel_trafico_canal_dia` | `make ga4` (GA4) | No existía | Real si GA4 está conectado |

**Por qué se mezclaba.**
- El origen se decidía bloque a bloque. Si en el servidor se había hecho `make ejemplo-cargar` (encargo 7.7),
  las tablas sin fuente real seguían mostrando su ejemplo junto a los bloques reales (mercado, precio,
  alertas).
- Además, la vista entera contaba como «de ejemplo»: el rol de dirección la veía desactivada salvo que
  estuviera en `PANEL_EJEMPLO_VISIBLE_PARA`.

## Qué cambia

- **Modo ejemplo explícito**: `PANEL_MODO_EJEMPLO=1`, una variable nueva del panel, vacía por defecto. Va en
  `infra/docker-compose.yml` y `.env.example`.
  - Sin ella, Dirección solo lee filas reales, y su estado para los permisos también sale solo de ellas.
  - Con ella, igual que antes. `make panel-dev` arranca con el modo ejemplo.
- **«Pendiente de conectar: <fuente>»** en los bloques cuya fuente todavía no existe. Los que tienen fuente
  pero aún no han escrito nada dicen «Sin datos todavía».
- **Stock publicado**: mientras no haya stock interno, la primera tarjeta muestra nuestros anuncios en la web.
  Son los propios del último día de Mercado, en toda España, con el reparto VN · VO.
- **Bloques nuevos**, con los mismos cálculos que Marketing (`panel/marketing.py`) y el selector de tipo:
  - Meta con Walcu de las últimas 4 semanas: gasto; leads, citas y ventas; € por lead, por cita y por venta.
  - Referencias 2025 frente a 2026: gasto; leads, citas y ventas; los tres costes; lead a cita y cita a venta;
    con la diferencia.
  - GA4 por canal.

## Decisiones tomadas sin preguntar

1. **Solo Dirección.** Ventas y Marketing siguen con la regla de siempre: un bloque es de ejemplo mientras su
   tabla no tenga filas reales, y la vista lleva la banda. Cambiarlas también es una línea en
   `contrato.VISTAS_SOLO_REAL`.
2. **Una variable nueva, no `PANEL_PERMITE_EJEMPLO`.** Esa sirve para cargar y borrar los datos de ejemplo
   (cdf_jobs), y es probable que en el servidor ya valga 1. Reutilizarla dejaría Dirección como estaba.
3. **Stock publicado en lugar de «pendiente»** en la tarjeta de stock, porque esa fuente es real. Antigüedad y
   días en exposición siguen pendientes: los días de nuestros VN publicados no están precalculados, y los
   de los VO están en el precio provisional.
4. **El stock publicado cuenta toda España**: así salen todos nuestros anuncios, sea cual sea la provincia de
   cada uno.
5. **Referencias en Dirección**: las filas de gasto, volumen, costes y tasas. Fuera de zona y número
   inexistente quedan en Marketing.

## Pruebas

- `tests/test_direccion_real.py`:
  - sin modo ejemplo y con el ejemplo cargado, ninguna etiqueta ni banda de ejemplo;
  - «Pendiente de conectar» con su fuente, stock publicado frente a SQL directo y «Sin datos todavía»;
  - con fuentes reales, los bloques de Meta y Walcu, referencias y GA4, con el gasto igual que en Marketing;
  - el modo ejemplo explícito y la variable;
  - Ventas y Marketing sin cambios;
  - los estados de la vista.
- `tests/test_contrato_panel.py`: las pruebas de los datos de ejemplo pasan con el modo ejemplo explícito.
- `tests/test_infra_panel.py`: la variable en el compose y en `.env.example`.
- Suite completa en verde.
