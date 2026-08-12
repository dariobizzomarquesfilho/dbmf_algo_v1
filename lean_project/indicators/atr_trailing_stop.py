"""ATR trailing stop calculation for Lean backtest.

Uses pre-loaded equity bars (from data/equity_bars.py embedded data)
instead of QC's History() API (which requires CSV.zip data files on disk).
Computes True Range, ATR with SMA smoothing, and trailing stop price.
"""

from __future__ import annotations

from bisect import bisect_right
from typing import Optional

from AlgorithmImports import *


def compute_atr_trailing_stop(
    symbol_str: str,
    bars_cache: dict,
    period: int = 15,
    multiplier: float = 3.0,
    smoothing: str = "SMA",
    as_of_date: Optional[str] = None,
    prev_stop: Optional[float] = None,
) -> Optional[float]:
    """Compute ATR trailing stop price for a symbol using embedded equity bars.

    Args:
        symbol_str: Ticker string (e.g. 'AAPL').
        bars_cache: Pre-loaded equity bars dict from data/equity_bars.py.
        period: ATR lookback period.
        multiplier: ATR multiplier for stop distance.
        smoothing: 'SMA', 'EMA', 'WMA', 'RMA'.
        as_of_date: Only use bars on or before this date (YYYY-MM-DD).
            Prevents look-ahead bias from full-cache sorting. If None, uses
            all bars (backward compatibility — never call this way in backtest).
        prev_stop: Previous stop level for ratchet logic. When provided,
            the returned stop never goes below this level (stop only rises).
            If the price has breached prev_stop, the stop resets to the
            current raw level and starts climbing again.

    Returns:
        Trailing stop price, or None if insufficient data.
    """
    if period <= 0:
        raise ValueError(f"ATR period must be positive, got {period}")

    ticker_bars = bars_cache.get(symbol_str, {})
    if not ticker_bars:
        return None

    sorted_dates = sorted(ticker_bars.keys())
    if as_of_date is not None:
        idx = bisect_right(sorted_dates, as_of_date)
        if idx < period + 2:
            return None
        sorted_dates = sorted_dates[:idx]
    elif len(sorted_dates) < period + 2:
        return None

    recent = sorted_dates[-(period + 5):]
    bars = [ticker_bars[d] for d in recent]

    close_prices = [b["close"] for b in bars]
    high_prices = [b["high"] for b in bars]
    low_prices = [b["low"] for b in bars]

    if len(close_prices) < 2:
        return None

    tr_values = []
    for i in range(1, len(close_prices)):
        hc = abs(high_prices[i] - close_prices[i - 1])
        lc = abs(low_prices[i] - close_prices[i - 1])
        hf = high_prices[i] - low_prices[i]
        tr_values.append(max(hc, lc, hf))

    if len(tr_values) < period:
        return None

    recent_tr = tr_values[-period:]
    if smoothing == "SMA" or smoothing == "0":
        atr = sum(recent_tr) / len(recent_tr)
    elif smoothing == "EMA":
        atr = _ema(recent_tr, period)
    elif smoothing == "WMA":
        atr = _wma(recent_tr, period)
    elif smoothing == "RMA":
        atr = _rma(recent_tr, period)
    else:
        atr = sum(recent_tr) / len(recent_tr)

    raw_stop = close_prices[-1] - multiplier * atr

    # Ratchet logic: stop only goes up. If prev_stop exists and price
    # hasn't breached it, lock in the higher level. If price breached
    # prev_stop, reset to raw_stop (handled by caller removing the
    # position on breach).
    if prev_stop is not None:
        return max(prev_stop, raw_stop)

    return raw_stop


def _ema(values, period):
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    alpha = 2 / (period + 1)
    e = values[0]
    for v in values[1:]:
        e = v * alpha + e * (1 - alpha)
    return e


def _wma(values, period):
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    recent = values[-period:]
    weights = list(range(1, period + 1))
    return sum(v * w for v, w in zip(recent, weights)) / sum(weights)


def _rma(values, period):
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    alpha = 1 / period
    r = values[0]
    for v in values[1:]:
        r = v * alpha + r * (1 - alpha)
    return r
