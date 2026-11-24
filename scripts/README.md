# scripts

Herramientas que se ejecutan en el Mac, no en el servidor.

## walcu_agregar.py

Agrega la exportación de leads de Walcu (`.xlsx` o `.csv`, tal como la saca el CRM) por mes, origen,
campaña y tipo, y escribe el CSV que carga `make walcu-cargar` en el servidor.

    make walcu-agregar ENTRADA=~/Descargas/leads.xlsx SALIDA=~/Descargas/walcu_agregado.csv
    scp ~/Descargas/walcu_agregado.csv cdf@<ip>:/opt/cdf/entrada/
    # en el servidor
    make walcu-cargar FICHERO=/opt/cdf/entrada/walcu_agregado.csv

- Lee solo las columnas de `config/walcu_export.yaml` (Origen, Detalle campaña, Tipo de vehículo
  solicitado, Fecha Lead, Id negociación creada, Resultado última actividad, Estado, Motivo fracaso,
  Código postal y, solo si hay comerciales excluidos, Vendedor); las demás no se leen. El `.xlsx` se lee
  con la biblioteca estándar, sin dependencias nuevas.
- **Comerciales excluidos**: fichero local opcional, por defecto `~/cdf_walcu/comerciales_excluidos.txt`
  (otro con `EXCLUIDOS=…` o `--excluidos`), un nombre por línea (sin mayúsculas ni tildes; `#` comenta). Sus
  leads se descartan antes de agregar. Lleva nombres: **no se versiona** (`.gitignore`), no se copia al
  repositorio ni al servidor, y el registro solo dice cuántos leads se han excluido. Si el de por defecto no
  existe, no se excluye nada; si se indica uno que no existe, no se escribe nada.
- Cita: el lead tiene negociación creada, salvo que su «Resultado última actividad» esté en
  `actividades_no_cita` (PRESUPUESTO POR TELÉFONO). Venta: cita y un estado de `estados_venta`. Número
  inexistente: el motivo de fracaso contiene uno de `motivos_num_inexistente`. Tipo VN/VO por `tipos`. Venta:
  el Estado contiene «Vendido»; número inexistente: «Número inexistente»; tipo: «Nuevo» → VN, «VO» y «Km0» →
  VO, vacío → VN (valores reales de la exportación).
- **Otras marcas**: los leads de campañas cuyo nombre empieza por un prefijo de `propio.prefijos_excluidos`
  (`config/concesionario.yaml`; como palabra entera) se descartan, se cuentan y se avisa.
- Zona por el código postal con la zona cercana de `config/concesionario.yaml` (nada de provincias en el
  código). El código postal no se escribe nunca.
- Orígenes: los de `config/walcu_export.yaml` y la web propia (`propio.web` de
  `config/concesionario.yaml`); los demás, «otros».
- Nada personal ni en el CSV ni en lo que muestra: solo recuentos, y los valores de Estado, Tipo y los
  motivos de fracaso que se repiten (al menos 5 veces), para ajustar la configuración. Antes de escribir, el
  CSV pasa la misma validación que en el servidor (`ingesta/walcu.py`).

Los estados, tipos y motivos son texto que configura cada concesionario en Walcu: la primera vez,
comprobar con lo que escribe el script y ajustar `config/walcu_export.yaml`.

## Cómo se prueba

`tests/test_walcu_meta_hist.py` (una exportación sintética en `.csv` y en `.xlsx` con columnas personales
que no deben aparecer en la salida ni en el registro):

    .venv/bin/python -m pytest tests/test_walcu_meta_hist.py
