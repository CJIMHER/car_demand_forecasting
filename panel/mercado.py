"""Vista «Mercado»: lee las tablas panel_mercado_* ya preparadas (nunca las de hechos) y las
prepara para la plantilla. Los textos propios del concesionario salen de config/concesionario.yaml.
"""

from __future__ import annotations

from datetime import date, timedelta

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
    for (modelo, n, hace7, altas7, ausencias7, precio, n_precio, dias, cota) in pg.execute(
            """SELECT modelo, anuncios, anuncios_hace7, altas_7, ausencias_7, precio_mediano_cents, n_precio,
                      dias_mediana, dias_cota
               FROM panel_mercado_modelo WHERE dia = %s AND tipo = %s AND zona = %s
               ORDER BY anuncios DESC, modelo""", (dia, tipo, zona)):
        modelos.append({
            "modelo": modelo, "anuncios": fmt.miles(n),
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
             f"solo si hace 7 días había al menos {conc.n_minimo_porcentaje} anuncios.",
             f"El precio y los días son medianas de los anuncios publicados {'hoy' if es_hoy else 'ese día'}.",
             "«≥ N»: parte de esos anuncios ya estaban publicados en la primera captura o tras un día sin "
             f"captura, así que llevan al menos N días. Con menos de {n_min} anuncios, la cifra va atenuada con su n."]
    if tipo == "VN":
        notas.insert(0, "VN incluye gasolina, diésel, eléctricos e híbridos.")
    return {
        **base, "hay_datos": True, "dia": dia, "es_hoy": es_hoy, "dia_largo": fmt.fecha_larga(dia),
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
    return {"anotaciones": anotaciones, "dias": dias}
