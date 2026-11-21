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
// (1440 y 390 px). En el detalle de un modelo (VN y VO, varias zonas y filtros de la matriz), con los
// acabados desplegados, que nada desborda ni se sale de su tarjeta a 1440 y 390 px, y que el enlace de
// la tabla por modelo lleva al detalle con la misma zona y tipo. Pulsa los filtros de la matriz
// (acabado, combustible, motor) y cambia de tipo y de zona: los tres viajan en la URL. Guarda Mercado, el detalle de un
// modelo (VN, VO y VO en la provincia), Dirección, Ventas y Marketing (Todo, VN y VO) a 1440 px y a
// 390 px. Sale con código 1 si algo falla o si hay errores de CSP.
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
// Detalle de un modelo (Mercado → pulsar un modelo), a 1440 y a 390 px, con todos los acabados
// desplegados: nada desborda y cada cifra de las filas por acabado y motor y de la matriz de
// antigüedad y km queda dentro de su tarjeta y de la pantalla.
const abrir0 = "document.querySelectorAll('details.seg-acabado').forEach((d, i) => { d.open = i < 2; }); true";
const DETALLES_MODELO = ["/mercado/modelo/T-Roc?tipo=VN&zona=espana", "/mercado/modelo/T-Roc?tipo=VO&zona=espana",
  "/mercado/modelo/T-Roc?tipo=VO&zona=provincia", "/mercado/modelo/Golf?tipo=VO&zona=espana&acabado=Life",
  "/mercado/modelo/Golf?tipo=VO&zona=espana&combustible=Di%C3%A9sel",
  "/mercado/modelo/ID.4?tipo=VN&zona=comunidad"];
const modeloRes = [];
const medirModelo = ancho => `(() => {
      document.querySelectorAll('details.seg-acabado').forEach(d => { d.open = true; });
      const ancho = ${ancho}, doc = document.documentElement, malas = [];
      for (const el of document.querySelectorAll('.seg-fila > *, .seg-fila .rango, .matriz th, .matriz td, .chip-filtro, .filtro-nombre')) {
        const caja = el.closest('.tarjeta').getBoundingClientRect(), r = el.getBoundingClientRect();
        if (r.width > 0 && (r.left < Math.max(0, caja.left) - 0.5 || r.right > Math.min(ancho, caja.right) + 0.5))
          malas.push(el.textContent.trim().slice(0, 40));
      }
      // ningún texto más ancho que su casilla (p. ej. «pocos anuncios» montado sobre la columna de al lado)
      for (const el of document.querySelectorAll('.seg-c, .matriz td, .chip-filtro'))
        if (el.getBoundingClientRect().width > 0 && el.scrollWidth > el.clientWidth + 1) malas.push('ancho: ' + el.textContent.trim().slice(0, 40));
      return JSON.stringify({ancho, ruta: decodeURIComponent(location.pathname + location.search), scrollWidth: doc.scrollWidth,
        desborda: doc.scrollWidth > ancho,
        acabados: document.querySelectorAll('details.seg-acabado').length, motores: document.querySelectorAll('.seg-motor').length,
        matriz: document.querySelectorAll('.matriz td').length, notaEspana: !!document.querySelector('.aviso-zona'),
        filtros: [...document.querySelectorAll('[data-filtro]')].map(g => g.dataset.filtro + ' ' + g.querySelectorAll('.chip-filtro').length),
        titulo: document.querySelector('#t-matriz')?.textContent.trim(), fuera: malas});
    })()`;
for (const [ancho, movil] of [[1440, false], [390, true]]) {
  await tamano(ancho, movil);
  for (const ruta of DETALLES_MODELO) {
    await ir(`${BASE}${ruta}`);
    modeloRes.push(JSON.parse(await evalua(medirModelo(ancho))));
  }
}
// Filtros de la matriz con htmx: acabado → combustible → motor (cada chip ofrece solo lo que existe
// con lo ya elegido), luego cambio de zona y de tipo y vuelta: los tres viajan en la URL.
const pulsa = async (selector, i) => { await evalua(`document.querySelectorAll(${JSON.stringify(selector)})[${i}].click(); true`); await espera(1300); };
const filtrosRes = [];
for (const [ancho, movil] of [[1440, false], [390, true]]) {
  await tamano(ancho, movil); await ir(`${BASE}/mercado/modelo/T-Roc?tipo=VO&zona=espana`);
  await pulsa('[data-filtro="acabado"] .chip-filtro', 1);
  await pulsa('[data-filtro="combustible"] .chip-filtro', 1);
  await pulsa('[data-filtro="motor"] .chip-filtro', 1);
  const elegida = await evalua("decodeURIComponent(location.search)"), direccion = await evalua("location.href");
  const medida = JSON.parse(await evalua(medirModelo(ancho)));
  modeloRes.push(medida);
  await pulsa('[aria-label="Tipo de stock"] .segmento', 0);     // a VN
  const enVN = await evalua("decodeURIComponent(location.search)");
  await pulsa('[aria-label="Tipo de stock"] .segmento', 1);     // y de vuelta a VO
  const vuelta = await evalua("decodeURIComponent(location.search)");
  await pulsa('[aria-label="Zona"] .segmento', 1);              // a la comunidad
  const enComunidad = await evalua("decodeURIComponent(location.search)");
  modeloRes.push(JSON.parse(await evalua(medirModelo(ancho))));
  const p = new URLSearchParams(elegida);
  filtrosRes.push({ancho, elegida, enVN, vuelta, enComunidad, titulo: medida.titulo,
    tres: ["acabado", "combustible", "motor"].every(k => p.get(k)),
    enTitulo: medida.titulo.includes(p.get("acabado")) && medida.titulo.includes(p.get("motor")),
    // en VN y en la comunidad cada filtro se conserva o se cae (si allí no existe), nunca cambia de valor
    coherente: [enVN, vuelta, enComunidad].every(q => { const x = new URLSearchParams(q);
      return ["acabado", "combustible", "motor"].every(k => !x.get(k) || x.get(k) === p.get(k)); })});
  await ir(direccion);
  await evalua(abrir0); await espera(300);
  await captura(`modelo_vo_filtros_${movil ? "movil_390" : "escritorio_1440"}.png`);
}
for (const r of filtrosRes) console.log("filtros de la matriz:", JSON.stringify(r));
for (const r of modeloRes) console.log("detalle de modelo:", JSON.stringify(r));
// Desde la tabla por modelo: el enlace lleva al detalle con la misma zona y tipo
await tamano(1440, false); await ir(`${BASE}/mercado?zona=comunidad&tipo=VO`);
await evalua(`document.querySelector('.tabla-modelos a.enlace-modelo').click(); true`); await espera(1500);
const enlaceModelo = await evalua("location.pathname + location.search");
console.log("enlace desde la tabla:", enlaceModelo);
await tamano(390, true);
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
  detalleModelo: modeloRes.filter(r => r.desborda || r.fuera.length || r.acabados === 0 ||
                                       (r.ruta.includes("tipo=VO") ? r.matriz !== 12 : r.matriz !== 0)).map(r => r.ancho + " " + r.ruta),
  filtros: filtrosRes.filter(f => !f.tres || !f.enTitulo || !f.coherente).map(f => f.ancho),
  enlaceModelo: /^\/mercado\/modelo\/[^?]+\?tipo=VO&zona=comunidad$/.test(enlaceModelo),
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
// Detalle de un modelo en VN y en VO, con los dos primeros acabados desplegados
const abrir = "document.querySelectorAll('details.seg-acabado').forEach((d, i) => { d.open = i < 2; }); true";
for (const [nombre, ruta] of [["modelo_vn", "/mercado/modelo/T-Roc?tipo=VN&zona=espana"],
                              ["modelo_vo", "/mercado/modelo/T-Roc?tipo=VO&zona=espana"],
                              ["modelo_vo_provincia", "/mercado/modelo/T-Roc?tipo=VO&zona=provincia"]]) {
  for (const [ancho, movil, sufijo] of [[1440, false, "escritorio_1440"], [390, true, "movil_390"]]) {
    await tamano(ancho, movil); await ir(`${BASE}${ruta}`); await evalua(abrir); await espera(300);
    await captura(`${nombre}_${sufijo}.png`);
  }
}
await tamano(390, true); await ir(`${BASE}/mercado`); await espera(500);
const zonas = await evalua(`JSON.stringify([...document.querySelectorAll('.segmento, .nav-movil-item, .boton-salir')].filter(e => getComputedStyle(e).display !== 'none').map(e => Math.round(e.getBoundingClientRect().height)))`);
console.log("altos de zonas táctiles (px):", zonas);
console.log("errores de consola/CSP:", errores.length ? errores : "ninguno");
const c = JSON.parse(comprobacion);
const mal = errores.length > 0 || c.desborda || c.filas === 0 || c.sinConcesionariosEnProvincia ||
  c.filasConDatosFuera.length > 0 || c.tarjetasFuera > 0 || c.escritorioFuera.length > 0 || c.sinBanda.length > 0 || c.sinGrafico.length > 0 || c.detalleFuera.length > 0 ||
  c.detalleModelo.length > 0 || !c.enlaceModelo || c.filtros.length > 0 || filtrosRes.length !== 2;
console.log("comprobación:", comprobacion);
console.log(mal ? "RESULTADO: FALLA" : "RESULTADO: correcto");
ws.close(); process.exit(mal ? 1 : 0);
