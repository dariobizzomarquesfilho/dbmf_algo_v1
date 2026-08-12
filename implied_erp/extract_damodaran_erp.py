"""Extract country-level ERP data from Damodaran's ctryprem*.xls/.xlsx spreadsheet.

Supports both modern .xlsx (openpyxl) and legacy .xls (xlrd) formats.

Usage:
    python extract_damodaran_erp.py --path "path/to/ctrypremJuly26.xlsx" --out "output.json"
    python extract_damodaran_erp.py --path "path/to/ctryprem00.xls" --out "output.json"
    python extract_damodaran_erp.py --path "path/to/ctrypremJuly26.xlsx"  # prints to stdout
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path
from typing import Iterator, Iterable

import openpyxl

# ── Constants ────────────────────────────────────────────────────────────────
SHEET_NAME = "ERPs by country"          # ← Change here if sheet name changes
COUNTRY_COL = 0                         # Column A (0-indexed)
DATA_START_ROW = 9                      # First data row (Excel row number, 1-based)
METADATA_MAX_ROW = 7                    # Last metadata row before headers

# Default output path: <script_dir>/data/july26.json
DEFAULT_OUT = Path(__file__).resolve().parent / "data" / "july26.json"

# Column indices for regular countries (0-indexed into the row tuple)
REGULAR_FIELDS = [
    ("region", 1),
    ("moody_rating", 2),
    ("rating_default_spread", 3),
    ("total_equity_risk_premium", 4),
    ("country_risk_premium", 5),
    ("sovereign_cds_net", 6),
    ("total_equity_risk_premium2", 7),
    ("country_risk_premium3", 8),
]

# Column indices for frontier markets (0-indexed)
FRONTIER_FIELDS = [
    ("prs_score", 1),
    ("erp", 2),
    ("crp", 3),
    ("default_spread", 4),
]

# Fields that should remain as strings (not converted to float)
REGULAR_STRING_FIELDS = {"region", "moody_rating"}


# ── Row-source abstraction ──────────────────────────────────────────────────

class _SheetRows:
    """Minimal row-source protocol for the extractor.

    Both backends expose .iter_rows(min_row, max_row, values_only=True)
    yielding tuples of cell values.  The abstraction hides engine-specific
    differences (0-based vs 1-based indexing, empty-cell representation).
    """

    def iter_rows(
        self,
        min_row: int = 1,
        max_row: int | None = None,
        values_only: bool = True,
    ) -> Iterator[tuple]:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError


class _OpenpyxlSheet(_SheetRows):
    """Wraps an openpyxl worksheet."""

    def __init__(self, ws) -> None:
        self._ws = ws

    @property
    def name(self) -> str:
        return self._ws.title

    def iter_rows(
        self,
        min_row: int = 1,
        max_row: int | None = None,
        values_only: bool = True,
    ) -> Iterator[tuple]:
        return self._ws.iter_rows(
            min_row=min_row,
            max_row=max_row,
            values_only=values_only,
        )


class _XlrdSheet(_SheetRows):
    """Wraps an xlrd sheet.  Rows are 0-based; pads ragged rows to a uniform width."""

    def __init__(self, sheet, book) -> None:
        self._sheet = sheet
        self._book = book
        self._ncols = sheet.ncols

    @property
    def name(self) -> str:
        return self._sheet.name

    def iter_rows(
        self,
        min_row: int = 1,
        max_row: int | None = None,
        values_only: bool = True,
    ) -> Iterator[tuple]:
        # xlrd rows are 0-based; min_row/max_row are 1-based (Excel convention)
        lo = max(0, min_row - 1)
        hi = (
            self._sheet.nrows
            if max_row is None
            else min(max_row, self._sheet.nrows)
        )
        for r in range(lo, hi):
            vals = list(self._sheet.row_values(r))
            # Pad ragged rows so all tuples have the same length
            if len(vals) < self._ncols:
                vals += [None] * (self._ncols - len(vals))
            yield tuple(vals)


def _open_sheet(path: Path) -> tuple[_SheetRows, str]:
    """Dispatch by file extension to the appropriate engine."""
    ext = path.suffix.lower()
    if ext == ".xls":
        import xlrd

        book = xlrd.open_workbook(str(path))
        try:
            sheet = book.sheet_by_name(SHEET_NAME)
        except xlrd.biffh.XLRDError:
            available = book.sheet_names()
            raise ValueError(
                f"Worksheet '{SHEET_NAME}' not found in {path}. "
                f"Sheets available: {available}"
            )
        return _XlrdSheet(sheet, book), "xlrd"

    # Default: treat as .xlsx (openpyxl)
    wb = openpyxl.load_workbook(str(path), data_only=True)
    if SHEET_NAME not in wb.sheetnames:
        raise ValueError(
            f"Worksheet '{SHEET_NAME}' not found in {path}. "
            f"Sheets available: {wb.sheetnames}"
        )
    return _OpenpyxlSheet(wb[SHEET_NAME]), "openpyxl"


def _cell_to_date_str(value, book=None) -> str | None:
    """Convert a date cell value to 'YYYY-MM-DD'.

    Handles openpyxl datetime objects and xlrd float serial dates.
    """
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, float) and book is not None:
        try:
            import xlrd as _xlrd

            y, m, d, _, _ = _xlrd.xldate.xldate_as_tuple(value, book.datemode)
            return datetime(y, m, d).strftime("%Y-%m-%d")
        except Exception:
            return None
    return None


def _to_float_or_none(value) -> float | None:
    """Convert value to float, returning None for NA/#N/A/empty."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip().upper() in ("NA", "#N/A", "N/A", ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _extract_regular_countries(rows: _SheetRows) -> dict[str, dict]:
    """Extract regular (rated) countries."""
    countries = {}
    for row in rows.iter_rows(min_row=DATA_START_ROW, values_only=True):
        first_col = row[COUNTRY_COL]

        # Stop if empty row (footer territory)
        if first_col is None or (isinstance(first_col, str) and not first_col.strip()):
            break

        # Stop if we hit the frontier markets section
        if isinstance(first_col, str) and "Frontier Markets" in first_col:
            break

        # Skip non-country rows
        if not isinstance(first_col, str):
            continue

        country_name = first_col.strip()
        entry = {"is_frontier": False}

        for field_name, col_idx in REGULAR_FIELDS:
            if col_idx < len(row):
                raw = row[col_idx]
                if field_name in REGULAR_STRING_FIELDS:
                    if raw is None:
                        entry[field_name] = None
                    elif isinstance(raw, str) and raw.strip().upper() in ("NA", "#N/A", "N/A", ""):
                        entry[field_name] = None
                    else:
                        entry[field_name] = str(raw).strip()
                else:
                    entry[field_name] = _to_float_or_none(raw)
            else:
                entry[field_name] = None

        countries[country_name] = entry

    return countries


def _extract_frontier_countries(rows: _SheetRows) -> dict[str, dict]:
    """Extract frontier markets (after the 'Frontier Markets' section header)."""
    countries = {}
    in_frontier = False
    header_found = False

    for row in rows.iter_rows(min_row=DATA_START_ROW, values_only=True):
        first_col = row[COUNTRY_COL]

        # Detect the frontier markets section header
        if isinstance(first_col, str) and "Frontier Markets" in first_col:
            in_frontier = True
            continue

        if not in_frontier:
            continue

        # Skip the header row of the frontier section
        if not header_found:
            header_found = True
            continue

        # Stop when we hit an empty row (footer starts after frontier section)
        if first_col is None or (isinstance(first_col, str) and not first_col.strip()):
            break

        if not isinstance(first_col, str):
            continue

        country_name = first_col.strip()
        entry = {"is_frontier": True}

        for field_name, col_idx in FRONTIER_FIELDS:
            if col_idx < len(row):
                entry[field_name] = _to_float_or_none(row[col_idx])
            else:
                entry[field_name] = None

        countries[country_name] = entry

    return countries


def _extract_metadata(rows: _SheetRows, book=None) -> tuple[str | None, float | None, float | None]:
    """Extract metadata from header rows (1-7)."""
    mature_market_erp = None
    us_erp = None
    updated = None

    for row in rows.iter_rows(min_row=1, max_row=METADATA_MAX_ROW, values_only=True):
        if not row or row[0] is None:
            continue
        first = str(row[0]).strip()

        if "Date of update:" in first and len(row) > 1:
            val = row[1]
            if val is not None:
                # Try xlrd serial date conversion first, then str fallback
                date_str = _cell_to_date_str(val, book)
                updated = date_str if date_str is not None else str(val)

        if "Enter the current risk premium for a mature equity" in first and len(row) > 4:
            mature_market_erp = _to_float_or_none(row[4])

        if "Enter the current risk premium for the US" in first and len(row) > 4:
            us_erp = _to_float_or_none(row[4])

    return updated, mature_market_erp, us_erp


def extract(path: str) -> dict:
    """Read the spreadsheet and return structured JSON-compatible dictionary.

    Works with both .xlsx (openpyxl) and .xls (xlrd) formats.
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {path}")

    rows, engine = _open_sheet(p)

    book = None
    if engine == "xlrd" and hasattr(rows, "_book"):
        book = rows._book

    updated, mature_market_erp, us_erp = _extract_metadata(rows, book=book)

    # Extract both sections
    countries = {}
    countries.update(_extract_regular_countries(rows))
    countries.update(_extract_frontier_countries(rows))

    if not countries:
        raise ValueError(f"Nenhum país encontrado na aba '{SHEET_NAME}' de {path}")

    return {
        "source": p.name,
        "updated": updated,
        "mature_market_erp": mature_market_erp,
        "us_erp": us_erp,
        "countries": countries,
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Extrai dados de ERP por país da planilha Damodaran ctryprem*.xls/.xlsx"
    )
    ap.add_argument(
        "--path",
        "--xlsx",
        dest="path",
        required=True,
        help="Caminho para o arquivo ctryprem*.xls ou ctryprem*.xlsx",
    )
    ap.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Caminho do arquivo JSON de saída (padrão: data/july26.json)",
    )
    args = ap.parse_args()

    data = extract(args.path)

    json_str = json.dumps(data, indent=2, ensure_ascii=False)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json_str, encoding="utf-8")
        print(f"[OK] Dados salvos em: {out_path}")
        print(f"     Países: {len(data['countries'])}")
        print(f"     Fonte:  {data['source']}")
    else:
        print(json_str)


if __name__ == "__main__":
    main()
