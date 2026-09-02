# Equity Data Pipeline

Procedure for downloading, repairing, validating, and embedding S&P 500 equity daily bars for the Lean backtest. This is the canonical sequence executed after any change to `config/.env` (backtest window) or when refreshing price data.

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                 Backtest Window (config/.env)                │
│                                                               │
│   BACKTEST_START  = 2011-01-01  (backtest start)               │
│   BACKTEST_END    = 2026-07-31  (backtest end)                 │
│   DATA_START      = BACKTEST_START - 252 trading days          │
│                     Warm-up so beta and ATR indicators resolve │
│   HISTORY_START   = earliest S&P 500 membership start in CSV   │
│                     Typically 1996; retains pre-window constituents │
│   Example .env path: `config/.env.example` currently sets      │
│   2011-01-01 to 2026-07-31; config defaults are 2020-01-01 to   │
│   2026-08-01 when no .env override is present                  │
└─────────────────────────────────────────────────────────────┘
         |
         v
Step 1   download_equity_data.py  (price bars)
         ├── yfinance download: HISTORY_START to BACKTEST_END
         ├── equity_bars.json   (all tickers plus ^GSPC and ^TNX)
         └── Creates equity_bars.bak.json before overwrite

Step 1b  download_edgartools_data.py  (fundamentals)
         └── fundamentals_history.json  (quarterly point-in-time, SEC EDGAR)
             Tickers with no EDGAR match are recorded as skipped

         |
         v
Step 2   repair_equity_data.py
         ├── Retries each missing ticker via yf.Ticker().history()
         ├── Tests symbol variants (BRK-B to BRK.B, etc.)
         ├── Applies growing backoff on HTTP 429
         ├── Merges recovered bars into equity_bars.json
         └── Classifies outcomes:
              recovered           -> merged
              resolved_via_alias  -> successor covers the window (diagnostic only)
              throttled (PENDING) -> rate-limited; re-run required after cooldown
              unavailable         -> delisted or never traded
             If PENDING is non-empty, re-run Step 2 after cooldown

         |
         v
Step 3   fetch_missing_delisted.py
         ├── Computes window members still missing
         ├── Source 1: curated RENAME_MAP (CTL to LUMN, ANTM to ELV, etc.)
         │     Slices successor continuous series across predecessor window
         ├── Source 2: Tiingo fallback (TIINGO_API_KEY in config/.env)
         │     US-exchange guard applied; foreign collisions rejected as tiingo_foreign_collision
         ├── Remaining missing -> equity_unavailable.json (tracked gap)
         └── Execute as dry run first, then --apply

         |
         v
Step 4   track_exclusions.py
         ├── Applies bar quality gate to every ticker in equity_bars.json
         ├── Cross-references membership and unavailable records
         ├── Writes missing-data.txt  (human-readable report)
         ├── Writes missing-data.json (machine-readable)
         └── Flags BROKEN tickers (malformed or unadjusted split data)
             BROKEN tickers require manual review

         |
         v
Step 5   embed_data.py
         ├── Validates coverage; terminates with error if any current S&P 500 member is missing or out of range
         ├── Compresses each JSON to zlib and base64 Python module
         ├── Writes equity_bars.py, damodaran_erp_history.py, and related modules
         └── Updates lean.json start and end dates

         |
         v
    Commit generated modules when publishing a new data vintage
```

## Quick Start

From the repository root with the virtual environment activated:

```powershell
.venv\Scripts\Activate.ps1
Set-Location lean_project

# 1. Download
python scripts/download_equity_data.py

# 2. Repair throttled or masked tickers
python scripts/repair_equity_data.py

# 3. Recover delisted and renamed tickers; generate unavailable record
python scripts/fetch_missing_delisted.py          # dry run
python scripts/fetch_missing_delisted.py --apply  # apply changes

# 4. Generate coverage and exclusion report
python scripts/track_exclusions.py

# 5. Embed into compressed Python modules
python scripts/embed_data.py
```

## Step-by-Step Reference

### Step 1: `download_equity_data.py`

Fetches the full S&P 500 membership from `sp500_ticker_start_end.csv` plus `^TNX` (10-year Treasury, risk-free rate) and `^GSPC` (S&P 500 index, beta), via yfinance with `auto_adjust=True`.

Output files in `lean_project/data/`:

| File | Description |
|------|-------------|
| `equity_bars.json` | Dictionary `{ticker: {date: {open, high, low, close, volume}}}`. Daily OHLCV bars clipped to each ticker's S&P 500 membership interval |
| `fundamentals_history.json` | Produced separately by `download_edgartools_data.py`. Quarterly point-in-time history from SEC EDGAR (fields include book_value, roe, eps, sic, business_category, g_eps) |
| `equity_bars.bak.json` | Backup of the previous `equity_bars.json` before overwrite |

Options:

```powershell
python scripts/download_equity_data.py --tickers AAPL MSFT GOOG   # specific tickers only
python scripts/download_equity_data.py --fundamentals-only        # restrict to tickers with PIT fundamentals plus ^TNX and ^GSPC
python scripts/download_equity_data.py --refresh-sp500            # re-download S&P 500 membership list
python scripts/download_equity_data.py --bars-path lean_project/data/equity_bars.json  # custom output path
python scripts/download_equity_data.py --start-date 2011-01-01 --end-date 2026-07-31     # custom window (defaults: HISTORY_START to BACKTEST_END)
```

Tickers for which yfinance returns no data are logged but not stored. This condition typically indicates rate limiting (Yahoo may return empty during throttling) or a delisted constituent. Such tickers are addressed in Steps 2 and 3.

### Step 1b: `download_edgartools_data.py`

Downloads quarterly point-in-time fundamentals from SEC EDGAR via `edgartools`. This script is the sole owner of `fundamentals_history.json`. Running `download_equity_data.py` does not modify fundamentals.

```powershell
python scripts/download_edgartools_data.py
python scripts/download_edgartools_data.py --tickers AAPL MSFT GOOG
python scripts/download_edgartools_data.py --backtest-start 2019-01-01
python scripts/download_edgartools_data.py --output-dir lean_project/data
python scripts/download_edgartools_data.py --max-tickers 10
python scripts/download_edgartools_data.py --force
python scripts/download_edgartools_data.py --clean-skip
python scripts/download_edgartools_data.py --refresh-sp500
```

Key behaviors:

- Keys history by `filing_date` (SEC acceptance date), not fiscal period end. Refer to `docs/data-limitations.md` for the point-in-time rule.
- Computes trailing twelve month values per quarter from 10-Q filings. Resolves delisted constituents via `sp500_cik_map.csv` when available.
- Persists progress incrementally; interrupted runs can be resumed. `--force` re-fetches tickers already present; `--clean-skip` clears the persisted skip set (`fundamentals_no_edgar_match.json`).

### Step 2: `repair_equity_data.py`

Retries every ticker present in the membership CSV but absent from `equity_bars.json`. This is the first recovery pass.

Procedure:

1. Computes the set of missing tickers (membership keys not in `equity_bars.json`, restricted to the tradeable universe defined by fundamentals).
2. For each ticker, attempts `yf.Ticker().history()` with `auto_adjust=True` across symbol variants (for example `BRK-B` to `BRK.B`).
3. Applies a growing backoff schedule (`[5, 15, 30, 60, 120, 300]` seconds) across multiple passes to accommodate rate limiting.
4. Paces each request by 1.0 second.
5. Merges recovered bars into `equity_bars.json` clipped to the membership interval.

Failure classification:

| Category | Meaning | Required Action |
|----------|---------|-----------------|
| `recovered` | Data returned and merged | None |
| `resolved_via_alias` | No direct data; a successor ticker covers the window | Diagnostic only |
| `throttled (PENDING)` | Rate-limited or empty response for a current S&P 500 member | Re-run after cooldown period |
| `unavailable` | Delisted or never traded on a US exchange | Proceed to Step 3 |

If the report indicates a non-empty throttled list, a cooldown period of several hours is required before re-running this script. `embed_data.py` will terminate with an error if current members remain missing.

Example output:

```
Requested 752, present 737, missing 15
  Repair pass 1: 25/15 (recovered so far 3)...
Recovered 5 tickers; resolved-via-alias 2; throttled PENDING 3; genuinely unavailable 5

THROTTLED PENDING (re-run repair_equity_data.py later):
  BK [CURRENT]: rate-limit: ...
  CMA [CURRENT]: rate-limit: ...
  PLTR [CURRENT]: empty (throttling-masked)
```

### Step 3: `fetch_missing_delisted.py`

Recovers tickers that Step 2 could not retrieve, primarily delisted or renamed S&P 500 constituents.

Two recovery sources are applied in order:

#### (a) Curated Rename Map (`RENAME_MAP`)

Defined in `lean_project/data/delisted_aliases.py`. Each entry is a manually verified predecessor to successor pair where the ticker change does not coincide with a corporate action that would distort the adjusted series:

| Predecessor | Successor | Description |
|-------------|-----------|-------------|
| `CTL` | `LUMN` | CenturyLink to Lumen |
| `ANTM` | `ELV` | Anthem to Elevance Health |
| `BLL` | `BALL` | Ball Corporation ticker change |
| `PKI` | `RVTY` | PerkinElmer to Revvity |
| `WLTW` | `WTW` | Willis Towers Watson to WTW |
| `NLOK` | `GEN` | Symantec to Gen Digital |
| `ABC` | `COR` | AmerisourceBergen to Cencora |
| `GPS` | `GAP` | Gap ticker change |
| `FLT` | `CPAY` | FleetCor to Corpay |
| `PEAK` | `DOC` | Healthpeak to DOC |
| `HFC` | `DINO` | HollyFrontier to HF Sinclair |
| `FRC` | `FRCB` | First Republic to receivership ticker |
| `FI` | `FISV` | Fiserv ticker change |
| `RE` | `EG` | Easterly Government Properties |

The successor's continuous adjusted series is sliced to the predecessor's membership window and stored under the predecessor key (`fetch_missing_delisted.py:_rename_bars`, path:line `lean_project/scripts/fetch_missing_delisted.py:195`).

#### (b) Tiingo Fallback

For tickers not in the rename map and still without data, the script queries Tiingo (`TIINGO_API_KEY` in `config/.env`). Tiingo may provide historical coverage not available from Yahoo.

US-exchange guard: Tiingo `exchangeCode` metadata is verified against the set `US_EXCHANGES` (path:line `lean_project/scripts/fetch_missing_delisted.py:74`). Foreign-listing collisions (for example `BK` resolving to TSX, `MMC` and `COG` to ASX) are rejected as `tiingo_foreign_collision`.

Tiingo free tier limits are 500 symbols per month and 1000 requests per day. A full S&P 500 recovery may require execution across multiple days or a higher tier. Rate-limited Tiingo requests are deferred, not marked unavailable, so they can be retried.

#### Dry Run and Apply

Always execute a dry run first:

```powershell
python scripts/fetch_missing_delisted.py
```

Output includes a per-ticker plan:

```
Plan summary by source:
  rename: 13
  tiingo: 46
  unavailable: 8

Per-ticker:
  ABC     rename      ok              ABC->COR
  ANTM    rename      ok              ANTM->ELV
  BK      tiingo      rejected        tiingo_foreign_collision:TSX
```

Apply changes:

```powershell
python scripts/fetch_missing_delisted.py --apply
```

This writes:

- Updated `equity_bars.json` (previous version backed up to `.bak.json`).
- `equity_unavailable.json`: explicit record of unrecoverable tickers with `reason` fields (`tiingo_no_prices`, `tiingo_foreign_collision`, `unrecoverable`, `excluded_complex`).

`equity_unavailable.json` is the tracked survivorship-gap record. Both `embed_data.py` and `track_exclusions.py` read this file.

### Step 4: `track_exclusions.py`

Generates the consolidated exclusion report: the complete list of tickers not included in the backtest and the reason for exclusion.

Procedure:

1. Applies the bar quality gate (`data/bar_quality.py:ticker_quality_verdict`) to every ticker in `equity_bars.json`. Detects malformed OHLC, all-zero prices, and extreme single-day moves (greater than 60 percent, indicative of an unadjusted split or incorrect instrument).
2. Computes the set of S&P 500 membership-window constituents still absent from bars.
3. Cross-references with `equity_unavailable.json` to classify gaps.

Output files in `lean_project/data/`:

| File | Description |
|------|-------------|
| `missing-data.txt` | Human-readable table of exclusions with reasons |
| `missing-data.json` | Machine-readable category data |

Exclusion categories:

| Category | Meaning | Severity |
|----------|---------|----------|
| `broken` | Failed the quality gate; malformed or unadjusted data | Requires manual review |
| `missing_window` | Constituent in the S&P 500 window but absent from `equity_bars` and not documented in `equity_unavailable.json` | Requires investigation; do not proceed to embedding |
| `documented_unavailable` | Absent from bars but present in `equity_unavailable.json` with a `reason` | Expected |

Throttling status is reported by `repair_equity_data.py` via its PENDING list. It is not tracked in this report.

### Step 5: `embed_data.py`

Compresses JSON data files into self-contained Python modules (zlib and base64) so the Lean algorithm has no filesystem I/O at runtime (`lean_project/scripts/embed_data.py:17`).

Pre-embedding validation:

- `equity_bars.json` must span `[DATA_START, BACKTEST_END]`. Failure terminates the process.
- Every current S&P 500 member (`end_date` is `None` in the CSV) that is part of the tradeable universe (has fundamentals) must have bars covering the window. Failure terminates the process after embedding.
- `fundamentals_history.json` coverage below threshold produces a warning or error.
- `damodaran_erp_history.json` must exist. If absent, the process terminates with instructions to run the ERP pipeline.

Generated modules in `lean_project/data/`:

| Source JSON | Embedded Module | Loader |
|-------------|----------------|--------|
| `equity_bars.json` | `equity_bars.py` | `load_equity_bars()` |
| `fundamentals_history.json` | `fundamentals_history.py` | `load_fundamentals_history()` |
| `damodaran_erp_history.json` | `damodaran_erp_history.py` | `load_damodaran_erp_history()` |
| `implied_erp/data/histimpl_us_erp.json` | `damodaran_erp_history_us.py` | `load_damodaran_erp_history_us()` (when present) |
| `config/.env` values | `backtest_config.py` | `load_backtest_window()` |

`lean.json` `start-date` and `end-date` are updated to match `config/.env`.

## Recovery Scenarios

### Scenario A: Throttled PENDING Tickers After Step 2

Yahoo may return empty responses during rate limiting for actively traded tickers. Examples from prior runs include COIN, PLTR, BK, and CMA.

Resolution: Wait 4 to 24 hours for the Yahoo rate limit to reset, then re-run:

```powershell
Set-Location lean_project
python scripts/repair_equity_data.py
```

Repeat until the throttled PENDING list is empty, then proceed to Steps 3 through 5.

### Scenario B: Tickers in `equity_unavailable.json` with Active Membership

If a ticker appears in `equity_unavailable.json` but its S&P 500 interval indicates it should still be active (its `end_date` is `None` or extends past `BACKTEST_START`), the entry represents a data gap, not an expected delisting.

Investigation:

1. Inspect the `reason` field in `equity_unavailable.json`.
2. If `tiingo_foreign_collision`, the ticker resolved to a foreign listing on Tiingo while the actual constituent is US-traded. Attempt a direct download:
   ```powershell
   python scripts/download_equity_data.py --tickers THE_TICKER
   ```
3. If `unrecoverable`, test symbol variants directly with yfinance:
   ```python
   import yfinance as yf
   yf.Ticker("THE_TICKER").history(start="...", auto_adjust=True)
   ```

### Scenario C: BROKEN Tickers (Step 4 Quality Gate)

The `broken` category in `missing-data.json` indicates tickers whose bars failed the quality gate. Examples from prior runs:

| Ticker | Issue | Probable Cause |
|--------|-------|----------------|
| `DEC` | `malformed_rows 691/1359` | Approximately 51 percent of rows fail OHLC containment; likely incorrect instrument or unadjusted data |
| `TNB` | `extreme_moves 305/784` | Approximately 40 percent of days show moves greater than 60 percent; likely unadjusted split or symbol misresolution |

Resolution: Remove affected tickers from `equity_bars.json` and record them in the exclusion report. The quality gate exists to prevent corrupt data from entering the backtest. If source data appears correctable, re-download the specific ticker as described in Scenario B.

### Scenario D: Full Re-download After S&P 500 Membership Refresh

When the S&P 500 membership CSV is refreshed:

```powershell
Set-Location lean_project
python scripts/download_equity_data.py --refresh-sp500
python scripts/repair_equity_data.py
python scripts/fetch_missing_delisted.py
python scripts/fetch_missing_delisted.py --apply
python scripts/track_exclusions.py
python scripts/embed_data.py
```

## Configuration

Backtest dates are controlled via `config/.env` (example values from `config/.env.example` which currently sets 2011-01-01 to 2026-07-31; code defaults when no override is present are 2020-01-01 to 2026-08-01, path:line `config/config.py:22`):

```env
# Backtest window (single source of truth)
BACKTEST_START="2011-01-01"
BACKTEST_END="2026-07-31"

# Warm-up days before BACKTEST_START (default: 252 trading days)
BACKTEST_WARMUP_DAYS="252"

# Optional floor for history fetch (default: earliest CSV membership start)
# BACKTEST_HISTORY_START="1996-01-02"

# SEC identity for edgartools (required)
SEC_USER="Name email@domain.com"

# Tiingo API key for delisted recovery fallback
TIINGO_API_KEY="your_key_here"
```

Derived values computed in `config/config.py`:

| Variable | Definition |
|----------|------------|
| `DATA_START` | `BACKTEST_START` minus 252 trading days (NYSE calendar via `data/nys_calendar.py:trading_days_before`, path:line `config/config.py:82`) |
| `HISTORY_START` | `BACKTEST_HISTORY_START` if set, otherwise the earliest `start_date` in `sp500_ticker_start_end.csv`, otherwise `1996-01-02` (path:line `config/config.py:46`) |

## File Reference

### Data Files (`lean_project/data/`)

| File | Generated By | Description |
|------|-------------|-------------|
| `sp500_ticker_start_end.csv` | External download | S&P 500 membership with start and end dates |
| `sp500_cik_map.csv` | `download_edgartools_data.py` / `build_cik_map.py` | Ticker to CIK mapping |
| `equity_bars.json` | `download_equity_data.py` | Source daily OHLCV bars |
| `equity_bars.py` | `embed_data.py` | Embedded bars (zlib and base64) |
| `fundamentals_history.json` | `download_edgartools_data.py` | Quarterly point-in-time history (SEC EDGAR) |
| `fundamentals_history.py` | `embed_data.py` | Embedded quarterly history |
| `damodaran_erp_history.json` | `implied_erp/` pipeline | Point-in-time ERP history |
| `equity_unavailable.json` | `fetch_missing_delisted.py` | Tracked survivorship gaps |
| `missing-data.txt` | `track_exclusions.py` | Human-readable exclusion report |
| `missing-data.json` | `track_exclusions.py` | Machine-readable exclusion report |
| `*.bak.json` | Download, repair, and fetch scripts | Backup before overwrite |

### Scripts (`lean_project/scripts/`)

| Script | Purpose |
|--------|---------|
| `download_equity_data.py` | Step 1: yfinance price bar download |
| `download_edgartools_data.py` | Step 1b: SEC EDGAR quarterly fundamentals download |
| `repair_equity_data.py` | Step 2: retry throttled and masked tickers |
| `fetch_missing_delisted.py` | Step 3: recovery via rename map and Tiingo |
| `track_exclusions.py` | Step 4: coverage and exclusion reporting |
| `embed_data.py` | Step 5: compression to Python modules |
| `convert_to_qc_format.py` | Conversion of JSON bars to QuantConnect zip format (for Lean CSV zip bootstrap) |

### Support Modules (`lean_project/data/`)

| Module | Purpose |
|--------|---------|
| `sp500_data.py` | Membership loading, interval clipping, alias map construction |
| `delisted_aliases.py` | Curated `RENAME_MAP` and corporate action screening |
| `bar_quality.py` | OHLC containment and extreme-move quality gate |
| `exclusions.py` | Exclusion categorization and report rendering |
| `corporate_actions.py` | Spinoff dates (excluded from quality gate) |
| `nys_calendar.py` | NYSE trading calendar utilities |
| `equity_bars.py` | Embedded bars (generated; do not edit) |
