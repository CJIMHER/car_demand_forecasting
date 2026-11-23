"""Alertas en las vistas: Dirección (todas, con su destinatario) y Ventas y Marketing (las de su rol).

El bloque sigue la regla del contrato (panel/contrato.py, «un bloque no mezcla orígenes"):
- real (alertas_evaluacion tiene filas reales: el motor ha evaluado alguna regla con datos reales):
  las alertas abiertas de la tabla alertas en la última evaluación (nuevas y abiertas, nunca las
  cerradas ni las calculadas sobre ejemplo);
- ejemplo: las filas de ejemplo de panel_alertas (make ejemplo-cargar);
- ninguno: «Sin datos todavía».
"""

from __future__ import annotations

from datetime import date

import psycopg

from panel import contrato


def leer(pg: psycopg.Connection, origen: str | None, tipos: tuple[str, ...], rol: str | None = None
         ) -> tuple[date | None, list[dict]]:
    """(día, alertas) con tema, destinatarios, nivel, texto, abierta_desde, regla y clave, de más a
    menos importante. `rol`: solo las de ese destinatario."""
    if origen == contrato.REAL:
        dia = pg.execute("SELECT max(dia) FROM alertas_evaluacion WHERE origen = 'real'").fetchone()[0]
        if dia is None:
            return None, []
        filas = pg.execute(
            """SELECT regla, clave, tema, array_agg(rol ORDER BY array_position(
                          ARRAY['direccion', 'ventas', 'marketing', 'tecnico'], rol)),
                      nivel, texto, abierta_desde
               FROM alertas WHERE fecha = %s AND origen = 'real' AND estado IN ('nueva', 'abierta')
                 AND (tipo IS NULL OR tipo = ANY(%s))
               GROUP BY regla, clave, tema, nivel, texto, abierta_desde""", (dia, list(tipos))).fetchall()
        alertas = [{"regla": r, "clave": c, "tema": t, "destinatarios": list(d), "nivel": n, "texto": x,
                    "abierta_desde": a} for r, c, t, d, n, x, a in filas]
    elif origen == contrato.EJEMPLO:
        dia = pg.execute("SELECT max(dia) FROM panel_alertas WHERE origen = 'ejemplo'").fetchone()[0]
        if dia is None:
            return None, []
        filas = pg.execute(
            """SELECT regla, alerta_id, tema, destinatarios, nivel, texto, abierta_desde FROM panel_alertas
               WHERE origen = 'ejemplo' AND dia = %s AND (tipo IS NULL OR tipo = ANY(%s))""",
            (dia, list(tipos))).fetchall()
        alertas = [{"regla": r, "clave": c, "tema": t, "destinatarios": list(d), "nivel": n, "texto": x,
                    "abierta_desde": a} for r, c, t, d, n, x, a in filas]
    else:
        return None, []
    if rol:
        alertas = [a for a in alertas if rol in a["destinatarios"]]
    alertas.sort(key=lambda a: (a["nivel"] != "aviso", -a["abierta_desde"].toordinal(), a["clave"], a["regla"]))
    return dia, alertas


def bloque_rol(pg: psycopg.Connection, origen: str | None, rol: str, tipo: str) -> dict:
    """Bloque «Alertas para <rol>» de Ventas y Marketing (las suyas, del tipo elegido)."""
    from panel import negocio as ng

    tipos = ("VN", "VO") if tipo == "todo" else (tipo,)
    dia, filas = leer(pg, origen, tipos, rol)
    if dia is None:
        return ng.bloque(origen, hay=False)
    return ng.bloque(origen, dia=dia, filas=[
        {"texto": a["texto"], "nivel": a["nivel"], "nueva": a["abierta_desde"] == dia,
         "pie": f"{ng.TEMAS[a['tema']]} · abierta desde el {a['abierta_desde']:%d/%m}"} for a in filas])


def estado_trafico(pg: psycopg.Connection, dias: int = 28) -> dict:
    """Para «Estado de las fuentes»: sesiones de GA4 por canal (últimos `dias` días con datos), las fuentes
    y medios que caen en «otros» (para completar config/canales.yaml) y las campañas de Google Ads."""
    from datetime import timedelta

    from ingesta import canales as cn
    from panel import formato as fmt

    try:
        nombres = cn.cargar()
    except (OSError, cn.ErrorCanales):
        nombres = None
    fin = pg.execute("SELECT max(dia) FROM panel_trafico_canal_dia WHERE origen = 'real'").fetchone()[0]
    canales, total = [], 0
    if fin:
        ini = fin - timedelta(days=dias - 1)
        filas = pg.execute(
            """SELECT canal, sum(sesiones), sum(sesiones) FILTER (WHERE tipo = 'sin_tipo') FROM panel_trafico_canal_dia
               WHERE origen = 'real' AND dia BETWEEN %s AND %s GROUP BY canal ORDER BY canal = %s, 2 DESC, canal""",
            (ini, fin, cn.OTROS)).fetchall()
        total = sum(int(s) for _, s, _ in filas)
        canales = [{"canal": c, "nombre": nombres.nombre(c) if nombres else c, "sesiones": fmt.miles(s),
                    "pct": fmt.porcentaje(int(s) / total * 100 if total else None, 1),
                    "sin_tipo": fmt.miles(st or 0)} for c, s, st in filas]
    otros = [{"fuente": f, "medio": m, "canal_ga4": g or fmt.GUION, "sesiones": fmt.miles(s),
              "fechas": f"{fmt.fecha_corta(p)}–{fmt.fecha_corta(u)}"}
             for f, m, g, p, u, s in pg.execute(
                 """SELECT fuente, medio, canal_ga4, primer_dia, ultimo_dia, sesiones FROM panel_trafico_otros
                    ORDER BY sesiones DESC, fuente, medio LIMIT 50""")]
    n_otros = pg.execute("SELECT count(*) FROM panel_trafico_otros").fetchone()[0]
    google = [{"campana": c, "sesiones": fmt.miles(s), "eventos": fmt.miles(e),
               "fechas": f"{fmt.fecha_corta(p)}–{fmt.fecha_corta(u)}"}
              for c, p, u, s, e in pg.execute(
                  """SELECT campana, primer_dia, ultimo_dia, sesiones, eventos_clave FROM panel_campanas_google_ads
                     ORDER BY ultimo_dia DESC, sesiones DESC, campana""")]
    return {"canales": canales, "total": fmt.miles(total), "dias": dias,
            "desde": fmt.fecha_corta(fin - timedelta(days=dias - 1)) if fin else None,
            "hasta": fmt.fecha_corta(fin) if fin else None, "otros": otros, "n_otros": n_otros, "google": google}


def estado_motor(pg: psycopg.Connection) -> dict:
    """Para «Estado de las fuentes» (técnico): reglas de la última pasada del motor, alertas del técnico
    (también las calculadas sobre ejemplo), últimos envíos y campañas sin regla en config/campanas.yaml."""
    from panel import formato as fmt

    ultima = pg.execute("SELECT dia, estado, detalle, registrado_at FROM fuentes_estado WHERE tipo = 'alertas' "
                        "ORDER BY id DESC LIMIT 1").fetchone()
    reglas = []
    if ultima and ultima[1] == "ok":
        for regla, texto in sorted((ultima[2] or {}).get("reglas", {}).items()):
            reglas.append({"regla": regla, "texto": texto, "activa": not texto.startswith("inactiva")})
    dia = pg.execute("SELECT max(fecha) FROM alertas").fetchone()[0]
    tecnico = [{"texto": t, "estado": e, "desde": fmt.fecha_corta(a)} for t, e, a in pg.execute(
        """SELECT texto, estado, abierta_desde FROM alertas WHERE fecha = %s AND rol = 'tecnico'
           ORDER BY estado = 'cerrada', origen, regla, clave""", (dia,))] if dia else []
    salidas = [{"cuando": fmt.fecha_hora(r), "que": q, "rol": rol, "destino": d, "estado": e,
                "texto": (x.splitlines() or [""])[0][:140]} for r, q, rol, d, e, x in pg.execute(
        """SELECT registrado_at, que, rol, destino, estado, texto FROM alertas_salida ORDER BY id DESC LIMIT 15""")]
    campanas = [{"fuente": "Meta" if f == "meta" else "GA4", "campana": c,
                 "fechas": f"{fmt.fecha_corta(p)}–{fmt.fecha_corta(u)}",
                 "cifra": fmt.euros(g) if f == "meta" else f"{fmt.miles(s)} sesiones",
                 "motivo": (m[:1].upper() + m[1:]) if m else fmt.GUION}
                for f, c, p, u, g, s, m in pg.execute(
        """SELECT fuente, campana, primer_dia, ultimo_dia, gasto_cents, sesiones, motivo FROM panel_campanas_sin_regla
           ORDER BY fuente, ultimo_dia DESC, campana""")]
    return {"ultima": {"dia": fmt.fecha(ultima[0]), "estado": ultima[1], "cuando": fmt.fecha_hora(ultima[3])}
            if ultima else None,
            "reglas": reglas, "tecnico": tecnico, "dia": fmt.fecha(dia) if dia else None, "salidas": salidas,
            "campanas": campanas}
