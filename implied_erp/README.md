# Damodaran ERP Extraction Pipeline

Extracts country-level equity risk premiums (ERP) from Damodaran's NYU Stern spreadsheet (`ctryprem*.xlsx`) and builds a point-in-time (PIT) history for use in the Lean backtest.

## Modules

### 1. Full Extraction (`extract_damodaran_erp.py`)

Parses the `ctryprem*.xlsx` sheet "ERPs by country" and extracts all fields into structured JSON.

**Usage:**
```bash
# Save to JSON file
python extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx" --out "output.json"

# Display in terminal (formatted JSON)
python extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx"
```

**Output fields:**
- **Metadata:** update date, mature market ERP, US ERP
- **Regular countries:** Moody's rating, default spread, total equity risk premium, country risk premium, sovereign CDS, alternative CDS-based ERPs
- **Frontier markets:** PRS score, ERP, CRP, default spread (no Moody's rating)

### 2. PIT Pipeline Scripts (`scripts/`)

Builds a historical point-in-time ERP series for the Lean backtest, so the correct ERP is used at each rebalance date with no look-ahead bias.

```powershell
# Step 1: Download Damodaran archive files (2001-2025 .xls + 2026 .xlsx)
python scripts/download_damodaran_erp.py --dry-run   # preview links
python scripts/download_damodaran_erp.py             # download

# Step 2: Extract all files into per-period JSONs
python scripts/extract_all_damodaran_erp.py

# Step 3: Build Lean-compatible PIT history (US + mature-market ERP only)
python scripts/build_lean_erp_history.py

# Step 4: Embed into Lean Python module
cd lean_project && python scripts/embed_data.py
```

**Data locations:**
- Downloaded `.xls`/`.xlsx` files -> `implied_erp/data/raw/` (gitignored)
- Per-period extracted JSONs -> `implied_erp/data/erp/erp_*.json`
- Lean PIT history -> `lean_project/data/damodaran_erp_history.json`

### Helper (`helper.py`)

Utility for fetching index-level data via yfinance. Used by other modules in the pipeline.

## Dependencies

- **openpyxl** — Excel file processing (`.xlsx`)
- **xlrd** — Legacy Excel file processing (`.xls` archive files)
- **requests** — HTTP download for archive files
