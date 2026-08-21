"""Repair pass: recover missing equity bars for tickers the batch download
dropped (mostly yfinance rate-limit masks / 404s, plus symbol-convention
mismatches like BRK-B vs BRK.B).

Retries each missing ticker with ``yf.Ticker(symbol).history(...)`` across a set
of symbol variants, with persistent growing-backoff on rate-limiting, then merges
recovered bars back into equity_bars.json. Classification is precise:

- ``recovered``            — data returned and merged
- ``resolved_via_alias``   — ticker has no data but its near-day successor does
                             (company is covered; diagnostics only)
- ``throttled (PENDING)``  — rate-limited / masked-empty for a *current* member;
                             remains to be re-run later
- ``unavailable``          — genuinely delisted / never traded; expected

Usage:
    python scripts/repair_equity_data.py
"""

from __future__ import annotations

import csv
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import yfinance as yf

# Add repo root to path so `import config` works
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import config  # loads .env and sets edgar identity

# Add lean_project to path so `from data.sp500_data import ...` works
_LEAN_PROJECT = Path(__file__).resolve().parent.parent
if str(_LEAN_PROJECT) not in sys.path:
    sys.path.insert(0, str(_LEAN_PROJECT))
from data.sp500_data import (
    load_sp500_membership,
    clip_to_membership,
    build_alias_map,
)

_DATA_DIR = _LEAN_PROJECT / "data"
_BARS_PATH = _DATA_DIR / "equity_bars.json"


def _variants(ticker: str) -> list:
    """Candidate yfinance symbols for a given CSV ticker."""
    out = []
    for sym in {ticker, ticker.replace("-", "."), ticker.replace(".", "-")}:
        if sym not in out:
            out.append(sym)
    if "." in ticker:
        base = ticker.split(".", 1)[0]
        for sym in {base, base + "-", base + ".", base.replace(".", "-")}:
            if sym not in out:
                out.append(sym)
    return out


def _row_to_bar(row) -> dict:
    return {
        "open": float(row.get("Open", 0)),
        "high": float(row.get("High", 0)),
        "low": float(row.get("Low", 0)),
        "close": float(row.get("Close", 0)),
        "volume": float(row.get("Volume", 0)),
    }


def _rate_limited(exc: Exception) -> bool:
    s = str(exc).lower()
    return "too many requests" in s or "rate limit" in s or "429" in s


def _parse_date(s: str) -> date:
    return date.fromisoformat(s)


def _is_current_member(membership: dict, ticker: str, win_start: date) -> bool:
    """True if ticker is a current member or was active after the backtest start."""
    for _s, e in membership.get(ticker, []):
        if e is None or _parse_date(e) > win_start:
            return True
    return False


def _fetch_with_variants(ticker: str, start: str, end: str):
    """Try every symbol variant; return (bars_dict_or_None, error_or_None).

    Uses ``auto_adjust=True`` (adjusted) to match the batch download — a single
    adjustment mode across the whole equity_bars.json. Returns the first
    non-empty result; captures the last exception (if any) for diagnostics.
    """
    err = None
    for sym in _variants(ticker):
        try:
            tk = yf.Ticker(sym)
            hist = tk.history(
                start=start,
                end=end,
                auto_adjust=True,
                actions=False,
                raise_errors=False,
            )
            if hist is not None and not hist.empty:
                return (
                    {d.strftime("%Y-%m-%d"): _row_to_bar(row) for d, row in hist.iterrows()},
                    None,
                )
        except Exception as e:  # noqa: BLE001 - we reclassify below
            err = e
    return None, err


def run_repair(
    bars: dict,
    membership: dict,
    requested: list,
    history_start: str,
    backtest_end: str,
    win_start: date,
    backoffs: list = None,
    max_passes: int = None,
    pacing: float = 1.0,
) -> tuple:
    """Recover missing tickers and return ``(bars, report)``.

    ``bars`` is mutated in place and clipped to membership before returning.
    ``report`` is a dict with keys ``recovered``, ``unavailable``, ``pending``
    (ticker -> raw error/message), and ``resolved_alias`` (list of (pred, succ)).
    ``backoffs``/``max_passes``/``pacing`` are overridable for testing.
    """
    if backoffs is None:
        backoffs = [5, 15, 30, 60, 120, 300]
    if max_passes is None:
        max_passes = len(backoffs) + 1

    missing = [t for t in requested if t not in bars]
    print(f"Requested {len(requested)}, present {len(bars)}, missing {len(missing)}")

    recovered: list[str] = []
    unavailable: list[str] = []
    pending: dict[str, str] = {}  # ticker -> raw error / masked message

    remaining = list(missing)

    for pass_idx in range(max_passes):
        if not remaining:
            break
        total = len(remaining)
        next_remaining: list[str] = []
        for i, ticker in enumerate(remaining, 1):
            if i % 25 == 0:
                print(f"  Repair pass {pass_idx + 1}: {i}/{total} (recovered so far {len(recovered)})...")
            got, err = _fetch_with_variants(ticker, history_start, backtest_end)
            if got:
                bars[ticker] = got
                recovered.append(ticker)
                continue
            # Classify the failure
            if err is not None and _rate_limited(err):
                pending[ticker] = f"rate-limit: {err}"
                next_remaining.append(ticker)
            elif _is_current_member(membership, ticker, win_start):
                # Yahoo masks throttling as empty for live stocks
                pending[ticker] = (str(err) if err else "empty (throttling-masked)")
                next_remaining.append(ticker)
            else:
                unavailable.append(ticker)
            if pacing:
                time.sleep(pacing)  # gentle pacing to avoid rate limits
        remaining = next_remaining
        if remaining and pass_idx < len(backoffs):
            print(f"  {len(remaining)} still throttled; backing off {backoffs[pass_idx]}s...")
            time.sleep(backoffs[pass_idx])

    # --- Alias diagnostics: a predecessor covered by a near-day successor is
    #     not a real gap (company still represented). Move it out of the
    #     pending/unavailable buckets so we don't false-alarm. ---
    aliases = build_alias_map(membership)
    resolved_alias: list[tuple[str, str]] = []

    def _covered(t: str) -> bool:
        succ = aliases.get(t)
        return bool(succ) and succ in bars and bool(bars[succ])

    for t in list(pending.keys()):
        if _covered(t):
            resolved_alias.append((t, aliases[t]))
            del pending[t]
    for t in list(unavailable):
        if _covered(t):
            resolved_alias.append((t, aliases[t]))
            unavailable.remove(t)

    # Clip every ticker to the union of its membership intervals (consistent
    # with the batch download) before persisting.
    bars = clip_to_membership(bars, membership, backtest_end)

    report = {
        "recovered": recovered,
        "unavailable": unavailable,
        "pending": pending,
        "resolved_alias": resolved_alias,
    }
    return bars, report


def main() -> None:
    if _BARS_PATH.exists():
        bars = json.load(open(_BARS_PATH, encoding="utf-8"))
    else:
        bars = {}

    csvp = _DATA_DIR / "sp500_ticker_start_end.csv"
    membership = load_sp500_membership(str(csvp))
    requested = sorted(membership.keys()) + ["^TNX", "^GSPC"]

    HISTORY_START = config.HISTORY_START
    BACKTEST_END = config.BACKTEST_END
    # yfinance treats `end` as EXCLUSIVE, so pass BACKTEST_END + 1 day to the
    # fetch (otherwise recovered bars stop one day short of the window).
    BACKTEST_END_EXCLUSIVE = (
        _parse_date(BACKTEST_END) + timedelta(days=1)
    ).strftime("%Y-%m-%d")
    win_start = _parse_date(config.BACKTEST_START)

    bars, report = run_repair(
        bars,
        membership,
        requested,
        HISTORY_START,
        BACKTEST_END_EXCLUSIVE,
        win_start,
    )
    recovered = report["recovered"]
    unavailable = report["unavailable"]
    pending = report["pending"]
    resolved_alias = report["resolved_alias"]

    json.dump(bars, open(_BARS_PATH, "w", encoding="utf-8"), indent=2)
    print(
        f"Recovered {len(recovered)} tickers; "
        f"resolved-via-alias {len(resolved_alias)}; "
        f"throttled PENDING {len(pending)}; "
        f"genuinely unavailable {len(unavailable)}"
    )

    if resolved_alias:
        print("Resolved via alias (successor holds data, no action needed):")
        for t, s in resolved_alias:
            print(f"  {t} -> {s}")

    if pending:
        print("THROTTLED PENDING (re-run repair_equity_data.py later):")
        for t, msg in pending.items():
            flag = "CURRENT" if _is_current_member(membership, t, win_start) else "historical"
            print(f"  {t} [{flag}]: {msg[:200]}")
        print(
            "WARN: throttled list is non-empty — the embedded data is NOT complete. "
            "Re-run repair_equity_data.py after a cooldown; do NOT declare done."
        )

    if unavailable:
        in_window = [
            t
            for t in unavailable
            if any(e and _parse_date(e) >= win_start for _s, e in membership.get(t, []))
        ]
        print(f"Genuinely unavailable (any): {len(unavailable)}")
        print(f"  Of those, in-window membership end (REAL concern): {len(in_window)}")
        print("  In-window missing:", ", ".join(in_window))
        print("  All unavailable:", ", ".join(unavailable))


if __name__ == "__main__":
    main()
