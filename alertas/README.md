# alertas

Motor de alertas y envío por Telegram por rol (hito 1b, sesión 3). Determinista: reglas con
umbrales fijos, sin ningún modelo de lenguaje. Cuando la predicción bayesiana esté validada, sus
probabilidades sustituirán a los umbrales (docs/diseno.md, Señales y alertas).

## Qué hace

- `reglas.yaml`: las reglas. Cada una con su id, sus destinatarios, las tablas de las que depende,
  sus umbrales, el n mínimo (una clave de `config/concesionario.yaml`) y su texto con huecos para las
  cifras. Nada de esto está en el código.
- `reglas.py`: el cálculo de cada regla (solo comparaciones). Devuelve lo que salta, con sus cifras en
  bruto (columna `cifras`, JSON) y ya escritas para el texto.
- `motor.py`: cron de `cdf_jobs` a las 09:15, después del precálculo de las 09:10. Evalúa todas las
  reglas del día y escribe `alertas` y `alertas_evaluacion`; después envía.
- `envio.py`: un mensaje de Telegram por rol y día con sus alertas nuevas (ingesta/telegram.py:
  reintentos y token nunca en logs). Y, cada minuto, los avisos de los botones de Ventas.

### Reglas

| Regla | Para | Fuente | Estado |
| --- | --- | --- | --- |
| Fuente caída | técnico | `fuentes_estado`: un scraper sin captura del día, la carga, el precálculo o la última copia en error; un conector con error | Real |
| Presión de un competidor (stock) | ventas, dirección | `panel_mercado_competidor`: otro concesionario de la provincia sube su stock de un modelo en 7 días (≥ 5 anuncios y ≥ 25 %) | Real |
| Presión de un competidor (precio) | ventas, dirección | `panel_mercado_competidor`: baja el precio mediano de un segmento (modelo + acabado) ≥ 3 % en 7 días | Real |
| Pérdida de peso | ventas, dirección | `panel_mercado_modelo`: nuestro peso en un modelo cae más de 5 puntos en 7 días | Real |
| Vehículo envejecido | ventas | `panel_riesgo_vehiculo` + `panel_curva_grupo` | Preparada |
| Riesgo de inmovilización | ventas, dirección | `panel_riesgo_vehiculo` | Preparada |
| Ventas fuera de la banda | dirección | `panel_ventas_mes` | Preparada |
| Caída de leads | marketing | `panel_leads_dia` | Preparada |
| Coste por lead fuera de rango | marketing | `panel_campana_dia` + `panel_leads_dia` | Preparada |
| Campaña en «vigilar» o «revisar», con su eslabón | marketing | `panel_campana_salud` | Preparada |
| Campaña sin citas | marketing | `panel_campana_dia` + `panel_leads_dia` | Preparada |
| Ventana favorable para una campaña general | marketing | `panel_semana_campana` | Preparada |

Reglas de mercado: solo evalúan un tipo (VN, VO) si hoy tiene captura completa y la tiene también el
día de hace 7 (`panel_mercado_dia.hace7_completo`), y nunca por debajo del n mínimo de
`config/concesionario.yaml` (`n_minimo_porcentaje` para stock y peso, `n_minimo_medianas` para el
precio). Si no se puede evaluar, sus alertas abiertas siguen abiertas: ni se cierran ni salta otra.

Preparadas: se activan solas cuando sus tablas del panel tienen filas reales. Con solo filas de
ejemplo, calculan sobre el ejemplo y su alerta va únicamente al técnico, con «[EJEMPLO]» delante
(la base de datos no admite otra cosa). Sin filas, inactivas.

### Estados

Una fila de `alertas` por día, regla, rol y clave (lo que la dispara: modelo, segmento, campaña…):
**nueva** el primer día, **abierta** mientras se sigue cumpliendo (no se reenvía), **cerrada** el día
que deja de cumplirse. Se envían las abiertas que nunca se le han enviado a ese rol desde que se
abrieron: las nuevas y las que no salieron por un fallo de Telegram.

### Telegram

Destinos en el `.env`: `TELEGRAM_CHAT_ID` (técnico), `TELEGRAM_CHAT_DIRECCION`,
`TELEGRAM_CHAT_VENTAS` y `TELEGRAM_CHAT_MARKETING`. Si falta el de un rol, sus mensajes van al
técnico con «[Para ventas]» delante. Cada envío queda en `alertas_salida`; un fallo se anota además
en `fuentes_estado` y no rompe la tarea.

### Botones de Ventas

«Pedir campaña a marketing» (aviso a marketing) y «Revisar precio» (aviso al jefe de ventas). El
panel anota la petición en `acciones` y cdf_jobs envía el aviso en el minuto siguiente (el panel no
tiene el token). La misma acción sobre el mismo vehículo no se repite en 7 días. Con datos de ejemplo,
el aviso va solo al técnico con «[EJEMPLO]». Nada se escribe en Walcu.

## Órdenes

    make alertas                    lo mismo que el cron de las 09:15
    SIN_TELEGRAM=1 make alertas     calcula y muestra los mensajes sin enviar nada
    DIA=2026-11-21 make alertas     otro día

Recalcular un día sustituye sus filas; lo ya enviado no se repite.

## Cómo se prueba

    .venv/bin/python -m pytest tests/test_alertas.py tests/test_acciones.py

Sin red (Telegram siempre simulado): cada regla con casos que la disparan y que no (n insuficiente,
falta de la captura de hace 7 días), alertas sobre filas de ejemplo solo para el técnico, tres días
con la misma condición y un solo envío, chat de reserva, fallo de Telegram, SIN_TELEGRAM, las vistas
leyendo de la tabla y los botones (CSRF, permisos por rol, bloqueo de 7 días, anotación y aviso).
