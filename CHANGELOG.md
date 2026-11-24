# Cambios

## 2026-11-24 · Marketing: las tarjetas de campaña leen Walcu por semanas (rama hito-1b-walcu-tipo)

- **Arreglo**: en «Campañas: decisión y venta», Leads, Citas, Ventas y los costes salían «—» aunque la regla de
  la misma campaña tenía los datos: las tarjetas leían `panel_leads_dia` (Walcu diario, sin filas reales) y las
  reglas, `panel_campana_semana`. Ahora las dos leen las mismas semanas.
- **Qué muestran**, sobre toda la vida de la campaña: gasto; leads de Walcu (sin carga de Walcu que case, los
  de Meta con «según Meta»); citas y ventas de Walcu hasta hoy. Costes con el gasto de las semanas con carga
  de Walcu, como las reglas, siempre que haya al menos un lead, una cita o una venta. Si alguna semana con
  leads aún no tiene las citas (14 días) o las ventas (60 días) completas, la cifra y su coste salen en gris
  con «provisional». Las reglas no cambian: solo semanas completas.
- Regla a (y d y f): «Revisar:» delante de lo que hay que revisar, como en los avisos de Telegram.
- El bloque de campañas del contrato pasa de `panel_leads_dia` a `panel_campana_semana`.

## 2026-11-24 · Walcu: tipo VN/VO, exclusiones y equivalencias (rama hito-1b-walcu-tipo)

- **Tipo VN/VO** (`scripts/walcu_agregar.py`): Walcu marca «Nuevo» casi todos los leads de campañas de ocasión
  (2.443 de 2.484 en Facebook). Ahora, por este orden: el nombre de la campaña (`[VN]`/`[VO]`, VN o VO al
  principio, «ocasión», «boutique» o «seminuevo» → VO); si no lo dice, sede o equipo de VO (`sedes_vo: [Carpa
  VO]`, `equipos_vo: [VO]` en `config/walcu_export.yaml`); el campo «Tipo de vehículo solicitado»; VN. Las
  columnas Sede y Equipo se leen solo para esto y para las exclusiones; no se escriben. El registro cuenta
  cuántos leads salen de cada paso.
- **Exclusiones** (`config/concesionario.yaml`): PV (posventa) en `propio.palabras_excluidas`; nueva
  `propio.empresas_excluidas: [Covey]`: leads con ese texto en sede, equipo u origen fuera al agregar (se
  cuentan y se avisa), y campañas de Meta o Walcu que lo llevan en el nombre fuera de `make referencias-meta`.
- **Cruce Walcu → Meta** (`config/campanas_walcu.yaml`): sufijos por regla « Campaña», « - leadgen» y
  « - landing» (además de -copy, copia y -OK) y sin espacios entre corchetes; seis equivalencias a mano (año
  distinto, «-VW», « B», « 2», «Test - », «Sin título…»). «Lanzamiento» y «Lanzamiento Julio» (generales de
  VN de Walcu) en `sin_equivalencia`: salen en el informe como campañas sin Meta, sin pedir equivalencia.
- El histórico corregido (`~/cdf_walcu/walcu_semanal_historico.csv`, 1.280 filas, 100 semanas) valida sin
  avisos.

## 2026-11-24 · Precio provisional de nuestros VO en Ventas y Dirección (rama hito-1b-precio-provisional)

- **`analitica/precio.py`** (en `make precalcular`): para cada coche VO nuestro, comparables del mismo modelo,
  acabado, motor, tramo de antigüedad y de km en la zona cercana, sin nuestros coches; p25, mediana y p75 como
  en Mercado. Con menos de 5 se amplía (sin km, sin antigüedad, zona cercana + comunidad) y se dice; con menos
  de 3 en el nivel más amplio, «sin comparables suficientes». Cinco reglas en orden (muy por encima, por
  encima y parado, en precio pero no sale, barato, en línea). Días publicado desde el primer día visto, con
  «al menos N días» si no lo vimos aparecer.
- **Umbrales** en `config/concesionario.yaml` (`precio_provisional`): 5 y 3 comparables, 30, 60 y 90 días.
- **Migración `0020`**: `panel_precio_vo` (contrato del panel, `dia`, `origen`, `calculado_at`).
- **Panel**: bloque «Precio de nuestros VO» en Ventas y Dirección (Marketing no lo ve), ordenado por regla,
  con filtro por modelo (`?modelo_vo=`), el nivel de comparación y el aviso «Recomendación provisional
  basada en el mercado actual; no es una probabilidad de venta». Datos de ejemplo con cada regla y nivel.
- La prueba de imports del Dockerfile del panel sigue en verde: el panel no importa nada nuevo de `ingesta`.

## 2026-11-24 · Imagen del panel: prueba de los imports de ingesta (rama hito-1b-walcu-semanal)

- Rama rebasada sobre `origin/main` con `4fe3eab` (COPY de `campanas`, `canales` y `referencias_meta` en
  `panel/Dockerfile`) y `a3c100e` (campañas sin regla sin duplicados; la descarga no se deshace).
- **Prueba nueva** (`tests/test_infra_panel.py`): recorre con `ast` los imports del panel, también los que
  van dentro de funciones y los indirectos, y falla si un módulo de `ingesta` no está en la línea
  `COPY ingesta/… /app/ingesta/`. Fuera queda `panel/dev.py`, que solo corre en el Mac.
- **`panel/Dockerfile`**: añade `ingesta/db.py`, que faltaba (lo importan dentro de funciones
  `ingesta/campanas.py`, `ingesta/referencias_meta.py` y `panel/ejemplo.py`).

## 2026-11-24 · Walcu por semanas, con cohortes de citas y ventas (rama hito-1b-walcu-semanal)

- **Migración `0019`**: `walcu_campana_semana` (solo añadir; clave semana, origen, campaña y tipo; `semana`,
  el lunes de la semana ISO del lead) sustituye a `walcu_campana_mes`, que se borra (estaba vacía); la vista
  `_vigente` da la última carga de cada semana. `panel_campana_semana` sustituye a `panel_campana_mes`, con
  `citas_completas` y `ventas_completas`. `panel_referencia_meta` pasa a semanas y añade las sumas de las
  semanas con citas completas y con ventas completas. `panel_walcu_sin_cruce`, con primera y última semana.
  El `downgrade` recrea las mensuales, vacías.
- **`make walcu-cargar`**: columna `semana` en AAAA-MM-DD; si no es lunes, se rechaza el fichero entero.
  Recargar una semana la sustituye. Avisos nuevos (no rechazan): ventas ≤ citas ≤ leads y zonas que no suman
  los leads. Bucket: `walcu/semanal/<día>/`.
- **`scripts/walcu_agregar.py`**: agrega por semana del lead (lunes ISO); lo demás, igual.
- **Cohorte** (`config/campanas_walcu.yaml`, `cohorte`): citas y ventas van a la semana del lead. Lead a cita y
  coste por cita solo con las semanas cuyo domingo tenía al menos 14 días al cargarse; cita a venta y coste
  por venta, 60 días. En las referencias, en las reglas (a, c, d, subir; versión `reglas-2026-11-24b`) y en
  Marketing. Sin ninguna semana completa: «citas aún incompletas» (la c propone revisar) o «ventas aún
  incompletas» (la d no se aplica).
- **Marketing**: «Campañas del último mes» pasa a «Campañas de las últimas 4 semanas» (completas), con una
  tabla por semana del lead que marca las incompletas y las que no tienen carga de Walcu. «Referencias
  históricas»: por año ISO, sumando semanas.
- El histórico semanal (`~/cdf_walcu/walcu_semanal_historico.csv`, 1.373 filas, 100 semanas) valida sin
  avisos. `walcu_agregado_historico.csv` (mensual) ya no se usa: se rechaza por la columna `mes`.

## 2026-11-24 · Arreglo: campañas sin regla repetidas rompían Meta y GA4 (rama hito-1b-arreglo-sin-regla)

- **`panel_campanas_sin_regla`**: `make ga4` y `make meta-historico` fallaban al final con `UniqueViolation`
  en `(fuente, campana)` cuando GA4 traía la misma campaña en varias filas (con y sin ID, otro origen o
  medio). Ahora se junta una fila por `(fuente, campana)` antes de insertar (primer y último día de todas,
  gasto y sesiones sumados, el motivo de la primera) y el `INSERT` lleva `ON CONFLICT … DO UPDATE` con la
  misma suma, para que no pueda volver a romper.
- **La descarga no se deshace por la clasificación** (`campanas.actualizar_sin_romper`): Meta (diaria e
  histórico) y GA4 confirman sus filas antes; si rehacer las campañas falla, la descarga se anota `ok` con
  `aviso_campanas` en el detalle y un aviso en el log, y el proceso sale con 0.
- Pruebas: dos filas de GA4 con el identificador `52581469922844` (reproduce el error de producción), la
  función de juntar, y un fallo forzado de la clasificación tras descargar Meta y GA4.

## 2026-11-24 · Histórico de Meta, Walcu agregado y referencias de coste (rama hito-1b-walcu-meta-hist)

- **`make meta-historico DESDE=2025-01-01 [HASTA=…]`** (`ingesta/meta.py --historico`): insights por campaña
  y día, por tramos de un mes, con las campañas en cualquier estado (también archivadas y borradas). Espera
  según `x-business-use-case-usage` (75 % → 60 s, 90 % → 300 s, o lo que diga Meta). Brutos al bucket por
  tramo antes de guardar; misma tabla y huella que la carga diaria: relanzar no duplica. Nombre y estado de
  cada campaña en `campanas_meta_estado`.
- **Migración `0017`**: `walcu_campana_mes` (solo añadir, con `fecha_carga`, `run_id` y `fichero_origen`; la
  vista `_vigente` da la última carga de cada mes), `campanas_meta_estado`, `panel_campana_mes` y
  `panel_referencia_meta` (contrato del panel) y `panel_walcu_sin_cruce`.
- **`make walcu-cargar FICHERO=…`** (`ingesta/walcu.py`): lista cerrada de columnas; rechaza el fichero entero
  si sobra una columna o un valor parece un correo, un teléfono, un DNI o un NIE (sin citar el valor).
- **`scripts/walcu_agregar.py`** (Mac, `make walcu-agregar`): de la exportación de leads de Walcu (xlsx o
  csv) al CSV agregado, leyendo solo las columnas necesarias (`config/walcu_export.yaml`), con la zona
  cercana de `config/concesionario.yaml` (nuevo `propio.web`) y sin escribir nada personal.
- **Cruce Meta–Walcu** (`ingesta/referencias_meta.py`, `config/campanas_walcu.yaml`): por nombre normalizado
  (sin tildes, espacios simplificados, sin `-copy`, `copia`, `-OK`), con equivalencias editables; solo
  origen Facebook (AiVORA fuera). **`make referencias-meta`**: tabla 2025 frente a 2026 por tipo, con la
  diferencia, y el informe de lo que no casa.
- **Reglas** (`alertas/reglas.yaml`, versión `reglas-2026-11-24`): a, fuera de zona 35 % (antes 30 %); d, el
  texto dice que el 33 % es el óptimo y que saltará a menudo; a, c, d y subir usan las citas y ventas de
  Walcu del mes o dicen «sin datos de Walcu» y no se aplican (ya no quedan inactivas); c, «coste por cita en
  la media» = como mucho la referencia de 2025 del mismo tipo (también para la d). Actualizado
  `docs/criterios_marketing_meta.md`.
- **Comerciales excluidos y citas** (`scripts/walcu_agregar.py`): fichero local opcional
  `~/cdf_walcu/comerciales_excluidos.txt` (un nombre por línea, en `.gitignore`, nunca en el repositorio ni en
  el servidor); sus leads se descartan antes de agregar y el registro solo dice cuántos. Un lead con
  negociación no cuenta como cita si su «Resultado última actividad» es PRESUPUESTO POR TELÉFONO
  (`actividades_no_cita` en `config/walcu_export.yaml`).
- **Valores reales de Walcu** (`config/walcu_export.yaml`): venta = Estado que contiene «Vendido»; número
  inexistente = «Número inexistente»; tipo «Nuevo» → VN, «VO» y «Km0» → VO, vacío → VN.
- **Otras marcas del grupo** (`config/concesionario.yaml`): `propio.prefijos_excluidos: [AU, SE, CU]` (primera
  palabra seguida de su etiqueta entre corchetes) y `propio.palabras_excluidas: [SK, SKODA, SEAT, AUDI, CUPRA]`
  (palabra entera en cualquier posición). No cuentan en `walcu_agregar.py`, `make referencias-meta` ni
  Marketing; se cuentan y se avisa, sin rechazar el fichero.
- **Migración `0018`**: `panel_campana_otra_marca`. Las campañas de Meta de otras marcas salen de
  `panel_campana_dia` (tarjetas, bloque de campañas y criterios) y de «Campañas sin regla»; debajo del bloque
  de campañas, «Campañas de otras marcas en esta cuenta: N, gasto X € en 28 días».
- **Marketing**: bloques «Campañas del último mes» (gasto, leads de Walcu y según Meta, citas, ventas, coste
  por lead, por cita y por venta, y la decisión) y «Referencias históricas».
- **Pruebas**: `tests/test_walcu_meta_hist.py` (validador con correo, columna de más, teléfonos, DNI y NIE;
  normalización; cruce y referencias; reglas con y sin Walcu; histórico sin duplicar y con espera por el
  límite; script con xlsx y csv; panel).

## 2026-11-23 · Google Trends: descarga manual guiada y atención relativa real (rama hito-1b-gtrends)

- **Sin acceso automático a Google**: ni pytrends, ni navegadores automáticos, ni peticiones a trends.google.com.
  Trends se descarga a mano una vez al mes desde el perfil de Chrome del proyecto; el resto, automático
  (`ingesta/gtrends.py`, `config/gtrends.yaml`).
- **`make gtrends-enlaces`** (Mac): `salidas/gtrends_enlaces.html` con una comparación por fila (identificador,
  términos, enlace, casilla de descargada y cuántas faltan). Comparaciones de `data/competidores.csv`: el modelo
  y hasta 4 competidores; con más, varias con el modelo como ancla. URL con España, 12 meses y la categoría
  «Autos y vehículos» (cat=47), del YAML.
- **`make gtrends-subir CARPETA=…`** (Mac): identifica cada CSV por los términos de su cabecera; avisa de las que
  faltan, las repetidas y los ficheros que no casan; rechaza otra categoría (o sin categoría), otra zona, otra
  frecuencia o un número de semanas distinto de unas 52, diciendo qué comparación volver a descargar. Si todo
  cuadra, sube los originales y un manifiesto (fecha, comparaciones y huellas) con scp a
  `/opt/cdf/entrada/gtrends/<fecha>/`. No borra nada local.
- **`make gtrends`** (servidor, y cron diario a las 07:00): carga la última entrega en `gtrends_entrega` y
  `gtrends_semana` (migración `0016`, solo añadir; repetir no duplica), con los originales comprimidos al bucket
  (`gtrends/raw/<fecha>/`). «<1» = 0,5 con `menor_que_1`. Carpeta montada en `cdf_jobs` (solo lectura).
- **Marketing → «Atención frente a sus rivales»** real: cuota de búsquedas del modelo en su comparación, últimas
  4 semanas completas frente a las 4 anteriores, en puntos (media de cuotas con varias comparaciones). Mantiene
  la nota de señal direccional y añade la fecha de la descarga.
- **Aviso** `gtrends_pendiente` al técnico: «Toca descargar Google Trends» si la última descarga tiene más de 35
  días (o no hay ninguna). Un error de carga sale en «Fuente caída».
- **Pruebas** (`tests/test_gtrends.py`): CSV en el formato real de Trends (categoría, «Semana», «término:
  (España)», «<1»); faltan, repetidas y desconocidos; categoría, zona, frecuencia y semanas; subida simulada;
  carga sin duplicar; cuota y cambio en puntos; Marketing; aviso de los 35 días.
- **Despliegue**: `make deploy`. En el Mac, `CDF_SERVIDOR_IP` en el `.env` (`.env.example`). Pasos de cada mes en
  `infra/deploy.md` (7.11).

## 2026-11-23 · Tráfico de GA4 por canal; solo Meta en «Campañas sin regla» (rama hito-1b-ga4-canales)

- **`config/canales.yaml`** (nuevo) y **`ingesta/canales.py`**:
  - valores reservados de GA4 ((direct), (organic), (referral), (not set), (none), (cross-network),
    (data not available)…; también cualquier valor entre paréntesis y el vacío): no son campañas;
  - canales por fuente y medio, en orden: Meta (facebook, fb, instagram, ig, meta, an… en la fuente o el
    medio), Google Ads (google o adwords con cpc, ppc, paid…), portales (coches.net, milanuncios, wallapop,
    autoscout24…), correo, búsqueda orgánica, directo y referencia. Si ninguna casa, la agrupación de canales
    por defecto de GA4 como respaldo y, si tampoco, «otros»;
  - tipo VN o VO por las palabras de la página de destino (ocasion, seminuevos… / nuevos, gama…).
- **Solo Meta en «Campañas sin regla»** (`ingesta/campanas.py`): de GA4 solo se intentan clasificar las filas
  del canal Meta o con el nombre o el ID de una campaña descargada de Meta (que cuentan como Meta llegue con
  la fuente que llegue). Google Ads, los valores reservados y el resto del tráfico ya no salen ahí.
- **GA4** (`ingesta/ga4.py`): pide también el ID de campaña (`sessionCampaignId`) y la agrupación de canales
  por defecto (`sessionDefaultChannelGroup`), en `ga4_trafico_dia.campana_id` y `canal_ga4` (no son clave).
- **Migración `0015`**: esas dos columnas (y la vista vigente rehecha); `panel_trafico_canal_dia` (contrato:
  día × canal × tipo, con sesiones, sesiones con interacción y eventos clave); `panel_trafico_otros` y
  `panel_campanas_google_ads` (campañas de Google Ads con su nombre, sin reglas).
- **Marketing → «Tráfico de la web por canal»**: sesiones (frente a los 28 días anteriores), con interacción
  (y %) y eventos clave por canal, últimos 28 días. En VN o VO, solo las sesiones con página de destino de
  ese tipo, y dice cuántas no lo tienen y cuentan solo en «Todo». Con datos de ejemplo mientras no hay GA4.
- **Estado de las fuentes**: sesiones por canal de los últimos 28 días (y cuántas sin tipo de página), la
  lista de fuente/medio que caen en «Otros» para completar el YAML y las campañas de Google Ads.
- **Pruebas**: `tests/test_canales.py` (ejemplos de cada canal, respaldo de GA4, valores reservados, tipo
  por página, configuración no válida, una campaña de Meta que llega por GA4 con su utm y otra por su ID,
  Google Ads sin reglas, Marketing con el selector y Estado de las fuentes); conector de GA4 con las
  dimensiones nuevas.
- **Despliegue**: `make deploy` (migración `0015`) y `make ga4` (recarga los últimos 7 días con las
  dimensiones nuevas y rehace las tablas).

## 2026-11-23 · Ámbito por palabras y reglas b, c y f con los leads de Meta (rama hito-1b-campanas-2)

- **Ámbito sin etiqueta** (`ingesta/campanas.py`, `config/campanas.yaml`): si el nombre no lleva [VN] ni [VO],
  las palabras enteras «ocasión», «ocasion» o «VO» lo hacen de VO, y «nuevos» o «VN», de VN; con ninguna (o con
  las de los dos), ambos. «Boutique Ocasión» y «Liquidacion VO» → general de VO; «Special Days» → general de
  ambos. «Nuevo» (singular) no cuenta. Las palabras de ámbito no entran en el modelo.
- **Leads según Meta**: migración `0014`, `panel_campana_dia.leads_meta` (de `campanas_meta_dia.leads`), que
  escribe `ingesta/campanas.py`.
- **Reglas b, c y f de Meta activas sin Walcu** (`alertas/criterios.py`, `alertas/reglas.yaml`): su fuente es
  solo `panel_campana_dia`. Sin filas en `panel_leads_dia` usan los leads de Meta y cada cifra lo dice («14
  leads según Meta»); la c propone «revisar» con «Falta el coste por cita para decidir entre continuar y parar».
  Con leads de Walcu, mandan los de Walcu. a, d y subir presupuesto siguen inactivas («falta Walcu»). La
  decisión de cada campaña se escribe con el origen más real de las reglas de Meta. Las alertas van a marketing
  (al chat del técnico si no hay chat de marketing, como hasta ahora).
- **Importes** de los criterios con dos decimales si no son enteros: «11,60 €», «4,90 €» (antes «11,6 €»).
- **Pruebas** (`tests/test_campanas.py`): ámbito por palabras; las cifras reales del 16 al 22 de noviembre
  (T-Roc: b 2,64 % y c 17,47 €; Taigo Noviembre 2: b 2,45 % y c 11,60 €; T-Cross: nada), con alertas a
  marketing y a, d y subir inactivas; f con leads de Meta; Walcu manda cuando entra.
- **Despliegue**: `make deploy` (migración `0014`) y `make campanas` para rellenar `leads_meta`.

## 2026-11-23 · Campañas clasificadas por reglas sobre el nombre (rama hito-1b-campanas)

- **Reglas en `config/campanas.yaml`** en lugar de la regla campaña a campaña (`ingesta/campanas.py`). El nombre
  se lee sin mayúsculas, tildes ni espacios de más:
  - ámbito por la etiqueta: [VN] → VN, [VO] → VO, sin etiqueta → ambos (con las dos a la vez, sin regla);
  - general si contiene una palabra de `reglas.generales` (days, friday, liquidación/liquidacion, boutique),
    también pegada a otra («BlackFriday»); conserva su ámbito («VW [VO][2026] Black Friday»: general de VO);
  - si no, de modelo: lo que va entre el año y el mes, normalizado con el catálogo de `config/versiones.yaml`
    («TROC», «t roc» → T-Roc). Lo que sigue al mes («Noviembre 2») se ignora;
  - un modelo fuera del catálogo (Caravelle) se asigna tal como está escrito, marcado «sin datos de mercado»;
  - las `asignaciones` a mano (por nombre, normalizado) mandan sobre las reglas.
- **`panel_campana_dia` real**: hasta ahora no la escribía nadie. Tras cada descarga de Meta o GA4 (y con el
  nuevo `make campanas`) se rehace desde `campanas_meta_dia` con las campañas clasificadas; el nombre vigente
  clasifica todos los días. Marketing y los criterios de Meta leen de aquí el tipo y el ámbito.
- **Migración `0013`**: el ámbito «general» pasa a «ambos» en `panel_campana_dia`, `panel_campana_resultado` y
  `panel_campana_decision` («general» queda solo para el tipo); `panel_campana_dia.sin_mercado`;
  `panel_campanas_sin_regla.motivo`.
- **Criterios de Meta**: en una campaña sin datos de mercado, la regla e queda «no se puede aplicar» (estado
  `no_aplica`, sin lanzar ni no lanzar), la regla d lo dice en lugar de citar similares, y «subir presupuesto» no
  se propone (la e no se puede comprobar). Documentado en `docs/criterios_marketing_meta.md`.
- **Marketing**: cada campaña dice su ámbito y su modelo o «General» («Ocasión (VO) · Caravelle · sin datos de
  mercado»), y lista la regla e no aplicable. En VN o VO, una general de ese ámbito cuenta con su coste; solo las
  de ambos van sin coste («sin las campañas de VN y VO»).
- **Estado de las fuentes** → «Campañas sin regla»: columna Motivo.
- **Pruebas**: `tests/test_campanas.py` (los cinco nombres reales, «Boutique Ocasión», «VW [VO][2026] Black
  Friday», «VW [VN][2026] Special Days diciembre», «Liquidacion VO» y uno que no casa; mayúsculas y espacios;
  catálogo; asignaciones; configuración no válida; tablas del panel; GA4 unida a Meta; criterios sin datos de
  mercado; Marketing y Estado de las fuentes).
- **Despliegue**: `make deploy` (migración `0013`) y `make campanas`.

## 2026-11-23 · Color con acabado y motor en el detalle de un modelo (rama hito-1b-color-2)

- **Filtro de color de la página** (VN y VO), en lugar del filtro de color de la matriz: con una familia
  elegida, el total, «Por acabado y motor» y la matriz de VO cuentan solo los anuncios de esa familia. Se combina
  con acabado, combustible y motor (con un color elegido, la matriz ofrece solo los que existen con él), viaja en
  la URL (`&color=`) y se conserva al cambiar de zona o tipo si allí existe. Las filas de «Por color» enlazan al
  filtro.
- **Tercer nivel** en «Por acabado y motor»: acabado → motor → familia de color, con anuncios, nuestros, precio
  mediano p25–p75 y días, con las mismas reglas de n (sin filtro de color; con él, la tabla ya es de una familia).
- **Cobertura**: la nota «Color conocido en el X %…; mientras se completa…» en «Por acabado y motor» y en la
  matriz con color mientras su segmento esté por debajo del 90 % (en «Por color», siempre la cifra). Con filtro,
  arriba: «Filtrando por color: solo cuentan los anuncios con color conocido (X % del modelo)».
- **Precálculo** (migración `0012`): niveles `acabado_color` y `motor_color` (modelo + acabado + motor + color),
  en VN y VO; la matriz ya tenía sus niveles con color. Sin cruce completo. Medido con los datos sintéticos de
  `make panel-dev` (8 últimos días): +8 % de filas de segmentos y +0,2–0,3 s con la cobertura actual de color;
  con el 100 % de los anuncios con color, 219.558 filas y 5,7 s frente a 164.671 filas y 4,5 s de `main`
  (+33 % filas, +27 % tiempo).
- **Pruebas**: `tests/test_color.py` (niveles nuevos frente a SQL directo en VN y VO y tres zonas; la tabla con
  y sin filtro de color, por acabado × motor × color, frente a SQL directo; con color, los filtros de la matriz
  no ofrecen valores que no existen y sus casillas cuadran; conservación del color al cambiar de zona y tipo;
  nota de cobertura y línea del filtro en el borde del 90 %). Chrome a 1440 y 390 px.
- **Despliegue**: `make deploy` (migración `0012`) y `make precalcular`.

## 2026-11-23 · Color en Mercado y volumen mínimo de la regla b de Meta (rama hito-1b-color)

- **Color de los anuncios** (`ingesta/colores.py`, migración `0011`). La tarea de las 08:30 (`make cargar`),
  después del stock, lee en solo lectura `/opt/cdf/data/vw_details/exports/colors_latest.jsonl.gz` (el compose
  monta solo `exports`, `:ro`) y añade a `color_anuncio` (solo añadir) fuente, vehicle_id, VIN, color exterior e
  interior tal cual, estado y fetched_at; solo las líneas que cambian algo. No toca los hechos de los scrapers,
  los eventos ni `anuncios_externos`. Sin fichero (`sin_captura`) o ilegible (`error`) se anota en
  `fuentes_estado` (fuente `colores`) y la carga del stock sigue con su mismo resultado.
- **Unión con el stock** en el precálculo: fuente + vehicle_id; vale el último color con estado `ok` o
  `listing_color` sabido ese día (fetched_at en Madrid: sin mirar al futuro); un VIN de la ficha distinto del
  bastidor del anuncio anula el color.
- **Familias** en `config/colores.yaml` (`analitica/colores.py`): blanco, negro, gris, plata, azul, rojo,
  verde, amarillo, naranja, marrón/beige, otros y «sin especificar»; palabras clave sin mayúsculas ni tildes;
  aparte, el acabado de la pintura (metalizado, perla, mate).
- **Mercado**: bloque «Por color» en el detalle de un modelo (VN y VO) con anuncios, nuestros, precio mediano
  p25–p75 y días, con las mismas reglas de n; filtro de color en la matriz de VO, combinable con acabado,
  combustible y motor (`&color=` en la URL, se conserva al cambiar de zona o tipo si existe). Precálculo: nivel
  `color` (familia) y seis niveles de matriz con color; nada de cruce completo.
- **Cobertura**: cada bloque de color dice qué % de los anuncios del segmento tiene color (`con_color`); por
  debajo del 90 %, «Color conocido en el X % de los anuncios; mientras se completa, predominan los publicados
  hace más tiempo». En «Estado de las fuentes», % con color por fuente y estado de la ficha (y los anulados por
  VIN) y los 20 nombres de color sin familia más frecuentes (`panel_mercado_color_fuente`,
  `panel_mercado_color_sin_familia`).
- **Regla b de Meta, volumen mínimo**: la tasa de clic solo salta con al menos 1.000 impresiones y la de clic a
  lead con al menos 50 clics (`criterios_meta.b.n_minimo` en `alertas/reglas.yaml`); por debajo se muestra la
  cifra con «sin volumen» y no salta. Versión de reglas `reglas-2026-11-23`. `docs/criterios_marketing_meta.md`
  y `alertas/README.md` actualizados.
- **Pruebas**: `tests/test_color.py` (familias con los nombres reales, carga solo añadir sin tocar los hechos,
  sin fichero y fichero ilegible, VIN distinto, sin mirar al futuro, niveles y cobertura frente a SQL directo,
  nota de cobertura en el borde 89 % / 90 %, filtro de color combinable, «Estado de las fuentes», migración);
  bordes 999 / 1.000 impresiones y 49 / 50 clics en `tests/test_criterios_meta.py`; Chrome a 1440 y 390 px
  (`tests/navegador/capturas.mjs`, capturas en `docs/capturas/`).
- **Despliegue**: `make deploy` (migración `0011`, nuevo montaje en cdf_jobs) y después `make precalcular` para
  rellenar el color hacia atrás.

## 2026-11-21 · Regla b de Meta: tasa de clic y tasa de clic a lead (rama hito-1b-regla-b)

- La regla b deja de usar «leads por visualización < 3 %» y salta con cualquiera de tres señales:
  - tasa de clic (clics / impresiones) < 1 %: «el anuncio no atrae», revisar creatividad (reel, foto) y mensaje;
  - tasa de clic a lead (leads / clics) < 3 %: «atrae pero no convence», revisar llamada a la acción,
    oferta y formulario;
  - leads por impresión por debajo de la mitad que las campañas del mismo modelo y mes de años
    anteriores (se mantiene).
- Umbrales y textos en `alertas/reglas.yaml` (`criterios_meta.b`); versión de reglas `reglas-2026-11-21b`.
  La alerta y la decisión dicen qué señal salta, con sus cifras, su umbral y qué revisar.
- `docs/criterios_marketing_meta.md` y `alertas/README.md` actualizados.
- Pruebas en el borde: 0,99 % / 1 % de tasa de clic y 2,99 % / 3 % de clic a lead; las dos señales a la
  vez; sin clics; la señal del histórico.

## 2026-11-21 · Criterios de Cayetano para las campañas de Meta (rama hito-1b-criterios-meta)

- **Criterios** guardados tal cual en `docs/criterios_marketing_meta.md` y llevados a
  `alertas/reglas.yaml` («criterios_meta»: todos los umbrales y volúmenes, iguales para VN y VO).
  `alertas/criterios.py` los aplica a cada campaña desde su segundo día de vida, con fracciones exactas
  (4,99 % salta, 5 % no).
  - Reglas a, b, c, d y f, y subir presupuesto. Lead → cita solo con al menos 20 leads y tasa de
    venta con al menos 6 citas; con menos se muestra la cifra sin saltar.
  - Cada alerta y cada recomendación lleva su regla, sus cifras frente al umbral y su n.
  - Sustituyen a tres reglas de la sesión 3: «coste por lead del doble de su media de 28 días»,
    «campaña en vigilar o revisar» y «campaña sin citas».
- **Decisión de cada campaña** (tabla nueva `panel_campana_decision`): continuar, revisar, parar o
  subir presupuesto. En Marketing es la columna de la tabla de campañas; la salud frente a su
  referencia queda como detalle.
- **Recomendación por modelo**: la de sus campañas o, sin campaña, la regla e (lanzar o no lanzar,
  con prioridad por stock y precio). Es real desde ya, con el mercado.
- **Zona cercana** (provincia propia + `propio.provincias_cercanas` de `config/concesionario.yaml`)
  como zona del precálculo y del selector de Mercado. En los segmentos, nuestra mediana de precio y la
  de la competencia por separado.
- **Contrato**: `panel_leads_dia.leads_cp_zona` y `leads_cp_fuera` (recuento dentro y fuera de la zona
  cercana; el código postal nunca se guarda), preparados para Walcu.
- **Alertas de ejemplo**: no se envían por Telegram salvo con `ALERTAS_ENVIAR_EJEMPLO=1`; se guardan
  y se ven igual.
- **Migración `0010`**.
- **Pruebas**: `tests/test_criterios_meta.py` (casos en el borde de cada umbral, volumen mínimo,
  subir con una regla de revisión activa, regla e con el mismo modelo, acabado y motor en la zona
  cercana, zona cercana frente a SQL directo, migración) y la de `ALERTAS_ENVIAR_EJEMPLO`.

## 2026-11-21 · Hito 1b, sesión 3: alertas por rol, botones de Ventas y conectores de Meta y GA4 (rama hito-1b-sesion-3)

Encargo en `docs/hitos/hito_1b_sesion_3.md`.

- **Motor de alertas** (`alertas/`, cron de `cdf_jobs` a las 09:15, tras el precálculo). Determinista,
  sin modelos de lenguaje.
  - Reglas, umbrales, destinatarios y textos en `alertas/reglas.yaml`; el código solo hace la
    comparación (`alertas/reglas.py`).
  - Con datos reales: fuente caída (técnico), presión de un competidor (stock de un modelo y precio
    mediano de un segmento) y pérdida de peso (ventas y dirección). Las de mercado no saltan sin la
    captura de hoy y la de hace 7 días, ni por debajo del n mínimo de `config/concesionario.yaml`.
  - Preparadas: vehículo envejecido, riesgo de inmovilización, ventas fuera de la banda, caída de
    leads, coste por lead, campaña en vigilar/revisar con su eslabón, campaña sin citas y ventana
    favorable. Se activan cuando sus tablas `panel_*` tienen filas reales. Sobre filas de ejemplo
    calculan solo para el técnico con «[EJEMPLO]» delante (la base lo exige).
  - Tablas `alertas` (nueva/abierta/cerrada; la abierta no se reenvía y se cierra sola) y
    `alertas_evaluacion`. Dirección, Ventas y Marketing leen sus alertas de ahí; el bloque deja de
    ser de ejemplo con la primera evaluación real.
  - `make alertas`; `SIN_TELEGRAM=1 make alertas` calcula sin enviar.
- **Telegram por rol** (`alertas/envio.py`): un mensaje por rol y día con sus alertas nuevas.
  `TELEGRAM_CHAT_DIRECCION`, `_VENTAS` y `_MARKETING` (sin ellos, al técnico con el rol delante).
  Cada envío en `alertas_salida`; un fallo, también en `fuentes_estado`, y la tarea sigue. Lo que no
  salió por un fallo sale en la pasada siguiente.
- **Botones de Ventas** activos: POST con CSRF a `/ventas/accion`; anotación en `acciones` (solo
  añadir); aviso a marketing o al jefe de ventas enviado por `cdf_jobs` cada minuto (el panel no
  tiene el token). Bloqueo de 7 días por vehículo y acción; la ficha dice quién, cuándo y si el aviso
  salió. Con datos de ejemplo, solo al técnico. Nada en Walcu.
- **Conectores** de Meta Marketing API (`ads_read`) y GA4 Data API (Lector), inactivos sin
  credenciales (anotan «sin credenciales», sin alerta y sin ninguna petición). Recargan 7 días cada
  noche (06:00 y 06:10) en tablas de solo añadir (`campanas_meta_dia`, `ga4_trafico_dia`) que solo
  añaden lo que cambia; brutos comprimidos al bucket antes de escribir; 429 con espera; nada
  personal. `config/campanas.yaml` documenta la unión por `utm_campaign` y las reglas de cada
  campaña; las que no tienen regla salen en «Estado de las fuentes».
- **Precálculo**: tabla `panel_mercado_competidor` (anuncios y precio mediano por concesionario,
  modelo y modelo + acabado en la provincia propia).
- **Migración `0009`**. `cryptography` en `requirements.txt` (firma del JWT de GA4).
- **Pruebas**: `tests/test_alertas.py` (34 pruebas), `tests/test_acciones.py` (10) y
  `tests/test_conectores.py` (16); la limpieza del entorno de `tests/conftest.py` cubre `META_*`,
  `GA4_*` y `CDF_CAMPANAS`. 277 en verde.

## 2026-11-21 · make test en el contenedor sin .env.example (rama hito-1b-pruebas-2)

- `tests/test_entorno.py::test_cubre_todo_el_env_de_ejemplo` fallaba en el servidor
  (`FileNotFoundError: '/app/.env.example'`): `.dockerignore` excluye `.env*` de la imagen. Se salta
  con `skipif` y el mismo motivo que las de `tests/test_infra_panel.py` («sin .env.example (lo
  excluye .dockerignore): se prueba en el host»); en el Mac se sigue ejecutando.
- Revisadas todas las lecturas de ficheros del repositorio en `tests/`: ninguna otra cae en un patrón
  de `.dockerignore` (lo que leen está en `infra/`, `config/`, `panel/`, `scrapers/vn/tests/fixtures`
  y `alembic.ini`, que van en la imagen).
- Comprobado en una réplica de la imagen (copia filtrada con `.dockerignore`, sin `make`,
  `PYTHONDONTWRITEBYTECODE=1`, `TZ=Europe/Madrid` y variables del `.env` de producción): 205 pasadas,
  7 saltadas, 0 fallos.

## 2026-11-21 · Pruebas independientes del entorno del host (rama hito-1b-pruebas)

- **Fallo**: `make test` en el servidor daba 2 fallos en `tests/test_contrato_panel.py` (el caso
  «sin variable» de `PANEL_EJEMPLO_VISIBLE_PARA` y la banda de ejemplo de /direccion). `cdf_jobs` recibe
  `/opt/cdf/.env` como `env_file`, con `PANEL_EJEMPLO_VISIBLE_PARA=tecnico,direccion` y
  `PANEL_PERMITE_EJEMPLO=1`, y las pruebas lo heredaban.
- **Arreglo** (`tests/conftest.py`): `entorno_limpio` borra al empezar cada prueba (y una vez al
  empezar la sesión, antes de las fixtures de módulo) toda la configuración que puede traer el host:
  `PANEL_*`, `TELEGRAM_*`, `RCLONE_*`, `POSTGRES_*`, `BUCKET`, `BACKUP_PASSPHRASE`,
  `CDF_DATABASE_URL`, `CDF_SCRAPERS_DATA`, `CDF_CONCESIONARIO`, `CDF_VERSIONES`, `CDF_ENV_FILE` y
  `CDF_PYTHON`. Se quedan `PG*` y `CDF_TEST_PG` (dónde está el Postgres de pruebas). La prueba que
  necesita una variable la fija ella con `monkeypatch`, como ya hacían todas.
- **Otras pruebas que dependían del host de la misma forma**: `test_caducidad_por_defecto_30_dias` y
  `test_cookies_solo_del_host` (con `PANEL_SESION_DIAS` en el .env) y todas las de
  `tests/test_segmentos.py` con catálogo (con `CDF_VERSIONES`); también las de Mercado, scrapers,
  panel y Telegram si el host tuviera `CDF_CONCESIONARIO`.
- **Pruebas**: `tests/test_entorno.py` (3): ninguna prueba ve esas variables; cada variable de
  `.env.example` está cubierta; y las pruebas sensibles pasan en un pytest hijo con un entorno como el
  de producción (falla con el conftest anterior).

## 2026-11-21 · Hito 1b: matriz de VO por combustible y motor (rama hito-1b-motor)

- **Filtros de la matriz «Precio por antigüedad y km»** (detalle de un modelo, VO): además del
  acabado, combustible (Todos, Gasolina, Diésel, Híbrido, Eléctrico: solo los que hay en ese modelo y
  zona) y motor exacto (combustible + CV + tecnología + cambio: solo los que hay con el acabado y el
  combustible elegidos). Se combinan, viajan en la URL (`acabado`, `combustible`, `motor`) y se
  conservan al cambiar de zona o tipo cuando el valor sigue existiendo allí; si no, se cae solo ese.
  Una URL con un valor que no existe redirige (303) a la que vale; con htmx, `HX-Push-Url`.
  Mismas reglas de muestra y la nota que propone España, con los mismos filtros.
- **Precálculo**: cuatro niveles nuevos, todos con la matriz años × km: modelo+combustible,
  modelo+motor, modelo+acabado+combustible y modelo+acabado+motor (no el cruce completo). Inserción con
  COPY. El resumen lleva filas por nivel y segundos (total y de segmentos). Medido en el Mac con los
  mismos datos sintéticos de `make panel-dev` (60 días, escala 4): de 212.467 filas y 12,9–13,2 s a
  455.544 filas y 12,8–13,3 s; con el escenario de las pruebas (12 días): de 16.542 a 34.673 filas,
  0,86 → 0,8 s.
- **Migración `0008`**: columna `combustible` (grupo) en `panel_mercado_segmento`, en la clave única;
  restricciones con nombre; la de combustible, NOT VALID (las filas del nivel motor de antes se
  rehacen con el siguiente `make precalcular`).
- **Catálogo**: `combustibles` y `sin_combustible` en `config/versiones.yaml`. «Sin dato de motor»
  lleva el combustible si lo hay.
- **Pruebas**: 6 nuevas en `tests/test_segmentos.py` (cada combinación de filtros frente a SQL
  directo, opciones que no existen, conservación al cambiar de zona y tipo, descartes, muestra y nota
  de España, migración de ida y vuelta) y los niveles nuevos en el cuadre general. VO sintético con
  un 12 % de híbridos. `tests/navegador/capturas.mjs` pulsa los tres filtros y cambia de tipo y zona;
  capturas `docs/capturas/modelo_vo_filtros_*`.

## 2026-11-21 · Hito 1b: Mercado por segmentos (rama hito-1b-segmentos)

- Encargo guardado en `docs/hitos/hito_1b_segmentos.md`.
- **Detalle de un modelo**: en la tabla por modelo de Mercado, el nombre enlaza a
  `/mercado/modelo/<modelo>?tipo=…&zona=…`, con el mismo selector de zona y tipo.
  - «Por acabado y motor»: cada acabado es una fila desplegable con sus motores dentro. Columnas:
    anuncios, nuestros, precio mediano con su rango p25–p75 y días publicado («≥ N» como en la tabla).
  - Solo en VO, «Precio por antigüedad y km»: matriz 3 × 4 con la mediana y su n en cada casilla y
    filtro de acabado (Todos por defecto). En el móvil pasa a una lista por antigüedad.
  - Reglas de muestra: con n < 5, cifra atenuada con su n; con n < 3, «—» y «pocos anuncios» (nunca
    el precio). Si la zona deja más de la mitad de las casillas en «—», una nota propone ver España.
  - Nota fija: «Precio publicado, no precio de venta. Los segmentos con pocos anuncios no son representativos».
  - Bloque «Por color exterior» preparado: sale solo cuando la web publique el color (hoy, vacío).
- **Lectura de la versión** (`analitica/versiones.py`, catálogo `config/versiones.yaml`): acabado por
  modelo con sinónimos y comillas, tildes y espacios normalizados (VO y VN dan el mismo acabado); lo que
  no está en el catálogo va a «Sin clasificar», nunca se adivina. Motor: tecnología + CV de `power_cv`
  (no del texto) + combustible + cambio cuando el texto lo dice. Con los textos reales de los históricos
  locales: 94,5 % de los anuncios de VO y 99,6 % de VN con acabado reconocido.
- **Segmentos** (`analitica/segmentos.py`, en el precálculo de las 09:10): tabla
  `panel_mercado_segmento` con solo los niveles que se muestran, por zona (provincia, comunidad,
  España): n, propios, precio p25/p50/p75 de los que tienen precio y días publicado. Los tramos de
  antigüedad (años cumplidos de la primera matriculación al día de la captura) y de km, en
  `config/concesionario.yaml` (`segmentos`); umbral `panel.n_minimo_mostrar: 3`.
- **Migración `0007`**: `stock_externo_snapshot` ya tenía versión, combustible, matriculación y km;
  le faltaban los CV. Se añaden `potencia_cv`, `cilindrada_cc` y `color_exterior` como columnas
  generadas desde `datos` (sin UPDATE, sin tocar scrapers, carga ni SQLite). `cdf_solo_anadir` deja de
  compararlas al cambiar `es_propio`. Tablas nuevas `panel_mercado_segmento` y
  `panel_mercado_sin_clasificar`, de solo lectura para `cdf_panel`.
- **Estado de las fuentes**: % de anuncios con acabado reconocido por modelo y tipo, y los 20 textos
  sin clasificar más frecuentes del último día.
- **Datos sintéticos**: versiones con el formato de VO y de VN (y acabados fuera del catálogo),
  matriculación y km; `make panel-dev` usa flotas cuatro veces mayores. En el escenario de pruebas,
  «Tiguan eHybrid» y «Golf eHybrid» pasan a «Tiguan» y «Golf».
- **Pruebas**: `tests/test_segmentos.py` (27) y ampliaciones de `tests/test_panel.py` (detalle en la
  prueba del concesionario ficticio, cabeceras y «sin scraper»). `tests/navegador/capturas.mjs`
  revisa el detalle a 1440 y 390 px (también texto más ancho que su casilla) y el enlace desde la tabla.
  Capturas nuevas: `docs/capturas/modelo_vn_*`, `modelo_vo_*` y `modelo_vo_provincia_*`.
- Docstrings de `analitica/mercado.py` sin el nombre de la marca (prueba nueva: ni la marca, ni el
  concesionario, ni su provincia en `panel/` ni `analitica/`).

## 2026-11-15 · Hito 1b: estáticos con huella y Marketing por tipo (rama hito-1b)

- **Caché de los estáticos** (fallo en producción tras la sesión 2: Dirección, Ventas y Marketing
  salían sin estilos ni gráficos hasta forzar la recarga, porque el CSS y el JS viejos se guardaban un día).
  - CSS, JS, fuentes, icono, htmx y ECharts se citan con la huella SHA-256 de su contenido
    (`/static/css/panel.css?v=…`, `panel/estaticos.py`). El CSS se sirve con sus `url()` a las
    fuentes ya con huella.
  - Con la huella vigente: `public, max-age=31536000, immutable`. Sin huella o con una vieja: `no-cache`.
    HTML, respuestas de htmx y errores: `no-store`.
  - Caddy no añade ni pisa cabeceras de caché; una prueba lo vigila.
  - Pruebas: `tests/test_estaticos.py` y, en Chrome con la caché activada, `tests/navegador/cache.mjs`.
    `capturas.mjs` ya no desactiva la caché.
- **Marketing por tipo**: selector Todo / Nuevos (VN) / Ocasión (VO).
  - Migración `0006`: `ambito` de cada campaña (VN, VO o general) y `tipo` en leads, recomendaciones,
    semanas y campañas pasadas. El gasto nunca se reparte.
  - En VN o VO: campañas de ese ámbito y generales. De las generales, leads, citas y ventas del tipo,
    y «—» en los costes con la nota «gasto común a VN y VO».
  - Tarjetas de VN o VO: solo campañas del ámbito, «sin la campaña general».
  - Recomendación por tipo (en Todo, con su etiqueta VN o VO).
  - En VO, «Ocasión: dónde hay stock que mover» en lugar de la atención frente a los rivales.
  - Semanas y campañas pasadas, del tipo elegido.
  - Datos de ejemplo: dos campañas de ocasión, la general con leads de los dos tipos y dos campañas
    pasadas generales y una de ocasión.
  - Capturas nuevas: `docs/capturas/marketing_vn_*` y `marketing_vo_*`.
- **Chrome**: la comprobación revisa también a 1440 px que ninguna celda sale de su tarjeta (encontró
  la tabla de ocasión cortada en escritorio, ya corregida).

## 2026-11-15 · Hito 1b: ajustes de Ventas, Marketing y tarjetas (rama hito-1b)

- **Ventas**
  - La lista muestra los 25 vehículos de más riesgo, con el recuento («25 de 142») y un botón «Ver
    25 más» (htmx). Los filtros y el resumen se aplican a todos los vehículos.
  - Si se elige un vehículo más abajo, la lista se amplía hasta él.
  - Escritorio: el detalle se queda fijo a la vista mientras se recorre la lista.
  - Móvil: el detalle se abre justo debajo del vehículo pulsado, que se mantiene en pantalla.
  - Curva del grupo redibujada: la banda p10–p90 no se pintaba bien.
  - En el móvil, las etiquetas de sugerencia ya no se cortan.
- **Marketing**: el diagnóstico de salud de una campaña va en una línea a todo el ancho bajo su fila,
  en lenguaje llano («Dónde falla: la web. Leads por clic: …»). En «Salud» queda solo el estado.
- **Tarjetas en el móvil**: la etiqueta de estado pasa debajo del título.
- **Campañas pasadas**: fechas y gasto, siempre en su propia línea bajo el título.
- **`tests/navegador/capturas.mjs`**
  - Pulsa un vehículo de mitad de lista y comprueba que su detalle queda en pantalla, a 1440 y a 390 px.
  - A 390 px compara con el ancho real (antes, con el de la ventana, que crece si algo desborda).
  - Revisa también el contenido de cada celda y no usa la caché del navegador.

## 2026-11-15 · Hito 1b, sesión 2: contrato de datos y vistas Dirección, Ventas y Marketing (rama hito-1b)

- Encargo guardado en `docs/hitos/hito_1b_sesion_2.md`.
- Contrato de datos del panel en `docs/contrato_panel.md`, con la migración `0005`. Son 12 tablas
  `panel_*` (stock, ventas del mes, alertas, riesgo por vehículo, curvas de grupo, leads, gasto por
  campaña, salud de campañas, recomendación, atención relativa, semanas y campañas pasadas).
  - Todas llevan `dia`, `origen` ('real' o 'ejemplo') y `calculado_at`.
  - Ninguna tiene datos personales.
  - Las salidas de modelo llevan p10/p50/p90, `modelo_version` y `n`.
  - `cdf_panel` solo puede leerlas.
- `panel/contrato.py`: un bloque no mezcla orígenes.
  - Si cualquiera de sus tablas tiene una fila real, el bloque solo lee filas reales.
  - Si no, lee las de ejemplo; sin filas, dice «Sin datos todavía».
- `panel/permisos.py`: una vista con algún bloque de ejemplo solo la ven los roles de
  `PANEL_EJEMPLO_VISIBLE_PARA` (por defecto, `tecnico`). Los demás la ven desactivada; sin datos,
  está desactivada para todos.
- Vistas nuevas, con banda «Datos de ejemplo» arriba y la etiqueta en cada bloque de ejemplo:
  - **Dirección**: selector VN, VO o Todo; tarjetas; ventas acumuladas con la banda p10–p90;
    alertas con tema y destinatario; stock por días en exposición; tarjeta del mercado con datos
    reales.
  - **Ventas**: vehículos por riesgo con su intervalo, filtros y detalle con la curva de su grupo.
    Los botones están desactivados.
  - **Marketing**: tarjetas de 28 días, recomendación por modelo, campañas con su salud, atención
    frente a los rivales, cuándo lanzar una campaña general y qué dieron las campañas pasadas.
  - En el móvil, sin desplazamiento horizontal.
- `panel/ejemplo.py` y `make ejemplo-cargar` / `make ejemplo-borrar`: datos de ejemplo deterministas
  y coherentes entre vistas, con los modelos de `config/concesionario.yaml`.
  - Solo funcionan con `PANEL_PERMITE_EJEMPLO=1`.
  - Nunca tocan tablas de hechos, `panel_mercado_*` ni filas reales.
  - `make panel-dev` los carga.
- `config/concesionario.yaml`: `panel.objetivo_antiguedad_dias` (60) y `modelos` de la marca.
- Mercado:
  - el modelo sale sin la marca delante: se quita en el precálculo, y `make precalcular` rehace el
    histórico sin duplicar modelos;
  - fuera de la provincia propia, «Nuestros» muestra solo las unidades.
- `.env.example`: `PANEL_PERMITE_EJEMPLO` y `PANEL_EJEMPLO_VISIBLE_PARA`, sin valor. El compose pasa
  la segunda al panel; la primera, nunca.
- Pruebas: `tests/test_contrato_panel.py`, Makefile `ejemplo-*` y marca en el precálculo.
  `tests/navegador/capturas.mjs` revisa las tres vistas a 390 px y hace sus capturas.

## 2026-11-15 · Hito 1b: «Nuestros» y «Por concesionario» en Mercado (rama hito-1b)

- Tabla por modelo: columna «Nuestros» (anuncios del concesionario propio y, atenuado, su peso sobre
  el modelo si este tiene al menos `panel.n_minimo_porcentaje` anuncios). En el móvil, en la
  primera línea.
- Bloque «Por concesionario», solo en la provincia del concesionario propio: anuncios, variación a
  7 días en unidades (porcentaje con base ≥ umbral), peso sobre la provincia y precio mediano; el
  propio resaltado en ámbar. Nombre tal como lo publica la web (el más reciente). En el móvil, dos
  líneas por concesionario.
- Migración `0004`: `panel_mercado_modelo.propios` y tabla `panel_mercado_concesionario`
  (precálculo en `analitica/mercado.py`; `cdf_panel` solo la lee). Tras desplegar: `make precalcular`.
- Escenario sintético con un concesionario que no publica un día y otro que aparece nuevo.
- `tests/test_infra_panel.py`: las cuatro pruebas del Makefile y de `.env.example` se saltan, con
  su motivo, donde no hay make o no está el fichero (contenedor cdf_jobs); en el Mac se ejecutan.
- `tests/navegador/capturas.mjs` revisa a 390 px las seis combinaciones de zona y tipo y las dos tablas.

## 2026-11-15 · Hito 1b: ajustes de la vista Mercado (rama hito-1b)

- Móvil: en pantallas estrechas, cada modelo de la tabla ocupa dos líneas sin desplazamiento
  horizontal (arriba modelo, anuncios y variación; debajo, atenuado y con etiquetas, altas,
  ausencias, precio y días). En escritorio la tabla no cambia.
- «Frente a hace 7 días»: diferencia en anuncios con signo; el porcentaje, atenuado, solo si hace 7
  días había al menos `panel.n_minimo_porcentaje` anuncios (10, en `config/concesionario.yaml`).
- Sin la palabra «scraper» en la vista Mercado: «captura» en su lugar.
- `tests/navegador/capturas.mjs`: comprobación en Chrome a 390 px y capturas de `docs/capturas/`.

## 2026-11-15 · Hito 1b, sesión 1: base del panel y vista Mercado (rama hito-1b)

- Encargo guardado en `docs/hitos/hito_1b_sesion_1.md` (sustituye a la ficha de `docs/desarrollo.md`).
  Regla nueva en `CLAUDE.md`: replicable, una instancia por concesionario, nada propio de AVISA en
  el código ni en las plantillas.
- `config/concesionario.yaml` + `ingesta/concesionario.py`: nombre del panel, marca, provincia
  (zona por defecto) y umbral de medianas; comunidad autónoma desde la provincia (`provincias.COMUNIDADES`).
- Migración `0003`: tablas derivadas `panel_mercado_dia`, `panel_mercado_modelo` y
  `panel_mercado_capturas`; `panel_usuarios`, `panel_sesiones` y `panel_accesos`; rol `cdf_panel`
  (lee `panel_mercado_*` y `fuentes_estado`, escribe solo sus tablas, sin acceso a las de hechos);
  tipo `precalculo` en `fuentes_estado`.
- `analitica/mercado.py`: precálculo de Mercado (cron 09:10, `make precalcular`), con las reglas
  de capturas completas, altas observadas, ausencias y bajas, ventanas de 7 días y cotas
  inferiores; anota en `fuentes_estado` y avisa por Telegram si falla (`aviso_copia.avisar_texto`,
  generalizado sin cambiar el aviso de la copia).
- `panel/`: FastAPI + Jinja2 + htmx + ECharts (servidos desde el propio panel, con versión fija y
  licencia), plantillas y CSS traducidos del prototipo, escritorio y móvil con barra inferior.
  Acceso con argon2id, contraseña temporal con cambio obligatorio, sesión en Postgres con cookie
  `__Host-`, CSRF, bloqueo de 15 minutos tras 5 fallos por usuario y por IP, registro de accesos de
  90 días, permisos en `panel/permisos.py`. Vistas: Mercado (datos reales), Estado de las fuentes
  (técnico), Dirección/Ventas/Marketing desactivadas; `/salud`, `robots.txt` y cabeceras de seguridad.
- Despliegue: servicios `panel` y `caddy` (perfil `panel`, red `cdf_web`), Caddyfile, imagen del
  panel sin privilegios; `make deploy` solo los arranca con `PANEL_DOMINIO`; `make panel-rol`,
  `panel-comprobar`, `panel-logs`, `precalcular`, `usuario-*`, `usuarios` y `panel-dev` (Mac).
  Variables nuevas en `.env.example`. Guía en `infra/deploy.md` (sección 7).
- Pruebas: reglas estadísticas, acceso, matriz de permisos, CSRF, secretos fuera de logs, permisos
  de `cdf_panel`, cabeceras y recursos propios, cifras frente a SQL directo, concesionario ficticio
  de otra provincia, despliegue sin `PANEL_DOMINIO`. Capturas en `docs/capturas/`.

## 2026-11-15 · Hito 0: aviso de copia fallida, rclone sin aviso y total de carga (rama hito-0)

- `pg_backup.sh`: si la copia falla, aviso al grupo de Telegram (`ingesta/aviso_copia.py`, con
  `telegram.enviar`: texto plano, reintentos, token fuera de logs) con el paso y el código. Se envía
  antes de anotar, para que llegue aunque Postgres no responda; el script sale siempre con el código
  de la copia. Si va bien no se envía nada. Las variables obligatorias se comprueban dentro del trap.
- `RCLONE_CONFIG=/dev/null` en `pg_backup.sh`, `probar_restauracion.sh`, `tarea.sh` y la imagen: sin
  el aviso «Config file ... rclone.conf not found». El remoto sigue en `RCLONE_CONFIG_HZ_*`.
- Resumen diario: «Carga a Postgres» suma las filas de todas las cargas del día en `fuentes_estado`.

## 2026-11-14 · Hito 0: gpg sin HOME escribible (rama hito-0)

- `pg_backup.sh` y `probar_restauracion.sh`: `GNUPGHOME` temporal (`/tmp/cdf_gnupg.XXXXXX`, 700) que
  se borra al salir junto con su gpg-agent; `--batch --no-tty --pinentry-mode loopback
  --no-symkey-cache` y la frase por `--passphrase-fd`. Arregla `gpg: Fatal: can't create directory
  '/app/.gnupg'`, que habría hecho fallar la copia de las 09:30.
- Imagen `cdf_jobs`: el usuario `cdf` tiene `HOME=/home/cdf` (creado, suyo, 700) en vez de `/app`.
- Pruebas: `pg_backup.sh` con HOME de solo lectura y conexión solo por variables PG*; la frase no
  aparece en la salida y no queda nada en HOME ni en `/tmp`.

## 2026-11-14 · Hito 0: make test en el contenedor (rama hito-0)

- `ingesta/db.py`: `url_sqlalchemy()` convierte una cadena libpq «clave=valor» en una URL
  `postgresql+psycopg://` completa (host, puerto, usuario, contraseña escapada, base y parámetros
  extra), completando con las variables PG*. Arregla los 17 errores de `make test` en el servidor
  (Alembic recibía `dbname=cdf_test_...`). La conexión de producción no cambia.
- `tests/test_db.py`: la conversión en los dos formatos (libpq del contenedor y URL del Mac).

## 2026-11-14 · Hito 0: resumen por Telegram y ajustes de despliegue (rama hito-0)

- Resumen diario por Telegram tras la comprobación de las 09:00 (`ingesta/telegram.py`): texto
  plano con una línea por scraper y la carga a Postgres; reintentos ante 429 y fallos de red; el
  token nunca sale en logs ni errores; un fallo de envío se anota en `fuentes_estado` sin hacer
  fallar el job. Migración `0002`: tipo `aviso` en `fuentes_estado`.
- rclone por variables `RCLONE_CONFIG_HZ_*` del `.env` (sin `rclone.conf`): el contenedor recibe el
  `.env` como `env_file` y cron solo la lista de `exportar_entorno.sh`.
- Hora de Madrid comprobada al arrancar `cdf_jobs` (tzdata, `/etc/localtime`, CET/CEST).
- `cdf_jobs` con el UID/GID de `cdf` obligatorio (sin valor por defecto); todo salvo el demonio de
  cron corre como `cdf`, y la carga se niega a abrir las bases de los scrapers como root.
- Postgres sin puertos, comprobado en pruebas. `make hora` y comprobaciones nuevas en
  `make comprobar-entorno`.

## 2026-11-14 · Hito 0, segunda parte (rama hito-0)

- `CLAUDE.md` con las reglas comunes; estructura de módulos con su README (`ingesta`, `analitica`,
  `alertas`, `panel`, `infra`).
- `infra/docker-compose.yml`: `cdf_postgres` (Postgres 16, sin puerto publicado) y `cdf_jobs`
  (Python 3.12 con cron); `cdf_panel` y `cdf_caddy` reservados con el perfil `hito1b`. `.env.example`.
- Migración Alembic `0001`: `scraper_runs`, `stock_externo_snapshot` (con `es_propio`),
  `eventos_externos`, `anuncios_externos` y `fuentes_estado`; disparadores que impiden UPDATE,
  DELETE y TRUNCATE en las tablas de hechos.
- `ingesta/scrapers_vw.py`: carga idempotente a las 08:30 de las bases SQLite de VO, VN y VN otros en
  solo lectura; provincia desde el código postal; comprobación a las 09:00 en `fuentes_estado`.
- `config/concesionario.yaml`: AVISA (ESP00710) como propio; Sevilla Wagen (ESP00713) y Utrewal
  (ESP00714) como competencia.
- Copias: `pg_dump` diario cifrado a `backups/postgres/` del bucket con 30 días de retención y
  `probar_restauracion.sh` en un contenedor aparte.
- `Makefile` e `infra/deploy.md` para desplegar en el servidor.
- Pruebas con bases SQLite sintéticas escritas con el código real de los scrapers.
