# Documento de desarrollo — Plataforma de stock, demanda y campañas

Nov 10, 2026 · @cayetano

## Cómo se usa

La plataforma se construye en siete hitos. El primero monta un servidor nuevo y aislado y deja los scrapers de VN y VO de Volkswagen guardándolo todo cada día. Cada día sin capturar es historia del stock externo que no se recupera.

- **Un hito, una o varias sesiones de Claude Code** en el repositorio `car_demand_forecasting`, lanzadas con el prompt de su ficha. Desde el hito 0 las reglas comunes viven en `CLAUDE.md`, así que cada prompt empieza pidiendo leerlo.
- **Cayetano supervisa**: hace las tareas previas, revisa el diff y la lista de decisiones que Claude tomó sin preguntar, ejecuta la revisión de la ficha y decide si se pasa la puerta. Ningún hito empieza sin la puerta del anterior; los scrapers no se paran nunca.
- **Alcance**: Volkswagen, VN y VO. El stock propio, VN y VO, sale de Walcu; el externo, de la web oficial de stock de VW. KROL no es fuente: tiene varias marcas. Audi entra después con el mismo esquema.
- **Relación con el documento de diseño**: este documento sustituye a su pestaña «Plan de programación». El diseño (modelo de datos, analítica, alertas, competidores) sigue allí y se copia al repositorio como `docs/diseno.md`.

## Infraestructura aislada

Recomendación: un servidor nuevo en Hetzner, dentro de un proyecto propio y sin nada más. KROL sigue intacto en DigitalOcean y la plataforma no se conecta a él, porque el VO también llega por Walcu. El VPS de Hetzner que ya existe no se reutiliza, aunque tenga hueco.

| Criterio | Hetzner, servidor nuevo | DigitalOcean, droplet nuevo |
| --- | --- | --- |
| Lo que ya hay | Un VPS disponible, que no se toca | El servidor de KROL, que no se toca |
| Aislamiento | Proyecto propio, con su token de API, firewall y copias | Proyecto propio, con su token, firewall y copias |
| Relación con KROL | Ninguna: otro proveedor | Mismo proveedor, en un proyecto aparte |
| Coste por la misma máquina | Normalmente menor | Normalmente mayor |
| Región | Alemania o Finlandia (UE) | Fráncfort o Ámsterdam (UE) |
| Brutos y copias | Object Storage compatible con S3 | Spaces, compatible con S3 |

Tamaño de partida: 4 vCPU compartidas y 8 GB de RAM (plan CX33, con 80 GB de disco). Los brutos van al bucket, y si el disco se queda corto se añade un volumen. Lo que más pide es PyMC en el hito 5 y los backtests; los scrapers y Postgres caben de sobra. El precio se confirma en la consola el día de la compra.

Aislamiento, punto por punto:

- **Cuenta**: proyecto nuevo en Hetzner Cloud (`cdf`), con servidor y bucket propios y un token de API que no sirve para nada más.
- **Acceso**: usuario `cdf` sin root, SSH solo con clave y sin contraseña, abierto solo a las IP de Cayetano o por WireGuard.
- **Red**: el firewall de Hetzner deja entrar el 22 restringido y el 443 del panel. Postgres nunca se publica.
- **Contenedores**: Docker Compose con el prefijo `cdf_` en contenedores, red y volúmenes.
- **Secretos**: en `/opt/cdf/.env` con permisos 600, fuera del repositorio, con un token distinto por fuente.
- **KROL**: la plataforma no se conecta a su base, porque mezcla varias marcas y el VO de VW ya llega por Walcu. De KROL solo se reutiliza el código de supervivencia, copiado en vendor/krol/.
- **KAIROS**: recibe las alertas por su interfaz (endpoint o cola) y no comparte servidor.
- **Copias**: `pg_dump` diario cifrado al bucket con 30 días de retención, copia semanal del servidor y una prueba de restauración al mes.
- **Panel**: subdominio propio con HTTPS automático (Caddy) y acceso con usuario.

[embedded content: arquitectura · proyecto cdf aislado]

Todo lo que la plataforma toca vive dentro del proyecto `cdf`. KROL queda fuera y KAIROS solo recibe alertas: ninguno de los dos depende de la plataforma.

## Reglas comunes (`CLAUDE.md`)

El hito 0 crea `CLAUDE.md` con este bloque. Desde entonces, cada sesión lo lee antes de tocar nada.

```
Reglas del proyecto car_demand_forecasting

- Módulos: infra/, scrapers/, ingesta/, analitica/, alertas/, panel/. Un README por módulo: qué hace y cómo se prueba.
- Servidor: Hetzner, proyecto cdf, aislado. Docker Compose con prefijo cdf_. No toques ningún otro servidor. KROL no es fuente de datos (mezcla varias marcas): de él solo se reutiliza código, en vendor/krol, que no se modifica.
- Nada se sobrescribe: cada captura añade filas con captured_at y run_id. Los brutos de los scrapers se guardan comprimidos en el bucket antes de parsear.
- Desaparecer no es vender: un anuncio externo ausente se marca ausente, nunca vendido. Venta solo si la confirma Walcu.
- Datos personales: nunca se guardan nombres, teléfonos, correos ni identificadores de clientes. Los leads se agregan por modelo, día y campaña antes de guardarse. De ventas_vn.csv solo se usan columnas no personales.
- Secretos solo en el .env del servidor; .env.example sin valores. Ningún token en el código, en un commit ni en el navegador.
- Scraping: solo páginas públicas y sin login, respetando robots.txt, con pausas entre peticiones y User-Agent de la empresa.
- Sin mirar al futuro: toda variable de un modelo se calcula solo con lo conocido en su fecha.
- Validación: todo modelo se compara con una referencia simple con origen móvil. Si no la bate, no se despliega.
- Cada sesión entrega: diff revisable, pytest en verde, entrada en CHANGELOG.md y la lista de decisiones tomadas sin preguntar.
```

## Hito 0 · Servidor aislado y scrapers en marcha (semana 1)

**Estado a 14 de noviembre de 2026**:

- Hecho: servidor en Hetzner (proyecto `cdf`, Ubuntu 26.04), acceso solo con clave y desde la IP de Cayetano, y bucket conectado.
- Hecho: scrapers VN y VO instalados con `cdf_scrapers`. Systemd los lanza cada madrugada con hasta 4 intentos: VO a las 04:00, VN gasolina y diésel a las 05:30 y VN eléctricos e híbridos (vn_otros, versión 1.1.0 de Astra) a las 06:45.
- Cada scraper guarda su histórico en su propio SQLite (`/opt/cdf/data/vn` y `/opt/cdf/data/vo`) y sube al bucket las respuestas originales y una copia de la base.
- Hecho el 13 de noviembre: VN ampliado a eléctricos e híbridos con una segunda copia del scraper; primera captura de 422 anuncios (40 eléctricos, 382 híbridos). El control del total falla de día, pero de madrugada las capturas salen a la primera.
- Pendiente: la segunda parte, Postgres y la carga diaria desde los scrapers, con el prompt de abajo.

**Objetivo**: que en la primera semana los scrapers de VN y VO corran cada día en el servidor nuevo y guarden el bruto y el parseado de cada pasada. Es el hito más urgente: la web solo enseña el stock de hoy, y las curvas del stock externo necesitan al menos 4 meses de capturas.

**Puerta**: siete días seguidos de pasadas en verde de los dos scrapers, con brutos re-parseables y una copia restaurada.

**Tareas de Cayetano antes de la sesión**:

- Crear el repositorio privado `car_demand_forecasting` en GitHub y clonarlo en el Mac; Claude Code lo inicializa en la primera sesión.
- Copiar al repositorio, antes de la sesión: los scrapers (`scrapers/vn`, `scrapers/vo`), `infra/cdf_scrapers`, el documento de diseño como `docs/diseno.md`, este documento como `docs/desarrollo.md` y el prototipo como `docs/prototipo/`.
- Anotar el `dealer_id` y el nombre con que nuestro concesionario aparece en los datos de los scrapers.
- Comprobar con `cdf-estado` siete días seguidos de capturas en verde.

**Prompt**:

```
Repositorio car_demand_forecasting, recién creado. Si no existe CLAUDE.md, créalo con el bloque de reglas comunes de docs/desarrollo.md y prepara la estructura de módulos y el .gitignore (nunca .env, datos ni bases). Después lee CLAUDE.md y docs/diseno.md (Fuentes y Modelo de datos).

Hito 0, segunda parte. En el servidor ya funcionan tres scrapers de Volkswagen, instalados con infra/cdf_scrapers: vo (ocasión, 04:00), vn (nuevos gasolina y diésel, 05:30) y vn_otros (nuevos eléctricos e híbridos, 06:45), cada uno con hasta 4 intentos. Cada uno guarda su histórico en SQLite (/opt/cdf/data/vo/vw_stock.sqlite3, /opt/cdf/data/vn/vw_vn_stock.sqlite3 y /opt/cdf/data/vn_otros/vw_vn_stock.sqlite3) y sube las respuestas originales al bucket. No toques los scrapers ni sus bases: solo se leen.

1. infra/docker-compose.yml con postgres:16 y jobs (Python 3.12, cron dentro); servicios panel y caddy vacíos de momento. Prefijo cdf_ en contenedores, red y volúmenes. Postgres sin puerto publicado. .env.example sin valores.
2. Migraciones Alembic para:
   - stock_externo_snapshot (captured_at, run_id, tipo_stock VN|VO, id_anuncio, bastidor, concesionario, código postal, provincia, modelo, versión, combustible, año, km, precio en céntimos, etiqueta del precio, estado de entrega y el resto de campos en jsonb);
   - anuncios_externos (id_anuncio, tipo_stock, first_seen, last_seen, ausente_desde, estado, reapariciones);
   - fuentes_estado.
3. ingesta/scrapers_vw.py: cada día a las 08:30 (cuando ya han podido acabar los reintentos) lee las tres bases SQLite de los scrapers en solo lectura (mode=ro) y copia a Postgres las capturas success y los eventos nuevos desde la última carga. Es idempotente: cargar dos veces el mismo día no duplica. La provincia se deriva del código postal. Se respetan los eventos de los scrapers: desaparecer no es vender.
4. Comprobación a las 09:00: si un scraper no tiene captura success del día, lo escribe en fuentes_estado.
5. Copias: pg_dump diario cifrado al bucket bajo backups/postgres/, con 30 días de retención, y un script de restauración probado en un contenedor aparte.
6. Pruebas con bases SQLite sintéticas (nada real en el repositorio).
7. Concesionario propio: config/concesionario.yaml con los dealer_id y nombres con que aparecemos en los scrapers. Una columna es_propio en stock_externo_snapshot los marca, para no contarnos como competencia.
8. Despliegue: infra/deploy.md y un Makefile. El servidor descarga el repositorio con una clave de despliegue de solo lectura en /opt/cdf/app/repo y «make deploy» aplica migraciones y reinicia los contenedores. Tú no te conectas al servidor: deja las órdenes listas para que las ejecute Cayetano.

Termina con las decisiones tomadas sin preguntar y el recuento de anuncios VN y VO cargados por día.
```

**Revisión de Cayetano**: `cdf-estado` siete días seguidos en verde; abrir un día del bucket y comprobar que están las respuestas originales; restaurar un `pg_dump` en un contenedor aparte; comparar el recuento de `stock_externo_snapshot` de un día con el `stock_latest.csv` del scraper.

**Nota**: el scraper de VO es la única fuente del VO externo de VW. KROL no se usa porque mezcla varias marcas.

## Hito 1 · Datos propios de Walcu, VN y VO (semanas 2–3)

**Objetivo**: capturas diarias del stock VN y VO, ventas, tasaciones y leads agregados de Walcu, todo mapeado a un catálogo común de versiones.

**Puerta**: siete días seguidos de capturas sin huecos en Walcu, ninguna tabla con datos personales y la cola de versiones sin mapear revisada.

**Tareas de Cayetano antes de la sesión**:

- Credenciales de la API de Walcu (previstas para el martes) y confirmar qué campos da: pedidos, financiaciones, tasaciones y pruebas de conducción.
- Confirmar con Walcu cuánto histórico de VO hay (entradas, salidas y precios), porque ya no viene de KROL.
- Copiar a `vendor/krol/` el traspaso de supervivencia: `Traspaso_Supervivencia_Stock_VO.md`, `r9_expected_time.py`, `r9b_expected_time.py` y `r9c_time_varying.py`.
- Dejar en `data/competidores.csv` la lista cerrada con el jefe de ventas: un modelo VW por fila con su segmento y sus competidores.

**Prompt**:

```
Repositorio car_demand_forecasting. Lee CLAUDE.md, docs/diseno.md (Fuentes de datos, Modelo de datos y Analítica 4, Relación entre VN y VO) e ingesta/README.md si existe.

Hito 1. Construye:
1. Migraciones Alembic para catalogo_versiones, alias_version, stock_interno_snapshot (con tipo_stock VN|VO), ventas, tasaciones, leads_agg (por modelo, día y campaña, sin datos personales) y retomas. Cada tabla de hechos lleva captured_at y nunca se actualiza en sitio.
2. data/catalogo_vw.csv con la gama actual (Polo, T-Cross, Taigo, Golf, T-Roc, Tiguan, Tayron, ID.3, ID.4, ID.5, ID.7, Passat) y columnas marca, modelo, segmento y conjunto_competidores, enlazado con data/competidores.csv. Carga ambos en catalogo_versiones.
3. ingesta/walcu.py: cliente de la API de Walcu (base https://api.crm.walcu.com/dealers/{dealer_id}; esquemas en docs/walcu_api.pdf) con las credenciales del .env. Solo peticiones GET: la API permite modificar y borrar, y este cliente no debe poder hacerlo. Recursos y campos:
   - cars: _id, vin, stock_number, type (new, demo y km0 son VN; used es VO), make, model, version, fuel, year, mileage, location, status y status_history (fechas de cada cambio de estado), prices.retail y prices.new con current_price y previous_prices (histórico de precios), purchase_date, published, unpublished_at, created_at.
   - saleleads: _id, created_at, origin.source, origin.medium y origin.campaign (el UTM), status y status_history, car_list (car_id, make, model, type), finance.finance_type, y de computed solo appointments, first_offer_date y number_of_offers.
   - appraisalleads: _id, created_at, car (vin, make, model, year, mileage), appraisal_value, status_history.
   - dates: start_date, lead_type, salelead.category, salelead.car_id, showed_up.
   - offers: lead_id, status (signed equivale a pedido firmado), created_at.
   Lista blanca de campos: el objeto computed de los leads trae nombre, teléfonos, correos, documento y dirección del cliente. Se descarta en memoria al recibir la respuesta; nunca se escribe en disco, en logs ni en el bucket. No se llama a clients ni a contacts. El client_id solo se usa para enlazar tasación y venta, y se guarda como hash con sal.
   Los estados son texto configurado por el concesionario: su significado (vendido, reservado, entregado) va en config/walcu_estados.yaml, que rellena Cayetano. Los coches borrados en Walcu desaparecen del listado: se detectan comparando con la captura anterior. Descarga diaria del stock VN y VO (entrada y salida de cada vehículo, con tipo_stock), ventas, tasaciones y leads. Los leads se agregan por modelo, día y campaña (utm_source, utm_medium y utm_campaign del lead) ANTES de guardarse; no persistas nombres, teléfonos, correos ni ningún identificador personal. El README dice qué campos usa y cuáles descarta.
4. VO: el stock VO propio sale de la misma API de Walcu, con tipo_stock = 'VO', año, kilómetros y precio, y sus ventas confirmadas con la misma lógica que el VN. No te conectes a KROL: mezcla varias marcas y no es fuente de este proyecto.
5. Mapeo de versiones: alias_version traduce los nombres de Walcu y de los scrapers (stock_externo_snapshot del hito 0) al catálogo. Lo que no mapea va a una cola de revisión manual y no se pierde. El VO se mapea a nivel de modelo, con año y kilómetros como atributos.
6. Retomas: cuando una venta VN lleva una tasación o una entrada VO asociada en Walcu, guarda el vínculo en retomas (venta_vn_id, episodio_vo_id, fecha), sin datos personales.
7. Programación: Walcu a las 05:30; la comprobación de las 09:00 del hito 0 cubre también estas fuentes.
8. Carga histórica: reconstruye stock_interno_snapshot de VN y de VO hacia atrás tanto como permita la API de Walcu, y documenta desde qué fecha hay datos de cada uno.
9. Pruebas con datos sintéticos, nunca con exportaciones reales en el repositorio.

Termina con las decisiones tomadas sin preguntar, las versiones que no han mapeado (por fuente) y cuántas ventas VN tienen retoma vinculada.
```

**Revisión de Cayetano**: una semana de capturas sin huecos; revisar la cola de versiones sin mapear; buscar en todas las tablas columnas con nombre, teléfono o correo (no debe haber ninguna); comprobar desde qué fecha hay histórico de VO; contrastar el stock de un día con lo que ve el jefe de ventas.

## Hito 1b · Primer panel (semana 3)

> **Ficha desactualizada.** La sustituye el encargo de docs/hitos/hito_1b_sesion_1.md (sesión 1 de 3, 15-11-2026); lo que sigue se conserva solo como referencia.

**Objetivo**: una primera versión del panel con los datos que ya hay, para probar el circuito completo de los datos a la pantalla y que el jefe de ventas vea algo real pronto. Sigue el prototipo de las tres pantallas validado con él.

**Puerta**: el jefe de ventas lo abre en su móvil y los números de dos modelos cuadran con la web de stock de VW de ese día.

**Tareas de Cayetano antes de la sesión**:

- Un subdominio apuntando al servidor y el 80 y el 443 abiertos en el firewall de Hetzner.
- La lista de usuarios iniciales con su rol.
- El prototipo validado, guardado en el repositorio como `docs/prototipo/`.

**Prompt**:

```
Repositorio car_demand_forecasting. Lee CLAUDE.md, docs/diseno.md (Panel diario) y docs/prototipo/ (diseño validado de las tres pantallas: respeta su estructura, colores y textos).

Hito 1b. Primer panel. Construye en panel/:
1. Aplicación FastAPI con plantillas Jinja2, htmx para filtros y tablas y ECharts para las gráficas. Sin compilación de frontend: los ficheros estáticos los sirve la propia aplicación.
2. Acceso con usuario y contraseña por persona (hash argon2, sesión con cookie segura) y roles direccion, ventas y marketing. Los usuarios se dan de alta con un comando, no desde la web.
3. Una sola vista por ahora, «Mercado» (docs/prototipo/Mercado.dc.html), con las otras tres pestañas del prototipo visibles pero desactivadas: stock externo VN y VO de Volkswagen por modelo y día (tablas del hito 0), con altas y ausencias del día, filtro por provincia (Sevilla por defecto) y por tipo VN/VO, y la serie de los últimos 90 días. Arriba, la hora de la última captura de cada scraper y su estado.
4. Si el hito 1 ya está, añade a la misma vista el stock propio de Walcu por modelo.
5. El panel lee de Postgres con un usuario de solo lectura; el navegador nunca recibe claves. El cálculo pesado se hace de noche en jobs y el panel lee tablas ya preparadas.
6. Caddy, en el mismo compose, sirve el panel en el subdominio del .env con HTTPS automático.
7. Pruebas: acceso, permisos por rol y la vista con datos sintéticos.

Termina con las decisiones tomadas sin preguntar y capturas de la vista en escritorio y en móvil.
```

**Revisión de Cayetano**: abrirlo en el móvil; comparar dos modelos con la web de VW; comprobar que un usuario de marketing no ve lo que no le corresponde.

## Hito 2 · Señales propias y atención relativa (semanas 4–5)

**Objetivo**: visitas de GA4, inversión y resultados de Meta, el cruce lead–campaña y la extracción de Google Trends de cada modelo VW y sus competidores, con la atención relativa al segmento.

**Puerta**: leads cruzados con su campaña y validados con marketing; `gt_relativo` con todos los conjuntos y el primer mes comparado a mano con la web de Google Trends; la implementación reproduce los números del experimento 08.

**Tareas de Cayetano antes de la sesión**:

- Cuenta de servicio de GA4 y token de la Marketing API de Meta, ambos de solo lectura.
- Copiar al repositorio `data/competidores.csv`, la lista cerrada con el jefe de ventas el 14 de noviembre (Renault 5 añadido al Polo y eléctricos separados en ID.3, ID.4/ID.5 e ID.7).
- Registrar en OSF el prerregistro de la confirmación de 2026 y dejar su URL en `docs/prerregistro_osf.md` antes de descargar ningún dato de Google Trends de 2026.
- Copiar `test_senales_demanda/` (resultados y scripts de los experimentos) al repositorio como referencia.

**Prompt**:

```
Repositorio car_demand_forecasting. Lee CLAUDE.md, docs/diseno.md (Fuentes, sección 2.1 Competidores y cuota de atención) y data/competidores.csv.

Hito 2. Construye:
1. ingesta/ga4.py: Data API de GA4, visitas diarias por ficha de modelo (pagePath mapeado a modelo con una tabla editable). Guarda por modelo y día.
2. ingesta/meta.py: Marketing API Insights, gasto, impresiones, clics y leads por campaña y día. Cruza con leads_agg por utm_campaign, con una tabla editable de correspondencia entre utm_campaign y campaña de Meta (lo que no cruza va a revisión), y calcula coste por lead, por cita y por venta en una vista materializada.
3. ingesta/google_trends.py: para cada conjunto de data/competidores.csv descarga el interés mensual de cada consulta «marca modelo» en minúsculas, categoría 47, geo ES y ES-AN. Con credenciales de la API oficial en el .env, úsala; si no, lotes de cinco con el ancla «volkswagen golf», tres repeticiones promediadas y re-anclaje de escala (método en test_senales_demanda/). Guarda todo en gt_raw con lote y fecha de captura; los brutos no se reescriben.
4. analitica/atencion_relativa.py: YoY3 = log(1 + media de 3 meses) − log(1 + la misma media 12 meses antes); relativo = YoY3 del modelo − media del YoY3 de su conjunto (mínimo dos competidores con dato); cuota = interés del modelo / suma del conjunto dentro del mismo lote. Escribe gt_relativo. Reproduce con estas funciones el resultado de test_senales_demanda/08_competidores sobre los datos históricos y documenta la diferencia si la hay.
5. Programación: GA4 y Meta a diario a las 06:00; Google Trends el día 3 de cada mes.

No descargues datos de Google Trends posteriores a diciembre de 2025 hasta que exista docs/prerregistro_osf.md con la URL del registro. Termina con las decisiones tomadas sin preguntar y las consultas que vuelven sin datos o con demasiados ceros.
```

**Revisión de Cayetano**: comparar el primer mes de `gt_raw` con la web de Google Trends para dos conjuntos; comprobar que `atencion_relativa.py` reproduce el experimento 08; validar el cruce lead–campaña con marketing; confirmar que ningún token llega al código.

## Hito 3 · Supervivencia del stock VN y VO (semanas 6–7)

**Objetivo**: curvas de salida del stock propio para VN (por versión) y VO (por comparables), la probabilidad de salida de cada vehículo y las medidas descriptivas de la relación VN–VO. El stock externo se activa cuando haya 4 meses de capturas: si los scrapers arrancan la semana del 16 de noviembre, hacia mediados de marzo de 2027.

**Puerta**: el backtest bate a la tasa media de salida del modelo y la calibración por deciles es razonable, en VN y en VO por separado.

**Tareas de Cayetano antes de la sesión**: fijar los mínimos por celda (por defecto, 10 episodios y 3 ventas) y el horizonte del RMST (90 días).

**Prompt**:

```
Repositorio car_demand_forecasting. Lee CLAUDE.md, docs/diseno.md (Analítica 1, Supervivencia del stock, con las Lecciones del traspaso de KROL, y Analítica 4, Relación entre VN y VO) y vendor/krol/ (Traspaso_Supervivencia_Stock_VO.md es la guía; r9b y r9c son el código de referencia). vendor/krol es solo lectura: copia y adapta a analitica/supervivencia/, no lo modifiques.

Hito 3. Construye, para VN y VO por separado (parámetro tipo_stock en todas las funciones):
1. episodios.py: un episodio por vehículo con el contrato del traspaso: vehicle_id, episode_id, published_at, observed_from (primera captura que lo ve, para la entrada tardía), end_at, event (1 solo con venta confirmada; baja sin venta = 0), precio inicial y actual, y los atributos de celda: versión, modelo y carrocería en VN; modelo, combustible, año y kilómetros en VO. Marca aparte las ventas detectadas en bloque tras un hueco de ingesta y exclúyelas de las curvas.
2. curvas.py: Kaplan-Meier con entry=observed_from y caída jerárquica cuando una celda no llega a MIN_EPISODIOS y MIN_VENTAS (config): en VN, versión → modelo → carrocería → todo; en VO, la jerarquía L1–L4 de r9b. Para cada celda: S(t), RMST a 90 días, percentiles 25/50/75 si existen, episodios, ventas, intervalo y nivel usado. No exijas la mediana.
3. probabilidad.py: para cada vehículo en stock, P(salida en 30, 60 y 90 días | lleva a días) = 1 − S(a+h)/S(a); ventas esperadas por modelo como suma de probabilidades; stock previsto = actual − salidas esperadas + entradas previstas. Entradas previstas: en VN, media de los últimos 3 meses por modelo; en VO, retomas esperadas (tasa de retoma por modelo VN aplicada a las ventas VN esperadas) más las compras directas medias.
4. depreciacion.py: en VN, caída del precio frente al inicial según días en stock, por modelo, sin coste financiero. En VO, curva de precio por modelo y edad con los precios de Walcu y del stock externo VO del scraper, usando el método de KROL como referencia.
5. vn_vo.py: por modelo y mes, en relacion_vn_vo: tasa de retoma, brecha de precio entre el VN de la versión y el VO de menos de 2 años de la misma versión (propio y del mercado, este último desde stock_externo_snapshot) y días de stock medianos de VN frente a VO. Solo descriptivo en este hito.
6. Todo se recalcula cada noche solo con lo conocido hasta ese día y se escribe en supervivencia_diaria con fecha de cálculo y tipo_stock.
7. validacion.py: backtest con cortes mensuales en los últimos 12 meses (los meses que haya en Walcu, en VN y en VO): curvas con datos hasta el corte, ventas esperadas a 30 días por modelo frente a las reales y calibración por deciles (Brier). Referencia: la tasa media de salida del modelo. Informe en analitica/supervivencia/validacion.md, con VN y VO en tablas separadas.
8. Deja preparado para el hito 5 el formato largo de tramos semanales (id, start, stop, event, covariables as-of) siguiendo r9c_time_varying.py: posición de precio frente a la mediana del stock externo comparable y brecha VN–VO, ambas calculadas solo con lo conocido en cada fecha.
9. Stock externo desde anuncios_externos, activado por config con al menos 4 meses de capturas: allí todo final es censura, porque desaparecer no es vender. Hasta entonces, solo recuentos y antigüedad de los anuncios.

Termina con las decisiones tomadas sin preguntar, las celdas que caen a niveles superiores de la jerarquía y los episodios y ventas disponibles en VN y en VO.
```

**Revisión de Cayetano**: comprobar que los episodios usan `entry` y que ninguna covariable mira al futuro; revisar el backtest y la calibración; contrastar las curvas de dos versiones conocidas con el jefe de ventas; ver qué porcentaje de vehículos cae a modelo o carrocería.

## Hito 4 · Panel y alertas por KAIROS (semanas 8–10)

**Objetivo**: el motor de reglas con las alertas del documento de diseño, el panel completo con las cuatro vistas del prototipo aprobado y el envío por KAIROS. Es el primer entregable que usa la dirección.

**Puerta**: piloto de dos semanas con dirección y jefes de ventas, recuento de alertas útiles frente a falsos positivos y umbrales ajustados antes de abrir el panel a marketing.

**Tareas de Cayetano antes de la sesión**:

- Umbrales iniciales y destinatarios de cada alerta, a partir de la propuesta de la presentación.
- Interfaz de KAIROS: endpoint o cola, formato del mensaje y canal de Telegram por rol.
- Lista de usuarios del panel y su rol (dirección general, jefe de ventas, marketing).

**Prompt**:

```
Repositorio car_demand_forecasting. Lee CLAUDE.md, docs/diseno.md (Señales y alertas, Panel diario) y alertas/umbrales.yaml.

Hito 4. Construye:
1. alertas/motor.py: cada mañana a las 09:15, después de las cargas, evalúa las reglas de la tabla de alertas del diseño (vehículo envejecido, riesgo de inmovilización, oportunidad de campaña, caída de leads, coste por lead fuera de rango, campaña sin citas, ventas fuera de la banda prevista, presión de un competidor, scraper o fuente caída). Umbrales en YAML, nunca en código. Cada alerta lleva su motivo en una frase con los números que la disparan y se guarda en la tabla alertas con fecha, destinatario y estado.
2. alertas/kairos.py: adaptador que entrega a KAIROS las alertas del día, agrupadas en un mensaje por destinatario y sin repetir las ya enviadas. Si KAIROS no responde, reintenta y lo deja en fuentes_estado.
3. panel/: aplicación web (FastAPI con plantillas y un frontend ligero) detrás de Caddy en el subdominio del .env, con usuario y contraseña por persona y las cuatro vistas de docs/prototipo/ (Dirección, Ventas, Marketing y Mercado, esta última ya hecha en el hito 1b), con el selector VN/VO/Todo y la versión móvil del prototipo:
   - dirección general: stock VN y VO, antigüedad media, ventas del mes frente a la banda prevista y alertas abiertas;
   - jefe de ventas: vehículos ordenados por riesgo con su probabilidad de salida a 90 días y la curva de su celda;
   - marketing: coste por lead, cita y venta por campaña y la atención relativa por modelo frente a sus competidores, con la nota de que es una señal direccional.
   En todas, el estado de las fuentes y la hora de la última actualización, incluidos los tres scrapers.
4. El navegador nunca recibe claves ni tokens; el panel solo lee de Postgres a través del servidor.
5. Pruebas de cada regla con casos sintéticos que la disparan y que no la disparan.
6. Banda de ventas previstas de Dirección: de momento, una referencia sencilla (ritmo del mismo mes en años anteriores con su error empírico), en una función aislada que el hito 5 sustituirá por el modelo.
7. Recomendación de Marketing (aumentar, mantener, reducir): reglas en YAML sobre coste por venta, stock envejecido, atención relativa y tendencia de leads, con el motivo en una frase. Sin cantidades: eso llega en el hito 6.
8. Botones «Pedir campaña a marketing» y «Revisar precio» de Ventas: envían un aviso por KAIROS (a marketing o al jefe de ventas) y dejan el vehículo marcado en la tabla acciones. No escriben en Walcu.

Termina con las decisiones tomadas sin preguntar.
```

**Revisión de Cayetano**: probar cada vista con un usuario de cada rol; forzar una caída de un scraper y comprobar que llega la alerta; durante el piloto, anotar cada alerta como útil o falso positivo y ajustar `umbrales.yaml`.

## Hito 5 · Predicción de ventas (semanas 11–14)

**Objetivo**: el modelo de embudo, Cox con el stock externo y la brecha VN–VO como covariables, el modelo jerárquico bayesiano de ventas y la confirmación prerregistrada de la atención relativa con datos de 2026.

**Puerta**: cada modelo bate a su referencia simple con origen móvil y Cayetano lo acepta o rechaza por separado. La atención relativa entra solo si pasa la confirmación.

**Tareas de Cayetano antes de la sesión**: revisión metodológica de cada modelo; URL del registro en OSF; decidir si la pérdida es simétrica o pesa más quedarse corto de stock.

**Prompt**:

```
Repositorio car_demand_forecasting. Lee CLAUDE.md, docs/diseno.md (Analítica 2, 3 y 4), docs/prerregistro_osf.md y test_senales_demanda/08_competidores/prerregistro_competidores.md.

Hito 5. Construye en analitica/:
1. embudo.py: visitas → leads → pruebas → pedidos → entregas por modelo, con tasas de conversión y retardos estimados con el histórico de Walcu y GA4. Predicción a 30, 60 y 90 días con intervalos.
2. cox.py: Cox con covariables que cambian en el tiempo sobre los tramos semanales del hito 3 (CoxTimeVaryingFitter): precio relativo a la mediana del stock externo comparable, stock externo de la misma versión, brecha VN–VO y mes. Validación con origen móvil frente a Kaplan-Meier, VN y VO por separado.
3. bayes_ventas.py: modelo jerárquico versión → modelo → marca con binomial negativa (PyMC), ventas a 30, 60 y 90 días. Compara con la referencia estacional ingenua y con el embudo. Informe de validación con origen móvil.
4. confirmacion_2026.py: ejecuta exactamente lo que dice docs/prerregistro_osf.md sobre los datos de Google Trends de 2026, sin cambiar un parámetro. Métrica principal: precisión direccional con nulo de permutación; secundaria: MAE. Escribe el veredicto en docs/confirmacion_2026.md. Solo si es favorable, añade la atención relativa como covariable a bayes_ventas.py, en una rama aparte.
5. Las predicciones se guardan con su fecha de origen, para que la alerta de desvío de ventas compare con la predicción anterior.

Termina con las decisiones tomadas sin preguntar y con lo que no bate a la referencia.
```

**Revisión de Cayetano**: revisar los informes de validación; aceptar o rechazar cada modelo por separado; decidir si la atención relativa entra según el veredicto de la confirmación.

## Hito 6 · Presupuesto de campañas (con 6 meses de historial de Meta)

**Objetivo**: recomendar cuánto invertir por modelo y semana, con curvas de respuesta de rendimientos decrecientes y un grupo de control que mida el efecto real de las campañas.

**Puerta**: al menos seis meses de inversión y resultados en `campanas_meta` y un diseño de semanas de prueba aprobado por dirección y marketing.

**Tareas de Cayetano antes de la sesión**: acordar con marketing las semanas de prueba con presupuesto variado y qué modelos quedan como control en cada una.

**Prompt**: se redacta al llegar, con lo aprendido en los hitos anteriores. Su núcleo será:

- una curva de respuesta por modelo (leads y ventas frente a inversión) con rendimientos decrecientes, estimada con las semanas de prueba;
- el efecto incremental medido frente al grupo de control, no frente a lo que habría pasado según el modelo;
- una recomendación semanal de aumentar, mantener o reducir, cruzada con la probabilidad de salida del stock del hito 3.

## Calendario y pendientes

[embedded content: calendario de hitos · semanas orientativas]

Las semanas cuentan desde que el servidor esté listo. Los scrapers no paran entre hitos, y el stock externo tendrá sus primeras curvas hacia la semana 17, con 4 meses de capturas.

Pendientes antes de arrancar:

- [ ] Elegir proveedor (propuesta: Hetzner) y crear proyecto, servidor, bucket y subdominio
- [ ] Revisar `robots.txt` y las condiciones de uso de la web de stock de VW
- [ ] Decidir alcance (propuesta: toda España) y frecuencia del scraping
- [ ] Dejar los dos scrapers en `scrapers/vendor/`
- [ ] Confirmar con Walcu el histórico de VO disponible
- [ ] Reunión con Walcu el lunes 16 de noviembre: credenciales de la API y lista de campos (el lead lleva UTM)
- [ ] Copiar a vendor/krol/ el código del traspaso de supervivencia (solo código, sin datos)
- [x] Respuesta del jefe de ventas: lista de competidores cerrada el 14 de noviembre
- [ ] Prerregistro en OSF antes de descargar Google Trends de 2026
- [ ] Accesos de GA4 y Meta en solo lectura
- [ ] Interfaz de KAIROS y canal de Telegram por rol
