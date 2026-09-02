# Lean Backtest: P/B versus ROE with ATR Trailing Stop

QuantConnect Lean backtest that screens S&P 500 constituents using embedded EDGAR trailing twelve month fundamentals and yfinance price data, selects constituents where Gordon-growth intrinsic P/B exceeds observed P/B, and manages exits via ATR trailing stop.

## Execution Flow

```
Embedded data (zlib and base64)
    ├── equity_bars.py            # S&P 500 history plus ^GSPC and ^TNX daily bars
    ├── damodaran_erp_history.py  # US ERP point-in-time series (2001 to 2026, per-date lookup)
    └── fundamentals_history.py   # Per-constituent quarterly point-in-time book_value, roe, eps, g_eps
            |
            # Bootstrap at startup
            # bootstrap_data.py writes CSV zip files into the Lean data directory
            # so the time loop advances on data events
            |
            v
    PbRoeAtrAlgorithm (main.py)
        ├── Initialize()
        │     ├── Load embedded data (no filesystem I/O)
        │     ├── Bootstrap CSV zip files into Lean data directory
        │     ├── AddEquity() for constituents active at the start date
        │     └── Arm OnData() as daily rebalance trigger
        ├── OnData()
        │     ├── _ensure_subscribed()  # AddEquity on index-add date (point-in-time guard)
        │     └── DailyRebalance()
        │           ├── _ensure_prices()      # SetMarketPrice from embedded bars
        │           ├── _check_stops()        # ATR trailing-stop exit
        │           ├── run_fine_selection()  # P/B versus ROE Gordon-growth screen
        │           └── SetHoldings, Liquidate, membership and spinoff exits
```

## Structure

```
lean_project/
├── main.py                  # Algorithm entry point (PbRoeAtrAlgorithm)
├── lean.json                # Lean configuration
├── data/
│   ├── equity_bars.py            # Embedded daily bars (generated)
│   ├── fundamentals_history.py   # Embedded quarterly point-in-time history (generated)
│   ├── bootstrap_data.py         # Writes CSV zip files to Lean data directory
│   ├── equity_bars.json          # Source bar data
│   ├── fundamentals_history.json # Source quarterly history (TTM per quarter, edgartools)
│   ├── sp500_data.py             # S&P 500 point-in-time membership utilities
│   ├── corporate_actions.py      # Curated spinoff exits and membership-end exits
│   ├── bar_quality.py            # Bar quality gate (ticker_quality_verdict)
│   ├── exclusions.py             # Aggregated exclusion logic
│   ├── equity/                   # Lean equity zip files (daily bars)
│   └── alternative/
│       └── interest-rate/usa/interest-rate.csv  # Lean parsing compatibility; risk-free rate uses ^TNX bars
├── universe/
│   └── pb_roe_universe.py   # Selection logic
├── indicators/
│   └── atr_trailing_stop.py # ATR trailing stop computation
├── valuation/
│   └── gordon_growth.py     # Intrinsic P/B (two-stage Gordon growth)
├── scripts/                 # Download, repair, and embedding pipeline (includes common.py helpers)
├── tests/                   # pytest suite
└── Lean/                    # QuantConnect Lean engine (cloned, not tracked)
```

## Running the Backtest

```powershell
cd lean_project
lean backtest
# Explicit configuration
lean backtest --config lean.json
```

The backtest window is defined in `config/.env` (`BACKTEST_START` and `BACKTEST_END`, defaults 2020-01-01 to 2026-08-01, path:line `config/config.py:22`) and propagated through `config/config.py` to `data/backtest_config.py` (path:line `lean_project/data/backtest_config.py:4`) and `lean.json`. The most recent executed window is 2011-01-01 to 2026-07-31 (path:line `lean_project/data/backtest_config.py:4` and `lean_project/lean.json:10`). After any change to `config/.env`, re-run `python scripts/embed_data.py`. Initial capital is $100,000 (`main.py:Initialize`, path:line `lean_project/main.py:29`; cash set at path:line `lean_project/main.py:30`). Equity bar data must cover a warm-up period of at least `BACKTEST_WARMUP_DAYS` trading days (default 252, path:line `config/config.py:28`) before `BACKTEST_START` so that rolling indicators such as beta and ATR resolve at the window start. `scripts/download_equity_data.py` fetches from `config.HISTORY_START` (path:line `config/config.py:46`) through `config.BACKTEST_END`; `scripts/embed_data.py` terminates with an error if coverage is incomplete (path:line `lean_project/scripts/embed_data.py:126`).

## Component Reference

### `main.py`

PbRoeAtrAlgorithm. Primary methods:

- `Initialize()` (path:line `lean_project/main.py:29`). Loads embedded data, bootstraps CSV zip files, registers tickers, configures daily rebalance via `OnData()`.
- `_ensure_prices()` (path:line `lean_project/main.py:219`). Sets `Security.Price` from embedded bars via `SetMarketPrice()`. Required because `Security.Price` can return 0.00 at scheduled event times without explicit injection.
- `_check_stops()` (path:line `lean_project/main.py:561`). Evaluates ATR trailing stops using embedded bar close prices.
- `DailyRebalance()` (path:line `lean_project/main.py:345`). Orchestrates the daily cycle: price injection, stop evaluation, re-screening, and rebalancing.

### `data/bootstrap_data.py`

Writes embedded equity bars as CSV zip files into the Lean data directory. At startup it:

1. Locates the Lean data directory (checks `cwd/data`, `/Lean/Data`, `/LeanCLI/data`).
2. Removes stale CSV zip files.
3. Writes `<ticker>.zip` (lowercase, no CSV extension) containing a headerless 6-column CSV: `YYYYMMDD HH:MM,open,high,low,close,volume`.
4. Writes `map_files/<ticker>.csv` for SecurityIdentifier resolution.

### `scripts/embed_data.py`

Builds embedded Python modules from JSON source files using zlib compression and base64 encoding. Execute after any update to source JSON files.

### `universe/pb_roe_universe.py`

Selection logic. Operates exclusively on embedded data. No filesystem I/O and no QuantConnect paid data feeds are used.

## Regenerating Embedded Data

Quarterly regeneration is recommended, as `book_value` changes with each earnings report.

```powershell
# Optional: refresh Damodaran ERP history (refer to ERP Pipeline below)

# 1. Quarterly point-in-time fundamentals from SEC filings (edgartools)
python scripts/download_edgartools_data.py

# 2. Equity bars (full S&P 500 constituents plus ^GSPC and ^TNX)
python scripts/download_equity_data.py
python scripts/repair_equity_data.py
python scripts/fetch_missing_delisted.py          # dry run
python scripts/fetch_missing_delisted.py --apply  # apply recovery
python scripts/track_exclusions.py                # generate coverage and exclusion report

# 3. Embed into Python modules. Terminates with an error if any current S&P 500 member is missing or out of range.
python scripts/embed_data.py
```

After step 3, the regenerated modules should be committed only when publishing a new data vintage.

Refer to `docs/equity-data-pipeline.md` for the complete procedure, failure classification, and recovery handling for throttled or incomplete tickers.

### ERP Pipeline (Damodaran Equity Risk Premium)

The Lean backtest uses a point-in-time ERP series so that each rebalance date uses the ERP available at that date.

```powershell
# 1. Download Damodaran archive files (2001 to 2025 .xls and 2026 .xlsx)
python implied_erp/scripts/download_damodaran_erp.py

# 2. Extract archive files into per-period JSON
python implied_erp/scripts/extract_all_damodaran_erp.py

# 3. Build Lean-compatible point-in-time history (US and mature-market ERP)
python implied_erp/scripts/build_lean_erp_history.py

# 4. Embed into Python module
cd lean_project; python scripts/embed_data.py
```

The archive downloader constructs URLs from the predictable naming pattern (`ctryprem00.xls` through `ctryprem25.xls`) and retrieves them via HTTP. Current 2026 files (January, April, July) are specified explicitly. All files are cached in `implied_erp/data/raw/` (not tracked). Extracted JSON files are written to `implied_erp/data/erp/`.

Point-in-time data requirement: The screening path (`fundamental_as_of` and `rolling_beta`, path:line `lean_project/universe/pit_data.py:22` and `lean_project/universe/pit_data.py:57`) requires quarterly history covering the full backtest period. `edgartools` retrieves quarterly 10-Q data from SEC EDGAR and provides trailing twelve month fundamentals per quarter. Before executing the backtest, verify that `fundamentals_history.json` contains quarterly data extending to at least the backtest start date. If data is incomplete for a given date, the screen returns no selection for that date and no position is taken. Static snapshots are not used as fallback.

EDGAR data source (`edgartools`): Retrieves quarterly 10-Q data from SEC EDGAR via the `edgar` Python module (provided by `edgartools`). Provides trailing twelve month revenue, net income, equity, and shares per quarter. No API key is required. SEC rate limit is approximately 10 requests per second.

```powershell
# Download quarterly point-in-time fundamentals from SEC filings
python scripts/download_edgartools_data.py

# Limit to specific tickers for verification
python scripts/download_edgartools_data.py --tickers AAPL MSFT GOOG

# Control the history window
python scripts/download_edgartools_data.py --backtest-start 2019-01-01
```

Point-in-time refresh model: `fundamentals_history.json` (embedded as `data/fundamentals_history.py`) stores per-constituent quarterly `book_value`, `roe`, `eps`, `revenue_ttm`, and `net_income_ttm` on each constituent's reporting cadence, computed as trailing twelve month values from SEC 10-Q filings via `edgartools`. The screen selects the latest quarter at or before the backtest date via `fundamental_as_of()`, and computes a rolling 252-day beta versus `^GSPC` from embedded bars only when evaluating a buy (initial fill or post-stop refill). No forward-looking data is accessed and no network activity occurs at runtime. Re-run the download pipeline at least quarterly to incorporate new filings.

```powershell
# 1. Quarterly point-in-time fundamentals from SEC filings (edgartools)
python scripts/download_edgartools_data.py
# --backtest-start defaults to config.DATA_START; --backtest-end defaults to config.BACKTEST_END

# 2. Equity bars from yfinance
python scripts/download_equity_data.py

# 3. Convert to QuantConnect zip format (for bootstrap)
python scripts/convert_to_qc_format.py

# 4. Embed into Python modules
python scripts/embed_data.py
```

## Observed Performance (latest backtest 2026-09-01, id 1791824131)

Window 2011-01-01 to 2026-07-31, initial capital $100,000, 252 warm-up days, 640 bootstrapped symbols (`log.txt: DIAG bootstrap_wrote=640`). Source: `backtests/2026-09-01_11-54-35/1791824131-summary.json` and `1791824131.json`.

| Metric | Value | Source field |
|--------|-------|--------------|
| End equity | $431,740 | `portfolioStatistics.endEquity` |
| Total net profit | 331.74% | `statistics.Net Profit` |
| Compounding annual return | 9.84% | `portfolioStatistics.compoundingAnnualReturn` |
| Annual standard deviation | 10.84% | `portfolioStatistics.annualStandardDeviation` |
| Sharpe ratio | 0.672 | `portfolioStatistics.sharpeRatio` |
| Sortino ratio | 0.739 | `portfolioStatistics.sortinoRatio` |
| Probabilistic Sharpe ratio | 4.84% | `statistics.Probabilistic Sharpe Ratio` |
| Information ratio | 0.675 (tracking error 0.108) | `portfolioStatistics.informationRatio` |
| Maximum drawdown | 21.7% on 2025-04-09; recovery 930 days; max drawdown duration 839 days | `portfolioStatistics.drawdown`, `drawdownRecovery`, `tradeStatistics.maximumDrawdownDuration` |
| Value at risk 99% / 95% | -1.6% / -1.1% | `portfolioStatistics.valueAtRisk99/95` |
| Portfolio turnover | 2.46% | `portfolioStatistics.portfolioTurnover` |
| Estimated capacity | $51M (lowest capacity asset JKHY) | `statistics.Estimated Strategy Capacity` |
| Total orders / closed trades | 1,547 / 754 | `statistics.Total Orders`, `tradeStatistics.totalNumberOfTrades` |
| Win rate | 44.8% (339 wins, 415 losses) | `portfolioStatistics.winRate` |
| Average win / loss | 1.15% / -0.58% | `statistics.Average Win/Loss` |
| Profit factor | 1.508 | `tradeStatistics.profitFactor` |
| Profit-loss ratio | 1.99 | `statistics.Profit-Loss Ratio` |
| Expectancy | 0.339 | `statistics.Expectancy` |
| Average trade duration | 51 days (median 42 days) | `tradeStatistics.averageTradeDuration` |
| Average winning / losing duration | 80 days / 28 days (median 71 / 24 days) | `tradeStatistics.averageWinningTradeDuration` |
| Total fees | $7,829 (volume $36.0M) | `tradeStatistics.totalFees` |

Annual returns (derived from `charts.Strategy Equity` year-end values): 2012 16.2%, 2013 24.1%, 2014 7.9%, 2015 1.0%, 2016 15.4%, 2017 19.2%, 2018 -3.4%, 2019 30.0%, 2020 44.6%, 2021 11.8%, 2022 -12.1%, 2023 4.5%, 2024 -4.5%, 2025 11.3%, 2026 (to 07-31) 5.8%. First trade 2011-05-05 (WU, `log.txt: Order BUY WU qty=1004 price=9.90`); holdings at end 7 positions, unrealized $21,951 (`runtimeStatistics`). Data requests 608 succeeded, 1 failed (`equity/usa/daily/spy.zip`, `data-monitor-report-20260901111201875.json`).

Configuration for this run (path:line `lean_project/main.py:34`): `max_positions = 10` (path:line `lean_project/main.py:34`), `atr_period = 15` (path:line `lean_project/main.py:35`), `atr_multiplier = 3.0` (path:line `lean_project/main.py:36`), `cooldown_days = 30` (path:line `lean_project/main.py:37`), `smoothing = "SMA"`. Selection via `universe/pb_roe_universe.py:run_fine_selection` (path:line `lean_project/universe/pb_roe_universe.py:188`) with `gap_pct = (implied - actual) / actual > 0` and top-`max_positions` ranking. Intrinsic P/B via `valuation/gordon_growth.py:intrinsic_pb_2stage` (path:line `lean_project/valuation/gordon_growth.py:9`) with `years_stage1 = 5`, `g_term = rf`, `r = rf + beta * erp`. Exit via `indicators/atr_trailing_stop.py:compute_atr_trailing_stop` (path:line `lean_project/indicators/atr_trailing_stop.py:16`) with ratchet.

Historical comparison: the 2026-08-29 window (id 1301645586, same 2011-2026 window) produced CAGR 8.58%, drawdown 17.2%, Sharpe 0.649, capacity $310M; the 2026-09-01 11:07 window (id 1591880909) produced CAGR 8.85%, drawdown 21.9%, Sharpe 0.593, capacity $14M. Variation reflects data vintage and fee/slippage handling; the latest run is the reference.

## Known Issues

1. **Interest rate CSV.** `data/alternative/interest-rate/usa/interest-rate.csv` previously used `YYYYMMDD` format which Lean could not parse. The file has been converted to `YYYY-MM-DD`. The strategy uses `^TNX` embedded bars for the risk-free rate. The CSV is retained for parsing compatibility.
2. **Drawdown and recovery.** Latest observed maximum drawdown is 21.7% (2025-04-09) with recovery 930 days and maximum drawdown duration 839 days (path: `backtests/2026-09-01_11-54-35/1791824131-summary.json`). Earlier windows under different code or data vintages reported higher drawdown (for example 72.1% on short 2020-2026 windows). This is a strategy characteristic. No defect is implied.
3. **yfinance negative book value.** yfinance returns negative `bookValue` and `priceToBook` for approximately 33 S&P 500 tickers (examples: SBUX, MCD, ABBV, LOW). These tickers are skipped on a per-screen basis when `book_value` is invalid. They are not removed from the cache and may have valid values in a subsequent quarter after refresh. P/B is computed as `current_price / book_value` at the evaluation date.
4. **Point-in-time coverage depth.** EDGAR XBRL fundamentals are available from approximately 2009. Many S&P 500 constituents added in later years have fundamentals only from approximately 2019 onward. Early-window universes are therefore thinner. Tickers without quarterly coverage at the evaluation date are skipped. Static snapshot fallback is not applied, as it would introduce look-ahead bias. Periodic execution of `python scripts/download_edgartools_data.py` accumulates new quarters via SEC filings.
5. **Linter and formatter.** No `flake8`, `black`, `ruff`, or `pylint` configuration is present.
