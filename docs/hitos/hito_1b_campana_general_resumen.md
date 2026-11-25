# Hito 1b · Marketing solo con datos reales, sin explicar Google Trends, y cuándo lanzar una campaña general

Rama `hito-1b-campana-general` desde `origin/main` (`092a031`). Sin push, sin conectarse al servidor.

## Qué se ha hecho

1. **Marketing solo con datos reales** (`contrato.VISTAS_SOLO_REAL` = Dirección, Ventas y Marketing): sin
   `PANEL_MODO_EJEMPLO`, ningún bloque de ejemplo ni el aviso amarillo de arriba.
   - **«Dónde invertir esta semana»**: inversión de Meta; leads, citas y ventas de Walcu por semanas de las campañas
     de los últimos 28 días (las semanas que los cubren, como las tarjetas de campaña y las reglas) o, de las que no
     casan, los leads según Meta; coste por lead, por cita y por venta, con «provisional» si alguna semana aún no
     tiene las citas o las ventas completas. Antes leían `panel_leads_dia` (Walcu diario, que no hay) y salían «Sin
     datos todavía».
   - **«Qué dieron las campañas pasadas»**: las campañas terminadas reales (sin gasto el último día de Meta), las 10
     más recientes: fechas, gasto, leads, citas, ventas y costes de toda su vida. Sin «sin campaña» ni «por encima
     de lo esperado».
   - **«Campaña general»** sustituye a «Cuándo lanzar una campaña general» (punto 3).
2. **Google Trends no se explica** a quien no es técnico. El bloque es «Interés frente a sus rivales»: cada modelo con
   su barra y «Sube», «Estable» o «Baja», nada más. Los puntos, la cuota, la fecha de descarga y las notas de método,
   en «Detalle técnico». Las razones de otros bloques dicen «el interés frente a sus rivales sube». En Marketing, la
   nota del tráfico dice «Visitas a la web» (el técnico ve también «Google Analytics»).
3. **Cuándo lanzar una campaña general** (`analitica/campana_general.py`, `config/campana_general.yaml`), por VN y
   VO, al final de `make precalcular`, en `panel_campana_general`:
   - caída de ventas (8 semanas frente a las 8 anteriores, más de un 20 %, diferencia mínima configurable);
   - subida del stock (más de un 25 % frente a hace 30 días);
   - temporada (VN marzo-abril, junio-julio y octubre-noviembre de liquidación; VO marzo-abril y junio-julio).
   Cada una basta sola; si se cumplen varias, se dicen todas. Bloque «Campaña general» en Marketing y en Dirección:
   «Conviene lanzar una campaña general» con los motivos o «Ahora no hace falta» con la próxima temporada; lo
   pendiente debajo; las cifras, en «Detalle técnico». Telegram: alerta `campana_general` a marketing y dirección,
   que se envía al abrirse (paso a «Conviene lanzar») y no cada día.
4. **Ventas por fecha de venta** (`ingesta/walcu_ventas.py`, migración 0024): `walcu_ventas_semana` (semana de la
   venta, tipo, modelo, ventas), con `make walcu-ventas-cargar FICHERO=…`, `scripts/walcu_agregar.py --negociaciones`
   (`make walcu-ventas-agregar`) y, con la API de Walcu y su mapeo, generadas desde las ventas del CRM (mandan sobre
   el CSV). El histórico de `~/cdf_walcu/walcu_ventas_semana_historico.csv` valida: 228 filas, 269 ventas (166 VN,
   103 VO), de la semana del 24-02-2025 a la del 16-11-2026. No se ha copiado al repositorio.

## Inventario de los bloques de Marketing

| Bloque | Tabla | Antes | Ahora (sin `PANEL_MODO_EJEMPLO`) |
| --- | --- | --- | --- |
| `prevision` (Previsión 4 semanas) | `panel_prevision_modelo` | Real o ejemplo | Real; sin ella, «Sin datos todavía» |
| `kpis` (Dónde invertir esta semana) | `panel_campana_dia`, `panel_campana_semana` | Inversión real; leads y costes de `panel_leads_dia`: «Sin datos todavía» | Inversión, leads, coste por lead, por cita y por venta de Meta y Walcu por semanas, con «provisional» |
| `recomendacion` (Recomendación por modelo) | `panel_recomendacion_modelo` | Real (criterios) o ejemplo | Real; sin ella, «Sin datos todavía» |
| `campanas` (Campañas: del gasto a la venta) | `panel_campana_dia`, `panel_campana_semana`, `panel_campana_salud`, `panel_campana_decision`, `panel_campana_otra_marca` | Real | Igual |
| `atencion` (Interés frente a sus rivales; Todo y VN) | `panel_atencion_relativa` | Real (Google Trends) o ejemplo, con la explicación del cálculo | Real; solo Sube, Estable o Baja y la barra; el método, en «Detalle técnico» |
| `ocasion` (solo VO) | `panel_riesgo_vehiculo` | Ejemplo | «Pendiente de conectar: stock interno (API de Walcu)» |
| `campana_general` (Campaña general) | `panel_campana_general` | «Cuándo lanzar una campaña general», de ejemplo (`panel_semana_campana`) | Real, del precálculo: reglas de ventas, stock y temporada |
| `pasadas` (Qué dieron las campañas pasadas) | `panel_campana_dia`, `panel_campana_semana` | Ejemplo (`panel_campana_resultado`) con «sin campaña» y veredictos | Campañas terminadas reales, sin contrafactual |
| `trafico` (Tráfico de la web por canal) | `panel_trafico_canal_dia` | Real (GA4) | Igual |
| `mes` (Campañas de las últimas 4 semanas) | `panel_campana_semana`, `panel_campana_decision` | Real | Igual |
| `referencias` (Referencias históricas) | `panel_referencia_meta` | Real | Igual |
| `alertas` | `alertas_evaluacion`, `panel_alertas` | Motor o ejemplo | Solo las del motor |

## Aviso: la caída de ventas salta a menudo por azar

Con el histórico de ventas por fecha de venta (76 semanas evaluables por tipo, del 9-6-2025 al 16-11-2026; media de
16,1 ventas de VN y 9,4 de VO cada 8 semanas), semanas en que la regla 1 habría saltado:

| Diferencia mínima | VN | VO |
| --- | --- | --- |
| 0 coches (la regla tal cual) | 28 de 76 semanas (37 %) | 27 de 76 (36 %) |
| 3 coches | 28 de 76 (37 %) | 27 de 76 (36 %) |
| 5 coches | 22 de 76 (29 %) | 14 de 76 (18 %) |

Si las ventas fueran constantes (Poisson con esas medias), saltaría solo por azar en el 26 % (VN) y el 31 % (VO) de
las semanas con diferencia mínima 0; con 3 coches, 26 % y 28 %; con 5, 21 % y 15 %. Con estos volúmenes, una caída
de más del 20 % ya implica casi siempre 3 coches o más, así que 3 no filtra nada; 5 empieza a quitar ruido, sobre
todo en VO. La configuración queda en 0 (la regla tal cual), como pide el encargo: la decisión es vuestra
(`ventas.diferencia_minima` en `config/campana_general.yaml`).

(Cálculo: cada lunes del histórico con 16 semanas completas detrás, la regla de `analitica/campana_general.py` sobre
el CSV. Las semanas evaluables son 76 porque el CSV cubre 91 semanas y hacen falta 15 previas.)

## Decisiones tomadas sin preguntar

1. **Tarjetas de arriba**: leads de las semanas que tocan los últimos 28 días (semanas enteras, como las reglas),
   con el gasto de esas semanas para los costes; la inversión, la de Meta de los 28 días. Si unas campañas casan con
   Walcu y otras no, las que no suman sus leads según Meta aparte («más N según Meta») y no entran en los costes.
   Se añade la tarjeta «Coste por cita».
2. **Campañas pasadas**: terminadas = sin gasto el último día de datos de Meta; las 10 más recientes.
3. **Ventas por fecha de venta**: cada carga dice qué semanas cubre; una semana cubierta sin filas es una semana sin
   ventas (así no hace falta escribir ceros). Para el CSV, de la primera a la última semana del fichero (o hasta
   `HASTA=`). La API manda sobre el CSV en las semanas que cubre.
4. **Ventanas de la regla 1**: terminan en la última semana completa con datos, que puede ir hasta 4 semanas por
   detrás de la última semana completa (`retraso_max_semanas`: el CSV se carga a mano); con datos más viejos,
   pendiente.
5. **Stock de la regla 2**: el del CRM si tiene datos de hace 30 días o más; si no, nuestros anuncios en la web en
   toda España (`panel_mercado_dia`, la misma cifra que «Stock publicado» de Dirección). Hoy = último día con dato;
   hace 30 días = último día con dato hasta entonces.
6. **Exportación de negociaciones**: sede, equipo y vendedor son columnas opcionales (si no vienen, no se excluye
   por ellas y se avisa). El modelo se reduce al nombre del catálogo (`modelos` de `config/concesionario.yaml`) si
   empieza por él; si no, tal cual sin la marca; vacío, «(sin modelo)», como en el histórico. Un tipo de negociación
   que no esté en la configuración no cuenta y se avisa.
7. **Telegram**: la alerta queda abierta mientras la tarjeta diga «Conviene lanzar»; el motor de alertas ya envía
   cada alerta solo al abrirse, así que el aviso sale en el cambio de estado sin lógica nueva. Si cambian los motivos
   sin cerrarse, no se repite.
8. **Prueba de «Google»**: el bloque de tráfico de la web queda fuera de la comprobación, porque sus canales
   («Google Ads», «Búsqueda orgánica») son nombres de canal de `config/canales.yaml`, no Google Trends.
9. **Ejemplo**: dos campañas terminadas nuevas, con su propio generador aleatorio para no cambiar las cifras de las
   demás; filas de ejemplo de `panel_campana_general` (VN conviene, VO no).
10. `panel_semana_campana` y `panel_campana_resultado` siguen en el contrato (el ejemplo las llena y la alerta
    `ventana_favorable` lee la primera), pero el panel ya no las lee.

## Pruebas

`tests/test_campana_general.py` (nuevo): cada regla sola (ventas, stock, temporada), el borde del 20 % y la
diferencia mínima, varias a la vez, VO sin octubre-noviembre, la próxima temporada, la regla del stock pendiente sin
30 días y la de ventas sin 16 semanas o con datos viejos; validación del CSV de ventas y carga por fecha de venta con
semanas sin ventas y la API mandando; las ventas por fecha desde la API de Walcu; la exportación de negociaciones;
el aviso de Telegram solo en el cambio de estado; Marketing sin ejemplo y con las tarjetas de Walcu; ninguna mención
a Google, Trends, búsqueda ni cuota para dirección, marketing y ventas en Dirección, Marketing, Ventas y Mercado; el
bloque de interés con solo Sube, Estable o Baja; la campaña general en Marketing y Dirección con el detalle solo para
el técnico.

Ajustadas por el cambio: `test_contrato_panel` (tarjetas, campañas pasadas, campaña general, bloques reales del
precálculo), `test_direccion_real` (Marketing también solo real), `test_alertas` (regla nueva) y `test_gtrends` (los
puntos, en el detalle técnico).
