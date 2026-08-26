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
│   ├── README.md
│   ├── data/
│   │   ├── erp/                  # Per-period extracted JSONs (2013-2026)
│   │   │   ├── _index.json
│   │   │   └── erp_*.json
│   │   └── raw/                  # Downloaded .xls/.xlsx (gitignored)
│   └── scripts/
│       ├── download_damodaran_erp.py  # Download archive .xls/.xlsx via HTTP
│       ├── extract_all_damodaran_erp.py  # Batch extract all periods
│       ├── scrape_histimpl.py            # Historical US implied ERP (histimpl)
│       └── build_lean_erp_history.py     # Build Lean-compatible PIT ERP history
└── lean_project/               # QuantConnect Lean backtest (primary deliverable)
    ├── main.py                 # PbRoeAtrAlgorithm — event-driven rebalancing
    ├── lean.json               # Lean v2 config (organization, dates, data folder)
    ├── README.md               # Lean-specific documentation
    ├── data/
    │   ├── equity_bars.py      # Embedded daily bars (~790 tickers × ~1,897 bars)
    │   ├── equity_bars.json    # Source bars data (for regeneration)
    │   ├── damodaran_erp_history.py  # Embedded US ERP PIT series (2001-2026)
    │   ├── damodaran_erp_history.json  # Source PIT ERP history
    │   ├── fundamentals_history.py  # Embedded quarterly PIT fundamentals (TTM)
    │   ├── fundamentals_history.json  # Source quarterly PIT history (edgartools)
    │   ├── backtest_config.py    # Embedded backtest window (from config/.env)
    │   ├── bootstrap_data.py     # Writes CSV.zip files into Lean's data folder
    │   ├── sp500_data.py         # S&P 500 PIT membership utilities
    │   ├── corporate_actions.py  # Curated S&P 500 spinoffs
    │   ├── exclusions.py         # Aggregate excluded tickers (broken, missing, documented)
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
    │   ├── build_cik_map.py            # Ticker → CIK map (edgar self-bootstrap)
    │   ├── common.py                   # Shared download/repair helpers
    │   └── track_exclusions.py         # Track excluded tickers and reasons
    ├── tests/                    # pytest test suite
    │   ├── conftest.py
    │   ├── test_embed.py
    │   ├── test_equity_completeness.py
    │   ├── test_erp_pit.py
    │   ├── test_eps_growth.py
    │   ├── test_bugfix_audit.py
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
6. Filter out financials using edgar's native SIC/business classification

**Key design decisions:**
- All data is embedded in Python modules (no external .csv.zip or .json files at runtime)
- Prices injected via `Security.SetMarketPrice()` to avoid Security.Price=0 bug
- Daily rebalance triggered from `OnData` (one cycle per new trading day, after the daily bar arrives); `CoarseSelection`/`FineSelection` Lean hooks are not used
- S&P 500 membership is point-in-time: members active at the start date are pre-subscribed, later additions are `AddEquity()`-ed on their index-add date via `data.sp500_data.intervals_active` (long-dead historical members are never subscribed)
- ATR computation uses embedded bars dict (bypasses `algorithm.History()`)
- Risk-free rate from ^TNX embedded bars, point-in-time as-of the rebalance date (not from interest-rate.csv)
- Financial sector excluded via edgar native classification (SIC/business category)
- Fundamentals use TTM from SEC 10-Q filings (edgartools)
- PIT quarterly fundamentals used when available; tickers without quarterly coverage at a date are skipped (no static snapshot fallback — that would be look-ahead bias)
- Backtest window is single source of truth: `config/.env` → `config/config.py` → `data/backtest_config.py` → `lean.json`

### Implied ERP

Extracts country-level equity risk premiums from Damodaran's spreadsheet (`ctryprem*.xlsx`) into structured JSON. (The former lightweight `build_damodaran_erp.py` static snapshot was removed — the Lean backtest now uses only the point-in-time ERP history described below.)

```powershell
# Full extraction (all fields)
python implied_erp/extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx" --out "output.json"
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

The backtest window is configured in `config/.env` via `BACKTEST_START`/`BACKTEST_END` (propagated through `config/config.py` → `data/backtest_config.py` → `lean.json` by `scripts/embed_data.py`; re-run embed after any `.env` change) with $100,000 initial capital. Equity bar data must additionally cover a warm-up window before `BACKTEST_START` (>= `BACKTEST_WARMUP_DAYS` trading days, default 252) so rolling indicators like beta/ATR have enough prior bars. `scripts/download_equity_data.py` pulls from `config.HISTORY_START` through `config.BACKTEST_END`; `scripts/embed_data.py` hard-fails if coverage is missing.

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
- QuantConnect Lean CLI v1.0.228 (pinned in `config/requirements.txt`; `pip install -r config/requirements.txt` restores it)

## Known Issues

1. **Interest rate CSV**: `data/alternative/interest-rate/usa/interest-rate.csv` had dates in `YYYYMMDD` format which Lean couldn't parse. Fixed by converting to `YYYY-MM-DD`. The strategy uses ^TNX from embedded bars for the risk-free rate, so this file is cosmetic only.
2. **82% max drawdown**: Strategy design concern, not a bug.
3. **No linter or formatter configured**: No `flake8`, `black`, `ruff`, or `pylint` configuration (a pytest suite exists under `lean_project/tests` and `implied_erp/tests`).
4. **yfinance negative bookValue**: yfinance returns negative `bookValue` and `priceToBook` for ~33 S&P 500 tickers (SBUX, MCD, ABBV, LOW, etc.). These tickers are skipped per-screen when book_value is invalid — they are not dropped from the cache and may have valid book_value in a future quarter after refresh. P/B is always computed dynamically as `current_price / book_value` for the correct time period.
5. **PIT data coverage depth varies by ticker**: `fundamentals_history.json` covers ~687 tickers via edgartools SEC XBRL, but XBRL facts only reach back to ~2009 and many later-added S&P 500 names only gained fundamentals ~2019, so early-window universes are thinner. When quarterly data is unavailable for a ticker at a given backtest date, the screen skips it (no static fallback — using current data for historical dates would be look-ahead bias). Run `python scripts/download_edgartools_data.py` periodically to accumulate new quarters via SEC filings.
6. **USD-only risk-free rate**: the CAPM cost of equity uses ^TNX (USD); non-USD listings are out of scope.
7. **Known bar-coverage gaps**: EA / FOX / FOXA / IR have incomplete or late-starting bars in the current `equity_bars.json`; `embed_data.py` reports them at embed time until a re-download repairs them.
