"""Download S&P 500 quarterly PIT fundamentals from edgartools (SEC filings).

Produces:
    1. ``fundamentals_history.json`` — quarterly PIT history (plus ``g_eps``)
    2. ``sp500_cik_map.csv`` — ticker->CIK map (auto-built; curate delisted CIKs)
    3. ``fundamentals_no_edgar_match.json`` — tickers with no SEC match (delisted
       without a curated CIK); re-probed each run, recovered when a CIK appears
       in the map.

TTM rules (applied per quarter for history):
  - Income statement items (revenue, net_income): sum of 4 most recent
    quarters using only quarters available at that point (no look-ahead)
  - Balance sheet items (book_value/equity): latest quarter value
  - Shares: diluted first, fall back to basic (shares_outstanding)
  - ROE = net_income_ttm / equity
  - EPS = net_income_ttm / shares
  - P/B = price / book_value_per_share

Equity is total shareholders equity excluding minority interests, taken
from edgartools' ``StockholdersEquity`` (parent equity).

Growth (``g_eps``): TTM-EPS CAGR over the last 2 years, computed from
EPS available up to each date (PIT, no look-ahead). No floor or cap.

Financial classification (used by the consumer to exclude financials +
REITs) comes from edgartools natively:
  - ``company.sic`` (int SIC code) — deterministic range check
  - ``company.business_category`` — 'Bank' / 'Insurance Company' / 'BDC' /
    'Investment Manager' / 'REIT' / 'Operating Company' / ...
  - ``company.industry`` — the SIC description string
The consumer ignores ``sector`` (always ``None``); ``industry`` carries the
edgar SIC description.

Usage:
    python scripts/download_edgartools_data.py
    python scripts/download_edgartools_data.py --tickers AAPL MSFT GOOG
    python scripts/download_edgartools_data.py --output-dir lean_project/data
    python scripts/download_edgartools_data.py --backtest-start 2019-01-01
    # (defaults: --backtest-start=config.DATA_START)
"""

from __future__ import annotations

from typing import Optional

import argparse
import csv
import json
import math
import shutil
import sys
import time
import traceback
from datetime import datetime, timedelta, date
from pathlib import Path

# Add repo root to path so `import config` works (config/ is at repo root)
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

# Add lean_project to path so `from data.sp500_data import ...` works
_lean_project = Path(__file__).resolve().parent.parent
if str(_lean_project) not in sys.path:
    sys.path.insert(0, str(_lean_project))

# Add this scripts dir to path so `import build_cik_map` works for self-bootstrap
_scripts_dir = Path(__file__).resolve().parent
if str(_scripts_dir) not in sys.path:
    sys.path.insert(0, str(_scripts_dir))

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

_CIK_MAP_PATH = _lean_project / "data" / "sp500_cik_map.csv"


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

    # Fallback: hardcoded subset (hyphenated ticker symbols; normalized to the
    # edgar form by _variants)
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


def resolve_company(ticker: str, cik_map: Optional[dict] = None) -> Optional[Company]:
    """Resolve a ticker to a loaded edgartools ``Company``.

    Order:
      1. If a curated ``cik_map`` holds this ticker, resolve directly by CIK
         (``Company(int_cik)``). This is the only path that recovers delisted
         constituents whose ticker is no longer in the SEC's live lookup.
      2. Otherwise fall back to the edgar ticker variant loop
          (``Company(var)``), which works for all *current* filers.

    Returns the resolved ``Company`` (callers reuse it — no second network
    lookup) or ``None`` if nothing resolves.
    """
    if cik_map and ticker in cik_map:
        cik = cik_map[ticker]
        try:
            c = Company(int(cik))
            if c is not None and c.name:
                return c
        except Exception:
            pass
    for var in _variants(ticker):
        try:
            c = Company(var)
            if c is not None and c.name:
                return c
        except Exception:
            continue
    return None


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
# CIK map (ticker -> CIK) for recovering delisted constituents
# ---------------------------------------------------------------------------


def load_cik_map(path: Path) -> dict:
    """Load the curated ``sp500_cik_map.csv`` (``ticker,cik``) into a dict.

    CIKs are coerced to ``int`` so they can be passed straight to
    ``Company(int_cik)``.  Returns an empty dict when the file is missing, so
    callers fall back to the live ticker lookup.
    """
    if not path.exists():
        return {}
    out: dict = {}
    with open(path, encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            t = (row.get("ticker") or "").strip()
            c = (row.get("cik") or "").strip()
            if not t or not c:
                continue
            try:
                out[t] = int(c)
            except ValueError:
                continue
    return out


# ---------------------------------------------------------------------------
# Pure helpers (kept stable for tests/test_eps_growth.py)
# ---------------------------------------------------------------------------


def get_parent_equity(tenq, fin):
    """Total shareholders equity excluding minority interests.

    Uses edgartools' ``StockholdersEquity`` (parent equity, ex-minority),
    which is the value the consumer needs for ROE.  Returns ``None`` when the
    fact is unavailable.
    """
    return fin.get_stockholders_equity()


def compute_pit_eps_growth(sorted_pairs: list, years: int = 2) -> dict:
    """Compute point-in-time EPS CAGR for each filing.

    ``sorted_pairs`` must be a list of ``(filed_str, ttm_eps)`` sorted
    ascending by filing_date (PIT). For each filing ``F`` the CAGR is
    computed against the latest entry with ``filed <= F - years`` —
    strictly past, no look-ahead. Negative or missing values return
    ``None``. No floor or cap is applied. When keys are filing dates,
    the CAGR denominator uses the actual calendar delta between the two
    filing dates (not the fixed ``years``), so gaps and filing lags are
    handled correctly. ``period`` is retained in the output for audit
    but not used for growth timing.
    """
    fileds = [p for p, _ in sorted_pairs]
    eps_vals = [e for _, e in sorted_pairs]
    result = {}

    for i, (filed, eps_now) in enumerate(sorted_pairs):
        if eps_now is None or eps_now <= 0:
            result[filed] = None
            continue
        # Find latest ref with filed <= F - years (strictly past, PIT)
        cutoff = datetime.strptime(filed, "%Y-%m-%d").date() - timedelta(days=365 * years)
        ref_idx = None
        for j in range(i - 1, -1, -1):
            if datetime.strptime(fileds[j], "%Y-%m-%d").date() <= cutoff:
                ref_idx = j
                break
        if ref_idx is None:
            result[filed] = None
            continue
        eps_ref = eps_vals[ref_idx]
        if eps_ref is None or eps_ref <= 0:
            result[filed] = None
            continue
        # Annualize over the ACTUAL calendar delta between the two filing dates
        # (not the fixed `years` cutoff), so gaps in the series and leap-year
        # day-counts are handled correctly. `ref_idx` is strictly before `i`,
        # so the delta is always positive.
        d_now = datetime.strptime(filed, "%Y-%m-%d").date()
        d_ref = datetime.strptime(fileds[ref_idx], "%Y-%m-%d").date()
        actual_years = (d_now - d_ref).days / 365.25
        cagr = (eps_now / eps_ref) ** (1.0 / actual_years) - 1
        result[filed] = cagr

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


def _filing_date_str(filing) -> Optional[str]:
    """Return filing_date as ISO YYYY-MM-DD string (PIT availability date).

    ``filing.filing_date`` is a cheap parquet column (no network), unlike
    ``period_of_report`` which may require SGML. It is the SEC acceptance
    date when the report becomes public — the correct PIT key. Normalizes
    date/datetime/string to ISO string for clean comparison.
    """
    try:
        fd = filing.filing_date
    except Exception:
        return None
    if fd is None:
        return None
    if isinstance(fd, (datetime, date)):
        return fd.isoformat()[:10]
    return str(fd)[:10]


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

    PIT key is ``filing_date`` (SEC acceptance date, cheap parquet column),
    NOT ``report_date``/``period_of_report`` (fiscal period end). Using the
    fiscal date as the PIT key introduces ~30-45 days of look-ahead bias
    because reports are only public after filing. ``filing_date`` is the
    correct availability date; ``report_date`` is kept as ``period`` for
    audit/TAM but never used for PIT joins.

    Restricts the expensive ``obj()`` XBRL parse to filings whose
    ``filing_date`` is within the backtest window plus a 2-year growth
    look-back, so the run scales to the full S&P 500.

    De-duplicates by period (keeping the latest filing for that period)
    before summing TTM (amendment guard) and sums the trailing *available*
    filings positionally (current + previous 3), so a missing quarter
    self-heals via the next filing's cumulative value. quarters are
    ordered by ``filing_date`` (PIT order), not period end.
    """
    try:
        filings = company.get_filings(form="10-Q", amendments=False)
        if filings is None or len(filings) == 0:
            return {}
    except Exception:
        return {}

    backtest_start_date = datetime.strptime(backtest_start, "%Y-%m-%d").date()
    # Fetch only filings that can contribute to output or the 2y g_eps look-back.
    # PIT cutoff is on filing_date, not period, so a filing whose period is
    # just before the cutoff but filed after it is correctly retained for TTM/growth.
    cutoff_date = backtest_start_date - timedelta(days=365 * 2)
    cutoff_str = cutoff_date.isoformat()

    # Collect (period, filing_date, filing) cheaply; both are parquet columns (no network).
    # filing_date is the PIT availability date; report_date is the fiscal period end.
    candidates = []
    for f in filings:
        period = f.report_date or _safe_period_of_report(f)
        filed = _filing_date_str(f)
        if not period or not filed:
            continue
        # PIT filter: filing must be on/after cutoff to contribute to TTM/growth window.
        if filed < cutoff_str:
            continue
        candidates.append((period, filed, f))

    if not candidates:
        return {}

    # Parse financials only for in-window candidates. De-duplicate by period
    # (keep the latest filing for that period) so an amendment double-count cannot happen.
    by_period: dict = {}
    parse_failures = 0
    for period, filed, f in candidates:
        try:
            tenq = f.obj()
            fin = tenq.financials
            rev = _clean(fin.get_revenue())
            ni = _clean(fin.get_net_income())
            eq = _clean(get_parent_equity(tenq, fin))
            shares_diluted = _clean(fin.get_shares_outstanding_diluted())
            shares_basic = _clean(fin.get_shares_outstanding_basic())
            # PIT integrity: never fall back to company-level *current* shares
            # outstanding — that would stamp today's share count onto
            # historical quarters. If neither per-period XBRL tag exists,
            # leave ``shares`` None: the quarter's book_value/eps become None
            # and the screen skips it instead of silently using look-ahead data.
            shares = shares_diluted or shares_basic
            # Amendment guard: keep the filing with the latest filing_date for this period.
            existing = by_period.get(period)
            if existing is not None and existing.get("filed", "") >= filed:
                continue
            by_period[period] = {
                "period": period,
                "filed": filed,
                "revenue": rev,
                "net_income": ni,
                "equity": eq,
                "shares": shares,
            }
        except Exception:
            parse_failures += 1
            continue

    if parse_failures:
        # One summary line instead of per-filing spam: silent data loss here
        # would otherwise shrink TTM windows without any trace in the log.
        # Company has no .ticker attr (use get_ticker()), fall back to CIK.
        try:
            _ticker_label = company.get_ticker()  # type: ignore[attr-defined]
        except Exception:
            _ticker_label = None
        if not _ticker_label:
            try:
                _t = getattr(company, "tickers", None)
                _ticker_label = _t[0] if isinstance(_t, (list, tuple)) and _t else None
            except Exception:
                _ticker_label = None
        if not _ticker_label:
            _ticker_label = str(getattr(company, "cik", "unknown"))
        print(
            f"WARN: {_ticker_label}: {parse_failures}/{len(candidates)} 10-Q "
            f"filings failed to parse (periods dropped)",
            file=sys.stderr,
        )

    if not by_period:
        return {}

    # PIT order: sort by filing_date, not period end — the report is only
    # available after filing, so TTM windows must follow filing chronology.
    quarters = sorted(by_period.values(), key=lambda x: x["filed"])

    all_quarters = []  # (filed_str, ttm_eps) for growth computation (PIT)
    hist_dict = {}
    for i, current in enumerate(quarters):
        # TTM = sum of current and previous 3 filings (positional, available).
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
        filed_str = str(current["filed"])
        all_quarters.append((filed_str, ttm_eps))

        # Only include filings with filing_date >= backtest_start in output.
        # A filing for period 2018-12-31 filed 2019-02-01 is NOT available at
        # 2019-01-01, so it must not be keyed by period. Using filing_date
        # as the canonical key guarantees no look-ahead in fundamental_as_of.
        if datetime.strptime(filed_str, "%Y-%m-%d").date() < backtest_start_date:
            continue

        hist_dict[filed_str] = {
            "period": period_str,
            "filed": filed_str,
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

    # Compute PIT EPS growth for every output filing (keyed by filing_date)
    g_eps_map = compute_pit_eps_growth(all_quarters, years=2)
    for filed_str, g_eps in g_eps_map.items():
        if filed_str in hist_dict:
            hist_dict[filed_str]["g_eps"] = round(g_eps, 6) if g_eps is not None else None

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
# Persisted skip-set resolution (with CIK-map TTL)
# ---------------------------------------------------------------------------


def _resolve_skip_set(
    skip_path: Path,
    cik_map_path: Path,
    force: bool,
    clean_skip: bool,
) -> set:
    """Load the persisted skip set, applying --clean-skip and a CIK-map TTL.

    - ``--clean-skip`` deletes the skip file on disk so every previously-skipped
      ticker is re-probed this run.
    - TTL: if the CIK map was modified *after* the skip file was written, the
      skip set is cleared (returned empty) so tickers that may now resolve via a
      freshly-curated CIK are retried. The file on disk is NOT deleted unless
      ``--clean-skip`` is set.
    """
    if clean_skip and skip_path.exists():
        try:
            skip_path.unlink()
            print(f"--clean-skip: removed {skip_path}", file=sys.stderr)
        except Exception as e:
            print(f"WARN: --clean-skip could not remove {skip_path}: {e}", file=sys.stderr)

    cik_map_mtime = cik_map_path.stat().st_mtime if cik_map_path.exists() else 0
    skip_mtime = skip_path.stat().st_mtime if skip_path.exists() else 0

    skip_set: set = set()
    if skip_path.exists() and not force:
        try:
            skip_set = set(json.load(open(skip_path, encoding="utf-8")))
        except Exception as e:
            print(f"WARN: could not load {skip_path.name}: {e}", file=sys.stderr)
        # CIK map newer than the skip file -> re-probe all skipped tickers.
        if skip_mtime < cik_map_mtime:
            print(
                "CIK map updated since skip file; retrying "
                f"{len(skip_set)} previously-skipped tickers",
                file=sys.stderr,
            )
            skip_set = set()
    return skip_set


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
        default=str(_lean_project / "data"),
        help="Output directory for JSON files (defaults to lean_project/data)",
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
    parser.add_argument(
        "--clean-skip",
        action="store_true",
        help="Delete the persisted skip file (fundamentals_no_edgar_match.json) "
             "before running so previously-skipped tickers are re-probed.",
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

    # CIK map (ticker -> CIK) lets us recover delisted constituents that the
    # live ticker lookup can no longer resolve. Self-bootstrap: if the map is
    # missing, build it from edgar's bundled parquet before processing.
    if not _CIK_MAP_PATH.exists():
        print("CIK map missing; building from edgar lookup...", file=sys.stderr)
        try:
            from build_cik_map import build_cik_map

            build_cik_map(_CIK_MAP_PATH, merge=True)
        except Exception as e:
            print(f"WARN: could not auto-build CIK map: {e}", file=sys.stderr)
    cik_map = load_cik_map(_CIK_MAP_PATH)
    print(f"Loaded CIK map: {len(cik_map)} tickers", file=sys.stderr)

    # Persisted skip set: tickers with no edgar match (delisted without a
    # curated CIK). Re-checked each run; a ticker present in the CIK map is
    # never hard-skipped, so later curation can recover it. --clean-skip and a
    # CIK-map TTL are applied here (see _resolve_skip_set).
    skip_path = out_dir / "fundamentals_no_edgar_match.json"
    skip_set = _resolve_skip_set(
        skip_path, _CIK_MAP_PATH, args.force, getattr(args, "clean_skip", False)
    )
    if skip_path.exists() and skip_set:
        print(f"Resuming skip set: {len(skip_set)} tickers", file=sys.stderr)

    def _write_skip(path: Path, skip_set: set, backup: bool) -> None:
        if backup and path.exists():
            try:
                bak = path.with_name(path.stem + ".bak.json")
                shutil.copy2(path, bak)
            except Exception:
                pass
        with open(path, "w", encoding="utf-8") as f:
            json.dump(sorted(skip_set), f, indent=2)

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

        # A curated CIK can recover a previously-unmatched ticker, so never
        # hard-skip a ticker that has a CIK entry — fall through and re-probe.
        if ticker not in cik_map and ticker in skip_set and not args.force:
            skipped += 1
            skipped_reasons.setdefault("no_edgar_match", []).append(ticker)
            continue

        company = resolve_company(ticker, cik_map)
        if company is None:
            skipped += 1
            skipped_reasons.setdefault("no_edgar_match", []).append(ticker)
            skip_set.add(ticker)
            continue

        try:
            # Native edgar classification (PRIMARY financial filter source).
            cls = classify_company(company)
            sic = cls["sic"]
            business_category = cls["business_category"]

            # sector is None (ignored by consumers); industry carries the edgar
            # SIC description string.
            sector = None
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
            _write_skip(skip_path, skip_set, backup=False)

        time.sleep(REQUEST_DELAY)

    # Final write with backup of the previous file.
    _write_history(hist_path, history, backup=True)
    _write_skip(skip_path, skip_set, backup=True)
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
