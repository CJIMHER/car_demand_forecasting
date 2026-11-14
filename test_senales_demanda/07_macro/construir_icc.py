"""ICC del CIS según el conjunto de información disponible (decisión del autor, 1-nov-2026 18:04).
Si el CIS no publica el ICC de un mes, se mantiene el último valor publicado (LOCF) hasta la siguiente
observación. Interpretación: «último ICC disponible», no una estimación del ICC latente de ese mes.
icc_status = OBSERVED | CARRIED_FORWARD. La bandera NO es regresor.
Entrada: icc_cis_raw.csv (valores tal como los publica el CIS). Salida: icc_cis.csv, de 2012-11 a 2026-07.
"""
import csv, sys
FIN = "2026-07"   # último mes publicado a 1-nov-2026; la ventana del estudio acaba en 2026-04
def main(raw="icc_cis_raw.csv", out="icc_cis.csv"):
    rows = [r for r in csv.DictReader(open(raw, encoding="utf-8")) if r["mes"] <= FIN]
    last = None
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["mes", "icc", "situacion_actual", "expectativas", "icc_status", "fuente_url"])
        for r in rows:
            if r["icc"]:
                last = r; w.writerow([r["mes"], r["icc"], r["situacion_actual"], r["expectativas"], "OBSERVED", r["fuente_url"]])
            else:
                assert last is not None, r["mes"]
                w.writerow([r["mes"], last["icc"], last["situacion_actual"], last["expectativas"], "CARRIED_FORWARD",
                            f"LOCF de {last['mes']}: {last['fuente_url']}"])
if __name__ == "__main__":
    main(*sys.argv[1:])
