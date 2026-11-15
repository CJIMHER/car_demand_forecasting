# panel

Panel web del concesionario: FastAPI + Jinja2 + htmx + ECharts, sin compilación de frontend, detrás
de Caddy (HTTPS automático en `PANEL_DOMINIO`). Traduce el prototipo aprobado (`docs/prototipo/`) a
plantillas y a una hoja de estilos con variables. Hoy tiene datos reales la vista **Mercado**.
**Dirección**, **Ventas** y **Marketing** leen las tablas del contrato de datos
(`docs/contrato_panel.md`), que de momento solo tienen datos de ejemplo (`make ejemplo-cargar`):
con ellos las ve el rol técnico (o los de `PANEL_EJEMPLO_VISIBLE_PARA`), con una banda y una
etiqueta «Datos de ejemplo» en cada bloque; el resto de roles las ve desactivadas («Se activa al
conectar sus datos») hasta que lleguen los datos reales.

## Qué hay

| Fichero | Qué hace |
| --- | --- |
| `app.py` | Aplicación (`uvicorn --factory panel.app:crear_app`): rutas, sesión, CSRF, cabeceras de seguridad |
| `permisos.py` | **Único** sitio con roles y permisos (quién ve qué vista, también según sus datos) |
| `contrato.py` | Qué tablas lee cada bloque y de qué origen (real o ejemplo; un bloque nunca los mezcla) |
| `direccion.py`, `ventas.py`, `marketing.py`, `negocio.py` | Consultas de las tres vistas sobre las tablas del contrato |
| `ejemplo.py` | Datos de ejemplo deterministas y coherentes entre vistas (`make ejemplo-cargar` / `ejemplo-borrar`) |
| `seguridad.py` | argon2id, sesiones en Postgres, CSRF de doble envío, bloqueo de intentos, IP detrás de Caddy |
| `mercado.py` | Consultas de la vista Mercado y de «Estado de las fuentes»: solo tablas `panel_*` y `fuentes_estado` |
| `formato.py` | Números y fechas en español, horas de Madrid |
| `usuarios.py` | Alta, baja, rol y restablecimiento (las lanza `make`, la contraseña se pide sin eco) |
| `rol_bd.py` | Activa el usuario de Postgres `cdf_panel` con `PANEL_DB_PASSWORD` (verificador SCRAM) |
| `dev.py` | `make panel-dev`: el panel en el Mac con un Postgres local y datos sintéticos |
| `templates/`, `static/` | Plantillas, CSS, JS propio y, con versión fija y licencia, htmx, ECharts, Inter y Space Grotesk (`static/VERSIONES.md`) |

Rutas: `/acceso`, `/cambiar-clave`, `/salir` (POST), `/mercado`, `/direccion?tipo=todo|VN|VO`,
`/ventas?tipo=…&riesgo=1&v=<número de stock>`, `/marketing?orden=venta|gasto`, `/estado-fuentes`
(rol técnico), `/salud` y `/robots.txt`.

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
el concesionario ficticio de Valladolid en las tres vistas.

En Chrome real, `tests/navegador/capturas.mjs` (instrucciones en su cabecera) entra en el panel de
`make panel-dev`, comprueba a 390 px que nada desborda en Mercado, Dirección, Ventas y Marketing, y
regenera las capturas de `docs/capturas/`.
