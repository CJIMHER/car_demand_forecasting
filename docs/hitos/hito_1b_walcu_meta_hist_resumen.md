Histórico de Meta, cargas de Walcu agregadas y referencias de coste, en la rama `hito-1b-walcu-meta-hist`
(desde `main` en `7139d1f`), sin push y sin conectarme al servidor.

**Pruebas:** suite completa con Postgres (`.venv/bin/python -m pytest -q`): 557 en verde y 1 saltada (la de
`cryptography`, que falta en el entorno local). Las nuevas están en `tests/test_walcu_meta_hist.py` (48).

**Paso 0 (ordenar el repositorio):** `hito-1b-gtrends` ya estaba rebasada sobre `hito-1b-ga4-canales` con la
0016 detrás de la 0015 (sesión anterior). La fusioné en `main` con `--ff-only` (`7139d1f`, ya en GitHub) y
creé esta rama desde ahí. La suite estaba en verde en ese punto (509 + 1 saltada).

## 1. Histórico de Meta

`make meta-historico DESDE=2025-01-01 [HASTA=AAAA-MM-DD]` (por defecto, hasta ayer). Usa el mismo token y la
misma cuenta, solo lectura:

- insights por campaña y día (gasto, impresiones, alcance, frecuencia, clics, leads), por tramos de un mes;
- leads: la acción `lead` o, si no está, `onsite_conversion.lead_grouped`, como la carga diaria;
- campañas en cualquier estado: filtro por `campaign.effective_status` con todos, también ARCHIVED y DELETED;
- nombre y estado de cada campaña en `campanas_meta_estado`;
- espera según `x-business-use-case-usage`: 60 s desde el 75 % de uso, 300 s desde el 90 %, o los minutos
  que diga Meta. Además, los reintentos de siempre ante 429 y 5xx;
- cada tramo sube sus brutos al bucket y se guarda antes de pedir el siguiente. Va a `campanas_meta_dia`
  (la misma tabla que la carga diaria, con la misma huella): relanzar no duplica. No es un upsert que
  sobrescriba: si Meta corrigió una cifra, queda como versión nueva y la vista `_vigente` da la última (regla
  «nada se sobrescribe»);
- las campañas viejas se clasifican con `config/campanas.yaml`; las que no casan van a «Campañas sin regla».

## 2. Walcu agregado

- **Migración 0017**: `walcu_campana_mes` con las 11 columnas más `fecha_carga`, `fichero_origen` (solo el
  nombre, sin la ruta del Mac), `run_id` y la huella del fichero. Clave `mes, origen, campana, tipo` dentro de
  cada carga. «Recargar un mes lo sustituye» sin borrar nada: `walcu_campana_mes_vigente` da, para cada mes,
  solo las filas de la última carga que lo trae. El mismo fichero dos veces no se carga.
- **`make walcu-cargar FICHERO=/ruta.csv`**: el fichero entra por la entrada estándar al contenedor (vale
  cualquier ruta del servidor). Rechaza el fichero entero si:
  - trae otra columna, falta una o se repite;
  - un valor contiene `@`, 9 dígitos seguidos, un teléfono con espacios o algo con forma de DNI o NIE;
  - un valor no tiene formato (mes, tipo, recuentos) o se repite una clave.

  Los mensajes dicen fila y columna, nunca el valor. Antes de guardar, el CSV va comprimido al bucket
  (`walcu/agregado/<día>/`).
- **Tu CSV** (`~/cdf_walcu/walcu_agregado_historico.csv`, no copiado al repositorio; ya filtrado por
  comerciales excluidos y sin las citas de presupuesto por teléfono) pasa la validación: 535 filas. Hay un
  aviso: 1 fila con más citas que leads, más ventas que citas o más números inexistentes que leads. Es un
  aviso y no un rechazo. (La versión anterior, de 591 filas, tenía otro aviso: 10 filas en que las zonas no
  sumaban los leads.)
- **`scripts/walcu_agregar.py`** (Mac; `make walcu-agregar ENTRADA=… SALIDA=…`):
  - lee el .xlsx o el .csv del CRM con la biblioteca estándar y solo las 8 columnas de
    `config/walcu_export.yaml`;
  - calcula la zona con la zona cercana de `config/concesionario.yaml`;
  - conserva la web propia como origen, sacándola de `propio.web` (nuevo campo);
  - escribe solo recuentos. Una campaña con aspecto de dato personal sale como «(campaña omitida)»;
  - el CSV pasa la misma validación que en el servidor antes de escribirse;
  - **comerciales excluidos**: fichero local opcional (por defecto `~/cdf_walcu/comerciales_excluidos.txt`, un
    nombre por línea). Sus leads se descartan antes de agregar. No se versiona (`.gitignore`), no se copia al
    repositorio ni al servidor, y el registro solo dice cuántos leads se han excluido. La columna Vendedor
    solo se lee si hay excluidos;
  - **cita** = negociación creada, salvo que «Resultado última actividad» sea PRESUPUESTO POR TELÉFONO
    (`actividades_no_cita` en `config/walcu_export.yaml`). Venta = cita con estado de venta.

## 3. Cruce y referencias

- **Cruce:** por nombre normalizado (minúsculas, sin tildes, espacios simplificados, sin `-copy`, `copia`,
  `-OK`, también con número detrás). Las equivalencias editables están en `config/campanas_walcu.yaml` (nombre
  de Walcu → nombre de Meta, o `id:…` si el nombre se repite). Solo origen Facebook: AiVORA queda fuera.
- **`make referencias-meta`:** tabla por año y tipo (VN, VO y todo) con gasto, leads según Meta y según Walcu,
  citas, ventas, coste por lead, coste por cita, coste por venta, lead a cita, cita a venta, % fuera de zona
  (sobre los leads con código postal) y % de número inexistente, más la diferencia entre años. Debajo, lo que
  no casa y el motivo.
- **Panel:** Marketing tiene el bloque «Referencias históricas», que se guarda en `panel_referencia_meta`. Las
  de 2025 son la «media» de las reglas.
- **Cómo se calcula:**
  - solo entran los meses con datos de Meta y carga de Walcu a la vez;
  - leads de Walcu: todos los de Facebook del tipo, casen o no con una campaña;
  - gasto: el de una campaña de VN o de VO va a su tipo. El de una de ambos o sin regla se reparte por sus
    leads de Walcu de cada tipo y, si no tiene, cuenta solo en «todo».

## 4. Reglas (versión `reglas-2026-11-24`)

- **a:** el umbral de fuera de zona sube al 35 % (antes 30 %). Lead a cita sigue en 5 %, VN y VO.
- **d:** sigue en 33 %. La alerta termina con «El 33 % es el óptimo: esta alerta saltará a menudo.».
- **Walcu campaña a campaña:** primero el diario (`panel_leads_dia`, cuando exista). Si no, el mensual si hay
  carga del mes del último día de la campaña y la campaña casa con sus leads.
  - Sin Walcu, a, d y subir dicen «sin datos de Walcu: no se aplica» con el motivo (no hay carga de ese mes,
    o la campaña no casa).
  - c dice «Sin datos de Walcu: falta el coste por cita…» y propone revisar.
  - b, c y f usan los leads de Walcu o, sin ellos, los de Meta («leads según Meta»).
- **c:** «coste por cita en la media» = como mucho la referencia de 2025 del mismo tipo (las de ambos, la de
  VN y VO juntas).
- `docs/criterios_marketing_meta.md`, `alertas/README.md` e `ingesta/README.md` dicen lo mismo.

## 5. Último mes en el panel

Marketing tiene un bloque nuevo, «Campañas del último mes: <mes>». Para cada campaña de Meta con gasto ese mes
muestra:

- gasto, leads de Walcu, leads según Meta, citas y ventas;
- coste por lead, por cita y por venta;
- la decisión de los criterios;
- el total del mes y cuántos leads de Facebook en Walcu no tienen campaña de Meta.

«—» quiere decir que la campaña no casa con Walcu. Las alertas se recalculan con `make alertas` (o con el cron
de las 09:15).

## Despliegue en el servidor

```bash
make deploy                                   # migración 0017
make meta-historico DESDE=2025-01-01          # relanzable; mira los tramos con make estado
# en el Mac:
#   scp -i ~/.ssh/cdf_hetzner ~/cdf_walcu/walcu_agregado_historico.csv cdf@<ip>:/opt/cdf/entrada/
make walcu-cargar FICHERO=/opt/cdf/entrada/walcu_agregado_historico.csv
make referencias-meta                         # tabla y lo que no casa → equivalencias si hace falta
SIN_TELEGRAM=1 make alertas                   # alertas recalculadas, sin enviar
```

Cada semana: `make walcu-agregar` en el Mac, `scp` y `make walcu-cargar`. Lo explica `infra/deploy.md` (7.12).

## Decisiones tomadas sin preguntar

1. **«Upsert» de Meta sin sobrescribir.** Usé la inserción versionada que ya existía: no duplica, pero
   conserva las versiones anteriores, por la regla «nada se sobrescribe».
2. **Recargar un mes en Walcu** añade filas, y la vista `_vigente` da la última carga de cada mes. Así no se
   borra nada.
3. **El fichero de Walcu entra por la entrada estándar**, así que sirve cualquier ruta del servidor. El nombre
   se guarda sin la ruta.
4. **Zonas que no suman** (10 filas de tu CSV) y más citas que leads: aviso, no rechazo.
5. **Se sube el CSV al bucket** (comprimido) antes de guardarlo, como los demás brutos. Si el bucket falla, no
   se carga.
6. **Valores de Walcu en configuración.** Los estados de venta, los motivos de número inexistente, los tipos
   y los alias de origen del script van en `config/walcu_export.yaml`, con valores supuestos («vendido»,
   «ganado»; «inexistente», «erróneo»; km0 → VO; sin tipo → VN). **Hay que comprobarlos con una exportación
   real**: el script muestra los valores que encuentra.
7. **Venta = negociación + estado de venta** (así no puede haber más ventas que citas).
8. **Web propia como origen**: `propio.web` en `config/concesionario.yaml`, para que el dominio no esté en el
   código.
9. **Reglas a, d y subir sin Walcu.** Antes quedaban inactivas sin `panel_leads_dia`. Ahora se evalúan con
   solo Meta y dicen «sin datos de Walcu»: el estado depende de cada campaña y de cada mes.
10. **Campaña que no casa con Walcu en un mes con carga**: se trata como «sin datos de Walcu» (con su motivo),
    no como cero citas. En cambio, una campaña que casa en algún mes tiene 0 en los meses sin leads.
11. **Regla f con Walcu mensual**: usa los leads de Meta por día, porque la carga mensual no da días.
12. **Señal 3 de la regla b** (leads por impresión frente a años anteriores): compara Meta con Meta mientras
    Walcu sea mensual.
13. **La referencia de 2025 también vale para la regla d** («coste por lead y por cita en la media»). Sin
    referencia, se usa la media de 90 días + 20 % de antes.
14. **Reparto del gasto de las campañas de ambos o sin regla** en VN y VO: por sus leads de Walcu; si no
    tienen, solo cuentan en «todo».
15. **Referencias: solo los meses con Meta y Walcu a la vez.** 2026 incluye el mes en curso: sus citas y
    ventas todavía pueden crecer, y el panel lo dice.
16. **Varias campañas de Meta con el mismo nombre normalizado**: se elige la que tiene gasto en esos meses; si
    hay más de una, va al informe para que se ponga una equivalencia con `id:`.
17. **Comerciales excluidos**: si el fichero por defecto no existe, no se excluye nada y el registro lo dice
    con «0»; si se indica uno con `--excluidos`/`EXCLUIDOS=` y no existe, no se escribe nada. Nombres sin
    mayúsculas ni tildes, líneas vacías y con `#` ignoradas.
18. **Presupuesto por teléfono** tampoco cuenta como venta (venta = cita con estado de venta).
19. **`make meta-historico` sin credenciales** termina con error (lo lanza alguien a mano). La carga diaria
    sigue anotando «sin_credenciales».

## Para revisar

- **Marcas.** Tu CSV de Walcu trae campañas de otras marcas del grupo (SK, AU, SEAT, CU…), y la cuenta de Meta
  puede traerlas también. No filtro por marca porque el encargo no lo pide, pero las referencias por tipo las
  incluyen. Si deben ser solo Volkswagen, se puede añadir un filtro por marca en `config/campanas_walcu.yaml`.
- **Atribución de AiVORA.** AiVORA tiene leads con campaña «(sin campaña)». Quedan fuera de las referencias
  de Meta, como pide el encargo.
