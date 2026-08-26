"""Tests for the equity-data completeness fix (throttled tickers, renames,
out-of-window retention, corporate-action exits).

Network access is mocked throughout: yfinance's ``download``/``Ticker.history``
are replaced so the logic (fetch window, adjustment mode, clipping, throttle
classification, alias resolution, per-member coverage guard, spinoff exits) is
exercised deterministically and fast.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

import data.sp500_data as _sp500_mod
from data.sp500_data import (
    load_sp500_membership,
    clip_to_membership,
    build_alias_map,
)
from data.corporate_actions import (
    last_trading_day,
    spinoff_parent_exits,
    membership_end_exits,
    corporate_action_exits,
)

# Resolve the membership CSV relative to the imported `data.sp500_data` module so
# the path is cwd-independent (the file is at lean_project/data/, not repo-root data/).
_SP500_CSV = str(Path(_sp500_mod.__file__).parent / "sp500_ticker_start_end.csv")


# ---------------------------------------------------------------------------
# clip_to_membership
# ---------------------------------------------------------------------------
def test_clip_to_membership_multi_interval():
    mem = load_sp500_membership(_SP500_CSV)
    bars = {
        "AAL": {
            "1996-01-02": {}, "1996-06-01": {}, "1997-02-01": {},
            "2010-01-01": {}, "2015-03-23": {}, "2024-09-23": {}, "2025-01-01": {},
        }
    }
    clipped = clip_to_membership(bars, mem, "2026-08-01")
    assert sorted(clipped["AAL"].keys()) == [
        "1996-01-02", "1996-06-01", "2015-03-23", "2024-09-23"
    ]


def test_clip_to_membership_renames_and_indices():
    mem = load_sp500_membership(_SP500_CSV)
    bars = {
        "CTL": {"2019-01-01": {}, "2020-09-18": {}, "2020-09-19": {}},
        "CTLT": {"2020-09-21": {}, "2021-01-01": {}},
        "^GSPC": {"1990-01-01": {}, "2026-08-01": {}},
    }
    clipped = clip_to_membership(bars, mem, "2026-08-01")
    assert sorted(clipped["CTL"].keys()) == ["2019-01-01", "2020-09-18"]
    assert sorted(clipped["CTLT"].keys()) == ["2020-09-21", "2021-01-01"]
    # Not in membership -> returned unchanged
    assert sorted(clipped["^GSPC"].keys()) == ["1990-01-01", "2026-08-01"]


# ---------------------------------------------------------------------------
# build_alias_map
# ---------------------------------------------------------------------------
def test_build_alias_map_near_day_successor():
    mem = load_sp500_membership(_SP500_CSV)
    aliases = build_alias_map(mem)
    assert aliases.get("CTL") == "LUMN"
    assert aliases.get("COG") == "CTRA"
    assert aliases.get("UTX") == "CARR"


# ---------------------------------------------------------------------------
# corporate_actions
# ---------------------------------------------------------------------------
def test_last_trading_day_weekend():
    assert last_trading_day(date(2020, 9, 19)) == date(2020, 9, 18)  # Sat
    assert last_trading_day(date(2020, 9, 20)) == date(2020, 9, 18)  # Sun
    assert last_trading_day(date(2020, 9, 21)) == date(2020, 9, 21)  # Mon


def test_spinoff_parent_exits_in_window():
    assert spinoff_parent_exits("2023-01-03") == {"GE"}     # before GEHC 2023-01-04
    assert spinoff_parent_exits("2024-04-01") == {"GE"}     # before GEV 2024-04-02
    assert spinoff_parent_exits("2024-03-29") == {"MMM"}    # before SOLV 2024-04-01
    assert spinoff_parent_exits("2020-04-02") == {"UTX"}    # before OTIS/CARR 2020-04-03
    assert spinoff_parent_exits("2023-08-24") == {"JNJ"}    # before KVUE 2023-08-25
    assert spinoff_parent_exits("2023-01-05") == set()      # not an exit day


def test_membership_end_exits():
    mem = {"CTL": [("2019-01-01", "2020-09-18")]}
    assert "CTL" in membership_end_exits(mem, ["CTL"], "2020-09-18")
    assert "CTL" not in membership_end_exits(mem, ["CTL"], "2020-09-17")
    # current member (end None) never exits via membership end
    mem2 = {"AAPL": [("1996-01-02", None)]}
    assert "AAPL" not in membership_end_exits(mem2, ["AAPL"], "2023-01-01")


def test_corporate_action_exits_union():
    mem = {"GE": [("1996-01-02", None)], "CTL": [("2019-01-01", "2020-09-18")]}
    # spinoff parent (GE) on its exit day
    assert "GE" in corporate_action_exits(mem, ["GE"], "2023-01-03")
    # membership end (CTL) on/after last trading day before end
    assert "CTL" in corporate_action_exits(mem, ["CTL"], "2020-09-18")
    assert "CTL" not in corporate_action_exits(mem, ["CTL"], "2020-09-17")
    # held ticker not in the action set is untouched
    assert corporate_action_exits(mem, ["GE"], "2023-01-05") == set()


# ---------------------------------------------------------------------------
# download_equity_data: fetch window, adjustment mode, clipping
# ---------------------------------------------------------------------------
def test_download_daily_bars_window_adjust_clip(monkeypatch, tmp_path):
    import scripts.download_equity_data as dl

    calls = []

    def fake_download(ticker, start=None, end=None, progress=False, threads=False, auto_adjust=None):
        calls.append((ticker, start, end, auto_adjust))
        if ticker in ("AAPL", "^GSPC"):
            idx = pd.date_range("1990-01-01", "2024-01-01", freq="D")
            return pd.DataFrame(
                {"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5, "Volume": 10.0},
                index=idx,
            )
        return pd.DataFrame()  # empty -> delisted/others

    monkeypatch.setattr(dl.yf, "download", fake_download)

    membership = {"AAPL": [("2020-01-01", "2023-01-01")]}
    out = tmp_path / "bars.json"
    dl.download_daily_bars(
        ["AAPL", "OLD", "^GSPC"], str(out), membership=membership, end_default="2026-08-01"
    )

    bars = json.loads(out.read_text())
    assert "AAPL" in bars
    assert "OLD" not in bars
    assert "^GSPC" in bars

    # AAPL clipped to its membership interval
    aapl_dates = sorted(bars["AAPL"].keys())
    assert aapl_dates[0] >= "2020-01-01"
    assert aapl_dates[-1] <= "2023-01-01"
    # ^GSPC not in membership -> kept whole
    assert sorted(bars["^GSPC"].keys())[0] < "2020-01-01"

    # Fetch window uses HISTORY_START and auto_adjust=True
    assert calls[0][1] == dl.config.HISTORY_START
    assert calls[0][3] is True


# ---------------------------------------------------------------------------
# repair_equity_data: classification, alias, retry
# ---------------------------------------------------------------------------
def _bar():
    return {"2020-01-01": {"open": 1, "high": 1, "low": 1, "close": 1, "volume": 1}}


def test_run_repair_classification_alias_retry(monkeypatch):
    import scripts.repair_equity_data as rp

    thr_attempts = {"n": 0}

    def fake_fetch(ticker, start, end):
        if ticker == "GOOD":
            return _bar(), None
        if ticker == "THR":
            thr_attempts["n"] += 1
            if thr_attempts["n"] >= 2:
                return _bar(), None
            return None, Exception("429 too many requests")
        if ticker == "CUR":
            return None, None  # empty, current member -> throttled (masked)
        if ticker == "HIST":
            return None, None  # empty, historical -> unavailable
        if ticker == "PRE":
            return None, None  # empty, historical predecessor (alias-covered)
        return None, None

    monkeypatch.setattr(rp, "_fetch_with_variants", fake_fetch)

    membership = {
        "GOOD": [("2020-01-01", None)],
        "THR": [("2020-01-01", None)],
        "CUR": [("2020-01-01", None)],
        "HIST": [("2000-01-01", "2010-01-01")],
        "PRE": [("2020-09-18", "2020-09-18")],  # ends 2020-09-18
        "SUCC": [("2020-09-21", None)],          # near-day successor, HAS data
    }
    bars = {"SUCC": {"2020-09-21": _bar()["2020-01-01"]}}
    requested = ["GOOD", "THR", "CUR", "HIST", "PRE", "SUCC"]

    bars2, report = rp.run_repair(
        bars, membership, requested,
        "1996-01-02", "2026-08-01", date(2020, 1, 1),
        backoffs=[], max_passes=3, pacing=0,
    )

    assert "GOOD" in report["recovered"]
    assert "THR" in report["recovered"]          # retried and succeeded
    assert "CUR" in report["pending"]            # current + empty -> PENDING
    assert "HIST" in report["unavailable"]       # historical -> unavailable
    assert ("PRE", "SUCC") in report["resolved_alias"]  # covered by successor
    assert "PRE" not in report["unavailable"]
    assert "PRE" not in report["pending"]
    # clip kept SUCC (in membership) and the recovered bars
    assert "SUCC" in bars2 and "GOOD" in bars2 and "THR" in bars2


def test_run_repair_throttled_current_stays_pending(monkeypatch):
    import scripts.repair_equity_data as rp

    def fake_fetch(ticker, start, end):
        return None, Exception("429 too many requests")

    monkeypatch.setattr(rp, "_fetch_with_variants", fake_fetch)

    membership = {"BK": [("2020-01-01", None)], "CMA": [("2020-01-01", None)]}
    bars = {}
    requested = ["BK", "CMA"]

    bars2, report = rp.run_repair(
        bars, membership, requested,
        "1996-01-02", "2026-08-01", date(2020, 1, 1),
        backoffs=[], max_passes=2, pacing=0,
    )

    assert set(report["pending"].keys()) == {"BK", "CMA"}
    # no data -> not in bars
    assert "BK" not in bars2 and "CMA" not in bars2


# ---------------------------------------------------------------------------
# embed_data: per-current-member coverage guard
# ---------------------------------------------------------------------------
def _write_bars(tmp_path, bars):
    p = tmp_path / "equity_bars.json"
    p.write_text(json.dumps(bars))
    return str(p)


def _member_csv(tmp_path, rows):
    p = tmp_path / "sp500.csv"
    p.write_text("ticker,start_date,end_date\n" + rows)
    return str(p)


def test_validate_per_member_pass(tmp_path):
    import scripts.embed_data as ed

    bars = {
        "AAPL": {"2019-01-14": _bar()["2020-01-01"], "2026-08-01": _bar()["2020-01-01"]},
        "BK": {"2019-01-14": _bar()["2020-01-01"], "2026-08-01": _bar()["2020-01-01"]},
    }
    bars_path = _write_bars(tmp_path, bars)
    csv_path = _member_csv(tmp_path, "AAPL,1996-01-02,\nBK,2020-01-01,\n")

    # Should not raise
    ed.validate_data_coverage(bars_path, fundamentals_path=None, membership_csv_path=csv_path)


def test_validate_per_member_fail_missing(tmp_path):
    import scripts.embed_data as ed

    bars = {"AAPL": {"2019-01-14": _bar()["2020-01-01"], "2026-08-01": _bar()["2020-01-01"]}}
    bars_path = _write_bars(tmp_path, bars)
    csv_path = _member_csv(tmp_path, "AAPL,1996-01-02,\nBK,2020-01-01,\n")
    # BK is in the tradeable (fundamentals) universe, so its missing bars must fail.
    fund_path = str(tmp_path / "fundamentals_history.json")
    (tmp_path / "fundamentals_history.json").write_text(json.dumps({"AAPL": {}, "BK": {}}))

    # validate_data_coverage returns False (the caller exits) when a current
    # member in the tradeable universe has no bars.
    assert ed.validate_data_coverage(bars_path, fundamentals_path=fund_path, membership_csv_path=csv_path) is False


def test_validate_per_member_fail_out_of_range(tmp_path):
    import scripts.embed_data as ed

    bars = {
        "AAPL": {"2019-01-14": _bar()["2020-01-01"], "2026-08-01": _bar()["2020-01-01"]},
        "BK": {"2021-01-01": _bar()["2020-01-01"], "2026-08-01": _bar()["2020-01-01"]},
    }
    bars_path = _write_bars(tmp_path, bars)
    csv_path = _member_csv(tmp_path, "AAPL,1996-01-02,\nBK,2020-01-01,\n")
    # BK is in the tradeable (fundamentals) universe, so its out-of-range bars must fail.
    fund_path = str(tmp_path / "fundamentals_history.json")
    (tmp_path / "fundamentals_history.json").write_text(json.dumps({"AAPL": {}, "BK": {}}))

    assert ed.validate_data_coverage(bars_path, fundamentals_path=fund_path, membership_csv_path=csv_path) is False
