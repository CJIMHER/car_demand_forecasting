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
  solicitado, Fecha Lead, Id negociación creada, Estado, Motivo fracaso y Código postal); las demás no se
  leen. El `.xlsx` se lee con la biblioteca estándar, sin dependencias nuevas.
- Cita: el lead tiene negociación. Venta: con negociación y un estado de `estados_venta`. Número
  inexistente: el motivo de fracaso contiene uno de `motivos_num_inexistente`. Tipo VN/VO por `tipos`.
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
