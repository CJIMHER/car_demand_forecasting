"""A. Comprobacion fuera de muestra de en.wikipedia lag 9, ene-abr 2026."""
import sys, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy import stats
from sklearn.linear_model import HuberRegressor
sys.path.insert(0, sys.argv[3]); 
MAT, WIK = sys.argv[1], sys.argv[2]
src = open(sys.argv[3] + "/confirm_lag4.py").read()
NAME_MAP = {}; exec(src[src.index("NAME_MAP"):src.index("idx =")])
idx = pd.date_range("2018-01-01", "2026-04-01", freq="MS")
mat = pd.read_csv(MAT, parse_dates=["month"]); w = pd.read_csv(WIK, parse_dates=["month"])
Y = {k: mat[mat.model_raw == v].groupby("month").units.sum().reindex(idx).fillna(0).astype(float) for k, v in NAME_MAP.items()}
G = {k: w[(w.modelo == k) & (w.wiki == "en")].set_index("month").views.reindex(idx).astype(float) for k in NAME_MAP}
E = {}
for k in NAME_MAP:
    y, g = Y[k], G[k]
    both = (y > 0) & (g > 0)
    if not both.any(): continue
    first = idx[both.values].min(); ys, gs = y[y.index >= first], g[g.index >= first]
    if len(ys) >= 60 and (ys == 0).sum() == 0 and (gs == 0).sum() < 12 and gs.notna().all():
        E[k] = (np.log(ys).diff(), np.log1p(gs).diff())
own, h = [4, 5, 12], 4
def run(yd, gd, gl, ev):
    df = pd.DataFrame({"y": yd}); [df.__setitem__(f"y{o}", yd.shift(o)) for o in own]; df["g"] = gd.shift(gl); df = df.dropna()
    oc = [f"y{o}" for o in own]; out = []
    for t in df.index[(df.index >= ev[0]) & (df.index <= ev[1])]:
        tr = df[df.index <= t - pd.DateOffset(months=h)]
        ms = HuberRegressor(max_iter=500).fit(tr[oc].values, tr.y.values); mb = HuberRegressor(max_iter=500).fit(tr[oc+["g"]].values, tr.y.values)
        te = df.loc[[t]]; out.append((te.y.values[0] - ms.predict(te[oc].values)[0], te.y.values[0] - mb.predict(te[oc+["g"]].values)[0], mb.coef_[-1]))
    return out
EV = (pd.Timestamp("2026-01-01"), pd.Timestamp("2026-04-01"))
rows = []
for gl in range(4, 13):
    for k, (yd, gd) in E.items():
        for i, (es, eb, c) in enumerate(run(yd, gd, gl, EV)):
            rows.append(dict(glag=gl, modelo=k, es=abs(es), eb=abs(eb), coef=c))
R = pd.DataFrame(rows)
def summ(d):
    pm = d.groupby("modelo").agg(es=("es", "mean"), eb=("eb", "mean"), coef=("coef", "last"))
    gain = 100 * (1 - pm.eb / pm.es); pos = int((pm.coef > 0).sum())
    return pd.Series({"modelos": len(pm), "mejora_agregada_%": 100 * (1 - d.eb.sum() / d.es.sum()), "mediana_modelo_%": gain.median(),
                      "%_mejoran": 100 * (gain > 0).mean(), "%_coef_pos": 100 * pos / len(pm),
                      "p_signo": stats.binomtest(pos, len(pm), 0.5, alternative="greater").pvalue})
S = R.groupby("glag").apply(summ); print(S.round(3).to_string())
s9 = S.loc[9]; c = [s9["mejora_agregada_%"] > 0, s9["mediana_modelo_%"] > 0, s9["p_signo"] < 0.05]
print("Regla lag 9:", c, "=>", "SIGUE VIVA" if all(c) else "REFUTADA")
# referencia: lo mismo con es
