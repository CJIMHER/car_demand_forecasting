"""Vista «Mercado»: lee las tablas panel_mercado_* ya preparadas (nunca las de hechos) y las
prepara para la plantilla. Los textos propios del concesionario salen de config/concesionario.yaml.
"""

from __future__ import annotations

import os
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from urllib.parse import quote

import psycopg
import yaml
from psycopg.rows import dict_row

from ingesta.concesionario import Concesionario
from panel import formato as fmt

ZONAS = ("provincia", "cercana", "comunidad", "espana")
TIPOS = ("VN", "VO")
FUENTES = (
    ("vo", "Ocasión"),
    ("vn", "Nuevos gasolina y diésel"),
    ("vn_otros", "Nuevos eléctricos e híbridos"),
)
DIAS_SERIE = 90
SIN_MODELO = "Sin modelo"   # el de analitica/mercado.py: sin detalle


@lru_cache(maxsize=1)
def sin_especificar() -> str:
    """Nombre de la familia «sin especificar» de config/colores.yaml (las familias las calcula el
    precálculo, analitica/colores.py; la imagen del panel no lleva analitica/)."""
    ruta = Path(os.environ.get("CDF_COLORES", Path(__file__).resolve().parent.parent / "config" / "colores.yaml"))
    return str((yaml.safe_load(ruta.read_text(encoding="utf-8")) or {}).get("sin_especificar") or "Sin especificar")


MOTIVO_AYER = "falta la captura completa del día anterior"
MOTIVO_HACE7 = "falta la captura completa de hace 7 días"
MOTIVO_VENTANA = "falta alguna captura completa de los últimos 7 días"


def etiquetas_zona(conc: Concesionario) -> dict[str, str]:
    """La zona cercana (provincia propia y propio.provincias_cercanas) se llama así en el selector; qué
    provincias son lo dice zona_cercana_nombre."""
    return {"provincia": conc.provincia, "cercana": "Zona cercana", "comunidad": conc.comunidad, "espana": "España"}


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
    return {"anotaciones": anotaciones, "dias": dias, "cobertura": cobertura(pg), "color": cobertura_colores(pg)}


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


def url_detalle(modelo: str, tipo: str, zona: str, acabado: str | None = None, combustible: str | None = None,
                motor: str | None = None, color: str | None = None) -> str:
    url = f"/mercado/modelo/{quote(modelo, safe='')}?tipo={tipo}&zona={zona}"
    for nombre, valor in (("acabado", acabado), ("combustible", combustible), ("motor", motor), ("color", color)):
        if valor:
            url += f"&{nombre}={quote(valor, safe='')}"
    return url


# Filtros del detalle: (acabado, combustible, motor, familia de color). El color filtra toda la página
# (totales, «Por acabado y motor» y la matriz); acabado, combustible y motor filtran la matriz de VO.
# Existen: las familias del modelo en esa zona y tipo (nivel color); y los (acabado, combustible, motor)
# con anuncios en la matriz (VO) o en el nivel motor (VN, sin matriz: para conservarlos al volver a VO),
# también con cada familia, para que con un color elegido solo se ofrezcan los que existen con él.
Filtro = tuple[str | None, str | None, str | None, str | None]
NIVEL_EXISTENCIA = {"VO": "acabado_motor_antiguedad_km", "VN": "motor"}
NIVEL_COLOR = {"VO": "acabado_motor_color_antiguedad_km", "VN": "motor_color"}
Existentes = tuple[set, set, set]   # (trios, cuartetos con color, familias)


def existentes(pg: psycopg.Connection, modelo: str) -> dict[tuple[str, str], Existentes]:
    """{(tipo, zona): ({(acabado, combustible, motor)}, {(acabado, combustible, motor, color)}, {color})} del
    último día de cada tipo."""
    salida: dict[tuple[str, str], Existentes] = {(t, z): (set(), set(), set()) for t in TIPOS for z in ZONAS}
    for tipo, zona, nivel, acabado, comb, motor, color in pg.execute(
            """SELECT s.tipo, s.zona, s.nivel, s.acabado, s.combustible, s.motor, s.color FROM panel_mercado_segmento s
               JOIN (SELECT tipo, max(dia) AS dia FROM panel_mercado_dia GROUP BY tipo) u USING (tipo, dia)
               WHERE s.modelo = %s
                 AND s.nivel IN ('color', CASE s.tipo WHEN 'VO' THEN %s ELSE %s END,
                                 CASE s.tipo WHEN 'VO' THEN %s ELSE %s END)""",
            (modelo, NIVEL_EXISTENCIA["VO"], NIVEL_EXISTENCIA["VN"], NIVEL_COLOR["VO"], NIVEL_COLOR["VN"])):
        trios, cuartetos, familias = salida[(tipo, zona)]
        if nivel == "color":
            familias.add(color)
        elif color is None:
            trios.add((acabado, comb, motor))
        else:
            cuartetos.add((acabado, comb, motor, color))
    return salida


def validar(hay: Existentes, acabado: str | None, combustible: str | None, motor: str | None,
            color: str | None = None) -> Filtro:
    """Se queda con los valores que existen juntos. Primero el color (una familia del modelo en esa zona y
    tipo); después, entre los anuncios de ese color, el acabado; el combustible con ese acabado; el motor
    con los dos. Un motor sin combustible lo toma del motor si solo hay uno."""
    trios, cuartetos, familias = hay
    col = color if color in familias else None
    if col is not None:
        trios = {q[:3] for q in cuartetos if q[3] == col}
    return (*_validar3(trios, acabado, combustible, motor), col)


def _validar3(trios: set[tuple[str, str, str]], acabado: str | None, combustible: str | None,
              motor: str | None) -> tuple[str | None, str | None, str | None]:
    a = acabado if any(t[0] == acabado for t in trios) else None
    con_a = [t for t in trios if a is None or t[0] == a]
    m = None
    if motor is not None:
        combustibles = {t[1] for t in con_a if t[2] == motor and combustible in (None, t[1])}
        if len(combustibles) == 1:
            m, combustible = motor, combustibles.pop()
    c = combustible if any(t[1] == combustible for t in con_a) else None
    return a, c, m


COBERTURA_COMPLETA = 90   # % de anuncios con color a partir del cual no hace falta la nota
NOTA_COBERTURA = "mientras se completa, predominan los publicados hace más tiempo"


def cobertura_color(con_color: int | None, anuncios: int) -> dict:
    """«Color conocido en el X % de los anuncios» (X redondeado hacia abajo: 89,6 % es «89 %») y, por
    debajo del 90 %, la nota fija. None: segmento precalculado antes de la columna con_color."""
    if con_color is None or not anuncios:
        return {"texto": "Color conocido: sin cifra hasta el próximo precálculo", "pct": None, "nota": False}
    pct = con_color * 100 // anuncios
    nota = con_color * 100 < COBERTURA_COMPLETA * anuncios
    texto = f"Color conocido en el {pct} % de los anuncios"
    return {"texto": texto + (f"; {NOTA_COBERTURA}" if nota else "."), "pct": pct, "nota": nota}


def _orden_color(sin_esp: str):
    return lambda f: (f["color"] == sin_esp, -f["anuncios"], f["color"])


def detalle_modelo(pg: psycopg.Connection, conc: Concesionario, tipo: str, zona: str, modelo: str, hoy: date,
                   acabado: str | None = None, combustible: str | None = None, motor: str | None = None,
                   color: str | None = None) -> dict:
    """Detalle de un modelo: por acabado, motor y color, por familia de color y, en VO, precio por
    antigüedad y km con filtros de acabado, combustible y motor. El filtro de color vale para toda la
    página. Lee solo panel_mercado_segmento (precalculada); nunca los hechos. `filtro` es el que vale de
    verdad (los valores que no existen se descartan) y `url`, la de la página."""
    nombres = etiquetas_zona(conc)
    n_mostrar, n_min = conc.n_minimo_mostrar, conc.n_minimo_medianas
    nombre_tipo = {"VN": "Nuevos", "VO": "Ocasión"}[tipo]
    hay = existentes(pg, modelo)
    filtro = validar(hay[(tipo, zona)], acabado, combustible, motor, color)
    col = filtro[3]

    def url_en(t: str, z: str) -> str:   # otra zona u otro tipo, con los filtros que sigan existiendo
        return url_detalle(modelo, t, z, *validar(hay[(t, z)], *filtro))

    base = {
        "tipo": tipo, "zona": zona, "zona_nombre": nombres[zona], "modelo": modelo, "nombre_tipo": nombre_tipo,
        "capturas": capturas(pg, hoy), "volver": f"/mercado?zona={zona}&tipo={tipo}",
        "zonas": [{"id": z, "etiqueta": nombres[z], "activa": z == zona, "url": url_en(tipo, z)} for z in ZONAS],
        "tipos": [{"id": t, "etiqueta": e, "activa": t == tipo, "url": url_en(t, zona)}
                  for t, e in (("VN", "Nuevos (VN)"), ("VO", "Ocasión (VO)"))],
        "hay_datos": False, "hay_modelo": False, "nota_precio": NOTA_PRECIO, "n_minimo": n_min,
        "n_mostrar": n_mostrar, "filtro": filtro, "color": col, "url": url_detalle(modelo, tipo, zona, *filtro),
    }
    dia = pg.execute("SELECT max(dia) FROM panel_mercado_dia WHERE tipo = %s", (tipo,)).fetchone()[0]
    if dia is None:
        return base
    base.update({"hay_datos": True, "dia": dia, "es_hoy": dia == hoy, "dia_largo": fmt.fecha_larga(dia)})
    with pg.cursor(row_factory=dict_row) as cur:
        filas = cur.execute(
            """SELECT nivel, acabado, acabado_reconocido, combustible, motor, antiguedad_orden, km_orden, color,
                      anuncios, propios, con_color, n_precio, precio_p25_cents, precio_p50_cents, precio_p75_cents,
                      dias_mediana, dias_cota
               FROM panel_mercado_segmento WHERE dia = %s AND tipo = %s AND zona = %s AND modelo = %s""",
            (dia, tipo, zona, modelo)).fetchall()
    if not filas:
        return base
    con_peso = zona == "provincia"
    pct = conc.n_minimo_porcentaje
    sin_esp = sin_especificar()

    def segmento(f) -> dict:
        n = f["anuncios"]
        return {"anuncios": fmt.miles(n), "n": n, "nuestros": nuestros(f["propios"], n, pct, con_peso),
                "precio": cifra_precio(f["precio_p50_cents"], f["precio_p25_cents"], f["precio_p75_cents"],
                                       f["n_precio"], n_mostrar, n_min),
                "dias": cifra_dias(f["dias_mediana"], f["dias_cota"], n, n_mostrar, n_min)}

    por_nivel: dict[str, list] = {}
    for f in filas:
        if col is None or f["color"] in (None, col):   # con color elegido, solo sus filas (y las sin color)
            por_nivel.setdefault(f["nivel"], []).append(f)
    modelo_fila = por_nivel["modelo"][0]
    cobertura_modelo = cobertura_color(modelo_fila["con_color"], modelo_fila["anuncios"])
    # Con color elegido, los totales, acabados y motores son los de esa familia
    total = next(f for f in por_nivel["color"] if f["color"] == col) if col else modelo_fila
    n_acabado, n_motor = ("acabado_color", "motor_color") if col else ("acabado", "motor")

    # Por acabado y motor; sin color elegido, cada motor despliega sus familias de color
    colores_motor: dict[tuple, list] = {}
    if col is None:
        for f in por_nivel.get("motor_color", []):
            colores_motor.setdefault((f["acabado"], f["combustible"], f["motor"]), []).append(f)
    acabados = []
    for f in sorted(por_nivel.get(n_acabado, []), key=lambda f: (not f["acabado_reconocido"], -f["anuncios"],
                                                                 f["acabado"])):
        motores = [{"nombre": m["motor"], **segmento(m),
                    "colores": [{"nombre": c["color"], **segmento(c)} for c in sorted(
                        colores_motor.get((m["acabado"], m["combustible"], m["motor"]), []), key=_orden_color(sin_esp))]}
                   for m in sorted((m for m in por_nivel.get(n_motor, []) if m["acabado"] == f["acabado"]),
                                   key=lambda m: (-m["anuncios"], m["motor"]))]
        acabados.append({"nombre": f["acabado"], "reconocido": f["acabado_reconocido"], **segmento(f),
                         "motores": motores})

    matriz = None
    if tipo == "VO" and conc.tramos_antiguedad and conc.tramos_km:
        matriz = _matriz(por_nivel, hay[(tipo, zona)], filtro, acabados, conc, n_mostrar, n_min,
                         lambda *f: url_detalle(modelo, tipo, zona, *f))
        sin_tramos = "color_antiguedad_km" if col else "antiguedad_km"
        matriz["fuera"] = total["anuncios"] - sum(f["anuncios"] for f in por_nivel.get(sin_tramos, []))

    # Filtro de color de la página: las familias del modelo; cada una conserva los demás filtros que existan con ella
    familias = sorted(por_nivel.get("color", []) if col is None else
                      [f for f in filas if f["nivel"] == "color"], key=_orden_color(sin_esp))
    filtro_color = [{"etiqueta": "Todos", "activa": col is None,
                     "url": url_detalle(modelo, tipo, zona, *validar(hay[(tipo, zona)], *filtro[:3], None))}] + [
        {"etiqueta": f["color"], "activa": f["color"] == col,
         "url": url_detalle(modelo, tipo, zona, *validar(hay[(tipo, zona)], *filtro[:3], f["color"]))}
        for f in familias]

    # Por color: todas las familias (con el filtro, la elegida marcada); enlazan al filtro
    colores = {"filas": [{"nombre": f["color"], **segmento(f), "activa": f["color"] == col,
                          "url": filtro_color[i + 1]["url"]} for i, f in enumerate(familias)],
               "cobertura": cobertura_modelo}

    # Si en la zona elegida la mayoría de las casillas quedan en «—», se propone ver España
    casillas = [a["precio"] for a in acabados] + [m["precio"] for a in acabados for m in a["motores"]]
    if matriz:
        casillas += [c["precio"] for fila in matriz["filas"] for c in fila["celdas"]]
    pocos = sum(1 for c in casillas if c["pocos"])
    nota_espana = None
    if zona != "espana" and casillas and pocos * 2 > len(casillas):
        nota_espana = {"texto": (f"En {nombres[zona]}, {pocos} de {len(casillas)} casillas tienen menos de "
                                 f"{n_mostrar} anuncios con precio."),
                       "url": url_en(tipo, "espana")}

    filtrando = None
    if col:
        filtrando = (f"Filtrando por color: solo cuentan los anuncios con color conocido "
                     f"({cobertura_modelo['pct']} % del modelo)" if cobertura_modelo["pct"] is not None else
                     "Filtrando por color: solo cuentan los anuncios con color conocido")

    notas = [
        f"Precio: mediana de los anuncios con precio publicados {'hoy' if dia == hoy else 'ese día'}; debajo, "
        "el rango p25–p75 (la mitad central de los precios). Días publicado: mediana; «≥ N», como en la tabla "
        "por modelo.",
        f"Con menos de {n_min} anuncios, la cifra va atenuada con su n; con menos de {n_mostrar}, «—».",
        "Acabado: el que figura en el texto de la versión. «Sin clasificar»: anuncios cuyo acabado no se "
        "reconoce; no se adivina.",
        f"Color: la familia del color exterior que publica la ficha de cada anuncio. «{sin_esp}»: "
        "con color, pero sin una familia reconocible en su nombre. El filtro de color vale para toda la página.",
    ]
    if matriz:
        notas.append(("Antigüedad: años cumplidos desde la primera matriculación hasta el día de los datos. "
                      + (f"{fmt.miles(matriz['fuera'])} anuncios {'de ese color ' if col else 'del modelo '}"
                         "sin fecha de matriculación o sin km no entran en la matriz." if matriz["fuera"] else "")).strip())
        notas.append("Filtros de la matriz: el motor es combustible, potencia, tecnología y cambio; solo se "
                     "ofrecen los que tienen anuncios en la matriz con los demás filtros elegidos (y con el color, "
                     "si hay uno elegido).")
    return {**base, "hay_modelo": True, "total": segmento(total), "acabados": acabados, "matriz": matriz,
            "colores": colores, "filtro_color": filtro_color, "filtrando": filtrando,
            "cobertura_tabla": cobertura_modelo if cobertura_modelo["nota"] else None,
            "nota_espana": nota_espana, "notas": notas}


def _matriz(por_nivel: dict, hay: Existentes, filtro: Filtro, acabados: list[dict], conc: Concesionario,
            n_mostrar: int, n_min: int, url) -> dict:
    """La matriz antigüedad × km del filtro y las opciones de acabado, combustible y motor (solo las que
    existen, con el color elegido si lo hay). `por_nivel` ya trae solo las filas del color elegido."""
    trios, cuartetos, _ = hay
    a, c, m, col = filtro
    if col:
        trios = {q[:3] for q in cuartetos if q[3] == col}
    base = ("acabado_" if a else "") + ("motor_" if m else "combustible_" if c else "")
    nivel = base + ("color_" if col else "") + "antiguedad_km"

    def del_filtro(f) -> bool:
        return ((a is None or f["acabado"] == a) and (c is None or f["combustible"] == c)
                and (m is None or f["motor"] == m))

    celdas = {(f["antiguedad_orden"], f["km_orden"]): f for f in por_nivel.get(nivel, []) if del_filtro(f)}
    filas = []
    for i, (_, etiqueta) in enumerate(conc.tramos_antiguedad):
        fila = []
        for j, (_, km) in enumerate(conc.tramos_km):
            f = celdas.get((i, j))
            n = f["n_precio"] if f else 0
            fila.append({"km": km, "n": n, "anuncios": f["anuncios"] if f else 0,
                         "precio": cifra_precio(f and f["precio_p50_cents"], f and f["precio_p25_cents"],
                                                f and f["precio_p75_cents"], n, n_mostrar, n_min)})
        filas.append({"etiqueta": etiqueta, "celdas": fila})

    def opcion(etiqueta: str, valor: tuple, activa: bool) -> dict:
        return {"etiqueta": etiqueta, "activa": activa, "url": url(*validar(hay, *valor, col))}

    # Orden: acabados como en la tabla; combustibles y motores, por anuncios en la matriz (del color elegido)
    con_a = [t for t in trios if a is None or t[0] == a]
    en_matriz: dict[tuple, int] = {}
    for f in por_nivel.get("acabado_motor_color_antiguedad_km" if col else "acabado_motor_antiguedad_km", []):
        if a is None or f["acabado"] == a:
            for k in ((f["combustible"],), (f["combustible"], f["motor"])):
                en_matriz[k] = en_matriz.get(k, 0) + f["anuncios"]
    combustibles = sorted({t[1] for t in con_a}, key=lambda x: (-en_matriz.get((x,), 0), x))
    motores = sorted({(t[1], t[2]) for t in con_a if c is None or t[1] == c},
                     key=lambda x: (-en_matriz.get(x, 0), x))
    con_matriz = {t[0] for t in trios}
    filtros = [
        {"id": "acabado", "nombre": "Acabado", "opciones": [opcion("Todos", (None, c, m), a is None)] + [
            opcion(x["nombre"], (x["nombre"], c, m), x["nombre"] == a) for x in acabados if x["nombre"] in con_matriz]},
        {"id": "combustible", "nombre": "Combustible", "opciones": [opcion("Todos", (a, None, None), c is None)] + [
            opcion(x, (a, x, m), x == c) for x in combustibles]},
        {"id": "motor", "nombre": "Motor", "opciones": [opcion("Todos", (a, c, None), m is None)] + [
            opcion(mo, (a, co, mo), (co, mo) == (c, m)) for co, mo in motores]},
    ]
    # Cobertura del color en lo que filtran los otros tres (solo con un color elegido y por debajo del 90 %)
    cobertura = None
    if col:
        sin = [f for f in por_nivel.get(base + "antiguedad_km", []) if del_filtro(f)]
        con = [f["con_color"] for f in sin]
        cobertura = cobertura_color(None if any(x is None for x in con) else sum(con), sum(f["anuncios"] for f in sin))
        cobertura = cobertura if cobertura["nota"] else None
    elegido = " · ".join(x for x in (a, m or c, col) if x)
    return {"columnas": [km for _, km in conc.tramos_km], "filas": filas, "filtros": filtros, "elegido": elegido,
            "nivel": nivel, "cobertura": cobertura}


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


ESTADOS_FICHA = {"ok": "Con ficha leída", "listing_color": "Color del listado", "pending": "Pendiente",
                 "empty": "Ficha sin color", "error": "Error al leer", "exhausted": "Agotados los intentos",
                 "sin_ficha": "Sin ficha en la exportación"}


def cobertura_colores(pg: psycopg.Connection) -> dict:
    """«Estado de las fuentes»: % de anuncios con color por fuente y estado de la ficha (última captura
    completa de cada fuente) y los 20 nombres de color sin familia más frecuentes (último día)."""
    filas = pg.execute(
        """SELECT c.fuente, c.dia, c.estado, c.anuncios, c.con_color, c.bastidor_distinto FROM panel_mercado_color_fuente c
           JOIN (SELECT fuente, max(dia) AS dia FROM panel_mercado_color_fuente GROUP BY fuente) u USING (fuente, dia)
           ORDER BY c.anuncios DESC, c.estado""").fetchall()
    fuentes = []
    for fuente, etiqueta in FUENTES:
        mias = [f for f in filas if f[0] == fuente]
        if not mias:
            continue
        n, con, vin = (sum(f[i] for f in mias) for i in (3, 4, 5))
        fuentes.append({
            "fuente": fuente, "etiqueta": etiqueta, "dia": fmt.fecha(mias[0][1]), "anuncios": fmt.miles(n),
            "con_color": fmt.miles(con), "porcentaje": fmt.porcentaje(con / n * 100, 1) if n else fmt.GUION,
            "vin_distinto": vin,
            "estados": [{"estado": ESTADOS_FICHA.get(e, e), "codigo": e, "anuncios": fmt.miles(a),
                         "con_color": fmt.miles(c), "porcentaje": fmt.porcentaje(c / a * 100, 0) if a else fmt.GUION,
                         "vin_distinto": fmt.miles(v)} for _, _, e, a, c, v in mias],
        })
    textos = [{"tipo": t, "color": c, "anuncios": fmt.miles(n)} for t, c, n in pg.execute(
        """SELECT s.tipo, s.color, s.anuncios FROM panel_mercado_color_sin_familia s
           JOIN (SELECT tipo, max(dia) AS dia FROM panel_mercado_segmento GROUP BY tipo) u USING (tipo, dia)
           ORDER BY s.anuncios DESC, s.tipo, s.color LIMIT 20""")]
    return {"fuentes": fuentes, "textos": textos}
