# Plataforma VN de stock y campañas — Documento de diseño

Nov 7, 2026 · @cayetano

La plataforma predice cuándo saldrá del stock cada vehículo nuevo de Volkswagen y Audi y avisa qué vehículo necesita campaña y en qué momento. Reutiliza el motor de supervivencia de KROL y arranca con un panel diario para dirección general.

## Objetivo

La plataforma responde cada día a una pregunta: **qué vehículos de nuestro stock VN van a tardar más de lo deseado en salir y cuáles merecen campaña ahora**. Cubre la gama actual de Volkswagen y Audi nuevos.

| Usuario | Decisión que toma | Frecuencia |
| --- | --- | --- |
| Director general | Ver desvíos de stock, demanda y coste frente a lo previsto | Diaria |
| Jefes de ventas | Priorizar vehículos envejecidos y ajustar precio o argumentario | Diaria |
| Marketing | Qué vehículo empujar, en qué campaña y cuándo | Diaria / semanal |

Alcance de la primera versión:

- **Incluido**: stock interno y del mercado, curvas de salida, señales de demanda, seguimiento de campañas de Meta, alertas por desvío y panel diario.
- **Fuera, por ahora**: recomendar cuánto presupuesto invertir. Desde ya se guardan inversión y resultados por campaña; la predicción del presupuesto llega cuando haya historial suficiente.

La predicción de ventas es bayesiana: da la distribución de ventas esperadas por modelo, no un único número. Cuando los datos no bastan, el sistema muestra **DATOS INSUFICIENTES** en lugar de una cifra.

## Arquitectura

[embedded content: arquitectura · 6 fuentes, VPS, KROL y salidas]

Todas las fuentes entran por la ingesta diaria del VPS y se guardan en Postgres. KROL aporta el módulo de supervivencia, y el motor de señales alimenta el panel y las alertas. Google Trends sigue descargándose desde Colab y escribe en la misma base. El proyecto corre en un servidor propio de Hetzner (proyecto cdf), sin compartir máquina con ningún otro sistema.

## Fuentes de datos

Las señales propias (visitas y leads) son las más cercanas a la compra y van por delante; Google Trends y las variables macro dan el contexto del mercado y su aporte se mide en nuestros datos.

| Bloque | Fuente | Conexión | Frecuencia | Estado |
| --- | --- | --- | --- | --- |
| Stock interno, leads, citas, ventas | Walcu (CRM) | API | Diaria | Credenciales el martes |
| Stock externo de la red VW | Web pública de stock de Volkswagen | Scraper de VO adaptado a VN (Astra) | Diaria | Lo adapta Cayetano |
| Stock externo de la red Audi | Web de stock de Audi | Scraper | Diaria | Fase posterior (de momento solo VW) |
| Visitas a la ficha de cada vehículo | Google Analytics 4 | Data API de GA4 (Google Cloud) | Diaria | Pendiente |
| Inversión y resultados de campañas | Meta Ads | Marketing API (Insights) | Diaria | Pendiente |
| Atención por modelo frente a sus competidores (cuota de atención relativa: señal direccional admitida; en absoluto, solo contexto) | Google Trends, categoría Autos & Vehicles (la subcategoría de compra tiene demasiados ceros); Wikipedia como contraste | Colab con escala re-anclada; API oficial solicitada | Mensual | Script hecho |
| Intención de búsqueda por modelo | Google Ads Keyword Planner: volúmenes absolutos de consultas con intención (precio, oferta, km0, renting) | API de Google Ads con la cuenta de campañas | Mensual | Candidata; pendiente de validar |
| Demanda propia avanzada | Cartera de pedidos, financiaciones, tasaciones de usado, pruebas de conducción, presupuestos; impresiones de Search Console | Walcu / VW Financial Services / formulario web / Search Console | Diaria | Confirmar qué campos da la API de Walcu |
| Expectativas del consumidor | CIS: componente de expectativas del ICC (agosto con último dato disponible). Probada en nov-2026: sin aporte a 4 meses; contexto | Descarga | Mensual | Pendiente |
| Tipos de interés al consumo | Banco de España, tabla 19.4: tipo de nuevas operaciones de crédito al consumo a 1–5 años. Probado en nov-2026: sin aporte a 4 meses; contexto | Descarga | Mensual | Pendiente |
| Motor de supervivencia y precios | KROL (DigitalOcean) | Código reutilizado | — | Operativo en VO |
| Stock VO interno y externo, episodios y ventas de VO | API de Walcu: el VO propio llega por la misma API que el VN (KROL no es fuente, mezcla varias marcas) | Lectura directa en solo lectura (default_transaction_read_only), misma clave que el análisis R9 | Diaria | Pendiente de la reunión con Walcu del 16 de noviembre |

Google Trends entra en **relativo al segmento**: la variación interanual de 3 meses del interés por cada modelo VW menos la media de la de sus competidores directos. En noviembre de 2026 esa medida, con la categoría Autos & Vehicles, superó el protocolo de admisión sobre las matriculaciones nacionales 2018–2025 (mejora del error del trimestre siguiente +0,8%, p de permutación 0,005 tras Holm, coeficiente positivo en todas las estimaciones). En absoluto no anticipa nada: lo dominan los shocks comunes. Las variables macro siguieron el planteamiento de Fantazzini y Toktamysova (2015); probadas con el mismo protocolo (tipo de crédito al consumo del BdE y expectativas del ICC del CIS), no mejoran la predicción a 4 meses en 2018–2026, una muestra con un solo ciclo de tipos y dos shocks de oferta. Quedan como contexto.

Cuando un vehículo desaparece de la web de stock se registra como **salida**, no como venta: puede ser una venta, una reserva, un traslado o un coche de demostración. Para el stock interno, Walcu confirma la venta real.

## Modelo de datos

Una base Postgres con un catálogo común de vehículos y tablas de hechos por fuente. La clave que une todo es **modelo → versión (motor + acabado) → vehículo**, normalizada en el catálogo para que Walcu, la web de stock, GA4 y Meta hablen del mismo coche.

| Tabla | Grano | Campos clave | Origen |
| --- | --- | --- | --- |
| `catalogo_versiones` | Versión | marca, modelo, motor, acabado, alias por fuente | Manual + mapeo |
| `stock_snapshot` | Vehículo × día | fuente (interno/externo), id anuncio o VIN, concesionario, precio, km, días en stock | Walcu, scraper |
| `stock_eventos` | Vehículo | fecha de entrada, fecha de salida, motivo (venta, retirada, traslado), censurado sí/no | Derivada de los snapshots |
| `precio_historial` | Vehículo × cambio | fecha, precio anterior, precio nuevo | Derivada |
| `ga4_visitas` | Ficha × día | usuarios, sesiones, eventos de contacto | GA4 |
| `leads` | Lead (agregado, sin datos personales) | fecha, modelo, origen, campaña, estado: cita, venta | Walcu |
| `meta_campanas` | Campaña × día | gasto, impresiones, clics, leads; modelo al que apunta | Meta |
| `gt_series` | Modelo/marca × mes | índice re-anclado, YoY 1/3/6, vintage de extracción | Google Trends |
| `macro` | Mes | confianza del consumidor, tipo de interés al consumo | Descarga |
| `predicciones` | Versión × fecha de cálculo × horizonte | percentiles de días hasta salida y de ventas esperadas | Modelos |
| `alertas` | Alerta | tipo, vehículo o modelo, umbral, destinatario, estado | Motor de señales |

Dos reglas de diseño: los snapshots diarios no se borran nunca (son la materia prima de las curvas de supervivencia) y en `leads` no se guardan nombres, teléfonos ni correos, solo el agregado necesario para el RGPD.

## Analítica

Tres capas, de lo más robusto a lo más ambicioso. Cada capa se valida con origen móvil y frente a una referencia simple antes de mostrarse en el panel.

### 1. Supervivencia del stock

- **Kaplan-Meier** por modelo y versión, para stock interno y externo por separado. Los coches que siguen en stock entran como observaciones censuradas: por eso no se usa la media simple de días.
- **Modelo de Cox** (o un AFT si no se cumplen los riesgos proporcionales) para explicar la velocidad de salida de cada vehículo. Variables:
  - diferencia de precio con el mercado para la misma versión;
  - unidades de esa versión en el stock externo (variable que cambia con el tiempo);
  - visitas GA4 y leads de los últimos 7 y 28 días;
  - Google Trends YoY y contexto macro;
  - campaña activa sí/no.
- Este módulo parte del código de KROL (VO), adaptado a VN.

**Lecciones del traspaso de KROL** (análisis R9, R9b y R9c sobre unos 660 episodios de VO; el código de referencia va a `vendor/krol/` en el repositorio). Son reglas del módulo, no sugerencias:

- **Desaparecer no es vender.** Solo cuenta como venta la confirmada en Walcu; una baja del stock externo se censura o se trata como riesgo competidor. Las ventas detectadas en bloque tras un fallo de la ingesta se marcan y se excluyen de las curvas.
- **Entrada tardía.** Los vehículos que ya estaban en stock cuando empieza el seguimiento entran en riesgo desde `observed_from`, no desde su publicación (`entry` en lifelines). Sin esto la curva sale optimista.
- **Sesgo de supervivencia.** La mediana de los vendidos quedó unos 20 días por debajo de la de Kaplan-Meier en VO. El stock sin vender entra siempre como censurado.
- **RMST a 90 días en lugar de la mediana.** La mediana de Kaplan-Meier a menudo no existe (exigirla dejó fuera al 73% de los episodios en VO). El tiempo medio restringido a un horizonte siempre existe.
- **Covariables as-of.** Toda covariable derivada del histórico se calcula solo con lo conocido en cada fecha y como covariable que cambia en el tiempo (tramos semanales, `CoxTimeVaryingFitter`). En VO, una covariable con fuga pasó de HR 0,29 (p = 0,004) a HR 0,97 (p = 0,94) al recalcularla as-of.
- **Mínimos por celda y jerarquía.** 10 episodios y 3 ventas por celda; si no llega, se sube al padre: versión → modelo → carrocería → todo el stock. Cada curva muestra su número de episodios, de ventas y su intervalo.
- **Posición de precio frente a comparables** fue el único predictor que se sostuvo en VO, y es asociación, no efecto causal. En VN, el comparable es la mediana del stock externo de la misma versión. No se usa ese coeficiente para fijar precios sin un experimento.
- **Potencia.** Con unas 100 ventas solo se estiman uno o dos coeficientes. Validación temporal (pasado → futuro), nunca partición aleatoria de filas coche-día.
- **Campañas.** La curva elige candidatos; el efecto de una campaña solo se mide con un grupo de control asignado al azar entre los vehículos elegibles. Sin control, cualquier campaña parece funcionar.

La probabilidad que se muestra al jefe de ventas es la condicional: P(salida en los próximos h días | lleva a días) = 1 − S(a+h)/S(a). Las ventas esperadas a h días son la suma de esas probabilidades sobre el stock, y el stock previsto es el actual menos esas salidas más las entradas previstas.

### 2. Señales de demanda

La predicción se construye de abajo arriba, desde el embudo propio. Las señales externas solo modulan la parte alta del embudo y entran por examen.

| Nivel | Señal | Papel |
| --- | --- | --- |
| A. Compromiso | Cartera de pedidos pendientes de entrega; solicitudes de financiación; tasaciones de usado solicitadas; pruebas de conducción y presupuestos | Núcleo de la predicción a 1–3 meses |
| B. Consideración propia | Visitas a la ficha de cada modelo (GA4); impresiones en Search Console por consultas con el modelo; vistas y contactos de los anuncios en portales | Predicción a 1–2 meses y ratio de captura |
| C. Intención externa | Volúmenes de Google Ads Keyword Planner para consultas con intención ("precio", "oferta", "km0", "renting"); valores residuales del VO calculados con los precios de Walcu y del scraper; calendario de incentivos y de ciclo de modelo | Candidatas: entran si superan el protocolo de validación |
| D. Atención | Google Trends; Wikipedia; YouTube | En absoluto: contexto y ratio de captura, no predicen. En relativo al segmento (Google Trends, cat. 47): señal direccional admitida de la cuota frente a los rivales. Wikipedia: descartada en absoluto y en relativo |
| E. Régimen | Confianza del consumidor, paro provincial, Euríbor, combustible | Variables de régimen. El tipo de crédito al consumo y las expectativas del ICC no mejoran la predicción a 4 meses en 2018–2026; se muestran como contexto |

- **Modelo de embudo**: visitas → leads → pruebas → pedidos → entregas, con tasa de conversión y retardo por etapa estimados con el histórico de Walcu. La cartera de pedidos fija el corto plazo; las etapas anteriores dan los meses siguientes con su incertidumbre.
- **Objetivo propio, no nacional**: ventas y pedidos del concesionario, separando particular, empresa y flota. Las matriculaciones nacionales incluyen renting y rent-a-car, con un ciclo ajeno a la intención del comprador.
- **Protocolo de admisión** para cada señal externa: hipótesis y retardo prerregistrados, placebos en otros retardos, referencia de surrogates o permutaciones y Clark-West fuera de muestra. Aplicado en noviembre de 2026 a ocho experimentos (carpeta `test_senales_demanda` en `car_demand_forecasting`).
- **Google Trends absoluto** (matriculaciones nacionales 2018–2025, 37–41 modelos; YoY en 1/3/6 meses, prewhitening, confirmación prerregistrada del retardo 4): correlación alta solo en el mismo mes y debida al COVID; ningún retardo supera a las series artificiales; el retardo 4 no se confirma (−1%).
- **Wikipedia** (es y en, 2016–2026): no admitida por ninguna vía. La hipótesis del retardo 9 en en.wikipedia, surgida de 2023–2025, quedó refutada fuera de muestra en enero–abril de 2026 (error agregado −8%).
- **Macro** (mercado total, 2023–2026, con días laborables como control): el tipo de crédito al consumo sale con el signo contrario (en 2022–23 subieron los tipos mientras las matriculaciones se recuperaban tras la escasez de chips) y las expectativas del ICC tienen el signo correcto pero no mejoran el error (−0,7%, Holm 0,38).
- **Atención relativa a los competidores** (prerregistro propio; segmentos fijados a priori): restar la media del segmento elimina los shocks comunes. Google Trends cat. 47 mejora la predicción de la cuota de matriculaciones del trimestre siguiente un 0,8% (p de permutación 0,005, Holm 0,02; coeficiente positivo en el 100% de las estimaciones; placebos que decaen de forma ordenada del retardo 3 al 9). Efecto pequeño y real: sirve como señal direccional para marketing, no para predecir unidades. Cautelas: la correlación plana entre los retardos 0 y 6 apunta a un componente común de ciclo de vida (lanzamientos y renovaciones), y la confirmación con 2026 debe registrarse en OSF antes de descargar los datos.

### 2.1 Competidores y cuota de atención

La señal admitida compara cada modelo VW con un conjunto fijo de rivales. Los conjuntos se fijan por producto, no por lo que mejor salga en los datos, y se cierran con el jefe de ventas antes de la primera extracción. El contraste de noviembre usó segmentos amplios; estos conjuntos, más estrechos, se revalidan con 2026.

| Modelo VW | Segmento | Competidores directos (cerrados con el jefe de ventas el 14-11-2026) |
| --- | --- | --- |
| Polo | Utilitario B | Seat Ibiza, Renault Clio, Peugeot 208, Opel Corsa, Toyota Yaris, Hyundai i20, Dacia Sandero, Skoda Fabia, Citroën C3, Renault 5 |
| T-Cross y Taigo | SUV-B | Seat Arona, Renault Captur, Peugeot 2008, Ford Puma, Nissan Juke, Hyundai Kona, Kia Stonic, Toyota Yaris Cross, Citroën C3 Aircross, MG ZS |
| Golf | Compacto C | Seat León, Toyota Corolla, Ford Focus, Kia Ceed, Hyundai i30, Renault Mégane, Peugeot 308, Citroën C4, Opel Astra |
| T-Roc | SUV-C compacto | Cupra Formentor, Seat Ateca, Nissan Qashqai, Skoda Karoq, Toyota C-HR, Mazda CX-30, Peugeot 3008, Hyundai Tucson, Kia Sportage |
| Tiguan | SUV-C | Hyundai Tucson, Kia Sportage, Nissan Qashqai, Seat Ateca, Toyota RAV4, Ford Kuga, Mazda CX-5, Skoda Karoq, Renault Austral |
| Tayron | SUV-D de 7 plazas | Skoda Kodiaq, Kia Sorento, Hyundai Santa Fe, Nissan X-Trail, Peugeot 5008, Toyota Highlander |
| ID.3 | Eléctrico compacto | Renault Mégane E-Tech, Cupra Born, MG4, BYD Dolphin, Kia EV3 |
| ID.4 e ID.5 | SUV eléctrico | Tesla Model Y, Skoda Enyaq, Renault Scénic E-Tech, Toyota bZ4X, Kia EV5 |
| ID.7 | Berlina eléctrica | Tesla Model 3, BYD Seal, Polestar 2 (conjunto escaso: solo descriptivo) |
| Passat | Berlina y familiar D | Skoda Superb, Peugeot 508, Mazda 6 (conjunto escaso: solo descriptivo) |
| Touareg, Caddy, Multivan, Transporter | — | Fuera de la primera versión (volumen bajo o comercial) |

Configuración de la extracción:

- **Fuente**: Google Trends, categoría 47 (Autos & Vehicles), geo España, mensual. Andalucía (ES-AN) se extrae también, pero solo se muestra como descriptivo hasta validarla: la señal se validó con matriculaciones nacionales.
- **Consulta**: «marca modelo» en minúsculas (por ejemplo «volkswagen t-cross»), sin topic, una por vehículo. Con la API oficial la escala es consistente (1.800 días por llamada). Mientras llega, descargas en lotes de cinco con un ancla común («volkswagen golf»), tres repeticiones promediadas y re-anclaje de escala, como en el script de Colab.
- **Transformación**: YoY3 = log(1 + media de 3 meses) − log(1 + la misma media hace 12 meses). Relativo = YoY3 del modelo − media del YoY3 de su conjunto (mínimo dos competidores con dato). Cuota de atención = interés del modelo dividido por la suma del conjunto, dentro del mismo lote.
- **Almacenamiento**: `gt_raw` (mes, consulta, geo, lote, valor) y `gt_relativo` (mes, modelo VW, YoY3, YoY3 del segmento, relativo, cuota). Los valores brutos no se reescriben: cada descarga es un snapshot.
- **Uso en el producto**: indicador en el panel de marketing y alerta «Presión de un competidor». Entra en el modelo bayesiano solo si la confirmación de 2026 la supera.
- **Confirmación de 2026**: prerregistro en OSF antes de descargar los datos; métrica principal la precisión direccional del signo relativo a tres meses frente al nulo de permutación, y el error como secundaria.

### 3. Predicción bayesiana

- **Ventas esperadas** por modelo a 30, 60 y 90 días con un modelo jerárquico de conteo (binomial negativa). Las versiones comparten información con su modelo y los modelos con su marca, porque los volúmenes por versión son bajos.
- **Probabilidad de que cada vehículo salga antes de un umbral** (por ejemplo, 90 días), combinando la supervivencia con la demanda prevista.
- **Decisión con pérdida asimétrica**: se recomienda campaña cuando la pérdida esperada de no actuar supera al coste esperado de la campaña. La pérdida se mide con la tasa de depreciación implícita del vehículo (caída de su precio frente al precio inicial, por día en stock) multiplicada por los días extra previstos; no hace falta el coste financiero.
- **Presupuesto**: se guardan los datos desde el primer día. Para estimar el efecto de la inversión harán falta curvas de respuesta con rendimientos decrecientes y, a ser posible, semanas con presupuesto variado a propósito. Sin eso, el efecto estimado estaría confundido con la demanda.

### 4. Relación entre VN y VO

La plataforma cubre los dos stocks. El VO propio llega por la API de Walcu, igual que el VN (KROL no es fuente: mezcla varias marcas), y se guarda en el mismo esquema con la columna `tipo_stock` (VN o VO), de modo que el panel, las alertas y las curvas usan las mismas tablas. Las curvas de supervivencia se estiman por separado (el VO tiene año y kilómetros; el VN, versión), pero comparten jerarquía y validación. Tres relaciones que se miden desde el primer día y entran en los modelos solo si pasan la validación con origen móvil:

- **Retoma como demanda adelantada de VN.** Tasaciones solicitadas y entradas de VO por retoma, por modelo del VN que se compra. Circuito completo: entrega de VN → entrada de VO → venta de VO. La tasa de retoma por modelo alimenta las entradas previstas del stock VO.
- **Sustitución dentro del modelo.** Brecha de precio entre el VN y el VO joven (km 0 y seminuevo de menos de 2 años) de la misma versión, propia y del mercado. Es la primera covariable candidata para el Cox de VN después de la posición de precio frente al stock externo.
- **Valores residuales.** Curva de precio del VO por modelo y edad, estimada con los precios de Walcu y del scraper de VO, con el método de KROL como referencia. Sirve para el argumento de cuota final y renting del VN, y como contexto en el panel.

Lo que no se hace: mezclar episodios de VN y VO en una misma curva, ni usar la relación VN–VO como causal. Son asociaciones hasta que haya un experimento.

## Señales y alertas

Las alertas empiezan como reglas con umbrales fijos y comprobables; cuando la predicción bayesiana esté validada, sus probabilidades sustituyen a los umbrales. Los valores son una propuesta inicial que se calibra en el piloto.

| Alerta | Se dispara cuando | Destinatario |
| --- | --- | --- |
| Vehículo envejecido | Días en stock por encima del percentil 75 de su curva Kaplan-Meier | Jefe de ventas de la marca |
| Oportunidad de campaña | Vehículo envejecido y demanda propia (visitas + leads) al alza frente a su referencia | Marketing |
| Riesgo de inmovilización | Probabilidad de salida en 90 días por debajo del 50% | Jefe de ventas + director general |
| Presión de mercado | Stock externo de la misma versión sube más del 20% en 30 días, o nuestro precio queda más del 3% por encima de la mediana | Jefe de ventas |
| Desvío de ventas | Ventas o salidas de stock fuera del intervalo al 80% de la predicción anterior | Director general |
| Coste de campaña disparado | Coste por lead por encima del doble de su media de 28 días | Marketing |
| Datos caídos | Una fuente no actualiza en 48 horas | Administrador |
| Demanda no capturada | El ratio de captura (leads propios / interés de mercado del modelo) cae más de un 20% frente a su media de 3 meses mientras el interés sube | Marketing |
| Presión de un competidor | La atención relativa del modelo (YoY3 de Google Trends menos la media de su conjunto de competidores) es negativa dos meses seguidos, o un competidor dobla su interés (lanzamiento u oferta). Es la única señal externa con adelanto validado: anticipa la cuota del trimestre siguiente | Marketing |

Las dos últimas alertas son el uso que tiene la atención. En absoluto (Google Trends, Wikipedia) se mueve a la vez que el mercado y se conoce antes que las matriculaciones: sirve para saber si la demanda existe y se la lleva otro. En relativo a los competidores, Google Trends sí anticipa la cuota del trimestre siguiente, y por eso la alerta de presión de un competidor es la primera que puede orientar una campaña antes de que la pérdida llegue a las ventas. La atención también alimenta al equipo de marketing fuera del modelo: consultas relacionadas para palabras clave y mensajes, perfil semanal y geográfico del interés para repartir presupuesto, y medición de campañas por geo-experimentos.

Cada alerta lleva su motivo en lenguaje claro (por ejemplo: *"Tiguan 1.5 TSI Life: 112 días en stock, percentil 82 de su curva; visitas +35% en 4 semanas; hay 46 unidades iguales en la red"*) y un nivel de confianza. Si la señal es fuerte pero la predicción es débil, se presenta como oportunidad, no como certeza. Si no hay stock de la versión, nunca se recomienda aumentar campaña sin avisarlo.

## Panel diario

Un único panel web con acceso por usuario y tres vistas según el rol. Se actualiza cada mañana tras la ingesta nocturna y envía las alertas del día.

| Vista | Contenido |
| --- | --- |
| Dirección general | Stock total y su antigüedad frente al mes anterior; ventas del mes frente al intervalo previsto; inversión y coste por lead, cita y venta; alertas abiertas por responsable |
| Jefes de ventas | Lista de vehículos ordenada por riesgo de inmovilización; curva de supervivencia de cada versión frente al mercado; posición de precio; alertas de su marca |
| Marketing | Oportunidades de campaña por modelo y versión; rendimiento diario de cada campaña de Meta; señales de demanda (visitas, leads, Google Trends) |

Comunes a las tres: estado de las fuentes y fecha de última actualización, explicación de cada alerta, filtro por marca y modelo, y exportación a Excel.

Las alertas también llegan por Telegram, a través de KAIROS, el agente que ya está configurado.

## Roadmap

[embedded content: roadmap · 6 sprints y 5 puertas]

El scraper va en el primer sprint porque el histórico del mercado solo existe desde el día en que arranca. Cada puerta es la condición para pasar al sprint siguiente; el panel tiene utilidad desde el Sprint 4, antes de que lleguen los modelos bayesianos.

El detalle de cada sprint, con su prompt para Claude Code, las tareas previas de Cayetano y el criterio de la puerta, está en la pestaña Plan de programación. La extracción de Google Trends para los competidores entra en el Sprint 2 y la confirmación de 2026 de la atención relativa, en el Sprint 5.

## Riesgos y mitigaciones

| Riesgo | Impacto | Mitigación |
| --- | --- | --- |
| El histórico externo empieza el día que arranca el scraper | Sin meses de datos no hay curvas del mercado | Arrancar el scraper en el primer sprint, antes que los modelos |
| La web de stock cambia su estructura o bloquea el acceso | Huecos en el stock externo | Alerta de "datos caídos", reintentos y un registro de la estructura esperada |
| Una salida de la web no siempre es una venta | Curvas externas sesgadas | Llamarlo "salida", detectar reapariciones del mismo VIN o anuncio y cruzar con Walcu en el stock propio |
| Pocas unidades por versión | Predicciones inestables | Modelo jerárquico versión → modelo → marca y aviso de DATOS INSUFICIENTES |
| El efecto de la inversión está confundido con la demanda | Recomendación de presupuesto errónea | No recomendar cantidades en la primera versión; registrar inversión y resultados y planificar semanas de prueba |
| La misma campaña con nombres distintos en Meta y en Walcu | No se puede calcular el coste por cita y venta | Walcu registra la campaña de origen de cada lead; tabla de equivalencias de nombres entre Meta y Walcu |
| Datos personales en Walcu | Cumplimiento del RGPD | Ingerir solo agregados; no guardar nombres, teléfonos ni correos |
| Señales externas sin aporte real (GT, macro) | Ruido en el modelo | Medir su aporte con ablación y Clark-West antes de incluirlas |

## Decisiones y pendientes

| Tema | Decisión |
| --- | --- |
| Stock externo | Solo Volkswagen por ahora, desde su web pública de stock. Audi, en una fase posterior |
| Histórico de stock en Walcu | La API da la fecha de entrada y de salida de cada vehículo: las curvas internas salen desde el primer día |
| Origen de los leads | Walcu registra la campaña de origen: el coste por lead, cita y venta se puede calcular |
| Infraestructura | Mismo VPS de Hetzner que el otro proyecto, aislado |
| Canal de alertas | Telegram, a través del agente KAIROS |
| Referencia de los desvíos | La predicción anterior del sistema |
| Coste del stock | Tasa de depreciación implícita (días en stock frente a la caída del precio inicial), sin coste financiero |
| Señales externas | Admitida la atención relativa a los competidores (Google Trends, cat. 47) como señal direccional. Descartadas Wikipedia, el tipo de crédito al consumo y las expectativas del ICC como predictores; quedan como contexto |
| Conjuntos de competidores | Fijados por producto antes de extraer (tabla de la sección 2.1) y cerrados con el jefe de ventas; no se eligen por los datos |
| Forma de trabajo | Plan de programación por sprints con un prompt por sesión, supervisado por Cayetano; no se pasa de sprint sin superar su puerta (pestaña Plan de programación) |
| Alcance VN y VO | La plataforma cubre los dos stocks. El VO propio se lee de Walcu, no de KROL (decisión del 11-11-2026); mismo esquema con tipo_stock; curvas separadas; se mide la relación VN–VO (retoma, sustitución, residuales) |

- [x] Recibir el repositorio y la documentación de KROL
- [ ] Credenciales de la API de Walcu (martes)
- [ ] Solicitar acceso a la API de Google Trends
- [x] Prueba de Wikipedia: ejecutar `descargar_wikipedia.py` en Colab y pasar el CSV
- [ ] Exportar de Google Ads Keyword Planner el histórico mensual de 10–15 consultas con intención por modelo
- [ ] Confirmar si la API de Walcu da pedidos, financiaciones, tasaciones y pruebas de conducción con fecha
- [ ] Cerrar con el jefe de ventas los conjuntos de competidores de la sección 2.1
- [ ] Registrar en OSF el prerregistro de la confirmación de 2026 (atención relativa) antes de descargar Google Trends 2026
- [ ] Configurar la extracción de Google Trends para los modelos VW y sus competidores (API oficial, o lotes anclados mientras llega)
- [ ] Copiar al repositorio, en vendor/krol/, el traspaso de supervivencia (Traspaso_Supervivencia_Stock_VO.md, r9_expected_time.py, r9b_expected_time.py, r9c_time_varying.py)
- [ ] Confirmar con Walcu cuánto histórico de VO ofrece la API (sustituye a la lectura de KROL)
