# Decisiones sobre campañas de Meta Ads en un concesionario

Criterios de Cayetano para campañas de Facebook e Instagram (Meta Ads) de un concesionario Volkswagen, nuevos (VN) y ocasión (VO). Solo Meta: Google Ads, correo u otros canales quedan fuera.

Usa esta skill cuando haya que decidir si lanzar una campaña, juzgar cómo va una campaña activa, explicar por qué va mal, decidir si subir el presupuesto o redactar una recomendación para marketing. También sirve de referencia para las reglas automáticas de la plataforma (alertas/reglas.yaml y la recomendación por modelo): esas reglas deben decir lo mismo que esta skill.

## Datos que necesitas

Por campaña y día:

- **Meta**: gasto, impresiones o visualizaciones, clics, frecuencia, leads, texto del anuncio (copy) y respuestas del formulario de lead, incluido el código postal.
- **CRM (Walcu)**: leads, citas y ventas de la campaña (por utm_campaign), y el motivo de descarte que anota el comercial en cada lead.
- **Stock propio** del modelo: unidades y precio, por versión y acabado.
- **Mercado** (web de stock de la marca): unidades y precio mediano de otros concesionarios para el mismo modelo, versión y acabado, en la zona cercana.

Nunca uses ni guardes datos personales de los leads. El código postal sirve solo para contar qué proporción de leads es de fuera de zona.

Si falta un dato, dilo y no lo supongas. Una regla sin su dato no se aplica.

## Definiciones

- **Coste por lead** = gasto / leads.
- **Coste por cita** = gasto / citas.
- **Coste por venta** = gasto / ventas.
- **Tasa de lead a cita** = citas / leads.
- **Tasa de venta** = ventas / citas.
- **Tasa de clic** = clics / impresiones de Meta.
- **Tasa de clic a lead** = leads / clics.
- **Leads por impresión** = leads / impresiones de Meta (solo para comparar con campañas del mismo modelo y mes).
- **Zona cercana**: la provincia del concesionario y Andalucía occidental: **Sevilla, Huelva, Cádiz y Córdoba** (para el concesionario de Sevilla). Para otro concesionario, la zona sale de su configuración.
- **Leads de fuera de zona** = leads cuyo código postal no está en la zona cercana / leads con código postal.

Los umbrales en euros y en porcentaje valen igual para nuevos (VN) y para ocasión (VO).

## Antes de lanzar una campaña de modelo (regla e)

No se lanza una campaña de un modelo si no se cumplen las dos condiciones:

1. **Stock**: al menos **3 unidades** propias del modelo. Con 1 o 2 unidades, no.
2. **Precio**: nuestro precio por debajo de la **mediana de la competencia** en la zona cercana, para el mismo modelo, versión y acabado.

Prioriza los modelos con más stock y mejor posición de precio frente a la competencia cercana.

Si en la zona hay muy pocos coches comparables y la mediana no es fiable, dilo, y compara con la versión y acabado más cercanos o con una zona más amplia, indicándolo.

Si el modelo no está entre los que se capturan del mercado (por ejemplo, un vehículo comercial como el Caravelle), no hay datos de mercado: di que la regla e y la comparación con la competencia no se pueden aplicar, sin decidir lanzar o no lanzar por ellas. Por lo mismo, no se propone subir el presupuesto aunque se cumplan las demás condiciones.

## Cuándo revisar una campaña activa

La campaña se juzga **desde el segundo día**. Motivo: si llegan muchos leads de fuera de zona en VN, casi no hay opción de venta. Ese cliente busca precio fuera, pero compra en su zona enseñando la oferta que le han dado.

### a) Leads que no se convierten en citas

- **Señal**: tasa de lead a cita por debajo del **5 %**, o más del **30 %** de los leads de fuera de zona (esta se mira ya desde el segundo día).
- **Revisar**:
  1. La zona de localización y las audiencias de la campaña.
  2. Los motivos de descarte que anota el comercial en el CRM. Si dominan los de fuera de zona:
     - que el copy, el reel y la foto dejen clara la provincia del concesionario;
     - que el formulario tenga como preguntas obligatorias el código postal y si quiere ver el coche en la provincia del concesionario.

### b) El anuncio no atrae o no convence

Tres señales; cualquiera de ellas la dispara:

1. **Tasa de clic** por debajo del **1 %**: el anuncio no atrae.
   - **Revisar**: la creatividad (reel, foto) y el mensaje.
2. **Tasa de clic a lead** por debajo del **3 %**: atrae pero no convence.
   - **Revisar**: la llamada a la acción, la oferta y el formulario.
3. **Leads por impresión** claramente por debajo de otras campañas del mismo modelo y del mismo mes del año.
   - **Revisar**: la creatividad, el mensaje, la llamada a la acción y la oferta.

Volumen mínimo: la señal 1 solo salta con al menos **1.000 impresiones**, y la señal 2 con al menos **50 clics**. Por debajo, la cifra se da igualmente, con su volumen, pero no salta.

### c) Lead caro

- **Señal**: coste por lead por encima de **10 €**.
- **Decidir por el coste por cita**:
  - si el coste por cita está en la media, **se continúa** la campaña;
  - si no, **se para** y se revisan audiencias y creatividad.

### d) Leads y citas en precio, pero pocas ventas

- **Señal**: tasa de venta por debajo del **33 %**, con el coste por lead y por cita en su media.
- **Revisar**:
  1. El motivo de descarte que anota el comercial de exposición.
  2. El mercado: cuántos vehículos similares (mismo modelo, versión y acabado) tienen otros concesionarios en la zona cercana y a qué precio mediano. Puede ser un vehículo con competidores fuertes en la zona.
  3. El desempeño del comercial.

### f) Cansancio del anuncio

- **Señal**: las dos a la vez:
  1. la velocidad de entrada de leads cae un **30 %** o más: leads por día de los últimos 3 días frente a la media de leads por día de los 7 días anteriores;
  2. el coste por lead sube por encima de **10 €**.
- Una frecuencia que sube a la vez lo confirma.
- **Revisar**: renovar la creatividad (reel, foto, copy) antes de seguir invirtiendo.

## Cuándo subir el presupuesto

Se sube el presupuesto de una campaña solo si se cumplen **todos** estos criterios a la vez, y ninguna regla de revisión (a, b, c, d, f) está activa:

| Criterio | Umbral |
| --- | --- |
| Coste por lead | por debajo de **10 €** |
| Coste por cita | por debajo de **100 €** |
| Tasa de lead a cita | por encima del **5 %** |
| Tasa de venta | por encima del **33 %** |
| Coste por venta | por debajo de **400 €** |

Y además, el modelo cumple la regla e (stock de al menos 3 unidades y precio por debajo de la mediana de la competencia cercana). No tiene sentido empujar un modelo que se queda sin stock.

## Cautelas al aplicar los umbrales

- Las tasas necesitan volumen. Con pocos leads o citas, una tasa baja o alta puede ser casualidad. Da siempre el número de leads, citas y ventas junto a la tasa, y si el volumen es pequeño, dilo antes de recomendar parar o subir.
- Una cita tarda días en llegar después del lead, y una venta, semanas. El día 2 se puede juzgar sobre todo la zona de los leads, el coste por lead, la tasa de clic y la tasa de clic a lead; la tasa de lead a cita, después de unos días, y la tasa de venta y el coste por venta, más adelante. Por eso subir presupuesto exige una campaña con ventas ya registradas.
- Compara cada campaña con campañas del mismo modelo y del mismo mes cuando haya historial. Si no lo hay, dilo y usa la media reciente del modelo.
- La causa de una mala tasa no siempre está en la campaña: puede estar en el stock, en el precio o en la atención comercial. Señálalo cuando los datos lo apunten.

## Cómo responder

Para cada campaña revisada:

1. **Decisión** en una palabra: continuar, revisar, parar o subir presupuesto. Para un modelo sin campaña: lanzar o no lanzar.
2. **Regla** que la dispara (a, b, c, d, e, f o subir presupuesto) y las cifras exactas frente a su umbral. Por ejemplo: «Lead a cita: 3 de 84 = 3,6 % (umbral 5 %)».
3. **Qué revisar**, en el orden de la regla.
4. **Lo que falta** para estar seguro, si falta algo.

Son recomendaciones para el equipo de marketing; decide marketing.
