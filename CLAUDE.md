# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

DBMF Quant is a Python-based quantitative trading system with standalone modules plus a QuantConnect Lean backtest migration. The Lean backtest (`lean_project/`) is the primary deliverable.

1. **Lean Backtest** — P/B vs ROE ATR trailing stop strategy running on QuantConnect Lean
2. **Implied ERP** — extracts country-level equity risk premiums from Damodaran's spreadsheet
3. **P/B vs ROE Screener** — standalone Gordon-growth valuation (pre-Lean)
4. **Volatility Trailing Stop** — ATR-based trailing stop with multiple smoothing modes

There is no build system, test suite, or linter configured. Each module is a standalone script package.

## Environment

- **Python 3.11+** virtualenv at `.venv` (Windows / PowerShell). Activate with `.\.venv\Scripts\Activate.ps1`.
- Dependencies in `config/requirements.txt`: `matplotlib`, `yfinance`, `pandas`, `openpyxl`, `xlrd>=2.0.1`, `numpy`, `edgar`, `python-dotenv`.
- Install: `pip install -r config/requirements.txt`
- **Docker** required for Lean backtesting (quantconnect/lean:foundation image).
- **QuantConnect Lean CLI** v1.0.227+ required.

## Module Layout

```
dbmf_quant/
├── lean_project/              # Lean backtest (primary deliverable)
│   ├── main.py                # PbRoeAtrAlgorithm
│   ├── lean.json              # Lean v2 config
│   ├── README.md              # Lean-specific documentation
│   ├── data/                  # All data files
│   │   ├── equity_bars.py     # Embedded daily bars (77 tickers)
│   │   ├── damodaran_erp_json.py  # Embedded ERP (175+ countries)
│   │   ├── fundamentals_json.py   # Embedded fundamentals (latest snapshot)
│   │   ├── fundamentals_history.py  # Embedded quarterly PIT history (if present)
│   │   ├── growth_cache.py        # Embedded growth cache
│   │   ├── bootstrap_data.py      # Writes CSV.zip into Lean data folder
│   │   ├── damodaran_erp.py       # DamodaranERP PythonData feed
│   │   ├── damodaran_erp_history.py  # Embedded US ERP PIT series
│   │   ├── equity_bars.json       # Source bars data
│   │   ├── damodaran_erp.json     # Source ERP data (static snapshot)
│   │   ├── damodaran_erp_history.json  # Source PIT ERP history
│   │   ├── fundamentals.json      # Source fundamentals
│   │   ├── growth_cache.json      # Source growth cache
│   │   ├── equity/             # Lean equity .zip files (daily bars)
│   │   └── alternative/        # Interest rate data (cosmetic)
│   ├── universe/
│   │   └── pb_roe_universe.py  # Fine selection logic
│   ├── indicators/
│   │   └── atr_trailing_stop.py # ATR trailing stop
│   ├── valuation/
│   │   └── gordon_growth.py      # Intrinsic P/B (Gordon growth)
│   ├── scripts/
│   │   ├── embed_data.py         # JSON → embedded Python modules
│   │   ├── download_equity_data.py
│   │   ├── download_edgartools_data.py  # TTM fundamentals from SEC filings
│   │   ├── compute_growth_cache.py
│   │   └── convert_to_qc_format.py
│   ├── Lean/                     # QuantConnect Lean framework
│   └── .gitignore
├── implied_erp/                  # Damodaran ERP extraction pipeline
│   ├── extract_damodaran_erp.py    # Rich extractor (.xlsx + .xls via xlrd)
│   ├── build_damodaran_erp.py      # Lightweight flat extractor
│   ├── helper.py
│   ├── scripts/                    # Downloader, batch extractor, consolidator
│   │   ├── download_damodaran_erp.py
│   │   ├── extract_all_damodaran_erp.py
│   │   └── build_lean_erp_history.py
│   ├── data/
│   │   ├── july26.json
│   │   ├── raw/                    # Downloaded .xls/.xlsx (gitignored)
│   │   └── erp/                    # Per-period extracted JSONs
│   └── README.md
├── pb_roe/                       # Standalone P/B vs ROE screener
│   ├── src/
│   │   ├── helpers.py
│   │   └── screener/
│   │       ├── damodaran.py
│   │       └── metrics.py
│   └── CLAUDE.md
├── vol_trail_stop/               # ATR trailing stop (standalone)
│   ├── vol_trail_stop.py
│   └── README.md
├── backtest/                     # Old custom backtest engine (superseded by Lean)
├── notebooks/                    # Jupyter demos
│   ├── notebook1.ipynb
│   └── notebook2.ipynb
├── config/
│   └── requirements.txt
├── helper.py                     # Root-level yfinance helper
├── README.md                     # Comprehensive project documentation
└── CLAUDE.md                     # This file
```

## Commands

```powershell
# Activate environment
.\.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r config/requirements.txt

# ── Implied ERP ──
python implied_erp/extract_damodaran_erp.py --xlsx "path/to/ctrypremJuly26.xlsx" --out "implied_erp/data/july26.json"
python implied_erp/build_damodaran_erp.py

# ── Damodaran ERP PIT pipeline (for Lean backtest) ──
# 1. Download archive .xls + current .xlsx files
python implied_erp/scripts/download_damodaran_erp.py --dry-run   # preview links
python implied_erp/scripts/download_damodaran_erp.py             # download
# 2. Extract all into per-period JSONs
python implied_erp/scripts/extract_all_damodaran_erp.py
# 3. Build Lean-compatible PIT history
python implied_erp/scripts/build_lean_erp_history.py
# 4. Embed into Lean (from lean_project/)
cd lean_project && python scripts/embed_data.py

# ── P/B vs ROE screener (standalone) ──
cd pb_roe && python src/helpers.py
python pb_roe/src/screener/metrics.py AAPL

# ── Volatility trailing stop ──
python vol_trail_stop/vol_trail_stop.py

# ── Lean backtest ──
cd lean_project
lean backtest

# ── Regenerate embedded data for Lean ──
cd lean_project
python scripts/download_edgartools_data.py   # TTM fundamentals from SEC filings
python scripts/download_equity_data.py        # Equity bars + yfinance fallback
python scripts/compute_growth_cache.py
python scripts/convert_to_qc_format.py
python scripts/embed_data.py
```

## Architecture & Data Flow

### Lean Backtest Pipeline

```
Embedded Python modules (zlib+base64)
    │
    ├── equity_bars.py      → 77 tickers × 751 daily bars
    ├── damodaran_erp_json.py → 175+ countries ERP
    ├── fundamentals_json.py  → P/B, ROE, Beta, EPS, Sector (TTM from SEC)
    └── growth_cache.py       → yfinance growth rates
    │
    ▼ (bootstrap at startup)
CSV.zip files in Lean data folder
    │
    ▼
PbRoeAtrAlgorithm (main.py)
    ├── Initialize() → load embedded data, bootstrap, AddEquity, schedule rebalance
    ├── CoarseSelection() → DollarVolume > $10M
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
- Fundamentals use TTM (trailing twelve months) from SEC 10-Q filings via edgartools

### Implied ERP Pipeline

```
Damodaran ctryprem*.xlsx  →  extract_damodaran_erp.py  →  implied_erp/data/july26.json
                              (full extraction: ratings, spreads, CDS, frontier)
                              (metadata: update date, mature market ERP, US ERP)

ctryprem*.xlsx  →  build_damodaran_erp.py  →  implied_erp/data/damodaran_erp.json
   (lightweight: country → Total Equity Risk Premium only)
```

### Damodaran ERP PIT Pipeline (for Lean backtest)

The Lean backtest uses a point-in-time ERP series so the correct ERP is used at each rebalance date (no look-ahead bias).

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

### P/B vs ROE Screener (Standalone)

```
yfinance (ticker info)  →  screener/metrics.py:get_pb_roe()  →  PBROE dataclass
                           screener/metrics.py:check_pb_roe_discrepancy()  →  DiscrepancyReport

yfinance (ticker country)  →  screener/damodaran.py:erp_for_ticker()  →  ERP (from JSON)

screen(ticker, g)  →  helpers.py
   roe, actual_pb from yfinance
   erp from Damodaran JSON (via erp_for_ticker)
   r = capm(risk_free_rate("USD"), beta, erp)
   implied_pb = intrinsic_pb(roe, g, r)   # Gordon growth
   verdict: "undervalued" if implied_pb > actual_pb else "overvalued"
```

## Known Issues

1. **Interest rate CSV**: `lean_project/data/alternative/interest-rate/usa/interest-rate.csv` had dates in `YYYYMMDD` format which Lean couldn't parse. Fixed by converting to `YYYY-MM-DD`. The strategy uses ^TNX from embedded bars for the risk-free rate, so this file is cosmetic only.
2. **82% max drawdown**: Strategy design concern, not a bug.
3. **No test suite**: No automated tests exist anywhere in the project.
4. **No linter or formatter**: No `flake8`, `black`, `ruff`, or `pylint` configuration.
5. **`implied_erp/implied_erp.py` is an empty placeholder**.
6. **`backtest/` is superseded by Lean** — old custom backtest engine, no longer used.
7. **`pb_roe/` standalone screener** — functional but the Lean migration in `lean_project/` is the primary deliverable.
8. **`pb_roe/src/screener/damodaran.py` data path mismatch**: The module resolves `_DATA_PATH` as `repo_root/data/damodaran_erp_july.json` (four `.parent` calls up from `pb_roe/src/screener/damodaran.py`), but no `data/` directory exists at the repo root. The actual ERP data lives at `implied_erp/data/july26.json` (full extraction) and `implied_erp/data/damodaran_erp.json` (what `build_damodaran_erp.py` writes). The path in `damodaran.py` must be updated to point to one of these files.
9. **`risk_free_rate()` only supports USD**: Non-US tickers will raise `ValueError`. The CAPM cost of equity `r` cannot be built for non-USD currencies.

## Data Files

| File | Location | Description |
|------|----------|-------------|
| `july26.json` | `implied_erp/data/` | Full ERP extraction (175+ countries, all fields) |
| `damodaran_erp.json` | `implied_erp/data/` | Lightweight ERP map (country → Total Equity Risk Premium) |
| `erp_*.json` | `implied_erp/data/erp/` | Per-period full ERP extractions (2001-2026) |
| `damodaran_erp_history.json` | `lean_project/data/` | US ERP PIT series (embedded as `damodaran_erp_history.py`) |
| `equity_bars.json` | `lean_project/data/` | Embedded equity daily bars (77 tickers) |
| `fundamentals.json` | `lean_project/data/` | Latest snapshot (503 tickers, TTM from SEC 10-Q) |
| `fundamentals_history.json` | `lean_project/data/` | Quarterly PIT history (TTM per quarter, back to backtest start) |
| `growth_cache.json` | `lean_project/data/` | Embedded growth rates |
| `ctryprem*.xls/.xlsx` | `implied_erp/data/raw/` (gitignored) | Downloaded Damodaran source files |
| `ctryprem*.xlsx` | User's Downloads (not in repo) | Source Damodaran spreadsheet |

## Dependencies (added)

- **edgar** — SEC EDGAR data access (TTM financials from 10-Q filings)
- **python-dotenv** — Load SEC_USER identity from `.env`
- **xlrd** — Read legacy `.xls` Damodaran archive files

## Dependencies

- **openpyxl** — Excel file processing (`implied_erp/`)
- **yfinance** — Yahoo Finance API (all modules)
- **pandas** — Data manipulation (`vol_trail_stop/`, `helper.py`)
- **numpy** — Numerical operations (`vol_trail_stop/`)
- **matplotlib** — Plotting (`vol_trail_stop/`)
- **difflib**, **unicodedata**, **json**, **argparse** — Standard library (used in `pb_roe/src/screener/damodaran.py` and `implied_erp/`)
- **zlib**, **base64** — Standard library (used in `lean_project/scripts/embed_data.py`)
