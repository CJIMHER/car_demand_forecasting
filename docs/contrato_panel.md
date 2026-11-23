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
| Dirección | Tarjeta de stock, tarjeta de antigüedad, stock por días en exposición | `panel_stock_vehiculo` |
| Dirección | Tarjeta de ventas del mes, gráfico de ventas acumuladas | `panel_ventas_mes` |
| Dirección | Tarjeta de alertas abiertas, alertas de hoy | `alertas_evaluacion` y `panel_alertas` (ver más abajo) |
| Ventas y Marketing | Alertas para su rol | `alertas_evaluacion` y `panel_alertas` |
| Dirección | Tarjeta del mercado | `panel_mercado_dia`. Siempre real: fuera del contrato |
| Ventas | Resumen y lista de vehículos | `panel_riesgo_vehiculo` |
| Ventas | Detalle del vehículo | `panel_riesgo_vehiculo` y `panel_curva_grupo` |
| Marketing | Tarjetas de 28 días | `panel_campana_dia` y `panel_leads_dia` |
| Marketing | Campañas: del gasto a la venta, con su salud | `panel_campana_dia`, `panel_leads_dia` y `panel_campana_salud` |
| Marketing | Recomendación por modelo | `panel_recomendacion_modelo` |
| Marketing | Atención frente a sus rivales (Todo y VN) | `panel_atencion_relativa` |
| Marketing | Ocasión: dónde hay stock que mover (solo VO) | `panel_riesgo_vehiculo`, la misma tabla que Ventas |
| Marketing | Cuándo lanzar una campaña general | `panel_semana_campana` |
| Marketing | Qué dieron las campañas pasadas | `panel_campana_resultado` |
| Marketing | Tráfico de la web por canal | `panel_trafico_canal_dia` |

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
- **Fuente**: Google Trends (`gt_relativo`), hito 2.

| Columna | Unidad | Significado |
| --- | --- | --- |
| `mes` | fecha | Mes de la señal |
| `relativo_puntos` | puntos | 100 × (YoY3 del modelo − media del YoY3 de su conjunto de competidores) |
| `n_competidores` | | Competidores con dato (mínimo 2) |

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
