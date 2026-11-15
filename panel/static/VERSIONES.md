# Recursos de terceros del panel

Se sirven desde el propio panel: el navegador no pide nada a otros dominios. Versiones fijas,
descargadas de npm (vía cdn.jsdelivr.net) el 15-11-2026. Cada carpeta lleva su licencia.

| Recurso | Versión | Fichero | Licencia | SHA-256 |
| --- | --- | --- | --- | --- |
| htmx | htmx.org 2.0.4 | vendor/htmx/htmx.min.js | 0BSD (vendor/htmx/LICENSE) | e209dda5c8235479f3166defc7750e1dbcd5a5c1808b7792fc2e6733768fb447 |
| echarts | echarts 5.5.1 | vendor/echarts/echarts.min.js | Apache-2.0 (vendor/echarts/LICENSE, NOTICE) | e84270bd0cd5bdf60fefc26d00c2a391cb2e81f4d26a7a9ee16185a54773a3cf |
| inter | @fontsource-variable/inter 5.1.0 (Inter, latín, variable) | fonts/inter/inter-latin-wght-normal.woff2 | SIL OFL 1.1 (fonts/inter/LICENSE) | f052ee44c3728dfd23aba8a4567150bc314d23903026fbb6ad089422c2df56af |
| space-grotesk | @fontsource-variable/space-grotesk 5.1.0 (latín, variable) | fonts/space-grotesk/space-grotesk-latin-wght-normal.woff2 | SIL OFL 1.1 (fonts/space-grotesk/LICENSE) | 7d707172ce856c4ce5413ba9adb35483106a2f1ad9b4fdd285f3b1540cb4f7e4 |

Para actualizar uno: descargar la nueva versión fija con su licencia, sustituir el fichero,
actualizar esta tabla y pasar las pruebas (tests/test_panel.py comprueba que nada viene de fuera).
