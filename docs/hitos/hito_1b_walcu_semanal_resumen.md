# Hito 1b · Walcu por semanas: resumen

Rama `hito-1b-walcu-semanal`, desde `main` en `ea838c1`. Sin push y sin conectarse al servidor.

## Qué cambia

1. **Migración 0019** (`infra/migraciones/versions/0019_walcu_semanal.py`).
   - `walcu_campana_semana` sustituye a `walcu_campana_mes`, que se borra (estaba vacía en el servidor).
   - Clave: semana, origen, campaña y tipo. `semana` es el lunes de la semana ISO del lead.
   - Mismos recuentos, solo añadir, con `fecha_carga`, `run_id`, `fichero_origen` y `huella_fichero`.
   - `walcu_campana_semana_vigente`: para cada semana, la última carga que la trae.
   - `panel_campana_semana` sustituye a `panel_campana_mes` y añade `citas_completas` y `ventas_completas`.
   - `panel_referencia_meta` pasa a `desde_semana`, `hasta_semana` y `semanas`, más las sumas de las
     semanas completas (`*_citas` y `*_ventas`).
   - `panel_walcu_sin_cruce`: `primera_semana` y `ultima_semana`.
   - El `downgrade` recrea las cuatro tablas de la 0017, vacías.
2. **CSV de entrada** (`ingesta/walcu.py`).
   - Lista cerrada: `semana, origen, campana, tipo, leads, leads_cercana, leads_fuera, leads_sin_zona, citas,
     ventas, num_inexistente`.
   - `semana` en AAAA-MM-DD. Si no es una fecha o no es lunes, se rechaza el fichero entero.
3. **`scripts/walcu_agregar.py`**: agrega por el lunes de la semana ISO de «Fecha Lead». Lo demás, igual.
4. **`make walcu-cargar`**.
   - Recargar una semana la sustituye, sin borrar nada.
   - Las validaciones de datos personales no cambian.
   - Avisos nuevos, que no rechazan: filas en que no se cumple ventas ≤ citas ≤ leads, y zonas que no suman
     los leads (este ya existía).
5. **Cohorte y retraso**.
   - Plazos en `config/campanas_walcu.yaml`: `cohorte.lead_a_cita_dias: 14` y `cita_a_venta_dias: 60`.
   - Una semana tiene las citas (o las ventas) completas si, el día en que se cargó, su domingo tenía al
     menos esos días.
   - Lead a cita y coste por cita solo usan semanas con las citas completas; cita a venta y coste por
     venta, semanas con las ventas completas.
   - Se aplica en las referencias, en las reglas y en Marketing.
   - El panel marca las semanas incompletas.
6. **Referencias, últimas 4 semanas y reglas**.
   - «Campañas del último mes» pasa a «Campañas de las últimas 4 semanas»: las cuatro últimas semanas
     terminadas el último día con datos de Meta.
   - Debajo, una tabla por semana con leads, citas, ventas y su estado: completa, ventas incompletas, citas
     y ventas incompletas, o sin carga de Walcu.
   - Referencias por año, sumando semanas.
   - Reglas en la versión `reglas-2026-11-24b`.
7. **Filtro de marca y reglas de Walcu**: no cambian (comerciales excluidos, otras marcas, presupuesto por
   teléfono, valores reales).

## Histórico

`~/cdf_walcu/walcu_semanal_historico.csv`, validado desde esa carpeta sin copiarlo al repositorio:
- `python -m ingesta.walcu comprobar` → «Fichero correcto: 1373 filas, 100 semanas, del 30-12-2024 al
  23-11-2026», sin avisos.
- Cargado en una base de pruebas desechable: 471 filas de campaña y semana, 96 semanas con carga en el
  panel. Sin datos de Meta en local no hay referencias, como se esperaba.
- `walcu_agregado_historico.csv` (mensual) ya no se carga: lo rechaza la columna `mes`.

En el servidor, después de `make deploy`:

```bash
# en el Mac
scp -i ~/.ssh/cdf_hetzner ~/cdf_walcu/walcu_semanal_historico.csv cdf@<ip>:/opt/cdf/entrada/
# en el servidor
make walcu-cargar FICHERO=/opt/cdf/entrada/walcu_semanal_historico.csv
make referencias-meta
```

## Decisiones tomadas sin preguntar

1. **Antigüedad de una semana**: se cuenta desde su domingo (el último lead posible) hasta el día en que se
   cargó, no hasta hoy. Así, una semana con 14 días tiene todos sus leads con 14 días o más, y una carga
   antigua no se da por completa por el paso del tiempo.
2. **Año de las referencias**: el año ISO de la semana. La del 29-12-2025 cuenta en 2026 y la del
   30-12-2024 en 2025.
3. **Referencias solo con semanas terminadas al cargarse**. La semana en curso tiene leads de pocos días
   frente al gasto de Meta hasta ayer, así que no entra. En el panel por semana sí está.
4. **Coste por cita y por venta con la cohorte**, igual que las tasas: el gasto y las citas (o ventas) de
   solo las semanas completas. El coste por lead usa todas las semanas con carga.
5. **Reglas con la carga semanal**.
   - Valen si hay carga de Walcu de la semana del último día con datos de Meta o de la anterior, porque la
     carga semanal llega con retraso. Si no: «la última carga de Walcu es de la semana del …».
   - El gasto con que se calculan los costes es el de las semanas de vida de la campaña que tienen carga de
     Walcu, para comparar el mismo periodo que los leads.
   - Sin ninguna semana con las citas completas, la c propone **revisar** («Citas aún incompletas»), en vez
     de parar por «sin citas».
   - Sin ninguna semana con las ventas completas, la d no se aplica («ventas aún incompletas»).
   - La a sigue calculando fuera de zona con todos los leads.
6. **«Últimas 4 semanas completas»**: semanas de lunes a domingo ya terminadas el último día con datos de
   Meta. Si ese día es domingo, su semana cuenta.
   - En el bloque, € / cita solo usa las semanas con las citas completas, y € / venta las de ventas
     completas. Con 4 semanas recientes, € / venta casi siempre sale «—»: es lo honesto.
7. **Versión de las reglas**: `reglas-2026-11-24b`, para distinguir las decisiones guardadas de hoy antes y
   después del cambio.
8. **Avisos de la carga**: ventas ≤ citas ≤ leads es un aviso, y «más números inexistentes que leads» queda
   como aviso aparte.
9. **Bucket**: los CSV semanales van a `walcu/semanal/<día>/`. Los mensuales iban a `walcu/agregado/`.
10. Los plazos de cohorte viven en `config/campanas_walcu.yaml`, junto al cruce, porque los usan el
    servidor (cruce, reglas, panel) y no el Mac.

## Para revisar

- El primer `make referencias-meta` en el servidor con el histórico semanal: comprobar el número de semanas
  de 2025 y 2026, y cuántas tienen las citas y las ventas completas (el informe lo dice).
- El 2026 incompleto: sus tasas de venta solo cubren hasta mediados de septiembre (60 días).
- La exportación semanal debería cubrir al menos los últimos 60 días, para que las citas y ventas tardías
  actualicen sus semanas al recargarlas.

## Pruebas

`tests/test_walcu_meta_hist.py` pasa de 70 a 85 pruebas. Nuevas o reescritas:
- semana que no es lunes, fecha inválida, cabecera mensual rechazada y lunes de la semana 1 ISO;
- un domingo rechaza el fichero entero;
- aviso de ventas ≤ citas ≤ leads;
- recargar una semana la sustituye;
- la tabla semanal es de solo añadir y la mensual ya no existe;
- límites de 13/14 y 59/60 días;
- cohorte mal configurada;
- año ISO y semana en curso fuera de las referencias;
- reglas con semanas completas e incompletas;
- carga de Walcu antigua;
- bloque de Marketing con las semanas marcadas.

Suite completa en verde.
