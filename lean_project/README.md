# Lean Backtest — P/B vs ROE ATR Trailing Stop

QuantConnect Lean backtest for the DBMF Quant strategy. Screens S&P 500 constituents using embedded edgartools TTM fundamentals (from SEC 10-Q filings) + yfinance price data, selects undervalued stocks via Gordon-growth implied P/B, and exits positions with ATR trailing stops.

## How It Works

```
Embedded data (zlib+base64)
    │
    ├── equity_bars.py            → full S&P 500 history + ^GSPC + ^TNX daily bars
    ├── damodaran_erp_history.py  → US ERP PIT series (2001-2026, per-date lookup)
    └── fundamentals_history.py   → per-company quarterly PIT book_value / roe / eps / g_eps
    │
    ┌─── Bootstrap (at startup) ──────────────────────────────┐
    │  bootstrap_data.py writes CSV.zip files into Lean's     │
    │  data folder so the time loop advances on data events   │
    └──────────────────────────────────────────────────────────┘
    │
    ▼
PbRoeAtrAlgorithm (main.py)
    │
    ├── Initialize()
    │     ├── Load all embedded data (no disk I/O)
    │     ├── Bootstrap CSV.zip files into Lean data folder
    │     ├── AddEquity() for members active at the start date
    │     └── OnData triggers DailyRebalance on each new trading day
    │
    ├── OnData()
    │     ├── _ensure_subscribed() → AddEquity on index-add dates (PIT guard)
    │     └── DailyRebalance()
    │           ├── _ensure_prices()     → SetMarketPrice from embedded bars
    │           ├── _check_stops()       → ATR trailing stop exit
    │           ├── run_fine_selection() → P/B vs ROE Gordon-growth rescreen
    │           └── SetHoldings / Liquidate / membership + spinoff exits
```

## Architecture

```
lean_project/
├── main.py                  # Algorithm entry point
├── lean.json                # Lean v2 config
├── data/
│   ├── equity_bars.py            # Embedded daily bars (auto-generated)
│   ├── fundamentals_history.py   # Embedded quarterly PIT history (auto-generated)
│   ├── bootstrap_data.py        # Writes CSV.zip to Lean data folder
│   ├── equity_bars.json         # Source bars data (for regeneration)
│   ├── fundamentals_history.json # Source quarterly PIT history (TTM per quarter, edgartools)
│   ├── sp500_data.py            # S&P 500 PIT membership utilities
│   ├── corporate_actions.py     # Curated spinoff exits + membership-end exits
│   ├── bar_quality.py           # Bar-quality gate (ticker_quality_verdict)
│   ├── exclusions.py            # Aggregate excluded tickers for the report
│   ├── equity/                   # Lean equity .zip files (daily bars)
│   └── alternative/
│       └── interest-rate/usa/interest-rate.csv  # Fixed: dates now in YYYY-MM-DD format
├── universe/
│   └── pb_roe_universe.py   # Fine selection logic
├── indicators/
│   └── atr_trailing_stop.py  # ATR trailing stop computation
├── valuation/
│   └── gordon_growth.py      # Intrinsic P/B (2-stage Gordon growth)
├── scripts/                  # Download / repair / embed pipeline (+ common.py helpers)
├── tests/                    # pytest suite
└── Lean/                     # QuantConnect Lean framework
```

## Running the Backtest

```powershell
cd lean_project

# Run backtest
lean backtest

# Or with explicit config
lean backtest --config lean.json
```

The backtest window comes from `config/.env` (`BACKTEST_START`/`BACKTEST_END`, env-overridable) and is propagated through `config/config.py` → `data/backtest_config.py` → `lean.json`; re-run `python scripts/embed_data.py` after changing `.env`. Initial capital is $100,000. Equity bar data must additionally cover a warm-up window before `BACKTEST_START` (>= `BACKTEST_WARMUP_DAYS` trading days, default 252) so rolling indicators like beta/ATR have enough prior bars. `scripts/download_equity_data.py` pulls from `config.HISTORY_START` through `config.BACKTEST_END`; `scripts/embed_data.py` hard-fails if coverage is missing.

## Key Files Explained

### `main.py`
The algorithm. Key methods:
- **`Initialize()`** — Loads embedded data, bootstraps CSV.zip files, registers tickers, sets up daily rebalance via `OnData`.
- **`_ensure_prices()`** — Explicitly sets `Security.Price` from embedded bars via `SetMarketPrice()`. This fixes a Lean bug where `Security.Price` returns 0.00 at scheduled event times.
- **`_check_stops()`** — Checks ATR trailing stops using embedded bar close prices (not `Security.Price` which can be stale).
- **`DailyRebalance()`** — Orchestrates the daily cycle: ensure prices → check stops → rescreen → rebalance.

### `data/bootstrap_data.py`
Writes embedded equity bars as CSV.zip files into Lean's data folder. At runtime, it:
1. Finds Lean's data directory (checks cwd/data, /Lean/Data, /LeanCLI/data)
2. Deletes stale CSV.zip files
3. Writes `<ticker>.zip` (lowercase, no .csv extension) with no-header 6-column CSV: `YYYYMMDD HH:MM,open,high,low,close,volume`
4. Writes `map_files/<ticker>.csv` for SecurityIdentifier resolution

### `scripts/embed_data.py`
Build script that converts JSON data files to embedded Python modules using zlib compression + base64 encoding. Run after updating any source JSON file.

### `universe/pb_roe_universe.py`
Fine selection logic. Uses embedded data only (no file I/O, no QC paid feeds).

## Regenerating Embedded Data

If you need to refresh the embedded data (recommended quarterly since `book_value` changes every earnings report):

```powershell
# 0. (Optional) Refresh Damodaran ERP history — see ERP Pipeline below

# 1. Download quarterly PIT fundamentals from SEC filings (edgartools)
python scripts/download_edgartools_data.py

# 2. Download + repair equity bars (full S&P 500 list + ^GSPC + ^TNX)
python scripts/download_equity_data.py
python scripts/repair_equity_data.py           # retry throttled/masked tickers
python scripts/fetch_missing_delisted.py       # dry-run: review the plan
python scripts/fetch_missing_delisted.py --apply  # recover rename/Tiingo gaps
python scripts/track_exclusions.py            # generate coverage + exclusion report

# 3. Embed everything into Python modules (hard-fails on missing current members)
python scripts/embed_data.py
```

After step 3, commit the regenerated `data/*_json.py`, `data/*_bars.py`, `data/fundamentals_history.py`, and `data/damodaran_erp_history.py` files.

> **Full pipeline documentation** — see [`docs/equity-data-pipeline.md`](../docs/equity-data-pipeline.md)
> for the step-by-step guide, failure classification table, and recovery
> scenarios for throttled/broken tickers.

### ERP Pipeline (Damodaran Equity Risk Premium)

The Lean backtest uses a point-in-time (PIT) ERP series so the backtest uses the correct ERP at each rebalance date (no look-ahead bias).

```powershell
# 1. Download Damodaran archive files (2001-2025 .xls + 2026 .xlsx)
python implied_erp/scripts/download_damodaran_erp.py

# 2. Extract all files into per-period JSONs
python implied_erp/scripts/extract_all_damodaran_erp.py

# 3. Build Lean-compatible PIT history (US + mature-market ERP only)
python implied_erp/scripts/build_lean_erp_history.py

# 4. Embed into Python module
cd lean_project && python scripts/embed_data.py
```

The archive downloader generates URLs from the predictable naming pattern (`ctryprem00.xls` … `ctryprem25.xls`) and downloads them via HTTP. The current 2026 files (Jan/Apr/Jul) are hardcoded. All files are cached in `implied_erp/data/raw/` (gitignored) and extracted JSONs live in `implied_erp/data/erp/`.

**PIT data requirement:** The point-in-time screening path (`fundamental_as_of` + `rolling_beta`) requires complete quarterly history for all tickers covering the full backtest period. edgartools pulls quarterly 10-Q data directly from SEC EDGAR, providing TTM fundamentals per quarter going back to 2019. **Before running the backtest, ensure `fundamentals_history.json` has quarterly data going back to at least the backtest start date.** If data is incomplete, the screen will return empty results for dates without quarterly coverage and no positions will be taken. Do NOT use stale static snapshots as a fallback — this would introduce look-ahead bias.

**edgartools (primary source for SEC data):** Pulls quarterly 10-Q data directly from SEC EDGAR via the `edgar` Python module (provided by the pinned `edgartools` package). Provides TTM revenue, net_income, equity, and shares per quarter. No API key required. Rate-limited to ~10 requests/second by SEC.

```powershell
# Download quarterly PIT fundamentals from SEC filings
python scripts/download_edgartools_data.py

# Limit to specific tickers for testing
python scripts/download_edgartools_data.py --tickers AAPL MSFT GOOG

# Control backtest start date for history
python scripts/download_edgartools_data.py --backtest-start 2019-01-01
```

**Point-in-time refresh model:** `fundamentals_history.json` (embedded as `data/fundamentals_history.py`) stores each company's quarterly `book_value` / `roe` / `eps` / `revenue_ttm` / `net_income_ttm` on its own quarter-end cadence, computed as TTM from SEC 10-Q filings via edgartools. The screen reads the latest quarter at-or-before the backtest date via `fundamental_as_of()`, and computes a rolling 252-day beta (vs `^GSPC` from embedded bars) only when scanning for a buy (initial fill or post-stop refill). No look-ahead, zero network at runtime. Re-run the download pipeline at least quarterly so each company's future quarters get refreshed.

```powershell
# 1. Download quarterly PIT fundamentals from SEC filings (edgartools)
python scripts/download_edgartools_data.py

#    Use --backtest-start to control how far back history goes (default: config.DATA_START; --backtest-end defaults to config.BACKTEST_END)

# 2. Download fresh equity bars from yfinance
python scripts/download_equity_data.py

# 3. Convert to QC zip format (for bootstrap)
python scripts/convert_to_qc_format.py

# 4. Embed everything into Python modules
python scripts/embed_data.py
```

After step 4, commit the regenerated `data/*_json.py`, `data/*_bars.py`, `data/fundamentals_history.py`, and `data/damodaran_erp_history.py` files.

## Known Issues

1. **Interest rate CSV**: `data/alternative/interest-rate/usa/interest-rate.csv` had dates in `YYYYMMDD` format which Lean couldn't parse. Fixed by converting to `YYYY-MM-DD`. The strategy uses ^TNX from embedded bars for the risk-free rate, so this file is cosmetic only.
2. **82% max drawdown**: Strategy design concern, not a bug.
3. **yfinance negative bookValue**: yfinance returns negative `bookValue` and `priceToBook` for ~33 S&P 500 tickers (SBUX, MCD, ABBV, LOW, etc.). These tickers are skipped per-screen when book_value is invalid — they are not dropped from the cache and may have valid book_value in a future quarter after refresh. P/B is always computed dynamically as `current_price / book_value` for the correct time period.
4. **PIT coverage depth varies by ticker**: edgartools XBRL facts reach back to ~2009 and many later-added S&P 500 names only gained fundamentals ~2019, so early-window universes are thinner (SEC source limit). When quarterly data is unavailable for a ticker at a given backtest date, the screen skips it (no static fallback — using current data for historical dates would be look-ahead bias). Run `python scripts/download_edgartools_data.py` periodically to accumulate new quarters via SEC filings.
5. **No linter or formatter configured**: No `flake8`, `black`, `ruff`, or `pylint` configuration.
