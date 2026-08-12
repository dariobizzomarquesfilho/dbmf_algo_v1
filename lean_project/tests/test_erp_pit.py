"""PIT ERP semantics tests — locks the verified implied_erp connection.

Mirrors the pattern of tests/test_pit_data.py — no Lean imports.
"""
from __future__ import annotations

import pytest

from universe.pit_data import erp_as_of, earliest_erp


ERP_HISTORY = {
    "erp_history": {
        "2013-01-01": {"us_erp": 0.058, "mature_market_erp": 0.058},
        "2014-01-01": {"us_erp": 0.0569, "mature_market_erp": 0.0569},
        "2015-01-01": {"us_erp": 0.054, "mature_market_erp": 0.054},
        "2020-01-01": {"us_erp": 0.052, "mature_market_erp": 0.052},
        "2026-01-01": {"us_erp": 0.044, "mature_market_erp": 0.044},
    }
}


def test_erp_as_of_returns_latest_past_entry():
    """@2020-06-15 should use 2020-01-01 entry (strictly past)."""
    entry = erp_as_of(ERP_HISTORY, "2020-06-15")
    assert entry is not None
    assert entry["us_erp"] == 0.052


def test_erp_as_of_never_uses_future():
    """@2020-01-01 must not return the 2020-01-01 entry itself
    (strictly less than)."""
    entry = erp_as_of(ERP_HISTORY, "2020-01-01")
    assert entry is not None
    assert entry["us_erp"] == 0.054  # 2015-01-01 (last past entry)


def test_erp_as_of_none_before_series():
    """Date before the earliest entry returns None."""
    assert erp_as_of(ERP_HISTORY, "2010-01-01") is None


def test_earliest_erp_returns_oldest():
    oldest = earliest_erp(ERP_HISTORY)
    assert oldest is not None
    assert oldest["us_erp"] == 0.058


def test_erp_as_of_strictly_past_no_lookahead():
    """On the exact date of an entry, the *prior* entry is used.
    This is the anti-look-ahead guarantee."""
    entry = erp_as_of(ERP_HISTORY, "2015-01-01")
    assert entry is not None
    assert entry["us_erp"] == 0.0569  # 2014 entry, not 2015


def test_erp_as_of_empty_history():
    assert erp_as_of({}, "2020-06-15") is None


def test_earliest_erp_empty_history():
    assert earliest_erp({}) is None
