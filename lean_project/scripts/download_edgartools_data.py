"""Download S&P 500 quarterly PIT fundamentals from edgartools (SEC filings).

Produces one output file:
  1. `fundamentals_history.json` — quarterly PIT history (plus ``g_eps``)

TTM rules (applied per quarter for history):
  - Income statement items (revenue, net_income): sum of 4 most recent
    quarters using only quarters available at that point (no look-ahead)
  - Balance sheet items (book_value/equity): latest quarter value
  - Shares: diluted first, fall back to basic (shares_outstanding)
  - ROE = net_income_ttm / equity
  - EPS = net_income_ttm / shares
  - P/B = price / book_value_per_share

Equity is total shareholders equity excluding minority interests:
  primary source is ``StockholdersEquity`` (parent equity);
  fallback is ``StockholdersEquityIncludingNoncontrollingInterest``
  minus ``MinorityInterest``.

Growth (``g_eps``): TTM-EPS CAGR over the last 2 years, computed from
EPS available up to each date (PIT, no look-ahead). No floor or cap.

Data sources:
  - edgartools: revenue_ttm, net_income_ttm, book_value (equity), shares
  - yfinance: sector, industry, market_cap, current price
  - computed: roe, pb, eps, book_value_per_share, g_eps

Usage:
    python scripts/download_edgartools_data.py
    python scripts/download_edgartools_data.py --tickers AAPL MSFT GOOG
    python scripts/download_edgartools_data.py --output-dir data
    python scripts/download_edgartools_data.py --backtest-start 2019-01-01
    # (defaults: --backtest-start=config.DATA_START, --backtest-end=config.BACKTEST_END)
"""

from __future__ import annotations

from typing import Optional

import argparse
import json
import sys
import time
from pathlib import Path
from datetime import datetime, timedelta

import yfinance as yf
from edgar import Company

# Add repo root to path so `import config` works (config/ is at repo root)
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Add lean_project to path so `from data.sp500_data import ...` works
_lean_project = Path(__file__).resolve().parent.parent
if str(_lean_project) not in sys.path:
    sys.path.insert(0, str(_lean_project))

import config  # loads .env and sets edgar identity
from data.sp500_data import load_sp500_membership, is_sp500_member

REQUEST_DELAY = 0.5


def get_sp500_tickers(refresh: bool = False) -> list[str]:
    """Return S&P 500 tickers from local sp500_ticker_start_end.csv."""
    csv_path = _lean_project / "data" / "sp500_ticker_start_end.csv"
    if refresh or not csv_path.exists():
        try:
            import urllib.request
            url = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"
            with urllib.request.urlopen(url, timeout=30) as resp:
                data = resp.read().decode("utf-8")
            csv_path.write_text(data, encoding="utf-8")
        except Exception:
            return []

    try:
        membership = load_sp500_membership(str(csv_path))
        return sorted(membership.keys())
    except Exception:
        return []


def normalize_ticker(ticker: str) -> Optional[str]:
    """Try ticker variants for edgartools compatibility.

    yfinance uses hyphens for class shares (BRK-B), SEC uses dots (BRK.B).
    """
    variants = [ticker, ticker.replace("-", "."), ticker.lower()]
    for var in variants:
        try:
            c = Company(var)
            if c and c.name:
                return var
        except Exception:
            continue
    return None


def get_yticker_info(ticker: str) -> dict:
    """Get sector, industry, name, market_cap, price from yfinance."""
    try:
        stock = yf.Ticker(ticker)
        info = stock.info
        return {
            "name": info.get("shortName") or info.get("longName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "market_cap": info.get("marketCap"),
            "price": info.get("currentPrice") or info.get("regularMarketPrice"),
        }
    except Exception:
        return {}


def get_parent_equity(tenq, fin):
    """Total shareholders equity excluding minority interests.

    Primary: US-GAAP ``StockholdersEquity`` (parent equity, ex-minority).
    Fallback: ``StockholdersEquityIncludingNoncontrollingInterest`` minus
    ``MinorityInterest`` when both are available.
    Returns ``None`` when neither path succeeds.
    """
    eq = fin.get_stockholders_equity()
    if eq is not None:
        return eq
    try:
        total = fin.get_stockholders_equity_including_noncontrolling_interest()
        minority = fin.get_minority_interest()
        if total is not None and minority is not None:
            return total - minority
    except Exception:
        pass
    return None


def compute_pit_eps_growth(sorted_pairs: list, years: int = 2) -> dict:
    """Compute point-in-time EPS CAGR for each period.

    ``sorted_pairs`` must be a list of ``(period_str, ttm_eps)`` sorted
    ascending by period.  For each period ``P`` the CAGR is computed
    against the latest entry with ``period <= P - years`` — strictly
    past, no look-ahead.  Negative or missing values return ``None``.
    No floor or cap is applied.
    """
    periods = [p for p, _ in sorted_pairs]
    eps_vals = [e for _, e in sorted_pairs]
    result = {}

    for i, (period, eps_now) in enumerate(sorted_pairs):
        if eps_now is None or eps_now <= 0:
            result[period] = None
            continue
        # Find latest ref with period <= P - years (strictly past)
        cutoff = datetime.strptime(period, "%Y-%m-%d").date() - timedelta(days=365 * years)
        ref_idx = None
        for j in range(i - 1, -1, -1):
            if datetime.strptime(periods[j], "%Y-%m-%d").date() <= cutoff:
                ref_idx = j
                break
        if ref_idx is None:
            result[period] = None
            continue
        eps_ref = eps_vals[ref_idx]
        if eps_ref is None or eps_ref <= 0:
            result[period] = None
            continue
        n_years = years
        cagr = (eps_now / eps_ref) ** (1.0 / n_years) - 1
        result[period] = cagr

    return result


def get_quarterly_history(
    company: Company,
    backtest_start: str,
    sector: str | None = None,
    industry: str | None = None,
) -> dict:
    """Get quarterly PIT financial history from 10-Q filings.

    For each quarter, computes TTM using only quarters available at that
    point (no look-ahead).  TTM EPS and EPS CAGR (``g_eps``) are also
    computed across the full filing series so the 2-year growth
    look-back can use pre-start quarters; those pre-start quarters are
    dropped from the output.

    Returns dict keyed by period string (YYYY-MM-DD).
    """
    try:
        filings = company.get_filings(form="10-Q", amendments=False)
        if filings.empty:
            return {}
    except Exception:
        return {}

    # Collect all filings with their financial data
    quarters = []
    for f in filings:
        try:
            tenq = f.obj()
            fin = tenq.financials

            rev = fin.get_revenue()
            ni = fin.get_net_income()
            eq = get_parent_equity(tenq, fin)
            shares_diluted = fin.get_shares_outstanding_diluted()
            shares_basic = fin.get_shares_outstanding_basic()
            shares = shares_diluted or shares_basic or company.shares_outstanding

            period = f.period_of_report
            if period is None:
                continue

            quarters.append({
                "period": period,
                "revenue": rev,
                "net_income": ni,
                "equity": eq,
                "shares": shares,
            })
        except Exception:
            continue

    if not quarters:
        return {}

    # Sort by period ascending
    quarters.sort(key=lambda q: q["period"])

    # Compute TTM for each quarter using only quarters available up to that point
    all_quarters = []  # (period_str, ttm_eps) for growth computation
    hist_dict = {}
    backtest_start_date = datetime.strptime(backtest_start, "%Y-%m-%d").date()
    for i, current in enumerate(quarters):
        # TTM = sum of current and previous 3 quarters (or fewer if not enough data)
        window = quarters[max(0, i - 3) : i + 1]

        rev_ttm = sum(q["revenue"] for q in window if q["revenue"] is not None) or None
        ni_ttm = sum(q["net_income"] for q in window if q["net_income"] is not None) or None
        equity = current["equity"]
        shares = current["shares"]

        if ni_ttm is None or equity is None:
            continue

        book_value_per_share = equity / shares if shares and shares > 0 else None
        roe = ni_ttm / equity if equity > 0 else None
        ttm_eps = ni_ttm / shares if shares and shares > 0 else None

        period_str = str(current["period"])
        all_quarters.append((period_str, ttm_eps))

        # Only include quarters >= backtest_start in output
        if datetime.strptime(period_str, "%Y-%m-%d").date() < backtest_start_date:
            continue

        hist_dict[period_str] = {
            "sector": sector,
            "industry": industry,
            "book_value": round(book_value_per_share, 6) if book_value_per_share else None,
            "roe": round(roe, 8) if roe else None,
            "eps": round(ttm_eps, 4) if ttm_eps else None,
            "revenue_ttm": round(rev_ttm) if rev_ttm else None,
            "net_income_ttm": round(ni_ttm) if ni_ttm else None,
        }

    # Compute PIT EPS growth for every output quarter
    g_eps_map = compute_pit_eps_growth(all_quarters, years=2)
    for period_str, g_eps in g_eps_map.items():
        if period_str in hist_dict:
            hist_dict[period_str]["g_eps"] = round(g_eps, 6) if g_eps is not None else None

    return hist_dict


def get_latest_ttm(company: Company) -> dict:
    """Get latest TTM financials from edgartools."""
    result = {}

    # TTM revenue and net_income from edgartools
    try:
        ttm_rev = company.get_ttm_revenue()
        if ttm_rev and ttm_rev.value is not None:
            result["revenue_ttm"] = ttm_rev.value
    except Exception:
        pass

    try:
        ttm_ni = company.get_ttm_net_income()
        if ttm_ni and ttm_ni.value is not None:
            result["net_income_ttm"] = ttm_ni.value
    except Exception:
        pass

    # Fallback: manual quarterly summation if TTM methods failed
    if "revenue_ttm" not in result or "net_income_ttm" not in result:
        try:
            filings = company.get_filings(form="10-Q", amendments=False)
            if not filings.empty:
                rev_sum = 0.0
                ni_sum = 0.0
                for f in filings.head(4):
                    try:
                        tenq = f.obj()
                        fin = tenq.financials
                        r = fin.get_revenue()
                        n = fin.get_net_income()
                        if r is not None:
                            rev_sum += r
                        if n is not None:
                            ni_sum += n
                    except Exception:
                        continue
                if "revenue_ttm" not in result and rev_sum > 0:
                    result["revenue_ttm"] = rev_sum
                if "net_income_ttm" not in result and ni_sum > 0:
                    result["net_income_ttm"] = ni_sum
        except Exception:
            pass

    # Latest quarter equity (balance sheet - cumulative, not period)
    try:
        filings = company.get_filings(form="10-Q", amendments=False)
        if not filings.empty:
            latest = filings[0].obj()
            eq = get_parent_equity(latest, latest.financials)
            if eq is not None:
                result["total_equity"] = eq
    except Exception:
        pass

    # Shares: diluted first, fall back to basic (shares_outstanding)
    try:
        filings = company.get_filings(form="10-Q", amendments=False)
        if not filings.empty:
            latest = filings[0].obj()
            shares = latest.financials.get_shares_outstanding_diluted()
            if shares is None:
                shares = latest.financials.get_shares_outstanding_basic()
            if shares is None:
                shares = company.shares_outstanding
            if shares is not None:
                result["shares"] = shares
    except Exception:
        try:
            result["shares"] = company.shares_outstanding
        except Exception:
            pass

    return result


def main():
    parser = argparse.ArgumentParser(
        description="Download S&P 500 quarterly PIT fundamentals from edgartools"
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=None,
        help="Specific tickers to process (default: all S&P 500)",
    )
    parser.add_argument(
        "--output-dir",
        default="data",
        help="Output directory for JSON files (relative to lean_project/)",
    )
    parser.add_argument(
        "--backtest-start",
        default=config.DATA_START,
        help="Earliest quarter start date (YYYY-MM-DD) for quarterly history "
             "(defaults to config.DATA_START to include warm-up history)",
    )
    parser.add_argument(
        "--backtest-end",
        default=config.BACKTEST_END,
        help="Backtest end date (YYYY-MM-DD) for quarterly history",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=None,
        help="Limit number of tickers (for testing)",
    )
    parser.add_argument(
        "--refresh-sp500",
        action="store_true",
        help="Re-download S&P 500 list from GitHub",
    )
    args = parser.parse_args()

    if args.tickers:
        tickers = args.tickers
    else:
        tickers = get_sp500_tickers(refresh=args.refresh_sp500)

    if args.max_tickers:
        tickers = tickers[: args.max_tickers]

    print(f"Processing {len(tickers)} tickers...", file=sys.stderr)

    history = {}
    skipped = 0

    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0:
            print(
                f"  [{i}/{len(tickers)}] {ticker}... "
                f"({len(history)} ok, {skipped} skipped)",
                file=sys.stderr,
            )

        # Normalize ticker for edgartools
        edgar_ticker = normalize_ticker(ticker)
        if edgar_ticker is None:
            skipped += 1
            continue

        try:
            company = Company(edgar_ticker)

            # Get sector/industry from yfinance (needed for quarterly history)
            yinfo = get_yticker_info(ticker)
            sector = yinfo.get("sector")
            industry = yinfo.get("industry")

            # Get quarterly PIT history
            hist = get_quarterly_history(
                company, args.backtest_start,
                sector=sector, industry=industry,
            )
            if hist:
                history[ticker] = hist
            else:
                print(
                    f"  Error: {ticker} ({edgar_ticker}) — "
                    f"no quarterly history available",
                    file=sys.stderr,
                )
                skipped += 1
                continue

        except Exception as e:
            print(
                f"  Warn: {ticker} ({edgar_ticker}) failed: {e}",
                file=sys.stderr,
            )
            skipped += 1

        time.sleep(REQUEST_DELAY)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    # Write quarterly PIT history
    hist_path = out_dir / "fundamentals_history.json"
    with open(hist_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)
    print(f"History: {len(history)} tickers with quarterly data saved to {hist_path}", file=sys.stderr)

    print(f"Skipped: {skipped}", file=sys.stderr)


if __name__ == "__main__":
    main()
