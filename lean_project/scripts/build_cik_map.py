"""Build a ticker -> CIK map for S&P 500 constituents from edgar.

edgartools exposes a bundled ``company_tickers.parquet`` (offline, instant)
via ``get_company_cik_lookup()``.  Current SEC filers resolve by ticker
(including base-ticker keys, e.g. ``BRK`` alongside ``BRK-B``).  Delisted
constituents are not in that lookup, so their CIK must be curated by hand
into ``sp500_cik_map.csv`` (``ticker,cik`` rows) — this script preserves
any such curated rows via merge mode.

The downloader imports ``build_cik_map`` and calls it (self-bootstrap) when
the map is missing, so the pipeline never needs a manual first step.

Usage:
    python scripts/build_cik_map.py
    python scripts/build_cik_map.py --output lean_project/data/sp500_cik_map.csv
    python scripts/build_cik_map.py --refresh   # ignore existing, rebuild current-only
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

# Repo/lean paths so `import config` and `from data.sp500_data import ...` work.
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))
_lean_project = Path(__file__).resolve().parent.parent
if str(_lean_project) not in sys.path:
    sys.path.insert(0, str(_lean_project))

import config  # noqa: E402  (sets edgar identity before any edgar call)
from edgar.reference.tickers import get_company_cik_lookup  # noqa: E402
from data.sp500_data import load_sp500_membership  # noqa: E402

_SP500_CSV = _lean_project / "data" / "sp500_ticker_start_end.csv"


def _variants(ticker: str) -> list:
    """Candidate edgar symbols for a CSV ticker (mirrors download script)."""
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


def _load_existing(path: Path) -> dict:
    """Load an existing ``ticker,cik`` map; returns {} when missing/unreadable."""
    if not path.exists():
        return {}
    out: dict = {}
    try:
        with open(path, encoding="utf-8") as f:
            for row in csv.DictReader(f):
                t = (row.get("ticker") or "").strip()
                c = (row.get("cik") or "").strip()
                if t and c:
                    try:
                        out[t] = int(c)
                    except ValueError:
                        continue
    except Exception:
        return {}
    return out


def build_cik_map(out_path: Path, merge: bool = True) -> dict:
    """Build (and write) the ticker->CIK map; return the resulting dict.

    With ``merge=True`` any pre-existing rows are preserved and only missing
    tickers are filled (keeps curated delisted CIKs).  With ``merge=False``
    existing rows are dropped and the map is rebuilt from current constituents
    only (``--refresh``).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)

    existing = _load_existing(out_path) if merge else {}

    membership = load_sp500_membership(str(_SP500_CSV))
    tickers = sorted(membership.keys())

    lookup = get_company_cik_lookup()

    result: dict = dict(existing)  # start from curated rows when merging
    unresolved: list = []

    for ticker in tickers:
        if ticker in result:
            continue
        cik = None
        for var in _variants(ticker):
            cik = lookup.get(var)
            if cik is not None:
                break
        if cik is not None:
            result[ticker] = int(cik)
        else:
            unresolved.append(ticker)

    # Write in ticker order for a stable, reviewable diff.
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["ticker", "cik"])
        for ticker in sorted(result.keys()):
            writer.writerow([ticker, result[ticker]])

    print(
        f"Wrote {len(result)} ticker->CIK rows to {out_path}"
        + (f" ({len(existing)} curated/merged)" if existing else ""),
        file=sys.stderr,
    )
    if unresolved:
        print(
            f"{len(unresolved)} ticker(s) unresolved (delisted without a CIK in "
            f"the edgar lookup):",
            file=sys.stderr,
        )
        # Group by first letter for readability.
        grouped: dict = {}
        for t in unresolved:
            grouped.setdefault(t[0], []).append(t)
        for letter in sorted(grouped):
            print(f"  {letter}: {', '.join(grouped[letter])}", file=sys.stderr)
        print(
            "  To recover these, add `ticker,cik` rows to "
            f"{out_path.name} (CIK from the SEC EDGAR URL of any of the "
            "company's filings) and re-run.",
            file=sys.stderr,
        )
    return result


def main():
    parser = argparse.ArgumentParser(
        description="Build S&P 500 ticker->CIK map from edgar (offline)"
    )
    parser.add_argument(
        "--output",
        default=str(_lean_project / "data" / "sp500_cik_map.csv"),
        help="Output CSV path (default: lean_project/data/sp500_cik_map.csv)",
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="Ignore any existing map and rebuild from current constituents only",
    )
    args = parser.parse_args()
    build_cik_map(Path(args.output), merge=not args.refresh)


if __name__ == "__main__":
    main()
