"""Rolling beta and the beta-adjusted residual price series (Plane B).

    r_i   = log(Close_i / Close_i.shift(1))
    r_m   = log(Close_SPY / Close_SPY.shift(1))
    B_i,t = Cov(r_i, r_m)_60d / Var(r_m)_60d
    e_i,t = r_i,t - B_i,t * r_m,t
    R_i,t = 100 * exp(cumsum(e_i))

Why a residual rather than a raw ``XLK / SPY`` ratio: sector betas range from
about 0.5 (XLU, XLP) to 1.2+ (XLK, XLY). A raw ratio therefore bakes beta drift
into what it calls "relative strength" — on any strong up day the high-beta
sectors look like they are rotating in, when all they are doing is having more
beta. Subtracting B * r_m removes the market component and leaves only the part
of the sector's return the market does not explain.

R is deliberately price-*like* so the identical Donchian channel code can run on
it unmodified. Synthetic OHLC is the sector's raw OHLC scaled by R_t / Close_t,
which preserves the intrabar geometry (and makes the synthetic close exactly R).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .params import BetaParams

__all__ = ["log_returns", "rolling_beta", "residual_returns", "residual_series", "residual_ohlc"]


def log_returns(close: pd.Series) -> pd.Series:
    """Log returns. First observation is NaN by construction."""
    close = close.astype("float64")
    # A non-positive price is not tradeable data; log() of it is meaningless.
    safe = close.where(close > 0)
    return np.log(safe / safe.shift(1))


def rolling_beta(r_asset: pd.Series, r_market: pd.Series, params: BetaParams) -> pd.Series:
    """Rolling OLS beta of ``r_asset`` on ``r_market``.

    Cov and Var are both sample statistics (ddof=1), so the degrees-of-freedom
    correction cancels in the ratio and the result equals the OLS slope.
    """
    aligned_asset, aligned_market = r_asset.align(r_market, join="left")

    cov = aligned_asset.rolling(params.window, min_periods=params.window).cov(aligned_market)
    var = aligned_market.rolling(params.window, min_periods=params.window).var()

    # A market with zero realised variance over the window gives no information
    # about beta. NaN (unknown) is the honest answer, not 0 (uncorrelated).
    beta = cov / var.where(var > 0)
    beta.name = "beta"
    return beta


def residual_returns(
    r_asset: pd.Series, r_market: pd.Series, beta: pd.Series
) -> pd.Series:
    """e_t = r_i,t - B_i,t * r_m,t. NaN wherever beta is not yet defined."""
    eps = r_asset - beta * r_market
    eps.name = "residual_return"
    return eps


def residual_series(eps: pd.Series, params: BetaParams) -> pd.Series:
    """Compound residual returns into a synthetic price series based at 100.

    The series starts at the first bar where beta (and therefore epsilon) is
    defined. Leading bars stay NaN — filling them with the base level would
    invent a flat history that the channel logic would read as a real range.
    """
    eps = eps.astype("float64")
    valid = eps.notna()
    if not valid.any():
        return pd.Series(np.nan, index=eps.index, name="residual")

    first = valid.idxmax()
    tail = eps.loc[first:]

    # Any interior NaN means a bar with no residual information. Treating it as
    # a zero residual holds the synthetic price flat for that bar, which is the
    # neutral assumption; propagating NaN would destroy the whole forward series.
    compounded = params.residual_base * np.exp(tail.fillna(0.0).cumsum())

    out = pd.Series(np.nan, index=eps.index, dtype="float64")
    out.loc[first:] = compounded
    out.name = "residual"
    return out


def residual_ohlc(sector_ohlcv: pd.DataFrame, residual: pd.Series) -> pd.DataFrame:
    """Project the sector's OHLC onto the residual plane.

    scale_t = R_t / Close_t, applied to open/high/low/close. The synthetic close
    is then exactly R_t, and high/low keep their proportional distance from the
    close, so Donchian channels on this frame measure residual-space breakouts
    with the same geometry they use in price space.

    Volume is carried through unscaled: it is a real, plane-independent quantity
    and the RVOL filter must see the actual traded volume on both planes.
    """
    close = sector_ohlcv["close"].astype("float64")
    scale = residual / close.where(close > 0)

    out = pd.DataFrame(index=sector_ohlcv.index)
    for col in ("open", "high", "low", "close"):
        out[col] = sector_ohlcv[col].astype("float64") * scale
    out["volume"] = sector_ohlcv["volume"].astype("float64")

    # Guard the (pathological) case where scaling inverts the bar geometry.
    hi = out[["open", "high", "low", "close"]].max(axis=1)
    lo = out[["open", "high", "low", "close"]].min(axis=1)
    out["high"] = hi
    out["low"] = lo
    return out
