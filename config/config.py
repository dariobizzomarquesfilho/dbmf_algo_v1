import sys
import os
from pathlib import Path
from datetime import date, datetime, timedelta

from dotenv import load_dotenv
import edgar

# Resolve .env in the same directory as this file
_env_path = Path(__file__).resolve().parent / ".env"
if not _env_path.exists():
    raise FileNotFoundError(f".env file not found at {_env_path}")
load_dotenv(dotenv_path=_env_path)
SEC_USER = os.getenv("SEC_USER")
if not SEC_USER:
    raise ValueError("SEC_USER not set in .env file")
edgar.set_identity(SEC_USER)

BACKTEST_START = os.getenv("BACKTEST_START", "2020-01-01")
BACKTEST_END = os.getenv("BACKTEST_END", "2026-08-01")

# Warm-up history (trading days) required before BACKTEST_START so that
# rolling indicators (beta, ATR) have enough prior bars at the very start
# of the backtest window.
BACKTEST_WARMUP_DAYS = int(os.getenv("BACKTEST_WARMUP_DAYS", "252"))


def _parse_env_date(s: str) -> date:
    return date.fromisoformat(s)


BACKTEST_START_DATE = _parse_env_date(BACKTEST_START)
BACKTEST_END_DATE = _parse_env_date(BACKTEST_END)

# Optional floor for how far back to fetch historical data. When unset, the
# scripts default to the earliest S&P 500 membership start_date in the CSV
# (fallback 1996-01-02). Forces retention of pre-backtest-window constituents
# so a longer backtest can run later without re-downloading. Raise via env if
# the embedded module gets too large — still long history, just not full 1996.
BACKTEST_HISTORY_START = os.getenv("BACKTEST_HISTORY_START")


def _compute_history_start() -> date:
    """Earliest date to fetch bars from.

    = BACKTEST_HISTORY_START env (if set) else the earliest membership
    start_date found in sp500_ticker_start_end.csv, else 1996-01-02.
    """
    if BACKTEST_HISTORY_START:
        return _parse_env_date(BACKTEST_HISTORY_START)
    csv_path = (
        Path(__file__).resolve().parent.parent
        / "lean_project" / "data" / "sp500_ticker_start_end.csv"
    )
    earliest = None
    if csv_path.exists():
        try:
            import csv as _csv
            with open(csv_path, newline="", encoding="utf-8") as _f:
                for _row in _csv.DictReader(_f):
                    _s = (_row.get("start_date") or "").strip()
                    if not _s:
                        continue
                    try:
                        _d = date.fromisoformat(_s)
                    except ValueError:
                        continue
                    if earliest is None or _d < earliest:
                        earliest = _d
        except Exception:
            earliest = None
    return earliest if earliest else date(1996, 1, 2)


HISTORY_START_DATE = _compute_history_start()
HISTORY_START = HISTORY_START_DATE.isoformat()


def trading_days_before(start_date: date, n: int = BACKTEST_WARMUP_DAYS) -> date:
    """Step back day-by-day (skipping Sat/Sun) until ``n`` trading days are collected.

    Holidays are ignored (they only add extra buffer). Returns the date on
    which the ``n``-th prior trading day falls — i.e. the earliest date for
    which ``n`` trading days of subsequent history exist up to ``start_date``.
    """
    cur = start_date
    collected = 0
    while collected < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5:  # Mon-Fri
            collected += 1
    return cur


# Derived data window: guaranteed >= BACKTEST_WARMUP_DAYS trading days before
# BACKTEST_START (so warm-up history is available) and ending at BACKTEST_END.
DATA_START_DATE = trading_days_before(BACKTEST_START_DATE, BACKTEST_WARMUP_DAYS)
DATA_START = DATA_START_DATE.isoformat()
DATA_END = BACKTEST_END

# Optional Tiingo API key used by the missing-window recovery script
# (fetch_missing_delisted.py). When unset, the script skips the Tiingo
# fallback and degrades to the curated-rename + unavailable path.
TIINGO_API_KEY = os.getenv("TIINGO_API_KEY")
