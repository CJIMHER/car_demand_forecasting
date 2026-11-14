"""Confirmacion prerregistrada del lag 4 (ver prerregistro_lag4.md)."""
import sys, warnings
import numpy as np
import pandas as pd
from scipy import stats
from sklearn.linear_model import HuberRegressor
from statsmodels.tsa.ar_model import ar_select_order

warnings.filterwarnings("ignore")
MAT, GTX, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
NSUR = int(sys.argv[4]) if len(sys.argv) > 4 else 200
RNG = np.random.default_rng(404)

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
EVAL = (pd.Timestamp("2023-01-01"), pd.Timestamp("2025-12-01"))


def eligible(cat):
    out = {}
    for k in NAME_MAP:
        y, g = Y[k], GT[cat][k]
        both = (y > 0) & (g > 0)
        if not both.any():
            continue
        first = idx[both.values].min()
        ys, gs = y[y.index >= first], g[g.index >= first]
        if len(ys) >= 60 and (ys == 0).sum() == 0 and (gs == 0).sum() < 12:
            out[k] = (np.log(ys).diff(), np.log1p(gs).diff())
    return out


def design(yd, gd, h, glag, own):
    df = pd.DataFrame({"y": yd})
    for o in own:
        df[f"y{o}"] = yd.shift(o)
    df["g"] = gd.shift(glag)
    return df.dropna()


def run(yd, gd, h, glag, own):
    df = design(yd, gd, h, glag, own)
    ocols = [f"y{o}" for o in own]
    eS, eB, pS, pB, cf = [], [], [], [], []
    for t in df.index[(df.index >= EVAL[0]) & (df.index <= EVAL[1])]:
        origin = t - pd.DateOffset(months=h)
        tr = df[df.index <= origin]
        if len(tr) < 24:
            continue
        te = df.loc[[t]]
        try:
            ms = HuberRegressor(max_iter=500).fit(tr[ocols].values, tr.y.values)
            mb = HuberRegressor(max_iter=500).fit(tr[ocols + ["g"]].values, tr.y.values)
        except Exception:
            continue
        ps = ms.predict(te[ocols].values)[0]; pb = mb.predict(te[ocols + ["g"]].values)[0]
        yt = te.y.values[0]
        eS.append(yt - ps); eB.append(yt - pb); pS.append(ps); pB.append(pb); cf.append(mb.coef_[-1])
    if len(eS) < 20:
        return None
    eS, eB, pS, pB = map(np.array, (eS, eB, pS, pB))
    f = eS ** 2 - (eB ** 2 - (pS - pB) ** 2); n = len(f); fc = f - f.mean(); L = h + 2
    v = fc @ fc / n
    for j in range(1, L + 1):
        v += 2 * (1 - j / (L + 1)) * (fc[j:] @ fc[:-j] / n)
    cw = f.mean() / np.sqrt(max(v, 1e-12) / n)
    return {"n": n, "gain": 100 * (1 - np.abs(eB).mean() / np.abs(eS).mean()),
            "p_CW": 1 - stats.norm.cdf(cw), "coef": cf[-1]}


def phase_sur(x):
    f = np.fft.rfft(x - x.mean())
    ph = RNG.uniform(0, 2 * np.pi, len(f)); ph[0] = 0
    if len(x) % 2 == 0: ph[-1] = 0
    return np.fft.irfft(np.abs(f) * np.exp(1j * ph), n=len(x)) + x.mean()


# ---------- 0. Comprobacion de seleccion: CCF prewhitened en 2018-2022 ----------
def ccf_pre(cat):
    vals = {l: [] for l in range(0, 13)}
    for k, (yd, gd) in eligible(cat).items():
        d = pd.DataFrame({"y": yd, "g": gd}).dropna()
        d = d[d.index <= "2022-12-01"]
        if len(d) < 36:
            continue
        sel = ar_select_order(d.g.values, maxlag=13, ic="aic", trend="c"); lags = sel.ar_lags or []
        p = max(lags) if lags else 0
        def filt(x, coef=None):
            if not p:
                return x - x.mean(), None
            X = np.column_stack([np.ones(len(x) - p)] + [x[p - j: len(x) - j] for j in lags])
            if coef is None:
                coef = np.linalg.lstsq(X, x[p:], rcond=None)[0]
            return x[p:] - X @ coef, coef
        a, coef = filt(d.g.values); b, _ = filt(d.y.values, coef)
        tt = d.index[p:]
        keep = np.array([not any((t - pd.DateOffset(months=j)) in covid for j in range(2)) for t in tt])
        for l in range(0, 13):
            aa, bb, k1, k2 = a[: len(a) - l], b[l:], keep[: len(a) - l], keep[l:]
            m = k1 & k2
            vals[l].append(np.corrcoef(aa[m], bb[m])[0, 1])
    return {l: np.nanmean(v) for l, v in vals.items()}, len(vals[0])

pre47, n47 = ccf_pre("cat47")
print(f"0. CCF media prewhitened 2018-2022, cat47, sin COVID ({n47} modelos):")
print("   " + "  ".join(f"lag{l}:{pre47[l]:+.3f}" for l in range(0, 13)))
rank4 = sorted(range(1, 13), key=lambda l: -pre47[l]).index(4) + 1
print(f"   Lag 4 ocupa el puesto {rank4} de 12 (lags 1-12) en 2018-2022.")

# ---------- 1. Especificacion principal + placebos ----------
rows = []
specs = [("cat47", 4, [4, 5, 12], "principal"), ("cat473", 4, [4, 5, 12], "secundaria"),
         ("cat47", 1, [1, 2, 12], "secundaria"), ("cat473", 1, [1, 2, 12], "secundaria")]
for cat, h, own, rol in specs:
    E = eligible(cat)
    glags = range(4, 13) if h == 4 else [4]
    for k, (yd, gd) in E.items():
        for gl in glags:
            r = run(yd, gd, h, gl, own)
            if r:
                r.update(cat=cat, h=h, glag=gl, modelo=k, rol=rol); rows.append(r)
R = pd.DataFrame(rows); R.to_csv(f"{OUT}/lag4_contrastes.csv", index=False)

def panel(d):
    n = len(d); pos = int((d.coef > 0).sum()); s5 = int((d.p_CW < 0.05).sum())
    return pd.Series({"modelos": n, "mejora_media_%": d.gain.mean(), "mejora_mediana_%": d.gain.median(),
                      "%_mejoran": 100 * (d.gain > 0).mean(), "CW_sig5": s5,
                      "p_binom": stats.binomtest(s5, n, 0.05, alternative="greater").pvalue,
                      "%_coef_pos": 100 * pos / n, "p_signo": stats.binomtest(pos, n, 0.5, alternative="greater").pvalue})

S = R.groupby(["rol", "cat", "h", "glag"]).apply(panel).reset_index()
S.to_csv(f"{OUT}/lag4_resumen.csv", index=False)
pd.set_option("display.width", 220)
print("\n1. Panel por especificacion y lag (h=4: placebos 4..12):")
print(S.round(3).to_string(index=False))

# ---------- 2. Surrogates para la principal ----------
E = eligible("cat47")
obs = R[(R.rol == "principal") & (R.glag == 4)]
obs_mean, obs_med = obs.gain.mean(), obs.gain.median()
sm, smed = [], []
for i in range(NSUR):
    g_list = []
    for k in obs.modelo:
        yd, gd = E[k]
        lv = np.log1p(GT["cat47"][k][GT["cat47"][k].index >= gd.index.min()]).values
        gs = pd.Series(phase_sur(lv), index=gd.index).diff()
        r = run(yd, gs, 4, 4, [4, 5, 12])
        if r: g_list.append(r["gain"])
    sm.append(np.mean(g_list)); smed.append(np.median(g_list))
p_mean = (np.sum(np.array(sm) >= obs_mean) + 1) / (NSUR + 1)
p_med = (np.sum(np.array(smed) >= obs_med) + 1) / (NSUR + 1)
print(f"\n2. Surrogates ({NSUR}) principal: mejora media obs {obs_mean:+.2f}% (p={p_mean:.3f}, p95 sur {np.percentile(sm,95):+.2f}%), "
      f"mediana obs {obs_med:+.2f}% (p={p_med:.3f})")

# ---------- 3. Regla de decision ----------
pl = S[(S.rol == "principal")].set_index("glag")
c1 = (p_mean < 0.05) and (obs_med > 0)
c2 = pl["mejora_media_%"].idxmax() == 4
c3 = pl.loc[4, "p_signo"] < 0.05
print("\n3. Regla de decision prerregistrada:")
print(f"   (1) mejora > p95 surrogates y mediana > 0: {c1}")
print(f"   (2) lag 4 es el mejor entre 4..12: {c2} (mejor: lag {pl['mejora_media_%'].idxmax()})")
print(f"   (3) coeficiente positivo en la mayoria (p<0.05): {c3} ({pl.loc[4, '%_coef_pos']:.0f}% positivos)")
print(f"   => {'CONFIRMADO' if (c1 and c2 and c3) else 'NO CONFIRMADO'}")
print("\nModelos VW (principal, lag 4):")
print(obs[obs.modelo.str.startswith("vw_")][["modelo", "n", "gain", "p_CW", "coef"]].round(3).to_string(index=False))
