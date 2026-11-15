// Capturas y comprobaciones del panel en Chrome real (protocolo DevTools, sin Playwright ni dependencias).
//
// 1. make panel-dev                       (en otra terminal: panel en http://localhost:8000)
// 2. "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
//      --remote-debugging-port=9333 --user-data-dir=$(mktemp -d) about:blank &
// 3. node tests/navegador/capturas.mjs 9333 docs/capturas direccion prueba-direccion-local
//
// Entra con el formulario de acceso, comprueba el gráfico y el cambio de zona con htmx, y a 390 px
// que el documento no desborda el ancho y que los siete datos de cada modelo están en pantalla.
// Guarda Mercado a 1440 px y a 390 px. Sale con código 1 si algo falla o si hay errores de CSP.
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
async function ir(url) { await cdp("Page.navigate", { url }); await espera(1500); }
async function tamano(ancho, movil) {
  await cdp("Emulation.setDeviceMetricsOverride", { width: ancho, height: movil ? 844 : 900, deviceScaleFactor: movil ? 2 : 1, mobile: movil });
}
async function captura(nombre) {
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
await ir(`${BASE}/mercado`); await espera(1200);
const comprobacion = await evalua(`(() => {
  const ancho = window.innerWidth, doc = document.documentElement;
  const filas = [...document.querySelectorAll('.tabla-modelos tbody tr')];
  const malas = filas.filter(tr => {
    const celdas = [...tr.children];
    return celdas.length !== 7 || celdas.some(c => { const r = c.getBoundingClientRect();
      return r.width === 0 || r.height === 0 || r.left < 0 || r.right > ancho + 0.5 || getComputedStyle(c).visibility === 'hidden'; });
  }).map(tr => tr.children[0].textContent);
  return JSON.stringify({ancho, scrollWidth: doc.scrollWidth, desborda: doc.scrollWidth > ancho, filas: filas.length, filasConDatosFuera: malas});
})()`);
console.log("móvil 390:", comprobacion);
for (const t of ["VO"]) { await ir(`${BASE}/mercado?zona=espana&tipo=${t}`); console.log("móvil 390 VO España:", await evalua(`JSON.stringify({scrollWidth: document.documentElement.scrollWidth, ancho: innerWidth})`)); }
await ir(`${BASE}/mercado`); await espera(800);
await captura("mercado_movil_390.png");
const zonas = await evalua(`JSON.stringify([...document.querySelectorAll('.segmento, .nav-movil-item, .boton-salir')].filter(e => getComputedStyle(e).display !== 'none').map(e => Math.round(e.getBoundingClientRect().height)))`);
console.log("altos de zonas táctiles (px):", zonas);
console.log("errores de consola/CSP:", errores.length ? errores : "ninguno");
const c = JSON.parse(comprobacion);
const mal = errores.length > 0 || c.desborda || c.filas === 0 || c.filasConDatosFuera.length > 0;
console.log(mal ? "RESULTADO: FALLA" : "RESULTADO: correcto");
ws.close(); process.exit(mal ? 1 : 0);
