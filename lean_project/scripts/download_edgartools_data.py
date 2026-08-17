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

Financial classification (used by the consumer to exclude financials +
REITs) comes from edgartools natively:
  - ``company.sic`` (int SIC code) — deterministic range check
  - ``company.business_category`` — 'Bank' / 'Insurance Company' / 'BDC' /
    'Investment Manager' / 'REIT' / 'Operating Company' / ...
  - ``company.industry`` — the SIC description string
yfinance ``.info`` is only an optional supplement for the human-readable
``sector``/``industry`` strings (kept for backward compatibility with the
keyword fallback in the consumer). The precise financial filter does NOT
depend on yfinance being reachable.

Usage:
    python scripts/download_edgartools_data.py
    python scripts/download_edgartools_data.py --tickers AAPL MSFT GOOG
    python scripts/download_edgartools_data.py --output-dir data
    python scripts/download_edgartools_data.py --backtest-start 2019-01-01
    # (defaults: --backtest-start=config.DATA_START)
"""

from __future__ import annotations

from typing import Optional

import argparse
import json
import math
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta, date
from pathlib import Path

import yfinance as yf

# Add repo root to path so `import config` works (config/ is at repo root)
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Add lean_project to path so `from data.sp500_data import ...` works
_lean_project = Path(__file__).resolve().parent.parent
if str(_lean_project) not in sys.path:
    sys.path.insert(0, str(_lean_project))

# Single source of truth for SEC identity: importing config sets edgar identity.
# Imported before `from edgar import Company` so identity is guaranteed set
# before any edgar network call.
import config
from edgar import Company
from data.sp500_data import load_sp500_membership

REQUEST_DELAY = 0.5
FLUSH_EVERY = 25  # flush incremental progress to disk every N tickers

_SP500_CSV = _lean_project / "data" / "sp500_ticker_start_end.csv"
_SP500_GITHUB_URL = (
    "https://raw.githubusercontent.com/fja05680/sp500/master/"
    "sp500_ticker_start_end.csv"
)


# ---------------------------------------------------------------------------
# S&P 500 ticker list (mirrors scripts/download_equity_data.py robustness)
# ---------------------------------------------------------------------------


def _refresh_sp500_csv() -> None:
    """Re-download sp500_ticker_start_end.csv from GitHub."""
    import urllib.request

    _SP500_CSV.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_SP500_GITHUB_URL, timeout=30) as resp:
        data = resp.read().decode("utf-8")
    _SP500_CSV.write_text(data, encoding="utf-8")


def get_sp500_tickers(refresh: bool = False) -> list[str]:
    """Return S&P 500 tickers from the local CSV.

    Refreshes the CSV from GitHub when ``refresh`` is set or the CSV is
    missing, and falls back to a hardcoded subset so a missing/unreachable
    CSV never blocks the run (the CSV is gitignored and only regenerated at
    runtime).
    """
    if refresh or not _SP500_CSV.exists():
        try:
            _refresh_sp500_csv()
        except Exception as e:
            print(f"WARN: could not refresh S&P 500 CSV: {e}", file=sys.stderr)

    try:
        membership = load_sp500_membership(str(_SP500_CSV))
        return sorted(membership.keys())
    except Exception:
        pass

    # Fallback: hardcoded subset (yfinance-style tickers; normalized later)
    return [
        "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JNJ",
        "JPM", "V", "PG", "UNH", "HD", "MA", "NVDA", "DIS", "PYPL",
        "NFLX", "ADBE", "CRM", "CMCSA", "PEP", "KO", "ABT", "CSCO",
        "PFE", "TMO", "COST", "AVGO", "ACN", "TXN", "LOW", "NEE", "UPS",
        "QCOM", "IBM", "AMD", "INTC", "NOW", "MDLZ", "ADP", "T", "VZ",
        "CL", "LLY", "SBUX", "MCD", "CAT", "DE", "AXP", "GS", "BLK",
        "SPGI", "ICE", "TGT", "MU", "LRCX", "AMAT", "KLAC", "SNPS", "CDNS",
        "FTNT", "PANW", "GILD", "BKNG", "ISRG", "EA", "TTWO",
        "CMG", "MNST", "CSGP", "KMX", "CTAS", "STZ", "PAYC",
    ]


# ---------------------------------------------------------------------------
# Ticker normalization (mirrors scripts/repair_equity_data.py _variants)
# ---------------------------------------------------------------------------


def _variants(ticker: str) -> list:
    """Candidate edgartools symbols for a given CSV ticker.

    Handles class-share conventions (BRK-B vs BRK.B) and base-symbol
    variants for dotted tickers (e.g. BRK.B -> base BRK).
    """
    out: list = []
    for sym in {ticker, ticker.replace("-", "."), ticker.replace(".", "-")}:
        if sym not in out:
            out.append(sym)
    if "." in ticker:
        base = ticker.split(".", 1)[0]
        for sym in {base, base + "-", base + ".", base.replace(".", "-")}:
            if sym not in out:
                out.append(sym)
    return out


def resolve_company(ticker: str) -> Optional[Company]:
    """Resolve a ticker variant to a loaded edgartools ``Company``.

    Tries yfinance-style (BRK-B) and SEC-style (BRK.B) forms plus base
    symbols so class-share tickers are not silently skipped. Returns the
    resolved ``Company`` (callers reuse it — no second network lookup) or
    ``None`` if none of the variants resolve.
    """
    for var in _variants(ticker):
        try:
            c = Company(var)
            if c is not None and c.name:
                return c
        except Exception:
            continue
    return None


# ---------------------------------------------------------------------------
# yfinance supplement (optional — financial filter does not depend on it)
# ---------------------------------------------------------------------------


def get_yticker_info(ticker: str) -> dict:
    """Get sector, industry, name, market_cap, price from yfinance (optional)."""
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


# ---------------------------------------------------------------------------
# edgartools classification (PRIMARY source for the financial filter)
# ---------------------------------------------------------------------------


def _to_int_sic(value) -> Optional[int]:
    """Convert an edgartools SIC (often a string, sometimes '') to int."""
    if value is None:
        return None
    try:
        return int(value)
    except (ValueError, TypeError):
        return None


def classify_company(company: Company) -> dict:
    """Return native edgar classification for a company.

    Returns ``{sic, business_category, industry}`` where ``sic`` is an int
    (or None) and ``business_category`` is the precise enum string
    ('Bank', 'Insurance Company', 'BDC', 'Investment Manager', 'REIT',
    'Operating Company', ...).
    """
    return {
        "sic": _to_int_sic(getattr(company, "sic", None)),
        "business_category": getattr(company, "business_category", None)
        or "Unknown",
        "industry": getattr(company, "industry", None) or None,
    }


# ---------------------------------------------------------------------------
# Pure helpers (kept stable for tests/test_eps_growth.py)
# ---------------------------------------------------------------------------


def get_parent_equity(tenq, fin):
    """Total shareholders equity excluding minority interests.

    Uses edgartools' ``StockholdersEquity`` (parent equity, ex-minority),
    which is the value the consumer needs for ROE.  Returns ``None`` when the
    fact is unavailable.  (The older noncontrolling-interest fallback
    referenced methods that do not exist in edgartools 5.x, so it has been
    removed rather than kept as dead code.)
    """
    return fin.get_stockholders_equity()


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


# ---------------------------------------------------------------------------
# Quarterly history
# ---------------------------------------------------------------------------


def _safe_period_of_report(filing) -> Optional[str]:
    """Period-of-report fallback using the (network) sgml path if needed.

    Normalizes whatever type edgartools returns (``date``/``datetime`` or
    string) to an ISO ``YYYY-MM-DD`` string so it compares cleanly against
    the other period strings.
    """
    try:
        p = filing.period_of_report
    except Exception:
        return None
    if p is None:
        return None
    if isinstance(p, (datetime, date)):
        return p.isoformat()[:10]
    return str(p)


def _clean(value):
    """Normalize a numeric XBRL fact.

    Drops ``None`` and ``float('nan')`` (edgartools emits ``nan`` for some
    missing facts); a legitimate ``0`` is preserved.  Returning ``None`` for
    missing/``nan`` keeps the TTM sums valid and the serialized JSON portable
    (``json.dump`` would otherwise write the non-standard literal ``NaN``).
    """
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    return value


def get_quarterly_history(
    company: Company,
    backtest_start: str,
    sector: str | None = None,
    industry: str | None = None,
    sic: int | None = None,
    business_category: str | None = None,
) -> dict:
    """Get quarterly PIT financial history from 10-Q filings.

    Uses the filings table's ``report_date`` (period of report) — a cheap,
    no-network column — instead of the per-filing ``period_of_report``
    property, which would trigger a separate SGML download for every
    filing.  Restricts the expensive ``obj()`` XBRL parse to quarters
    whose period is within the backtest window plus a 2-year growth
    look-back, so the run scales to the full S&P 500.

    De-duplicates by period before summing TTM (amendment guard) and sums
    the trailing *available* quarters positionally (current + previous 3),
    so a missing quarter self-heals via the next filing's cumulative value.
    """
    try:
        filings = company.get_filings(form="10-Q", amendments=False)
        if filings is None or len(filings) == 0:
            return {}
    except Exception:
        return {}

    backtest_start_date = datetime.strptime(backtest_start, "%Y-%m-%d").date()
    # Fetch only periods that can contribute to output or the 2y g_eps look-back.
    cutoff_date = backtest_start_date - timedelta(days=365 * 2)
    cutoff_str = cutoff_date.isoformat()

    # Collect (period, filing) cheaply; report_date is the period of report.
    candidates = []
    for f in filings:
        period = f.report_date or _safe_period_of_report(f)
        if not period:
            continue
        if period < cutoff_str:
            continue
        candidates.append((period, f))

    if not candidates:
        return {}

    # Parse financials only for in-window candidates. De-duplicate by period
    # (keep the last occurrence) so an amendment double-count cannot happen.
    by_period: dict = {}
    for period, f in candidates:
        try:
            tenq = f.obj()
            fin = tenq.financials
            rev = _clean(fin.get_revenue())
            ni = _clean(fin.get_net_income())
            eq = _clean(get_parent_equity(tenq, fin))
            shares_diluted = _clean(fin.get_shares_outstanding_diluted())
            shares_basic = _clean(fin.get_shares_outstanding_basic())
            shares = shares_diluted or shares_basic or _clean(company.shares_outstanding)
            by_period[period] = {
                "period": period,
                "revenue": rev,
                "net_income": ni,
                "equity": eq,
                "shares": shares,
            }
        except Exception:
            continue

    if not by_period:
        return {}

    quarters = [by_period[k] for k in sorted(by_period.keys())]

    all_quarters = []  # (period_str, ttm_eps) for growth computation
    hist_dict = {}
    for i, current in enumerate(quarters):
        # TTM = sum of current and previous 3 quarters (positional, available).
        window = quarters[max(0, i - 3): i + 1]

        rev_vals = [q["revenue"] for q in window if q["revenue"] is not None]
        ni_vals = [q["net_income"] for q in window if q["net_income"] is not None]
        rev_ttm = sum(rev_vals) if rev_vals else None
        ni_ttm = sum(ni_vals) if ni_vals else None

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
            "sic": sic,
            "business_category": business_category,
            "book_value": round(book_value_per_share, 6) if book_value_per_share is not None else None,
            "roe": round(roe, 8) if roe is not None else None,
            "eps": round(ttm_eps, 4) if ttm_eps is not None else None,
            "revenue_ttm": round(rev_ttm) if rev_ttm is not None else None,
            "net_income_ttm": round(ni_ttm) if ni_ttm is not None else None,
        }

    # Compute PIT EPS growth for every output quarter
    g_eps_map = compute_pit_eps_growth(all_quarters, years=2)
    for period_str, g_eps in g_eps_map.items():
        if period_str in hist_dict:
            hist_dict[period_str]["g_eps"] = round(g_eps, 6) if g_eps is not None else None

    return hist_dict


# ---------------------------------------------------------------------------
# Output write + backup
# ---------------------------------------------------------------------------


def _write_history(out_path: Path, history: dict, backup: bool) -> None:
    if backup and out_path.exists():
        try:
            bak = out_path.with_name(out_path.stem + ".bak.json")
            shutil.copy2(out_path, bak)
            print(f"Backed up existing {out_path.name} -> {bak.name}", file=sys.stderr)
        except Exception as e:
            print(f"WARN: could not back up {out_path}: {e}", file=sys.stderr)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(history, f, indent=2)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


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
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-fetch tickers already present in the existing output (resume "
             "skips completed tickers by default)",
    )
    args = parser.parse_args()

    if args.tickers:
        tickers = args.tickers
    else:
        tickers = get_sp500_tickers(refresh=args.refresh_sp500)

    if args.max_tickers:
        tickers = tickers[: args.max_tickers]

    print(f"Processing {len(tickers)} tickers...", file=sys.stderr)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    hist_path = out_dir / "fundamentals_history.json"

    # Resume: load any existing output and merge per-ticker.
    history: dict = {}
    if hist_path.exists():
        try:
            history = json.load(open(hist_path, encoding="utf-8"))
            print(
                f"Resuming: loaded {len(history)} tickers from {hist_path.name}",
                file=sys.stderr,
            )
        except Exception as e:
            print(f"WARN: could not load existing {hist_path.name}: {e}", file=sys.stderr)

    skipped = 0
    skipped_reasons: dict = {}
    tracebacks_shown = 0
    max_tracebacks = 5

    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0:
            print(
                f"  [{i}/{len(tickers)}] {ticker}... "
                f"({len(history)} ok, {skipped} skipped)",
                file=sys.stderr,
            )

        # Resume: keep completed tickers unless --force.
        if ticker in history and not args.force:
            continue

        company = resolve_company(ticker)
        if company is None:
            skipped += 1
            skipped_reasons.setdefault("no_edgar_match", []).append(ticker)
            continue

        try:
            # Native edgar classification (PRIMARY financial filter source).
            cls = classify_company(company)
            sic = cls["sic"]
            business_category = cls["business_category"]

            # yfinance supplement (optional) for human-readable sector/industry.
            yinfo = get_yticker_info(ticker)
            sector = yinfo.get("sector")
            industry = yinfo.get("industry")
            # Prefer edgar's SIC description when yfinance is unavailable.
            if not industry and cls["industry"]:
                industry = cls["industry"]

            hist = get_quarterly_history(
                company, args.backtest_start,
                sector=sector, industry=industry,
                sic=sic, business_category=business_category,
            )
            if hist:
                history[ticker] = hist
            else:
                skipped += 1
                skipped_reasons.setdefault("no_quarterly_history", []).append(ticker)
                print(
                    f"  Error: {ticker} ({company.name}) — "
                    f"no quarterly history available",
                    file=sys.stderr,
                )

        except Exception as e:
            skipped += 1
            skipped_reasons.setdefault(f"{type(e).__name__}", []).append(ticker)
            if tracebacks_shown < max_tracebacks:
                tracebacks_shown += 1
                print(
                    f"  Traceback: {ticker} ({company.name}) failed:",
                    file=sys.stderr,
                )
                traceback.print_exc()
            else:
                print(
                    f"  Warn: {ticker} ({company.name}) failed: {e}",
                    file=sys.stderr,
                )

        # Flush incremental progress so a mid-run failure is recoverable.
        if i % FLUSH_EVERY == 0:
            _write_history(hist_path, history, backup=False)

        time.sleep(REQUEST_DELAY)

    # Final write with backup of the previous file.
    _write_history(hist_path, history, backup=True)
    print(
        f"History: {len(history)} tickers with quarterly data saved to {hist_path}",
        file=sys.stderr,
    )
    print(f"Skipped: {skipped}", file=sys.stderr)
    for reason, tickers_in_reason in sorted(skipped_reasons.items()):
        sample = ", ".join(tickers_in_reason[:20])
        more = "" if len(tickers_in_reason) <= 20 else f" ...(+{len(tickers_in_reason) - 20})"
        print(f"  {reason}: {len(tickers_in_reason)} -> {sample}{more}", file=sys.stderr)


if __name__ == "__main__":
    main()
