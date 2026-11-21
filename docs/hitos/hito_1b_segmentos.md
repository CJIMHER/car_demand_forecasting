Rama nueva hito-1b-segmentos desde main, sin push y sin conectarte al servidor. Guarda este encargo tal cual en docs/hitos/hito_1b_segmentos.md.

Objetivo: ampliar la vista Mercado con el detalle de cada modelo por segmentos, con su precio mediano, en VN y en VO.

Datos reales comprobados en el servidor (21/11):
- VO (3.076 anuncios) y VN (4.750): version, fuel, power_cv y model_year al 100 %; engine_capacity_cc al 96 % en VO. En VO, first_registration_date y mileage_km al 100 %.
- El color exterior viene vacío desde la web de VW ({"out": {}}) en todos los anuncios: fuera de este encargo. Deja preparado el sitio (columna y desglose) para añadirlo cuando haya dato.
- El acabado va dentro del texto de la versión, escrito distinto en VO y VN. Ejemplos reales:
  VO: «Volkswagen T-Roc R-Line 1.5 eTSI 110 kW (150 CV) DSG», «Volkswagen T-Roc ``Más`` 1.5 TSI 110 kW (150 CV) DSG», «Volkswagen T-Roc Dark Line 1.0 TSI 85 kW (115 CV)»
  VN: «T-Roc R-Line  1.5 eTSI 110 kW (150 CV)  Automático DSG 7 vel.», «T-Roc "Más"  1.5 eTSI 85 kW (116 CV)  Automático DSG 7 vel.», «T-Roc T-Roc  1.5 eTSI 85 kW (116 CV) …» (este último es el acabado básico).

1. Normalización (analitica/, con catálogo en config/versiones.yaml)
- Acabado: catálogo por modelo de los acabados conocidos (R-Line, Life, Style, Más, Dark Line, Match, básico, GTI, GTD, R…), con sinónimos y comillas o espacios normalizados. Un texto sin acabado reconocido va a «sin clasificar», nunca se adivina.
- Motor: combustible + potencia en CV + tecnología (TSI, eTSI, TDI, TSI Hybrid, eléctrico…), y cambio (manual o automático) cuando venga. Los CV salen del campo power_cv, no del texto.
- Comprueba si stock_externo_snapshot ya tiene versión, combustible, CV, matriculación y km. Si falta alguno, añádelo con una migración a partir de los datos ya cargados, sin tocar scrapers/ ni las bases SQLite.
- Informe de cobertura en «Estado de las fuentes»: % de anuncios con acabado reconocido por modelo y tipo, y los 20 textos sin clasificar más frecuentes, para ir completando el catálogo.

2. Segmentos (precálculo nocturno, tabla panel_mercado_segmento, migración nueva)
- VN: modelo → acabado → motor.
- VO: modelo → acabado → motor, y además antigüedad y km:
  - antigüedad desde la primera matriculación hasta el día de la captura, en años cumplidos: «0–2 años» (0, 1 o 2), «3–4 años», «más de 4 años» (5 o más);
  - km: 0–20.000, 20.001–60.000, 60.001–100.000 y más de 100.000.
  Los cortes van en config/concesionario.yaml, no en el código.
- No calcules el cruce de todas las dimensiones a la vez: solo los niveles que se muestran (modelo; modelo+acabado; modelo+acabado+motor; y en VO, modelo+antigüedad×km y modelo+acabado+antigüedad×km).
- Por segmento: n, anuncios propios, precio p25, p50 y p75 (solo anuncios con precio), y días publicado mediano con la misma regla «≥ N» de la vista.
- Por zona: provincia propia, comunidad y España, como el resto de Mercado.

3. Vista
- En la tabla por modelo de Mercado, el nombre del modelo enlaza a su detalle: /mercado/modelo/<modelo>?tipo=…&zona=…, con el mismo selector de zona y tipo.
- Bloque «Por acabado y motor»: acabado como fila desplegable con sus motores dentro; columnas anuncios, nuestros, precio mediano con su rango p25–p75 y días publicado.
- Solo en VO, bloque «Precio por antigüedad y km»: matriz 3 × 4 con la mediana y el n en cada casilla, con filtro de acabado (Todos por defecto).
- Reglas de muestra: con n < 5, la cifra va atenuada con su n; con n < 3, se muestra «—» y «pocos anuncios». En la provincia casi todo tendrá n pequeño: si la zona elegida deja la mayoría de casillas en «—», muestra una nota que proponga ver España.
- Nota fija: «Precio publicado, no precio de venta. Los segmentos con pocos anuncios no son representativos».
- Móvil a 390 px sin desplazamiento horizontal; la matriz puede pasar a una lista por antigüedad.
- Regla Replicable: nada de «Volkswagen», «AVISA» ni «Sevilla» escrito en el código o las plantillas.

4. Pruebas, sin red
- Normalización con los textos reales de arriba (VO y VN dan el mismo acabado y motor), un texto desconocido va a «sin clasificar» y el informe de cobertura lo cuenta.
- Cortes de antigüedad en los bordes (2 años y 364 días; 3 años justos; 5 años justos) y de km (20.000 y 20.001).
- Percentiles y n de cada segmento cuadran con SQL directo; n < 3 nunca muestra precio.
- Chrome a 1440 y 390 px sin desbordes; capturas del detalle de un modelo en VN y en VO en docs/capturas/.

Termina con lo probado y lo no probado, las decisiones tomadas sin preguntar, la entrada en CHANGELOG.md y los pasos para Cayetano.
