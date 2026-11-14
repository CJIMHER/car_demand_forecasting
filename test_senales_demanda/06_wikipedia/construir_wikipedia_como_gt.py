"""Convierte wikipedia_pageviews.csv al formato del xlsx de Google Trends para reutilizar los scripts 02, 04 y 05.
cat47 = es.wikipedia, cat473 = en.wikipedia; 2018-01 a 2025-12. Uso: python construir_wikipedia_como_gt.py CSV SALIDA.xlsx"""
import sys, pandas as pd
w = pd.read_csv(sys.argv[1], parse_dates=["month"]); w = w[(w.month >= "2018-01-01") & (w.month <= "2025-12-01")]
with pd.ExcelWriter(sys.argv[2]) as x:
    for wiki, sh in (("es", "tidy_cat47_autos"), ("en", "tidy_cat473_compra")):
        w[w.wiki == wiki].rename(columns={"views": "gt"})[["month", "modelo", "gt"]].to_excel(x, sheet_name=sh, index=False)
