"""Vista «Mercado»: lee las tablas panel_mercado_* ya preparadas (nunca las de hechos) y las
prepara para la plantilla. Los textos propios del concesionario salen de config/concesionario.yaml.
"""

from __future__ import annotations

from datetime import date, timedelta
from urllib.parse import quote

import psycopg

from ingesta.concesionario import Concesionario
from panel import formato as fmt

ZONAS = ("provincia", "comunidad", "espana")
TIPOS = ("VN", "VO")
FUENTES = (
    ("vo", "Ocasión"),
    ("vn", "Nuevos gasolina y diésel"),
    ("vn_otros", "Nuevos eléctricos e híbridos"),
)
DIAS_SERIE = 90
SIN_MODELO = "Sin modelo"   # el de analitica/mercado.py: sin detalle

MOTIVO_AYER = "falta la captura completa del día anterior"
MOTIVO_HACE7 = "falta la captura completa de hace 7 días"
MOTIVO_VENTANA = "falta alguna captura completa de los últimos 7 días"


def etiquetas_zona(conc: Concesionario) -> dict[str, str]:
    return {"provincia": conc.provincia, "comunidad": conc.comunidad, "espana": "España"}


def capturas(pg: psycopg.Connection, hoy: date) -> list[dict]:
    """Última captura completa de cada fuente, para la cabecera."""
    ultimas = {f: (d, fin, n) for f, d, fin, n in pg.execute(
        """SELECT DISTINCT ON (fuente) fuente, dia, fin_at, anuncios FROM panel_mercado_capturas
           WHERE completa ORDER BY fuente, dia DESC""")}
    salida = []
    for fuente, etiqueta in FUENTES:
        dia, fin, n = ultimas.get(fuente, (None, None, None))
        if dia is None:
            texto, ok = f"{etiqueta}: todavía sin capturas completas", False
        elif dia == hoy:
            texto, ok = f"{etiqueta}: captura completa hoy a las {fmt.hora(fin)} · {fmt.miles(n)} anuncios en España", True
        else:
            texto, ok = (f"{etiqueta}: sin captura completa hoy · la última, el {fmt.fecha_corta(dia)} "
                         f"a las {fmt.hora(fin)}"), False
        salida.append({"fuente": fuente, "texto": texto, "ok": ok, "dia": dia, "fin": fin})
    return salida


def estado_cabecera(pg: psycopg.Connection, hoy: date) -> dict:
    """La píldora de la barra superior: «Fuentes al día · HH:MM» o cuántas faltan."""
    caps = capturas(pg, hoy)
    faltan = [c for c in caps if not c["ok"]]
    if not faltan:
        return {"ok": True, "texto": f"Fuentes al día · {fmt.hora(max(c['fin'] for c in caps if c['fin']))}"}
    return {"ok": False, "texto": "Falta la captura de hoy de " + (
        "1 fuente" if len(faltan) == 1 else f"{len(faltan)} fuentes")}


def variacion(anuncios: int, hace7: int | None, motivo: str, n_minimo_pct: int) -> dict:
    """Diferencia en anuncios frente a hace 7 días («−2», «+3», «0») y, atenuado al lado, el
    porcentaje solo si hace 7 días había al menos `n_minimo_pct` anuncios."""
    if hace7 is None:
        return {"texto": fmt.GUION, "porcentaje": None, "motivo": motivo, "clase": "neutro"}
    diferencia = anuncios - hace7
    texto = "0" if diferencia == 0 else ("+" if diferencia > 0 else "") + fmt.miles(diferencia)
    porcentaje = None
    if hace7 >= n_minimo_pct:
        porcentaje = fmt.porcentaje(diferencia / hace7 * 100, 0, signo=True)
    clase = "sube" if diferencia > 0 else ("baja" if diferencia < 0 else "neutro")
    return {"texto": texto, "porcentaje": porcentaje, "motivo": None, "clase": clase}


def nuestros(propios: int | None, anuncios: int, n_minimo_pct: int, con_peso: bool = True) -> dict:
    """Anuncios propios del modelo y, atenuado, su peso sobre el total del modelo si este tiene al
    menos `n_minimo_pct` anuncios (solo en la provincia propia: fuera, solo las unidades).
    0 es «0»; NULL (fila anterior a la columna) es «—»."""
    if propios is None:
        return {"texto": fmt.GUION, "peso": None, "motivo": "sin cifra hasta el próximo precálculo"}
    peso = (fmt.porcentaje(propios / anuncios * 100, 0)
            if con_peso and anuncios >= n_minimo_pct and anuncios else None)
    return {"texto": fmt.miles(propios), "peso": peso, "motivo": None}


def datos(pg: psycopg.Connection, conc: Concesionario, tipo: str, zona: str, hoy: date) -> dict:
    nombres = etiquetas_zona(conc)
    nombre_tipo = {"VN": "nuevos", "VO": "ocasión"}[tipo]
    base = {"tipo": tipo, "zona": zona, "zona_nombre": nombres[zona], "capturas": capturas(pg, hoy),
            "zonas": [{"id": z, "etiqueta": nombres[z], "activa": z == zona} for z in ZONAS],
            "tipos": [{"id": "VN", "etiqueta": "Nuevos (VN)", "activa": tipo == "VN"},
                      {"id": "VO", "etiqueta": "Ocasión (VO)", "activa": tipo == "VO"}],
            "titulo_tabla": f"{nombre_tipo} en {nombres[zona]}", "hay_datos": False}
    dia = pg.execute("SELECT max(dia) FROM panel_mercado_dia WHERE tipo = %s", (tipo,)).fetchone()[0]
    if dia is None:
        return base
    fila = pg.execute(
        """SELECT anuncios, propios, concesionarios, altas_hoy, ausencias_hoy, bajas_7,
                  ayer_completo, hace7_completo, ventana_completa
           FROM panel_mercado_dia WHERE dia = %s AND tipo = %s AND zona = %s""", (dia, tipo, zona)).fetchone()
    if fila is None:  # el precálculo escribe siempre las tres zonas; por si acaso, sin datos
        return base
    anuncios, propios, concesionarios, altas, ausencias, bajas, ayer_ok, hace7_ok, ventana_ok = fila
    es_hoy = dia == hoy
    cuando = "hoy" if es_hoy else f"del {fmt.fecha_corta(dia)}"

    kpis = [
        {"etiqueta": "Anuncios publicados", "valor": fmt.miles(anuncios),
         "sub": f"{'Nuevos' if tipo == 'VN' else 'De ocasión'} · {nombres[zona]}"},
        {"etiqueta": f"Altas {cuando}", "valor": fmt.miles(altas) if ayer_ok else fmt.GUION,
         "sub": (("Anuncios que no estaban ayer" if es_hoy else "Anuncios que no estaban el día anterior")
                 if ayer_ok else f"Sin cifra: {MOTIVO_AYER}"),
         "sin_dato": not ayer_ok},
        {"etiqueta": f"Ausencias {cuando}", "valor": fmt.miles(ausencias) if ayer_ok else fmt.GUION,
         "sub": (("Estaban ayer y hoy no" if es_hoy else "Estaban el día anterior y ese día no")
                 if ayer_ok else f"Sin cifra: {MOTIVO_AYER}"),
         "sin_dato": not ayer_ok},
    ]
    if zona == "provincia":
        peso = propios / anuncios * 100 if anuncios else None
        kpis.append({"etiqueta": f"Nuestro peso en {conc.provincia}", "valor": fmt.porcentaje(peso, 1),
                     "sub": f"{fmt.miles(propios)} de {fmt.miles(anuncios)} anuncios publicados son nuestros"})
    else:
        kpis.append({"etiqueta": "Concesionarios con stock", "valor": fmt.miles(concesionarios),
                     "sub": "Con al menos un anuncio publicado"})

    n_min = conc.n_minimo_medianas
    modelos = []
    n_pct = conc.n_minimo_porcentaje
    for (modelo, n, propios_m, hace7, altas7, ausencias7, precio, n_precio, dias, cota) in pg.execute(
            """SELECT modelo, anuncios, propios, anuncios_hace7, altas_7, ausencias_7, precio_mediano_cents,
                      n_precio, dias_mediana, dias_cota
               FROM panel_mercado_modelo WHERE dia = %s AND tipo = %s AND zona = %s
               ORDER BY anuncios DESC, modelo""", (dia, tipo, zona)):
        modelos.append({
            "modelo": modelo, "url": None if modelo == SIN_MODELO else url_detalle(modelo, tipo, zona),
            "anuncios": fmt.miles(n), "nuestros": nuestros(propios_m, n, n_pct, con_peso=zona == "provincia"),
            "variacion": variacion(n, hace7, MOTIVO_HACE7, conc.n_minimo_porcentaje),
            "altas": fmt.miles(altas7) if ventana_ok else fmt.GUION,
            "ausencias": fmt.miles(ausencias7) if ventana_ok else fmt.GUION,
            "precio": fmt.euros(precio), "precio_n": n_precio, "precio_atenuado": 0 < n_precio < n_min,
            "dias": fmt.GUION if dias is None else (f"≥ {fmt.miles(dias)}" if cota else fmt.miles(dias)),
            "dias_cota": cota, "dias_n": n, "dias_atenuado": 0 < n < n_min,
        })
    motivos = []
    if not hace7_ok:
        motivos.append(f"Frente a hace 7 días: {MOTIVO_HACE7} ({fmt.fecha_corta(dia - timedelta(days=7))}).")
    if not ventana_ok:
        motivos.append(f"Altas y ausencias de 7 días: {MOTIVO_VENTANA}.")

    desde = dia - timedelta(days=DIAS_SERIE - 1)
    valores = dict(pg.execute(
        """SELECT dia, anuncios FROM panel_mercado_dia WHERE tipo = %s AND zona = %s AND dia BETWEEN %s AND %s""",
        (tipo, zona, desde, dia)).fetchall())
    primero = min(valores)
    serie = {"dias": [], "valores": []}
    d = primero
    while d <= dia:  # sin interpolar: un día sin captura completa es un hueco (null)
        serie["dias"].append(fmt.fecha_corta(d))
        serie["valores"].append(valores.get(d))
        d += timedelta(days=1)

    notas = [f"Frente a hace 7 días: diferencia en número de anuncios; al lado, atenuado, el porcentaje, "
             f"solo si hace 7 días había al menos {n_pct} anuncios.",
             (f"Nuestros: anuncios propios en ese modelo; al lado, atenuado, su peso sobre el total del "
              f"modelo, solo si el modelo tiene al menos {n_pct} anuncios." if zona == "provincia" else
              "Nuestros: anuncios propios en ese modelo."),
             f"El precio y los días son medianas de los anuncios publicados {'hoy' if es_hoy else 'ese día'}.",
             "«≥ N»: parte de esos anuncios ya estaban publicados en la primera captura o tras un día sin "
             f"captura, así que llevan al menos N días. Con menos de {n_min} anuncios, la cifra va atenuada con su n."]
    if tipo == "VN":
        notas.insert(0, "VN incluye gasolina, diésel, eléctricos e híbridos.")
    por_concesionario = None
    if zona == "provincia":
        por_concesionario = []
        for dealer, nombre, propio, n, hace7, precio, n_precio in pg.execute(
                """SELECT dealer_id, concesionario, es_propio, anuncios, anuncios_hace7, precio_mediano_cents, n_precio
                   FROM panel_mercado_concesionario WHERE dia = %s AND tipo = %s
                   ORDER BY anuncios DESC, concesionario, dealer_id""", (dia, tipo)):
            por_concesionario.append({
                "concesionario": nombre, "propio": propio, "anuncios": fmt.miles(n),
                "variacion": variacion(n, hace7, MOTIVO_HACE7, n_pct),
                "precio": fmt.euros(precio), "precio_n": n_precio, "precio_atenuado": 0 < n_precio < n_min,
                "peso": fmt.porcentaje(n / anuncios * 100, 1) if anuncios else fmt.GUION,
            })
    return {
        **base, "hay_datos": True, "dia": dia, "es_hoy": es_hoy, "dia_largo": fmt.fecha_larga(dia),
        "por_concesionario": por_concesionario, "provincia": conc.provincia,
        "kpis": kpis, "modelos": modelos, "motivos": motivos, "notas": notas, "n_minimo": n_min,
        "serie": serie, "bajas_7": None if bajas is None else fmt.miles(bajas),
        "motivo_bajas": None if ventana_ok else MOTIVO_VENTANA,
    }


def estado_fuentes(pg: psycopg.Connection) -> dict:
    anotaciones = [
        {"fuente": f, "tipo": t, "dia": fmt.fecha(d), "estado": e, "registrado": fmt.fecha_hora(r),
         "detalle": (str(det) if det else "")[:200]}
        for f, t, d, e, r, det in pg.execute(
            """SELECT DISTINCT ON (fuente, tipo) fuente, tipo, dia, estado, registrado_at, detalle
               FROM fuentes_estado ORDER BY fuente, tipo, id DESC""")]
    dias = []
    for fuente, etiqueta in FUENTES:
        filas = pg.execute("SELECT dia, completa, anuncios, sin_provincia FROM panel_mercado_capturas "
                           "WHERE fuente = %s ORDER BY dia", (fuente,)).fetchall()
        completas = [f for f in filas if f[1]]
        ultima = completas[-1] if completas else None
        dias.append({
            "fuente": fuente, "etiqueta": etiqueta,
            "desde": fmt.fecha(filas[0][0]) if filas else fmt.GUION,
            "con": len(completas), "sin": len(filas) - len(completas),
            "dias_sin": [fmt.fecha(f[0]) for f in filas if not f[1]],
            "ultima": fmt.fecha(ultima[0]) if ultima else fmt.GUION,
            "anuncios": fmt.miles(ultima[2]) if ultima else fmt.GUION,
            "sin_provincia": fmt.miles(ultima[3]) if ultima else fmt.GUION,
        })
    return {"anotaciones": anotaciones, "dias": dias, "cobertura": cobertura(pg)}


# --------------------------------------------------------------------------- detalle de un modelo

NOTA_PRECIO = "Precio publicado, no precio de venta. Los segmentos con pocos anuncios no son representativos."


def _euros_rango(p25, p75) -> str:
    return f"{fmt.miles(round(p25 / 100))}–{fmt.miles(round(p75 / 100))} €"


def cifra_precio(p50, p25, p75, n: int, n_mostrar: int, n_min: int) -> dict:
    """Precio mediano de un segmento con su rango p25–p75. Con n < n_mostrar, «—» y «pocos anuncios»
    (nunca la cifra); con n < n_min, la cifra atenuada con su n."""
    if n < n_mostrar or p50 is None:
        return {"texto": fmt.GUION, "pocos": True, "n": n, "rango": None, "atenuado": False}
    return {"texto": fmt.euros(p50), "pocos": False, "n": n, "rango": _euros_rango(p25, p75),
            "atenuado": n < n_min}


def cifra_dias(dias, cota: bool, n: int, n_mostrar: int, n_min: int) -> dict:
    if n < n_mostrar or dias is None:
        return {"texto": fmt.GUION, "pocos": True, "n": n, "atenuado": False, "cota": False}
    return {"texto": f"≥ {fmt.miles(dias)}" if cota else fmt.miles(dias), "pocos": False, "n": n,
            "atenuado": n < n_min, "cota": cota}


def url_detalle(modelo: str, tipo: str, zona: str, acabado: str | None = None) -> str:
    url = f"/mercado/modelo/{quote(modelo, safe='')}?tipo={tipo}&zona={zona}"
    return url + (f"&acabado={quote(acabado, safe='')}" if acabado else "")


def detalle_modelo(pg: psycopg.Connection, conc: Concesionario, tipo: str, zona: str, modelo: str, hoy: date,
                   acabado: str | None = None) -> dict:
    """Detalle de un modelo: por acabado y motor y, en VO, precio por antigüedad y km. Lee solo
    panel_mercado_segmento (precalculada); nunca las tablas de hechos."""
    nombres = etiquetas_zona(conc)
    n_mostrar, n_min = conc.n_minimo_mostrar, conc.n_minimo_medianas
    nombre_tipo = {"VN": "Nuevos", "VO": "Ocasión"}[tipo]
    base = {
        "tipo": tipo, "zona": zona, "zona_nombre": nombres[zona], "modelo": modelo, "nombre_tipo": nombre_tipo,
        "capturas": capturas(pg, hoy), "volver": f"/mercado?zona={zona}&tipo={tipo}",
        "zonas": [{"id": z, "etiqueta": nombres[z], "activa": z == zona, "url": url_detalle(modelo, tipo, z)}
                  for z in ZONAS],
        "tipos": [{"id": t, "etiqueta": e, "activa": t == tipo, "url": url_detalle(modelo, t, zona)}
                  for t, e in (("VN", "Nuevos (VN)"), ("VO", "Ocasión (VO)"))],
        "hay_datos": False, "hay_modelo": False, "nota_precio": NOTA_PRECIO, "n_minimo": n_min,
        "n_mostrar": n_mostrar,
    }
    dia = pg.execute("SELECT max(dia) FROM panel_mercado_dia WHERE tipo = %s", (tipo,)).fetchone()[0]
    if dia is None:
        return base
    base.update({"hay_datos": True, "dia": dia, "es_hoy": dia == hoy, "dia_largo": fmt.fecha_larga(dia)})
    filas = pg.execute(
        """SELECT nivel, acabado, acabado_reconocido, motor, antiguedad, antiguedad_orden, km, km_orden, color,
                  anuncios, propios, n_precio, precio_p25_cents, precio_p50_cents, precio_p75_cents,
                  dias_mediana, dias_cota
           FROM panel_mercado_segmento WHERE dia = %s AND tipo = %s AND zona = %s AND modelo = %s""",
        (dia, tipo, zona, modelo)).fetchall()
    if not filas:
        return base
    con_peso = zona == "provincia"
    pct = conc.n_minimo_porcentaje

    def segmento(f) -> dict:
        (_, _, _, _, _, _, _, _, _, n, propios, n_precio, p25, p50, p75, dias, cota) = f
        return {"anuncios": fmt.miles(n), "n": n, "nuestros": nuestros(propios, n, pct, con_peso),
                "precio": cifra_precio(p50, p25, p75, n_precio, n_mostrar, n_min),
                "dias": cifra_dias(dias, cota, n, n_mostrar, n_min)}

    por_nivel: dict[str, list] = {}
    for f in filas:
        por_nivel.setdefault(f[0], []).append(f)
    total = por_nivel["modelo"][0]

    acabados = []
    for f in sorted(por_nivel.get("acabado", []), key=lambda f: (not f[2], -f[9], f[1])):
        motores = [{"nombre": m[3], **segmento(m)}
                   for m in sorted((m for m in por_nivel.get("motor", []) if m[1] == f[1]), key=lambda m: (-m[9], m[3]))]
        acabados.append({"nombre": f[1], "reconocido": f[2], **segmento(f), "motores": motores})

    matriz = None
    if tipo == "VO" and conc.tramos_antiguedad and conc.tramos_km:
        con_matriz = sorted({f[1] for f in por_nivel.get("acabado_antiguedad_km", [])})
        elegido = acabado if acabado in con_matriz else None
        celdas = {(f[5], f[7]): f for f in (
            por_nivel.get("antiguedad_km", []) if elegido is None
            else [f for f in por_nivel.get("acabado_antiguedad_km", []) if f[1] == elegido])}
        filas_m = []
        for i, (_, etiqueta) in enumerate(conc.tramos_antiguedad):
            fila = []
            for j, (_, km) in enumerate(conc.tramos_km):
                c = celdas.get((i, j))
                n = c[11] if c else 0
                fila.append({"km": km, "n": n, "anuncios": c[9] if c else 0,
                             "precio": cifra_precio(c[13] if c else None, c[12] if c else None,
                                                    c[14] if c else None, n, n_mostrar, n_min)})
            filas_m.append({"etiqueta": etiqueta, "celdas": fila})
        en_matriz = sum(f[9] for f in por_nivel.get("antiguedad_km", []))
        orden_acabados = [a["nombre"] for a in acabados if a["nombre"] in con_matriz]
        matriz = {
            "columnas": [km for _, km in conc.tramos_km], "filas": filas_m,
            "filtro": [{"etiqueta": "Todos", "activa": elegido is None, "url": url_detalle(modelo, tipo, zona)}] + [
                {"etiqueta": a, "activa": a == elegido, "url": url_detalle(modelo, tipo, zona, a)}
                for a in orden_acabados],
            "acabado": elegido, "fuera": total[9] - en_matriz,
        }

    colores = [{"nombre": f[8], **segmento(f)} for f in sorted(por_nivel.get("color", []), key=lambda f: -f[9])]

    # Si en la zona elegida la mayoría de las casillas quedan en «—», se propone ver España
    casillas = [a["precio"] for a in acabados] + [m["precio"] for a in acabados for m in a["motores"]]
    if matriz:
        casillas += [c["precio"] for fila in matriz["filas"] for c in fila["celdas"]]
    pocos = sum(1 for c in casillas if c["pocos"])
    nota_espana = None
    if zona != "espana" and casillas and pocos * 2 > len(casillas):
        nota_espana = {"texto": (f"En {nombres[zona]}, {pocos} de {len(casillas)} casillas tienen menos de "
                                 f"{n_mostrar} anuncios con precio."),
                       "url": url_detalle(modelo, tipo, "espana", matriz and matriz["acabado"])}

    notas = [
        f"Precio: mediana de los anuncios con precio publicados {'hoy' if dia == hoy else 'ese día'}; debajo, "
        "el rango p25–p75 (la mitad central de los precios). Días publicado: mediana; «≥ N», como en la tabla "
        "por modelo.",
        f"Con menos de {n_min} anuncios, la cifra va atenuada con su n; con menos de {n_mostrar}, «—».",
        "Acabado: el que figura en el texto de la versión. «Sin clasificar»: anuncios cuyo acabado no se "
        "reconoce; no se adivina.",
    ]
    if matriz:
        notas.append(("Antigüedad: años cumplidos desde la primera matriculación hasta el día de los datos. "
                      + (f"{fmt.miles(matriz['fuera'])} anuncios sin fecha de matriculación o sin km no entran en "
                         "la matriz." if matriz["fuera"] else "")).strip())
    return {**base, "hay_modelo": True, "total": segmento(total), "acabados": acabados, "matriz": matriz,
            "colores": colores, "nota_espana": nota_espana, "notas": notas}


def cobertura(pg: psycopg.Connection) -> dict:
    """«Estado de las fuentes»: anuncios con acabado reconocido por modelo y tipo (España, último día
    calculado) y los 20 textos de versión sin clasificar más frecuentes."""
    filas = pg.execute(
        """SELECT s.tipo, s.dia, s.modelo, sum(s.anuncios),
                  sum(s.anuncios) FILTER (WHERE s.acabado_reconocido)
           FROM panel_mercado_segmento s
           JOIN (SELECT tipo, max(dia) AS dia FROM panel_mercado_segmento GROUP BY tipo) u USING (tipo, dia)
           WHERE s.zona = 'espana' AND s.nivel = 'acabado'
           GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC, 3""").fetchall()
    tipos = []
    for tipo in TIPOS:
        mias = [f for f in filas if f[0] == tipo]
        if not mias:
            continue
        n, ok = sum(f[3] for f in mias), sum(f[4] or 0 for f in mias)
        tipos.append({
            "tipo": tipo, "dia": fmt.fecha(mias[0][1]), "total": fmt.porcentaje(ok / n * 100, 1) if n else fmt.GUION,
            "anuncios": fmt.miles(n),
            "modelos": [{"modelo": f[2], "anuncios": fmt.miles(f[3]), "reconocidos": fmt.miles(f[4] or 0),
                         "porcentaje": fmt.porcentaje((f[4] or 0) / f[3] * 100, 0)} for f in mias],
        })
    textos = [{"tipo": t, "modelo": m, "version": v, "anuncios": fmt.miles(n)} for t, m, v, n in pg.execute(
        """SELECT s.tipo, s.modelo, s.version, s.anuncios FROM panel_mercado_sin_clasificar s
           JOIN (SELECT tipo, max(dia) AS dia FROM panel_mercado_segmento GROUP BY tipo) u USING (tipo, dia)
           ORDER BY s.anuncios DESC, s.tipo, s.modelo, s.version LIMIT 20""")]
    return {"tipos": tipos, "textos": textos}
