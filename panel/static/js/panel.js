/* Panel · gráficos con ECharts. Sin código en línea: los datos van en data-serie (JSON). */
(function () {
  "use strict";

  function miles(v) {
    if (v === null || v === undefined || isNaN(v)) { return "—"; }
    var n = Math.round(v), s = String(Math.abs(n)).replace(/\B(?=(\d{3})+(?!\d))/g, ".");
    return (n < 0 ? "−" : "") + s;
  }

  function pintar(raiz) {
    if (typeof echarts === "undefined") { return; }
    (raiz || document).querySelectorAll("[data-serie]").forEach(function (el) {
      var d = JSON.parse(el.getAttribute("data-serie"));
      var previo = echarts.getInstanceByDom(el);
      if (previo) { previo.dispose(); }
      var g = echarts.init(el, null, { renderer: "canvas" });
      g.setOption({
        animation: false,
        textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
        grid: { left: 52, right: 16, top: 16, bottom: 28 },
        tooltip: {
          trigger: "axis",
          valueFormatter: function (v) { return (v === null || v === undefined) ? "sin captura completa" : miles(v); }
        },
        xAxis: {
          type: "category", data: d.dias, boundaryGap: false,
          axisLine: { lineStyle: { color: "#9AA5B5" } }, axisTick: { show: false },
          axisLabel: { color: "#5B6675", fontSize: 11 }
        },
        yAxis: {
          type: "value", scale: true,
          splitLine: { lineStyle: { color: "#EEF0F3" } },
          axisLabel: { color: "#5B6675", fontSize: 11, formatter: miles }
        },
        series: [{
          type: "line", data: d.valores, connectNulls: false,
          showSymbol: true, symbol: "circle", symbolSize: 4,
          lineStyle: { width: 2.5, color: "#1D5FD6" }, itemStyle: { color: "#1D5FD6" },
          emphasis: { itemStyle: { color: "#14213D" } }
        }]
      });
    });
  }

  var EJE = { lineStyle: { color: "#9AA5B5" } };
  var ETIQUETA = { color: "#5B6675", fontSize: 11 };
  var REJILLA = { lineStyle: { color: "#EEF0F3" } };

  // Solo se pintan los gráficos visibles (el detalle de Ventas existe dos veces: al lado en escritorio y
  // bajo el vehículo en el móvil; el oculto se pinta si llega a verse al cambiar el ancho)
  function visible(el) { return el.getClientRects().length > 0 && el.clientWidth > 0; }

  function iniciar(el) {
    var previo = echarts.getInstanceByDom(el);
    if (previo) { previo.dispose(); }
    return echarts.init(el, null, { renderer: "canvas" });
  }

  // Dirección: ventas acumuladas del mes, previsto (p50) y banda p10–p90
  function ventas(raiz) {
    (raiz || document).querySelectorAll("[data-ventas]").forEach(function (el) {
      if (!visible(el)) { return; }
      var d = JSON.parse(el.getAttribute("data-ventas"));
      var ancho = d.p90.map(function (v, i) { return v - d.p10[i]; });
      iniciar(el).setOption({
        animation: false,
        textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
        grid: { left: 40, right: 16, top: 24, bottom: 28 },
        tooltip: {
          trigger: "axis",
          formatter: function (ps) {
            var i = ps[0].dataIndex;
            var real = d.real[i] === null ? "—" : miles(d.real[i]);
            return "Día " + d.dias[i] + "<br>Real: " + real + "<br>Previsto: " + miles(d.p50[i]) +
              " (" + miles(d.p10[i]) + "–" + miles(d.p90[i]) + ")";
          }
        },
        xAxis: { type: "category", data: d.dias, boundaryGap: false, axisLine: EJE, axisTick: { show: false }, axisLabel: ETIQUETA },
        yAxis: { type: "value", min: 0, splitLine: REJILLA, axisLabel: ETIQUETA },
        series: [
          { type: "line", data: d.p10, stack: "banda", symbol: "none", lineStyle: { opacity: 0 }, silent: true },
          { type: "line", data: ancho, stack: "banda", symbol: "none", lineStyle: { opacity: 0 }, silent: true,
            areaStyle: { color: "#1D5FD6", opacity: 0.12 } },
          { type: "line", data: d.p50, symbol: "none", lineStyle: { color: "#1D5FD6", width: 2, type: [6, 5] } },
          { type: "line", data: d.real, symbol: "none", connectNulls: false, lineStyle: { color: "#14213D", width: 3 },
            markLine: { silent: true, symbol: "none", label: { formatter: "hoy", color: "#44505F" },
              lineStyle: { color: "#9AA5B5", type: [3, 4] }, data: [{ xAxis: d.dias.indexOf(d.hoy) }] },
            markPoint: { symbol: "circle", symbolSize: 10, itemStyle: { color: "#F2A93B", borderColor: "#14213D", borderWidth: 2 },
              label: { show: false }, data: [{ coord: [d.dias.indexOf(d.hoy), d.real[d.dias.indexOf(d.hoy)]] }] } }
        ]
      });
    });
  }

  // Ventas: curva de salida del grupo (p50 y banda p10–p90) con los próximos 90 días del vehículo.
  // Eje de categorías (los puntos van de 10 en 10 días) para poder apilar la banda.
  function curva(raiz) {
    (raiz || document).querySelectorAll("[data-curva]").forEach(function (el) {
      if (!visible(el)) { return; }
      var d = JSON.parse(el.getAttribute("data-curva"));
      function pct(v) { return v.map(function (x) { return Math.round(x * 1000) / 10; }); }
      function indice(dias) {   // el punto de la curva más cercano a esos días
        var mejor = 0;
        d.t.forEach(function (t, i) { if (Math.abs(t - dias) < Math.abs(d.t[mejor] - dias)) { mejor = i; } });
        return mejor;
      }
      var ancho = d.p90.map(function (v, i) { return Math.round((v - d.p10[i]) * 1000) / 10; });
      var ia = indice(d.a), ib = indice(d.b);
      iniciar(el).setOption({
        animation: false,
        textStyle: { fontFamily: "Inter, system-ui, sans-serif" },
        grid: { left: 44, right: 16, top: 24, bottom: 28 },
        tooltip: {
          trigger: "axis",
          formatter: function (ps) {
            var i = ps[0].dataIndex;
            return d.t[i] + " días: " + Math.round(d.p50[i] * 100) + " % sin vender (" +
              Math.round(d.p10[i] * 100) + "–" + Math.round(d.p90[i] * 100) + " %)";
          }
        },
        xAxis: { type: "category", data: d.t, boundaryGap: false, axisLine: EJE, axisTick: { show: false },
          axisLabel: { color: "#5B6675", fontSize: 11, interval: 4, formatter: function (v) { return v + " d"; } } },
        yAxis: { type: "value", min: 0, max: 100, splitLine: REJILLA,
          axisLabel: { color: "#5B6675", fontSize: 11, formatter: function (v) { return v + " %"; } } },
        series: [
          { type: "line", data: pct(d.p10), stack: "banda", symbol: "none", lineStyle: { opacity: 0 }, silent: true },
          { type: "line", data: ancho, stack: "banda", symbol: "none", lineStyle: { opacity: 0 }, silent: true,
            areaStyle: { color: "#1D5FD6", opacity: 0.14 } },
          { type: "line", data: pct(d.p50), symbol: "none", lineStyle: { color: "#1D5FD6", width: 2.5 },
            markArea: { silent: true, itemStyle: { color: "#F2A93B", opacity: 0.18 }, data: [[{ xAxis: ia }, { xAxis: ib }]] },
            markLine: { silent: true, symbol: "none", label: { formatter: "hoy", color: "#14213D" },
              lineStyle: { color: "#14213D", type: [3, 4] }, data: [{ xAxis: ia }] } }
        ]
      });
    });
  }

  function todo(raiz) {
    if (typeof echarts === "undefined") { return; }
    pintar(raiz);
    ventas(raiz);
    curva(raiz);
  }

  document.addEventListener("DOMContentLoaded", function () { todo(document); });
  // Ventas en el móvil: al elegir un vehículo, su detalle se abre bajo él. Si el del vehículo anterior
  // estaba más arriba, la fila sube al quitarlo: se vuelve a llevar a la vista.
  var vehiculoPulsado = false;
  document.addEventListener("htmx:beforeRequest", function (e) {
    vehiculoPulsado = !!(e.detail.elt && e.detail.elt.classList && e.detail.elt.classList.contains("vehiculo"));
  });
  document.addEventListener("htmx:afterSettle", function () {
    todo(document);
    if (!vehiculoPulsado) { return; }
    vehiculoPulsado = false;
    var detalle = document.querySelector("tr.detalle-movil");
    var fila = document.querySelector("tr.elegido");
    if (detalle && fila && detalle.getClientRects().length && getComputedStyle(detalle).display !== "none") {
      fila.scrollIntoView({ block: "start" });
    }
  });
  window.addEventListener("resize", function () {
    if (typeof echarts === "undefined") { return; }
    var nuevos = false;
    document.querySelectorAll("[data-serie], [data-ventas], [data-curva]").forEach(function (el) {
      var g = echarts.getInstanceByDom(el);
      if (g) { g.resize(); } else if (visible(el)) { nuevos = true; }
    });
    if (nuevos) { ventas(document); curva(document); }
  });
})();
