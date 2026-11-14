"""
Panel de 50 modelos: ¿se generaliza el resultado del Golf?

Especificacion PRINCIPAL (fijada antes de ejecutar):
    cat473 (Vehicle Shopping), senal GT_YoY3 en t -> objetivo YoY3 de matriculaciones en t+3.
Secundarias: senales YoY1, YoY3, YoY6; objetivos YoY1 (h=1) y YoY3 (h=3); cat47 y cat473;
muestra completa y sin COVID.

Por modelo: origen movil (ventana expansiva, inicio 30), referencia = propio YoY de
matriculaciones; aumentado = referencia + senal GT. Clark-West con HAC (h+2 lags).

Panel: proporcion de mejoras, proporcion de CW significativos vs 5% (binomial), test de signo
del coeficiente de GT, BH (q=0.10) sobre todos los contrastes, y referencia de surrogates
conjunta: en cada replica se sustituye el GT de TODOS los modelos por surrogates de fase de
Fourier (sobre log GT) y se calcula la mejora media del panel.
"""
import sys
import numpy as np
import pandas as pd
from scipy import stats

MAT, GTX, OUT = sys.argv[1], sys.argv[2], sys.argv[3]
N_SUR = int(sys.argv[4]) if len(sys.argv) > 4 else 200
RNG = np.random.default_rng(2026)

NAME_MAP = {
    "dacia_sandero": "Dacia Sandero", "hyundai_tucson": "Hyundai Tucson",
    "kia_sportage": "Kia Sportage", "toyota_chr": "Toyota C-Hr",
    "toyota_corolla": "Toyota Corolla", "dacia_duster": "Dacia Duster",
    "seat_ibiza": "Seat Ibiza", "toyota_yaris": "Toyota Yaris",
    "seat_arona": "Seat Arona", "hyundai_kona": "Hyundai Kona",
    "renault_clio": "Renault Clio", "renault_captur": "Renault Captur",
    "seat_leon": "Seat Leon", "nissan_qashqai": "Nissan Qashqai",
    "peugeot_2008": "Peugeot 2008", "kia_stonic": "Kia Stonic",
    "vw_troc": "Volkswagen T-Roc", "seat_ateca": "Seat Ateca",
    "mg_zs": "Mg Zs", "toyota_yaris_cross": "Toyota Yaris Cross",
    "peugeot_3008": "Peugeot 3008", "toyota_rav4": "Toyota Rav 4",
    "peugeot_208": "Peugeot 208", "hyundai_i20": "Hyundai I20",
    "kia_niro": "Kia Niro", "citroen_c3": "Citroën C3",
    "kia_ceed": "Kia Ceed", "vw_tiguan": "Volkswagen Tiguan",
    "ford_focus": "Ford Focus", "vw_golf": "Volkswagen Golf",
    "ford_kuga": "Ford Kuga", "vw_tcross": "Volkswagen T-Cross",
    "opel_corsa": "Opel Corsa", "kia_xceed": "Kia Xceed",
    "nissan_juke": "Nissan Juke", "hyundai_i30": "Hyundai I30",
    "citroen_c3_aircross": "Citroën C3 Aircross", "mazda_cx5": "Mazda Cx5",
    "vw_polo": "Volkswagen Polo", "ford_puma": "Ford Puma",
    "renault_megane": "Renault Megane", "tesla_model3": "Tesla Model 3",
    "peugeot_308": "Peugeot 308", "bmw_x1": "Bmw X1",
    "skoda_karoq": "Skoda Karoq", "mazda_cx30": "Mazda Cx30",
    "renault_arkana": "Renault Arkana", "citroen_c4": "Citroën C4",
    "skoda_fabia": "Skoda Fabia", "volvo_xc40": "Volvo Xc40",
}

idx = pd.date_range("2018-01-01", "2025-12-01", freq="MS")
mat = pd.read_csv(MAT, parse_dates=["month"])
mat = mat[mat.model_raw.str.upper() != "TOTAL"]
xl = pd.ExcelFile(GTX)
GT = {}
for sh, cat in [("tidy_cat47_autos", "cat47"), ("tidy_cat473_compra", "cat473")]:
    g = pd.read_excel(xl, sh)
    GT[cat] = {k: g[g.modelo == k].set_index("month")["gt"].reindex(idx).astype(float) for k in NAME_MAP}
Y = {k: mat[mat.model_raw == v].groupby("month").units.sum().reindex(idx).fillna(0).astype(float)
     for k, v in NAME_MAP.items()}


def yoy(s, k):
    m = s.rolling(k).mean()
    with np.errstate(divide="ignore", invalid="ignore"):
        r = np.log(m) - np.log(m.shift(12))
    return r.replace([np.inf, -np.inf], np.nan)


covid = pd.date_range("2020-03-01", "2021-06-01", freq="MS")
def contaminado(t, k):
    a = pd.date_range(t - pd.DateOffset(months=k - 1), t, freq="MS")
    return a.isin(covid).any() or (a - pd.DateOffset(months=12)).isin(covid).any()
CONT6 = pd.Series([contaminado(t, 6) for t in idx], index=idx)

TARGETS = {"Y1_h1": (1, 1), "Y3_h3": (3, 3)}
SIGNALS = {"GT_YoY1": 1, "GT_YoY3": 3, "GT_YoY6": 6}


def evaluate(tgt, own, feat, excl, h, start=30):
    df = pd.DataFrame({"tgt": tgt, "own": own, "f": feat}).dropna()
    if excl:
        df = df[~CONT6.reindex(df.index).astype(bool).values]
    n = len(df)
    if n - start < 10:
        return None
    T, O, Fv = df.tgt.values, df.own.values, df.f.values
    eS, eB, pS, pB, cf = [], [], [], [], []
    for i in range(start, n):
        Xs = np.c_[np.ones(i), O[:i]]
        Xb = np.c_[Xs, Fv[:i]]
        bs = np.linalg.lstsq(Xs, T[:i], rcond=None)[0]
        bb = np.linalg.lstsq(Xb, T[:i], rcond=None)[0]
        ps = bs[0] + bs[1] * O[i]
        pb = bb[0] + bb[1] * O[i] + bb[2] * Fv[i]
        eS.append(T[i] - ps); eB.append(T[i] - pb); pS.append(ps); pB.append(pb); cf.append(bb[2])
    eS, eB, pS, pB, cf = map(np.array, (eS, eB, pS, pB, cf))
    f = eS ** 2 - (eB ** 2 - (pS - pB) ** 2)
    L = h + 2
    fc = f - f.mean(); m = len(f)
    v = fc @ fc / m
    for j in range(1, L + 1):
        v += 2 * (1 - j / (L + 1)) * (fc[j:] @ fc[:-j] / m)
    v = max(v, 1e-12) / m
    cw = f.mean() / np.sqrt(v)
    return {"n_eval": m, "mae_ref": np.abs(eS).mean(), "mae_gt": np.abs(eB).mean(),
            "mejora_MAE_%": 100 * (1 - np.abs(eB).mean() / np.abs(eS).mean()),
            "CW": cw, "p_CW": 1 - stats.norm.cdf(cw), "coef_GT": cf[-1]}


# ---------------- elegibilidad ----------------
elig = {}
for k in NAME_MAP:
    z473 = int((GT["cat473"][k] == 0).sum())
    n_y3 = yoy(Y[k], 3).dropna().shape[0]
    elig[k] = {"ceros_cat473": z473, "obs_YoY3_matric": n_y3,
               "primer_mes": Y[k][Y[k] > 0].index.min().date(),
               "elegible": (z473 < 12) and (n_y3 >= 60)}
E = pd.DataFrame(elig).T
ok = [k for k in NAME_MAP if E.loc[k, "elegible"]]
print(f"Modelos elegibles: {len(ok)} de {len(NAME_MAP)}")
print("Excluidos:", E[~E.elegible.astype(bool)][["ceros_cat473", "obs_YoY3_matric", "primer_mes"]].to_string())

# ---------------- todos los contrastes ----------------
rows = []
for k in ok:
    for cat in ("cat473", "cat47"):
        g = GT[cat][k]
        sig = {s: yoy(g, w) for s, w in SIGNALS.items()}
        for excl in (False, True):
            for tn, (tk, h) in TARGETS.items():
                tgt, own = yoy(Y[k], tk).shift(-h), yoy(Y[k], tk)
                for sn, sv in sig.items():
                    r = evaluate(tgt, own, sv, excl, h)
                    if r:
                        r.update(modelo=k, cat=cat, muestra="sin_COVID" if excl else "todo",
                                 objetivo=tn, senal=sn)
                        rows.append(r)
R = pd.DataFrame(rows)

# BH global
p = R.p_CW.values; m = len(p); o = np.argsort(p)
thr = 0.10 * np.arange(1, m + 1) / m
passed = np.zeros(m, bool)
w = np.where(p[o] <= thr)[0]
if len(w):
    passed[o[: w.max() + 1]] = True
R["BH10"] = passed
R.to_csv(f"{OUT}/panel50_contrastes.csv", index=False)

# ---------------- resumen por especificacion ----------------
def resumen(d):
    n = len(d)
    k_sig = int((d.p_CW < 0.05).sum())
    return pd.Series({
        "modelos": n,
        "mejora_MAE_mediana_%": d["mejora_MAE_%"].median(),
        "%_modelos_mejora": 100 * (d["mejora_MAE_%"] > 0).mean(),
        "CW_sig5_n": k_sig,
        "p_binom_vs_5%": stats.binomtest(k_sig, n, 0.05, alternative="greater").pvalue,
        "BH10_n": int(d.BH10.sum()),
        "%_coef_negativo": 100 * (d.coef_GT < 0).mean(),
        "p_signo": stats.binomtest(int((d.coef_GT < 0).sum()), n, 0.5).pvalue,
    })

S = R.groupby(["cat", "muestra", "objetivo", "senal"]).apply(resumen).reset_index()
S.to_csv(f"{OUT}/panel50_resumen.csv", index=False)
pd.set_option("display.width", 230)
print("\n=== Resumen por especificacion (panel) ===")
print(S.round(3).to_string(index=False))

# ---------------- surrogates conjuntos para la especificacion principal ----------------
def phase_sur(x):
    f = np.fft.rfft(x - x.mean())
    ph = RNG.uniform(0, 2 * np.pi, len(f)); ph[0] = 0
    if len(x) % 2 == 0: ph[-1] = 0
    return np.fft.irfft(np.abs(f) * np.exp(1j * ph), n=len(x)) + x.mean()

sur_rows = []
for cat, sname, tname in [("cat473", "GT_YoY3", "Y3_h3"), ("cat473", "GT_YoY6", "Y3_h3"),
                          ("cat47", "GT_YoY3", "Y3_h3")]:
    tk, h = TARGETS[tname]; w = SIGNALS[sname]
    for excl in (False, True):
        tag = "sin_COVID" if excl else "todo"
        obs = R[(R.cat == cat) & (R.senal == sname) & (R.objetivo == tname) & (R.muestra == tag)]
        obs_mean = obs["mejora_MAE_%"].mean(); obs_med = obs["mejora_MAE_%"].median()
        sur_means, sur_meds = [], []
        for _ in range(N_SUR):
            vals = []
            for k in obs.modelo:
                g = GT[cat][k]
                lg = np.log(g.clip(lower=1).values)  # ceros (si los hay) a 1 para el log
                gs = pd.Series(np.exp(phase_sur(lg)), index=idx)
                r = evaluate(yoy(Y[k], tk).shift(-h), yoy(Y[k], tk), yoy(gs, w), excl, h)
                if r: vals.append(r["mejora_MAE_%"])
            sur_means.append(np.mean(vals)); sur_meds.append(np.median(vals))
        sur_rows.append({"cat": cat, "senal": sname, "objetivo": tname, "muestra": tag,
                         "mejora_media_obs_%": obs_mean,
                         "p_sur_media": (np.sum(np.array(sur_means) >= obs_mean) + 1) / (N_SUR + 1),
                         "mejora_mediana_obs_%": obs_med,
                         "p_sur_mediana": (np.sum(np.array(sur_meds) >= obs_med) + 1) / (N_SUR + 1),
                         "sur_media_p95_%": np.percentile(sur_means, 95)})
SUR = pd.DataFrame(sur_rows)
SUR.to_csv(f"{OUT}/panel50_surrogates.csv", index=False)
print("\n=== Referencia de surrogates conjunta (panel) ===")
print(SUR.round(3).to_string(index=False))

# ---------------- detalle de la especificacion principal ----------------
P = R[(R.cat == "cat473") & (R.senal == "GT_YoY3") & (R.objetivo == "Y3_h3")]
piv = P.pivot(index="modelo", columns="muestra", values=["mejora_MAE_%", "p_CW", "coef_GT"]).round(3)
piv.to_csv(f"{OUT}/panel50_principal_por_modelo.csv")
print("\n=== Especificacion principal por modelo (ordenado por mejora, muestra completa) ===")
print(piv.sort_values(("mejora_MAE_%", "todo"), ascending=False).to_string())
