"""
Logs -> estacionariedad (KPSS / fuerza estacional STL) -> prewhitening AR (Box-Jenkins)
-> correlacion cruzada lineal y no lineal, lags -3..+12 (positivo = GT adelanta).

Por par modelo x categoria x muestra:
  - d (regular) y D (estacional) comunes al par: maximo de lo que pide cada serie.
  - AR(p) por AIC (p<=13) sobre GT transformado; mismo filtro aplicado a matriculaciones.
  - Pearson, Spearman, Kendall, distance correlation por lag.
  - p de permutacion (300) del MAX |medida| sobre los 16 lags (Pearson, Spearman, dCor).
Panel: CCF media entre modelos por lag con p de permutacion conjunta; BH entre modelos.
Muestra sin_COVID: se excluyen pares (t, t-l) cuyo filtrado por diferencias toca mar-2020..jun-2021.
"""
import sys
import warnings
import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import kpss
from statsmodels.tsa.seasonal import STL
from statsmodels.tsa.ar_model import ar_select_order

warnings.filterwarnings("ignore")
MAT, GTX, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
NPERM = int(sys.argv[4]) if len(sys.argv) > 4 else 300
RNG = np.random.default_rng(3)
LAGS = list(range(-3, 13))

NAME_MAP = {
    "dacia_sandero": "Dacia Sandero", "hyundai_tucson": "Hyundai Tucson", "kia_sportage": "Kia Sportage",
    "toyota_chr": "Toyota C-Hr", "toyota_corolla": "Toyota Corolla", "dacia_duster": "Dacia Duster",
    "seat_ibiza": "Seat Ibiza", "toyota_yaris": "Toyota Yaris", "seat_arona": "Seat Arona",
    "hyundai_kona": "Hyundai Kona", "renault_clio": "Renault Clio", "renault_captur": "Renault Captur",
    "seat_leon": "Seat Leon", "nissan_qashqai": "Nissan Qashqai", "peugeot_2008": "Peugeot 2008",
    "kia_stonic": "Kia Stonic", "vw_troc": "Volkswagen T-Roc", "seat_ateca": "Seat Ateca", "mg_zs": "Mg Zs",
    "toyota_yaris_cross": "Toyota Yaris Cross", "peugeot_3008": "Peugeot 3008", "toyota_rav4": "Toyota Rav 4",
    "peugeot_208": "Peugeot 208", "hyundai_i20": "Hyundai I20", "kia_niro": "Kia Niro",
    "citroen_c3": "Citroën C3", "kia_ceed": "Kia Ceed", "vw_tiguan": "Volkswagen Tiguan",
    "ford_focus": "Ford Focus", "vw_golf": "Volkswagen Golf", "ford_kuga": "Ford Kuga",
    "vw_tcross": "Volkswagen T-Cross", "opel_corsa": "Opel Corsa", "kia_xceed": "Kia Xceed",
    "nissan_juke": "Nissan Juke", "hyundai_i30": "Hyundai I30", "citroen_c3_aircross": "Citroën C3 Aircross",
    "mazda_cx5": "Mazda Cx5", "vw_polo": "Volkswagen Polo", "ford_puma": "Ford Puma",
    "renault_megane": "Renault Megane", "tesla_model3": "Tesla Model 3", "peugeot_308": "Peugeot 308",
    "bmw_x1": "Bmw X1", "skoda_karoq": "Skoda Karoq", "mazda_cx30": "Mazda Cx30",
    "renault_arkana": "Renault Arkana", "citroen_c4": "Citroën C4", "skoda_fabia": "Skoda Fabia",
    "volvo_xc40": "Volvo Xc40",
}

idx = pd.date_range("2018-01-01", "2025-12-01", freq="MS")
mat = pd.read_csv(MAT, parse_dates=["month"])
xl = pd.ExcelFile(GTX)
GT = {}
for sh, cat in [("tidy_cat47_autos", "cat47"), ("tidy_cat473_compra", "cat473")]:
    g = pd.read_excel(xl, sh)
    GT[cat] = {k: g[g.modelo == k].set_index("month")["gt"].reindex(idx).astype(float) for k in NAME_MAP}
Y = {k: mat[mat.model_raw == v].groupby("month").units.sum().reindex(idx).fillna(0).astype(float)
     for k, v in NAME_MAP.items()}
covid = pd.date_range("2020-03-01", "2021-06-01", freq="MS")


def needs_d(x):
    try:
        p = kpss(x, regression="c", nlags="auto")[1]
    except Exception:
        return 0
    return int(p < 0.05)


def needs_D(x):
    try:
        r = STL(pd.Series(x), period=12, robust=True).fit()
        fs = max(0.0, 1 - np.var(r.resid) / np.var(r.resid + r.seasonal))
        return int(fs > 0.64)
    except Exception:
        return 0


def transform(s, d, D):
    out = s.copy()
    if D:
        out = out - out.shift(12)
    for _ in range(d):
        out = out - out.shift(1)
    return out


def dcor(x, y):
    a = np.abs(x[:, None] - x[None, :]); b = np.abs(y[:, None] - y[None, :])
    A = a - a.mean(0) - a.mean(1)[:, None] + a.mean()
    B = b - b.mean(0) - b.mean(1)[:, None] + b.mean()
    v = (A * A).mean() * (B * B).mean()
    return np.sqrt(max((A * B).mean(), 0) / np.sqrt(v)) if v > 0 else np.nan


def pairs(alpha, beta, l, keep):
    # corr(alpha_{t-l}, beta_t)
    n = len(alpha)
    if l >= 0:
        a, b, k1, k2 = alpha[: n - l], beta[l:], keep[: n - l], keep[l:]
    else:
        a, b, k1, k2 = alpha[-l:], beta[: n + l], keep[-l:], keep[: n + l]
    m = k1 & k2 & ~np.isnan(a) & ~np.isnan(b)
    return a[m], b[m]


def measures(alpha, beta, keep, which=("pearson", "spearman", "kendall", "dcor")):
    out = {w: [] for w in which}
    for l in LAGS:
        a, b = pairs(alpha, beta, l, keep)
        if len(a) < 20:
            for w in which: out[w].append(np.nan)
            continue
        if "pearson" in which: out["pearson"].append(np.corrcoef(a, b)[0, 1])
        if "spearman" in which: out["spearman"].append(stats.spearmanr(a, b)[0])
        if "kendall" in which: out["kendall"].append(stats.kendalltau(a, b)[0])
        if "dcor" in which: out["dcor"].append(dcor(a, b))
    return {w: np.array(v) for w, v in out.items()}


rows, ccf_rows, perm_store = [], [], {}
for cat in ("cat47", "cat473"):
    for k in NAME_MAP:
        y, g = Y[k], GT[cat][k]
        first = idx[((y > 0) & (g > 0)).values].min() if ((y > 0) & (g > 0)).any() else None
        if first is None:
            continue
        ys, gs = y[y.index >= first], g[g.index >= first]
        if len(ys) < 60 or (y[y.index >= first] == 0).sum() > 0 or (gs == 0).sum() >= 12:
            continue
        ly, lg = np.log(ys), np.log1p(gs)
        d = max(needs_d(ly.values), needs_d(lg.values))
        D = max(needs_D(ly.values), needs_D(lg.values))
        ty, tg = transform(ly, d, D), transform(lg, d, D)
        ok = ~(ty.isna() | tg.isna())
        ty, tg = ty[ok], tg[ok]
        try:
            sel = ar_select_order(tg.values, maxlag=13, ic="aic", trend="c")
            lags_ar = sel.ar_lags or []
        except Exception:
            lags_ar = []
        p = max(lags_ar) if lags_ar else 0
        # coeficientes AR por OLS sobre GT transformado
        X = np.column_stack([np.ones(len(tg) - p)] + [tg.values[p - j: len(tg) - j] for j in lags_ar]) if p else np.ones((len(tg), 1))
        yy = tg.values[p:]
        beta_ar = np.linalg.lstsq(X, yy, rcond=None)[0]
        def filt(x):
            Xf = np.column_stack([np.ones(len(x) - p)] + [x[p - j: len(x) - j] for j in lags_ar]) if p else np.ones((len(x), 1))
            return x[p:] - Xf @ beta_ar if p else x - x.mean()
        alpha = filt(tg.values)
        beta = filt(ty.values) if p else ty.values - ty.values.mean()
        tt = ty.index[p:]
        span = d + 12 * D
        cont = np.array([any((t - pd.DateOffset(months=j)) in covid for j in range(span + 1)) for t in tt])
        lb = stats.acorr_ljungbox if hasattr(stats, "acorr_ljungbox") else None
        for excl in (False, True):
            keep = ~cont if excl else np.ones(len(tt), bool)
            obs = measures(alpha, beta, keep)
            if np.all(np.isnan(obs["pearson"])):
                continue
            # permutacion del alpha (casi ruido blanco) -> nula del max |medida| sobre lags
            perm_max = {w: [] for w in ("pearson", "spearman", "dcor")}
            perm_ccf = []
            for _ in range(NPERM):
                ap = alpha.copy(); ap[keep] = RNG.permutation(ap[keep])
                m = measures(ap, beta, keep, which=("pearson", "spearman", "dcor"))
                for w in perm_max:
                    perm_max[w].append(np.nanmax(np.abs(m[w])))
                perm_ccf.append(m["pearson"])
            tag = "sin_COVID" if excl else "todo"
            perm_store[(cat, tag, k)] = np.array(perm_ccf)
            row = {"cat": cat, "muestra": tag, "modelo": k, "desde": first.date(), "d": d, "D": D,
                   "AR_p": p, "n": int(keep.sum())}
            for w in ("pearson", "spearman", "kendall", "dcor"):
                v = obs[w]; i = int(np.nanargmax(np.abs(v)))
                row[f"{w}_mejor_lag"] = LAGS[i]; row[f"{w}_r"] = v[i]
                if w in perm_max:
                    row[f"{w}_p"] = (np.sum(np.array(perm_max[w]) >= abs(v[i])) + 1) / (NPERM + 1)
            row["pearson_lag0"] = obs["pearson"][LAGS.index(0)]
            row["pearson_lag1"] = obs["pearson"][LAGS.index(1)]
            rows.append(row)
            for li, l in enumerate(LAGS):
                ccf_rows.append({"cat": cat, "muestra": tag, "modelo": k, "lag": l,
                                 "pearson": obs["pearson"][li], "spearman": obs["spearman"][li],
                                 "dcor": obs["dcor"][li]})

R = pd.DataFrame(rows)
for (cat, tag), grp in R.groupby(["cat", "muestra"]):
    for w in ("pearson", "spearman", "dcor"):
        p = grp[f"{w}_p"].values; m = len(p); o = np.argsort(p)
        thr = 0.10 * np.arange(1, m + 1) / m
        passed = np.zeros(m, bool); ww = np.where(p[o] <= thr)[0]
        if len(ww): passed[o[: ww.max() + 1]] = True
        R.loc[grp.index, f"{w}_BH10"] = passed
R.to_csv(f"{OUT}/prewhite_por_modelo.csv", index=False)
CC = pd.DataFrame(ccf_rows); CC.to_csv(f"{OUT}/prewhite_ccf.csv", index=False)

# CCF media del panel y p de permutacion conjunta por lag
panel = []
for (cat, tag), grp in CC.groupby(["cat", "muestra"]):
    mods = grp.modelo.unique()
    obs_mean = grp.groupby("lag").pearson.mean().reindex(LAGS).values
    perm = np.nanmean(np.stack([perm_store[(cat, tag, m)] for m in mods]), axis=0)  # NPERM x lags
    for li, l in enumerate(LAGS):
        panel.append({"cat": cat, "muestra": tag, "lag": l, "modelos": len(mods),
                      "r_medio": obs_mean[li],
                      "p_dos_colas": (np.sum(np.abs(perm[:, li]) >= abs(obs_mean[li])) + 1) / (NPERM + 1),
                      "spearman_medio": grp[grp.lag == l].spearman.mean(),
                      "dcor_medio": grp[grp.lag == l].dcor.mean()})
P = pd.DataFrame(panel); P.to_csv(f"{OUT}/prewhite_panel_ccf.csv", index=False)

pd.set_option("display.width", 240)
print("=== Resumen del panel por categoria y muestra ===")
for (cat, tag), grp in R.groupby(["cat", "muestra"]):
    n = len(grp)
    print(f"\n{cat} | {tag} | modelos: {n} | d=1 en {int((grp.d == 1).sum())}, D=1 en {int((grp.D == 1).sum())} | AR p mediano {grp.AR_p.median():.0f}")
    for w in ("pearson", "spearman", "dcor"):
        s5 = int((grp[f"{w}_p"] < 0.05).sum())
        print(f"  {w:<8}: sig5% {s5}/{n} (binom p={stats.binomtest(s5, n, 0.05, alternative='greater').pvalue:.4f}), "
              f"BH10 {int(grp[f'{w}_BH10'].sum())}, |r| mejor mediano {grp[f'{w}_r'].abs().median():.3f}, "
              f"mejor lag: {grp[f'{w}_mejor_lag'].value_counts().sort_index().to_dict()}")
print("\n=== CCF media del panel (Pearson prewhitened), lags con p<0.10 ===")
print(P[P.p_dos_colas < 0.10].round(3).to_string(index=False))
print("\n=== CCF media completa, cat47/cat473 todo y sin_COVID ===")
print(P.pivot_table(index="lag", columns=["cat", "muestra"], values="r_medio").round(3).to_string())
print("\n=== Modelos VW ===")
vw = R[R.modelo.str.startswith("vw_")]
print(vw[["cat", "muestra", "modelo", "d", "D", "AR_p", "n", "pearson_mejor_lag", "pearson_r", "pearson_p",
          "spearman_mejor_lag", "spearman_r", "spearman_p", "dcor_mejor_lag", "dcor_r", "dcor_p",
          "pearson_lag0", "pearson_lag1"]].round(3).to_string(index=False))
