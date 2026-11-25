# Hito 3 · Conector de la API de Walcu (listo antes de la credencial): resumen

Rama `hito-3-walcu-api` desde `origin/main` (`a260e98`, que ya trae las salidas y la previsión). Sin push, sin
conectarse al servidor y sin credencial: todo se ha probado con respuestas de ejemplo. Migración nueva: 0023.

## Qué hay

1. **Conector** (`ingesta/walcu_api.py`, parámetros en `config/walcu_api.yaml`):
   - recursos `cars` (`/cars/all`, también vendidos y bajas), `saleleads` y `dates`; `appraisalleads`, fuera;
   - cliente HTTP que solo sabe hacer GET, con la API Key en una cabecera configurable;
   - incremental por `updated_at`: marca de agua por recurso en `walcu_api_marca` (la hora de inicio de la carga) y
     se pide desde la marca menos 60 minutos de solape. La inserción es idempotente: cada fila lleva una huella y
     solo se añade una versión si cambia;
   - paginación configurable, «página y tamaño» o «cursor», y dónde vienen los elementos en la respuesta. Pendiente
     de que Walcu la confirme, y así lo dice el resumen de cada carga;
   - reintentos con espera ante errores de red, 429 y 5xx. Con 401 o 403 para en seco con «Walcu rechazó la
     credencial (HTTP …)»;
   - si la API devuelve algo actualizado antes de lo pedido (el filtro no se aplica), para para no descargarlo
     todo: nunca un volcado completo diario.
2. **Privacidad**:
   - Lista blanca por recurso. Todo lo demás se descarta en memoria; el bucket (`walcu_api/raw/<día>/`) guarda el
     JSON ya filtrado, nunca la respuesta.
   - La configuración no arranca si la lista blanca lleva nombres, teléfonos, correos, direcciones, DNI, notas,
     comentarios, `computed` o campos personalizados no confirmados (`personalizados_no_personales`).
   - `client_id`, el usuario de cada transición, el comercial asignado, la matrícula y el bastidor se guardan solo
     como HMAC-SHA256 con `WALCU_SEUDONIMO_CLAVE`. Sin esa clave no se guarda nada.
   - Un texto de la lista blanca con aspecto de correo, teléfono o DNI (p. ej., una campaña) se guarda vacío.
   - Registro y errores: solo nombres de campo y recuentos. Los errores de la API no copian su mensaje.
3. **Órdenes**: `make walcu-api` (en el crontab a las 06:30), `make walcu-api-inicial DESDE=AAAA-MM-DD` (por meses),
   `make walcu-api-probar` (una página de cada recurso: nombres de campo, tipos, en cuántos elementos aparece cada
   uno y si se guarda, se seudonimiza o se descarta; ningún valor, nada guardado) y `make walcu-api-mapeo`. Estado
   en `fuentes_estado` (`walcu_api`) y alerta «Fuente caída» como Meta y GA4.
4. **Tablas** (migración 0023, solo añadir; vistas `*_vigente`):
   - `walcu_api_vehiculo`: tipo, marca, modelo, versión, combustible, código de versión, location, fecha de
     entrada (`purchase_date` o, si no viene, `created_at`), publicado, despublicación, precio, km, año, color,
     estado, matrícula y bastidor seudonimizados;
   - `walcu_api_vehiculo_precio_dia` (foto diaria del precio de los publicados) y `walcu_api_vehiculo_estado`;
   - `walcu_api_lead`: cliente seudonimizado, alta, origen (source, medium, campaign), tipo y modelo de
     `car_list`, location, estado y comercial seudonimizado. Su historia de estados va en `walcu_api_lead_estado`,
     con fecha y usuario seudonimizado;
   - `walcu_api_cita`: lead, tipo de lead, cliente seudonimizado, vehículo, fecha, `showed_up` y estado.
5. **Mapeo** (`config/walcu_api.yaml`, vacío, «a definir con AVISA»): estados del vehículo (vendido, reservado,
   baja, publicado, finales), estados del lead (venta y sus hitos Firmado, Matriculado y Entregado, descartado,
   finales, número inexistente) y location (incluidas, excluidas, de VO). Mientras esté vacío, descarga y guarda
   pero no deduce ventas ni stock ni excluye sedes. El resumen de cada carga y `make walcu-api-mapeo` dicen qué
   estados y location hay, cuántos y cuáles faltan por mapear.
6. **Con datos y mapeo** (`ingesta/walcu_crm.py`, tras cada carga; sin ellos todo sigue como ahora):
   - **Walcu por semanas desde la API** (`walcu_campana_semana`, columna nueva `fuente` = `api`), con las mismas
     columnas, exclusiones y cohortes que el CSV:
     - cita = el lead tiene una cita a la que el cliente asistió (`showed_up`);
     - venta = el lead está en un estado de venta.
     - Se escribe solo si cambia algo frente a lo vigente; después se rehacen el cruce con Meta y las referencias.
       La carga por CSV sigue disponible.
   - **Tipo VN/VO**: la regla de siempre (nombre de la campaña; si no, location de VO; si no, la tipología de
     `car_list`; si no, el tipo por defecto). El resumen da el porcentaje de acuerdo con la tipología de `car_list`.
   - **Dirección**: «Ventas del mes» con las ventas del CRM, por VN y VO, y un bloque por mes, tipo y modelo. Las
     salidas de stock quedan debajo como «comprobación de las ventas del CRM».
   - **Stock del CRM** en `panel_stock_vehiculo` (además, con el mapeo de estados del vehículo). Enciende las
     tarjetas de stock y antigüedad de Dirección, hasta ahora «Pendiente de conectar», y el bloque nuevo de Ventas
     «Stock VO en el CRM» (cuántos, días de media desde su entrada, los que no están publicados en la web). Un
     traspaso mantiene el id: a otra sede que cuenta, sigue en stock; a una excluida, sale del stock sin ser venta.
   - **Precio provisional**: si el anuncio de la web casa con el CRM por el bastidor (los dos seudonimizados), las
     reglas usan los días en stock desde la entrada real, y Ventas los muestra como «N en stock».
7. `CLAUDE.md`: regla de no conectarse nunca al servidor ni a su base de datos.

## La documentación de la API (v0.0.2) frente al encargo

Lo que no cuadra o no está en `docs/walcu_api.pdf`:

1. **`prices.*.previous_prices` sí lleva `created_at`** en la documentación (el encargo dice que no lleva
   fecha). No se usa: no está en la lista blanca, y la foto diaria del precio fecha las bajadas igualmente. Si la
   prueba con la credencial enseña que viene relleno, se puede añadir.
2. **Las citas (`date`) no tienen campo de estado** en la documentación: tienen `showed_up`, `is_vacation` y
   `notification.done`. `status` está en la lista blanca marcado PENDIENTE; si no viene, la columna queda vacía y
   nada depende de ella. El vehículo de la cita va en `salelead.car_id`, no en un campo propio.
3. **Código de versión**: el único código estructurado es `provider_codes.eurotax`; es el que se guarda.
4. **Tipología de `car_list`**: `type` con new, used, demo o km0 (no «VN/VO/Demo/Km0»). Se pasa a VN o VO con
   `tipos` (Km0 y Demo, VO, como en la exportación).
5. **Ni parámetros ni autenticación**: la documentación no dice el nombre de la cabecera de la API Key, ni cómo
   se filtra por `updated_at`, ni la paginación. Los tres son configurables y se comprueban con
   `make walcu-api-probar`.
6. **Comercial**: el lead trae `current_assigned.to` (el asignado). Es el que se usa, seudonimizado, para los
   comerciales excluidos.
7. **Código postal**: el lead solo lo trae dentro de `computed` (`computed.client_address.postal_code`). Desde
   los ajustes (abajo) es la única excepción a «nunca `computed`».

## Decisiones tomadas sin preguntar

1. **Rama desde `origin/main`**, como pide el encargo. Ya trae las salidas y la previsión; la migración nueva es
   la 0023.
2. **Comerciales excluidos**: en el servidor, `WALCU_COMERCIALES_EXCLUIDOS` en el `.env` con sus ids de usuario de
   Walcu (no nombres), que solo se usan en memoria para calcular su seudónimo y compararlo. El fichero local de
   nombres sigue solo en el Mac para el CSV. El registro dice solo cuántos leads se excluyen.
3. **Zona**: sustituida por los ajustes (abajo): el código postal se lee en memoria y el lead guarda su zona, así
   que la regla e vuelve a tener datos con la API.
4. **Venta**: el lead está ahora en un estado de venta (o de sus hitos). Su fecha es la del primer Firmado; si no
   lo hay, la del primer estado de venta. A diferencia del CSV, no exige cita (con los ajustes se guarda además
   `ventas_con_cita`). Si el lead sale del estado de venta
   (venta anulada), deja de contar en todo.
5. **`car_list` con varios coches**: se toma el primero (se guarda cuántos trae).
6. **La API manda sobre el CSV**: cada carga compara lo generado con lo vigente de esas semanas y, si difiere, lo
   añade. Un CSV cargado a mano vale hasta la siguiente carga de la API que traiga otra cosa. Las semanas empiezan
   en el primer lunes desde el `DESDE` de la carga inicial, para no dejar una semana a medias.
7. **Ventas del CRM**: el tipo es la tipología del coche vendido, no la regla de la campaña; las exclusiones, las
   mismas que en Walcu por semanas.
8. **Stock del CRM**: llena `panel_stock_vehiculo`, la tabla que Dirección ya esperaba para el «stock interno»,
   aunque el encargo solo nombra Ventas. El texto «Pendiente de conectar: stock interno» dice ahora «(API de
   Walcu)». Hace falta además el mapeo de estados del vehículo (vendido o baja).
9. **Enlace web–CRM por el bastidor seudonimizado**: los dos lados con la misma clave y normalización. Los días en
   stock sustituyen a los días publicado en las reglas del precio provisional.
10. **Marca de agua**: la hora de inicio de la carga, con 60 minutos de solape. Lo repetido no se duplica.
11. **Seudónimo**: HMAC-SHA256 del valor sin espacios ni guiones y en mayúsculas, para que «1234 BCD» y «1234BCD»
    den lo mismo.
12. **`make walcu-api-probar`** pide los últimos 7 días, para que la página sea pequeña y sirva también para
    comprobar el filtro.
13. **Foto diaria del precio**: de los coches publicados con precio, una por coche y día (la primera carga del día).

## Para el día de la credencial

Los pasos están en `infra/deploy.md`, apartado 7.16:
1. poner la clave en `.env`;
2. `make walcu-api-probar`;
3. ajustar la cabecera, el filtro y la paginación;
4. `make walcu-api-inicial DESDE=…`;
5. `make walcu-api-mapeo` y mapear estados y sedes con AVISA;
6. repetir `make walcu-api-inicial DESDE=…` para llenar el agregado por código postal.

## Pruebas

`tests/test_walcu_api.py`, 35 pruebas (26 + 9 de los ajustes), con una API de Walcu simulada (filtra por fecha, pagina por página o por
cursor) y respuestas llenas de datos personales falsos. Cubren:
- **Privacidad**:
  - nada personal ni ningún identificador en claro en la base, el bucket ni el registro;
  - el bucket solo con la lista blanca;
  - la lista blanca rechaza campos personales;
  - textos con aspecto personal vaciados;
  - sin clave de seudónimo no se guarda nada;
  - HMAC estable, con clave.
- **Solo GET**: en el cliente y en el código.
- **Credenciales**: sin ellas, ninguna petición.
- **Paginación** por página y por cursor, y parada si la API no aplica el filtro.
- **Incremental**: marca de agua e idempotencia, cambio de precio como versión nueva y carga inicial repetida sin
  duplicar.
- **Errores**: 401 y 403 paran sin reintentar y lo anotan sin la clave; los 5xx se reintentan. El resumen se anota en `fuentes_estado` sin valores.
- **Mapeo**:
  - pendiente, sin ventas, stock ni semanas, con el inventario;
  - Walcu por semanas desde la API con la cita por `showed_up`, las exclusiones (comercial, sede, otra marca) y el
    acuerdo del tipo;
  - traspaso de sede, que no es venta;
  - venta anulada, que vuelve al stock y deja de contar;
  - ventas del CRM en Dirección y fecha de entrada en el precio provisional.
- **Replicable**: nada del concesionario en el código del conector.

Ajustes en las pruebas existentes:
- `test_contrato_panel`: Dirección con «Ventas del mes» del CRM;
- `test_direccion_real`: el texto «stock interno (API de Walcu)»;
- `test_precio_provisional` y `test_acciones`: el bloque nuevo de Ventas lee `panel_stock_vehiculo`, que también
  tiene que ser real para abrir Ventas al rol de ventas;
- `test_infra`: el crontab a las 06:30.

## Ajustes (segundo commit, tras rebasar sobre `origin/main` `53af9c7`)

1. **Código postal**, única excepción acotada a «nunca `computed`»: `saleleads.codigo_postal` en
   `config/walcu_api.yaml` (`computed.client_address.postal_code`; la configuración no arranca con otra cosa que un
   único campo `postal_code` de los leads). Se lee solo en memoria:
   - el lead guarda la zona (`cercana`, `fuera` o `sin_dato`, con la zona cercana de `config/concesionario.yaml`)
     y la provincia (los dos primeros dígitos); Walcu por semanas reparte `leads_cercana`, `leads_fuera` y
     `leads_sin_zona` con ella;
   - el código postal completo solo va a `walcu_api_cp_semana`: leads, citas, ventas y ventas con cita por código
     postal, semana del lead y tipo, sin lead ni cliente. Nunca en el lead, el bucket ni el registro (la prueba de
     privacidad lo comprueba en toda la base salvo esa tabla).
2. **Ventas**: dos cifras en `walcu_campana_semana`, `panel_campana_semana`, `panel_referencia_meta` y
   `panel_ventas_crm`: `ventas` (todas, con su fecha: coste por venta y Dirección) y `ventas_con_cita` (el lead tuvo
   una cita con `showed_up` que empezó antes de la venta: tasa de cita a venta, comparable con el CSV). En las
   filas del CSV, `ventas_con_cita` vale `ventas`. La usan la tasa de cita a venta de las referencias, el
   criterio d y la previsión; el coste por venta sigue con todas.
3. **Sin credencial**: sin `WALCU_API_KEY`, `make walcu-api` y la tarea de las 06:30 terminan sin error, anotan
   `sin_configurar` en `fuentes_estado` (estado nuevo, migración 0023) y no disparan la alerta de fuente caída.

### Decisiones de los ajustes tomadas sin preguntar

1. **El agregado por código postal suma cambios**: el código postal solo está en memoria durante la carga, así
   que cada carga suma la diferencia entre lo que cuenta ahora cada lead descargado y lo ya sumado
   (`walcu_api_cp_contado`, sin el código postal). Si el código postal de un lead cambia, lo ya sumado se resta
   del nuevo (no se sabe el anterior) y el reparto entre códigos queda algo desviado; si desaparece, el lead se
   queda como estaba. Los totales siempre cuadran con Walcu por semanas.
2. **Leads con citas nuevas**: una cita puede no cambiar el `updated_at` del lead. Los leads de las citas de cada
   carga que no han venido en la de leads se piden uno a uno (`GET /saleleads/{id}`, `ruta_uno`, tope
   `leads_por_cita_max` 2000), para leer su código postal y sumar su cita. Los que ya no existen (404) se saltan.
3. **Solo con el mapeo definido**, como Walcu por semanas (sin él no se sabe qué sedes excluir ni qué es venta).
   Por eso, tras definir el mapeo, hay que repetir `make walcu-api-inicial` (paso 4 de `infra/deploy.md` 7.16).
4. **Código postal válido**: 5 dígitos de 01 a 52 (admite 4 dígitos sin el cero inicial); si no, `sin_dato` y
   sin código postal en el agregado (fila vacía, para que los totales cuadren).
5. **Cita antes de la venta**: la cita asistida más temprana empieza antes del primer Firmado (o del primer estado
   de venta). Una cita sin fecha de inicio no cuenta para `ventas_con_cita`.
6. **Con `WALCU_API_KEY` pero sin `WALCU_DEALER_ID`** es un error (y salta la alerta): alguien empezó a
   configurarlo y le falta algo. `make walcu-api-inicial` sin clave termina con error, porque se lanza a mano.
7. **Celdas pequeñas**: un código postal con un solo lead en una semana y tipo, cruzado con la semana y el tipo
   del lead, podría señalarlo. La tabla no es del panel (sin permiso para `cdf_panel`); si se publica, habrá que
   agrupar o suprimir las celdas pequeñas.
8. Las filas ya calculadas de `panel_campana_semana` y `panel_referencia_meta` (todas del CSV) toman
   `ventas_con_cita` = `ventas` en la migración.

