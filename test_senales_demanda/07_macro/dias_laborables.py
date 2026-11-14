"""Días laborables por mes en España (lunes a viernes menos festivos comunes a todo el territorio).
Regla congelable (F13): cuenta L–V; resta solo los festivos que el BOE publica como comunes
a TODAS las comunidades autónomas en la resolución anual de la Dirección General de Trabajo.
No se restan festivos autonómicos ni locales, ni traslados a lunes que no sean comunes.
Fuente de fechas: paquete `holidays` (ES, sin subdivisión), contrastado año a año con el BOE.
"""
import calendar, csv, datetime as dt, sys, holidays
INI, FIN = (2012, 11), (2026, 12)
def meses():
    y, m = INI
    while (y, m) <= FIN:
        yield y, m
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
def main(out):
    fes = holidays.ES(years=range(INI[0], FIN[0] + 1))
    with open(out, "w", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["mes", "lv", "festivos_lv", "laborables", "festivos"])
        for y, m in meses():
            dias = [dt.date(y, m, d) for d in range(1, calendar.monthrange(y, m)[1] + 1)]
            lv = [d for d in dias if d.weekday() < 5]
            fl = [d for d in lv if d in fes]
            w.writerow([f"{y}-{m:02d}", len(lv), len(fl), len(lv) - len(fl), ";".join(d.isoformat() for d in fl)])
if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "laborables_es.csv")
