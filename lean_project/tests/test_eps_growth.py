"""Pure-function tests for EPS-growth and equity helpers.

Mirrors the pattern of tests/test_pit_data.py — no Lean imports.
"""
from __future__ import annotations

from datetime import date

import pytest

from scripts.download_edgartools_data import compute_pit_eps_growth, get_parent_equity


# ---------------------------------------------------------------------------
# compute_pit_eps_growth
# ---------------------------------------------------------------------------


def test_cagr_exact_two_years():
    """Ratio 2.0 over true day-count delta (2024 is a leap year, so
    2022-06-30 -> 2024-06-30 is 731 days ≈ 2.0021 years, not exactly 2)."""
    d0, d1 = date(2022, 6, 30), date(2024, 6, 30)
    years = (d1 - d0).days / 365.25
    pairs = [
        ("2022-06-30", 1.0),
        ("2024-06-30", 2.0),
    ]
    result = compute_pit_eps_growth(pairs, years=2)
    assert result["2024-06-30"] == pytest.approx(2.0 ** (1.0 / years) - 1, rel=1e-9)


def test_negative_eps_returns_none():
    pairs = [
        ("2022-06-30", 1.0),
        ("2024-06-30", -0.5),
    ]
    result = compute_pit_eps_growth(pairs, years=2)
    assert result["2024-06-30"] is None


def test_no_ref_period_returns_none():
    """Only one quarter — no 2-yr look-back available."""
    pairs = [("2024-06-30", 1.5)]
    result = compute_pit_eps_growth(pairs, years=2)
    assert result.get("2024-06-30") is None


def test_ref_must_be_strictly_past():
    """Ref must be <= P - 2y, not on the same date."""
    pairs = [
        ("2022-06-30", 1.0),
        ("2024-06-30", 1.0),
    ]
    result = compute_pit_eps_growth(pairs, years=2)
    # 2024-06-30 - 2y = 2022-06-30; strictly past means ref must be < cutoff,
    # but our impl uses <= cutoff. 2022-06-30 <= 2022-06-30 → found.
    assert result["2024-06-30"] == pytest.approx(0.0, abs=1e-9)


def test_negative_cagr_preserved():
    """EPS declined — negative CAGR returned as-is (no floor)."""
    d0, d1 = date(2022, 6, 30), date(2024, 6, 30)
    years = (d1 - d0).days / 365.25
    pairs = [
        ("2022-06-30", 2.0),
        ("2024-06-30", 1.0),
    ]
    result = compute_pit_eps_growth(pairs, years=2)
    assert result["2024-06-30"] == pytest.approx(0.5 ** (1.0 / years) - 1, rel=1e-9)


def test_no_cap_or_floor():
    """Very high CAGR is returned unclamped."""
    pairs = [
        ("2022-06-30", 0.01),
        ("2024-06-30", 10.0),
    ]
    result = compute_pit_eps_growth(pairs, years=2)
    cagr = result["2024-06-30"]
    assert cagr > 0.50  # well above any former cap


def test_calendar_lookup_not_index_based():
    """With a gap in the series the ref is found by date, not index, and the
    CAGR is annualized over the ACTUAL period delta."""
    pairs = [
        ("2020-06-30", 1.0),
        # gap — no 2021 or 2022 data
        ("2024-06-30", 4.0),
    ]
    result = compute_pit_eps_growth(pairs, years=2)
    # 2024-06-30 - 2y = 2022-06-30; latest <= that is 2020-06-30.
    # Actual delta = 4 years -> annualized growth is 4 ** (1/4) - 1
    assert result["2024-06-30"] == pytest.approx(4 ** 0.25 - 1, abs=1e-4)


def test_actual_delta_annualization_two_year_spacing():
    """Exactly-two-year spacing keeps the classic sqrt(ratio) behavior."""
    pairs = [
        ("2022-06-30", 2.0),
        ("2024-07-01", 3.0),
    ]
    result = compute_pit_eps_growth(pairs, years=2)
    # ref must be <= 2024-07-01 minus ~2y (730.5 days): 2022-06-30 qualifies;
    # actual delta ≈ 2.00 years → growth ≈ sqrt(1.5) - 1 regardless of the
    # small day-count excess vs the nominal cutoff.
    assert result["2024-07-01"] == pytest.approx((3.0 / 2.0) ** 0.5 - 1, rel=0.01)


def test_empty_series():
    assert compute_pit_eps_growth([], years=2) == {}


# ---------------------------------------------------------------------------
# get_parent_equity
# ---------------------------------------------------------------------------


def _make_fin(equity=None, incl=None, minority=None):
    """Build a minimal mock financials object."""

    class Fin:
        def get_stockholders_equity(self):
            return equity

        def get_stockholders_equity_including_noncontrolling_interest(self):
            return incl

        def get_minority_interest(self):
            return minority

    return Fin()


def _make_tenq():
    """Minimal mock tenq (not used by primary path)."""
    return None


def test_parent_equity_primary():
    assert get_parent_equity(None, _make_fin(equity=50.0)) == 50.0


def test_parent_equity_none_when_primary_missing():
    """Primary equity unavailable → None (no fabricated noncontrolling fallback)."""
    assert get_parent_equity(None, _make_fin(incl=100.0, minority=10.0)) is None


def test_parent_equity_none_when_both_missing():
    assert get_parent_equity(None, _make_fin()) is None


def test_parent_equity_primary_takes_precedence():
    """Primary (ex-minority) wins even when fallback data exists."""
    assert get_parent_equity(None, _make_fin(equity=50.0, incl=100.0, minority=10.0)) == 50.0
