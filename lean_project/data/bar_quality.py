"""Pure equity-bar quality checks (no yfinance / Lean imports).

Used by both the download pipeline and the embed validation so the SAME
rules decide (a) whether a freshly-fetched ticker is fit to store and
(b) whether the embedded bars contain garbage.

Two classes of defect are caught:

* **Float-epsilon false positives** — OHLC values that differ by ~1e-17 due
  to float rounding must NOT be flagged as malformed. All containment checks
  use a small absolute tolerance (``EPS``).
* **Genuinely broken rows** — ``high < low``, open/close outside
  ``[low, high]`` (beyond epsilon), non-positive prices, and extreme
  single-day moves that usually mean a split/dividend was NOT adjusted or
  yfinance returned the wrong instrument.
"""

from __future__ import annotations

from typing import Optional

# Absolute tolerance for OHLC containment. Prices here are > 0 (S&P names are
# never penny stocks), so a flat 1e-6 covers float-rounding noise (observed
# gaps ~1e-17) without masking real violations.
EPS = 1e-6


def _bad_row_reason(o, h, l, c) -> Optional[str]:
    """Return a short reason if the OHLC row is malformed, else None."""
    try:
        o = float(o)
        h = float(h)
        l = float(l)
        c = float(c)
    except (TypeError, ValueError):
        return "nan"
    if c <= 0 or o <= 0 or h <= 0 or l <= 0:
        return "nonpositive"
    if h + EPS < l:
        return "h<l"
    if not (l - EPS <= o <= h + EPS):
        return "o_out"
    if not (l - EPS <= c <= h + EPS):
        return "c_out"
    return None


def validate_bar_quality(bars: dict, max_daily_ret: float = 0.6) -> dict:
    """Scan embedded bars for anomalies (WARN-only). See module docstring.

    Spinoff-parent exit days are excluded from the extreme-move check (their
    return legitimately gaps on the ex-date; the strategy sells before it).
    """
    from datetime import date as _d, timedelta as _td
    from data.corporate_actions import SPINOFFS, last_trading_day

    exit_days: dict[str, set] = {}
    for ex_date, _added, parent in SPINOFFS:
        ed = _d.fromisoformat(ex_date)
        exit_days.setdefault(parent, set()).add(
            last_trading_day(ed - _td(days=1)).isoformat()
        )

    summary = {
        "tickers": len(bars),
        "bad_rows": 0,
        "extreme_moves": [],
        "bad_fields": [],
        "nonpositive": [],
    }
    for ticker, tb in bars.items():
        if not tb:
            continue
        prev_close = None
        for dt, row in tb.items():
            reason = _bad_row_reason(
                row.get("open"), row.get("high"), row.get("low"), row.get("close")
            )
            if reason:
                summary["bad_rows"] += 1
                if reason == "nonpositive" and len(summary["nonpositive"]) < 10:
                    summary["nonpositive"].append(f"{ticker}:{dt}")
                elif len(summary["bad_fields"]) < 10:
                    summary["bad_fields"].append(f"{ticker}:{dt}:{reason}")
            try:
                c = float(row.get("close", 0))
            except (TypeError, ValueError):
                c = 0
            if prev_close not in (None, 0) and c not in (None, 0):
                ret = (c - prev_close) / prev_close
                if abs(ret) > max_daily_ret and dt not in exit_days.get(ticker, set()):
                    summary["extreme_moves"].append((ticker, dt, round(ret, 3)))
            prev_close = c
    summary["extreme_moves"] = summary["extreme_moves"][:20]
    return summary


def ticker_quality_verdict(
    ticker_bars: dict,
    max_bad_frac: float = 0.05,
    min_rows: int = 20,
) -> tuple[bool, str]:
    """Decide whether a single ticker's bars are fit to store.

    Returns ``(is_bad, reason)``. A ticker is rejected when a large fraction of
    its rows are malformed, when it is entirely zero-priced, or when an
    implausible share of its days show extreme (>60%) moves (almost always a
    mis-resolved yfinance instrument / unadjusted split, not a real S&P name).
    """
    if not ticker_bars:
        return True, "empty"
    rows = list(ticker_bars.values())
    n = len(rows)
    bad = 0
    zero = 0
    extreme = 0
    prev_close = None
    for row in rows:
        if _bad_row_reason(
            row.get("open"), row.get("high"), row.get("low"), row.get("close")
        ):
            bad += 1
        try:
            c = float(row.get("close", 0))
        except (TypeError, ValueError):
            c = 0
        if c == 0:
            zero += 1
        if prev_close not in (None, 0) and c not in (None, 0):
            if abs((c - prev_close) / prev_close) > 0.6:
                extreme += 1
        prev_close = c
    if n >= min_rows and bad / n > max_bad_frac:
        return True, f"malformed_rows {bad}/{n}"
    if n > 0 and zero == n:
        return True, "all_zero_prices"
    if n >= min_rows and extreme / n > 0.05:
        return True, f"extreme_moves {extreme}/{n}"
    return False, ""
