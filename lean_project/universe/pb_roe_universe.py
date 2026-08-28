"""Fine fundamental universe selection for P/B vs ROE strategy.

Uses local fundamental data from data/fundamentals_history.json (produced by
scripts/download_edgartools_data.py) instead of QC's paid Morningstar
FineFundamental feed.

Screening pipeline:
1. Exclude financials (by sector keyword matching)
2. Require valid P/B, ROE, Beta, EPS from local cache
3. Compute intrinsic P/B via 2-stage Gordon Growth
4. Rank by gap_pct = (implied - actual) / actual
5. Return top N undervalued symbols
"""

from __future__ import annotations

from typing import Optional

from AlgorithmImports import *
from valuation.gordon_growth import intrinsic_pb_2stage

# Core required data modules. A missing embed would otherwise fail later with a
# raw ModuleNotFoundError deep inside the backtest. Re-raise an actionable error
# instead of falling back to {} (a silent empty cache would empty the whole
# portfolio with no diagnostic).
try:
    from data.equity_bars import load_equity_bars as load_bars_cache_from_data
except ImportError as _e:
    raise RuntimeError(
        "data module 'equity_bars' is missing — run "
        "lean_project/scripts/embed_data.py before backtesting"
    ) from _e
from universe.pit_data import (
    fundamental_as_of,
    rolling_beta,
    erp_as_of,
    resolve_erp_as_of,
    resolve_risk_free_rate,
)

# Load PIT ERP history if available (embedded damodaran_erp_history.py)
def _load_erp_history_fallback() -> dict:
    return {}

try:
    from data.damodaran_erp_history import load_damodaran_erp_history as _load_erp_history
except ImportError:
    _load_erp_history = _load_erp_history_fallback

# Fallback US implied-ERP history (annual, from histimpl.html)
def _load_histimpl_erp_fallback() -> dict:
    return {}

try:
    from data.damodaran_erp_history_us import load_damodaran_erp_history_us as _load_histimpl_erp
except ImportError:
    _load_histimpl_erp = _load_histimpl_erp_fallback


# Precise, yfinance-independent financial classification (uses edgar's native
# classification stored in each snapshot). No keyword/string fallback: a ticker
# that cannot be grouped by these exact standards is treated as non-financial
# rather than mis-classified, which would otherwise surface as a bogus
# Gordon-growth P/B valuation downstream.
FINANCIAL_CATEGORIES = {
    "Bank",
    "Insurance Company",
    "BDC",
    "Investment Manager",
    "REIT",
}
# SIC ranges: banks 6021-6036, insurance 6311-6371, investment managers 6211/6282,
# real estate (incl. REIT 6798) 6500-6799.
FINANCIAL_SIC_BANK = range(6021, 6037)
FINANCIAL_SIC_INSURANCE = range(6311, 6372)
FINANCIAL_SIC_INVESTMENT_MANAGER = {6211, 6282}
FINANCIAL_SIC_REAL_ESTATE = range(6500, 6800)


def is_financial(
    sic: Optional[int] = None,
    business_category: Optional[str] = None,
) -> bool:
    """Check if a ticker is a financial firm.

    Classification uses edgar's native signals stored in each snapshot:
    ``business_category`` and ``sic``.  There is deliberately no sector/
    industry keyword fallback — if a company cannot be classified by these
    exact standards it is left as non-financial rather than risked into a
    bogus valuation.  ``sic`` is coerced to int to handle legacy string
    storage (e.g. "6021").
    """
    if business_category is not None:
        bc = str(business_category).strip()
        if bc in FINANCIAL_CATEGORIES:
            return True
    if sic is not None:
        try:
            sic_int = int(float(str(sic).strip())) if isinstance(sic, str) else int(sic)
        except (ValueError, TypeError):
            return False
        if (
            sic_int in FINANCIAL_SIC_BANK
            or sic_int in FINANCIAL_SIC_INSURANCE
            or sic_int in FINANCIAL_SIC_INVESTMENT_MANAGER
            or sic_int in FINANCIAL_SIC_REAL_ESTATE
        ):
            return True
    return False


def get_erp(erp_cache: dict, country: str = "United States") -> Optional[float]:
    """Get ERP for a country from Damodaran cache.

    Returns None if no ERP is available (instead of silent 0.055 fallback).
    A missing ERP is a data failure and must be surfaced as an error.
    """
    us = erp_cache.get("us_erp")
    if isinstance(us, (int, float)) and not isinstance(us, bool):
        fv = float(us)
        # ERP == 0 is a Damodaran fetch failure — treat as missing
        if fv == 0:
            return None
        return fv
    if country != "United States":
        countries = erp_cache.get("countries", {})
        cd = countries.get(country, {})
        for field in ("total_equity_risk_premium2", "Total Equity Risk Premium 2",
                      "total_equity_risk_premium", "Total Equity Risk Premium",
                      "TotalEquityRiskPremium", "ERP", "erp"):
            val = cd.get(field)
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                fv = float(val)
                if fv == 0:
                    return None
                return fv
    mature = erp_cache.get("mature_market_erp")
    if isinstance(mature, (int, float)) and not isinstance(mature, bool):
        fv = float(mature)
        if fv == 0:
            return None
        return fv
    return None


def _record_missing_g_eps(algorithm, ticker: str, as_of: str) -> None:
    """Track the as_of span over which a ticker lacked ``g_eps``.

    The screen skips these names every rebalance, which would otherwise emit
    one WARN line per ticker per day.  We instead record the first/last
    ``as_of`` date here and emit a single consolidated summary at the end of
    the backtest (see :func:`log_missing_g_eps_summary`).
    """
    spans = getattr(algorithm, "_missing_g_eps_spans", None)
    if spans is None:
        spans = {}
        algorithm._missing_g_eps_spans = spans
    rec = spans.get(ticker)
    if rec is None:
        spans[ticker] = [as_of, as_of]
    else:
        rec[1] = as_of


def log_missing_g_eps_summary(algorithm) -> None:
    """Emit ONE consolidated warning per ticker instead of per-rebalance noise.

    Reports the as_of span during which each ticker had no 2y TTM-EPS history
    (``g_eps`` was ``None``) and was therefore skipped by the screen.
    """
    spans = getattr(algorithm, "_missing_g_eps_spans", None)
    if not spans:
        return
    algorithm.Log("=" * 60)
    algorithm.Log(
        f"EPS GROWTH COVERAGE: {len(spans)} ticker(s) lacked g_eps "
        f"(2y TTM-EPS history) during the backtest and were skipped:"
    )
    for ticker in sorted(spans):
        first, last = spans[ticker]
        algorithm.Log(
            f"  WARN {ticker}: missing EPS growth (need 2y TTM-EPS history) "
            f"during {first} to {last}"
        )
    algorithm.Log("=" * 60)


def run_fine_selection(
    algorithm: QCAlgorithm,
    tickers: list,
    max_positions: int = 10,
    bars_cache: Optional[dict] = None,
    history_cache: Optional[dict] = None,
    market_bars: Optional[dict] = None,
    erp_history_cache: Optional[dict] = None,
) -> list:
    """Screen tickers using PIT quarterly fundamentals data.

    Requires ``history_cache`` and ``market_bars`` — errors and skips
    if either is missing. No static snapshot fallback.

    The ERP and risk-free rate are always looked up as-of the current
    backtest date (PIT, no look-ahead) whenever ``erp_history_cache``
    and/or ``bars_cache`` are available.
    """
    if bars_cache is None:
        bars_cache = load_bars_cache_from_data()
    if erp_history_cache is None:
        erp_history_cache = _load_erp_history()
    histimpl_cache = _load_histimpl_erp()

    as_of = algorithm.Time.strftime("%Y-%m-%d")

    # PIT risk-free rate: 10-yr yield as-of the backtest date (PIT, no static fallback)
    tn_bars = bars_cache.get("^TNX", {})
    rf = resolve_risk_free_rate(tn_bars, as_of)
    if rf is None:
        algorithm.Log(
            f"ERROR: No PIT risk-free rate available as_of={as_of} "
            f"(no ^TNX bar <= as_of — refusing invented 0.042). Skipping screen for this date."
        )
        return []

    # PIT ERP is the sole, authoritative source of truth. There is NO fallback
    # to a current/latest snapshot (that would be look-ahead bias). If neither
    # the PIT spreadsheet series nor the histimpl series yields an entry, we
    # refuse (empty screen) instead of pricing with a future ERP.
    entry = None
    if erp_history_cache or histimpl_cache:
        entry = resolve_erp_as_of(erp_history_cache, histimpl_cache, as_of)
    if entry is None:
        algorithm.Log(
            f"ERROR: No PIT ERP available as_of={as_of} "
            f"(PIT history and histimpl both empty) — refusing look-ahead "
            f"snapshot. Skipping screen for this date."
        )
        return []
    erp = get_erp(entry, "United States")
    if erp is None or erp == 0:
        algorithm.Log(
            f"ERROR: Damodaran ERP missing/zero as_of={as_of} entry={entry} "
            f"(source={entry.get('source')}) — Damodaran fetch failure. Skipping screen."
        )
        return []
    effective_source = entry.get("source", "pit")
    algorithm.Log(
        f"DIAG ERP PIT as_of={as_of} erp={erp:.4f} (source={effective_source})"
    )

    # PIT fundamentals require history_cache and market_bars
    if not history_cache or not market_bars:
        algorithm.Log("ERROR: No PIT history or market bars — cannot screen")
        return []

    scored = []
    for ticker in tickers:
        try:
            # Skip tickers with no price data
            ticker_bars = bars_cache.get(ticker, {})
            if not ticker_bars:
                continue

            # PIT fundamentals
            snap = fundamental_as_of(history_cache, ticker, as_of)
            if snap is None:
                continue

            book_value = snap.get("book_value")
            roe = snap.get("roe")
            eps = snap.get("eps")

            # Skip financials (native edgar classification only)
            sic = snap.get("sic")
            business_category = snap.get("business_category")
            if is_financial(sic=sic, business_category=business_category):
                continue

            beta_res = rolling_beta(ticker_bars, market_bars, as_of)
            if beta_res is None:
                continue
            beta, _alpha = beta_res

            if not all(v is not None and v > 0 for v in [roe, beta, eps]):
                continue
            if book_value is None or book_value <= 0:
                continue

            # PIT price: use the bar on the ACTUAL backtest date (absolute),
            # not the latest *available* bar. A name with no bar on as_of is
            # not tradeable that day (delisted, or a throttled/dropped equity
            # download such as FCN last traded 1998, FMCC 2008) and must be
            # skipped; falling back to the latest available bar would mis-rank
            # it on a stale price and starve tradeable names of screen slots.
            bar = ticker_bars.get(as_of)
            if not bar:
                continue
            import math as _math
            try:
                current_price = float(bar.get("close", 0))
            except (TypeError, ValueError):
                continue
            if not _math.isfinite(current_price) or current_price <= 0:
                continue

            pb = current_price / book_value

            # Growth: PIT EPS CAGR — no fallback, error + skip if unavailable
            g_start = snap.get("g_eps")
            if g_start is None or not isinstance(g_start, (int, float)) or not _math.isfinite(float(g_start)):
                # Record the span and emit one consolidated warning at the end
                # of the backtest instead of spamming a line every rebalance.
                _record_missing_g_eps(algorithm, ticker, as_of)
                continue

            g_term = rf
            r = rf + beta * erp
            try:
                implied_pb = intrinsic_pb_2stage(roe, g_start, g_term, r)
            except (ValueError, ZeroDivisionError):
                continue
            if not _math.isfinite(implied_pb) or implied_pb <= 0:
                continue

            gap_pct = (implied_pb - pb) / pb
            if gap_pct > 0:
                scored.append((ticker, gap_pct, pb, roe, implied_pb))
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:max_positions]]
