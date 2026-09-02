# DBMF Algo v.1

[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](config/requirements.txt)
[![QuantConnect Lean](https://img.shields.io/badge/Lean-1.0.228-orange.svg)](lean_project/lean.json)
[![Tests](https://img.shields.io/badge/tests-pytest-green.svg)](lean_project/tests)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

Point-in-time backtest implemented in QuantConnect Lean. The strategy applies P/B versus ROE valuation screening with ATR trailing-stop risk control on the S&P 500 universe. Equity risk premium is sourced from a Damodaran implied ERP extraction pipeline. All runtime data is embedded as zlib and base64 encoded Python modules. No filesystem I/O or forward-looking data access occurs during backtest execution.

## Strategy Overview

1. **Universe.** S&P 500 point-in-time membership defined in `lean_project/data/sp500_ticker_start_end.csv` via `data/sp500_data.py:intervals_active` (path:line `lean_project/data/sp500_data.py:38`). Constituents active at `BACKTEST_START` are pre-subscribed in `main.py:Initialize()` (path:line `lean_project/main.py:29`); constituents added after the start date are subscribed on their index-add date via `main.py:_ensure_subscribed()` (path:line `lean_project/main.py:272`).

2. **Screening (daily).** Trailing twelve month fundamentals sourced from SEC EDGAR via `edgartools` (`universe/pit_data.py:fundamental_as_of`, path:line `lean_project/universe/pit_data.py:22`) and keyed by `filing_date` (SEC acceptance date). Valuation uses a two-stage Gordon growth model (`valuation/gordon_growth.py`) to derive intrinsic P/B, with cost of equity from CAPM using `^TNX` as the risk-free rate, `^GSPC` beta from `pit_data.py:rolling_beta` (path:line `lean_project/universe/pit_data.py:57`), and point-in-time Damodaran ERP from `pit_data.py:resolve_erp_as_of` (path:line `lean_project/universe/pit_data.py:144`). A position is selected when intrinsic P/B exceeds observed P/B.

3. **Exclusions.** Financial sector constituents are excluded based on EDGAR SIC and business category. Classification uses the current snapshot. Refer to `docs/data-limitations.md` for the limitation disclosure.

4. **Portfolio construction and exit.** Equal weight `1 / max_positions` (default 10). Exit is governed by ATR trailing stop (`indicators/atr_trailing_stop.py:compute_atr_trailing_stop`). Supported moving average types: SMA, EMA, WMA, RMA.

Additional detail: `lean_project/README.md` describes runtime flow; `docs/equity-data-pipeline.md` describes the data pipeline.

## Backtest Output

The active backtest window is defined in `config/.env` and propagated through `config/config.py` (path:line `config/config.py:22`) to `lean_project/data/backtest_config.py` (path:line `lean_project/data/backtest_config.py:7`) and `lean_project/lean.json`. The most recent executed window is 2011-01-01 to 2026-07-31 with initial capital $100,000 and 252 trading days of warm-up. Artifacts:

```
lean_project/report.html                                        # Lean HTML report
lean_project/backtests/2026-09-01_11-54-35/1791824131-summary.json  # Machine-readable statistics (CAGR, Sharpe, drawdown, trade counts)
lean_project/backtests/2026-09-01_11-54-35/1791824131.json          # Full backtest payload (charts, orders, profitLoss)
lean_project/backtests/2026-09-01_11-54-35/log.txt                  # Structured log (DailyRebalance, ERP diagnostics, order events)
```

Reproduction:

```powershell
cd lean_project
lean backtest            # alternative: lean backtest --config lean.json
```

Requires Docker image `quantconnect/lean:foundation`. After any change to `config/.env`, regenerate embedded artifacts with `python lean_project/scripts/embed_data.py`.

### Observed Characteristics (latest backtest 2026-09-01, id 1791824131, window 2011-01-01 to 2026-07-31)

Source: `lean_project/backtests/2026-09-01_11-54-35/1791824131-summary.json` and `1791824131.json`. Runtime 667 seconds, 1,638,017 data points.

| Metric | Value |
|--------|-------|
| Start / end equity | $100,000 to $431,740 |
| Total net profit | 331.74% |
| Compounding annual return | 9.84% |
| Annual standard deviation | 10.84% (variance 0.012) |
| Sharpe ratio | 0.672 |
| Sortino ratio | 0.739 |
| Probabilistic Sharpe ratio | 4.84% |
| Information ratio | 0.675 (tracking error 0.108) |
| Maximum drawdown | 21.7% on 2025-04-09; recovery 930 days; maximum drawdown duration 839 days; maximum intra-trade drawdown -77,074; maximum closed-trade drawdown -74,583 |
| Value at risk (99% / 95%) | -1.6% / -1.1% |
| Portfolio turnover | 2.46% |
| Estimated capacity | $51M (lowest capacity asset JKHY) |
| Total orders / closed trades | 1,547 / 754 |
| Win rate / loss rate | 44.8% / 55.2% (339 wins, 415 losses) |
| Average win / average loss | 1.15% / -0.58% |
| Profit factor / profit-loss ratio | 1.51 / 1.99 |
| Expectancy | 0.339 |
| Largest profit / largest loss | $32,501 / -$15,646 |
| Total profit / total loss | $943,208 / -$625,605 |
| Average trade duration | 51 days (median 42 days); average winning duration 80 days (median 71 days); average losing duration 28 days (median 24 days) |
| Maximum consecutive wins / losses | 9 / 16 |
| Total fees | $7,829 (volume $36.0M) |
| Holdings at end | 7 positions; unrealized $21,951 |

Annual return (year-end equity, derived from `charts.Strategy Equity`):

| Year | End equity | Annual return |
|------|------------|---------------|
| 2011 | $95,281 | — |
| 2012 | $110,690 | 16.2% |
| 2013 | $137,371 | 24.1% |
| 2014 | $148,262 | 7.9% |
| 2015 | $149,703 | 1.0% |
| 2016 | $172,808 | 15.4% |
| 2017 | $205,953 | 19.2% |
| 2018 | $198,863 | -3.4% |
| 2019 | $258,433 | 30.0% |
| 2020 | $373,727 | 44.6% |
| 2021 | $417,923 | 11.8% |
| 2022 | $367,351 | -12.1% |
| 2023 | $383,969 | 4.5% |
| 2024 | $366,664 | -4.5% |
| 2025 | $408,003 | 11.3% |
| 2026 (to 07-31) | $431,740 | 5.8% |

Strategy configuration for this run (path:line `lean_project/main.py:34`): `max_positions = 10` (path:line `lean_project/main.py:34`), `atr_period = 15` (path:line `lean_project/main.py:35`), `atr_multiplier = 3.0` (path:line `lean_project/main.py:36`), `smoothing = "SMA"`, `cooldown_days = 30` (path:line `lean_project/main.py:37`), equal weight 10% of NAV with integer shares, slippage 0.5%, fee $1.00 minimum plus $0.005 per share, dust fallback to top 3 ranked candidates. Screening via `universe/pb_roe_universe.py:run_fine_selection` (path:line `lean_project/universe/pb_roe_universe.py:188`) requiring `roe > 0`, `beta > 0`, `eps > 0`, `book_value > 0`, price on `as_of` date, `g_eps` finite (2-year TTM EPS CAGR), and `gap_pct = (implied - actual) / actual > 0`; ranking by `gap_pct` and selecting top `max_positions`. Intrinsic P/B from `valuation/gordon_growth.py:intrinsic_pb_2stage` (path:line `lean_project/valuation/gordon_growth.py:9`) with `years_stage1 = 5`, `g_start = g_eps`, `g_term = rf`, `r = rf + beta * erp`, payout clamped to [0, 1], terminal denominator guard `r - g_term >= 0.005`. Exits from `indicators/atr_trailing_stop.py:compute_atr_trailing_stop` (path:line `lean_project/indicators/atr_trailing_stop.py:16`) with ratchet logic plus corporate action and membership-end exits.

## Design Constraints

- Data embedding. Bars, fundamentals, and ERP are embedded in Python modules. Prices are injected via `Security.SetMarketPrice()` from the embedded bar dictionary to ensure `Security.Price` is defined at scheduled event times (`main.py:_ensure_prices`, path:line `lean_project/main.py:219`).
- Rebalance scheduling. Daily rebalance is triggered from `OnData()` (one cycle per trading day). Lean `CoarseSelection` and `FineSelection` hooks are not used (`main.py:OnData`, path:line `lean_project/main.py:260`).
- Fundamentals point-in-time rule. `fundamental_as_of(ticker, as_of)` returns the latest filing with `filing_date <= as_of`. Legacy rows keyed by fiscal period without a `filed` or `filing_date` field are rejected to avoid approximately 30 to 45 days of look-ahead bias. The `period` and `report_date` fields are retained for audit only.
- ERP point-in-time rule. `resolve_erp_as_of()` selects the latest entry with date strictly less than `as_of`, preferring the Damodaran spreadsheet point-in-time series and falling back to the annual `histimpl` series. If neither series contains an applicable entry, the screen returns an empty universe. No synthetic ERP is generated.
- Risk-free rate point-in-time rule. `resolve_risk_free_rate()` selects the latest `^TNX` bar with date `<= as_of` (`pit_data.py:resolve_risk_free_rate`, path:line `lean_project/universe/pit_data.py:182`). Returns `None` when unavailable; the caller skips the screen without substituting a static value.
- Corporate actions and membership exits are evaluated against the NYSE calendar (`lean_project/data/nys_calendar.py`).
- Bars are clipped to membership intervals. ATR and beta are computed from the embedded bar dictionary, not from `algorithm.History()`.

## Limitations and Disclosure

The backtest is point-in-time but subject to documented data constraints. Refer to `docs/data-limitations.md` and `lean_project/data/missing-data.txt` for complete disclosure.

- **Fundamentals coverage.** EDGAR XBRL coverage is material from approximately 2009. Many S&P 500 constituents added in later years have fundamentals available only from approximately 2019. Tickers without a filing at or before the evaluation date are skipped. No static fallback is applied. Regenerate quarterly via `scripts/download_edgartools_data.py`.
- **Price bars.** Bars are sourced from yfinance with `auto_adjust=True` (splits and dividends adjusted). Known gaps have included EA, FOX, FOXA, and IR, flagged at embed time until repaired by re-running the download and repair steps. Tickers subject to yfinance throttling are reported as `PENDING` by `repair_equity_data.py` and require a cooldown before retry.
- **Survivorship and coverage.** `window_members_without_fundamentals` is defined as `window_members - fundamentals_keys` (210 tickers for the 2011-2026 window per `lean_project/data/missing-data.json` from the most recent `track_exclusions.py` run: 829 window members, 640 bar tickers, 687 fundamental tickers, `window_members_without_fundamentals` = 210). Bars and fundamentals are independent sources; this difference is expected. Refer to `docs/data-limitations.md` section 3.
- **Renames and delisted constituents.** `fetch_missing_delisted.py` recovers coverage via a curated `RENAME_MAP` and an optional Tiingo fallback. The Tiingo path is guarded by a US-exchange check; `tiingo_foreign_collision` entries are rejected. Unrecoverable tickers are recorded in `equity_unavailable.json`.
- **Financials classification.** Uses current SIC (`L1 accepted`), not point-in-time SIC history.
- **Risk-free rate.** USD only (`^TNX`).
- **Interest rate CSV.** `lean_project/data/alternative/interest-rate/usa/interest-rate.csv` is present for Lean parsing compatibility and is formatted as `YYYY-MM-DD`. The strategy uses `^TNX` bars for the risk-free rate.

Exclusion counts can be regenerated with `python lean_project/scripts/track_exclusions.py`.

## Project Structure

```
dbmf_quant_v2/
├── README.md                    # Project overview
├── LICENSE                      # MIT
├── config/
│   ├── config.py                # Window derivation, warm-up, HISTORY_START
│   ├── .env.example             # Environment template
│   └── requirements.txt         # Pinned dependencies
├── docs/
│   ├── equity-data-pipeline.md  # Bar and fundamental pipeline, recovery scenarios
│   └── data-limitations.md      # Point-in-time rules, survivorship, accepted gaps
├── implied_erp/                 # Damodaran ERP extraction and point-in-time builder
│   ├── extract_damodaran_erp.py
│   ├── README.md
│   ├── data/
│   │   ├── erp/                 # Per-period JSON (generated, not tracked)
│   │   └── raw/                 # Downloaded .xls/.xlsx (generated, not tracked)
│   └── scripts/
│       ├── download_damodaran_erp.py
│       ├── extract_all_damodaran_erp.py
│       ├── scrape_histimpl.py
│       └── build_lean_erp_history.py
└── lean_project/                # Lean backtest
    ├── main.py                  # PbRoeAtrAlgorithm
    ├── lean.json
    ├── report.html              # Most recent backtest report (tracked artifact)
    ├── data/
    │   ├── equity_bars.py / fundamentals_history.py / damodaran_erp_history.py  # Embedded modules (generated)
    │   ├── sp500_ticker_start_end.csv   # PIT membership
    │   ├── sp500_cik_map.csv            # Ticker to CIK mapping
    │   ├── bootstrap_data.py / sp500_data.py / bar_quality.py / exclusions.py
    │   └── equity/ / alternative/       # Lean CSV zip data (generated)
    ├── universe/  pb_roe_universe.py / pit_data.py
    ├── indicators/atr_trailing_stop.py
    ├── valuation/gordon_growth.py
    ├── scripts/  download_* / repair_* / fetch_* / track_* / embed_data.py
    ├── tests/    # pytest suite
    └── Lean/     # Lean engine source (cloned, not tracked)
```

`lean_project/Lean/` is a cloned Lean engine directory. The full pipeline with per-step logs is available via `.\run_pipeline.ps1` (output to `logs/<timestamp>/`).

## Setup

```powershell
# 1. Clone and enter
git clone https://github.com/dariobizzomarquesfilho/dbmf_quant_v2.git
cd dbmf_quant_v2

# 2. Virtual environment (Windows PowerShell; Linux/macOS: python -m venv .venv && source .venv/bin/activate)
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r config/requirements.txt

# 3. Environment configuration
Copy-Item config/.env.example config/.env
# Edit config/.env: set SEC_USER="Full Name email@domain" (required for edgartools)
# Optional: BACKTEST_START, BACKTEST_END, BACKTEST_WARMUP_DAYS, TIINGO_API_KEY
```

`config/config.py` validates `SEC_USER` and computes `DATA_START = BACKTEST_START - 252 trading days` and `HISTORY_START` (earliest membership start date or `BACKTEST_HISTORY_START` if set).

## Execution

```powershell
cd lean_project
lean backtest            # or: lean backtest --config lean.json
# Open report
start report.html        # Windows; macOS: open report.html
```

Docker image `quantconnect/lean:foundation` is required. `lean_project/lean.json` date fields are overwritten by `scripts/embed_data.py` and should not be edited manually.

## Regenerating Embedded Data

Quarterly regeneration is recommended.

```powershell
cd lean_project

# Fundamentals from SEC EDGAR (point-in-time quarterly TTM)
python scripts/download_edgartools_data.py              # use --tickers AAPL MSFT for single-ticker verification

# Price bars from yfinance (full S&P 500 plus ^GSPC and ^TNX)
python scripts/download_equity_data.py
python scripts/repair_equity_data.py
python scripts/fetch_missing_delisted.py                # dry run
python scripts/fetch_missing_delisted.py --apply
python scripts/track_exclusions.py                      # verify missing-data.txt

# QuantConnect zip conversion and embedding (fails if coverage is incomplete)
python scripts/convert_to_qc_format.py
python scripts/embed_data.py
```

After `embed_data.py`, the generated `*bars.py`, `*_history.py`, and `backtest_config.py` modules remain excluded from version control by default. Commit them only when publishing a new data vintage. Refer to `docs/equity-data-pipeline.md` for recovery procedures for throttled or incomplete tickers (Scenarios A, B, C).

## Damodaran ERP Point-in-Time Pipeline

```powershell
python implied_erp/scripts/download_damodaran_erp.py             # use --dry-run to preview
python implied_erp/scripts/extract_all_damodaran_erp.py
python implied_erp/scripts/build_lean_erp_history.py             # outputs lean_project/data/damodaran_erp_history.json
cd lean_project; python scripts/embed_data.py                    # embed
# Optional annual fallback: python implied_erp/scripts/scrape_histimpl.py
```

## Testing

```powershell
python -m pytest lean_project/tests implied_erp/tests
python -m pytest lean_project/tests/test_pit_data.py -v
```

No linter or formatter is configured. The test suite is the verification gate. `run_pipeline.ps1` executes the full sequence (ERP, fundamentals and bars, embedding, tests, backtest) with logs in `logs/<yyyyMMdd_HHmmss>/`.

## Requirements

- Python 3.11 or later, Docker (for `lean backtest`)
- QuantConnect Lean CLI 1.0.228 (pinned; restored via `pip install -r config/requirements.txt`)
- `edgartools==5.52.0` provides the `edgar` module for SEC filings

Primary development environment is Windows PowerShell. The Python scripts and `lean backtest` also run on Linux and macOS (activate the virtual environment with `source .venv/bin/activate`).

## License

MIT. Refer to [LICENSE](LICENSE).
