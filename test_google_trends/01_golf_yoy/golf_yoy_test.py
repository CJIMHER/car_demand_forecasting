"""
Test VW Golf: YoY en log de matriculaciones y Google Trends, ventanas 1, 3 y 6 meses.

Definicion (ventana k):
    YoY_k(t) = log( media x[t-k+1 .. t] ) - log( media x[t-12-k+1 .. t-12] )

Parte A - Correlacion (asociacion):
    corr( G_k(t - l), Y_k(t) ), l = 0..6, Pearson y Spearman.
    p-valor por surrogates: se aleatoriza la fase de Fourier de log(GT) en bruto
    (conserva su autocorrelacion y estacionalidad), se recalculan los YoY y se toma
    el MAXIMO |r| sobre los 7 lags, para que elegir el mejor lag no infle el resultado.

Parte B - Prediccion (valor predictivo fuera de muestra):
    Objetivos: Y_1(t+1) (proximo mes) y Y_3(t+3) (proximo trimestre).
    Referencia: regresion sobre el propio YoY de matriculaciones conocido en t.
    Aumentado: referencia + una senal de GT conocida en t.
    Origen movil con ventana expansiva; MAE, acierto de direccion y Clark-West.

Todo se repite excluyendo los meses con YoY contaminado por el COVID.
"""
import sys
import numpy as np
import pandas as pd
from scipy import stats

RNG = np.random.default_rng(42)
MAT = sys.argv[1]
GTX = sys.argv[2]
OUT = sys.argv[3]

# ---------------- datos ----------------
mat = pd.read_csv(MAT, parse_dates=["month"])
y = (mat[mat.model_raw == "Volkswagen Golf"].groupby("month").units.sum())
idx = pd.date_range("2018-01-01", "2025-12-01", freq="MS")
y = y.reindex(idx).astype(float)

xl = pd.ExcelFile(GTX)
gts = {}
for sh, name in [("tidy_cat47_autos", "cat47"), ("tidy_cat473_compra", "cat473")]:
    g = pd.read_excel(xl, sh)
    g = g[g.modelo == "vw_golf"].set_index("month")["gt"].reindex(idx).astype(float)
    gts[name] = g

print("Mes minimo de matriculaciones:", y.idxmin().date(), int(y.min()))


def yoy(s, k):
    m = s.rolling(k).mean()
    return np.log(m) - np.log(m.shift(12))


# meses cuyo YoY (cualquier ventana hasta 6) toca mar-2020..jun-2021
covid = pd.date_range("2020-03-01", "2021-06-01", freq="MS")
def contaminado(t, k):
    actual = pd.date_range(t - pd.DateOffset(months=k - 1), t, freq="MS")
    previo = actual - pd.DateOffset(months=12)
    return actual.isin(covid).any() or previo.isin(covid).any()


# mascara precalculada: CONT[k][t] = True si el YoY de ventana k en t toca el COVID
CONT = {k: pd.Series([contaminado(t, k) for t in idx], index=idx) for k in (1, 3, 6)}


def keep_mask(index, k, l):
    c = CONT[k]
    a = c.reindex(index).fillna(False).astype(bool).values
    b = c.shift(l).reindex(index).fillna(False).astype(bool).values
    return ~(a | b)


# ---------------- Parte A: correlacion ----------------
def corr_table(gser, excl):
    rows = []
    for k in (1, 3, 6):
        Y = yoy(y, k)
        G = yoy(gser, k)
        for l in range(0, 7):
            d = pd.DataFrame({"Y": Y, "G": G.shift(l)}).dropna()
            if excl:
                d = d[keep_mask(d.index, k, l)]
            rp = d.Y.corr(d.G)
            rs = d.Y.corr(d.G, method="spearman")
            rows.append({"k": k, "lag": l, "n": len(d), "pearson": rp, "spearman": rs})
    return pd.DataFrame(rows)


def phase_surrogate(x):
    x = np.asarray(x, float)
    f = np.fft.rfft(x - x.mean())
    ph = RNG.uniform(0, 2 * np.pi, len(f))
    ph[0] = 0
    if len(x) % 2 == 0:
        ph[-1] = 0
    s = np.fft.irfft(np.abs(f) * np.exp(1j * ph), n=len(x)) + x.mean()
    return s


def surrogate_p(gser, k, excl, metric, n_sur=1000):
    """p-valor del max |r| sobre lags 0..6 frente a surrogates de fase de log(GT)."""
    def max_abs(gs):
        t = corr_table_single(gs, k, excl, metric)
        return np.nanmax(np.abs(t))
    obs = max_abs(gser)
    lg = np.log(gser.values)
    cnt = 0
    for _ in range(n_sur):
        sur = pd.Series(np.exp(phase_surrogate(lg)), index=gser.index)
        if max_abs(sur) >= obs:
            cnt += 1
    return obs, (cnt + 1) / (n_sur + 1)


def corr_table_single(gser, k, excl, metric):
    Y = yoy(y, k)
    G = yoy(gser, k)
    out = []
    for l in range(0, 7):
        d = pd.DataFrame({"Y": Y, "G": G.shift(l)}).dropna()
        if excl:
            d = d[keep_mask(d.index, k, l)]
        out.append(d.Y.corr(d.G, method=metric))
    return np.array(out)


# ---------------- Parte B: prediccion ----------------
def features(gser):
    G1, G3, G6 = yoy(gser, 1), yoy(gser, 3), yoy(gser, 6)
    return {
        "GT_YoY1": G1,
        "GT_YoY3": G3,
        "GT_YoY6": G6,
        "GT_acel(1-6)": G1 - G6,
        "GT_dYoY3": G3 - G3.shift(1),
    }


def clark_west(e_small, e_big, p_small, p_big):
    f = e_small ** 2 - (e_big ** 2 - (p_small - p_big) ** 2)
    n = len(f)
    fc = f - f.mean()
    g0 = fc @ fc / n
    g1 = fc[1:] @ fc[:-1] / n
    var = max((g0 + 2 * g1) / n, g0 / n)
    cw = f.mean() / np.sqrt(var)
    return cw, 1 - stats.norm.cdf(cw)


def rolling_eval(target, own, feat, excl_k, excl, start=30):
    df = pd.DataFrame({"tgt": target, "own": own, "f": feat}).dropna()
    if excl:
        df = df[[not contaminado(t, excl_k) for t in df.index]]
    eS, eB, pS, pB, yr, own_last = [], [], [], [], [], []
    for i in range(start, len(df)):
        tr, te = df.iloc[:i], df.iloc[i]
        Xs = np.c_[np.ones(len(tr)), tr.own]
        Xb = np.c_[Xs, tr.f]
        bs = np.linalg.lstsq(Xs, tr.tgt, rcond=None)[0]
        bb = np.linalg.lstsq(Xb, tr.tgt, rcond=None)[0]
        ps = bs @ [1, te.own]
        pb = bb @ [1, te.own, te.f]
        eS.append(te.tgt - ps); eB.append(te.tgt - pb)
        pS.append(ps); pB.append(pb); yr.append(te.tgt)
    eS, eB, pS, pB, yr = map(np.array, (eS, eB, pS, pB, yr))
    if len(yr) < 10:
        return None
    cw, p = clark_west(eS, eB, pS, pB)
    return {
        "n_eval": len(yr),
        "MAE_ref": np.mean(np.abs(eS)),
        "MAE_con_GT": np.mean(np.abs(eB)),
        "mejora_MAE_%": 100 * (1 - np.mean(np.abs(eB)) / np.mean(np.abs(eS))),
        "dir_ref": np.mean(np.sign(pS) == np.sign(yr)),
        "dir_con_GT": np.mean(np.sign(pB) == np.sign(yr)),
        "CW": cw, "p_CW": p,
    }


# ---------------- ejecucion ----------------
res_corr, res_sur, res_pred = [], [], []
for cat, g in gts.items():
    for excl in (False, True):
        tag = "sin_COVID" if excl else "todo"
        t = corr_table(g, excl); t["cat"] = cat; t["muestra"] = tag
        res_corr.append(t)
        for k in (1, 3, 6):
            for metric in ("pearson", "spearman"):
                obs, p = surrogate_p(g, k, excl, metric, n_sur=500)
                res_sur.append({"cat": cat, "muestra": tag, "k": k, "metrica": metric,
                                "max_abs_r_lags0_6": obs, "p_surrogate": p})
        F = features(g)
        targets = {
            "YoY1 mes siguiente (h=1)": (yoy(y, 1).shift(-1), yoy(y, 1), 1),
            "YoY3 trimestre siguiente (h=3)": (yoy(y, 3).shift(-3), yoy(y, 3), 3),
        }
        for tname, (tgt, own, kk) in targets.items():
            for fname, f in F.items():
                r = rolling_eval(tgt, own, f, max(kk, 6), excl)
                if r:
                    r.update({"cat": cat, "muestra": tag, "objetivo": tname, "senal_GT": fname})
                    res_pred.append(r)

corr = pd.concat(res_corr)
sur = pd.DataFrame(res_sur)
pred = pd.DataFrame(res_pred)
corr.to_csv(f"{OUT}/golf_correlaciones.csv", index=False)
sur.to_csv(f"{OUT}/golf_surrogates.csv", index=False)
pred.to_csv(f"{OUT}/golf_prediccion.csv", index=False)

pd.set_option("display.width", 200)
print("\n=== A. Correlacion por ventana y lag (Spearman), cat473 / cat47 ===")
for cat in ("cat473", "cat47"):
    for tag in ("todo", "sin_COVID"):
        c = corr[(corr.cat == cat) & (corr.muestra == tag)]
        piv = c.pivot(index="k", columns="lag", values="spearman").round(2)
        n = c.groupby("k").n.min().to_dict()
        print(f"\n{cat} | {tag} | n min por k: {n}")
        print(piv.to_string())
        pp = c.pivot(index="k", columns="lag", values="pearson").round(2)
        print("Pearson:"); print(pp.to_string())

print("\n=== A2. Significacion por surrogates (max |r| sobre lags 0-6) ===")
print(sur.round(3).to_string(index=False))

print("\n=== B. Prediccion fuera de muestra ===")
cols = ["cat", "muestra", "objetivo", "senal_GT", "n_eval", "MAE_ref", "MAE_con_GT",
        "mejora_MAE_%", "dir_ref", "dir_con_GT", "CW", "p_CW"]
print(pred[cols].round(3).to_string(index=False))
