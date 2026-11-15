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
- Replicable: la aplicación se instalará en otros concesionarios Volkswagen con Walcu, una instancia aislada por concesionario. Nada propio de AVISA va en el código ni en las plantillas: nombre del concesionario, código de concesionario, provincia y comunidad, marca, dominio, chats de Telegram, umbrales y textos como «Volkswagen Sevilla» o «Nuestro peso en Sevilla» salen de config/concesionario.yaml o del .env. La zona por defecto es la provincia del concesionario. Una prueba levanta el panel con un concesionario ficticio de otra provincia y comprueba que no aparece ni «AVISA» ni «Sevilla» en ninguna página. No se construye nada multicliente (ni tabla de clientes ni columna de concesionario): una instancia, un concesionario.
- Cada sesión entrega: diff revisable, pytest en verde, entrada en CHANGELOG.md y la lista de decisiones tomadas sin preguntar.
