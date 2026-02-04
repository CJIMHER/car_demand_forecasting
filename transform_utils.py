import numpy as np
import pandas as pd
from scipy import stats

def choose_and_transform(series):
    """
    series: pd.Series of non-negative values (sales or GT)
    returns: transformed series, transform_info dict
    Strategy:
      - If zeros present: prefer Yeo-Johnson (works with zeros and negatives)
      - Else: try Box-Cox, examine lambda and fallback to log1p if lambda ~0
    """
    s = series.copy().astype(float)
    info = {}
    if (s <= 0).any():
        # Use Yeo-Johnson
        transformed, lmbda = stats.yeojohnson(s.values)
        info['method'] = 'yeo-johnson'
        info['lambda'] = lmbda
        return pd.Series(transformed, index=s.index), info
    else:
        # Try Box-Cox
        try:
            transformed, lmbda = stats.boxcox(s.values)
            info['method'] = 'boxcox'
            info['lambda'] = lmbda
            # If lambda is ~0 then boxcox ~= log; optionally use log1p
            if abs(lmbda) < 0.15:
                info['note'] = 'lambda ~ 0, consider log1p'
            return pd.Series(transformed, index=s.index), info
        except Exception as e:
            # fallback
            transformed = np.log1p(s.values)
            info['method'] = 'log1p-fallback'
            info['lambda'] = None
            info['error'] = str(e)
            return pd.Series(transformed, index=s.index), info