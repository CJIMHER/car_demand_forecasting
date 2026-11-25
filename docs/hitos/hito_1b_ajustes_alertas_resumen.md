# Hito 1b · Ajustes: caída de ventas, reparto de alertas por rol y exclusiones de las negociaciones

Rama `hito-1b-ajustes-alertas` desde `origin/main` (`8a4ed67`). Sin push, sin conectarse al servidor. Un commit por
ajuste.

## 1. Campaña general: caída de ventas con 5 coches y sin las 2 últimas semanas

`config/campana_general.yaml`: `ventas.diferencia_minima: 5` y `ventas.semanas_recientes_excluidas: 2`. Las dos
ventanas de 8 semanas terminan 2 semanas antes de la última semana completa (Walcu cierra las ventas con retraso).
Hoy (25-11-2026) la ventana actual va del 14-9 al 2-11 (semanas) y la anterior, del 20-7 al 7-9.

Semanas en que habría saltado la regla con el histórico (`~/cdf_walcu/walcu_ventas_semana_historico.csv`), evaluando
cada lunes en que las dos ventanas ya tienen datos:

| Configuración | VN | VO |
| --- | --- | --- |
| Antes: diferencia 0, sin excluir semanas (76 lunes, del 16-6-2025 al 23-11-2026) | 28 (37 %) | 27 (36 %) |
| Diferencia 5, sin excluir semanas (76 lunes) | 22 (29 %) | 14 (18 %) |
| Diferencia 0, excluyendo 2 semanas (74 lunes, del 30-6-2025 al 23-11-2026) | 26 (35 %) | 26 (35 %) |
| **Ahora: diferencia 5, excluyendo 2 semanas (74 lunes)** | **20 (27 %)** | **14 (19 %)** |

Con el histórico, hoy saltaría para VN (5 ventas frente a 17, −71 %) y no para VO (9 frente a 8).

En el histórico ya cargado las semanas recientes están completas, así que excluirlas apenas cambia la frecuencia:
lo que evita es la caída falsa de cada lunes con los datos al día, cuando las 2 últimas semanas aún no tienen todas
sus ventas cerradas.

## 2. Reparto de las alertas por rol

**Lo revisado**: «nueva» y «ya enviada» se deciden por destinatario en los dos sitios.
- `alertas/motor.py` (`filas_de`): cada alerta es una fila por regla, rol y clave; es «nueva» si ese rol no la
  tenía abierta en la evaluación anterior (`_anteriores`, por `(rol, clave)`).
- `alertas/envio.py` (`pendientes`): para cada rol, se envía lo abierto que ese rol no ha recibido desde que se le
  abrió (`alertas_salida` del mismo rol). Lo que haya recibido el técnico no cuenta para marketing ni al revés.

`tests/test_alertas_por_rol.py` lo reproduce: ayer las reglas de campaña solo iban a marketing y se le envió la
campaña que saltaba; hoy van también al técnico y el cansancio de «VW [VN][2026] Taigo noviembre» es nuevo. Marketing
recibe el cansancio de Taigo (texto limpio) y no repite lo que ya tenía; el técnico recibe todas, nuevas para él, con
el detalle. Un fallo de envío a uno no afecta al otro, en los dos sentidos.

**Lo del 25-11** encaja con esto si el envío de las 09:15 corrió todavía con las reglas viejas (campañas solo para
marketing): marketing recibió entonces el cansancio de Taigo, y la ejecución a mano de después, con las reglas
nuevas, no se lo repite; el técnico, que no tenía ninguna, las recibe todas por primera vez. También lo reproduce
una prueba. No he cambiado la lógica del reparto. Para confirmarlo sin conectarme, en el servidor:

```bash
make psql
SELECT registrado_at, rol, destino, estado, left(texto, 120) FROM alertas_salida
 WHERE fecha = '2026-11-25' AND que = 'alertas' ORDER BY id;
SELECT rol, regla, clave, estado, abierta_desde FROM alertas
 WHERE fecha = '2026-11-25' AND regla LIKE 'meta_%' ORDER BY clave, regla, rol;
```

Si el primer envío a marketing de ese día ya lleva «Taigo noviembre», es lo explicado. **Cambio**: con
`SIN_TELEGRAM=1 make alertas`, cada rol dice ahora qué alertas abiertas no se repiten porque ya se le enviaron, y
cuándo («--- marketing: 3 abiertas que no se repiten (ya enviadas a marketing): meta_f … el 25/11»).

## 3. Exclusiones de la exportación de negociaciones

`config/walcu_export.yaml` (negociaciones): fuera, por sede o por equipo (contiene, sin mayúsculas ni tildes), Covey
(cualquier sede o equipo con «Covey», incluido Covey Autocasión), Cupra Córdoba, SEAT Córdoba, Avisa Škoda Badajoz y
Cartuja Motor. «Avisa Audi» no se excluye: recibe leads de VW del reparto general. Una prueba lo comprueba con sedes
y equipos escritos de varias formas (`test_exclusiones_de_las_negociaciones_de_esta_instancia`).

**Comprobación contra el histórico: pendiente.** La exportación está en `~/Downloads/negociaciones VW.numbers`,
formato de Numbers, que el script no lee (.xlsx o .csv). No he podido exportarla desde aquí (Numbers no respondió a
la automatización; no ha quedado ninguna copia). Para comprobarlo, en Numbers: Archivo › Exportar a › Excel, y en el
Mac:

```bash
.venv/bin/python scripts/walcu_agregar.py --negociaciones ~/Downloads/negociaciones_VW.xlsx --ventas /tmp/ventas_neg.csv
cmp /tmp/ventas_neg.csv ~/cdf_walcu/walcu_ventas_semana_historico.csv && echo "Idéntico al histórico (269 ventas)"
```

El script escribe las filas en el mismo orden y formato que el histórico, así que `cmp` basta. Los comerciales
excluidos se leen solos de `~/cdf_walcu/comerciales_excluidos.txt`. El registro que imprime dice cuántas ventas se
excluyen por sede o empresa, por comercial y por tipo desconocido; si `cmp` encuentra diferencias,
`diff /tmp/ventas_neg.csv ~/cdf_walcu/walcu_ventas_semana_historico.csv` dice en qué semanas y modelos.
