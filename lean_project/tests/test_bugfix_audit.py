"""Regression tests for the production-readiness audit bugfixes.

Each test targets one confirmed bug that was reproduced red before the fix:
rf double-division fallback, ghost subscriptions, ignored --start-date/
--end-date args, and insertion-order-dependent bar-quality iteration. Helpers
are imported inside the tests so the module collects even where the target
modules are absent.
"""

from __future__ import annotations

import pandas as pd


# ---------------------------------------------------------------------------
# Bug 1 — RF double-division fallback (pb_roe_universe.run_fine_selection)
# ---------------------------------------------------------------------------

def test_resolve_rf_pit_bar_used_once():
    """Latest ^TNX close <= as_of -> /100 exactly once."""
    from universe.pit_data import resolve_risk_free_rate

    tn_bars = {"2024-01-02": {"close": 4.25}}
    assert resolve_risk_free_rate(tn_bars, "2024-06-01") == 0.0425


def test_resolve_rf_no_pit_bar_uses_default_not_future():
    """No bar <= as_of: must NOT use a future close (that is look-ahead); returns None (no invented yield)."""
    from universe.pit_data import resolve_risk_free_rate

    tn_bars = {"2025-01-01": {"close": 4.5}}
    assert resolve_risk_free_rate(tn_bars, "2024-06-01") is None


def test_resolve_rf_empty_bars_default():
    from universe.pit_data import resolve_risk_free_rate

    assert resolve_risk_free_rate({}, "2024-06-01") is None


# ---------------------------------------------------------------------------
# Bug 2 — ghost subscriptions (main._ensure_subscribed)
# ---------------------------------------------------------------------------

def test_intervals_active_dead_member_false():
    """A member that exited long before backtest start must NOT be active."""
    from data.sp500_data import intervals_active

    entries = [("2005-01-01", "2008-01-01")]
    assert intervals_active(entries, "2020-01-05") is False


def test_intervals_active_on_end_date_inclusive():
    from data.sp500_data import intervals_active

    entries = [("2019-01-01", "2020-09-18")]
    assert intervals_active(entries, "2020-09-18") is True


def test_intervals_active_start_equals_date():
    from data.sp500_data import intervals_active

    entries = [("2020-01-05", None)]
    assert intervals_active(entries, "2020-01-05") is True


def test_intervals_active_second_stint_only():
    from data.sp500_data import intervals_active

    entries = [("1996-01-02", "2001-01-01"), ("2010-01-01", None)]
    assert intervals_active(entries, "2005-06-01") is False
    assert intervals_active(entries, "2012-06-01") is True


# ---------------------------------------------------------------------------
# Bug 3 — ignored --start-date / --end-date CLI args
# ---------------------------------------------------------------------------

def test_download_daily_bars_honors_explicit_dates(monkeypatch, tmp_path):
    import scripts.download_equity_data as dl

    calls = []

    def fake_download(ticker, start=None, end=None, progress=False, threads=False, auto_adjust=None):
        calls.append((ticker, start, end, auto_adjust))
        return pd.DataFrame()  # empty -> no bars, just capture args

    monkeypatch.setattr(dl.yf, "download", fake_download)

    dl.download_daily_bars(
        ["AAPL"], str(tmp_path / "bars.json"), membership=None, end_default="2016-01-01",
        start="2015-01-01", end="2016-01-01",
    )
    # yfinance treats end as EXCLUSIVE -> one extra day
    assert calls[0] == ("AAPL", "2015-01-01", "2016-01-02", True)


def test_download_daily_bars_defaults_to_module_constants(monkeypatch, tmp_path):
    import scripts.download_equity_data as dl

    calls = []

    def fake_download(ticker, start=None, end=None, progress=False, threads=False, auto_adjust=None):
        calls.append((ticker, start, end, auto_adjust))
        return pd.DataFrame()

    monkeypatch.setattr(dl.yf, "download", fake_download)

    dl.download_daily_bars(["AAPL"], str(tmp_path / "bars.json"), membership=None, end_default=dl.config.BACKTEST_END)
    # Defaults fall through to module constants (verifies the feature is honored).
    assert calls[0][1] == dl.config.HISTORY_START
    assert calls[0][2] == dl.BACKTEST_END_EXCLUSIVE


# ---------------------------------------------------------------------------
# Bug 4 — unsorted bar iteration in bar_quality
# ---------------------------------------------------------------------------

def test_ticker_quality_verdict_independent_of_insertion_order():
    from data.bar_quality import ticker_quality_verdict

    # Two regimes (1.0 then 1.7) over 40 days, with redundant daily bars.
    dates_a = [f"2020-01-{d:02d}" for d in range(1, 21)]
    dates_b = [f"2020-02-{d:02d}" for d in range(1, 21)]

    def _bar(c):
        return {"open": c, "high": c, "low": c, "close": c, "volume": 0}

    # Chronological insertion -> a single 1.0->1.7 step (one extreme move).
    chrono = {d: _bar(1.0) for d in dates_a}
    chrono.update({d: _bar(1.7) for d in dates_b})

    # Non-chronological (interleaved) insertion -> the step edge is repeated
    # between every adjacent pair -> many spurious extreme moves. The verdict
    # MUST be the same as chronological; currently (unsorted iteration) it is not.
    interleaved = {}
    for a, b in zip(dates_a, dates_b):
        interleaved[a] = _bar(1.0)
        interleaved[b] = _bar(1.7)

    verdict_chrono, _ = ticker_quality_verdict(chrono)
    verdict_interleaved, _ = ticker_quality_verdict(interleaved)
    assert verdict_chrono == verdict_interleaved
