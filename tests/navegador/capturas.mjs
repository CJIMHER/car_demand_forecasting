// Capturas y comprobaciones del panel en Chrome real (protocolo DevTools, sin Playwright ni dependencias).
//
// 1. make panel-dev                       (en otra terminal: panel en http://localhost:8000)
// 2. "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
//      --remote-debugging-port=9333 --user-data-dir=$(mktemp -d) about:blank &
// 3. node tests/navegador/capturas.mjs 9333 docs/capturas tecnico prueba-tecnico-local
//
// Entra con el formulario de acceso (con tecnico: con datos de ejemplo, Dirección, Ventas y
// Marketing solo las ve ese rol), comprueba los gráficos y el cambio de zona con htmx, y a 390 px,
// en cada zona y tipo de Mercado y en cada vista y filtro de Dirección, Ventas y Marketing, que el
// documento no desborda el ancho y que todas las celdas de cada fila de las tablas están en pantalla.
// En Ventas, tras pulsar un vehículo de mitad de lista, comprueba que su detalle queda en pantalla
// (1440 y 390 px). Guarda Mercado, Dirección, Ventas y Marketing (Todo, VN y VO) a 1440 px y a 390 px. Sale con código 1 si algo
// falla o si hay errores de CSP.
import fs from "node:fs";
const [puerto, salida, usuario, clave] = process.argv.slice(2);
const BASE = "http://localhost:8000";
const lista = await (await fetch(`http://127.0.0.1:${puerto}/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(lista.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pend = new Map(); const errores = [];
ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
  if (m.method === "Log.entryAdded" && ["error", "warning"].includes(m.params.entry.level)) errores.push(m.params.entry.text);
  if (m.method === "Runtime.exceptionThrown") errores.push(JSON.stringify(m.params.exceptionDetails.exception?.description || m.params.exceptionDetails.text));
  if (m.method === "Runtime.consoleAPICalled" && m.params.type === "error") errores.push(m.params.args.map(a => a.value).join(" "));
});
const cdp = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const espera = ms => new Promise(r => setTimeout(r, ms));
const evalua = async expr => (await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("Log.enable");
await cdp("Network.enable"); await cdp("Network.setCacheDisabled", { cacheDisabled: false });  // caché activada, como un usuario: los estáticos llevan huella
async function ir(url) { await cdp("Page.navigate", { url }); await espera(1500); }
async function tamano(ancho, movil) {
  await cdp("Emulation.setDeviceMetricsOverride", { width: ancho, height: movil ? 844 : 900, deviceScaleFactor: movil ? 2 : 1, mobile: movil });
}
async function captura(nombre) {
  await evalua("window.scrollTo(0, 0); true"); await espera(300);   // Chrome restaura el desplazamiento de visitas anteriores
  const alto = await evalua("Math.ceil(document.documentElement.scrollHeight)");
  const ancho = await evalua("document.documentElement.clientWidth");
  const movil = ancho < 500;
  if (movil) { await cdp("Emulation.setDeviceMetricsOverride", { width: ancho, height: alto, deviceScaleFactor: 2, mobile: true }); await espera(800); }
  const r = await cdp("Page.captureScreenshot", { format: "png", captureBeyondViewport: true, clip: { x: 0, y: 0, width: ancho, height: alto, scale: 1 } });
  fs.writeFileSync(`${salida}/${nombre}`, Buffer.from(r.result.data, "base64"));
  console.log("captura", nombre, ancho + "x" + alto);
}
await tamano(1440, false);
await ir(`${BASE}/acceso`);
await evalua(`document.getElementById("usuario").value = ${JSON.stringify(usuario)}; document.getElementById("clave").value = ${JSON.stringify(clave)}; document.querySelector("form.campos").submit(); true`);
await espera(2000);
console.log("tras entrar:", await evalua("location.pathname"));
await ir(`${BASE}/mercado`); await espera(1000);
console.log("gráfico pintado:", await evalua("!!document.querySelector('[data-serie] canvas')"));
await captura("mercado_escritorio_1440.png");
// htmx: cambiar de zona sin recargar
await evalua(`[...document.querySelectorAll('.segmento')].find(a => a.textContent.trim() === 'España').click(); true`);
await espera(1500);
console.log("htmx:", await evalua("location.search"), await evalua("document.querySelector('#t-modelos').textContent"), "gráfico:", await evalua("!!document.querySelector('[data-serie] canvas')"));
await tamano(390, true);
// Cada combinación de zona y tipo: nada desborda y todas las celdas de cada fila (8 por modelo,
// 5 por concesionario) están dentro de la pantalla.
const resultados = [];
for (const zona of ["provincia", "comunidad", "espana"]) for (const tipo of ["VN", "VO"]) {
  await ir(`${BASE}/mercado?zona=${zona}&tipo=${tipo}`);
  resultados.push(JSON.parse(await evalua(`(() => {
    const ancho = 390  /* innerWidth crece si algo desborda (emulación móvil) */, doc = document.documentElement;
    const tablas = {"tabla-modelos": 8, "tabla-concesionarios": 5};
    const malas = [], cuenta = {};
    for (const [clase, n] of Object.entries(tablas)) {
      const filas = [...document.querySelectorAll('.' + clase + ' tbody tr')];
      cuenta[clase] = filas.length;
      for (const tr of filas) {
        const celdas = [...tr.children];
        if (celdas.length !== n || celdas.some(c => { const r = c.getBoundingClientRect();
            return r.width === 0 || r.height === 0 || r.left < 0 || r.right > ancho + 0.5 || getComputedStyle(c).visibility === 'hidden'; }))
          malas.push(clase + ': ' + celdas[0].textContent.trim());
      }
    }
    return JSON.stringify({zona: ${JSON.stringify(zona)}, tipo: ${JSON.stringify(tipo)}, scrollWidth: doc.scrollWidth,
                           desborda: doc.scrollWidth > ancho, filas: cuenta, filasConDatosFuera: malas});
  })()`)));
}
// Dirección, Ventas y Marketing: nada desborda y las celdas de sus tablas están dentro de la pantalla
const VISTAS = ["/direccion", "/direccion?tipo=VN", "/ventas", "/ventas?tipo=VO&riesgo=1", "/marketing",
                "/marketing?orden=gasto", "/marketing?tipo=VN", "/marketing?tipo=VO&orden=gasto"];
for (const ruta of VISTAS) {
  await ir(`${BASE}${ruta}`);
  resultados.push(JSON.parse(await evalua(`(() => {
    const ancho = 390  /* innerWidth crece si algo desborda (emulación móvil) */, doc = document.documentElement, malas = [];
    for (const tr of document.querySelectorAll('.tabla-movil tbody tr')) {
      // cada celda y todo su contenido (p. ej. una etiqueta que no cabe) dentro de la tabla y de la pantalla
      const caja = (tr.closest('.tabla-scroll') || tr).getBoundingClientRect();
      for (const c of [...tr.children, ...tr.querySelectorAll('*')]) { const r = c.getBoundingClientRect();
        if (r.width > 0 && (r.left < Math.max(0, caja.left) - 0.5 || r.right > Math.min(ancho, caja.right) + 0.5)) {
          malas.push(tr.children[0].textContent.trim().slice(0, 40)); break; } }
    }
    const fuera = [...document.querySelectorAll('.tarjeta, .kpi, .resumen-tarjeta')].filter(e => {
      const r = e.getBoundingClientRect(); return r.width > 0 && (r.left < 0 || r.right > ancho + 0.5); }).length;
    return JSON.stringify({ruta: ${JSON.stringify(ruta)}, scrollWidth: doc.scrollWidth, desborda: doc.scrollWidth > ancho,
      filas: {"tabla-modelos": 1}, tarjetasFuera: fuera, filasConDatosFuera: malas,
      graficos: document.querySelectorAll('[data-ventas] canvas, [data-curva] canvas').length,
      etiquetas: document.querySelectorAll('.etiqueta-ejemplo').length, banda: !!document.querySelector('.banda-ejemplo')});
  })()`)));
}
// Escritorio (1440 px): ninguna celda de una tabla se sale de su tarjeta (p. ej. la tabla estrecha de VO)
const escritorio = [];
await tamano(1440, false);
for (const ruta of VISTAS) {
  await ir(`${BASE}${ruta}`);
  escritorio.push(JSON.parse(await evalua(`(() => {
    const malas = [];
    for (const t of document.querySelectorAll('.tabla')) {
      const caja = (t.closest('.tabla-scroll') || t.closest('.tarjeta') || t).getBoundingClientRect();
      for (const c of t.querySelectorAll('th, td')) { const r = c.getBoundingClientRect();
        if (r.width > 0 && (r.left < caja.left - 0.5 || r.right > caja.right + 0.5)) { malas.push(c.textContent.trim().slice(0, 30)); break; } }
    }
    return JSON.stringify({ruta: ${JSON.stringify(ruta)}, celdasFuera: malas,
      desborda: document.documentElement.scrollWidth > document.documentElement.clientWidth});
  })()`)));
}
for (const r of escritorio) console.log("escritorio 1440:", JSON.stringify(r));
await tamano(390, true);
// Ventas: tras pulsar un vehículo de mitad de lista (fila 13 de 25), su detalle queda dentro de la
// pantalla: en escritorio, el panel fijo a la vista; en el móvil, la fila que se abre bajo el vehículo.
const detalles = [];
for (const [ancho, movil] of [[1440, false], [390, true]]) {
  await tamano(ancho, movil); await ir(`${BASE}/ventas`);
  const id = await evalua(`(() => { const a = document.querySelectorAll('.tabla-ventas a.vehiculo')[12];
    a.scrollIntoView({block: 'center'}); return a.querySelector('.vehiculo-detalle').textContent.split(' · ').pop(); })()`);
  await espera(400);
  await evalua(`document.querySelectorAll('.tabla-ventas a.vehiculo')[12].click(); true`);
  await espera(1500);
  detalles.push(JSON.parse(await evalua(`(() => {
    const el = ${movil} ? document.querySelector('tr.detalle-movil') : document.querySelector('.tarjeta-detalle');
    const r = el ? el.getBoundingClientRect() : null, alto = window.innerHeight;
    return JSON.stringify({ancho: ${ancho}, elegido: ${JSON.stringify(id)}, url: location.search,
      deEse: !!el && el.textContent.includes(${JSON.stringify(id)}), arriba: r && Math.round(r.top), alto,
      dentro: !!r && r.top >= 0 && r.top < alto - 100, curva: !!el && !!el.querySelector('[data-curva] canvas'),
      scrollY: Math.round(window.scrollY)});
  })()`)));
}
for (const d of detalles) console.log("detalle tras pulsar:", JSON.stringify(d));
for (const r of resultados) console.log("móvil 390:", JSON.stringify(r));
const comprobacion = JSON.stringify({
  desborda: resultados.some(r => r.desborda),
  filas: Math.min(...resultados.map(r => r.filas["tabla-modelos"])),
  sinConcesionariosEnProvincia: resultados.some(r => r.zona === "provincia" && r.filas["tabla-concesionarios"] === 0),
  filasConDatosFuera: resultados.flatMap(r => r.filasConDatosFuera),
  tarjetasFuera: resultados.reduce((n, r) => n + (r.tarjetasFuera || 0), 0),
  sinBanda: resultados.filter(r => r.ruta && !r.banda).map(r => r.ruta),
  sinGrafico: resultados.filter(r => r.ruta && /direccion|ventas/.test(r.ruta) && r.graficos === 0).map(r => r.ruta),
  escritorioFuera: escritorio.filter(r => r.celdasFuera.length || r.desborda).map(r => r.ruta),
  detalleFuera: detalles.filter(d => !d.dentro || !d.deEse || !d.curva || d.scrollY === 0).map(d => d.ancho),
});
await ir(`${BASE}/mercado`); await espera(800);
await captura("mercado_movil_390.png");
const PAGINAS = {direccion: "direccion", ventas: "ventas", marketing: "marketing",
                 marketing_vn: "marketing?tipo=VN", marketing_vo: "marketing?tipo=VO"};
for (const [v, ruta] of Object.entries(PAGINAS)) {
  await tamano(390, true); await ir(`${BASE}/${ruta}`); await espera(800);
  await captura(`${v}_movil_390.png`);
}
await tamano(1440, false);
for (const [v, ruta] of Object.entries(PAGINAS)) {
  await ir(`${BASE}/${ruta}`); await espera(800);
  await captura(`${v}_escritorio_1440.png`);
}
await tamano(390, true); await ir(`${BASE}/mercado`); await espera(500);
const zonas = await evalua(`JSON.stringify([...document.querySelectorAll('.segmento, .nav-movil-item, .boton-salir')].filter(e => getComputedStyle(e).display !== 'none').map(e => Math.round(e.getBoundingClientRect().height)))`);
console.log("altos de zonas táctiles (px):", zonas);
console.log("errores de consola/CSP:", errores.length ? errores : "ninguno");
const c = JSON.parse(comprobacion);
const mal = errores.length > 0 || c.desborda || c.filas === 0 || c.sinConcesionariosEnProvincia ||
  c.filasConDatosFuera.length > 0 || c.tarjetasFuera > 0 || c.escritorioFuera.length > 0 || c.sinBanda.length > 0 || c.sinGrafico.length > 0 || c.detalleFuera.length > 0;
console.log("comprobación:", comprobacion);
console.log(mal ? "RESULTADO: FALLA" : "RESULTADO: correcto");
ws.close(); process.exit(mal ? 1 : 0);
