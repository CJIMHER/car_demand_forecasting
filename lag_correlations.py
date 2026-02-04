import numpy as np
import pandas as pd
from scipy.stats import pearsonr, spearmanr
from sklearn.feature_selection import mutual_info_regression
import dcor

def lagged_correlations(series_x, series_y, max_lag=12):
    """
    series_x: pd.Series (VENTAS)
    series_y: pd.Series (GT)
    Returns: pd.DataFrame with rows lag=1..max_lag and columns: pearson, spearman, distance_corr, mutual_info
    """
    results = []
    for lag in range(1, max_lag+1):
        y_lag = series_y.shift(lag).dropna()
        x_aligned = series_x.reindex(y_lag.index).dropna()
        y_aligned = y_lag.reindex(x_aligned.index)
        if len(x_aligned) < 3:
            results.append((lag, np.nan, np.nan, np.nan, np.nan))
            continue
        pr, _ = pearsonr(x_aligned, y_aligned)
        sr, _ = spearmanr(x_aligned, y_aligned)
        try:
            dc = dcor.distance_correlation(x_aligned.values, y_aligned.values)
        except Exception:
            dc = np.nan
        try:
            mi = mutual_info_regression(x_aligned.values.reshape(-1,1), y_aligned.values, random_state=0)
            mi = float(mi[0])
        except Exception:
            mi = np.nan
        results.append((lag, pr, sr, dc, mi))
    df = pd.DataFrame(results, columns=['lag','pearson','spearman','distance_corr','mutual_info'])
    return df