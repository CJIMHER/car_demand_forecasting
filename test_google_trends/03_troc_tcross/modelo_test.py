"""
Mismo test que el Golf, aplicado a T-Roc y T-Cross (solo categoria 47 es utilizable).
A: correlacion YoY(log) por ventana 1/3/6 y lag 0..6, con p de surrogates sobre el max |r|.
B: prediccion fuera de muestra (origen movil), Clark-West con HAC h+2, signo del coeficiente
   y p de surrogates para cada contraste.
"""
import sys
import numpy as np
import pandas as pd
from scipy import stats

MAT, GTX, RAW, OUT = sys.argv[1:5]
N_SUR = int(sys.argv[5]) if len(sys.argv) > 5 else 300
RNG = np.random.default_rng(11)

mat = pd.read_csv(MAT, parse_dates=["month"])
def matric(name, end):
    idx = pd.date_range("2018-01-01", end, freq="MS")
    return mat[mat.model_raw == name].groupby("month").units.sum().reindex(idx).astype(float)

top = pd.read_excel(GTX, "tidy_cat47_autos")
def gt_top(key):
    idx = pd.date_range("2018-01-01", "2025-12-01", freq="MS")
    return top[top.modelo == key].set_index("month")["gt"].reindex(idx).astype(float)

raw = pd.read_csv(RAW, parse_dates=["date"])
def gt_raw(key):
    idx = pd.date_range("2018-01-01", "2026-04-01", freq="MS")
    r = raw[(raw.model_id == key) & (raw.geo == "ES") & (raw.category == 47)]
    return r.set_index("date")["value"].reindex(idx).astype(float)

CASOS = {
    "T-Roc | cat47 extraccion 2018-2026": (matric("Volkswagen T-Roc", "2026-04-01"), gt_raw("vw_troc")),
    "T-Roc | cat47 top-50 2018-2025": (matric("Volkswagen T-Roc", "2025-12-01"), gt_top("vw_troc")),
    "T-Cross | cat47 top-50 2018-2025": (matric("Volkswagen T-Cross", "2025-12-01"), gt_top("vw_tcross")),
}

covid = pd.date_range("2020-03-01", "2021-06-01", freq="MS")
def cont_series(index, k):
    out = []
    for t in index:
        a = pd.date_range(t - pd.DateOffset(months=k - 1), t, freq="MS")
        out.append(a.isin(covid).any() or (a - pd.DateOffset(months=12)).isin(covid).any())
    return pd.Series(out, index=index)

def yoy(s, k):
    m = s.rolling(k).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.log(m) - np.log(m.shift(12))
    return r.replace([np.inf, -np.inf], np.nan)

def phase_sur(x):
    f = np.fft.rfft(x - x.mean())
    ph = RNG.uniform(0, 2 * np.pi, len(f)); ph[0] = 0
    if len(x) % 2 == 0: ph[-1] = 0
    return np.fft.irfft(np.abs(f) * np.exp(1j * ph), n=len(x)) + x.mean()

def corr_vec(y, g, k, excl, C, method):
    Y, G = yoy(y, k), yoy(g, k)
    out, ns = [], []
    for l in range(7):
        d = pd.DataFrame({"Y": Y, "G": G.shift(l)}).dropna()
        if excl:
            c = C[k]
            keep = ~(c.reindex(d.index).fillna(False).astype(bool).values |
                     c.shift(l).reindex(d.index).fillna(False).astype(bool).values)
            d = d[keep]
        out.append(d.Y.corr(d.G, method=method) if len(d) > 8 else np.nan); ns.append(len(d))
    return np.array(out), min(ns)

def evaluate(y, g, sk, tk, h, excl, C, start=24):
    tgt, own, f = yoy(y, tk).shift(-h), yoy(y, tk), yoy(g, sk)
    df = pd.DataFrame({"tgt": tgt, "own": own, "f": f}).dropna()
    if excl:
        df = df[~C[6].reindex(df.index).fillna(False).astype(bool).values]
    n = len(df)
    if n - start < 8:
        return None
    T, O, F = df.tgt.values, df.own.values, df.f.values
    eS, eB, pS, pB, cf = [], [], [], [], []
    for i in range(start, n):
        Xs = np.c_[np.ones(i), O[:i]]; Xb = np.c_[Xs, F[:i]]
        bs = np.linalg.lstsq(Xs, T[:i], rcond=None)[0]; bb = np.linalg.lstsq(Xb, T[:i], rcond=None)[0]
        ps = bs[0] + bs[1] * O[i]; pb = bb[0] + bb[1] * O[i] + bb[2] * F[i]
        eS.append(T[i] - ps); eB.append(T[i] - pb); pS.append(ps); pB.append(pb); cf.append(bb[2])
    eS, eB, pS, pB, cf = map(np.array, (eS, eB, pS, pB, cf))
    fv = eS ** 2 - (eB ** 2 - (pS - pB) ** 2); m = len(fv); fc = fv - fv.mean()
    v = fc @ fc / m
    for j in range(1, h + 3):
        v += 2 * (1 - j / (h + 3)) * (fc[j:] @ fc[:-j] / m)
    cw = fv.mean() / np.sqrt(max(v, 1e-12) / m)
    return {"n_eval": m, "mejora_MAE_%": 100 * (1 - np.abs(eB).mean() / np.abs(eS).mean()),
            "CW": cw, "p_CW": 1 - stats.norm.cdf(cw), "coef_GT": cf[-1],
            "signo_estable_%": 100 * np.mean(np.sign(cf) == np.sign(cf[-1]))}

pd.set_option("display.width", 230)
all_corr, all_pred = [], []
for caso, (y, g) in CASOS.items():
    # recorta a la ventana donde ambas series existen (> 0)
    valid = (y > 0) & (g > 0)
    first = valid[valid].index.min()
    y = y[y.index >= first]; g = g[g.index >= first]
    C = {k: cont_series(y.index, k) for k in (1, 3, 6)}
    lg = np.log(g.clip(lower=1).values)
    print(f"\n######## {caso} | desde {first.date()} | {len(y)} meses")
    for excl in (False, True):
        tag = "sin_COVID" if excl else "todo"
        # --- A: correlacion ---
        for k in (1, 3, 6):
            for meth in ("spearman", "pearson"):
                r, n = corr_vec(y, g, k, excl, C, meth)
                obs = np.nanmax(np.abs(r)); best = int(np.nanargmax(np.abs(r)))
                ge = 0
                for _ in range(N_SUR):
                    gs = pd.Series(np.exp(phase_sur(lg)), index=g.index)
                    rs, _ = corr_vec(y, gs, k, excl, C, meth)
                    if np.nanmax(np.abs(rs)) >= obs: ge += 1
                all_corr.append({"caso": caso, "muestra": tag, "k": k, "metrica": meth, "n_min": n,
                                 **{f"lag{l}": r[l] for l in range(7)},
                                 "mejor_lag": best, "r_mejor": r[best], "p_sur": (ge + 1) / (N_SUR + 1)})
        # --- B: prediccion ---
        for tn, (tk, h) in {"YoY1 mes sig. (h=1)": (1, 1), "YoY3 trim. sig. (h=3)": (3, 3)}.items():
            for sk in (1, 3, 6):
                res = evaluate(y, g, sk, tk, h, excl, C)
                if not res:
                    all_pred.append({"caso": caso, "muestra": tag, "objetivo": tn, "senal": f"GT_YoY{sk}",
                                     "n_eval": 0}); continue
                ge = 0
                for _ in range(N_SUR):
                    gs = pd.Series(np.exp(phase_sur(lg)), index=g.index)
                    rr = evaluate(y, gs, sk, tk, h, excl, C)
                    if rr and rr["mejora_MAE_%"] >= res["mejora_MAE_%"]: ge += 1
                res.update(caso=caso, muestra=tag, objetivo=tn, senal=f"GT_YoY{sk}", p_sur=(ge + 1) / (N_SUR + 1))
                all_pred.append(res)

Cc = pd.DataFrame(all_corr); Pp = pd.DataFrame(all_pred)
Cc.to_csv(f"{OUT}/troc_tcross_correlaciones.csv", index=False)
Pp.to_csv(f"{OUT}/troc_tcross_prediccion.csv", index=False)
print("\n=== A. Correlacion (mejor lag 0-6, p de surrogates sobre el max |r|) ===")
print(Cc[["caso", "muestra", "k", "metrica", "n_min", "lag0", "lag1", "lag3", "lag6",
          "mejor_lag", "r_mejor", "p_sur"]].round(3).to_string(index=False))
print("\n=== B. Prediccion ===")
print(Pp[["caso", "muestra", "objetivo", "senal", "n_eval", "mejora_MAE_%", "p_CW", "p_sur",
          "coef_GT", "signo_estable_%"]].round(3).to_string(index=False))
