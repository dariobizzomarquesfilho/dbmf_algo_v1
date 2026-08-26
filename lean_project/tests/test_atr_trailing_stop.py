"""Tests for ATR trailing stop."""

from __future__ import annotations

import sys
import types

# Stub AlgorithmImports (Lean module not available in pytest)
if "AlgorithmImports" not in sys.modules:
    _stub = types.ModuleType("AlgorithmImports")
    # Minimal TradeBar for type hints if needed
    class _TradeBar:  # noqa: D101
        pass
    _stub.TradeBar = _TradeBar
    sys.modules["AlgorithmImports"] = _stub

import pytest

from indicators.atr_trailing_stop import compute_atr_trailing_stop


def _bars(n, price=100, atr=1):
    """Helper: build n daily bars with constant TR."""
    bars = {}
    for i in range(n):
        d = f"2020-01-{i+1:02d}"
        # high/low close produce TR ~ 2
        bars[d] = {"open": price, "high": price + 1, "low": price - 1, "close": price, "volume": 1000}
        price += 0.5
    return bars


def test_period_zero_raises():
    with pytest.raises(ValueError):
        compute_atr_trailing_stop("AAPL", {"AAPL": _bars(20)}, period=0)


def test_insufficient_data_none():
    bars = {"AAPL": _bars(5)}
    assert compute_atr_trailing_stop("AAPL", bars, period=15) is None
    # as_of too early
    assert compute_atr_trailing_stop("AAPL", {"AAPL": _bars(20)}, period=15, as_of_date="2020-01-05") is None


def test_sma_basic():
    bars = {"AAPL": _bars(30, price=100)}
    cache = {"AAPL": bars["AAPL"]}
    stop = compute_atr_trailing_stop("AAPL", cache, period=15, multiplier=3.0, smoothing="SMA")
    assert stop is not None
    # SMA ATR ~2, price ~114.5, stop ~108.5
    assert 90 < stop < 120


def test_as_of_bisect():
    bars = _bars(30)
    cache = {"AAPL": bars}
    # as_of 2020-01-20 should use only first 20 bars
    stop_early = compute_atr_trailing_stop("AAPL", cache, period=15, as_of_date="2020-01-20")
    stop_late = compute_atr_trailing_stop("AAPL", cache, period=15, as_of_date="2020-01-30")
    assert stop_early is not None
    assert stop_late is not None
    assert stop_late > stop_early  # price trending up


def test_prev_stop_ratchet():
    bars = _bars(30, price=100)
    cache = {"AAPL": bars}
    stop1 = compute_atr_trailing_stop("AAPL", cache, period=15, multiplier=3.0, as_of_date="2020-01-25")
    stop2 = compute_atr_trailing_stop("AAPL", cache, period=15, multiplier=3.0, as_of_date="2020-01-30", prev_stop=stop1)
    # Ratchet: never goes down
    assert stop2 >= stop1


def test_smoothing_variants():
    bars = _bars(30)
    cache = {"AAPL": bars}
    for sm in ["SMA", "EMA", "WMA", "RMA", "sma", "ema"]:
        stop = compute_atr_trailing_stop("AAPL", cache, period=15, smoothing=sm)
        assert stop is not None
        assert stop > 0


def test_nan_guard():
    bars = {"AAPL": {"2020-01-01": {"open": 100, "high": 101, "low": 99, "close": float("nan"), "volume": 1000}}}
    assert compute_atr_trailing_stop("AAPL", bars, period=15) is None


def test_zero_atr_none():
    # flat prices => TR 0 => atr 0 => should return None (guard)
    flat = {}
    for i in range(20):
        d = f"2020-01-{i+1:02d}"
        flat[d] = {"open": 100, "high": 100, "low": 100, "close": 100, "volume": 1000}
    assert compute_atr_trailing_stop("AAPL", {"AAPL": flat}, period=15) is None
