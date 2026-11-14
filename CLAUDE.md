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
