# DBMF Quant

Quantitative trading system for the Dario Filho secondary portfolio. Primary deliverable is a QuantConnect Lean backtest of a P/B vs ROE ATR trailing stop strategy, supported by an implied equity risk premium (ERP) extraction pipeline and standalone utility modules.

## Project Structure

```
dbmf_quant_v2/
├── README.md                   # This file
├── CLAUDE.md                   # Project guidance for Claude Code
├── .gitignore
├── config/
│   ├── __init__.py
│   ├── config.py               # Env-driven backtest window, warm-up, history start
│   ├── .env                    # SEC_USER, BACKTEST_START, BACKTEST_END (gitignored)
│   └── requirements.txt        # Python dependencies
├── docs/
│   └── superpowers/
│       ├── plans/               # Implementation plans
│       └── specs/               # Design specs
├── implied_erp/                # Damodaran ERP extraction pipeline
│   ├── extract_damodaran_erp.py  # Full extraction (.xlsx → structured JSON)
│   ├── build_damodaran_erp.py    # Lightweight: country → Total Equity Risk Premium
│   ├── helper.py                 # yfinance index-level fetcher
│   ├── README.md
│   ├── data/
│   │   ├── erp/                  # Per-period extracted JSONs (2013-2026)
│   │   │   ├── _index.json
│   │   │   └── erp_*.json
│   │   └── raw/                  # Downloaded .xls/.xlsx (gitignored)
│   └── scripts/
│       ├── download_damodaran_erp.py  # Download archive .xls/.xlsx via HTTP
│       ├── extract_all_damodaran_erp.py  # Batch extract all periods
│       └── build_lean_erp_history.py     # Build Lean-compatible PIT ERP history
└── lean_project/               # QuantConnect Lean backtest (primary deliverable)
    ├── main.py                 # PbRoeAtrAlgorithm — event-driven rebalancing
    ├── lean.json               # Lean v2 config (organization, dates, data folder)
    ├── README.md               # Lean-specific documentation
    ├── data/
    │   ├── equity_bars.py      # Embedded daily bars (~790 tickers × ~1,897 bars)
    │   ├── equity_bars.json    # Source bars data (for regeneration)
    │   ├── damodaran_erp_json.py  # Embedded ERP data (175+ countries)
    │   ├── damodaran_erp.json    # Source ERP data (static snapshot)
    │   ├── damodaran_erp_history.py  # Embedded US ERP PIT series (2001-2026)
    │   ├── damodaran_erp_history.json  # Source PIT ERP history
    │   ├── fundamentals_history.py  # Embedded quarterly PIT fundamentals (TTM)
    │   ├── fundamentals_history.json  # Source quarterly PIT history
    │   ├── fundamentals.json     # Latest fundamentals snapshot
    │   ├── backtest_config.py    # Embedded backtest window (from config/.env)
    │   ├── bootstrap_data.py     # Writes CSV.zip files into Lean's data folder
    │   ├── damodaran_erp.py      # DamodaranERP PythonData feed
    │   ├── sp500_data.py         # S&P 500 PIT membership utilities
    │   ├── corporate_actions.py  # Curated S&P 500 spinoffs
    │   ├── exclusions.py         # Aggregate excluded tickers (broken, missing, throttled)
    │   ├── delisted_aliases.py   # Delisted ticker alias mappings
    │   ├── bar_quality.py        # Bar-quality gate (impossible OHLC, zero prices)
    │   ├── sp500_ticker_start_end.csv  # S&P 500 membership with start/end dates
    │   ├── equity/               # Lean equity .zip files + map_files (daily bars)
    │   └── alternative/
    │       └── interest-rate/usa/interest-rate.csv  # Cosmetic (strategy uses ^TNX)
    ├── universe/
    │   ├── pb_roe_universe.py   # Fine selection: P/B vs ROE Gordon-growth screen
    │   └── pit_data.py           # Point-in-time fundamental + rolling-beta helpers
    ├── indicators/
    │   └── atr_trailing_stop.py  # ATR trailing stop (SMA/EMA/WMA/RMA)
    ├── valuation/
    │   └── gordon_growth.py      # Intrinsic P/B (2-stage Gordon growth)
    ├── scripts/
    │   ├── embed_data.py         # Build: JSON → embedded Python modules (zlib+base64)
    │   ├── download_equity_data.py  # Fetch S&P 500 bars via yfinance
    │   ├── download_edgartools_data.py  # TTM fundamentals + quarterly PIT from SEC 10-Q
    │   ├── convert_to_qc_format.py     # Convert data to QC zip format
    │   ├── repair_equity_data.py       # Repair/fix equity bar data
    │   ├── fetch_missing_delisted.py   # Fetch missing/delisted ticker data
    │   └── track_exclusions.py         # Track excluded tickers and reasons
    ├── tests/                    # pytest test suite
    │   ├── conftest.py
    │   ├── test_embed.py
    │   ├── test_equity_completeness.py
    │   ├── test_erp_pit.py
    │   ├── test_eps_growth.py
    │   └── test_pit_data.py
    └── Lean/                     # QuantConnect Lean framework
```

## Modules

### Lean Backtest (Primary)

The `lean_project/` directory contains the QuantConnect Lean migration of the P/B vs ROE ATR trailing stop strategy.

**Strategy logic:**
1. Screen S&P 500 constituents using embedded edgartools TTM fundamentals (from SEC 10-Q filings) + yfinance price data
2. Compute implied P/B via 2-stage Gordon growth model with CAPM cost of equity
3. Select stocks where implied P/B > actual P/B (undervalued)
4. Equal-weight positions (1/max_positions)
5. Exit positions when ATR trailing stop is breached
6. Filter out financials by sector keyword matching

**Key design decisions:**
- All data is embedded in Python modules (no external .csv.zip or .json files at runtime)
- Prices injected via `Security.SetMarketPrice()` to avoid Security.Price=0 bug
- Daily rebalance scheduled at `AfterMarketClose("AAPL", 1)` (16:01) when all daily bars arrive
- ATR computation uses embedded bars dict (bypasses `algorithm.History()`)
- Risk-free rate from ^TNX embedded bars (not from interest-rate.csv)
- Financial sector excluded via keyword matching on yfinance sector/industry fields
- Fundamentals use TTM from SEC 10-Q filings (edgartools)
- PIT quarterly history used when available; falls back to latest snapshot
- Backtest window is single source of truth: `config/.env` → `config/config.py` → `data/backtest_config.py` → `lean.json`

### Implied ERP

Extracts country-level equity risk premiums from Damodaran's spreadsheet (`ctryprem*.xlsx`).

```powershell
# Full extraction (all fields)
python implied_erp/extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx" --out "output.json"

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

The backtest runs from **2020-01-01 to 2026-08-01** (configured in `config/.env` via `BACKTEST_START`/`BACKTEST_END`, propagated through `config/config.py` → `data/backtest_config.py` → `lean.json`) with $100,000 initial capital. Equity bar data must additionally cover a warm-up window before `BACKTEST_START` (>= `BACKTEST_WARMUP_DAYS` trading days, default 252) so rolling indicators like beta/ATR have enough prior bars. `scripts/download_equity_data.py` pulls from `config.DATA_START` (warm-up) through `config.BACKTEST_END`; `scripts/embed_data.py` hard-fails if coverage is missing.

### Regenerating Embedded Data

If you need to refresh the embedded data (recommended quarterly since `book_value` changes every earnings report):

```powershell
cd lean_project

# 1. Download quarterly PIT fundamentals from SEC filings (edgartools)
python scripts/download_edgartools_data.py

#    Use --tickers AAPL MSFT GOOG to limit to specific tickers for testing
#    Use --backtest-start to control how far back history goes (default: config.DATA_START)

# 2. Download fresh equity bars from yfinance (full S&P 500 list + ^GSPC)
python scripts/download_equity_data.py

# 3. Convert to QC zip format (for bootstrap)
python scripts/convert_to_qc_format.py

# 4. Embed everything into Python modules
python scripts/embed_data.py
```

After step 4, commit the regenerated `data/*_json.py`, `data/*_bars.py`, `data/fundamentals_history.py`, and `data/damodaran_erp_history.py` files.

## Requirements

- Python 3.11+
- Windows 11 (PowerShell)
- Docker (for Lean backtesting)
- QuantConnect Lean CLI v1.0.227+

## Known Issues

1. **Interest rate CSV**: `data/alternative/interest-rate/usa/interest-rate.csv` had dates in `YYYYMMDD` format which Lean couldn't parse. Fixed by converting to `YYYY-MM-DD`. The strategy uses ^TNX from embedded bars for the risk-free rate, so this file is cosmetic only.
2. **82% max drawdown**: Strategy design concern, not a bug.
3. **No test suite**: No automated tests exist yet.
4. **No linter or formatter**: No `flake8`, `black`, `ruff`, or `pylint` configuration.
5. **yfinance negative bookValue**: yfinance returns negative `bookValue` and `priceToBook` for ~33 S&P 500 tickers (SBUX, MCD, ABBV, LOW, etc.). These tickers are skipped per-screen when book_value is invalid — they are not dropped from the cache and may have valid book_value in a future quarter after refresh. P/B is always computed dynamically as `current_price / book_value` for the correct time period.
6. **PIT data coverage gap**: `fundamentals_history.json` currently covers only 2 tickers. yfinance only returns the most recent ~7 quarters per ticker. When quarterly data is unavailable for a ticker at a given backtest date, the screen skips that ticker (no static fallback — using current data for historical dates would be look-ahead bias). Run `python scripts/download_edgartools_data.py` periodically to accumulate new quarters via SEC filings.
7. **`risk_free_rate()` only supports USD**: Non-US tickers will raise `ValueError`. The CAPM cost of equity `r` cannot be built for non-USD currencies.
