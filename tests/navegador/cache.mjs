// Caché del navegador ACTIVADA: tras cambiar un estático, la siguiente carga usa el nuevo sin forzar la recarga.
//
// 1. make panel-dev                       (en otra terminal: panel en http://localhost:8000)
// 2. "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless=new \
//      --remote-debugging-port=9333 --user-data-dir=$(mktemp -d) about:blank &
// 3. node tests/navegador/cache.mjs 9333 tecnico prueba-tecnico-local panel/static
//
// Pasos: carga Mercado; vuelve a cargarlo y comprueba que el CSS, el JS, htmx, ECharts y las
// fuentes salen de la caché sin pedirse al panel (huella vigente: un año e immutable). Después
// añade una línea a css/panel.css y a js/panel.js (como un despliegue), carga otra vez con la misma
// caché y comprueba que la página pide las URL nuevas y aplica el CSS y el JS nuevos. Deja los dos
// ficheros como estaban. Sale con código 1 si algo falla.
import fs from "node:fs";
import path from "node:path";
const [puerto, usuario, clave, estaticos] = process.argv.slice(2);
const BASE = "http://localhost:8000";
const lista = await (await fetch(`http://127.0.0.1:${puerto}/json/new?about:blank`, { method: "PUT" })).json();
const ws = new WebSocket(lista.webSocketDebuggerUrl);
await new Promise(r => ws.addEventListener("open", r));
let id = 0; const pend = new Map(); const errores = [];
let pedidas = new Map();     // requestId -> {url, red: true si fue a la red}
ws.addEventListener("message", ev => {
  const m = JSON.parse(ev.data);
  if (m.id && pend.has(m.id)) { pend.get(m.id)(m); pend.delete(m.id); }
  if (m.method === "Network.requestWillBeSent" && m.params.request.url.includes("/static/"))
    pedidas.set(m.params.requestId, { url: m.params.request.url.replace(BASE, ""), red: true });
  if (m.method === "Network.requestServedFromCache" && pedidas.has(m.params.requestId)) pedidas.get(m.params.requestId).red = false;
  if (m.method === "Network.responseReceived" && pedidas.has(m.params.requestId)) {
    const r = m.params.response;
    if (r.fromDiskCache || r.fromMemoryCache) pedidas.get(m.params.requestId).red = false;
  }
  if (m.method === "Runtime.exceptionThrown") errores.push(m.params.exceptionDetails.text);
  if (m.method === "Log.entryAdded" && m.params.entry.level === "error") errores.push(m.params.entry.text);
});
const cdp = (method, params = {}) => new Promise(r => { const i = ++id; pend.set(i, r); ws.send(JSON.stringify({ id: i, method, params })); });
const espera = ms => new Promise(r => setTimeout(r, ms));
const evalua = async expr => (await cdp("Runtime.evaluate", { expression: expr, awaitPromise: true, returnByValue: true })).result?.result?.value;
await cdp("Page.enable"); await cdp("Runtime.enable"); await cdp("Log.enable");
await cdp("Network.enable"); await cdp("Network.setCacheDisabled", { cacheDisabled: false });   // caché ACTIVADA
await cdp("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1, mobile: false });
async function ir(url) { pedidas = new Map(); await cdp("Page.navigate", { url }); await espera(2000); return [...pedidas.values()]; }
const estado = () => evalua(`JSON.stringify({
  css: document.querySelector('link[rel=stylesheet]').getAttribute('href'),
  js: [...document.scripts].map(s => s.getAttribute('src')).find(s => s && s.includes('/js/panel.js')),
  marcaCss: getComputedStyle(document.documentElement).getPropertyValue('--prueba-cache').trim(),
  marcaJs: window.__pruebaCache || "",
  grafico: !!document.querySelector('[data-serie] canvas'),
  fuente: document.fonts.check('16px Inter')})`).then(JSON.parse);

const fallos = [];
const comprueba = (ok, texto) => { console.log(ok ? "  bien:" : "  MAL: ", texto); if (!ok) fallos.push(texto); };
const ficheros = { css: path.join(estaticos, "css/panel.css"), js: path.join(estaticos, "js/panel.js") };
const originales = Object.fromEntries(Object.entries(ficheros).map(([k, f]) => [k, fs.readFileSync(f)]));
try {
  await ir(`${BASE}/acceso`);
  await evalua(`document.getElementById("usuario").value = ${JSON.stringify(usuario)}; document.getElementById("clave").value = ${JSON.stringify(clave)}; document.querySelector("form.campos").submit(); true`);
  await espera(2000);

  console.log("1. Primera carga");
  await ir(`${BASE}/mercado`);
  const v1 = await estado();
  console.log("  ", v1.css, v1.js);
  comprueba(/\?v=[0-9a-f]{12}$/.test(v1.css) && /\?v=[0-9a-f]{12}$/.test(v1.js), "CSS y JS con huella en la URL");
  comprueba(v1.grafico && v1.fuente && v1.marcaCss === "" && v1.marcaJs === "", "estilos, fuente y gráfico de la versión 1");

  console.log("2. Segunda carga, sin cambios: todo de la caché");
  const segunda = await ir(`${BASE}/mercado?zona=espana`);
  const deRed = segunda.filter(p => p.red).map(p => p.url);
  comprueba(segunda.length >= 5 && deRed.length === 0, `${segunda.length} estáticos, de la red: ${deRed.length ? deRed.join(", ") : "ninguno"}`);

  console.log("3. Despliegue: cambian panel.css y panel.js");
  const marca = "v" + Date.now();
  fs.writeFileSync(ficheros.css, Buffer.concat([originales.css, Buffer.from(`\n:root { --prueba-cache: ${marca}; }\n`)]));
  fs.writeFileSync(ficheros.js, Buffer.concat([originales.js, Buffer.from(`\nwindow.__pruebaCache = "${marca}";\n`)]));
  const tercera = await ir(`${BASE}/mercado?zona=comunidad`);
  const v2 = await estado();
  console.log("  ", v2.css, v2.js);
  comprueba(v2.css !== v1.css && v2.js !== v1.js, "las URL del CSS y del JS han cambiado");
  comprueba(v2.marcaCss === marca, `el CSS aplicado es el nuevo (--prueba-cache: ${v2.marcaCss || "vacío"})`);
  comprueba(v2.marcaJs === marca, `el JS que corre es el nuevo (${v2.marcaJs || "vacío"})`);
  const nuevas = tercera.filter(p => p.red).map(p => p.url.split("?")[0]);
  comprueba(nuevas.includes("/static/css/panel.css") && nuevas.includes("/static/js/panel.js"), `de la red solo lo nuevo: ${nuevas.join(", ")}`);
  comprueba(!nuevas.some(u => u.includes("/vendor/") || u.includes("/fonts/")), "htmx, ECharts y las fuentes siguen saliendo de la caché");
  comprueba(v2.grafico && v2.fuente, "gráfico y fuente siguen bien");
} finally {
  for (const [k, f] of Object.entries(ficheros)) fs.writeFileSync(f, originales[k]);
}
comprueba(errores.length === 0, `errores de consola/CSP: ${errores.length ? errores.join(" | ") : "ninguno"}`);
console.log(fallos.length ? "RESULTADO: FALLA" : "RESULTADO: correcto");
ws.close(); process.exit(fallos.length ? 1 : 0);
