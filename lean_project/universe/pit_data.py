"""Point-in-time fundamental + rolling-beta helpers.

Pure module (no Lean/AlgorithmImports import) so it is unit-testable
with plain pytest and reusable at buy-scan time inside Lean.
ISO YYYY-MM-DD strings compare correctly with ``<=``.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def fundamental_as_of(hist: dict, ticker: str, date_str: str) -> Optional[dict]:
    """Latest quarter snapshot with quarter_end <= date_str (inclusive).

    A quarter reported ON the backtest date is considered available.
    """
    qs = [q for q in hist.get(ticker, {}) if q <= date_str]
    return hist[ticker][max(qs)] if qs else None


def latest_price_as_of(bars: dict, date_str: str) -> Optional[float]:
    """Latest bar close with bar date <= date_str (inclusive). Returns None if no bar yet."""
    ds = [d for d in bars if d <= date_str]
    return float(bars[max(ds)]["close"]) if ds else None


def rolling_beta(
    stock_bars: dict,
    market_bars: dict,
    as_of: str,
    window: int = 252,
    min_points: int = 30,
) -> Optional[tuple]:
    """Return (beta, alpha) from trailing ``window`` daily returns up to ``as_of``.

    Aligns stock and market on common trading dates <= as_of, takes the last
    ``window`` aligned days, regresses stock returns on market returns.
    Returns None if fewer than ``min_points`` aligned points are available.
    """
    dates = sorted(d for d in stock_bars if d in market_bars and d <= as_of)
    if len(dates) < min_points:
        return None
    dates = dates[-(window + 1):]
    s = np.array([stock_bars[d]["close"] for d in dates], dtype=float)
    m = np.array([market_bars[d]["close"] for d in dates], dtype=float)
    sr = np.diff(s) / s[:-1]
    mr = np.diff(m) / m[:-1]
    if len(sr) < 2:
        return None
    beta, alpha = np.polyfit(mr, sr, 1)  # beta = slope, alpha = intercept (daily)
    return float(beta), float(alpha)


def erp_as_of(history: dict, date_str: str) -> Optional[dict]:
    """Latest ERP entry with date key < date_str (strictly past).

    Uses strict less-than to guarantee no look-ahead: the ERP published
    ON the backtest date must not be used until the next day.
    """
    hist = history.get("erp_history", {}) if history else {}
    ds = [d for d in hist if d < date_str]
    return hist[max(ds)] if ds else None


def earliest_erp(history: dict) -> Optional[dict]:
    """Return the oldest ERP entry (fallback for dates before the series).

    Returns None if the history is empty or absent.
    """
    hist = history.get("erp_history", {}) if history else {}
    if not hist:
        return None
    return hist[min(hist)]
