"""Point-in-time fundamental + rolling-beta helpers.

Pure module (no Lean/AlgorithmImports import) so it is unit-testable
with plain pytest and reusable at buy-scan time inside Lean.
ISO YYYY-MM-DD strings compare correctly with ``<=``.

PIT fundamentals: ``fundamentals_history.json`` is keyed by ``filing_date``
(SEC acceptance date, when the report becomes public), NOT fiscal
``period``/``report_date``. Using the fiscal date would be ~30-45 days
look-ahead bias. ``fundamental_as_of`` picks the latest snapshot with
``filing_date <= as_of`` (inclusive). Snapshots retain ``period`` for
audit but it is never used for PIT joins.
"""

from __future__ import annotations

from typing import Optional

import numpy as np


def fundamental_as_of(hist: dict, ticker: str, date_str: str) -> Optional[dict]:
    """Latest PIT filing snapshot with filing_date <= date_str (inclusive).

    PIT key is ``filing_date`` (SEC ``filing_date``), not fiscal period end.
    A filing made ON the backtest date is considered available that day.
    Legacy period-keyed entries (outer key is fiscal period, no ``filed``/
    ``filing_date`` field) are **skipped** — they would introduce ~30-45d
    look-ahead bias. Run ``download_edgartools_data.py`` to regenerate history
    with filing-keyed data; this function returns ``None`` for legacy-only
    tickers until re-downloaded.
    """
    ticker_hist = hist.get(ticker, {})
    if not ticker_hist:
        return None
    # PIT date is filed/filing_date only; legacy period-keyed entries are rejected.
    candidates = []
    for key, snap in ticker_hist.items():
        pit_date = None
        if isinstance(snap, dict):
            pit_date = snap.get("filed") or snap.get("filing_date")
        if not pit_date:
            # Legacy period-keyed data is rejected; run download_edgartools_data.py to regenerate.
            continue
        # Normalize to ISO string (already is, but be defensive)
        pit_date = str(pit_date)[:10]
        if pit_date <= date_str:
            candidates.append((pit_date, key))
    if not candidates:
        return None
    # Pick latest PIT date; tie-break by key (deterministic)
    candidates.sort()
    _, best_key = candidates[-1]
    return ticker_hist[best_key]


def rolling_beta(
    stock_bars: dict,
    market_bars: dict,
    as_of: str,
    window: int = 252,
    min_points: int = 30,
) -> Optional[tuple]:
    """Return (beta, alpha) from trailing ``window`` daily returns up to ``as_of``.

    Aligns stock and market on common trading dates <= as_of, takes the last
    ``window`` aligned days, regresses stock returns on market returns.
    Returns None if fewer than ``min_points`` aligned points are available.
    """
    dates = sorted(d for d in stock_bars if d in market_bars and d <= as_of)
    if len(dates) < min_points:
        return None
    dates = dates[-(window + 1):]
    s = np.array([stock_bars[d]["close"] for d in dates], dtype=float)
    m = np.array([market_bars[d]["close"] for d in dates], dtype=float)
    # Guard against zero/NaN closes (would produce inf/nan returns)
    if not np.all(np.isfinite(s)) or not np.all(np.isfinite(m)):
        return None
    if np.any(s <= 0) or np.any(m <= 0):
        return None
    sr = np.diff(s) / s[:-1]
    mr = np.diff(m) / m[:-1]
    if len(sr) < 2:
        return None
    if not np.all(np.isfinite(sr)) or not np.all(np.isfinite(mr)):
        return None
    # Flat market (zero variance) makes polyfit singular — use variance threshold
    # rather than exact equality (floating noise e.g. 0.001 vs 0.0010000001)
    if np.var(mr) < 1e-12:
        return None
    try:
        beta, alpha = np.polyfit(mr, sr, 1)  # beta = slope, alpha = intercept (daily)
    except (np.linalg.LinAlgError, ValueError):
        return None
    if not np.isfinite(beta) or not np.isfinite(alpha):
        return None
    return float(beta), float(alpha)


def erp_as_of(history: dict, date_str: str) -> Optional[dict]:
    """Latest ERP entry with date key < date_str (strictly past).

    Uses strict less-than to guarantee no look-ahead: the ERP published
    ON the backtest date must not be used until the next day.
    """
    hist = history.get("erp_history", {}) if history else {}
    ds = [d for d in hist if d < date_str]
    return hist[max(ds)] if ds else None


def earliest_erp(history: dict) -> Optional[dict]:
    """Return the oldest ERP entry.

    NOTE: This is look-ahead for dates before the series start — it returns a
    future ERP.  Callers that require strict PIT should NOT use this as a
    fallback; prefer ``histimpl_erp_as_of`` or return None.
    Returns None if the history is empty or absent.
    """
    hist = history.get("erp_history", {}) if history else {}
    if not hist:
        return None
    return hist[min(hist)]


def histimpl_erp_as_of(histimpl_hist: dict, date_str: str) -> Optional[dict]:
    """Latest US implied-ERP (histimpl) entry with date key < date_str (strict).

    Annual values are keyed YYYY-01-01, so strict less-than returns year Y's
    value for any as_of in (Y-01-01, Y+1-01-01) — the "published January, used
    whole year" convention.  Returns a normalized entry with ``source`` tag.
    """
    hist = histimpl_hist.get("us_erp_history", {}) if histimpl_hist else {}
    ds = [d for d in hist if d < date_str]
    if not ds:
        return None
    us_erp = hist[max(ds)]
    return {
        "us_erp": us_erp,
        "mature_market_erp": us_erp,  # US is the mature-market proxy
        "source": "histimpl",
    }


def resolve_erp_as_of(
    spreadsheet_hist: dict,
    histimpl_hist: dict,
    date_str: str,
) -> Optional[dict]:
    """Resolve the ERP entry for ``date_str``, preferring spreadsheet PIT data.

    Strict PIT: only ``erp_as_of`` (< date_str) is used from the spreadsheet.
    ``earliest_erp`` is deliberately NOT used as a fallback — it would be
    look-ahead for dates before the series start.  Falls back to
    ``histimpl_erp_as_of`` only when the spreadsheet entry is missing or has
    no usable ``us_erp``.  Always returns an entry with a ``source`` tag
    (``"pit"`` or ``"histimpl-fallback"``) so callers can log the effective source.
    """
    pit = erp_as_of(spreadsheet_hist, date_str)
    if pit is not None and isinstance(pit.get("us_erp"), (int, float)):
        # Exclude bool (subclass of int) which would be 0/1 ERP
        if isinstance(pit["us_erp"], bool):
            pit = None
        else:
            return {"us_erp": pit["us_erp"],
                    "mature_market_erp": pit.get("mature_market_erp"),
                    "source": "pit"}

    hi = histimpl_erp_as_of(histimpl_hist, date_str)
    if hi is not None:
        hi = dict(hi)
        hi["source"] = "histimpl-fallback"
        return hi

    # Spreadsheet entry exists but lacks us_erp; still expose it if present.
    if pit is not None:
        return {"us_erp": pit.get("us_erp"),
                "mature_market_erp": pit.get("mature_market_erp"),
                "source": "pit"}
    return None


def resolve_risk_free_rate(tn_bars: dict, as_of: str) -> Optional[float]:
    """Point-in-time risk-free rate (decimal) from ``^TNX`` bars as-of ``as_of``.

    Returns the latest bar close that is ``<= as_of``, divided by 100 exactly
    once (Yahoo quotes the 10-yr yield index in yield*10). PIT: returns None
    when no bar exists at/before ``as_of`` (no look-ahead, no invented yield).
    Callers must check for None and skip the screen/log an error. Finite and
    positive checks are applied; invalid closes also yield None.
    """
    if tn_bars:
        ds = [d for d in tn_bars if d <= as_of]
        if ds:
            try:
                close = float(tn_bars[max(ds)]["close"])
            except (TypeError, ValueError):
                return None
            # Guard NaN/inf: float('nan') > 0 is False but explicit is clearer
            import math
            if math.isfinite(close) and close > 0:
                return close / 100.0
            return None
    return None
