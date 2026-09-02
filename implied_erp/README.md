# Damodaran ERP Extraction Pipeline

Extraction of country-level equity risk premiums from Damodaran NYU Stern workbooks (`ctryprem*.xlsx` and archive `.xls`) and construction of a point-in-time history for the Lean backtest.

## Module 1: Single-Workbook Extraction

Script: `extract_damodaran_erp.py`

Parses the workbook sheet `ERPs by country` and extracts structured fields into JSON.

Usage:

```bash
# Write to JSON file
python extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx" --out "output.json"

# Write formatted JSON to standard output
python extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx"
```

Output fields:

- **Metadata.** Update date, mature market ERP, US ERP.
- **Country entries.** Moody's rating, default spread, total equity risk premium, country risk premium, sovereign CDS, alternative CDS-based ERPs.
- **Frontier markets.** PRS score, ERP, CRP, default spread (no Moody's rating).

## Module 2: Point-in-Time Pipeline

Scripts: `scripts/`

Constructs a historical point-in-time ERP series so that each backtest rebalance date uses the ERP available at that date. No forward-looking data is accessed.

```powershell
# Step 1: Download Damodaran archive files (2001 to 2025 .xls and 2026 .xlsx)
python scripts/download_damodaran_erp.py --dry-run   # preview URLs without downloading
python scripts/download_damodaran_erp.py             # download

# Step 2: Extract archive files into per-period JSON
python scripts/extract_all_damodaran_erp.py

# Step 3: Build Lean-compatible point-in-time history (US and mature-market ERP)
python scripts/build_lean_erp_history.py

# Step 4: Embed into Lean Python module
cd lean_project; python scripts/embed_data.py
```

Data locations:

- Downloaded `.xls` and `.xlsx` files: `implied_erp/data/raw/` (not tracked)
- Per-period extracted JSON: `implied_erp/data/erp/erp_*.json`
- Lean point-in-time history: `lean_project/data/damodaran_erp_history.json`

Refer to `lean_project/README.md` and `docs/data-limitations.md` for point-in-time resolution rules (`universe/pit_data.py:resolve_erp_as_of`, path:line `lean_project/universe/pit_data.py:144`).

## Dependencies

| Package | Purpose |
|---------|---------|
| `openpyxl` | Excel `.xlsx` processing |
| `xlrd` | Legacy `.xls` archive processing |
| `requests` | HTTP download for archive files |
