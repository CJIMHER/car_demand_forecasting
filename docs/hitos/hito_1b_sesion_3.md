Repositorio car_demand_forecasting. Lee CLAUDE.md, docs/diseno.md (Señales y alertas; 5. Marketing), docs/contrato_panel.md, docs/hitos/hito_1b_sesion_2.md, ingesta/telegram.py y analitica/.

Hito 1b, sesión 3 de 3: motor de alertas con envío por Telegram por rol, botones de Ventas y conectores de GA4 y Meta preparados. Rama nueva hito-1b-sesion-3 desde main, sin push y sin conectarte al servidor. Guarda este encargo tal cual en docs/hitos/hito_1b_sesion_3.md.

1. Motor de alertas (alertas/)
- Tarea diaria a las 09:15 en el cron de cdf_jobs, después del precálculo de las 09:10. Determinista, sin ningún modelo de lenguaje.
- Reglas en alertas/reglas.yaml, nunca en el código: id, rol destinatario, fuente de la que depende, umbrales y texto con huecos para las cifras. Cada alerta lleva su motivo en una frase con los números que la disparan.
- Con datos reales desde ya:
  - Fuente caída: un scraper sin captura del día, la carga, el precálculo o la copia en error (destinatario: técnico).
  - Presión de un competidor: en la provincia propia, otro concesionario sube en 7 días su stock de un modelo por encima de un umbral, o baja el precio mediano de un segmento (modelo + acabado) con n suficiente (destinatarios: ventas y dirección).
  - Pérdida de peso: el peso propio en un modelo cae más de N puntos en 7 días, con base mínima (destinatarios: ventas y dirección).
  Ninguna alerta de mercado salta si falta la captura de hace 7 días o el n no llega al mínimo de config/concesionario.yaml.
- Preparadas pero inactivas hasta que su fuente sea real (lo decide si sus tablas panel_* tienen filas con origen real): vehículo envejecido, riesgo de inmovilización, ventas fuera de la banda, caída de leads, coste por lead fuera de rango, campaña en «vigilar» o «revisar» con su eslabón, campaña sin citas y ventana favorable para una campaña general.
- Una alerta calculada sobre filas de ejemplo solo puede ir al rol técnico, con «[EJEMPLO]» delante. Pruébalo.
- Tabla alertas (migración nueva): fecha, regla, rol, clave de lo que la dispara (modelo, segmento, campaña…), texto, cifras en JSON, origen y estado (nueva, abierta, cerrada). Una alerta abierta que se repite al día siguiente no se reenvía; se cierra sola cuando deja de cumplirse.
- La vista Dirección y las de cada rol leen sus alertas de esta tabla. El bloque de alertas deja de ser de ejemplo para las reglas que ya tienen datos reales.

2. Envío por Telegram
- Un mensaje por rol y día, en texto plano, agrupando sus alertas nuevas, con ingesta/telegram.py (reintentos, token nunca en logs).
- Destinos en el .env: TELEGRAM_CHAT_ID (técnico, ya existe), TELEGRAM_CHAT_DIRECCION, TELEGRAM_CHAT_VENTAS y TELEGRAM_CHAT_MARKETING. Si falta el de un rol, sus alertas van al técnico con el rol delante. Añádelos a .env.example sin valores.
- Tabla alertas_salida: qué se envió, a quién y cuándo, y si falló. Un fallo de Telegram se anota en fuentes_estado y no rompe la tarea.
- make alertas lanza la tarea a mano; SIN_TELEGRAM=1 make alertas la calcula sin enviar.

3. Botones de Ventas
- «Pedir campaña a marketing» y «Revisar precio» se activan: envían un aviso por Telegram (al rol marketing o ventas) con el vehículo, sus días en stock y su probabilidad de venta, y lo anotan en una tabla acciones (vehículo, acción, usuario del panel, fecha). Con POST y CSRF. Ninguna escritura en Walcu.
- Un vehículo con una acción en los últimos 7 días lo muestra en su ficha, y el botón no deja repetir la misma acción en ese plazo.
- Con datos de ejemplo, el aviso solo va al grupo técnico con «[EJEMPLO]».

4. Conectores de GA4 y Meta (ingesta/), inactivos hasta tener credenciales
- Meta Marketing API, solo lectura (ads_read): insights diarios por campaña, conjunto de anuncios y anuncio (gasto, impresiones, alcance, frecuencia, clics, CPM, acciones de lead si las hay). Token en el .env.
- GA4 Data API, solo lectura (cuenta de servicio con rol Lector): por día, fuente, medio, campaña y página de destino: sesiones, sesiones con interacción y eventos clave. El fichero de la cuenta de servicio fuera del repositorio, ruta en el .env, permisos 600.
- Ningún dato personal ni identificador de usuario. Las respuestas brutas, comprimidas al bucket como las de los scrapers.
- Tablas de hechos de solo añadir (campanas_meta_dia, ga4_trafico_dia) con origen y fecha de descarga; recarga de los últimos 7 días cada noche, porque Meta corrige sus cifras.
- Unión con los leads de Walcu por utm_campaign: deja la regla de unión documentada en config/campanas.yaml (nombre de campaña → modelo, ámbito VN/VO/general), con las campañas sin regla listadas en «Estado de las fuentes».
- Si faltan las credenciales, la tarea no hace nada y lo anota como «sin credenciales», sin aviso de error.
- Pruebas con respuestas simuladas: paginación, límites de la API (429 y backoff), corrección de días pasados sin duplicar, y que ningún campo personal llega a las tablas.

5. Pruebas, sin red
- Cada regla con casos que la disparan y que no, incluido el de n insuficiente y el de falta de captura.
- Una alerta con filas de ejemplo nunca va a un rol que no sea técnico.
- Deduplicación: tres días seguidos con la misma condición, un solo envío.
- Botones: CSRF, permisos por rol, bloqueo de 7 días y anotación en acciones.
- Conectores: sin credenciales no hacen ninguna petición.
- Las variables nuevas del .env quedan cubiertas por la limpieza de tests/conftest.py.

Termina con lo probado y lo no probado, las decisiones tomadas sin preguntar, la entrada en CHANGELOG.md y los pasos para Cayetano (incluidos los permisos exactos que hay que pedir en Meta y en GA4).
