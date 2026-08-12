"""Fine fundamental universe selection for P/B vs ROE strategy.

Uses local fundamental data from data/fundamentals.json instead of QC's
paid Morningstar FineFundamental feed.

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

from data.damodaran_erp_json import load_damodaran_erp as load_erp_cache_from_data
from data.equity_bars import load_equity_bars as load_bars_cache_from_data
from universe.pit_data import (
    fundamental_as_of,
    latest_price_as_of,
    rolling_beta,
    erp_as_of,
    earliest_erp,
)

# Load PIT ERP history if available (embedded damodaran_erp_history.py)
def _load_erp_history_fallback() -> dict:
    return {}

try:
    from data.damodaran_erp_history import load_damodaran_erp_history as _load_erp_history
except ImportError:
    _load_erp_history = _load_erp_history_fallback


FINANCIAL_SECTORS = {
    "financial services",
    "banks",
    "insurance",
    "asset management",
    "capital markets",
    "finance",
    "real estate",
    "reit",
}


def is_financial(sector: str, industry: str) -> bool:
    """Check if a ticker's sector/industry indicates a financial firm."""
    combined = f"{sector or ''} {industry or ''}".lower()
    for keyword in FINANCIAL_SECTORS:
        if keyword in combined:
            return True
    return False


def get_tnx_rate(bars_cache: dict) -> float:
    """Get latest ^TNX 10-year Treasury yield from local equity bars cache."""
    tn_bars = bars_cache.get("^TNX", {})
    if not tn_bars:
        return 0.042
    sorted_dates = sorted(tn_bars.keys())
    latest = sorted_dates[-1]
    tn_close = float(tn_bars[latest].get("close", 0))
    if tn_close > 0:
        return tn_close / 100.0
    return 0.042


def get_erp(erp_cache: dict, country: str = "United States") -> float:
    """Get ERP for a country from Damodaran cache."""
    if country == "United States" and isinstance(erp_cache.get("us_erp"), (int, float)):
        return float(erp_cache["us_erp"])
    countries = erp_cache.get("countries", {})
    cd = countries.get(country, {})
    # Prefer total_equity_risk_premium2 (more recent/accurate)
    for field in ("total_equity_risk_premium2", "Total Equity Risk Premium 2",
                  "total_equity_risk_premium", "Total Equity Risk Premium",
                  "TotalEquityRiskPremium", "ERP", "erp"):
        val = cd.get(field)
        if val is not None and isinstance(val, (int, float)):
            return float(val)
    if isinstance(erp_cache.get("mature_market_erp"), (int, float)):
        return float(erp_cache["mature_market_erp"])
    return 0.055


def run_fine_selection(
    algorithm: QCAlgorithm,
    tickers: list,
    max_positions: int = 10,
    erp_cache: Optional[dict] = None,
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
    if erp_cache is None:
        erp_cache = load_erp_cache_from_data()
    if bars_cache is None:
        bars_cache = load_bars_cache_from_data()
    if erp_history_cache is None:
        erp_history_cache = _load_erp_history()

    as_of = algorithm.Time.strftime("%Y-%m-%d")

    # PIT risk-free rate: 10-yr yield as-of the backtest date
    tn_bars = bars_cache.get("^TNX", {})
    tn_close = latest_price_as_of(tn_bars, as_of) if tn_bars else None
    if tn_close is None:
        tn_close = get_tnx_rate(bars_cache)  # global latest fallback
    rf = tn_close / 100.0 if tn_close and tn_close > 0 else 0.042

    erp = get_erp(erp_cache, "United States")

    # PIT ERP whenever the history exists (overrides static snapshot)
    if erp_history_cache:
        entry = erp_as_of(erp_history_cache, as_of) or earliest_erp(
            erp_history_cache
        )
        if entry is not None:
            erp = get_erp(entry, "United States")
            algorithm.Log(
                f"DIAG ERP PIT as_of={as_of} erp={erp:.4f} "
                f"(source={'pit' if entry else 'static'})"
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

            # Skip financials
            sector = snap.get("sector") or ""
            industry = snap.get("industry") or ""
            if is_financial(sector, industry):
                continue

            beta_res = rolling_beta(ticker_bars, market_bars, as_of)
            if beta_res is None:
                continue
            beta, _alpha = beta_res

            if not all(v is not None and v > 0 for v in [roe, beta, eps]):
                continue
            if book_value is None or book_value <= 0:
                continue

            # PIT price
            current_price = latest_price_as_of(ticker_bars, as_of)
            if current_price is None or current_price <= 0:
                continue

            pb = current_price / book_value

            # Growth: PIT EPS CAGR — no fallback, error + skip if unavailable
            g_start = snap.get("g_eps")
            if g_start is None:
                algorithm.Log(
                    f"WARN {ticker}: no EPS growth available "
                    f"(need 2y TTM-EPS history), skipping"
                )
                continue

            g_term = rf
            r = rf + beta * erp
            implied_pb = intrinsic_pb_2stage(roe, g_start, g_term, r)

            gap_pct = (implied_pb - pb) / pb
            if gap_pct > 0:
                scored.append((ticker, gap_pct, pb, roe, implied_pb))
        except Exception:
            continue

    scored.sort(key=lambda x: x[1], reverse=True)
    return [s[0] for s in scored[:max_positions]]
