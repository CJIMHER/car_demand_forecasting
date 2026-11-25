# Contrato de datos del panel

Qué tablas lee cada vista del panel, qué significa cada columna y quién la rellenará. Conectar
una fuente consiste en escribir filas reales en estas tablas, sin tocar las vistas. Las tablas
se crean en la migración `0005` (`infra/migraciones/versions/0005_contrato_panel.py`); la `0006`
(`0006_marketing_por_tipo.py`) añade el ámbito de las campañas y el tipo VN/VO de Marketing; la `0013`
(`0013_campanas_por_reglas.py`), el ámbito «ambos» y «sin datos de mercado». La
correspondencia entre bloques y tablas está en `panel/contrato.py`.

Mercado no entra en este contrato: lee `panel_mercado_*`, que el precálculo de las 09:10 rellena
siempre con datos reales (ver `analitica/README.md`).

## Reglas comunes

- **Columnas de todas las tablas**: `dia`, `origen` y `calculado_at`.
  - `dia`: día de cálculo, o el día del dato cuando el grano es diario (leads, gasto).
  - `origen`: `'real'` o `'ejemplo'`.
  - `calculado_at`: cuándo se escribió la fila.
- **El origen va en la clave.** Una fila real y una de ejemplo del mismo día conviven sin pisarse.
- **Tablas derivadas**, como `panel_mercado_*`:
  - quien las rellena sustituye sus filas;
  - la regla «nada se sobrescribe» es para las tablas de hechos;
  - el panel solo las lee: `cdf_panel` tiene SELECT y nada más.
- **Sin datos personales**:
  - vehículos, por número de stock;
  - leads, solo agregados por día, modelo, tipo (VN/VO) y campaña;
  - ninguna columna con nombre, teléfono, correo ni identificador de cliente. Una prueba lo comprueba.
- **Salidas de modelo**: llevan siempre `_p10`, `_p50` y `_p90`, `modelo_version` y `n`, el número de casos en que se apoya. Nunca un punto solo.
  - La base exige p10 ≤ p50 ≤ p90.
  - Las reglas (recomendaciones, alertas) llevan su versión en `modelo_version`, o en `regla` en el caso de las alertas.
- **Unidades**:
  - importes en céntimos de euro (`_cents`);
  - probabilidades entre 0 y 1;
  - porcentajes en tanto por cien (`_pct`, y las medidas con `unidad = '%'`);
  - días, enteros.

## Dirección: solo datos reales salvo en el modo ejemplo explícito

Sin `PANEL_MODO_EJEMPLO=1` (variable del panel, vacía por defecto), Dirección nunca muestra filas de ejemplo,
aunque estén cargadas (`make ejemplo-cargar`): cada bloque lee solo filas reales. Sin ellas, dice «Pendiente de
conectar: <fuente>» si su fuente todavía no existe (stock interno y la predicción de ventas) o «Sin datos
todavía» si la fuente existe y aún no ha escrito nada. Sin stock interno real, la tarjeta de stock muestra
nuestro stock publicado en la web (Mercado). El estado de la vista para los permisos sale también solo de las
filas reales. Ventas y Marketing no cambian. Con `PANEL_MODO_EJEMPLO=1` (y en `make panel-dev`), Dirección se
comporta como las demás vistas.

## Un bloque no mezcla orígenes

Cada bloque de una vista lee una o varias tablas (`panel/contrato.py`, `BLOQUES`). Su origen se
decide así:

1. **real** si cualquiera de sus tablas tiene alguna fila real;
2. si no, **ejemplo** si alguna tiene filas de ejemplo;
3. si no, ninguno: el bloque dice «Sin datos todavía», nunca un cero.

Después, todas las tablas del bloque se leen solo con ese origen. En consecuencia:

- En cuanto entra la primera fila real de una tabla, sus filas de ejemplo dejan de mostrarse. Lo
  mismo pasa con las filas de ejemplo de las demás tablas del mismo bloque.
- Si un bloque lee dos tablas y solo una tiene ya filas reales, la parte que falta dice «Sin datos
  todavía». No se completa con ejemplo.
  - Ejemplo: entran los leads de Walcu antes que el gasto de Meta. Las tarjetas y la tabla de
    campañas de Marketing pasan a reales y dicen «Sin datos todavía» hasta que llegue Meta.
- Las filas de ejemplo no hace falta borrarlas para conectar una fuente. `make ejemplo-borrar` las
  quita cuando se quiera.

**Etiquetas y estado de la vista:**

- Todo bloque leído con origen ejemplo lleva la etiqueta «Datos de ejemplo».
- Una vista con algún bloque de ejemplo lleva una banda arriba que lo dice.
- Estado de la vista (`permisos.py`):

| Estado | Cuándo | Quién la ve |
| --- | --- | --- |
| `real` | Algún bloque real y ninguno de ejemplo | Todos los roles con permiso |
| `ejemplo` | Algún bloque de ejemplo | Solo los roles de `PANEL_EJEMPLO_VISIBLE_PARA` (por defecto, `tecnico`). Los demás la ven desactivada, con «Se activa al conectar sus datos» |
| `vacia` | Sin filas | Desactivada para todos |

## Bloques de cada vista

| Vista | Bloque | Tablas |
| --- | --- | --- |
| Dirección | Tarjeta de stock, tarjeta de antigüedad, stock por días en exposición (el stock del CRM: API de Walcu) | `panel_stock_vehiculo` |
| Dirección | Tarjeta «Ventas del mes»: las del CRM o, sin ellas, «Salidas del mes» (estimación) | `panel_ventas_crm` y `panel_salidas_stock` |
| Dirección | Bloque «Ventas del CRM» por mes, tipo y modelo (solo si hay ventas del CRM) | `panel_ventas_crm` |
| Dirección | Bloque «Salidas de nuestro stock» (estimación de ventas o, con ventas del CRM, comprobación) | `panel_salidas_stock` |
| Dirección | Gráfico de ventas acumuladas frente a lo previsto (sin modo ejemplo: «Pendiente de conectar: predicción de ventas») | `panel_ventas_mes` |
| Dirección | Tarjeta de alertas abiertas, alertas de hoy | `alertas_evaluacion` y `panel_alertas` (ver más abajo) |
| Ventas y Marketing | Alertas para su rol | `alertas_evaluacion` y `panel_alertas` |
| Dirección | Tarjeta del mercado | `panel_mercado_dia`. Siempre real: fuera del contrato |
| Dirección | Stock publicado (sin stock interno real, en lugar de la tarjeta de stock) | `panel_mercado_dia` (anuncios propios). Siempre real |
| Dirección | Campañas de Meta con Walcu, últimas 4 semanas (lo mismo que Marketing) | `panel_campana_semana` y `panel_campana_decision` |
| Dirección | Referencias por año (2025 frente a 2026) | `panel_referencia_meta` |
| Dirección | Tráfico de la web por canal (GA4) | `panel_trafico_canal_dia` |
| Dirección | Ventas previstas de las campañas de Meta, próximas 4 semanas, por VN y VO | `panel_prevision_modelo` y `panel_prevision_semana` |
| Dirección y Ventas | Precio de nuestros VO | `panel_precio_vo` |
| Ventas | Resumen y lista de vehículos | `panel_riesgo_vehiculo` |
| Ventas | Detalle del vehículo | `panel_riesgo_vehiculo` y `panel_curva_grupo` |
| Ventas y Dirección | Precio de nuestros VO (provisional; Marketing no lo ve) | `panel_precio_vo` |
| Ventas | Salidas de nuestro stock de las últimas 4 semanas, un coche por fila | `panel_salidas_stock` |
| Ventas | Stock VO en el CRM: cuántos, días de media desde su entrada y los que no están publicados | `panel_stock_vehiculo` |
| Marketing | Tarjetas de 28 días | `panel_campana_dia` y `panel_leads_dia` |
| Marketing | Campañas: del gasto a la venta, con su salud | `panel_campana_dia`, `panel_campana_semana` (Walcu por semanas, como las reglas), `panel_campana_salud` y `panel_campana_decision` |
| Marketing | Recomendación por modelo | `panel_recomendacion_modelo` |
| Marketing | Atención frente a sus rivales (Todo y VN) | `panel_atencion_relativa` |
| Marketing | Ocasión: dónde hay stock que mover (solo VO) | `panel_riesgo_vehiculo`, la misma tabla que Ventas |
| Marketing | Cuándo lanzar una campaña general | `panel_semana_campana` |
| Marketing | Qué dieron las campañas pasadas | `panel_campana_resultado` |
| Marketing | Tráfico de la web por canal | `panel_trafico_canal_dia` |
| Marketing | Campañas de las últimas 4 semanas (Meta y Walcu) | `panel_campana_semana` y la decisión de `panel_campana_decision` |
| Marketing | Referencias históricas | `panel_referencia_meta` |
| Marketing | Previsión 4 semanas (escenarios de gasto y «para conseguir N citas», recalculados con la posterior guardada) | `panel_prevision_modelo` |

**Marketing por tipo.** La vista tiene el selector Todo / Nuevos (VN) / Ocasión (VO):

| Bloque | Todo | VN o VO |
| --- | --- | --- |
| Tarjetas | Todas las campañas | Solo las del ámbito elegido, sin las de ambos; lo dicen («sin la campaña de VN y VO») |
| Campañas | Todas, con todos sus leads | Las del ámbito y las de ambos. De una de ambos: los leads, citas y ventas de ese tipo, y «—» en coste por lead, cita y venta, con la nota «gasto común a VN y VO» |
| Recomendación | Las de VN y VO, cada una con su etiqueta | Las del tipo |
| Atención / Ocasión | Atención | VN: atención. VO: en su lugar, el stock de ocasión por modelo |
| Semanas | `tipo = 'todo'` | `tipo` elegido |
| Campañas pasadas | Filas `tipo = 'todo'` | Las del ámbito (fila `todo`) y las de ambos (fila del tipo) |
| Tráfico por canal | Todas las sesiones | Solo las de página de destino de ese tipo; dice cuántas no tienen tipo y cuentan solo en «Todo» |

El gasto es siempre de la campaña: nunca se reparte entre VN y VO con ninguna regla. Una campaña de
ámbito VN o VO se muestra con todos sus leads en cualquier vista; solo las de ambos se parten por tipo.
El ámbito no depende del tipo de campaña: una general de VO («VW [VO][2026] Black Friday») cuenta en VO
con su coste, como una de modelo.

El panel muestra de cada tabla el último `dia` con el origen del bloque. Sobre esas filas solo
hace recuentos, sumas, medias y cocientes; ningún modelo se ajusta al servir una página.

## Tablas

Hitos según `docs/desarrollo.md`.

| Fuente | Hito |
| --- | --- |
| Walcu | 1 |
| Meta, GA4 y Google Trends | 2 |
| Supervivencia | 3 |
| Alertas y reglas | 4 |
| Predicción de ventas | 5 |

### panel_stock_vehiculo · stock propio

- **Grano**: día × vehículo (número de stock).
- **Fuente**: Walcu (`stock_interno_snapshot`), hito 1. Los vehículos `new`, `demo` y `km0` son VN; `used`, VO.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `stock_id` | texto | Número de stock de Walcu (`stock_number`) |
| `tipo` | VN / VO | |
| `modelo`, `version` | texto | Del catálogo de versiones, sin la marca delante |
| `dias_exposicion` | días | Días en stock ese día |
| `precio_cents` | céntimos | Precio de venta publicado |

- **La rellena**: la carga de Walcu, una vez al día.
- **Comparación con el mes anterior**: Dirección compara con el último día del mes anterior que haya en la tabla. Si no lo hay, no compara.

### panel_ventas_mes · ventas del mes frente a la banda prevista

- **Grano**: día de cálculo × tipo (`VN`, `VO`, `todo`) × día del mes.
- **Fuente**: Walcu para las ventas reales. La banda la da la referencia sencilla del hito 4 (ritmo del mismo mes en años anteriores con su error empírico) y la sustituye el modelo del hito 5.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `ventas_acumuladas` | ventas | Ventas confirmadas en Walcu del día 1 al `dia_mes`. NULL en los días que aún no han pasado |
| `previsto_p10`, `_p50`, `_p90` | ventas | Ventas acumuladas previstas a ese día del mes |
| `modelo_version`, `n` | | Versión de la referencia o modelo; `n` = meses de histórico en que se apoya |

- **La fila `todo` la escribe el modelo**: los percentiles no se suman, así que el panel no suma VN + VO.
- **«Lo normal: 8 de cada 10 meses caen aquí»** es la banda p10–p90.

### Alertas: tabla alertas del motor (real) y panel_alertas (ejemplo)

Desde la sesión 3 del hito 1b, el motor (`alertas/`, cron 09:15) escribe sus propias tablas
(migración `0009`):

- `alertas`: una fila por día, regla, rol y clave, con texto, cifras (JSON), origen y estado
  (nueva, abierta, cerrada). Una alerta calculada sobre filas de ejemplo solo puede ir al rol técnico.
- `alertas_evaluacion`: qué reglas se evaluaron cada día y con qué origen, con el formato de las tablas
  del contrato (`dia`, `origen`, `calculado_at`).

Los bloques de alertas leen `alertas_evaluacion` y `panel_alertas`, con la regla de siempre: en cuanto
el motor ha evaluado una regla con datos reales, el bloque es **real** y las alertas salen de la tabla
`alertas` (nuevas y abiertas de la última evaluación; dice «Ninguna alerta abierta» si no salta
ninguna). Hasta entonces, las de ejemplo de `panel_alertas`. Como la regla de fuente caída es real
desde el primer día, el bloque de alertas pasa a real con la primera pasada del motor; una vista solo
con bloques reales se abre a sus roles aunque los demás bloques digan «Sin datos todavía».

`panel_alertas` queda para los datos de ejemplo (`make ejemplo-cargar`); nadie escribe en ella filas reales.

### panel_alertas · alertas abiertas (ejemplo)

- **Grano**: día × alerta abierta ese día.
- **Fuente**: datos de ejemplo. Las reales, en la tabla `alertas` del motor (arriba).

| Columna | Unidad | Significado |
| --- | --- | --- |
| `alerta_id`, `regla` | texto | Identificador y regla que la dispara |
| `tema` | ventas, stock, campanas, mercado, datos | Lo que se muestra antes del destinatario |
| `tipo` | VN / VO / NULL | NULL: afecta a los dos. El selector de Dirección filtra por aquí |
| `destinatarios` | roles | `direccion`, `ventas` (jefe de ventas), `marketing`, `tecnico` |
| `nivel` | aviso / info | Punto ámbar o azul |
| `texto` | texto | El motivo en una frase, con los números que la disparan |
| `abierta_desde` | fecha | «Nuevas hoy»: `abierta_desde = dia` |
| `stock_id`, `campana_id`, `modelo` | texto | Lo que cita la alerta, para enlazarlo con Ventas y Marketing |

### panel_riesgo_vehiculo · riesgo de cada vehículo en stock

- **Grano**: día × vehículo.
- **Fuente**: modelo de supervivencia (hito 3, `analitica/supervivencia/probabilidad.py`), con el stock de Walcu, los comparables del stock externo y los leads.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `stock_id`, `tipo`, `modelo`, `version`, `detalle`, `dias_exposicion` | | Copia de lo que el modelo usó, para que la vista no mezcle tablas. `detalle`: motor, cambio y color en VN; año y km en VO |
| `grupo` | texto | Celda de la curva (clave de `panel_curva_grupo`) |
| `p_venta90_p10`, `_p50`, `_p90` | 0–1 | P(venta en los próximos 90 días \| lleva `dias_exposicion` días) = 1 − S(a+90)/S(a) |
| `precio_mercado_pct` | % | Nuestro precio frente a la mediana de los similares del stock externo. Positivo: más caro. NULL si no hay similares |
| `similares_n` | coches | Similares publicados en la provincia |
| `campana_activa` | sí/no | El modelo tiene una campaña activa |
| `sugerencia` | campana, revisar_precio, mantener | Regla sobre la probabilidad y el precio. Decide el jefe de ventas |
| `leads_14`, `leads_14_previos` | leads | Leads del modelo en los 14 días anteriores y en los 14 previos a esos |
| `modelo_version`, `n` | | `n` = episodios de la curva de su grupo |

### panel_curva_grupo · curva de salida de cada grupo

- **Grano**: día × grupo.
- **Fuente**: modelo de supervivencia, hito 3 (Kaplan-Meier con entrada tardía y caída jerárquica).

| Columna | Unidad | Significado |
| --- | --- | --- |
| `descripcion` | texto | Cómo se nombra el grupo («T-Roc nuevos») |
| `t_dias` | días | Puntos de la curva |
| `s_p10`, `s_p50`, `s_p90` | 0–1 | Proporción que sigue sin venderse en cada punto |
| `mediana_p10`, `_p50`, `_p90` | días | Días hasta que se vende la mitad. NULL si la curva no baja del 50 % |
| `n`, `n_ventas` | | Episodios y ventas confirmadas |
| `modelo_version` | | |

### panel_leads_dia · leads agregados

- **Grano**: día × modelo × tipo × campaña (NULL: sin campaña).
- **Fuente**: Walcu (`leads_agg`), hito 1. La campaña se cruza con Meta en el hito 2.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `tipo` | VN / VO | Tipo del vehículo por el que pregunta el lead (no el ámbito de la campaña) |
| `leads`, `citas`, `ventas` | número | Citas y ventas cuentan el día en que ocurren, atribuidas a la campaña del lead |
| `leads_cp_zona`, `leads_cp_fuera` | número | Leads con código postal dentro y fuera de la zona cercana (la provincia propia y `propio.provincias_cercanas` de `config/concesionario.yaml`). NULL: sin dato. Los leads sin código postal son `leads` menos los dos. El código postal se lee del lead de Walcu al agregar y **nunca se guarda**: solo este recuento |

Nada que identifique a una persona: el agregado se hace antes de guardar.

### panel_campana_dia · gasto por campaña

- **Grano**: día × campaña.
- **Fuente**: Meta Marketing API (`campanas_meta_dia`, nivel campaña). Las filas reales las rehace
  `ingesta/campanas.py` tras cada descarga de Meta o GA4 y con `make campanas`: una fila por día y
  campaña **clasificada** con las reglas de `config/campanas.yaml`. Las que no casan con ninguna regla no
  entran: salen en «Estado de las fuentes» → «Campañas sin regla», con el motivo.
- **Tipo, ámbito y modelo salen del nombre de la campaña** (o de su asignación a mano en
  `config/campanas.yaml`, que manda): [VN] → VN, [VO] → VO; sin etiqueta, «ocasión»/«VO» → VO y
  «nuevos»/«VN» → VN (palabras enteras); sin ninguna, ambos; general si contiene
  una palabra de `reglas.generales` (days, friday, liquidación, boutique); si no, de modelo: lo que va
  entre el año y el mes, normalizado con el catálogo de `config/versiones.yaml`. El nombre vigente (el
  del último día) clasifica todos los días de la campaña.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `campana_id`, `titulo` | texto | Identificador y nombre de la campaña en Meta |
| `tipo_campana` | modelo / general | De un modelo (`modelo` obligatorio) o sin modelo concreto |
| `ambito` | VN / VO / ambos | A qué stock va dirigida; ambos: a los dos. Independiente del tipo: una general puede ser de VN o de VO. El gasto no se reparte entre VN y VO |
| `sin_mercado` | sí/no | Campaña de un modelo que no está en el catálogo de turismos que capturamos (Caravelle, de Vehículos Comerciales): sin datos de mercado. La regla e y la comparación con la competencia (regla d, subir presupuesto) dicen que no se pueden aplicar en lugar de decidir |
| `inicio` | fecha | Primer día de la campaña: de aquí salen sus días de vida |
| `gasto_cents` | céntimos | |
| `impresiones`, `clics` | número | |
| `leads_meta` | número o vacío | Leads que cuenta Meta ese día (`campanas_meta_dia.leads`). Con ellos se aplican las reglas b, c y f de los criterios de Meta mientras no hay leads de Walcu en `panel_leads_dia`; las cifras dicen «leads según Meta». Marketing sigue contando los leads de `panel_leads_dia` |

**Cifras de Marketing:**

- **Ventana**: 28 días que acaban en el último `dia` con gasto.
- **Leads, citas y ventas atribuidas**: las de `panel_leads_dia` con campaña. En VN o VO, las de
  las campañas de ese ámbito; de una de ambos, solo las del tipo elegido.
- **Coste por lead**: gasto / leads. Se compara con los 28 días anteriores.
- **Coste por venta**: gasto / ventas.
- **Campaña activa**: tiene gasto el último día.

### panel_campana_salud · salud de cada campaña

- **Grano**: día × campaña × eslabón.
- **Fuente**: modelo de salud de campañas, hito 4. Necesita el historial de Meta.

**Qué significa:**

- Cada eslabón tiene una medida, que se compara con su referencia: la distribución de esa medida en
  campañas anteriores **de su mismo ámbito** (VN, VO o ambos) **con los mismos días de vida**.
- El estado sale de la probabilidad de que la campaña esté peor que esa referencia:
  - **vigilar** desde 0,7;
  - **revisar** desde 0,9;
  - **bien** por debajo;
  - **sin datos suficientes** con menos de 7 días de vida o sin referencia (n pequeño).
- Los umbrales son del modelo: van en su configuración, no en el panel.
- **Estado de la campaña en el panel**: el peor de sus eslabones con datos. Si ninguno tiene datos, «sin datos suficientes».
- **Eslabones que fallan**: se listan los que están en vigilar o revisar, con sus cifras.

**Eslabones y medidas:**

| Eslabón | Medida | Unidad | Mejor |
| --- | --- | --- | --- |
| entrega | Coste por mil impresiones | € | bajo |
| anuncio | Clics por impresión | % | alto |
| web | Leads por clic | % | alto |
| calidad_lead | Citas por lead | % | alto |
| stock | Unidades del modelo en stock | uds | alto |

**Columnas:**

| Columna | Unidad | Significado |
| --- | --- | --- |
| `dias_vida` | días | Días desde el inicio de la campaña |
| `medida`, `unidad`, `mejor` | | Qué se mide, en qué unidad y si es mejor alto o bajo |
| `valor` | | La medida de la campaña |
| `ref_p10`, `_p50`, `_p90` | | La referencia a los mismos días de vida |
| `prob_peor` | 0–1 | Probabilidad de estar peor que la referencia |
| `estado` | bien, vigilar, revisar, sin_datos | |
| `modelo_version`, `n` | | `n` = campañas anteriores de la referencia |

### panel_campana_decision · decisión de cada campaña (criterios de Meta)

- **Grano**: día × campaña activa.
- **Fuente**: motor de alertas (`alertas/criterios.py`) con los criterios de `docs/criterios_marketing_meta.md`
  y sus umbrales en `alertas/reglas.yaml` («criterios_meta»). Origen: el de las filas de Meta y Walcu
  de las que sale. No la carga `make ejemplo-cargar`: con datos de ejemplo, la calcula el motor sobre ellos.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `decision` | continuar, revisar, parar, subir, sin_juzgar | Se juzga desde el segundo día de vida |
| `reglas` | JSON | Cada regla (a, b, c, d, e, f, subir) con su estado (activa, no, sin_volumen, sin_dato), su texto con las cifras frente al umbral y su n, y qué revisar |
| `leads`, `citas`, `ventas`, `dias_vida` | | Desde el inicio de la campaña |
| `modelo_version`, `n` | | Versión de las reglas; `n` = leads |

En la vista Marketing, la decisión es la columna de cada campaña; la salud frente a su referencia
(`panel_campana_salud`) queda como detalle explicativo, no decide.

### panel_recomendacion_modelo · recomendación por modelo

- **Grano**: día × tipo × modelo. `tipo` VN: por modelo del stock nuevo; VO: del stock de ocasión.
- **Fuente**: el motor de alertas con los criterios de Meta (`docs/criterios_marketing_meta.md`). Las
  filas reales salen ya con la regla e (stock y precio en la zona cercana, del precálculo de Mercado);
  la decisión de las campañas, cuando Meta y Walcu tengan filas reales.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `accion` | aumentar, mantener, revisar, parar, lanzar, no_lanzar (y reducir, cambiar de los datos de ejemplo anteriores) | Criterios de Meta: la decisión de las campañas del modelo (subir → aumentar, continuar → mantener) o, sin campaña, la regla e |
| `regla`, `cifras`, `prioridad` | | La regla que la decide, sus cifras y, en «lanzar», el orden (más stock y mejor precio primero) |
| `motivo` | texto | Una frase con las cifras que la justifican |
| `modelo_version`, `n` | | Versión de las reglas; `n` = días de datos en que se apoyan |

No lleva intervalo porque es una decisión, no una cifra. Las cifras en que se apoya están en las demás tablas.

### panel_atencion_relativa · atención frente a los rivales

- **Grano**: día × modelo.
- **Fuente**: Google Trends, descargado a mano una vez al mes (`ingesta/gtrends.py`: `gtrends_semana`). Las
  filas reales las escribe la carga de cada entrega, con `dia` = fecha de la descarga.
- **Cálculo**: para cada modelo, su cuota de búsquedas en cada comparación (su valor entre la suma de los
  términos de la comparación) en las últimas 4 semanas completas frente a las 4 anteriores. Con varias
  comparaciones (más de 4 competidores), la media de las cuotas, todas con el mismo peso. «<1» cuenta 0,5.
  La semana de la descarga no cuenta (va a medias).

| Columna | Unidad | Significado |
| --- | --- | --- |
| `mes` | fecha | Primera semana usada (real) o mes de la señal (ejemplo) |
| `relativo_puntos` | puntos | 100 × (cuota reciente − cuota anterior). En las filas de ejemplo: 100 × (YoY3 del modelo − media del YoY3 de su conjunto) |
| `n_competidores` | | Competidores con dato (mínimo 2) |
| `entrega` | fecha | Fecha de la descarga de Google Trends (vacía en el ejemplo) |
| `semana_fin` | fecha | Primer día de la última semana completa usada |
| `cuota_pct`, `cuota_anterior_pct` | % | Cuota del modelo en las 4 últimas semanas y en las 4 anteriores |
| `n_comparaciones` | | Comparaciones de Trends en que se apoya |

No es salida de un modelo, así que no lleva intervalo. Es una señal direccional: la nota de la vista lo dice.

### panel_semana_campana · cuándo lanzar una campaña general

- **Grano**: día × tipo (`todo`, VN, VO) × semana (lunes), las seis siguientes. El índice es la
  venta esperada de ese tipo.
- **Fuente**: estacionalidad del modelo de ventas (hito 5) más reglas de calendario.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `nivel` | favorable, normal, poco_favorable | |
| `indice_p10`, `_p50`, `_p90` | veces | Ventas esperadas de la semana frente a una semana media |
| `motivos` | JSON | Lista de `{tipo, texto}`. Tipos: estacionalidad, calendario, stock_envejecido, atencion, presion_competencia |
| `modelo_version`, `n` | | `n` = años de histórico |

Es orientativo: describe cómo viene cada semana y no mide el efecto de lanzar en una u otra.

### panel_campana_resultado · qué dieron las campañas pasadas

- **Grano**: día × campaña terminada × tipo.
  - Toda campaña tiene una fila `tipo = 'todo'` con todos sus leads y ventas.
  - Las de ámbito ambos tienen además una fila VN y otra VO, con los leads y ventas de ese tipo y
    lo esperado sin campaña para ese tipo. La base no admite filas VN o VO en una campaña que no sea de ambos.
- **Fuente**: referencia sin campaña del modelo de ventas, hito 5. El efecto medido con grupo de control llega en el hito 6.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `titulo`, `ambito`, `inicio`, `fin`, `gasto_cents` | | El gasto, entero en todas las filas: no se reparte |
| `tipo` | todo / VN / VO | |
| `leads`, `ventas` | número | Observados durante la campaña |
| `leads_sin_p10`, `_p50`, `_p90` y `ventas_sin_p10`, `_p50`, `_p90` | número | Lo esperado en esas fechas sin campaña |
| `veredicto_leads`, `veredicto_ventas` | por_encima, normal, por_debajo | Por encima de p90, entre p10 y p90, o por debajo de p10 |
| `modelo_version`, `n` | | |

Es una comparación con un modelo, no un experimento.

### panel_trafico_canal_dia · tráfico de la web por canal

- **Grano**: día × canal × tipo de la página de destino.
- **Fuente**: GA4 (`ga4_trafico_dia_vigente`). Las filas reales las rehace `ingesta/campanas.py` tras
  cada descarga de GA4 y con `make campanas`, con las reglas de `config/canales.yaml`
  (`ingesta/canales.py`):
  - canal por la fuente y el medio de la sesión, en orden: Meta, Google Ads, portales, correo,
    búsqueda orgánica, directo y referencia. Si ninguna regla casa, la agrupación de canales por
    defecto de GA4 (`respaldo_ga4`) y, si tampoco, «otros»;
  - una sesión con el nombre (utm_campaign) o el ID de una campaña descargada de Meta es de Meta,
    llegue con la fuente que llegue;
  - tipo VN o VO si las palabras de la ruta de la página de destino lo dicen (`paginas`); si no, o si
    dicen los dos, `sin_tipo`.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `canal` | texto | `meta`, `google_ads`, `portales`, `correo`, `organica`, `directo`, `referencia`, `otros` (y los que añada `config/canales.yaml`); el nombre en el panel sale de ese fichero |
| `tipo` | VN / VO / sin_tipo | Por la página de destino. Las `sin_tipo` cuentan solo en «Todo» |
| `sesiones`, `sesiones_interaccion` | número | Sesiones y sesiones con interacción (engaged sessions) |
| `eventos_clave` | número | Eventos clave (key events) de GA4 |

**Cifras de Marketing:** 28 días que acaban en el último `dia` con datos, frente a los 28 anteriores.

### panel_campana_semana · Meta y Walcu por campaña y semana

- **Grano**: semana (lunes de la semana ISO) × campaña de Meta (o campaña de Walcu sin campaña de Meta:
  `campana_id` vacío).
- **Fuente**: `campanas_meta_dia_vigente` (nivel campaña, sumado de lunes a domingo) y
  `walcu_campana_semana_vigente` (origen Facebook; AiVORA fuera), unidas por el nombre normalizado
  (`config/campanas_walcu.yaml`), sin las campañas de otras marcas (`propio.prefijos_excluidos` y
  `propio.palabras_excluidas` de `config/concesionario.yaml`). Las rehace `ingesta/referencias_meta.py` tras
  cada descarga de Meta y GA4, tras cada carga de Walcu y con `make referencias-meta`. `dia`: el último día
  con datos de Meta.
- **Cohorte**: las citas y las ventas de Walcu cuentan en la semana del lead y llegan después. Una semana
  tiene las citas completas si, el día en que se cargó, su domingo tenía al menos `cohorte.lead_a_cita_dias`
  (14) y las ventas, `cohorte.cita_a_venta_dias` (60) (`config/campanas_walcu.yaml`).

| Columna | Unidad | Significado |
| --- | --- | --- |
| `semana` | fecha (lunes) | Semana ISO |
| `campana_id`, `titulo`, `ambito`, `tipo_campana`, `modelo` | texto | Campaña de Meta y su clasificación (`config/campanas.yaml`); `ambito` vacío: sin regla |
| `gasto_cents`, `impresiones`, `clics`, `leads_meta` | número | De Meta en la semana |
| `walcu` | sí/no | Hay carga de Walcu de esa semana |
| `citas_completas`, `ventas_completas` | sí/no | Cohorte cumplida para citas y para ventas; vacías sin carga de Walcu |
| `cruce` | nombre / equivalencia / vacío | Cómo casa con Walcu. Vacío: ningún lead de Walcu con ese nombre (sus cifras de Walcu quedan vacías, nunca cero). Una campaña que casa en alguna semana tiene 0 en las semanas con carga sin leads |
| `leads`, `citas`, `ventas` y sus `_vn`, `_vo` | número | De Walcu, por semana del lead. Cita: el lead tiene negociación (API: cita con `showed_up`); venta: negociación cerrada como vendida (API: lead en estado de venta, con o sin cita). Coste por venta |
| `ventas_con_cita` y sus `_vn`, `_vo` | número | Ventas con una cita asistida antes de la venta: la tasa de cita a venta (criterio d, previsión). En las cargas por CSV, igual a `ventas` |
| `leads_cercana`, `leads_fuera`, `leads_sin_zona`, `num_inexistente` | número | Zona por código postal (zona cercana del concesionario) y números inexistentes |

**Cifras de Marketing («Campañas de las últimas 4 semanas»):** las 4 últimas semanas terminadas el último
`dia` con datos de Meta. Coste por lead = gasto / leads de Walcu de las 4 semanas; coste por cita, solo con
las semanas con las citas completas, y por venta, con las de ventas completas («—» si no hay ninguna). Debajo,
una fila por semana con sus leads, citas y ventas y su estado: completa, ventas incompletas, citas y ventas
incompletas o sin carga de Walcu. En VN o VO, las de ambos con sus leads, citas y ventas de ese tipo y sin
costes (su gasto no se reparte).

### panel_referencia_meta · referencias de coste por año

- **Grano**: año ISO × tipo (VN, VO, todo). Solo las semanas con datos de Meta y carga de Walcu a la vez, ya
  terminadas el día en que se cargaron.
- **Fuente**: la misma que `panel_campana_semana`. Leads de Walcu: todos los de Facebook de ese tipo (casen o
  no con una campaña). Gasto: el de las campañas de VN o de VO va a su tipo; el de las de ambos o sin regla
  se reparte en proporción a sus leads de Walcu de cada tipo y, si no tienen, solo cuenta en «todo».

| Columna | Unidad | Significado |
| --- | --- | --- |
| `anio`, `tipo`, `desde_semana`, `hasta_semana`, `semanas` | | Año ISO, tipo y semanas que cubre |
| `gasto_cents`, `leads_meta`, `leads`, `citas`, `ventas` | número | Sumas del año |
| `leads_cercana`, `leads_fuera`, `leads_sin_zona`, `num_inexistente` | número | Para % fuera de zona (fuera / con código postal) y % de número inexistente (/ leads) |
| `semanas_citas`, `gasto_citas_cents`, `leads_citas`, `citas_citas` | número | Solo las semanas con las citas completas: lead a cita y coste por cita |
| `semanas_ventas`, `gasto_ventas_cents`, `citas_ventas`, `ventas_ventas` | número | Solo las semanas con las ventas completas: coste por venta (con todas las ventas) |
| `ventas_con_cita`, `ventas_con_cita_ventas` | número | Ventas con una cita asistida antes (del año y de las semanas con las ventas completas): cita a venta |

Las de 2025 (`criterios_meta.c.referencia_anio`) son la «media» de las reglas c y d.

### panel_campana_otra_marca · campañas de otras marcas del grupo

- **Grano**: día × campaña de Meta de otra marca (`propio.prefijos_excluidos` y `propio.palabras_excluidas`
  de `config/concesionario.yaml`). Esas campañas no están en `panel_campana_dia`: no salen en las tarjetas ni
  en el bloque de campañas, y no se juzgan con los criterios. La rehace `ingesta/campanas.py`.
- Columnas: `campana_id`, `titulo`, `gasto_cents`, `impresiones`, `clics`.
- **Cifras de Marketing:** debajo del bloque de campañas, «Campañas de otras marcas en esta cuenta: N, gasto
  X € en 28 días» (campañas con gasto en la misma ventana de 28 días). Sin ninguna, no hay línea.

### panel_precio_vo · precio provisional de nuestros VO

- **Grano**: día del precálculo de Mercado × anuncio VO propio (`fuente`, `id_anuncio`: el anuncio en la web de
  stock, sin nada del cliente). Lo escribe `analitica/precio.py` dentro de `make precalcular`, con origen
  `real`; recalcular un día sustituye sus filas reales (las de ejemplo no se tocan). Es provisional: lo
  sustituirá el modelo de probabilidad de venta.
- Columnas: `modelo`, `version`, `acabado` (NULL si no está en el catálogo), `motor`, `antiguedad` y `km`
  (etiquetas de los tramos de `config/concesionario.yaml`), `precio_cents` (NULL sin precio publicado),
  `dias_publicado` y `dias_cota` (true: «al menos N días», no lo vimos aparecer), `nivel` (`segmento`,
  `sin_km`, `sin_antiguedad`, `comunidad` o NULL sin comparables suficientes), `zona` (`cercana` o
  `comunidad`), `n_comparables`, `p25_cents`, `mediana_cents`, `p75_cents`, `regla` (`muy_por_encima`,
  `por_encima_parado`, `en_precio_no_sale`, `barato`, `en_linea`, `sin_comparables`, `sin_precio`), `orden`
  (1: reglas 1 y 2; 2: regla 3; 3: regla 4; 4: el resto) y `recomendacion` (el texto, con la ampliación si la
  hubo).
- **Comparables**: anuncios de otros concesionarios (nunca los nuestros) con precio, del mismo modelo, acabado,
  motor, tramo de antigüedad y tramo de km, en la zona cercana. Con menos de `min_comparables` se amplía: sin el
  tramo de km, sin el de antigüedad, zona cercana más la comunidad; en el último nivel bastan
  `min_comparables_amplio`. Umbrales en `config/concesionario.yaml` (`precio_provisional`).
- **Cifras de Ventas y Dirección**: «Precio de nuestros VO», un coche por fila ordenado por `orden` y, dentro,
  por días publicado de más a menos; filtro por modelo (`?modelo_vo=`); el aviso «Recomendación provisional
  basada en el mercado actual; no es una probabilidad de venta».

### panel_salidas_stock · salidas de nuestro stock publicado (estimación de ventas)

- **Grano**: salida × anuncio propio (`fuente`, `id_anuncio`: el anuncio en la web de stock; ni matrícula ni
  bastidor, nada del cliente). `dia` es la fecha de salida: el día siguiente al último en que se vio. Lo
  escribe `analitica/salidas.py` dentro de `make precalcular`, con origen `real`; cada noche recalcula la
  ventana de los últimos R + K días (`dias_reaparicion` + `dias_ausente`), y el histórico entero con
  `make precalcular` sin argumentos. Las filas de ejemplo no se tocan.
- **Solo nuestros anuncios** (`es_propio`, los de «Nuestros» en Mercado). Para los de otros concesionarios
  desaparecer no es vender: no se leen.
- **Cuándo hay salida** (umbrales en `config/concesionario.yaml`, `salidas_stock`):
  - no aparece en los `dias_ausente` días siguientes con captura buena de su fuente (un día sin captura
    completa no cuenta: la ventana se alarga);
  - no reaparece antes de `dias_reaparicion` días (si reaparece, se anula); las de los últimos
    `dias_reaparicion` días son provisionales;
  - no es una republicación: ningún anuncio nuestro nuevo del mismo coche en la ventana de
    `republicacion_dias` días centrada en la salida (VO: modelo, acabado, motor, año, color y km a menos de
    `republicacion_km`; VN: modelo, acabado, motor y color);
  - arranque: solo desde el primer día con captura buena de la fuente más `dias_ausente` días.
- Columnas: `tipo`, `modelo`, `version`, `acabado` (NULL si no está en el catálogo), `motor`, `anio` (año de
  matriculación, VO), `km` (etiqueta del tramo, VO), `color`, `precio_cents` (último precio publicado),
  `dias_publicado` y `dias_cota` (true: «al menos N días»), `ultimo_dia`, `provisional`, y la mediana de su
  segmento el último día que se vio: `nivel`, `n_comparables`, `mediana_cents` (los mismos niveles que
  `panel_precio_vo`; en VN, sin tramos de antigüedad ni de km; NULL sin comparables suficientes).
- **Cifras de Dirección**: tarjeta «Salidas del mes» (mes en curso, con VN · VO y las provisionales) y bloque
  por mes en curso y anterior, tipo y modelo, con las provisionales en gris y la nota «Estimación: un coche
  que deja de estar publicado. Incluye coches traspasados a otras sedes o empresas del grupo. Se validará con
  las ventas del CRM».
- **Cifras de Ventas**: las salidas de las últimas 4 semanas, un coche por fila: fecha (provisional en gris),
  último precio, días publicado, mediana del segmento y la diferencia del precio frente a ella.

### panel_ventas_crm · ventas del CRM (API de Walcu)

- **Grano**: mes de la venta × tipo (VN, VO) × modelo; `ventas` es un recuento (todas las ventas) y
  `ventas_con_cita`, las de leads con una cita asistida (`showed_up`) que empezó antes de la venta. Lo escribe
  `ingesta/walcu_crm.py` después de cada carga de la API de Walcu (`make walcu-api`, cron 06:30), con origen
  `real`, rehaciéndolo entero (`dia` = día del cálculo). Solo con datos de la API y el mapeo de
  `config/walcu_api.yaml` definido (estados de venta del lead y location); si no, no escribe nada y Dirección
  sigue con las salidas.
- **Venta**: un lead de venta que está ahora en un estado de venta (o de sus hitos Firmado, Matriculado,
  Entregado). Su mes es el del primer Firmado (si no, el del primer estado de venta). Si el lead sale del estado
  de venta (venta anulada), deja de contar. Los traspasos de coches entre sedes no son ventas.
- **Tipo y modelo**: la tipología del coche vendido (`car_list` del lead o el coche enlazado; `config/walcu_api.yaml`,
  `tipos`); sin ella, la regla de siempre (nombre de la campaña, location de VO, tipo por defecto). Modelo: el del
  coche; sin él, «Sin modelo».
- **Exclusiones**: las de Walcu por semanas (location excluidas, comerciales excluidos, empresas del grupo con su
  propio marketing y campañas de otras marcas o de posventa).
- **Cifras de Dirección**: con filas, la tarjeta es «Ventas del mes» (mes en curso, VN · VO) y se añade el bloque
  «Ventas del CRM» por mes en curso y anterior, tipo y modelo. Las salidas de nuestro stock quedan debajo como
  comprobación.

`panel_stock_vehiculo` (desde la 0023, `publicado` y `fecha_entrada`): con el mapeo de estados del vehículo, el
stock del CRM de cada día: coches ni vendidos ni de baja, de las location que cuentan, con `dias_exposicion`
desde su fecha de entrada real (`purchase_date` o, sin ella, `created_at`). `stock_id` es el id del coche en
Walcu. `panel_precio_vo` (desde la 0023, `fecha_entrada` y `dias_stock`): si el anuncio de la web se enlaza con el
CRM por el bastidor (los dos seudonimizados con `WALCU_SEUDONIMO_CLAVE`), las reglas del precio provisional usan
los días en stock en vez de los días publicado, y Ventas los muestra como «N en stock».

### panel_prevision_modelo y panel_prevision_semana · previsión de leads, citas y ventas de Meta

Las escribe `analitica/prevision.py` (`make prevision` y cada noche, tras el precálculo), con origen `real`;
recalcular un día sustituye sus filas reales. Parámetros en `config/prevision.yaml`.

- **panel_prevision_modelo**. Grano: día × tipo (VN, VO; `modelo` NULL) y × modelo con campaña. Es la
  posterior de las tres piezas:
  - leads: regresión de log(leads + 1) sobre log(gasto + 1), mes y régimen. Columnas `columnas`, `media` y
    `covarianza` (V; la de β es σ²·V) y `a`, `b` (σ² ~ Gamma inversa).
  - lead a cita: `cita_a`, `cita_b` (Beta); cita a venta: `venta_a`, `venta_b`.
  - `trends`: el índice de Google Trends usado, o NULL si no se usa.
  - Para simular igual en el panel: `primera_semana`, `gasto_semanal_cents` (gasto previsto),
    `regimen_desde`, `simulaciones`, `semilla` e `intervalo`.
  - Datos: `semanas`, `leads_citas`, `citas_ventas`, `pocos_datos` y `motivo`; `ultima_semana` es la del
    aviso «Previsión con datos hasta la semana del …».
  - **Publicación** (regla del proyecto: si no bate a la referencia, no se despliega): `validada` es true si,
    en la validación, el error de los leads con el gasto que hubo es menor que el de la media de las últimas
    semanas. `validacion` lleva la cifra o el motivo. `aviso_gasto` avisa si con el gasto previsto no mejora.
    Sin `validada`, el panel no enseña la cifra: dice por qué.
- **panel_prevision_semana**. Grano: día × tipo × modelo × semana (NULL: el total) × escenario (% sobre el gasto
  previsto, de `escenarios_gasto`). Columnas `gasto_cents`, `leads_p10`/`p50`/`p90`, `citas_…`, `ventas_…`
  (intervalo del 80 %), `modelo_version`, `n` (semanas de entrenamiento) y `pocos_datos`.
- **Cifras de Marketing**: «Previsión 4 semanas», por tipo y por modelo con campaña activa. Muestra el total
  de las semanas (mediana e intervalo) con el escenario de gasto elegido (`?gasto=`), recalculado en el panel
  con la posterior guardada (`analitica/predictiva.py`, misma semilla: el escenario actual da lo mismo que
  `panel_prevision_semana`). Por tipo, «Para conseguir N citas en 4 semanas haría falta un gasto de X € a la
  semana (entre A y B)» (`?citas_VN=`, `?citas_VO=`).
- **Cifras de Dirección**: las ventas de Meta del total de las semanas, escenario actual, por VN y VO
  (`panel_prevision_semana`), diciendo que no es el total del concesionario.

Fuera del contrato (siempre real, en «Estado de las fuentes»): `panel_prevision_validacion`. Es la validación
hacia atrás por tipo, nivel (`tipo` o `modelo`), medida y variante (`gasto_real`: con el gasto que hubo;
`gasto_previsto`: con el que se habría previsto). Lleva `n`, `mae_modelo`, `mae_referencia` (media de las
últimas semanas), `cobertura` del intervalo, `mejora`, periodo y `trends`.

Fuera del contrato: `panel_walcu_sin_cruce` (lo que no casa entre Meta y Walcu, con el motivo; lo escribe
`make referencias-meta`).

Fuera del contrato (solo «Estado de las fuentes», siempre reales): `panel_trafico_otros` (fuente,
medio y agrupación de GA4 de las sesiones que caen en «otros», para completar `config/canales.yaml`)
y `panel_campanas_google_ads` (campañas de Google Ads con su nombre, sin reglas ni criterios).

## Datos de ejemplo

`make ejemplo-cargar` rellena todas las tablas del contrato con filas `origen = 'ejemplo'`
(`panel/ejemplo.py`). `make ejemplo-borrar` las quita.

**Límites:**

- Solo funcionan con `PANEL_PERMITE_EJEMPLO=1` en el `.env`. El Makefile lo comprueba antes de llamar a docker y el programa, otra vez.
- Solo tocan filas de ejemplo de estas tablas. Nunca tocan las tablas de hechos, las `panel_mercado_*` ni las filas reales.

**Cómo son los datos:**

- **Deterministas**: el mismo día da las mismas filas.
- **Coherentes entre vistas**:
  - las alertas citan vehículos de Ventas por número de stock y campañas de Marketing;
  - los leads de Ventas suman lo mismo que los de Marketing (por modelo y tipo);
  - hay campañas de VN, de VO y de ambos, y estas con leads de los dos tipos;
  - el tráfico de la web tiene 56 días por canal (para comparar 28 con 28), con sesiones de VN, de VO y sin tipo;
  - el stock de Dirección es el de Ventas.
- **Modelos**: los de la marca configurada (`config/concesionario.yaml`, `modelos`).
- **Cifras de modelo**: se escriben tal cual, con p10–p90, versión `ejemplo` y su n. No se ajusta nada.
