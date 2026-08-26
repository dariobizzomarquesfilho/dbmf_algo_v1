"""Scrape Damodaran's historical US implied ERP from histimpl.html.

Source: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histimpl.html
(1960–2025, annual).  The relevant column is "Implied ERP (FCFE)".

Output: implied_erp/data/histimpl_us_erp.json
    {
      "source": "histimpl.html",
      "updated": "2026-01",
      "url": <source url>,
      "us_erp_history": {"1960-01-01": 0.0292, ..., "2025-01-01": 0.0423}
    }

The history is a FALLBACK for the spreadsheet-derived PIT ERP used in the Lean
backtest.  Annual values are keyed YYYY-01-01 (published January, used whole year).

Usage:
    python scrape_histimpl.py
    python scrape_histimpl.py --out path/to/histimpl_us_erp.json
    python scrape_histimpl.py --dry-run        # parse + print, write nothing
    python scrape_histimpl.py --force          # re-download even if present
"""

from __future__ import annotations

import argparse
import io
import json
import re
from pathlib import Path

import pandas as pd
import requests

HISTIMPL_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/datafile/histimpl.html"

DEFAULT_OUT = Path(__file__).resolve().parent.parent / "data" / "histimpl_us_erp.json"

YEAR_MIN = 1960
# Upper bound follows Damodaran's latest published year; set dynamically to
# current year +1 so 2026+ is not silently dropped (was hardcoded 2025).
from datetime import datetime as _dt
YEAR_MAX = _dt.now().year + 1


def _pct_to_float(cell) -> float | None:
    """'2.92%' -> 0.0292 ; else None.

    Rejects NaN / pd.NA / empty cells so missing FCFE rows are dropped.
    Handles dash variants, N/A*, parenthesized negatives, etc.
    """
    if cell is None:
        return None
    try:
        import math
        import pandas as pd

        if isinstance(cell, float) and math.isnan(cell):
            return None
        if cell is pd.NA:
            return None
    except (TypeError, ImportError):
        pass
    s = str(cell).strip()
    if not s or s.lower() in ("nan", "nat", "none", "<na>", "—", "–", "-", "n/a", "n/a*", ""):
        return None
    # Parenthesized negative: (2.5%) -> -2.5%
    if s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1]
    s = s.replace("%", "").replace(",", "").replace("$", "").strip()
    # Remove footnote markers e.g. 2.5* or 2.5†
    s = re.sub(r"[*†‡]+$", "", s).strip()
    if not s or s.lower() in ("nan", "nat", "none", "<na>"):
        return None
    try:
        return float(s) / 100.0
    except ValueError:
        return None


def _find_year_table(tables: list) -> pd.DataFrame | None:
    """Among read_html frames, pick the one with a 'Year' column that also has an ERP column."""
    best = None
    for df in tables:
        cols = [str(c).lower() for c in df.columns]
        if "year" in cols:
            # Prefer table that also contains an implied ERP column
            has_erp = any("implied" in str(c).lower() and "erp" in str(c).lower() for c in df.columns)
            if has_erp:
                return df
            if best is None:
                best = df
    return best


def _find_erp_column(df: pd.DataFrame) -> str | None:
    """Locate the implied-ERP column: header contains 'implied' AND 'erp'."""
    # Prefer the FCFE variant if multiple implied ERP columns exist
    fcfe_candidate = None
    for c in df.columns:
        low = str(c).lower()
        if "implied" in low and "erp" in low:
            if "fcfe" in low:
                return c
            if fcfe_candidate is None:
                fcfe_candidate = c
    return fcfe_candidate


def parse_html(html_text: str) -> dict[str, float]:
    """Parse histimpl.html text into {YYYY-01-01: erp_decimal}."""
    tables = pd.read_html(io.StringIO(html_text), header=0)
    df = _find_year_table(tables)
    if df is None:
        raise ValueError("No table with a 'Year' column found in histimpl.html")

    erp_col = _find_erp_column(df)
    if erp_col is None:
        raise ValueError("Could not locate 'Implied ERP (FCFE)' column in histimpl.html")

    history: dict[str, float] = {}
    for _, row in df.iterrows():
        year_raw = row.get("Year")
        try:
            year = int(year_raw)
        except (TypeError, ValueError):
            continue
        if year < YEAR_MIN or year > YEAR_MAX:
            continue
        erp = _pct_to_float(row.get(erp_col))
        if erp is None:
            continue
        history[f"{year}-01-01"] = erp

    if not history:
        raise ValueError("Parsed 0 usable year rows from histimpl.html")
    return dict(sorted(history.items()))


def fetch_html(url: str = HISTIMPL_URL, timeout: int = 30) -> str:
    headers = {"User-Agent": "Mozilla/5.0 (compatible; dbmf-quant; +https://github.com/anomalyco/opencode)"}
    last_exc = None
    for attempt in range(3):
        try:
            resp = requests.get(url, timeout=timeout, headers=headers)
            resp.raise_for_status()
            return resp.text
        except requests.exceptions.RequestException as e:
            last_exc = e
            if attempt < 2:
                import time
                time.sleep(1.5 * (2 ** attempt))
                continue
            raise
    raise last_exc  # type: ignore[misc]


def scrape(
    url: str = HISTIMPL_URL,
    updated: str = "2026-01",
    timeout: int = 30,
) -> dict:
    """Download + parse histimpl.html into the output dict."""
    html = fetch_html(url, timeout=timeout)
    history = parse_html(html)
    return {
        "source": "histimpl.html",
        "updated": updated,
        "url": url,
        "us_erp_history": history,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Scrape Damodaran historical US implied ERP.")
    ap.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output JSON path (default: implied_erp/data/histimpl_us_erp.json)",
    )
    ap.add_argument("--url", default=HISTIMPL_URL, help="Source URL")
    ap.add_argument("--updated", default="2026-01", help="Page date tag (e.g. 2026-01)")
    ap.add_argument("--dry-run", action="store_true", help="Parse + print, write nothing")
    ap.add_argument("--force", action="store_true", help="Re-scrape even if output exists")
    args = ap.parse_args()

    out_path = Path(args.out)

    if out_path.exists() and not args.force and not args.dry_run:
        print(f"[skip] {out_path} already exists (use --force to overwrite)", flush=True)
        return

    print(f"[scrape] fetching {args.url} ...", flush=True)
    result = scrape(url=args.url, updated=args.updated)

    n = len(result["us_erp_history"])
    first_year = min(k[:4] for k in result["us_erp_history"])
    last_year = max(k[:4] for k in result["us_erp_history"])
    print(
        f"[scrape] parsed {n} years ({first_year}–{last_year}); "
        f"last={result['us_erp_history'][f'{last_year}-01-01']:.4f}",
        flush=True,
    )

    if args.dry_run:
        print(json.dumps(result, indent=2)[:2000])
        return

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"[OK] wrote {out_path}", flush=True)


if __name__ == "__main__":
    main()
