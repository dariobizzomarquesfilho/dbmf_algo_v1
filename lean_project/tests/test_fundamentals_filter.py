"""Tests for the strict fundamentals-only ticker filter (T2/T3/T5/T6/T7/T9)."""

from __future__ import annotations

import json
import sys

import pytest


# ---------------------------------------------------------------------------
# load_fundamentals_tickers (common.py)
# ---------------------------------------------------------------------------


def test_load_fundamentals_tickers_union(tmp_path):
    from scripts.common import load_fundamentals_tickers, REQUIRED_INDICES

    f = tmp_path / "fundamentals_history.json"
    f.write_text(json.dumps({"AAPL": {}, "MSFT": {}}))
    result = load_fundamentals_tickers(str(f))
    assert result == {"AAPL", "MSFT"} | REQUIRED_INDICES
    assert "^TNX" in result and "^GSPC" in result


def test_load_fundamentals_tickers_missing_file(tmp_path):
    from scripts.common import load_fundamentals_tickers, REQUIRED_INDICES

    result = load_fundamentals_tickers(str(tmp_path / "nope.json"))
    assert result == set(REQUIRED_INDICES)


def test_load_fundamentals_tickers_empty_and_invalid(tmp_path):
    from scripts.common import load_fundamentals_tickers, REQUIRED_INDICES

    # empty dict -> fallback to required indices
    empty = tmp_path / "empty.json"
    empty.write_text(json.dumps({}))
    assert load_fundamentals_tickers(str(empty)) == set(REQUIRED_INDICES)

    # invalid JSON -> fallback (never raises)
    bad = tmp_path / "bad.json"
    bad.write_text("{not json")
    assert load_fundamentals_tickers(str(bad)) == set(REQUIRED_INDICES)


# ---------------------------------------------------------------------------
# download_equity_data.py uses the strict filter
# ---------------------------------------------------------------------------


def test_download_uses_filtered_list(config_env, monkeypatch):
    import scripts.download_equity_data as m

    filtered = {"AAPL", "MSFT", "^TNX", "^GSPC"}
    monkeypatch.setattr(m, "load_fundamentals_tickers", lambda: set(filtered))
    monkeypatch.setattr(m, "get_sp500_tickers", lambda refresh=False: [])
    monkeypatch.setattr(m, "load_sp500_membership", lambda p: {})

    captured = {}

    def fake_download(tickers, output_path, membership=None, end_default=None):
        captured["tickers"] = list(tickers)

    monkeypatch.setattr(m, "download_daily_bars", fake_download)
    monkeypatch.setattr(sys, "argv", ["download_equity_data.py"])

    m.main()
    assert set(captured["tickers"]) == filtered


# ---------------------------------------------------------------------------
# repair_equity_data.py uses the strict filter for `requested`
# ---------------------------------------------------------------------------


def test_repair_requested_filtered(config_env, monkeypatch, tmp_path):
    import scripts.repair_equity_data as m

    filtered = {"AAPL", "MSFT", "^TNX", "^GSPC"}
    monkeypatch.setattr(m, "load_fundamentals_tickers", lambda: set(filtered))
    monkeypatch.setattr(m, "load_sp500_membership", lambda p: {})
    monkeypatch.setattr(m, "_BARS_PATH", tmp_path / "absent.json")

    captured = {}

    def fake_run(*args, **kwargs):
        captured["requested"] = list(args[2])
        return {}, {
            "recovered": [], "unavailable": [], "pending": {}, "resolved_alias": []
        }

    monkeypatch.setattr(m, "run_repair", fake_run)
    monkeypatch.setattr(sys, "argv", ["repair_equity_data.py"])

    m.main()
    assert set(captured["requested"]) == filtered


# ---------------------------------------------------------------------------
# fetch_missing_delisted.compute_missing filters to fundamentals
# ---------------------------------------------------------------------------


def test_compute_missing_filtered():
    from scripts.fetch_missing_delisted import compute_missing

    membership = {
        "AAPL": [("2019-01-01", None)],
        "MSFT": [("2019-01-01", None)],
        "GOOG": [("2019-01-01", None)],
        "^TNX": [("2019-01-01", None)],
        "^GSPC": [("2019-01-01", None)],
    }
    bars = {"AAPL": {"2020-01-02": {}}}

    # With fundamentals filter, only AAPL/MSFT are candidates; AAPL has bars.
    missing = compute_missing(
        bars, membership, "2020-01-01", "2026-01-01", fundamentals={"AAPL", "MSFT"}
    )
    assert missing == ["MSFT"]

    # Without the filter, GOOG is also missing.
    missing_all = compute_missing(bars, membership, "2020-01-01", "2026-01-01")
    assert set(missing_all) == {"MSFT", "GOOG"}


# ---------------------------------------------------------------------------
# B1 fix guard: no `company.ticker` reference in the edgartools downloader
# ---------------------------------------------------------------------------


def test_b1_no_company_ticker_reference():
    import inspect

    import scripts.download_edgartools_data as m

    source = inspect.getsource(m)
    # Negative guard: no forbidden company-level *current* ticker reference.
    assert "company.ticker" not in source
    # Positive guard: the B1 parse-failure fallback block must be present
    # (it warns on dropped 10-Q filings without using company.ticker).
    assert "get_ticker" in source or 'getattr(company, "tickers"' in source


# ---------------------------------------------------------------------------
# download_edgartools_data._resolve_skip_set (--clean-skip + CIK-map TTL)
# ---------------------------------------------------------------------------


def test_skip_ttl_retry_clears(config_env, tmp_path):
    import os

    import scripts.download_edgartools_data as m

    skip_path = tmp_path / "fundamentals_no_edgar_match.json"
    skip_path.write_text(json.dumps(["OLD1", "OLD2"]))
    cik_map_path = tmp_path / "sp500_cik_map.csv"
    cik_map_path.write_text("ticker,cik\nAAPL,123\n")

    # Force the skip file to be older than the CIK map.
    os.utime(skip_path, (1_000_000, 1_000_000))

    result = m._resolve_skip_set(skip_path, cik_map_path, force=False, clean_skip=False)
    assert result == set()  # cleared because CIK map is newer


def test_skip_no_ttl_keeps(config_env, tmp_path):
    import os

    import scripts.download_edgartools_data as m

    skip_path = tmp_path / "fundamentals_no_edgar_match.json"
    skip_path.write_text(json.dumps(["KEEP1"]))
    cik_map_path = tmp_path / "sp500_cik_map.csv"
    cik_map_path.write_text("ticker,cik\nAAPL,1\n")

    # Force the CIK map to be OLDER than the skip file.
    os.utime(cik_map_path, (1_000_000, 1_000_000))

    result = m._resolve_skip_set(skip_path, cik_map_path, force=False, clean_skip=False)
    assert result == {"KEEP1"}


def test_skip_clean_skip_deletes(config_env, tmp_path):
    import scripts.download_edgartools_data as m

    skip_path = tmp_path / "fundamentals_no_edgar_match.json"
    skip_path.write_text(json.dumps(["X"]))
    cik_map_path = tmp_path / "sp500_cik_map.csv"
    cik_map_path.write_text("ticker,cik\nAAPL,1\n")

    result = m._resolve_skip_set(skip_path, cik_map_path, force=False, clean_skip=True)
    assert not skip_path.exists()
    assert result == set()
