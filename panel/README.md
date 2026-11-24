# panel

Panel web del concesionario: FastAPI + Jinja2 + htmx + ECharts, sin compilación de frontend, detrás
de Caddy (HTTPS automático en `PANEL_DOMINIO`). Traduce el prototipo aprobado (`docs/prototipo/`) a
plantillas y a una hoja de estilos con variables. Hoy tiene datos reales la vista **Mercado**.
**Dirección**, **Ventas** y **Marketing** leen las tablas del contrato de datos
(`docs/contrato_panel.md`), que de momento solo tienen datos de ejemplo (`make ejemplo-cargar`):
con ellos las ve el rol técnico (o los de `PANEL_EJEMPLO_VISIBLE_PARA`), con una banda y una
etiqueta «Datos de ejemplo» en cada bloque; el resto de roles las ve desactivadas («Se activa al
conectar sus datos») hasta que lleguen los datos reales. **Dirección** no mezcla nunca: sin el modo ejemplo
explícito (`PANEL_MODO_EJEMPLO=1`) solo muestra datos reales (Mercado, stock publicado, salidas de nuestro
stock como estimación de ventas, precio provisional, Meta con Walcu, referencias por año, GA4 y alertas) y, donde aún no hay fuente, «Pendiente de conectar:
<fuente>».

## Qué hay

| Fichero | Qué hace |
| --- | --- |
| `app.py` | Aplicación (`uvicorn --factory panel.app:crear_app`): rutas, sesión, CSRF, cabeceras de seguridad |
| `permisos.py` | **Único** sitio con roles y permisos (quién ve qué vista, también según sus datos) |
| `contrato.py` | Qué tablas lee cada bloque y de qué origen (real o ejemplo; un bloque nunca los mezcla) |
| `direccion.py`, `ventas.py`, `marketing.py`, `negocio.py` | Consultas de las tres vistas sobre las tablas del contrato |
| `precio.py` | Bloque «Precio de nuestros VO» de Ventas y Dirección (`panel_precio_vo`), con el filtro por modelo |
| `salidas.py` | Salidas de nuestro stock (`panel_salidas_stock`): tarjeta y bloque por mes de Dirección, últimas 4 semanas en Ventas |
| `alertas.py` | Alertas de las vistas: las del motor (tabla `alertas`) o, sin él, las de ejemplo; y el motor, las campañas sin regla y el tráfico de GA4 por canal en «Estado de las fuentes» |
| `ejemplo.py` | Datos de ejemplo deterministas y coherentes entre vistas (`make ejemplo-cargar` / `ejemplo-borrar`) |
| `seguridad.py` | argon2id, sesiones en Postgres, CSRF de doble envío, bloqueo de intentos, IP detrás de Caddy |
| `mercado.py` | Consultas de la vista Mercado y de «Estado de las fuentes»: solo tablas `panel_*` y `fuentes_estado` |
| `formato.py` | Números y fechas en español, horas de Madrid |
| `usuarios.py` | Alta, baja, rol y restablecimiento (las lanza `make`, la contraseña se pide sin eco) |
| `rol_bd.py` | Activa el usuario de Postgres `cdf_panel` con `PANEL_DB_PASSWORD` (verificador SCRAM) |
| `estaticos.py` | URL de los estáticos con la huella de su contenido (`estatico('css/panel.css')` en las plantillas) y cabeceras de caché |
| `dev.py` | `make panel-dev`: el panel en el Mac con un Postgres local y datos sintéticos |
| `templates/`, `static/` | Plantillas, CSS, JS propio y, con versión fija y licencia, htmx, ECharts, Inter y Space Grotesk (`static/VERSIONES.md`) |

Rutas: `/acceso`, `/cambiar-clave`, `/salir` (POST), `/mercado`,
`/mercado/modelo/<modelo>?tipo=VN|VO&zona=provincia|comunidad|espana&acabado=…&combustible=…&motor=…&color=…`
(detalle de un modelo: por acabado, motor y familia de color —acabado → motor → color—, por familia de
color con su cobertura —«Color conocido en el X % de los anuncios» y, por debajo del 90 %, la nota fija— y,
en VO, precio por antigüedad y km con filtros de acabado, combustible y motor. El filtro de color vale para
toda la página: total, tabla y matriz cuentan solo esa familia, con la línea «Filtrando por color…» arriba.
Los filtros se conservan al cambiar de zona o tipo si siguen existiendo; uno que
no existe se descarta y se redirige a la URL sin él; se llega pulsando el modelo en la
tabla de Mercado), `/direccion?tipo=todo|VN|VO&modelo_vo=…`,
`/ventas?tipo=…&riesgo=1&v=<número de stock>&modelo_vo=…` (`modelo_vo`: filtro del bloque «Precio de nuestros
VO»), `/ventas/accion` (POST con CSRF: «Pedir campaña a
marketing» y «Revisar precio»), `/marketing?tipo=todo|VN|VO&orden=venta|gasto`, `/estado-fuentes`
(rol técnico), `/salud` y `/robots.txt`.

Alertas (sesión 3): Dirección muestra todas las abiertas con su destinatario; Ventas y Marketing, las
de su rol. Las leen de la tabla `alertas` del motor (`alertas/`) en cuanto ha evaluado alguna regla
con datos reales (`alertas_evaluacion`); hasta entonces, las de ejemplo de `panel_alertas`. Los
botones de Ventas anotan la petición en `acciones` (cdf_panel solo puede leer e insertar ahí) y el
aviso por Telegram lo envía cdf_jobs: el panel no tiene el token. La misma acción no se repite sobre
un vehículo en 7 días, y la ficha dice quién la pidió, cuándo y si el aviso salió.

## Reglas que cumple

- **Datos**: nunca calcula sobre las tablas de hechos; lee `panel_mercado_*`, que rellena
  `analitica/mercado.py` a las 09:10, y las tablas del contrato (`docs/contrato_panel.md`). Su
  usuario de Postgres (`cdf_panel`) no puede leer ni escribir las tablas de hechos: solo lee
  `panel_mercado_*`, las del contrato y `fuentes_estado`, y escribe en `panel_usuarios`,
  `panel_sesiones` y `panel_accesos`.
- **Datos de ejemplo**: filas `origen = 'ejemplo'`. Un bloque no mezcla orígenes: en cuanto una de
  sus tablas tiene una fila real, solo lee reales. Cada bloque de ejemplo lleva su etiqueta y la
  vista, una banda. `make ejemplo-cargar` y `ejemplo-borrar` solo funcionan con
  `PANEL_PERMITE_EJEMPLO=1` en el `.env` y nunca tocan tablas de hechos ni de mercado.
- **Replicable**: nada propio del concesionario en el código ni en las plantillas. Nombre, marca,
  provincia (zona por defecto), comunidad y umbrales salen de `config/concesionario.yaml`
  (`ingesta/concesionario.py`); el dominio y las contraseñas, del `.env`.
- **Acceso**: usuario y contraseña por persona (argon2id); sin registro ni recuperación por la web.
  Contraseña temporal de 12 caracteres como mínimo, que se cambia en el primer acceso. 5 fallos en
  15 minutos bloquean 15 minutos al usuario y a la IP, con el mismo mensaje exista o no el usuario.
- **Sesión**: en Postgres (en la cookie solo va un testigo aleatorio; en la base, su SHA-256).
  Cookie `__Host-cdf_sesion`: sin Domain, Secure, HttpOnly, SameSite=Lax. Caduca tras
  `PANEL_SESION_DIAS` días sin uso (30 por defecto).
- **CSRF** en todo POST: testigo en cookie `__Host-cdf_csrf` y en el formulario, y `Origin` del propio sitio.
- **Registro de accesos** (usuario si existe, hora, resultado, IP), sin contraseñas; se borra a los 90 días.
- **Cabeceras**: HSTS, CSP solo con origen propio y sin scripts ni estilos en línea, nosniff,
  `frame-ancestors 'none'`, Referrer-Policy same-origin, X-Robots-Tag noindex; `robots.txt` lo prohíbe todo.
- **Proxy**: solo acepta `X-Forwarded-For` si la conexión llega de Caddy (`PANEL_PROXY=caddy`).

## Cómo se prueba

```bash
.venv/bin/python -m pytest tests/test_panel.py tests/test_contrato_panel.py tests/test_mercado.py tests/test_infra_panel.py
make panel-dev        # http://localhost:8000 con datos sintéticos y de ejemplo; usuarios de prueba:
                      # direccion, ventas, marketing y tecnico (las contraseñas salen en la consola)
```

Las pruebas usan el TestClient de Starlette, sin red, con la aplicación conectada como `cdf_panel`.
Cubren acceso, bloqueo, caducidad, cierre, CSRF, la matriz completa de permisos, que ni la
contraseña ni el hash salen en logs o respuestas, los permisos del usuario de Postgres, las
cabeceras, que el HTML no pide nada fuera, las cifras de Mercado frente a SQL directo y un
concesionario ficticio de otra provincia sin rastro del propio.

`tests/test_contrato_panel.py` cubre el contrato (columnas comunes, intervalos, nada personal,
permisos de `cdf_panel`), los datos de ejemplo (deterministas, coherentes, sin tocar hechos ni
mercado, se niegan sin `PANEL_PERMITE_EJEMPLO=1`), la etiqueta de cada bloque y la banda, la
visibilidad de cada rol por vista, el paso de ejemplo a real tabla a tabla, «Sin datos todavía» y
el concesionario ficticio de Valladolid en las tres vistas, Marketing por tipo (las tres opciones
del selector cuadran con las filas; una de ambos nunca tiene coste por venta en VN ni en VO) y la
migración `0006`.

`tests/test_estaticos.py`: la URL de cada estático cambia si cambia su contenido (y la del CSS si
cambia una fuente), no si cambia solo la fecha; estáticos con huella, un año e immutable; HTML,
htmx y errores, `no-store`.

En Chrome real, `tests/navegador/capturas.mjs` (instrucciones en su cabecera) entra en el panel de
`make panel-dev`, comprueba a 390 px que nada desborda en Mercado, Dirección, Ventas y Marketing
(Todo, VN y VO), y a 1440 px que ninguna celda sale de su tarjeta, y regenera las capturas de
`docs/capturas/`. Usa la caché del navegador, como un usuario. `tests/navegador/cache.mjs`, con la
caché activada, carga una versión, cambia `panel.css` y `panel.js` como haría un despliegue (y los
deja como estaban) y comprueba que la siguiente carga usa los nuevos y lo demás sale de la caché.
