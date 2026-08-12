# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DBMF Quant is a Python-based quantitative trading system with a QuantConnect Lean backtest as the primary deliverable, plus a Damodaran ERP extraction pipeline.

1. **Lean Backtest** — P/B vs ROE ATR trailing stop strategy running on QuantConnect Lean
2. **Implied ERP** — extracts country-level equity risk premiums from Damodaran's spreadsheet
3. **Implied ERP PIT Pipeline** — builds point-in-time ERP history for no-look-ahead backtesting

There is no build system, test suite, or linter configured at the repo root. `lean_project/` has a pytest suite.

## Environment

- **Python 3.11+** virtualenv at `.venv` (Windows / PowerShell). Activate with `.\.venv\Scripts\Activate.ps1`.
- Dependencies in `config/requirements.txt`: `matplotlib`, `yfinance`, `pandas`, `openpyxl`, `xlrd>=2.0.1`, `numpy`, `edgar`, `python-dotenv`, `fredapi`, `requests`.
- Install: `pip install -r config/requirements.txt`
- **Docker** required for Lean backtesting (quantconnect/lean:foundation image).
- **QuantConnect Lean CLI** v1.0.227+ required.
- `.env` file required at `config/.env` with `SEC_USER` identity for edgartools and optional `BACKTEST_START`/`BACKTEST_END` overrides.

## Module Layout

```
dbmf_quant_v2/
├── README.md                     # Comprehensive project documentation
├── CLAUDE.md                     # This file
├── config/
│   ├── config.py                 # Env-driven backtest window, warm-up, history start
│   ├── .env                      # SEC_USER, dates (gitignored)
│   └── requirements.txt
├── implied_erp/                  # Damodaran ERP extraction pipeline
│   ├── extract_damodaran_erp.py    # Full extraction (.xlsx → structured JSON)
│   ├── build_damodaran_erp.py      # Lightweight flat extractor
│   ├── helper.py                   # yfinance index-level fetcher
│   ├── README.md
│   ├── scripts/                    # PIT pipeline scripts
│   │   ├── download_damodaran_erp.py
│   │   ├── extract_all_damodaran_erp.py
│   │   └── build_lean_erp_history.py
│   ├── data/
│   │   ├── erp/                    # Per-period extracted JSONs (2013-2026)
│   │   └── raw/                    # Downloaded .xls/.xlsx (gitignored)
│   └── README.md
├── lean_project/                 # Lean backtest (primary deliverable)
│   ├── main.py                    # PbRoeAtrAlgorithm
│   ├── lean.json                  # Lean v2 config
│   ├── README.md                  # Lean-specific documentation
│   ├── data/
│   │   ├── equity_bars.py         # Embedded daily equity bars (~790 tickers)
│   │   ├── equity_bars.json       # Source bars data
│   │   ├── damodaran_erp_json.py  # Embedded ERP (175+ countries)
│   │   ├── damodaran_erp.json     # Source ERP data (static snapshot)
│   │   ├── damodaran_erp_history.py  # Embedded US ERP PIT series
│   │   ├── damodaran_erp_history.json # Source PIT ERP history
│   │   ├── fundamentals_history.py # Embedded quarterly PIT fundamentals
│   │   ├── fundamentals_history.json # Source quarterly PIT history
│   │   ├── fundamentals.json      # Latest fundamentals snapshot
│   │   ├── backtest_config.py     # Embedded backtest window (from config/.env)
│   │   ├── bootstrap_data.py      # Writes CSV.zip into Lean data folder
│   │   ├── damodaran_erp.py       # DamodaranERP PythonData feed
│   │   ├── sp500_data.py          # S&P 500 PIT membership utilities
│   │   ├── corporate_actions.py   # Curated S&P 500 spinoffs
│   │   ├── exclusions.py          # Aggregate excluded tickers
│   │   ├── delisted_aliases.py    # Delisted ticker aliases
│   │   ├── bar_quality.py         # Bar-quality gate
│   │   ├── sp500_ticker_start_end.csv  # S&P 500 membership
│   │   ├── equity/                # Lean equity .zip files + map_files
│   │   └── alternative/           # Interest rate data (cosmetic)
│   ├── universe/
│   │   ├── pb_roe_universe.py     # Fine selection logic
│   │   └── pit_data.py            # PIT fundamental + rolling-beta helpers
│   ├── indicators/
│   │   └── atr_trailing_stop.py   # ATR trailing stop
│   ├── valuation/
│   │   └── gordon_growth.py       # Intrinsic P/B (2-stage Gordon growth)
│   ├── scripts/
│   │   ├── embed_data.py          # JSON → embedded Python modules
│   │   ├── download_equity_data.py
│   │   ├── download_edgartools_data.py
│   │   ├── convert_to_qc_format.py
│   │   ├── repair_equity_data.py
│   │   ├── fetch_missing_delisted.py
│   │   └── track_exclusions.py
│   ├── tests/                     # pytest test suite
│   └── Lean/                      # QuantConnect Lean framework
└── docs/
    └── superpowers/
        ├── plans/
        └── specs/
```

## Commands

```powershell
# Activate environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r config/requirements.txt

# ── Implied ERP ──
python implied_erp/extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx" --out "output.json"
python implied_erp/build_damodaran_erp.py

# ── Damodaran ERP PIT pipeline (for Lean backtest) ──
python implied_erp/scripts/download_damodaran_erp.py --dry-run   # preview
python implied_erp/scripts/download_damodaran_erp.py             # download
python implied_erp/scripts/extract_all_damodaran_erp.py
python implied_erp/scripts/build_lean_erp_history.py
cd lean_project && python scripts/embed_data.py

# ── Lean backtest ──
cd lean_project
lean backtest

# ── Regenerate embedded data for Lean ──
cd lean_project
python scripts/download_edgartools_data.py   # TTM fundamentals from SEC filings
python scripts/download_equity_data.py        # Equity bars + yfinance fallback
python scripts/convert_to_qc_format.py
python scripts/embed_data.py
```

## Architecture & Data Flow

### Lean Backtest Pipeline

```
Embedded Python modules (zlib+base64)
    │
    ├── equity_bars.py           → ~790 tickers × ~1,897 daily bars
    ├── damodaran_erp_json.py    → 175+ countries ERP
    ├── fundamentals_history.py  → quarterly PIT book_value / roe / eps / g_eps
    └── damodaran_erp_history.py → US ERP PIT series (2001-2026)
    │
    ▼ (bootstrap at startup)
CSV.zip files in Lean data folder
    │
    ▼
PbRoeAtrAlgorithm (main.py)
    ├── Initialize() → load embedded data, bootstrap, AddEquity, schedule rebalance
    ├── CoarseSelection() → DollarVolume > $10M filter
    ├── FineSelection() → P/B vs ROE Gordon-growth screen
    └── DailyRebalance() → ensure prices, check stops, rescreen, rebalance
```

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
- S&P 500 membership is point-in-time via `sp500_ticker_start_end.csv`

### Implied ERP Pipeline

```
Damodaran ctryprem*.xlsx  →  extract_damodaran_erp.py  →  output.json
                              (full extraction: ratings, spreads, CDS, frontier)

ctryprem*.xlsx  →  build_damodaran_erp.py  →  implied_erp/data/damodaran_erp.json
   (lightweight: country → Total Equity Risk Premium only)
```

### Damodaran ERP PIT Pipeline (for Lean backtest)

```
Archive .xls/.xlsx  →  download_damodaran_erp.py  →  implied_erp/data/raw/
                              (download files via HTTP)

raw/ files          →  extract_all_damodaran_erp.py  →  implied_erp/data/erp/erp_*.json
                              (full per-country extraction, one JSON per period)

erp/                →  build_lean_erp_history.py  →  lean_project/data/damodaran_erp_history.json
                              (US + mature-market ERP only, compact PIT series)

damodaran_erp_history.json  →  embed_data.py  →  data/damodaran_erp_history.py
                              (embedded as load_damodaran_erp_history())
```

At runtime, `pb_roe_universe.py` uses `erp_as_of(erp_history_cache, as_of)` to pick the latest ERP at-or-before the backtest date, falling back to the static snapshot if no PIT entry is available.

## Known Issues

1. **Interest rate CSV**: `lean_project/data/alternative/interest-rate/usa/interest-rate.csv` had dates in `YYYYMMDD` format which Lean couldn't parse. Fixed by converting to `YYYY-MM-DD`. The strategy uses ^TNX from embedded bars for the risk-free rate, so this file is cosmetic only.
2. **82% max drawdown**: Strategy design concern, not a bug.
3. **No test suite**: No automated tests exist yet.
4. **No linter or formatter**: No `flake8`, `black`, `ruff`, or `pylint` configuration.
5. **`risk_free_rate()` only supports USD**: Non-US tickers will raise `ValueError`. The CAPM cost of equity `r` cannot be built for non-USD currencies.
6. **yfinance negative bookValue**: yfinance returns negative `bookValue` and `priceToBook` for ~33 S&P 500 tickers (SBUX, MCD, ABBV, LOW, etc.). These tickers are skipped per-screen when book_value is invalid — they are not dropped from the cache and may have valid book_value in a future quarter after refresh. P/B is always computed dynamically as `current_price / book_value` for the correct time period.
7. **PIT data coverage gap**: `fundamentals_history.json` currently covers only 2 tickers. yfinance only returns the most recent ~7 quarters per ticker. When quarterly data is unavailable for a ticker at a given backtest date, the screen skips that ticker (no static fallback — using current data for historical dates would be look-ahead bias). Run `python scripts/download_edgartools_data.py` periodically to accumulate new quarters via SEC filings.

## Data Files

| File | Location | Description |
|------|----------|-------------|
| `damodaran_erp.json` | `implied_erp/data/` | Lightweight ERP map (country → Total Equity Risk Premium) |
| `erp_*.json` | `implied_erp/data/erp/` | Per-period full ERP extractions (2013-2026) |
| `damodaran_erp_history.json` | `lean_project/data/` | US ERP PIT series (embedded as `damodaran_erp_history.py`) |
| `equity_bars.json` | `lean_project/data/` | Source equity daily bars (~790 tickers) |
| `fundamentals.json` | `lean_project/data/` | Latest fundamentals snapshot (TTM from SEC 10-Q) |
| `fundamentals_history.json` | `lean_project/data/` | Quarterly PIT history (TTM per quarter) |
| `sp500_ticker_start_end.csv` | `lean_project/data/` | S&P 500 membership with start/end dates |
| `ctryprem*.xls/.xlsx` | `implied_erp/data/raw/` (gitignored) | Downloaded Damodaran source files |
| `ctryprem*.xlsx` | User's Downloads (not in repo) | Source Damodaran spreadsheet |

## Dependencies (added)

- **edgar** — SEC EDGAR data access (TTM financials from 10-Q filings)
- **python-dotenv** — Load SEC_USER identity from `.env`
- **xlrd** — Read legacy `.xls` Damodaran archive files
- **fredapi** — FRED API access
- **requests** — HTTP downloads

## Dependencies

- **openpyxl** — Excel file processing (`implied_erp/`)
- **yfinance** — Yahoo Finance API (all modules)
- **pandas** — Data manipulation (`lean_project/`)
- **numpy** — Numerical operations (`lean_project/`)
- **matplotlib** — Plotting (`config/requirements.txt`)
- **difflib**, **unicodedata**, **json**, **argparse**, **zlib**, **base64** — Standard library
