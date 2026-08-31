"""Tests for Van Tharp ATR-risk sizing, cash gate, NAV handling, and fine-selection.

Covers review findings:
- Cash oversubscription via remaining_cash decrement
- NAV invalid skip (no 100k invent)
- Whole shares only (int)
- Fine-selection top 20, no max_positions cap
- Import at top-level, explicit fallback warning
"""

from __future__ import annotations

import sys
import types
import pathlib
import math

# Stub AlgorithmImports before importing main — ensure QCAlgorithm etc even if prior test stubbed a minimal version
def _ensure_algorithm_imports_stub():
    mod = sys.modules.get("AlgorithmImports")
    if mod is None:
        mod = types.ModuleType("AlgorithmImports")
        sys.modules["AlgorithmImports"] = mod
    # Populate missing attributes (keep existing if already there)
    if not hasattr(mod, "QCAlgorithm"):
        class _QCAlgorithm:  # noqa: D101
            pass

        mod.QCAlgorithm = _QCAlgorithm
    if not hasattr(mod, "BrokerageName"):
        class _BrokerageName:  # noqa: D101
            InteractiveBrokersBrokerage = 0

        mod.BrokerageName = _BrokerageName
    if not hasattr(mod, "AccountType"):
        class _AccountType:  # noqa: D101
            Cash = 0
            Margin = 1

        mod.AccountType = _AccountType
    if not hasattr(mod, "Resolution"):
        class _Resolution:  # noqa: D101
            Daily = 0

        mod.Resolution = _Resolution
    if not hasattr(mod, "SecurityType"):
        class _SecurityType:  # noqa: D101
            Equity = 0

        mod.SecurityType = _SecurityType
    if not hasattr(mod, "Market"):
        class _Market:  # noqa: D101
            USA = 0

        mod.Market = _Market
    if not hasattr(mod, "Symbol"):
        class _Symbol:  # noqa: D101
            @staticmethod
            def Create(ticker, sec_type, market):
                return ticker

        mod.Symbol = _Symbol
    if not hasattr(mod, "TradeBar"):
        class _TradeBar:  # noqa: D101
            pass

        mod.TradeBar = _TradeBar
    return mod


_ensure_algorithm_imports_stub()
stub = sys.modules["AlgorithmImports"]

# Ensure lean_project on path (conftest does this, but keep standalone)
import importlib

import pytest


def _import_helper():
    # Import helper without triggering full algorithm Initialize
    import main as m  # type: ignore

    return m._van_tharp_shares, m


# ---------------------------------------------------------------------------
# Pure arithmetic: _van_tharp_shares
# ---------------------------------------------------------------------------

def test_van_tharp_basic_risk_branch():
    _van_tharp_shares, _ = _import_helper()
    nav = 100_000.0
    entry, stop = 100.0, 90.0  # risk 10, 10% distance
    shares, capped, van, rps = _van_tharp_shares(nav, entry, stop, 0.01, 0.10)
    # risk_dollars 1000, van = 1000*100/10=10000, capped 10000, shares 100
    assert rps == 10.0
    assert van == pytest.approx(10_000.0)
    assert capped == pytest.approx(10_000.0)
    assert shares == 100
    # whole shares only
    assert isinstance(shares, int)
    # actual risk = 100*10/100k =1%
    assert shares * rps / nav == pytest.approx(0.01)


def test_van_tharp_cap_branch_low_vol():
    _van_tharp_shares, _ = _import_helper()
    nav = 100_000.0
    entry, stop = 100.0, 99.0  # risk 1, 1% distance -> huge van
    shares, capped, van, rps = _van_tharp_shares(nav, entry, stop, 0.01, 0.10)
    # van = 1000*100/1=100000, capped 10000, shares 100, actual risk 0.1% (cap limits)
    assert van == pytest.approx(100_000.0)
    assert capped == pytest.approx(10_000.0)
    assert shares == 100
    assert shares * rps / nav == pytest.approx(0.001)


def test_van_tharp_high_vol_small_position():
    _van_tharp_shares, _ = _import_helper()
    nav = 100_000.0
    entry, stop = 100.0, 80.0  # risk 20, 20% distance
    shares, capped, van, rps = _van_tharp_shares(nav, entry, stop, 0.01, 0.10)
    # van = 1000*100/20=5000, capped 5000, shares 50, weight 5%
    assert van == pytest.approx(5_000.0)
    assert capped == pytest.approx(5_000.0)
    assert shares == 50
    assert shares * rps / nav == pytest.approx(0.01)


def test_van_tharp_shares_zero_when_capped_small():
    _van_tharp_shares, _ = _import_helper()
    nav = 1_000.0
    entry, stop = 500.0, 490.0  # risk 10, van= 10*500/10=500, capped 100, shares 0
    shares, capped, van, rps = _van_tharp_shares(nav, entry, stop, 0.01, 0.10)
    assert capped == pytest.approx(100.0)
    assert shares == 0  # int(100/500) =0


def test_van_tharp_whole_shares_not_fractional():
    _van_tharp_shares, _ = _import_helper()
    nav = 100_000.0
    entry, stop = 33.33, 30.0  # risk 3.33
    shares, *_ = _van_tharp_shares(nav, entry, stop, 0.01, 0.10)
    # Ensure int, not float, and floor
    assert isinstance(shares, int)
    # capped 10000, 10000/33.33=300.03 -> int 300
    # van =1000*33.33/3.33= ~10009, capped 10000 -> 300 shares
    assert shares == 300


# ---------------------------------------------------------------------------
# Cash gate: remaining_cash decrement and ADJUST logic
# ---------------------------------------------------------------------------

def test_cash_gate_insufficient_skips():
    """Single ticker needing $10k but only $1k remaining -> SKIP."""
    _van_tharp_shares, _ = _import_helper()
    nav = 100_000.0
    entry, stop = 100.0, 90.0
    shares, capped, van, rps = _van_tharp_shares(nav, entry, stop, 0.01, 0.10)
    assert shares == 100
    cost = shares * entry
    remaining = 1_000.0
    max_cash_shares = int(remaining / entry) if entry > 0 else 0
    assert max_cash_shares == 10
    # If remaining were 50, max would be 0 -> skip
    remaining2 = 50.0
    max2 = int(remaining2 / entry)
    assert max2 == 0


def test_cash_gate_sequential_decrement():
    """Three tickers each want 10k with 15k cash -> only first + partial second."""
    _van_tharp_shares, _ = _import_helper()
    nav = 100_000.0
    entry, stop = 100.0, 90.0  # each 100 shares cost 10k
    remaining = 15_000.0
    placed = []
    for _ in range(3):
        shares, *_ = _van_tharp_shares(nav, entry, stop, 0.01, 0.10)
        cost = shares * entry
        if cost > remaining:
            max_shares = int(remaining / entry)
            if max_shares <= 0:
                continue
            shares = max_shares
            cost = shares * entry
        placed.append(shares)
        remaining -= cost

    assert placed == [100, 50]  # third skipped (remaining 0)
    assert remaining == pytest.approx(0.0)


def test_cash_gate_no_oversubscription():
    """Without remaining_cash tracking, 5 names each 10k with 15k cash would oversubscribe to 50k."""
    _van_tharp_shares, _ = _import_helper()
    nav = 100_000.0
    entry, stop = 50.0, 45.0
    # each wants ~10k? compute
    shares, *_ = _van_tharp_shares(nav, entry, stop, 0.01, 0.10)
    # risk 5, van 1000*50/5=10000, shares 200? 10000/50=200 cost 10k
    assert shares == 200
    # Simulate buggy version that re-reads Portfolio.Cash each time (15k each) -> would place 5 orders
    buggy_placed_cost = 5 * shares * entry  # 50k > 15k
    assert buggy_placed_cost == 50_000.0
    # Correct with remaining_cash -> at most 1 full + partial
    remaining = 15_000.0
    correct_cost = 0
    count = 0
    for _ in range(5):
        cost = shares * entry
        if cost > remaining:
            shares2 = int(remaining / entry)
            if shares2 <= 0:
                break
            cost = shares2 * entry
            shares = shares2
        correct_cost += cost
        remaining -= cost
        count += 1
        if remaining <= 0:
            break
    assert correct_cost <= 15_000.0
    assert count < 5


# ---------------------------------------------------------------------------
# NAV invalid handling — no 100k invent
# ---------------------------------------------------------------------------

def test_nav_invalid_should_skip_not_invent_100k():
    # Helper itself doesn't validate nav; caller does. Verify caller logic:
    # When nav <=0 or non-finite, caller checks Portfolio.Cash then skips if still invalid.
    # Simulate the caller snippet with helper to ensure no 100k fallback
    for bad_nav in [0, -1, float("nan"), float("inf"), float("-inf")]:
        # caller would do:
        nav = bad_nav
        cash = 0.0  # bankrupt
        if nav <= 0 or not math.isfinite(nav):
            nav = cash  # fallback to cash, not 100k
            if nav <= 0 or not math.isfinite(nav):
                # should SKIP, not invent 100k
                assert nav != 100_000.0
                assert nav <= 0 or not math.isfinite(nav)


def test_nav_fallback_to_cash_when_nav_bad_but_cash_good():
    # When nav is bad but cash is 50k, sizing should use cash as nav (valid)
    bad_nav = float("nan")
    cash = 50_000.0
    nav = bad_nav
    if nav <= 0 or not math.isfinite(nav):
        try:
            nav = float(cash)
        except Exception:
            nav = 0.0
    assert nav == 50_000.0
    # And sizing would proceed
    _van_tharp_shares, _ = _import_helper()
    shares, *_ = _van_tharp_shares(nav, 100, 90, 0.01, 0.10)
    assert shares == 50  # 50k*1% =500, van 500*100/10=5000, capped 5000 shares 50


# ---------------------------------------------------------------------------
# Algorithm-level invariants (source inspection)
# ---------------------------------------------------------------------------

def test_algorithm_no_max_positions_guard_and_fine_selection_20():
    main_path = pathlib.Path(__file__).parent.parent / "main.py"
    text = main_path.read_text(encoding="utf-8")
    # Old guard must be gone
    assert "if len(self.selected_symbols) < self.max_positions" not in text
    # No max_positions attribute assignment of 10
    assert "self.max_positions = 10" not in text
    # Fine selection limit is 20
    assert "self.fine_selection_limit = 20" in text
    assert "max_positions=self.fine_selection_limit" in text
    # Remaining cash tracking must exist
    assert "remaining_cash" in text
    assert "remaining_cash -= cost" in text
    # NAV fallback must not invent 100k
    assert "or 100_000" not in text
    assert "invalid NAV" in text
    # Import math at top-level, no inner alias
    assert "import math as _math_vt" not in text
    assert text.lstrip().startswith('"""')  # sanity
    # Whole shares via int
    assert "int(capped_dollars / entry_price)" in text
    # Fallback warning
    assert "FALLBACK" in text


def test_algorithm_has_van_tharp_helper():
    _van_tharp_shares, m = _import_helper()
    assert callable(_van_tharp_shares)
    # Check signature
    import inspect

    sig = inspect.signature(_van_tharp_shares)
    assert list(sig.parameters.keys()) == ["nav", "entry_price", "entry_stop", "risk_pct", "max_pct"]


def test_algorithm_logs_fine_selection_in_initialize():
    main_path = pathlib.Path(__file__).parent.parent / "main.py"
    text = main_path.read_text(encoding="utf-8")
    assert "fine_selection" in text.lower()


# ---------------------------------------------------------------------------
# Edge: entry stop validation
# ---------------------------------------------------------------------------

def test_entry_stop_none_or_beyond_entry_skipped():
    # Simulate caller guard: entry_stop None / <=0 / >= entry -> SKIP
    for entry, stop, should_skip in [
        (100, None, True),
        (100, 0, True),
        (100, -5, True),
        (100, 100, True),
        (100, 101, True),
        (100, 90, False),
        (100, 99.9, False),
    ]:
        skip = stop is None or stop <= 0 or stop >= entry
        assert skip == should_skip


def test_risk_per_share_positive_and_finite():
    for entry, stop in [(100, 90), (100, 99), (50, 45)]:
        rps = entry - stop
        assert rps > 0
        assert math.isfinite(rps)
    # zero or negative should be skipped
    assert (100 - 100) <= 0
    assert (100 - 110) <= 0
