# DBMF Quant

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](config/requirements.txt)
[![QuantConnect Lean](https://img.shields.io/badge/Lean-1.0.228-orange.svg)](lean_project/lean.json)
[![Tests](https://img.shields.io/badge/tests-pytest-green.svg)](lean_project/tests)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Point-in-time QuantConnect Lean backtest of a **P/B vs ROE + ATR trailing stop** strategy on the S&P 500, plus a Damodaran implied ERP extraction pipeline. All runtime data is embedded (`zlib`+`base64`) — zero file I/O at backtest time, no look-ahead.

> **Portfolio showcase:** open `lean_project/report.html` locally for the full backtest report (equity curve, drawdown, trades). The report is tracked as a showcase artifact.

## Strategy at a Glance

1. **Universe:** S&P 500 PIT membership from `lean_project/data/sp500_ticker_start_end.csv` (`intervals_active`). Members active at `BACKTEST_START` are pre-subscribed; later additions are `AddEquity()`-ed on their index-add date.
2. **Screen (daily):** TTM fundamentals from SEC EDGAR (edgartools, keyed by **filing_date**, not fiscal period) → 2-stage Gordon growth → intrinsic P/B → CAPM (`^TNX` as risk-free, `^GSPC` beta, Damodaran ERP PIT) → select where `intrinsic P/B > actual P/B` (undervalued).
3. **Financials excluded** via EDGAR SIC/business category (current classification, documented limitation).
4. **Equal-weight** `1/max_positions`, **ATR trailing stop** exits (SMA/EMA/WMA/RMA).

See `lean_project/README.md` for the runtime flow diagram and `docs/equity-data-pipeline.md` for the 5-step data pipeline.

## Backtest Report

The last backtest (2011-01-01 → 2026-07-31, $100k, warm-up 252 trading days) is in:

```
lean_project/report.html    # interactive HTML — open in a browser
```

To reproduce: `cd lean_project && lean backtest` (requires Docker `quantconnect/lean:foundation`). The window is driven by `config/.env` → `config/config.py` → `lean_project/data/backtest_config.py` → `lean_project/lean.json` (re-run `python lean_project/scripts/embed_data.py` after any `.env` change).

## Key Design Decisions (PIT / No Look-Ahead)

- Embedded bars/fundamentals/ERP in Python modules, prices via `Security.SetMarketPrice()` (fixes `Security.Price == 0` at scheduled times).
- Daily rebalance from `OnData` (one cycle per trading day); `CoarseSelection`/`FineSelection` Lean hooks are not used.
- `fundamental_as_of(ticker, as_of)` picks latest filing with `filing_date <= as_of`; legacy fiscal-period-keyed rows are rejected (would be ~30-45d look-ahead).
- `resolve_erp_as_of()` prefers Damodaran spreadsheet PIT series, falls back to annual `histimpl`; never invents ERP (empty universe if neither yields data).
- `resolve_risk_free_rate()` from `^TNX` embedded bars PIT only (returns `None` → screen skipped; no static `0.042` fallback).
- `corporate_actions.last_trading_day` and membership exits are NYSE-calendar aware (`lean_project/data/nys_calendar.py`).
- Bars are clipped to membership intervals; ATR/beta use the embedded bar dict, not `algorithm.History()`.

## Limitations & Disclosure

This backtest is PIT but not free of accepted gaps. Full disclosure is in `docs/data-limitations.md` and the generated `lean_project/data/missing-data.txt`.

- **Fundamentals coverage:** EDGAR XBRL starts ~2009; many later S&P 500 additions only have fundamentals from ~2019. Early windows are thinner — tickers without a filing at-or-before the date are skipped (no static fallback). Re-run `scripts/download_edgartools_data.py` quarterly.
- **Bars:** yfinance `auto_adjust=True` (splits/dividends adjusted). Some names have gaps: EA/FOX/FOXA/IR flagged at embed time until a re-download repairs them. Throttled tickers appear as `PENDING` in `repair_equity_data.py` — retry after a cooldown.
- **Survivorship:** `window_members_without_fundamentals` = `window_members - fundamentals_keys` (≈ 607 tickers in the 2011-2026 window). Bars and fundamentals are independent sources — this is expected, not a bug. See `docs/data-limitations.md:§3`.
- **Renames/delisted:** `fetch_missing_delisted.py` recovers via curated `RENAME_MAP` + Tiingo fallback (US-exchange-guarded; `tiingo_foreign_collision` rejected). Unrecoverable names go to `equity_unavailable.json`.
- **Financials classification:** uses current SIC (`L1 accepted`), not PIT SIC history.
- **Risk-free:** USD-only (`^TNX`).
- **Interest-rate CSV:** `lean_project/data/alternative/interest-rate/usa/interest-rate.csv` is cosmetic (Lean parse-fixed); strategy uses `^TNX` bars.

Run `python lean_project/scripts/track_exclusions.py` to regenerate the exclusion counts.

## Project Structure

```
dbmf_quant_v2/
├── README.md                    # this file
├── LICENSE                      # MIT
├── config/
│   ├── config.py                # env-driven window, warm-up, HISTORY_START
│   ├── .env.example             # template (copy to .env)
│   └── requirements.txt         # yfinance, edgartools==5.52.0, lean==1.0.228, ...
├── docs/
│   ├── equity-data-pipeline.md  # 5-step bar/fundamental pipeline + recovery scenarios
│   └── data-limitations.md      # PIT rules, survivorship & accepted gaps
├── implied_erp/                 # Damodaran ERP extraction + PIT builder
│   ├── extract_damodaran_erp.py
│   ├── README.md
│   ├── data/
│   │   ├── erp/                 # per-period JSONs (gitignored, examples not tracked)
│   │   └── raw/                 # downloaded .xls/.xlsx (gitignored)
│   └── scripts/
│       ├── download_damodaran_erp.py
│       ├── extract_all_damodaran_erp.py
│       ├── scrape_histimpl.py
│       └── build_lean_erp_history.py
└── lean_project/                # Lean backtest (primary deliverable)
    ├── main.py                  # PbRoeAtrAlgorithm
    ├── lean.json
    ├── report.html              # last backtest report (showcase artifact, tracked)
    ├── data/
    │   ├── equity_bars.py / fundamentals_history.py / damodaran_erp_history.py  # embedded (gitignored, generated)
    │   ├── sp500_ticker_start_end.csv   # PIT membership (tracked)
    │   ├── sp500_cik_map.csv            # ticker→CIK (tracked)
    │   ├── bootstrap_data.py / sp500_data.py / bar_quality.py / exclusions.py / ...
    │   └── equity/ / alternative/       # Lean CSV.zip data (generated)
    ├── universe/  pb_roe_universe.py / pit_data.py
    ├── indicators/atr_trailing_stop.py
    ├── valuation/gordon_growth.py
    ├── scripts/  download_* / repair_* / fetch_* / track_* / embed_data.py / ...
    ├── tests/    # pytest (lean_project/tests + implied_erp/tests)
    └── Lean/     # Lean engine source (gitignored)
```

> `lean_project/Lean/` is a cloned Lean engine (gitignored). Run `.\run_pipeline.ps1` for the full pipeline with per-step logs in `logs/<timestamp>/`.

## Setup

```powershell
# 1. Clone and enter
git clone https://github.com/dariobizzomarquesfilho/dbmf_quant_v2.git
cd dbmf_quant_v2

# 2. Virtualenv (Windows PowerShell; Linux/macOS: python -m venv .venv && source .venv/bin/activate)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r config/requirements.txt   # restores `lean` CLI and `edgar` (edgartools)

# 3. Env
cp config/.env.example config/.env
# edit config/.env: set SEC_USER="Full Name email@domain" (required for edgartools)
# optional: BACKTEST_START, BACKTEST_END, BACKTEST_WARMUP_DAYS, TIINGO_API_KEY
```

`config/config.py` validates `SEC_USER` and computes `DATA_START = BACKTEST_START - 252 trading days` and `HISTORY_START` (earliest membership start or `BACKTEST_HISTORY_START` env).

## Running the Backtest

```powershell
cd lean_project
lean backtest            # or: lean backtest --config lean.json
# then open the report
start report.html        # Windows; macOS: open report.html
```

Docker image `quantconnect/lean:foundation` is required. `lean_project/lean.json` dates are overwritten by `scripts/embed_data.py` — never edit them by hand.

## Regenerating Embedded Data (quarterly recommended)

```powershell
cd lean_project

# fundamentals from SEC EDGAR (PIT quarterly TTM)
python scripts/download_edgartools_data.py              # --tickers AAPL MSFT for a quick test

# bars from yfinance (full S&P 500 + ^GSPC + ^TNX)
python scripts/download_equity_data.py
python scripts/repair_equity_data.py
python scripts/fetch_missing_delisted.py                # dry-run
python scripts/fetch_missing_delisted.py --apply
python scripts/track_exclusions.py                      # verify missing-data.txt

# QC zip + embed (hard-fails if coverage missing)
python scripts/convert_to_qc_format.py
python scripts/embed_data.py
```

After `embed_data.py`, the generated `*bars.py` / `*_history.py` / `backtest_config.py` stay gitignored; commit only if you intend to publish a new data vintage. See `docs/equity-data-pipeline.md` for throttling recovery (Scenario A/B/C).

## Damodaran ERP PIT Pipeline

```powershell
python implied_erp/scripts/download_damodaran_erp.py             # or --dry-run
python implied_erp/scripts/extract_all_damodaran_erp.py
python implied_erp/scripts/build_lean_erp_history.py             # → lean_project/data/damodaran_erp_history.json
cd lean_project && python scripts/embed_data.py                  # embed
# optional annual fallback: python implied_erp/scripts/scrape_histimpl.py
```

## Testing

```powershell
python -m pytest lean_project/tests implied_erp/tests
python -m pytest lean_project/tests/test_pit_data.py -v
```

No linter/formatter is configured; tests are the gate. `run_pipeline.ps1` runs the full chain (ERP → fundamentals/bars → embed → tests → backtest) with logs in `logs/<yyyyMMdd_HHmmss>/`.

## Requirements

- Python 3.11+, Docker (for `lean backtest`)
- QuantConnect Lean CLI `1.0.228` (pinned; `pip install -r config/requirements.txt` restores it)
- `edgartools==5.52.0` provides `edgar` for SEC filings

Cross-platform: primary dev is Windows PowerShell, but `lean backtest` and Python scripts run on Linux/macOS as well (use `source .venv/bin/activate`).

## License

MIT — see [LICENSE](LICENSE).
