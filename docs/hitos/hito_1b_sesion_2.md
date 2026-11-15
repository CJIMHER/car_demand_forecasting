Repositorio car_demand_forecasting. Lee CLAUDE.md, docs/hitos/hito_1b_sesion_1.md, panel/README.md, analitica/README.md, docs/prototipo/ (Main.dc.html es Dirección; Ventas.dc.html, Marketing.dc.html y Movil.dc.html) y docs/diseno.md (Señales y alertas, Panel diario).

Hito 1b, sesión 2 de 3: contrato de datos del panel y vistas Dirección, Ventas y Marketing con datos de ejemplo marcados. Rama hito-1b (al día con main), sin push y sin conectarte al servidor. Guarda este encargo tal cual en docs/hitos/hito_1b_sesion_2.md.

Contexto. Las fuentes (Walcu, GA4, Meta) aún no están conectadas. Esta sesión deja hechas las tres vistas y las tablas de las que leen, rellenas con datos de ejemplo, de modo que conectar una fuente sea sustituir filas de ejemplo por reales sin tocar las vistas. No se ajusta ningún modelo ni se inventa ningún cálculo: los datos de ejemplo se escriben directamente en las tablas panel_*. No toques scrapers/, las tablas de hechos ni las tareas actuales.

1. Contrato de datos (docs/contrato_panel.md y migración)
- Una tabla panel_* por bloque o familia de bloques de las vistas. El documento da, por tabla: columnas, unidad, grano, qué fuente la rellenará (Walcu, GA4, Meta o un modelo) y en qué hito.
- Todas llevan día, origen ('real' o 'ejemplo') y calculado_at.
- Sin datos personales: los leads, solo agregados por día, modelo y campaña; los vehículos, por número de stock; ningún nombre, teléfono, correo ni identificador de cliente.
- Toda salida de un modelo lleva intervalo (p10, p50, p90), versión del modelo y el n en que se apoya; nunca un punto solo.
- Un bloque no mezcla orígenes: cuando entra la primera fila real de una tabla, sus filas de ejemplo dejan de mostrarse. Decide el mecanismo y pruébalo.
- El usuario de Postgres del panel solo lee las panel_* nuevas, como las de mercado.

2. Datos de ejemplo
- make ejemplo-cargar rellena las panel_* nuevas con datos de ejemplo deterministas, coherentes entre vistas (las alertas de Dirección hablan de vehículos y campañas que existen en Ventas y Marketing) y con modelos de la marca configurada. make ejemplo-borrar los quita. Nunca escriben en tablas de hechos ni en las panel_mercado_*.
- En producción solo funcionan con PANEL_PERMITE_EJEMPLO=1 en el .env.
- Marca visible: todo bloque que lea filas de ejemplo lleva la etiqueta «Datos de ejemplo», y la vista, una banda arriba que lo dice. Los bloques con datos reales (por ejemplo, la tarjeta de mercado de Dirección) no la llevan.
- Visibilidad: una vista con algún bloque de ejemplo solo la ven los roles de PANEL_EJEMPLO_VISIBLE_PARA (por defecto, tecnico). Los demás siguen viendo «Se activa al conectar sus datos». Sin filas en una tabla, el bloque dice «Sin datos todavía», nunca un cero.

3. Vista Dirección (Main.dc.html y Movil.dc.html)
- Selector VN, VO, Todo.
- Tarjetas: stock total con su reparto VN y VO; antigüedad media frente al objetivo; ventas del mes frente a la banda prevista; alertas abiertas y cuántas son nuevas hoy.
- Ventas acumuladas del mes frente a lo previsto, con la banda p10–p90 («lo normal: 8 de cada 10 meses caen aquí»).
- Alertas de hoy, cada una con su tema y su destinatario.
- Stock por días en exposición, con lo que supera los 90 días resaltado.
- Tarjeta del mercado: datos reales de panel_mercado_*, con enlace a Mercado.

4. Vista Ventas (Ventas.dc.html)
- Vehículos en stock ordenados por riesgo: vehículo, tipo, días, probabilidad de venderse en 90 días con su intervalo, precio frente al mercado y sugerencia. Filtro por tipo y «solo los de menos del 50 %».
- Al elegir un vehículo: su probabilidad, la curva de su grupo con la zona de los próximos 90 días, mediana del grupo, número de similares y leads recientes.
- Los botones «Pedir campaña a marketing» y «Revisar precio» se muestran desactivados: su circuito llega en la sesión 3.
- En móvil, dos líneas por vehículo, como la tabla de Mercado.

5. Vista Marketing (Marketing.dc.html, más tres bloques nuevos con el mismo lenguaje visual)
Del prototipo:
- Tarjetas de los últimos 28 días: inversión, leads (con citas y ventas atribuidas), coste por lead frente a los 28 días anteriores y coste por venta.
- Recomendación por modelo (aumentar, mantener, reducir o cambiar), con su motivo en una frase.
- Tabla «Campañas: del gasto a la venta».
- Atención frente a sus rivales, con la nota de que es una señal direccional.
Nuevos:
- Salud de cada campaña, dentro de la tabla de campañas: tipo (de modelo o general), días de vida, estado (bien, vigilar, revisar, sin datos suficientes) y, si no va bien, el eslabón que falla (entrega, anuncio, web, calidad del lead, stock) con las cifras que lo muestran. El contrato fija el significado: cada medida se compara con su referencia a los mismos días de vida, y el estado sale de la probabilidad de estar peor que esa referencia, con su n. Una nota lo explica en una frase.
- «Cuándo lanzar una campaña general»: las seis semanas siguientes, cada una con su nivel (favorable, normal, poco favorable) y sus motivos (estacionalidad, calendario, stock envejecido, atención, presión de otros concesionarios). Con la nota: «Orientativo: describe cómo viene cada semana; no mide el efecto de lanzar en una u otra».
- «Qué dieron las campañas pasadas»: campaña, fechas, gasto, y leads y ventas frente a lo esperado sin campaña, con su intervalo y un veredicto (por encima de lo esperado, dentro de lo normal, por debajo).
En móvil, todo sin desplazamiento horizontal.

6. Dos ajustes de Mercado
- El modelo se muestra sin la marca delante («T-Cross», no «Volkswagen T-Cross»). La marca sale de la configuración; hazlo en la normalización del precálculo y comprueba que make precalcular rehace el histórico sin duplicar modelos.
- «Nuestros»: fuera de la provincia propia, solo las unidades, sin porcentaje.

7. Pruebas, sin red
- Cada vista con datos de ejemplo: etiqueta en cada bloque de ejemplo, banda en la vista y ninguna etiqueta en los bloques reales.
- Visibilidad: con datos de ejemplo, cada rol por cada vista según PANEL_EJEMPLO_VISIBLE_PARA; al sustituir una tabla por filas reales, su bloque pierde la etiqueta, y la vista se abre a su rol cuando no queda ningún bloque de ejemplo.
- make ejemplo-cargar se niega en producción sin PANEL_PERMITE_EJEMPLO=1 y nunca toca tablas de hechos ni de mercado.
- Sin filas: «Sin datos todavía», nunca un cero.
- Ninguna columna de las panel_* tiene nombre, teléfono, correo ni identificador de cliente.
- Regla Replicable: con el concesionario ficticio de Valladolid, las tres vistas no muestran «AVISA» ni «Sevilla».
- Chrome a 390 px en las tres vistas: nada desborda. Capturas de las tres en escritorio y móvil en docs/capturas/.

Termina con: qué has probado y qué no, las decisiones tomadas sin preguntar (sobre todo las del contrato), la entrada en CHANGELOG.md y los pasos para Cayetano.
