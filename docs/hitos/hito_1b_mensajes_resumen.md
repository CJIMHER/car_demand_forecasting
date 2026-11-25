# Hito 1b · Mensajes de acción sin «la cocina», y Ventas solo con datos reales

Rama `hito-1b-mensajes`, rebasada sobre `origin/main` en cuanto el conector de Walcu entró en main (`da27b1f`).
Sin push, sin conectarse al servidor.

## Qué se ha hecho

1. **Mensajes de acción** (`panel/mensajes.py`, textos en `config/mensajes_marketing.yaml`):
   - cada recomendación de campaña muestra la decisión, un diagnóstico en una frase y las acciones; para un
     modelo sin campaña, lanzar o no lanzar;
   - las 13 situaciones de la tabla del encargo, más cuatro que hacían falta para no dejar huecos (abajo);
   - si se cumplen varias, todas, de más a menos importante (parar, no lanzar, revisar, continuar, subir, lanzar)
     y sin repetir acciones;
   - `{ciudad}` sale de `propio.ciudad` de `config/concesionario.yaml` (campo nuevo de `Concesionario`; sin él,
     la provincia).
2. **Qué deja de ver quien no es técnico** (Marketing, Dirección y Ventas): umbrales y fórmulas, nombres de
   reglas, cohortes, reparto del gasto, cómo se calcula el interés de búsqueda, priors y validación de la
   previsión. Los datos (gasto, leads, citas, ventas, costes) se siguen viendo. El rol técnico lo ve todo: un
   desplegable «Detalle técnico» bajo cada recomendación y las notas de método de siempre.
3. **Telegram**: las alertas de las campañas de Meta llevan dos textos (`texto` y `texto_tecnico` en
   `alertas/reglas.yaml`, que `alertas/motor.py` elige por rol). Marketing recibe «Campaña «…» · revisar:
   <diagnóstico> Qué hacer: <acciones>»; el técnico, ahora también destinatario de esas alertas, las cifras frente
   a los umbrales y la regla. «Vehículo envejecido» (ventas) dice «más que la mayoría de coches parecidos» en vez
   del percentil y el límite.
4. **Google Trends**: en Marketing, «Interés de búsqueda frente a sus rivales» con «Sube», «Estable» o «Baja» y la
   barra de tendencia; los puntos, la cuota y cómo se calcula, solo técnico. Mercado no muestra Google Trends.
5. **Previsión**: el usuario ve la previsión, los escenarios de gasto, el aviso de gasto y «Pocos datos» (sin el
   motivo técnico). La validación (filas «Validación…» y el motivo de no publicar) solo la ve el técnico; los demás
   ven «Sin previsión todavía: faltan datos para que sea fiable». La tabla de validación sigue en «Estado de las
   fuentes», que solo abre el técnico.
6. **Ventas solo con datos reales** (`contrato.VISTAS_SOLO_REAL` = Dirección y Ventas): sin `PANEL_MODO_EJEMPLO`,
   ningún bloque de Ventas lee filas de ejemplo.

## Inventario de los bloques de Ventas

| Bloque | Tabla | Antes | Ahora (sin `PANEL_MODO_EJEMPLO`) |
| --- | --- | --- | --- |
| `resumen` (tarjetas de arriba) | `panel_riesgo_vehiculo` | Ejemplo: «con menos del 50 % de venderse en 90 días», «más de 90 días», «con campaña activa» | Fuentes reales: stock publicado en la web (`panel_mercado_dia`, scrapers), VO con el precio a bajar o revisar (`panel_precio_vo`; no en VN), salidas del mes (`panel_salidas_stock`) y leads y citas de las campañas de Meta en las últimas 4 semanas (`panel_campana_semana`, Walcu por semanas). Sin una, «Pendiente de conectar: <fuente>» |
| `lista` (vehículos de más a menos riesgo) | `panel_riesgo_vehiculo` | Ejemplo, con la probabilidad de venta a 90 días | «Pendiente: modelo de probabilidad de venta (necesita el stock del CRM)», sin ejemplos; remite a «Precio de nuestros VO», que ya ordena por precio frente al mercado y días publicado. Sin el filtro «menos del 50 %» |
| `detalle` (vehículo elegido y curva de su grupo) | `panel_riesgo_vehiculo`, `panel_curva_grupo` | Ejemplo | «Pendiente: modelo de probabilidad de venta (necesita el stock del CRM)» |
| `alertas` | `alertas_evaluacion`, `panel_alertas` | Motor o, sin él, ejemplo | Solo las del motor (reales); sin ellas, «Sin datos todavía» |
| `precio` (Precio de nuestros VO) | `panel_precio_vo` | Real (precálculo de Mercado) | Igual |
| `salidas` (salidas de las últimas 4 semanas) | `panel_salidas_stock` | Real | Igual |
| `stock_crm` (Stock VO en el CRM) | `panel_stock_vehiculo` | Real con la API de Walcu; si no, «Pendiente de conectar: stock interno (API de Walcu)» | Igual (se llena el día que haya credencial y mapeo) |

Con `PANEL_MODO_EJEMPLO=1` (y `make panel-dev`), Ventas sigue enseñando el ejemplo, como antes.

## Decisiones tomadas sin preguntar

1. **Cuatro situaciones más** en `config/mensajes_marketing.yaml`, para que ninguna recomendación se quede sin
   mensaje:
   - `lead_caro`: lead caro sin coste por cita con el que comparar (sin Walcu, citas incompletas o sin media).
     Revisar audiencias y creatividad y vigilar el coste por cita;
   - `en_linea`: ninguna señal («La campaña va en línea, sin señales de alarma. Continuar.»);
   - `sin_comparables`: no lanzar porque no hay coches comparables en el mercado cercano;
   - `lanzar`: hay stock y el precio compite.
2. **«Pocos datos todavía»**: campaña sin juzgar (primer día) o sin ninguna regla activa y alguna de a, b, c o d
   con poco volumen.
3. **Lead a cita y fuera de zona** salen de la misma regla interna; el mensaje distingue cuál de las dos (o las
   dos) con sus cifras guardadas. Si una regla b o a llega sin la señal concreta (filas antiguas), se toma la
   primera de su tabla.
4. **El técnico pasa a recibir las alertas de campañas** (`roles: [marketing, tecnico]`), con el texto detallado,
   para que el chat técnico siga teniendo el detalle. «Vehículo envejecido» sigue yendo solo a ventas (el técnico
   la recibe con detalle solo sobre datos de ejemplo, como todas).
5. **La recomendación por modelo guarda la decisión y todas las reglas activas** en `cifras` (`decision`,
   `activas`), para que el mensaje junte todas las situaciones y no solo las de la regla que decide. El `motivo` con
   cifras sigue igual y solo lo ve el técnico. Filas sin esas cifras (ejemplo antiguo): se muestra el motivo.
6. **Interés de búsqueda**: «Sube» o «Baja» desde 1 punto de cuota (`interes.umbral_puntos` en la configuración);
   entre medias, «Estable».
7. **Ventas, tarjetas**: siempre las cuatro; en VN no hay tarjeta de precio (el precio provisional es de VO). Los
   leads y citas son los de las campañas de Meta (Walcu de origen Facebook), los únicos por semana que tiene el
   panel; las cifras de toda la tienda llegarán con la API de Walcu.
8. **Ventas sin ordenar por riesgo**: no se inventa un orden nuevo; el bloque «Precio de nuestros VO» ya ordena por
   precio frente al mercado y días publicado, y la lista remite a él.
9. Las notas de método se mantienen para el técnico tal cual; para los demás, una frase corta en su lugar
   («Las citas y las ventas de las últimas semanas todavía pueden crecer», «Es una pista, no una orden»…).
10. «Sin regla» en la nota de una campaña sin clasificar pasa a «Sin clasificar».

## Pruebas

`tests/test_mensajes.py` (nuevo):
- cada situación de la tabla: el texto no técnico no lleva números, «%», «regla», «umbral», «cohorte», «percentil»,
  «mínimo» ni la fórmula; el técnico ve «Detalle técnico» con los umbrales;
- varias situaciones ordenadas y sin acciones repetidas; «pocos datos»;
- `{ciudad}` con un concesionario ficticio de Valladolid, y la provincia si falta la ciudad;
- Telegram: el mensaje de marketing sin regla, umbral, cifras de fórmula ni «%»; el del técnico, con ellos;
- Marketing como marketing y como técnico; ninguna página de dirección, ventas ni marketing (con todos los bloques
  llenos) muestra el método;
- Ventas sin `PANEL_MODO_EJEMPLO`: sin ejemplo, la probabilidad «Pendiente», tarjetas reales o «Pendiente de
  conectar»; con el modo ejemplo, el ejemplo sigue;
- el inventario de este resumen cubre todos los bloques de Ventas del contrato.

Ajustadas, por el cambio pedido: los textos de las alertas de Meta y de «vehículo envejecido» (`test_alertas`,
`test_criterios_meta`, `test_campanas`, `test_walcu_meta_hist`), la nota de cohorte y de referencias para marketing
(`test_walcu_meta_hist`), el detalle dentro de «Detalle técnico» (`test_contrato_panel`) y Ventas sin ejemplo fuera
del modo ejemplo (`test_direccion_real`).
