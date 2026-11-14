"""
descargar_wikipedia.py — vistas mensuales de Wikipedia por modelo de coche.
Ejecutar en Google Colab. No necesita clave: la API de Wikimedia es publica.

Salida: wikipedia_pageviews.csv con columnas month, modelo, wiki, views
(wiki = es o en). Se descargan es.wikipedia (principal) y en.wikipedia (contraste).
Si un titulo no existe, el script lo dice al final para que lo corrijas.
"""
import requests, time
import pandas as pd

# Titulos de los articulos (es.wikipedia / en.wikipedia). Ajusta si alguno falla.
ARTICULOS = {
    "dacia_sandero": ("Dacia_Sandero", "Dacia_Sandero"),
    "hyundai_tucson": ("Hyundai_Tucson", "Hyundai_Tucson"),
    "kia_sportage": ("Kia_Sportage", "Kia_Sportage"),
    "toyota_chr": ("Toyota_C-HR", "Toyota_C-HR"),
    "toyota_corolla": ("Toyota_Corolla", "Toyota_Corolla"),
    "dacia_duster": ("Dacia_Duster", "Dacia_Duster"),
    "seat_ibiza": ("SEAT_Ibiza", "SEAT_Ibiza"),
    "toyota_yaris": ("Toyota_Yaris", "Toyota_Yaris"),
    "seat_arona": ("SEAT_Arona", "SEAT_Arona"),
    "hyundai_kona": ("Hyundai_Kona", "Hyundai_Kona"),
    "renault_clio": ("Renault_Clio", "Renault_Clio"),
    "renault_captur": ("Renault_Captur", "Renault_Captur"),
    "seat_leon": ("SEAT_León", "SEAT_León"),
    "nissan_qashqai": ("Nissan_Qashqai", "Nissan_Qashqai"),
    "peugeot_2008": ("Peugeot_2008", "Peugeot_2008"),
    "kia_stonic": ("Kia_Stonic", "Kia_Stonic"),
    "vw_troc": ("Volkswagen_T-Roc", "Volkswagen_T-Roc"),
    "seat_ateca": ("SEAT_Ateca", "SEAT_Ateca"),
    "mg_zs": ("MG_ZS", "MG_ZS"),
    "toyota_yaris_cross": ("Toyota_Yaris_Cross", "Toyota_Yaris_Cross"),
    "peugeot_3008": ("Peugeot_3008", "Peugeot_3008"),
    "toyota_rav4": ("Toyota_RAV4", "Toyota_RAV4"),
    "peugeot_208": ("Peugeot_208", "Peugeot_208"),
    "hyundai_i20": ("Hyundai_i20", "Hyundai_i20"),
    "kia_niro": ("Kia_Niro", "Kia_Niro"),
    "citroen_c3": ("Citroën_C3", "Citroën_C3"),
    "kia_ceed": ("Kia_Ceed", "Kia_Ceed"),
    "vw_tiguan": ("Volkswagen_Tiguan", "Volkswagen_Tiguan"),
    "ford_focus": ("Ford_Focus", "Ford_Focus"),
    "vw_golf": ("Volkswagen_Golf", "Volkswagen_Golf"),
    "ford_kuga": ("Ford_Kuga", "Ford_Kuga"),
    "vw_tcross": ("Volkswagen_T-Cross", "Volkswagen_T-Cross"),
    "opel_corsa": ("Opel_Corsa", "Opel_Corsa"),
    "kia_xceed": ("Kia_XCeed", "Kia_XCeed"),
    "nissan_juke": ("Nissan_Juke", "Nissan_Juke"),
    "hyundai_i30": ("Hyundai_i30", "Hyundai_i30"),
    "citroen_c3_aircross": ("Citroën_C3_Aircross", "Citroën_C3_Aircross"),
    "mazda_cx5": ("Mazda_CX-5", "Mazda_CX-5"),
    "vw_polo": ("Volkswagen_Polo", "Volkswagen_Polo"),
    "ford_puma": ("Ford_Puma_(crossover)", "Ford_Puma_(crossover)"),
    "renault_megane": ("Renault_Mégane", "Renault_Mégane"),
    "tesla_model3": ("Tesla_Model_3", "Tesla_Model_3"),
    "peugeot_308": ("Peugeot_308", "Peugeot_308"),
    "bmw_x1": ("BMW_X1", "BMW_X1"),
    "skoda_karoq": ("Škoda_Karoq", "Škoda_Karoq"),
    "mazda_cx30": ("Mazda_CX-30", "Mazda_CX-30"),
    "renault_arkana": ("Renault_Arkana", "Renault_Arkana"),
    "citroen_c4": ("Citroën_C4", "Citroën_C4"),
    "skoda_fabia": ("Škoda_Fabia", "Škoda_Fabia"),
    "volvo_xc40": ("Volvo_XC40", "Volvo_XC40"),
    "audi_q3": ("Audi_Q3", "Audi_Q3"),
    "audi_q5": ("Audi_Q5", "Audi_Q5"),
    "vw_taigo": ("Volkswagen_Taigo", "Volkswagen_Taigo"),
}

INICIO, FIN = "2016010100", "2026043000"
HEADERS = {"User-Agent": "demanda-vn-test/1.0 (uso academico; contacto por correo del proyecto)"}
BASE = "https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/{wiki}.wikipedia/all-access/user/{title}/monthly/{ini}/{fin}"

import os

# Reanuda: si ya existe un CSV parcial, no vuelve a pedir lo que ya tiene
SALIDA = "wikipedia_pageviews.csv"
previo = pd.read_csv(SALIDA) if os.path.exists(SALIDA) else pd.DataFrame(columns=["month", "modelo", "wiki", "views"])
hechos = set(zip(previo.modelo, previo.wiki))
filas, fallos = previo.to_dict("records"), []

def pedir(url, intentos=6):
    """GET con reintentos y espera creciente ante 429 (limite de peticiones)."""
    espera = 5
    for _ in range(intentos):
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 429:
            time.sleep(espera); espera = min(espera * 2, 120); continue
        return r
    return r

for clave, (t_es, t_en) in ARTICULOS.items():
    for wiki, title in (("es", t_es), ("en", t_en)):
        if (clave, wiki) in hechos:
            continue
        url = BASE.format(wiki=wiki, title=requests.utils.quote(title, safe=""), ini=INICIO, fin=FIN)
        try:
            r = pedir(url)
            if r.status_code != 200:
                fallos.append((clave, wiki, title, r.status_code)); continue
            for it in r.json().get("items", []):
                filas.append({"month": pd.Timestamp(it["timestamp"][:8]).replace(day=1).strftime("%Y-%m-01"),
                              "modelo": clave, "wiki": wiki, "views": it["views"]})
            print(f"OK  {clave:<20} {wiki}  {title}")
            pd.DataFrame(filas).to_csv(SALIDA, index=False)   # guarda tras cada articulo
        except Exception as e:
            fallos.append((clave, wiki, title, str(e)))
        time.sleep(2.0)   # ~30 peticiones/minuto: por debajo del limite de la API

df = pd.DataFrame(filas)
df.to_csv(SALIDA, index=False)
print(f"\nGuardado {SALIDA}: {len(df)} filas, {df.modelo.nunique()} modelos")
if fallos:
    print("\nTitulos que han fallado (corrige y reejecuta solo esos):")
    for f in fallos: print("  ", f)
try:
    from google.colab import files; files.download(SALIDA)
except ImportError:
    pass
