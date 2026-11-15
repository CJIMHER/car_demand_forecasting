Repositorio car_demand_forecasting. Lee CLAUDE.md, panel/README.md, infra/ (docker-compose.yml, Makefile, deploy.md), ingesta/README.md y docs/prototipo/ (diseño aprobado por el jefe de ventas: respeta su estructura, colores, tipografías y textos).

Hito 1b, sesión 1 de 3: base del panel y vista «Mercado» con datos reales. Rama nueva hito-1b desde main, sin push y sin conectarte al servidor. Este encargo sustituye a la ficha «Hito 1b» de docs/desarrollo.md, que está desactualizada: guárdalo tal cual en docs/hitos/hito_1b_sesion_1.md.

Contexto. El plan ha cambiado: se construye ya la estructura completa del panel y las fuentes (Walcu, GA4, Meta) se conectan después. Esta sesión deja lista para producción la base y la única vista que ya tiene datos reales. Las vistas Dirección, Ventas y Marketing llegan en la sesión 2. No toques scrapers/, infra/cdf_scrapers, las tablas de hechos ni el comportamiento de las tareas actuales (carga, comprobación, copia).

1. Aplicación, en panel/
- FastAPI, Jinja2, htmx y ECharts, sin compilación de frontend.
- Nada de terceros en el navegador: htmx, ECharts y las fuentes Space Grotesk e Inter van en el repositorio, con versión fija y su licencia.
- Traduce el prototipo a plantillas y a una hoja de estilos con variables (#0F1B2D, #1D5FD6, #F2A93B, #F6F5F1). No copies el runtime de los .dc.html (support.js, x-dc).
- Adaptable: escritorio como Mercado.dc.html y móvil con la barra inferior de Movil.dc.html, con zonas táctiles de 44 px como mínimo.
- Interfaz en español; números y fechas en formato español y hora de Madrid.

2. Acceso y roles
- Usuario y contraseña por persona, con hash argon2id. Sin registro ni recuperación desde la web: alta, baja, cambio de rol y restablecimiento con órdenes de make que piden la contraseña por teclado y sin eco, nunca como argumento. Contraseña inicial temporal, de 12 caracteres como mínimo, con cambio obligatorio en el primer acceso.
- Roles direccion, ventas, marketing y tecnico. Permisos en un único fichero, no repartidos por las vistas: direccion ve las cuatro vistas; ventas, Ventas y Mercado; marketing, Marketing y Mercado; tecnico, todo y además «Estado de las fuentes».
- Sesión guardada en el servidor. Cookie solo del host (sin atributo Domain: el dominio principal lo usa otro sistema), Secure, HttpOnly y SameSite=Lax; caducidad por inactividad configurable (30 días por defecto) y cierre de sesión.
- Protección CSRF en todo POST. Bloqueo de 15 minutos tras 5 intentos fallidos, por usuario y por IP, con el mismo mensaje exista o no el usuario.
- Registro de accesos (usuario, hora, resultado, IP), sin contraseñas, que se borra a los 90 días. De las personas del panel solo se guardan usuario, nombre visible y rol.

3. Datos
- El panel nunca calcula sobre las tablas de hechos al servir una página: lee tablas panel_* ya preparadas.
- Tarea nueva de precálculo en analitica/, en el cron de cdf_jobs a las 09:10, que rellena las tablas panel_mercado_* a partir de stock_externo_snapshot, anuncios_externos, eventos_externos y scraper_runs. Las panel_* son derivadas: recalcular un día sustituye ese día (la regla «nada se sobrescribe» es para las tablas de hechos). make precalcular la lanza a mano y, sin argumentos, rehace todo el histórico.
- Anota el resultado en fuentes_estado y, si falla, avisa por Telegram con el mismo mecanismo que el aviso de copia fallida.
- El panel se conecta a Postgres con un usuario propio que solo lee panel_* y fuentes_estado y solo escribe en sus tablas de usuarios, sesiones y accesos. No puede leer ni escribir tablas de hechos. Su contraseña va en el .env y no aparece en migraciones ni en logs.

4. Vista «Mercado» (docs/prototipo/Mercado.dc.html), con datos reales
- Selectores del prototipo: zona (provincia de Sevilla por defecto, Andalucía, España, según la provincia del concesionario) y tipo (VN, VO). VN es la suma de las fuentes vn y vn_otros.
- Cabecera: estado y hora de la última captura de cada scraper, leídos de la base, y el día de los datos mostrados. Si falta la captura de hoy, se dice.
- Tarjetas: anuncios publicados; altas de hoy; ausencias de hoy; y «Nuestro peso en Sevilla» (anuncios propios entre todos los de la provincia) o, en las otras zonas, concesionarios con stock.
- Tabla por modelo, ordenada por anuncios: anuncios, variación frente a hace 7 días, altas y ausencias de 7 días, precio mediano y días publicado.
- Serie de anuncios publicados, hasta 90 días. No interpola: un día sin captura es un hueco.
- El modelo es el que da el scraper, normalizado; el catálogo de versiones llega en el hito 1.
- La nota del prototipo «Faltan los eléctricos…» ya no vale: sustitúyela por una que diga que VN incluye gasolina, diésel, eléctricos e híbridos.

Reglas estadísticas de la vista (cada una con su prueba):
- Nunca «vendido» ni «ventas»: ausencias. Una ausencia es baja solo cuando el scraper la marca retirada (tres capturas seguidas sin verla).
- Un día cuenta para una fuente solo si tiene una captura completa. Un día cuenta para VN solo si la tienen vn y vn_otros.
- Alta observada: anuncio visto por primera vez un día D con captura completa de su fuente en D−1. Los anuncios de la primera captura de una fuente, o los que aparecen tras un hueco, no son altas.
- Altas y ausencias de hoy exigen captura de ayer. La variación a 7 días exige captura de hace 7 días. Las altas y ausencias de 7 días exigen captura en los 7 días. Si falta, se muestra «—» con el motivo, nunca un cero ni una cifra parcial.
- Ausencias de hoy: anuncios que estaban ayer y hoy no. Ausencias de 7 días: anuncios vistos en esos 7 días que hoy no están publicados.
- Días publicado: mediana de (hoy − primer día visto) de los anuncios publicados hoy. Si el anuncio mediano no tiene alta observada, la cifra es una cota inferior y se muestra como «≥ N».
- Precio mediano: de los anuncios publicados hoy con precio. Con menos de 5 anuncios, precio y días se muestran atenuados con su n al lado.

5. Otras páginas
- «Estado de las fuentes» (rol tecnico): últimas anotaciones de fuentes_estado por fuente y tipo, días con y sin captura completa por fuente, y anuncios sin provincia.
- Dirección, Ventas y Marketing aparecen en la navegación según el rol, desactivadas y con el texto «Se activa al conectar sus datos». Sin datos inventados en esta sesión.
- /salud, sin datos, para la comprobación del contenedor.

6. Despliegue
- Completa los servicios panel y caddy del compose. Caddy da HTTPS automático a PANEL_DOMINIO y es el único que publica puertos (80 y 443). Si PANEL_DOMINIO está vacío, make deploy no arranca ni panel ni caddy y todo lo demás sigue como hoy.
- El panel corre sin privilegios y solo se fía de las cabeceras de proxy que le pone Caddy.
- Cabeceras: HSTS, CSP solo con origen propio y sin scripts en línea (o con nonce), X-Content-Type-Options, frame-ancestors 'none', Referrer-Policy y X-Robots-Tag noindex; robots.txt que lo prohíbe todo.
- Variables nuevas en .env.example, sin valores.
- make panel-dev: levanta el panel en el Mac, en localhost, contra un Postgres local con los datos sintéticos de tests/ y un usuario de prueba por rol, para verlo antes de desplegar.
- Actualiza infra/deploy.md con los pasos exactos para Cayetano: registro DNS, apertura del 80 y el 443 en el cortafuegos de Hetzner y en ufw, variables del .env, despliegue, alta del primer usuario y comprobaciones (certificado, cabeceras, puertos publicados).

7. Pruebas, sin red
- Acceso: correcto, incorrecto, bloqueo, cambio obligatorio de contraseña, caducidad, cierre de sesión y CSRF.
- Matriz completa de permisos: cada rol por cada ruta; sin sesión, todo lleva al acceso salvo el propio acceso, /salud y los estáticos.
- Ni la contraseña ni el hash aparecen en logs o respuestas.
- El usuario de Postgres del panel no puede leer ni escribir tablas de hechos.
- Precálculo y vista sobre datos sintéticos de varios días, con huecos y con una fuente que empieza más tarde: cada regla estadística de arriba, y que las cifras de la vista cuadran con una consulta SQL directa.
- El HTML no pide nada a otros dominios y lleva las cabeceras de seguridad.
- Sin PANEL_DOMINIO no arrancan ni panel ni caddy.
- Si tienes Playwright, guarda en docs/capturas/ la vista Mercado en escritorio (1440 px) y en móvil (390 px) con datos sintéticos; si no, dilo.

Termina con: qué has probado y qué no (Docker, Caddy y el certificado no se pueden probar en el Mac), las decisiones tomadas sin preguntar, la entrada en CHANGELOG.md y la lista de pasos para Cayetano.

---

Regla añadida durante la sesión (también en CLAUDE.md):

Añade esta regla a CLAUDE.md y aplícala desde esta sesión:

- Replicable: la aplicación se instalará en otros concesionarios Volkswagen con Walcu, una instancia aislada por concesionario. Nada propio de AVISA va en el código ni en las plantillas: nombre del concesionario, código de concesionario, provincia y comunidad, marca, dominio, chats de Telegram, umbrales y textos como «Volkswagen Sevilla» o «Nuestro peso en Sevilla» salen de config/concesionario.yaml o del .env. La zona por defecto es la provincia del concesionario. Una prueba levanta el panel con un concesionario ficticio de otra provincia y comprueba que no aparece ni «AVISA» ni «Sevilla» en ninguna página.

No construyas nada multicliente (ni tabla de clientes ni columna de concesionario): una instancia, un concesionario.
