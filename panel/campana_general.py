"""Bloque «Campaña general» de Marketing y Dirección (tabla panel_campana_general del contrato).

Dos tarjetas, VN y VO: «Conviene lanzar una campaña general» con sus motivos, o «Ahora no hace falta» con la
próxima temporada. Lo que falta para alguna regla («Pendiente: …»), debajo. Las cifras (ventas de cada ventana,
cambio, stock de hoy y de hace un mes) solo las ve el rol técnico. Los textos llegan ya escritos desde
config/campana_general.yaml (analitica/campana_general.py, en el precálculo).
"""

from __future__ import annotations

from datetime import date

import psycopg
import yaml

from panel import formato as fmt
from panel import negocio as ng

TIPOS = ("VN", "VO")
NOMBRE = {"VN": "Nuevos (VN)", "VO": "Ocasión (VO)"}


def _textos() -> dict:
    from pathlib import Path
    ruta = Path(__file__).resolve().parent.parent / "config" / "campana_general.yaml"
    try:
        return (yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}).get("textos") or {}
    except OSError:
        return {}


def _fecha(v) -> str:
    return fmt.fecha_corta(date.fromisoformat(v)) if v else fmt.GUION


def detalle(cifras: dict) -> list[str]:
    """Las cifras de cada regla, para el rol técnico."""
    v, s, t = cifras.get("ventas") or {}, cifras.get("stock") or {}, cifras.get("temporada") or {}
    lineas = []
    if v.get("estado") == "pendiente":
        lineas.append(f"Caída de ventas: pendiente ({v.get('motivo', '')}).")
    elif v:
        cambio = "—" if v.get("cambio_pct") is None else fmt.porcentaje(v["cambio_pct"], 1, signo=True)
        lineas.append(
            f"Caída de ventas ({'se cumple' if v['estado'] == 'si' else 'no se cumple'}): {v['actual']} ventas del "
            f"{_fecha(v['actual_desde'])} al {_fecha(v['actual_hasta'])} (semanas) frente a {v['anterior']} del "
            f"{_fecha(v['anterior_desde'])} al {_fecha(v['anterior_hasta'])}: {cambio} (umbral "
            f"−{fmt.decimal(v['caida_pct_umbral'], 0)} %, diferencia mínima {v['diferencia_minima']} coches).")
    if s.get("estado") == "pendiente":
        lineas.append(f"Subida del stock: pendiente ({s.get('motivo', '')}).")
    elif s:
        cambio = "—" if s.get("cambio_pct") is None else fmt.porcentaje(s["cambio_pct"], 1, signo=True)
        fuente = {"crm": "stock del CRM", "web": "nuestros anuncios en la web"}.get(s.get("fuente"), s.get("fuente"))
        lineas.append(
            f"Subida del stock ({'se cumple' if s['estado'] == 'si' else 'no se cumple'}): {s['hoy']} el "
            f"{_fecha(s['dia_hoy'])} frente a {s['antes']} el {_fecha(s['dia_antes'])}: {cambio} (umbral "
            f"+{fmt.decimal(s['subida_pct_umbral'], 0)} % en {s['dias']} días; {fuente}).")
    if t:
        lineas.append(f"Temporada: {t.get('nombre')} ({t.get('meses')})." if t.get("estado") == "si"
                      else "Temporada: no.")
    return lineas


def bloque(pg: psycopg.Connection, origen: str | None, tipo: str = "todo") -> dict:
    dia = ng.ultimo_dia(pg, "panel_campana_general", origen)
    if dia is None:
        return ng.bloque(origen, hay=False)
    tx = _textos()
    tarjetas = []
    for t, conviene, motivos, pendientes, proxima, cifras in pg.execute(
            """SELECT tipo, conviene, motivos, pendientes, proxima, cifras FROM panel_campana_general
               WHERE origen = %s AND dia = %s AND (%s = 'todo' OR tipo = %s) ORDER BY tipo""",
            (origen, dia, tipo, tipo)):
        tarjetas.append({
            "tipo": t, "nombre": NOMBRE[t], "conviene": conviene,
            "titulo": tx.get("conviene" if conviene else "no_hace_falta",
                             "Conviene lanzar una campaña general" if conviene else "Ahora no hace falta"),
            "motivos": [m["texto"] for m in motivos],
            "proxima": (tx.get("proxima", "Próxima temporada: {meses}").format(meses=proxima)
                        if proxima and not conviene else None),
            "pendientes": [p["texto"] for p in pendientes],
            "detalle": detalle(cifras)})
    return ng.bloque(origen, tarjetas=tarjetas, dia=fmt.fecha_corta(dia))
