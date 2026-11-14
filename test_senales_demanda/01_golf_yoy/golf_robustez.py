"""
Robustez de la Parte B: Clark-West con HAC de h+2 lags, BH sobre todos los contrastes,
signo del coeficiente de GT y test de surrogates (fase de Fourier sobre log GT) para
los candidatos que sobreviven a BH.
"""
import sys
import numpy as np
import pandas as pd
from scipy import stats

sys.argv = sys.argv  # rutas por argumento
MAT, GTX, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
RNG = np.random.default_rng(7)

mat = pd.read_csv(MAT, parse_dates=["month"])
idx = pd.date_range("2018-01-01", "2025-12-01", freq="MS")
y = mat[mat.model_raw == "Volkswagen Golf"].groupby("month").units.sum().reindex(idx).astype(float)
xl = pd.ExcelFile(GTX)
gts = {}
for sh, name in [("tidy_cat47_autos", "cat47"), ("tidy_cat473_compra", "cat473")]:
    g = pd.read_excel(xl, sh)
    gts[name] = g[g.modelo == "vw_golf"].set_index("month")["gt"].reindex(idx).astype(float)

def yoy(s, k):
    m = s.rolling(k).mean()
    return np.log(m) - np.log(m.shift(12))

covid = pd.date_range("2020-03-01", "2021-06-01", freq="MS")
def contaminado(t, k):
    a = pd.date_range(t - pd.DateOffset(months=k - 1), t, freq="MS")
    return a.isin(covid).any() or (a - pd.DateOffset(months=12)).isin(covid).any()
CONT6 = pd.Series([contaminado(t, 6) for t in idx], index=idx)

def feats(g):
    G1, G3, G6 = yoy(g, 1), yoy(g, 3), yoy(g, 6)
    return {"GT_YoY1": G1, "GT_YoY3": G3, "GT_YoY6": G6,
            "GT_acel(1-6)": G1 - G6, "GT_dYoY3": G3 - G3.shift(1)}

def cw_hac(eS, eB, pS, pB, L):
    f = eS ** 2 - (eB ** 2 - (pS - pB) ** 2)
    n = len(f); fc = f - f.mean()
    v = fc @ fc / n
    for j in range(1, L + 1):
        v += 2 * (1 - j / (L + 1)) * (fc[j:] @ fc[:-j] / n)
    v = max(v, 1e-12) / n
    cw = f.mean() / np.sqrt(v)
    return cw, 1 - stats.norm.cdf(cw)

def evaluate(target, own, feat, excl, h, start=30):
    df = pd.DataFrame({"tgt": target, "own": own, "f": feat}).dropna()
    if excl:
        df = df[~CONT6.reindex(df.index).astype(bool).values]
    eS, eB, pS, pB, coefs = [], [], [], [], []
    for i in range(start, len(df)):
        tr, te = df.iloc[:i], df.iloc[i]
        Xs = np.c_[np.ones(i), tr.own]; Xb = np.c_[Xs, tr.f]
        bs = np.linalg.lstsq(Xs, tr.tgt, rcond=None)[0]
        bb = np.linalg.lstsq(Xb, tr.tgt, rcond=None)[0]
        ps, pb = bs @ [1, te.own], bb @ [1, te.own, te.f]
        eS.append(te.tgt - ps); eB.append(te.tgt - pb); pS.append(ps); pB.append(pb)
        coefs.append(bb[2])
    eS, eB, pS, pB = map(np.array, (eS, eB, pS, pB))
    if len(eS) < 10:
        return None
    cw, p = cw_hac(eS, eB, pS, pB, L=h + 2)
    return {"n_eval": len(eS), "mejora_MAE_%": 100 * (1 - np.abs(eB).mean() / np.abs(eS).mean()),
            "CW_hac": cw, "p_CW_hac": p, "coef_GT_final": coefs[-1],
            "coef_GT_signo_estable_%": 100 * np.mean(np.sign(coefs) == np.sign(coefs[-1]))}

def phase_sur(x):
    f = np.fft.rfft(x - x.mean())
    ph = RNG.uniform(0, 2 * np.pi, len(f)); ph[0] = 0
    if len(x) % 2 == 0: ph[-1] = 0
    return np.fft.irfft(np.abs(f) * np.exp(1j * ph), n=len(x)) + x.mean()

targets = {"YoY1 mes siguiente (h=1)": (1, 1), "YoY3 trimestre siguiente (h=3)": (3, 3)}
rows = []
for cat, g in gts.items():
    F = feats(g)
    for excl in (False, True):
        for tname, (k, h) in targets.items():
            tgt, own = yoy(y, k).shift(-h), yoy(y, k)
            for fname, f in F.items():
                r = evaluate(tgt, own, f, excl, h)
                if r:
                    r.update(cat=cat, muestra="sin_COVID" if excl else "todo", objetivo=tname, senal_GT=fname)
                    rows.append(r)
res = pd.DataFrame(rows)

# Benjamini-Hochberg (q = 0.10) sobre los 40 contrastes
p = res.p_CW_hac.values; m = len(p); order = np.argsort(p)
thr = 0.10 * np.arange(1, m + 1) / m
passed = np.zeros(m, bool)
k_max = np.where(p[order] <= thr)[0]
if len(k_max):
    passed[order[: k_max.max() + 1]] = True
res["sobrevive_BH10"] = passed

# surrogates para los que sobreviven a BH
sur_rows = []
for i in np.where(passed)[0]:
    r = res.iloc[i]
    g = gts[r["cat"]]; excl = r.muestra == "sin_COVID"
    k, h = targets[r.objetivo]
    tgt, own = yoy(y, k).shift(-h), yoy(y, k)
    obs = r["mejora_MAE_%"]
    lg = np.log(g.values); ge = 0; N = 500
    for _ in range(N):
        gs = pd.Series(np.exp(phase_sur(lg)), index=idx)
        rr = evaluate(tgt, own, feats(gs)[r.senal_GT], excl, h)
        if rr and rr["mejora_MAE_%"] >= obs:
            ge += 1
    sur_rows.append({"cat": r["cat"], "muestra": r.muestra, "objetivo": r.objetivo,
                     "senal_GT": r.senal_GT, "mejora_MAE_%": obs,
                     "p_surrogate": (ge + 1) / (N + 1)})
sur = pd.DataFrame(sur_rows)

res.to_csv(f"{OUT}/golf_prediccion_robusta.csv", index=False)
sur.to_csv(f"{OUT}/golf_prediccion_surrogates.csv", index=False)
pd.set_option("display.width", 220)
cols = ["cat", "muestra", "objetivo", "senal_GT", "n_eval", "mejora_MAE_%", "CW_hac", "p_CW_hac",
        "sobrevive_BH10", "coef_GT_final", "coef_GT_signo_estable_%"]
print(res[cols].sort_values("p_CW_hac").round(3).to_string(index=False))
print("\nSurrogates (500 por candidato):")
print(sur.round(3).to_string(index=False))
