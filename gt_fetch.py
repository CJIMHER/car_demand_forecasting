from pytrends.request import TrendReq
import pandas as pd

def fetch_trends_monthly(keyword, start_ym, end_ym, geo='', cat=None, sleep=0.1):
    """
    keyword: str, e.g. "Toyota Corolla"
    start_ym, end_ym: "YYYY-MM" strings inclusive
    geo: country code e.g. 'ES' or '' for worldwide
    cat: Google Trends category id (int) or None
    Returns: pd.Series indexed by period (YYYY-MM)
    """
    pytrends = TrendReq(hl='es-ES', tz=0)
    # pytrends expects YYYY-MM-DD; build start/end with first/last day
    start = start_ym + "-01"
    end = end_ym + "-01"
    timeframe = f"{start} {end}"
    kw_list = [keyword]
    pytrends.build_payload(kw_list, cat=cat or 0, timeframe=timeframe, geo=geo, gprop='')
    df = pytrends.interest_over_time()
    if df.empty:
        return pd.Series([], dtype=float)
    df = df.resample('M').sum() if not df.index.is_monotonic_increasing else df
    s = df[keyword].groupby(pd.Grouper(freq='M')).mean()
    s.index = s.index.to_period('M').to_timestamp()
    s.name = 'GT'
    return s