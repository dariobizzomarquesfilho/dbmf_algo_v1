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
- Dependencies in `config/requirements.txt`: `yfinance`, `pandas`, `openpyxl`, `xlrd>=2.0.1`, `numpy`, `edgartools==5.52.0` (provides the `edgar` import), `lean==1.0.228`, `python-dotenv`, `requests`.
- Install: `pip install -r config/requirements.txt` (restores `lean` CLI + `edgar`; no separate `pip install lean` needed)
- **Docker** required for Lean backtesting (quantconnect/lean:foundation image).
- `.env` file required at `config/.env` with `SEC_USER` identity for edgartools and optional `BACKTEST_START`/`BACKTEST_END` overrides.
- Tests: `python -m pytest lean_project/tests implied_erp/tests` from the repo root.

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
│   ├── README.md
│   ├── scripts/                    # PIT pipeline scripts
│   │   ├── download_damodaran_erp.py
│   │   ├── extract_all_damodaran_erp.py
│   │   ├── scrape_histimpl.py      # Historical US implied ERP (annual fallback)
│   │   └── build_lean_erp_history.py
│   ├── data/
│   │   ├── erp/                    # Per-period extracted JSONs (2013-2026)
│   │   └── raw/                    # Downloaded .xls/.xlsx (gitignored)
│   └── tests/                      # pytest tests for extractor + histimpl parser
├── lean_project/                 # Lean backtest (primary deliverable)
│   ├── main.py                    # PbRoeAtrAlgorithm
│   ├── lean.json                  # Lean v2 config
│   ├── README.md                  # Lean-specific documentation
│   ├── data/
│   │   ├── equity_bars.py         # Embedded daily equity bars (~790 tickers)
│   │   ├── equity_bars.json       # Source bars data
│   │   ├── damodaran_erp_history.py  # Embedded US ERP PIT series
│   │   ├── damodaran_erp_history.json # Source PIT ERP history
│   │   ├── fundamentals_history.py # Embedded quarterly PIT fundamentals
│   │   ├── fundamentals_history.json # Source quarterly PIT history (edgartools)
│   │   ├── backtest_config.py     # Embedded backtest window (from config/.env)
│   │   ├── bootstrap_data.py      # Writes CSV.zip into Lean data folder
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
│   │   ├── build_cik_map.py
│   │   ├── common.py              # Shared variants/row-conversion/end helpers
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
    ├── equity_bars.py           → ~790 tickers × daily bars
    ├── fundamentals_history.py  → quarterly PIT book_value / roe / eps / g_eps
    └── damodaran_erp_history.py → US ERP PIT series (2001-2026)
    │
    ▼ (bootstrap at startup)
CSV.zip files in Lean data folder
    │
    ▼
PbRoeAtrAlgorithm (main.py)
    ├── Initialize() → load embedded data, bootstrap, pre-subscribe members
    │   active at the start date, arm the OnData daily trigger
    ├── OnData() → _ensure_subscribed() adds members on their index-add date
    │   (guarded by data.sp500_data.intervals_active), then DailyRebalance()
    └── DailyRebalance() → ensure prices, check stops, run_fine_selection()
        (P/B vs ROE Gordon-growth screen in universe/pb_roe_universe.py),
        corporate-action / membership exits, SetHoldings / Liquidate
```

**Key design decisions:**
- All data is embedded in Python modules (no external .csv.zip or .json files at runtime)
- Prices injected via `Security.Price` fix: `Security.SetMarketPrice()` from embedded bars
- Daily rebalance triggered from `OnData` (one cycle per trading day); Lean's Coarse/Fine universe hooks are not wired up
- ATR computation uses embedded bars dict (bypasses `algorithm.History()`)
- Risk-free rate is point-in-time from ^TNX embedded bars (`universe/pit_data.resolve_risk_free_rate`), never future bars
- Financial sector excluded via edgar native classification (SIC / business category)
- Fundamentals use TTM from SEC 10-Q filings (edgartools)
- PIT quarterly fundamentals used when available; tickers without quarterly coverage at a date are skipped (no static snapshot fallback — that would be look-ahead bias)
- Backtest window is single source of truth: `config/.env` → `config/config.py` → `data/backtest_config.py` → `lean.json` (re-run `scripts/embed_data.py` after changing `.env`)
- S&P 500 membership is point-in-time via `sp500_ticker_start_end.csv`; bars are clipped to membership intervals

### Implied ERP Pipeline

```
Damodaran ctryprem*.xlsx  →  extract_damodaran_erp.py  →  output.json
                               (full extraction: ratings, spreads, CDS, frontier)
```

The lightweight static-snapshot path (`build_damodaran_erp.py` → `lean_project/data/damodaran_erp.json`) was removed. The Lean backtest's ERP source of truth is the point-in-time series below.

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

At runtime, `pb_roe_universe.py` resolves the ERP **solely** through the point-in-time history: `resolve_erp_as_of(erp_history_cache, histimpl_cache, as_of)` picks the latest ERP at-or-before the backtest date (preferring the spreadsheet PIT series, falling back to the annual histimpl US series). There is no static "current" snapshot to fall back on — if neither series yields an entry, the screen refuses (returns an empty universe) rather than pricing with a future ERP.

## Known Issues

1. **Interest rate CSV**: `lean_project/data/alternative/interest-rate/usa/interest-rate.csv` had dates in `YYYYMMDD` format which Lean couldn't parse. Fixed by converting to `YYYY-MM-DD`. The strategy uses ^TNX from embedded bars for the risk-free rate, so this file is cosmetic only.
2. **82% max drawdown**: Strategy design concern, not a bug.
3. **No linter or formatter**: No `flake8`, `black`, `ruff`, or `pylint` configuration. A pytest suite exists (`lean_project/tests`, `implied_erp/tests`).
4. **USD-only risk-free rate**: CAPM cost of equity uses ^TNX (USD); non-USD listings are out of scope.
5. **yfinance negative bookValue**: yfinance returns negative `bookValue` and `priceToBook` for ~33 S&P 500 tickers (SBUX, MCD, ABBV, LOW, etc.). These tickers are skipped per-screen when book_value is invalid — they are not dropped from the cache and may have valid book_value in a future quarter after refresh. P/B is always computed dynamically as `current_price / book_value` for the correct time period.
6. **PIT coverage depth varies**: edgartools XBRL fundamentals reach back to ~2009; many later-added S&P 500 names only have fundamentals from ~2019 onward, so early-window universes are thinner (SEC source limit). Tickers without quarterly coverage at a date are skipped — no static-snapshot fallback (look-ahead bias).
7. **Known bar-coverage gaps**: EA / FOX / FOXA / IR have incomplete bars in the current `equity_bars.json`; `embed_data.py` reports them at embed time until repaired by re-running the download/repair pipeline.

## Data Files

| File | Location | Description |
|------|----------|-------------|
| `erp_*.json` | `implied_erp/data/erp/` | Per-period full ERP extractions (2013-2026) |
| `damodaran_erp_history.json` | `lean_project/data/` | US ERP PIT series (embedded as `damodaran_erp_history.py`) |
| `histimpl_us_erp.json` | `implied_erp/data/` | Annual US implied ERP fallback (optional; embedded as `damodaran_erp_history_us.py` when present) |
| `equity_bars.json` | `lean_project/data/` | Source equity daily bars (~790 tickers) |
| `fundamentals_history.json` | `lean_project/data/` | Quarterly PIT history (TTM per quarter, edgartools) |
| `sp500_ticker_start_end.csv` | `lean_project/data/` | S&P 500 membership with start/end dates |
| `ctryprem*.xls/.xlsx` | `implied_erp/data/raw/` (gitignored) | Downloaded Damodaran source files |

## Dependencies (added)

- **edgartools==5.52.0** — SEC EDGAR data access (provides the `edgar` module used across the project)
- **lean==1.0.228** — QuantConnect Lean CLI (provides `lean backtest`; now pinned in `requirements.txt` so the venv self-heals)
- **python-dotenv** — Load SEC_USER identity from `.env`
- **xlrd** — Read legacy `.xls` Damodaran archive files
- **requests** — HTTP downloads

## Dependencies

- **openpyxl** — Excel file processing (`implied_erp/`)
- **yfinance** — Yahoo Finance API (all modules)
- **pandas** — Data manipulation (`lean_project/`)
- **numpy** — Numerical operations (`lean_project/`)
- **difflib**, **unicodedata**, **json**, **argparse**, **zlib**, **base64** — Standard library
