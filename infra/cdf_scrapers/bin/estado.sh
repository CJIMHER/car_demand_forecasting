#!/usr/bin/env bash
# estado.sh — resumen de los scrapers: próximas ejecuciones, últimas capturas y disco.
BASE="${CDF_BASE:-/opt/cdf}"

echo "== Próximas ejecuciones"
systemctl list-timers 'cdf-scraper-*' --no-pager 2>/dev/null || echo "(systemd no disponible)"

echo
echo "== Últimas ejecuciones (scraper: 0 bien, 1 error o revisión, 3 robots.txt; subida: 0 bien)"
tail -n 10 "$BASE/logs/estado.log" 2>/dev/null || echo "(todavía ninguna)"

for n in vo vn vn_otros; do
  f="$BASE/data/$n/exports/runs.csv"
  [ -f "$f" ] || continue
  echo
  echo "== $n: últimas capturas (día, estado, anuncios, error)"
  python3 - "$f" <<'PY'
import csv, sys
filas = list(csv.DictReader(open(sys.argv[1], encoding="utf-8-sig")))
for r in filas[-5:]:
    print(f"  {r['snapshot_day']}  {r['status']:<11} {r['observed_count'] or '-':>6}  {(r['error'] or '')[:70]}")
PY
done

echo
df -h / | awk 'NR==2 {print "== Disco: " $3 " usados de " $2 " (" $5 ")"}'
