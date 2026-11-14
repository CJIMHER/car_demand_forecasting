"""B. Tipos de credito al consumo (BdE 19.4) y expectativas ICC (CIS) -> matriculaciones totales. Ver prerregistro."""
import sys, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from scipy import stats
from sklearn.linear_model import HuberRegressor
MAT, BE, ICC, LAB, OUT = sys.argv[1:6]; NSUR = int(sys.argv[6]); RNG = np.random.default_rng(2026)
MES = dict(ENE=1, FEB=2, MAR=3, ABR=4, MAY=5, JUN=6, JUL=7, AGO=8, SEP=9, OCT=10, NOV=11, DIC=12)
b = pd.read_csv(BE, encoding="latin-1", header=None); col = list(b.iloc[0]).index("DN_1TI2T0009")
b = b.iloc[6:][b.iloc[6:, 0].str.match(r"^[A-Z]{3} \d{4}$", na=False)]
r = pd.Series(pd.to_numeric(b.iloc[:, col], errors="coerce").values,
              index=[pd.Timestamp(int(s[4:]), MES[s[:3]], 1) for s in b.iloc[:, 0]])
ic = pd.read_csv(ICC); e = pd.Series(ic.expectativas.values, index=pd.to_datetime(ic.mes + "-01"))
lb = pd.read_csv(LAB); lab = pd.Series(lb.laborables.values.astype(float), index=pd.to_datetime(lb.mes + "-01"))
m = pd.read_csv(MAT, parse_dates=["month"]); Y = m[m.model_raw.str.upper() == "TOTAL"].groupby("month").units.sum()
idx = pd.date_range("2013-01-01", "2026-04-01", freq="MS")
r, e, lab, Y = (s.reindex(idx) for s in (r, e, lab, Y))
EV = (pd.Timestamp("2023-01-01"), pd.Timestamp("2026-04-01")); h = 4

def frames(Y, x, kind, xlag, spec):
    ly = np.log(Y)
    if spec == "B1":
        y = ly.diff(); base = pd.DataFrame({"y": y, "a": y.shift(4), "b": y.shift(5), "c": y.shift(12), "d": lab.diff()})
        z = x.diff().shift(xlag)
    else:
        s3 = np.log(Y.rolling(3).sum()); y = s3 - s3.shift(12); l3 = lab.rolling(3).sum()
        base = pd.DataFrame({"y": y, "a": y.shift(4), "b": y.shift(5), "d": l3 - l3.shift(12)})
        z = (x - x.shift(12)).shift(xlag)
    base["z"] = z; return base.dropna()

def run(df):
    bc = [c for c in df.columns if c not in ("y", "z")]; es, eb, ps, pb, cf = [], [], [], [], []
    for t in df.index[(df.index >= EV[0]) & (df.index <= EV[1])]:
        tr = df[df.index <= t - pd.DateOffset(months=h)]; te = df.loc[[t]]
        ms = HuberRegressor(max_iter=1000).fit(tr[bc].values, tr.y.values); mb = HuberRegressor(max_iter=1000).fit(tr[bc + ["z"]].values, tr.y.values)
        p0, p1 = ms.predict(te[bc].values)[0], mb.predict(te[bc + ["z"]].values)[0]; yt = te.y.values[0]
        es.append(yt - p0); eb.append(yt - p1); ps.append(p0); pb.append(p1); cf.append(mb.coef_[-1] * tr.z.std())
    es, eb, ps, pb = map(np.array, (es, eb, ps, pb))
    f = es**2 - (eb**2 - (ps - pb)**2); n = len(f); fc = f - f.mean(); L = h + 2; v = fc @ fc / n
    for j in range(1, L + 1): v += 2 * (1 - j / (L + 1)) * (fc[j:] @ fc[:-j] / n)
    return dict(n=n, gain=100 * (1 - np.abs(eb).mean() / np.abs(es).mean()), p_CW=1 - stats.norm.cdf(f.mean() / np.sqrt(max(v, 1e-12) / n)),
                coef_sd=np.mean(cf), pct_signo=None, cfs=np.array(cf))

def phase_sur(x):
    x = x.dropna(); f = np.fft.rfft(x.values - x.mean()); ph = RNG.uniform(0, 2*np.pi, len(f)); ph[0] = 0
    if len(x) % 2 == 0: ph[-1] = 0
    return pd.Series(np.fft.irfft(np.abs(f) * np.exp(1j*ph), n=len(x)) + x.mean(), index=x.index).reindex(idx)

tests = [("B1r", "B1", r, 6, -1), ("B1e", "B1", e, 5, +1), ("B2r", "B2", r, 6, -1), ("B2e", "B2", e, 5, +1)]
rows = []
for name, spec, x, lag, sg in tests:
    o = run(frames(Y, x, None, lag, spec)); o["pct_signo"] = 100 * np.mean(np.sign(o["cfs"]) == sg)
    sur = [run(frames(Y, phase_sur(x[x.index >= "2012-01-01"]), None, lag, spec))["gain"] for _ in range(NSUR)]
    o["p95_sur"] = np.percentile(sur, 95); o["p_sur"] = (np.sum(np.array(sur) >= o["gain"]) + 1) / (NSUR + 1)
    pl = {L: run(frames(Y, x, None, L, spec))["gain"] for L in range(lag, 13)}
    o["placebos"] = " ".join(f"{L}:{g:+.1f}" for L, g in pl.items())
    o.update(test=name); o.pop("cfs"); rows.append(o)
R = pd.DataFrame(rows).set_index("test")
p = R.p_CW.sort_values(); k = len(p); holm = {}; run_max = 0
for i, (t, pv) in enumerate(p.items()): run_max = max(run_max, min(1, (k - i) * pv)); holm[t] = run_max
R["p_CW_holm"] = pd.Series(holm)
R["admitida"] = (R.gain > 0) & (R.p_CW_holm < 0.05) & (R.pct_signo >= 80) & (R.gain > R.p95_sur)
pd.set_option("display.width", 250); print(R.drop(columns="placebos").round(3).to_string()); print(R.placebos.to_string())
R.to_csv(f"{OUT}/macro_total.csv")

# Secundario: panel de modelos (descriptivo)
src = open(sys.argv[7] + "/confirm_lag4.py").read(); NAME_MAP = {}; exec(src[src.index("NAME_MAP"):src.index("idx =")])
P = []
for k, v in NAME_MAP.items():
    y = m[m.model_raw == v].groupby("month").units.sum().reindex(idx)
    y = y[y.index >= "2018-01-01"]
    if y.isna().any() or (y <= 0).any() or len(y) < 60: continue
    for name, spec, x, lag, sg in tests:
        o = run(frames(y.reindex(idx), x, None, lag, spec)); P.append(dict(modelo=k, test=name, gain=o["gain"], cw5=o["p_CW"] < .05,
                                                                        signo_ok=np.sign(o["coef_sd"]) == sg))
P = pd.DataFrame(P); P.to_csv(f"{OUT}/macro_panel.csv", index=False)
print("\nPanel (descriptivo):"); print(P.groupby("test").agg(modelos=("gain", "size"), media=("gain", "mean"), mediana=("gain", "median"),
      pct_mejoran=("gain", lambda g: 100*(g > 0).mean()), cw5=("cw5", "sum"), pct_signo_ok=("signo_ok", lambda s: 100*s.mean())).round(2).to_string())
print("\nVW:"); print(P[P.modelo.str.startswith("vw_")].pivot(index="modelo", columns="test", values="gain").round(2).to_string())
