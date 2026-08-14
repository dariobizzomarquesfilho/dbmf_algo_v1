"""Batch-extract all Damodaran raw files into per-period JSONs.

Runs extract_damodaran_erp.extract() over every .xls/.xlsx in the raw dir
and writes one JSON per period to the erp/ directory.

Usage:
    python extract_all_damodaran_erp.py
    python extract_all_damodaran_erp.py --raw-dir implied_erp/data/raw
    python extract_all_damodaran_erp.py --erp-dir implied_erp/data/erp
    python extract_all_damodaran_erp.py --force   # re-extract all
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

# Add repo root to path so `implied_erp` imports work
_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from implied_erp.extract_damodaran_erp import extract

DEFAULT_RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
DEFAULT_ERP_DIR = Path(__file__).resolve().parent.parent / "data" / "erp"


_MONTH_MAP = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _date_key_from_filename(basename: str) -> str | None:
    """Derive a YYYY-MM-DD date key from a Damodaran filename.

    Patterns handled:
      ctryprem00.xls  → 2001-01-01
      ctryprem24.xls  → 2025-01-01
      ctryprem.xlsx   → None (no year in name; use updated cell)
      ctrypremApr26.xlsx → 2026-04-01
      ctrypremJuly26.xlsx → 2026-07-01
    """
    stem = Path(basename).stem

    # ctrypremNN.xls → year 2001 + NN.  The NN is an OFFSET FROM 2001: the file
    # published on Jan 1 of (2001+NN) and is used during that year.  So
    # ctryprem00.xls = 2001, ctryprem24.xls = 2025.  The embedded 'Date of
    # update' cell is only the in-year publication date and is NOT used for
    # anchoring (see _resolve_date).
    m = re.match(r"ctryprem(\d{2})\.xls", basename, re.IGNORECASE)
    if m:
        year = 2001 + int(m.group(1))
        return f"{year}-01-01"

    # ctrypremMMMYY.xlsx → YYYY-MM-01  (e.g. Apr26 → 2026-04-01, July26 → 2026-07-01)
    m = re.match(r"ctryprem([A-Za-z]{3,4})(\d{2})\.xlsx", basename, re.IGNORECASE)
    if m:
        month_str = m.group(1).lower()[:3]
        month = _MONTH_MAP.get(month_str)
        if month is not None:
            year = 2000 + int(m.group(2))
            return f"{year}-{month:02d}-01"

    # ctryprem.xlsx (current/latest) — no year in filename, rely on cell
    return None


def _resolve_date(raw_path: Path, erp_data: dict) -> str:
    """Resolve a date key for a raw file.

    Anchoring model (per Damodaran's publication convention):
      - ctrypremNN.xls  → use the filename year 2000 + NN.  This file IS the
        year-NN ERP; its embedded 'Date of update' cell is only the in-year
        publication date and must NOT be used for anchoring.
      - ctrypremMMMYY.xlsx (mid-year updates) → use the embedded 'Date of
        update' cell (the real update date), falling back to the filename
        month map.  These are left untouched / anchored at their true date.
      - ctryprem.xlsx (current year) → no year in name; use the embedded cell.
      - last resort: file mtime.
    """
    fb = _date_key_from_filename(raw_path.name)
    # Archive .xls: the filename year is authoritative — ignore the embedded cell.
    if fb and raw_path.suffix.lower() == ".xls":
        return fb

    # Otherwise (any .xlsx) prefer the embedded 'Date of update' cell.
    updated = erp_data.get("updated")
    if updated:
        if " " in updated:  # openpyxl "2026-07-09 00:00:00"
            updated = updated.split(" ")[0]
        if len(updated) == 10 and updated[4] == "-" and updated[7] == "-":
            return updated

    # Filename fallback (covers ctrypremMMMYY.xlsx when the cell is missing).
    if fb:
        return fb

    # Last resort: file mtime as YYYY-MM-DD
    mtime = raw_path.stat().st_mtime
    from datetime import datetime, timezone

    return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def _get_us_erp(data: dict) -> float | None:
    """Get US ERP from country-level data, falling back to metadata."""
    us_erp = data.get("us_erp")
    if us_erp is not None:
        return us_erp
    countries = data.get("countries", {})
    for us_key in ("United States", "United States of America"):
        us = countries.get(us_key)
        if isinstance(us, dict):
            for field in ("total_equity_risk_premium2", "total_equity_risk_premium"):
                val = us.get(field)
                if val is not None and isinstance(val, (int, float)):
                    return float(val)
    return data.get("mature_market_erp")


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Batch-extract Damodaran raw files into per-period JSONs"
    )
    ap.add_argument(
        "--raw-dir",
        default=str(DEFAULT_RAW_DIR),
        help="Directory with downloaded raw .xls/.xlsx files",
    )
    ap.add_argument(
        "--erp-dir",
        default=str(DEFAULT_ERP_DIR),
        help="Output directory for per-period JSONs",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-extract all files even if output already exists",
    )
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    erp_dir = Path(args.erp_dir)
    erp_dir.mkdir(parents=True, exist_ok=True)

    if not raw_dir.exists():
        print(
            f"[error] raw dir does not exist: {raw_dir}",
            file=sys.stderr,
        )
        print("  Run download_damodaran_erp.py first.", file=sys.stderr)
        sys.exit(1)

    # Collect all .xls and .xlsx files
    raw_files = sorted(
        list(raw_dir.glob("*.xls")) + list(raw_dir.glob("*.xlsx"))
    )

    if not raw_files:
        print(f"[warn] No .xls/.xlsx files found in {raw_dir}", file=sys.stderr)
        return

    print(
        f"[extract_all] {len(raw_files)} files in {raw_dir}",
        file=sys.stderr,
    )

    extracted = 0
    skipped = 0
    failed = 0

    for raw_path in raw_files:
        # Output filename: erp_<YYYY-MM-DD>.json
        # We don't know the date yet — extract first, then resolve.
        # For efficiency, check if output exists first (using filename heuristic).
        date_key = _date_key_from_filename(raw_path.name)
        if date_key is None:
            # For files like ctryprem.xlsx, we need to extract first to get the date.
            # Extract to a temp name, then rename.
            pass

        # Determine output path
        if date_key:
            out_name = f"erp_{date_key}.json"
        else:
            # Extract first to get the updated date, then name properly
            try:
                data = extract(str(raw_path))
                resolved = _resolve_date(raw_path, data)
                out_name = f"erp_{resolved}.json"
            except Exception as e:
                print(f"[error] extracting {raw_path.name}: {e}", file=sys.stderr)
                failed += 1
                continue

        out_path = erp_dir / out_name

        if out_path.exists() and not args.force:
            print(f"[skip] {out_name} (already exists)", file=sys.stderr)
            skipped += 1
            continue

        try:
            data = extract(str(raw_path))

            # If we already extracted above (no date_key), we have the data.
            # If we haven't, extract now.
            if date_key is None:
                resolved = _resolve_date(raw_path, data)
                out_name = f"erp_{resolved}.json"
                out_path = erp_dir / out_name

            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            n_countries = len(data.get("countries", {}))
            print(
                f"[OK] {out_name}  ({n_countries} countries, "
                f"us_erp={data.get('us_erp')}, "
                f"source={data.get('source')})",
                file=sys.stderr,
            )
            extracted += 1
        except Exception as e:
            print(f"[error] {raw_path.name}: {e}", file=sys.stderr)
            failed += 1

        time.sleep(0.2)

    # Write index
    index_path = erp_dir / "_index.json"
    entries = []
    for f in sorted(erp_dir.glob("erp_*.json")):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
            # The JSON filename (erp_YYYY-MM-DD.json) already encodes the
            # resolved date — use it directly.  _resolve_date is for RAW files
            # and would fall back to mtime here (wrong for .xls archives).
            stem = f.stem
            if stem.startswith("erp_"):
                date_key = stem[len("erp_"):]
            else:
                date_key = _resolve_date(f, d)
            entries.append(
                {
                    "date": date_key,
                    "path": f.name,
                    "source": d.get("source"),
                    "countries": len(d.get("countries", {})),
                    "us_erp": _get_us_erp(d),
                    "mature_market_erp": d.get("mature_market_erp"),
                }
            )
        except Exception:
            continue

    index_path.write_text(
        json.dumps(entries, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(
        f"[done] extracted={extracted} skipped={skipped} failed={failed} "
        f"index written to {index_path}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
