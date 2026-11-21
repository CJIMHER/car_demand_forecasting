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
| Campañas de Meta: reglas a, b, c, d, f y subir presupuesto | marketing | `panel_campana_dia` + `panel_leads_dia` (y Mercado en la zona cercana para la d) | Preparadas |
| Ventana favorable para una campaña general | marketing | `panel_semana_campana` | Preparada |

Reglas de mercado: solo evalúan un tipo (VN, VO) si hoy tiene captura completa y la tiene también el
día de hace 7 (`panel_mercado_dia.hace7_completo`), y nunca por debajo del n mínimo de
`config/concesionario.yaml` (`n_minimo_porcentaje` para stock y peso, `n_minimo_medianas` para el
precio). Si no se puede evaluar, sus alertas abiertas siguen abiertas: ni se cierran ni salta otra.

Preparadas: se activan solas cuando sus tablas del panel tienen filas reales. Con solo filas de
ejemplo, calculan sobre el ejemplo y su alerta va únicamente al técnico, con «[EJEMPLO]» delante
(la base de datos no admite otra cosa). Sin filas, inactivas.

### Criterios de las campañas de Meta

Los de Cayetano, tal cual en `docs/criterios_marketing_meta.md`, con todos sus umbrales en
`reglas.yaml` («criterios_meta»), los mismos para VN y VO. `criterios.py` los aplica a cada campaña
activa desde su segundo día de vida y de ahí salen, con las mismas cifras: las alertas `meta_*` para
marketing, la decisión de cada campaña (`panel_campana_decision`: continuar, revisar, parar o subir
presupuesto) y la recomendación por modelo (`panel_recomendacion_modelo`).

- a) lead → cita < 5 % (con al menos 20 leads) o fuera de zona > 30 %: revisar.
- b) leads por visualización < 3 %, o menos de la mitad que las campañas del mismo modelo y mes de años anteriores: revisar.
- c) coste por lead > 10 €: continuar si el coste por cita está en su media (la de las demás campañas de los últimos 90 días, +20 %); si no, parar. Sustituye a «el doble de su media de 28 días».
- d) tasa de venta < 33 % (con al menos 6 citas) con coste por lead y por cita en su media: revisar, con los similares de la zona cercana (mismo modelo, acabado y motor).
- e) lanzar solo con al menos 3 unidades propias en la zona cercana y nuestro precio por debajo de la mediana de la competencia (mismo modelo, acabado y motor); primero los de más stock y mejor precio. Es real desde ya: sale del precálculo de Mercado.
- f) cansancio: leads por día de los 3 últimos días al menos un 30 % por debajo de los 7 anteriores, y coste por lead de esos 3 días > 10 €: revisar.
- Subir presupuesto: todo a la vez (coste por lead < 10 €, por cita < 100 €, lead → cita > 5 %, tasa de venta > 33 %, por venta < 400 €, la regla e) y ninguna regla de revisión activa.

Con poco volumen se muestra la cifra y la regla no salta. La comparación con la referencia por días
de vida (`panel_campana_salud`) es solo detalle explicativo.

### Estados

Una fila de `alertas` por día, regla, rol y clave (lo que la dispara: modelo, segmento, campaña…):
**nueva** el primer día, **abierta** mientras se sigue cumpliendo (no se reenvía), **cerrada** el día
que deja de cumplirse. Se envían las abiertas que nunca se le han enviado a ese rol desde que se
abrieron: las nuevas y las que no salieron por un fallo de Telegram.

### Telegram

Destinos en el `.env`: `TELEGRAM_CHAT_ID` (técnico), `TELEGRAM_CHAT_DIRECCION`,
`TELEGRAM_CHAT_VENTAS` y `TELEGRAM_CHAT_MARKETING`. Si falta el de un rol, sus mensajes van al
técnico con «[Para ventas]» delante. Las alertas calculadas sobre filas de ejemplo no se envían salvo
con `ALERTAS_ENVIAR_EJEMPLO=1` en el `.env` (se guardan y se ven en el panel igual). Cada envío queda en `alertas_salida`; un fallo se anota además
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
