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

  document.addEventListener("DOMContentLoaded", function () { pintar(document); });
  document.addEventListener("htmx:afterSettle", function () { pintar(document); });
  window.addEventListener("resize", function () {
    if (typeof echarts === "undefined") { return; }
    document.querySelectorAll("[data-serie]").forEach(function (el) {
      var g = echarts.getInstanceByDom(el);
      if (g) { g.resize(); }
    });
  });
})();
