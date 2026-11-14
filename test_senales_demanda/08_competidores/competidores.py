import sys, warnings; warnings.filterwarnings("ignore")
import numpy as np, pandas as pd
from sklearn.linear_model import HuberRegressor
from scipy import stats
MAT, GTX, WIX, OUT, NP = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], int(sys.argv[5])
RNG = np.random.default_rng(77)
src = open(sys.argv[6] + "/confirm_lag4.py").read(); NAME_MAP = {}; exec(src[src.index("NAME_MAP"):src.index("idx =")])
SEG = {"SUV-B": "seat_arona renault_captur peugeot_2008 kia_stonic hyundai_kona vw_tcross nissan_juke ford_puma toyota_yaris_cross citroen_c3_aircross mg_zs dacia_duster",
       "SUV-C": "hyundai_tucson kia_sportage nissan_qashqai seat_ateca peugeot_3008 toyota_rav4 vw_tiguan ford_kuga mazda_cx5 skoda_karoq vw_troc toyota_chr kia_niro kia_xceed mazda_cx30 renault_arkana",
       "C": "vw_golf seat_leon toyota_corolla ford_focus kia_ceed hyundai_i30 renault_megane peugeot_308 citroen_c4",
       "B": "dacia_sandero seat_ibiza toyota_yaris renault_clio peugeot_208 hyundai_i20 citroen_c3 opel_corsa vw_polo skoda_fabia",
       "SUV-P": "bmw_x1 volvo_xc40"}
SEG = {s: v.split() for s, v in SEG.items()}; MODS = [m for v in SEG.values() for m in v]; segof = {m: s for s, v in SEG.items() for m in v}
idx = pd.date_range("2018-01-01", "2025-12-01", freq="MS")
mat = pd.read_csv(MAT, parse_dates=["month"])
Y = pd.DataFrame({k: mat[mat.model_raw == NAME_MAP[k]].groupby("month").units.sum().reindex(idx) for k in MODS})
def load(path, sh):
    g = pd.read_excel(path, sh); return pd.DataFrame({k: g[g.modelo == k].set_index("month")["gt"].reindex(idx).astype(float) for k in MODS})
SIG = {"gt47": load(GTX, "tidy_cat47_autos"), "gt473": load(GTX, "tidy_cat473_compra"),
       "wiki_es": load(WIX, "tidy_cat47_autos"), "wiki_en": load(WIX, "tidy_cat473_compra")}
def yoy(D, k):
    L = np.log1p(D.where(D > 0).rolling(k).mean()); return L - L.shift(12)
def rel(D):
    out = D.copy()
    for s, ms in SEG.items():
        sub = D[ms]; mu = sub.mean(axis=1); n = sub.notna().sum(axis=1)
        out[ms] = sub.sub(mu, axis=0).where(n >= 2, axis=0)
    return out
# nota: ventas con ceros (lanzamientos) -> NaN via where(D>0)
relS = {k: rel(yoy(Y, k)) for k in (1, 3, 6)}

def long_panel(rA, k=3, alag=3):
    rS = relS[k]; rows = []
    for m in MODS:
        d = pd.DataFrame({"y": rS[m], "s3": rS[m].shift(3), "s6": rS[m].shift(6), "a": rA[m].shift(alag)}).dropna()
        d["modelo"] = m; rows.append(d)
    return pd.concat(rows)

EV = pd.date_range("2023-01-01", "2025-12-01", freq="MS")
def predict(P):
    eS, eB, cf = [], [], []
    for t in EV:
        tr = P[P.index <= t - pd.DateOffset(months=3)]; te = P[P.index == t]
        if te.empty: continue
        ms = HuberRegressor(max_iter=300).fit(tr[["s3", "s6"]].values, tr.y.values)
        mb = HuberRegressor(max_iter=300).fit(tr[["s3", "s6", "a"]].values, tr.y.values)
        eS += list(np.abs(te.y.values - ms.predict(te[["s3", "s6"]].values)))
        eB += list(np.abs(te.y.values - mb.predict(te[["s3", "s6", "a"]].values))); cf.append(mb.coef_[-1])
    return 100 * (1 - np.mean(eB) / np.mean(eS)), 100 * np.mean(np.array(cf) > 0), len(eS)

def permute(D):
    out = D.copy()
    for s, ms in SEG.items():
        perm = list(ms)
        while any(a == b for a, b in zip(perm, ms)) and len(ms) > 1: perm = list(RNG.permutation(ms))
        out[ms] = D[perm].values
    return out

def ccf(rA, k):
    rS = relS[k]; res = {}
    for l in range(-3, 13):
        a = rA.stack(); s = rS.shift(-l).stack(); j = pd.concat([a, s], axis=1).dropna()
        res[l] = (np.corrcoef(j[0], j[1])[0, 1], stats.spearmanr(j[0], j[1])[0])
    return res

res, ccfs = [], []
for name, D in SIG.items():
    rA = {k: rel(yoy(D, k)) for k in (1, 3, 6)}
    g, pc, n = predict(long_panel(rA[3]))
    plac = {L: predict(long_panel(rA[3], alag=L))[0] for L in range(3, 10)}
    null = []; nullc = {k: [] for k in (1, 3, 6)}
    for i in range(NP):
        Dp = permute(D); rp = rel(yoy(Dp, 3)); null.append(predict(long_panel(rp))[0])
        if i < 100:
            for k in (1, 3, 6):
                rpk = rp if k == 3 else rel(yoy(Dp, k))
                c = ccf(rpk, k); nullc[k].append(max(abs(v[0]) for v in c.values()))
    p = (np.sum(np.array(null) >= g) + 1) / (NP + 1)
    res.append(dict(senal=name, n=n, mejora=g, p_perm=p, p95_null=np.percentile(null, 95), pct_coef_pos=pc,
                    placebos=" ".join(f"{L}:{v:+.1f}" for L, v in plac.items())))
    for k in (1, 3, 6):
        c = ccf(rA[k], k); thr = np.percentile(nullc[k], 95)
        for l, (pr, sp) in c.items(): ccfs.append(dict(senal=name, k=k, lag=l, pearson=pr, spearman=sp, umbral95_max=thr))
    print(name, "hecho", flush=True)
R = pd.DataFrame(res).set_index("senal")
o = R.p_perm.sort_values(); run = 0; holm = {}
for i, (t, v) in enumerate(o.items()): run = max(run, min(1, (len(o) - i) * v)); holm[t] = run
R["p_holm"] = pd.Series(holm); R["admitida"] = (R.mejora > 0) & (R.p_holm < .05) & (R.pct_coef_pos >= 80)
pd.set_option("display.width", 250); print(R.drop(columns="placebos").round(3).to_string()); print(R.placebos.to_string())
C = pd.DataFrame(ccfs); C.to_csv(f"{OUT}/ccf_relativa.csv", index=False); R.to_csv(f"{OUT}/prediccion_relativa.csv")
print("\nCorrelacion agrupada (Pearson) relA_t vs relS_{t+l}; umbral = p95 del max|r| permutado:")
print(C.pivot_table(index=["senal", "k"], columns="lag", values="pearson").round(2).to_string())
print(C.groupby(["senal", "k"]).umbral95_max.first().round(3).to_string())
# VW frente a su segmento: correlacion lag 0 y lag 3 (k=3)
print("\nVW vs segmento (k=3): r lag0 / r lag3")
for name, D in SIG.items():
    rA = rel(yoy(D, 3)); line = []
    for m in [x for x in MODS if x.startswith("vw_")]:
        j0 = pd.concat([rA[m], relS[3][m]], axis=1).dropna(); j3 = pd.concat([rA[m], relS[3][m].shift(-3)], axis=1).dropna()
        line.append(f"{m}:{j0.corr().iloc[0,1]:+.2f}/{j3.corr().iloc[0,1]:+.2f}")
    print(name, "  ".join(line))
