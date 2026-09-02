"""Market regime: dispersion and cross-sectional correlation.

Computed ONCE per run across the whole sector cross-section, not per sector.

    dispersion  = cross-sectional stdev of the 11 sector daily returns
                -> rolling mean (smoothing_window)
                -> trailing percentile rank (percentile_window, ~3y)

    correlation = mean of the off-diagonal elements of the rolling correlation
                  matrix of the 11 sector return series

Why this gates the whole relative plane: when dispersion is in the bottom
quartile of its own 3-year history, the sectors are all doing the same thing and
the residual series are measuring noise. Every Plane B signal in that regime is
a coin flip dressed as a conclusion. A dashboard that keeps rendering confident
rotation calls through a low-dispersion tape is not merely unhelpful, it is
actively harmful — so the UI grays the relative plane out and says why.

Rising correlation is the same warning from the other direction: as the mean
pairwise correlation climbs, the relative plane carries progressively less
information than the absolute one.
"""

from __future__ import annotations

from itertools import combinations

import numpy as np
import pandas as pd

from .params import RegimeParams

__all__ = [
    "cross_sectional_dispersion",
    "dispersion_percentile",
    "mean_pairwise_correlation",
    "compute_regime",
]


def cross_sectional_dispersion(returns: pd.DataFrame, params: RegimeParams) -> pd.DataFrame:
    """Per-day cross-sectional stdev of sector returns, plus its rolling mean.

    ``returns`` is a wide frame: index=date, one column per sector.
    """
    # ddof=1 across the cross-section; require at least two sectors present.
    raw = returns.std(axis=1, ddof=1, skipna=True)
    raw = raw.where(returns.notna().sum(axis=1) >= 2)

    window = params.dispersion.smoothing_window
    smoothed = raw.rolling(window, min_periods=window).mean()

    return pd.DataFrame({"dispersion_raw": raw, "dispersion": smoothed})


def dispersion_percentile(dispersion: pd.Series, params: RegimeParams) -> pd.Series:
    """Trailing percentile rank of dispersion within its own history.

    ``Rolling.rank`` ranks the window's final observation against the rest of
    that window, so this is strictly backward looking — the percentile shown on
    any given day used only data available on that day.

    The full window is required. A "3-year percentile" computed from 200
    observations is a different statistic wearing the same label, and the
    quarter-percentile gate would fire at the wrong times.
    """
    window = params.dispersion.percentile_window
    pct = dispersion.rolling(window, min_periods=window).rank(pct=True) * 100.0
    pct.name = "dispersion_percentile"
    return pct


def mean_pairwise_correlation(returns: pd.DataFrame, params: RegimeParams) -> pd.Series:
    """Mean off-diagonal element of the rolling correlation matrix.

    Computed as the mean over the C(n,2) unique pairs, which equals the mean of
    the off-diagonal entries of the symmetric matrix (each pair appears twice,
    above and below the diagonal, and the diagonal ones are excluded).
    """
    window = params.correlation.window
    cols = list(returns.columns)
    if len(cols) < 2:
        return pd.Series(np.nan, index=returns.index, name="correlation")

    total = pd.Series(0.0, index=returns.index)
    count = pd.Series(0, index=returns.index)

    for a, b in combinations(cols, 2):
        pair = returns[a].rolling(window, min_periods=window).corr(returns[b])
        valid = pair.notna()
        total = total.add(pair.where(valid, 0.0), fill_value=0.0)
        count = count.add(valid.astype(int), fill_value=0)

    mean_corr = total / count.where(count > 0)
    mean_corr.name = "correlation"
    return mean_corr


def compute_regime(returns: pd.DataFrame, params: RegimeParams) -> pd.DataFrame:
    """Full regime frame: dispersion, its percentile, and mean correlation.

    Adds ``low_dispersion``: True when the percentile is below the configured
    threshold. It is deliberately False (not NA-propagating) where the
    percentile is unknown — the API reports that separately as a data-quality
    condition rather than graying the panel out on missing data.
    """
    disp = cross_sectional_dispersion(returns, params)
    pct = dispersion_percentile(disp["dispersion"], params)
    corr = mean_pairwise_correlation(returns, params)

    out = pd.DataFrame(
        {
            "dispersion_raw": disp["dispersion_raw"],
            "dispersion": disp["dispersion"],
            "dispersion_percentile": pct,
            "correlation": corr,
        }
    )
    out["low_dispersion"] = (pct < params.dispersion.low_threshold).fillna(False)
    return out
