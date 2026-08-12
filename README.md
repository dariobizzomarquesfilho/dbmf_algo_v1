# DBMF Quant

Quantitative trading system for the Dario Filho secondary portfolio. Three independent modules plus a Lean backtest migration.

## Project Structure

```
dbmf_quant/
├── lean_project/              # Lean backtest (primary deliverable)
│   ├── main.py                # PbRoeAtrAlgorithm — event-driven rebalancing
│   ├── lean.json              # Lean v2 config (organization, dates, data folder)
│   ├── data/                  # All data: embedded modules, equity zips, sources
│   │   ├── equity_bars.py     # Embedded daily bars (77 tickers, 751 bars each)
│   │   ├── damodaran_erp_json.py  # Embedded ERP data (175+ countries)
│   │   ├── fundamentals_json.py   # Embedded fundamentals (P/B, ROE, Beta, EPS)
│   │   ├── growth_cache.py         # Embedded growth rate cache
│   │   ├── bootstrap_data.py     # Writes CSV.zip files into Lean's data folder
│   │   ├── damodaran_erp.py      # DamodaranERP PythonData feed
│   │   ├── equity/           # Lean equity .zip files (daily bars)
│   │   └── alternative/      # Interest rate data (cosmetic — uses ^TNX directly)
│   ├── universe/
│   │   └── pb_roe_universe.py  # Fine selection: P/B vs ROE Gordon-growth screen
│   ├── indicators/
│   │   └── atr_trailing_stop.py # ATR trailing stop (SMA/EMA/WMA/RMA)
│   ├── valuation/
│   │   └── gordon_growth.py     # Intrinsic P/B (2-stage Gordon growth)
│   ├── scripts/
│   │   ├── embed_data.py      # Build script: JSON → embedded Python modules
│   │   ├── download_equity_data.py  # Fetch S&P 500 bars via yfinance
│   │   ├── download_edgartools_data.py  # TTM fundamentals + quarterly PIT history from SEC 10-Q filings
│   │   ├── compute_growth_cache.py  # Compute growth rates from fundamentals
│   │   └── convert_to_qc_format.py  # Convert data to QC zip format
│   ├── Lean/                  # QuantConnect Lean framework (git submodule)
│   └── .gitignore
├── implied_erp/               # Damodaran ERP extraction pipeline
│   ├── extract_damodaran_erp.py  # Parse ctryprem*.xlsx → structured JSON
│   ├── build_damodaran_erp.py    # Lightweight: country → Total Equity Risk Premium
│   ├── helper.py                 # yfinance index-level fetcher
│   ├── data/
│   │   └── july26.json           # Current processed ERP data (175+ countries)
│   └── README.md
├── pb_roe/                    # Standalone P/B vs ROE screener (pre-Lean)
│   ├── src/
│   │   ├── helpers.py            # intrinsic_pb(), capm(), risk_free_rate(), screen()
│   │   └── screener/
│   │       ├── damodaran.py      # ERP lookup by ticker/country
│   │       └── metrics.py        # P/B, ROE extraction + discrepancy check
│   └── CLAUDE.md
├── vol_trail_stop/            # ATR trailing stop (standalone)
│   ├── vol_trail_stop.py      # atr_calc(), atr_trail_stop() with SMA/EMA/WMA/RMA
│   └── README.md
├── backtest/                  # Old custom backtest engine (superseded by Lean)
├── notebooks/                 # Jupyter demos (P/B vs ROE, S&P 500)
├── config/
│   └── requirements.txt       # matplotlib, yfinance, pandas, openpyxl, numpy, edgar, python-dotenv
├── helper.py                  # Root-level yfinance helper
└── CLAUDE.md                  # Project guidance for Claude Code
```

## Modules

### Lean Backtest (Primary)

The `lean_project/` directory contains the QuantConnect Lean migration of the P/B vs ROE ATR trailing stop strategy.

**Strategy logic:**
1. Screen S&P 500 constituents using embedded yfinance data for P/B, ROE, Beta
2. Compute implied P/B via 2-stage Gordon growth model with CAPM cost of equity
3. Select stocks where implied P/B > actual P/B (undervalued)
4. Equal-weight positions (1/max_positions)
5. Exit positions when ATR trailing stop is breached
6. Filter out financials by sector keyword matching

**Key design decisions:**
- All data is embedded in Python modules (no external .csv.zip or .json files at runtime)
- Prices injected via `Security.SetMarketPrice()` to avoid Security.Price=0 bug
- Daily rebalance scheduled at `AfterMarketClose` (16:01) when all daily bars arrive
- ATR computation uses embedded bars dict (bypasses `algorithm.History()`)
- Risk-free rate from ^TNX embedded bars (not from interest-rate.csv)

### Implied ERP

Extracts country-level equity risk premiums from Damodaran's spreadsheet (`ctryprem*.xlsx`).

```powershell
# Full extraction (all fields)
python implied_erp/extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx" --out "implied_erp/data/july26.json"

# Lightweight (country → Total Equity Risk Premium only)
python implied_erp/build_damodaran_erp.py
```

### Damodaran ERP PIT Pipeline (for Lean backtest)

Gives the Lean backtest access to historical Damodaran ERP as a point-in-time series (no look-ahead bias).

```powershell
# 1. Download archive .xls files (2001-2025) + current 2026 .xlsx
python implied_erp/scripts/download_damodaran_erp.py --dry-run   # preview
python implied_erp/scripts/download_damodaran_erp.py             # download

# 2. Extract all into per-period JSONs
python implied_erp/scripts/extract_all_damodaran_erp.py

# 3. Build Lean-compatible PIT history
python implied_erp/scripts/build_lean_erp_history.py

# 4. Embed into Lean
cd lean_project && python scripts/embed_data.py
```

### P/B vs ROE Screener (Standalone)

The `pb_roe/` module provides the same Gordon-growth valuation as the Lean algorithm but as a standalone Python script (no Lean dependency).

```powershell
cd pb_roe && python src/helpers.py    # Interactive: ticker + growth rate
python pb_roe/src/screener/metrics.py AAPL  # P/B, ROE, discrepancy report
```

### Volatility Trailing Stop

ATR-based trailing stop with multiple smoothing modes (SMA/EMA/WMA/RMA).

```powershell
python vol_trail_stop/vol_trail_stop.py  # Interactive: ticker, period, multiplier
```

## Setup

```powershell
# Activate virtual environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r config/requirements.txt
```

## Running the Lean Backtest

```powershell
cd lean_project

# Run backtest with Lean CLI
lean backtest

# Or specify config explicitly
lean backtest --config lean.json
```

The backtest uses embedded data — no external data files are needed at runtime. The `bootstrap_data.py` script writes CSV.zip files to Lean's data folder on each run.

### Regenerating Embedded Data

If you need to refresh the embedded data (e.g., new fundamentals, updated bars):

```powershell
cd lean_project

# 1. Download TTM fundamentals + quarterly PIT history from SEC filings (edgartools)
python scripts/download_edgartools_data.py

#    Use --snapshot-only to skip quarterly history (faster for testing)
#    Use --backtest-start to control how far back history goes (default: 2020-01-01)

# 2. Download fresh equity data (yfinance)
python scripts/download_equity_data.py

# 3. Compute growth cache
python scripts/compute_growth_cache.py

# 4. Convert to QC format
python scripts/convert_to_qc_format.py

# 5. Embed everything into Python modules
python scripts/embed_data.py
```

## Requirements

- Python 3.11+
- Windows 11 (PowerShell)
- Docker (for Lean backtesting)
- QuantConnect Lean CLI v1.0.227+

## Known Issues

1. **Interest rate CSV**: `data/alternative/interest-rate/usa/interest-rate.csv` has a date format Lean can't parse. Cosmetic only — the strategy uses ^TNX from embedded bars for the risk-free rate.
2. **82% max drawdown**: Strategy design concern, not a bug.
3. **No test suite**: No automated tests exist yet.
