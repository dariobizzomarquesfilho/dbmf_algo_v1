# Equity Data Pipeline

End-to-end workflow for downloading, repairing, validating, and embedding
S&P 500 equity daily bars into the Lean backtest. This is the canonical
sequence run after any change to `config/.env` (backtest window) or whenever
refreshing price data.

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                 BACKTEST WINDOW (config/.env)                │
│                                                               │
│   BACKTEST_START  = 2020-01-01  (backtest begins here)        │
│   BACKTEST_END    = 2026-08-01  (backtest ends here)          │
│   DATA_START      = ~2018-12-26 (252 trading days before start)│
│              (warm-up history so beta/ATR indicators resolve) │
│   HISTORY_START   = earliest S&P 500 membership start in CSV   │
│              (usually 1996 — retains pre-window constituents) │
└─────────────────────────────────────────────────────────────┘
         │
         ▼
Step 1  download_equity_data.py
         ├── yfinance download: HISTORY_START → BACKTEST_END
         ├── equity_bars.json   (all tickers + ^GSPC + ^TNX)
         ├── fundamentals.json     (snapshot: P/B, ROE, market cap, etc.)
         ├── fundamentals_history.json  (quarterly PIT from yfinance)
         └── spares a .bak.json before overwriting
         │   Some tickers drop here (Yahoo rate-limits → "empty" return)
         │
         ▼
Step 2  repair_equity_data.py
         ├── retries each missing ticker via yf.Ticker().history()
         ├── tries symbol variants (BRK-B → BRK.B, etc.)
         ├── with growing-backoff on HTTP 429
         ├── merges recovered bars back into equity_bars.json
         └── classifies failures:
              recovered        → merged in
              resolved_via_alias → successor has the data (diagnostics)
              throttled (PENDING)→ rate-limited, MUST re-run later
              unavailable       → genuinely delisted/never-traded
         │   If PENDING list is non-empty → re-run Step 2 after cooldown
         │
         ▼
Step 3  fetch_missing_delisted.py
         ├── computes window members still missing
         ├── source 1: curated RENAME_MAP (CTL→LUMN, ANTM→ELV, etc.)
         │     slices successor's continuous series across predecessor window
         ├── source 2: Tiingo fallback (TIINGO_API_KEY in config/.env)
         │     with mandatory US-exchange guard (rejects TSX/ASX collisions)
         ├── anything still missing → equity_unavailable.json (tracked gap)
         └── ALWAYS dry-run first, then --apply
         │
         ▼
Step 4  track_exclusions.py
         ├── runs bar-quality gate on every ticker in equity_bars.json
         ├── cross-references membership + unavailable + throttled
         ├── writes missing-data.txt  (human-readable report)
         ├── writes missing-data.json  (machine-readable)
         └── flags BROKEN tickers (malformed/unadjusted-split data)
         │   → broken tickers need manual review
         │
         ▼
Step 5  embed_data.py
         ├── hard-fails if any CURRENT S&P 500 member is missing/out-of-range
         ├── compresses each JSON → zlib + base64 Python module
         ├── writes equity_bars.py, damodaran_erp_json.py, etc.
         └── updates lean.json start/end dates
         │
         ▼
    COMMIT the generated *_json.py / *_bars.py modules
```

## Quick Start

From the repository root, with the virtualenv activated:

```powershell
.venv\Scripts\Activate.ps1
cd lean_project

# 1. Download
python scripts/download_equity_data.py

# 2. Repair — retry throttled/masked tickers
python scripts/repair_equity_data.py

# 3. Recover delisted/rename tickers + generate unavailable record
python scripts/fetch_missing_delisted.py          # dry-run: review the plan
python scripts/fetch_missing_delisted.py --apply   # commit changes

# 4. Generate coverage + exclusion report
python scripts/track_exclusions.py

# 5. Embed into compressed Python modules for Lean
python scripts/embed_data.py
```

## Step-by-Step

### Step 1 — `download_equity_data.py`

Fetches the full S&P 500 membership (from `sp500_ticker_start_end.csv`) plus
`^TNX` (10-year Treasury for risk-free rate) and `^GSPC` (S&P 500 index for beta),
all pulled via yfinance with `auto_adjust=True`.

**Output files** (in `lean_project/data/`):

| File | Description |
|------|-------------|
| `equity_bars.json` | `{ticker: {date: {open, high, low, close, volume}}}` — daily OHLCV bars clipped to each ticker's S&P 500 membership interval |
| `fundamentals.json` | Snapshot fundamentals per ticker (P/B, ROE, EPS, market cap, sector, etc.) |
| `fundamentals_history.json` | Quarterly PIT history from yfinance (~7 quarters per ticker) |
| `equity_bars.bak.json` | Backup of the previous `equity_bars.json` before overwrite |

**Options:**

```powershell
python scripts/download_equity_data.py --tickers AAPL MSFT GOOG   # specific tickers only
python scripts/download_equity_data.py --bars-only                # skip fundamentals
python scripts/download_equity_data.py --fundamentals-only        # skip bars
python scripts/download_equity_data.py --history-only             # quarterly PIT only
python scripts/download_equity_data.py --refresh-sp500            # re-download S&P 500 list
```

**What gets dropped:** Tickers that yfinance returns as "empty" (no data) are
logged but not stored. This is typically rate-limiting (Yahoo masks real data
as empty during throttling) or genuinely delisted tickers. These are the
targets of Steps 2 and 3.

---

### Step 2 — `repair_equity_data.py`

Retries every ticker that's listed in the membership CSV but missing from
`equity_bars.json`. This is the first recovery pass.

**What it does:**

1. Computes the set of missing tickers (membership keys not in `equity_bars.json`)
2. For each, tries `yf.Ticker().history()` with `auto_adjust=True` across multiple
   **symbol variants** (e.g. `BRK-B` → `BRK.B`, `BRK-B` → `BRK-B`)
3. Uses a **growing backoff** schedule (`[5, 15, 30, 60, 120, 300]` seconds) across
   multiple passes to ride out rate-limiting
4. Paces each request by 1.0s to avoid triggering throttling
5. Merges any recovered bars back into `equity_bars.json` (clipped to membership)

**Failure classification:**

| Category | Meaning | Action |
|----------|---------|--------|
| `recovered` | Data returned and merged | Done |
| `resolved_via_alias` | No direct data, but a near-day successor covers the window | Diagnostics only; no action needed |
| `throttled (PENDING)` | Rate-limited or masked-empty for a **current** S&P 500 member | **Re-run after cooldown** |
| `unavailable` | Genuinely delisted / never traded on a US exchange | Expected; proceed to Step 3 |

> **Critical:** If the report ends with "WARN: throttled list is non-empty", you
> must wait for a cooldown period (hours, not minutes) and re-run this script
> before proceeding to `embed_data.py`, which will hard-fail on missing current
> members.

**Example output:**
```
Requested 752, present 737, missing 15
  Repair pass 1: 25/15 (recovered so far 3)...
Recovered 5 tickers; resolved-via-alias 2; throttled PENDING 3; genuinely unavailable 5

THROTTLED PENDING (re-run repair_equity_data.py later):
  BK [CURRENT]: rate-limit: ...
  CMA [CURRENT]: rate-limit: ...
  PLTR [CURRENT]: empty (throttling-masked)
```

---

### Step 3 — `fetch_missing_delisted.py`

Recovers tickers that Step 2 couldn't retrieve — primarily **delisted or renamed**
S&P 500 constituents. This is the "clean up the survivors" pass.

**Two recovery sources, tried in order:**

#### (a) Curated rename map (`RENAME_MAP`)

Defined in `lean_project/data/delisted_aliases.py`. These are **hand-curated**
predecessor → successor pairs verified against Yahoo to be a pure ticker change
with no corporate action (split/dividend) on the transition date:

| Predecessor | Successor | What changed |
|-------------|-----------|-------------|
| `CTL` | `LUMN` | CenturyLink → Lumen |
| `ANTM` | `ELV` | Anthem → Elevance Health |
| `BLL` | `BALL` | Ball Corp old → new ticker |
| `PKI` | `RVTY` | PerkinElmer → Revvity |
| `WLTW` | `WTW` | Willis Towers Watson → WTW |
| `NLOK` | `GEN` | Symantec → Gen Digital |
| `ABC` | `COR` | AmerisourceBergen → Cencora |
| `GPS` | `GAP` | Gap old → new ticker |
| `FLT` | `CPAY` | FleetCor → Corpay |
| `PEAK` | `DOC` | Healthpeak → DOC |
| `HFC` | `DINO` | HollyFrontier → HF Sinclair |
| `FRC` | `FRCB` | First Republic → receivership ticker |
| `FI` | `FISV` | Fiserv ticker change |
| `RE` | `EG` | Easterly Government Properties |

The successor's continuous adjusted series is **sliced** across the predecessor's
membership window and stored under the predecessor key.

#### (b) Tiingo fallback (keyed API)

For tickers that aren't in the rename map and still have no data, the script
falls back to **Tiingo** (`TIINGO_API_KEY` in `config/.env`). This is important
because Tiingo has different historical coverage — it can serve data for tickers
that Yahoo no longer returns.

**Mandatory US-exchange guard:** Tiingo metadata is checked to ensure the
ticker is listed on a US exchange (NYSE, NASDAQ, BATS, etc.). Foreign-listing
collisions (e.g. `BK` resolving to a TSX company, `MMC`/`COG` to ASX) are
**rejected** as `tiingo_foreign_collision`.

> **Free-tier note:** Tiingo free tier = 500 symbols/month, 1000 requests/day.
> For a full S&P 500 recovery run, this may need to be split across multiple days
> or upgraded. Rate-limited Tiingo requests are **deferred** (not marked
> unavailable) so they can be retried.

#### Dry-run vs. apply

**Always dry-run first** to review the recovery plan:

```powershell
python scripts/fetch_missing_delisted.py
```

This prints a per-ticker breakdown:

```
Plan summary by source:
  rename: 13
  tiingo: 46
  unavailable: 8

Per-ticker:
  ABC     rename      ok              ABC->COR
  ANTM    rename      ok              ANTM->ELV
  BK      tiingo      rejected        tiingo_foreign_collision:TSX
  ...
```

Then apply:

```powershell
python scripts/fetch_missing_delisted.py --apply
```

This writes:
- **Updated `equity_bars.json`** — merged recovered bars (backed up to `.bak.json`)
- **`equity_unavailable.json`** — explicit, tracked record of unrecoverable tickers
  (foreign collisions, delisted names with no successor, genuine data gaps)

> `equity_unavailable.json` is the **explicit survivorship-gap record**. It
> transforms the implicit ~12% data failure into something documented and
> reviewable. The `embed_data.py` coverage gate and `track_exclusions.py`
> both read from this file.

---

### Step 4 — `track_exclusions.py`

Generates the consolidated exclusion report — the definitive list of every
ticker NOT in the backtest and why.

**What it does:**

1. Runs the **bar-quality gate** (`bar_quality.py`) on every ticker in
   `equity_bars.json` — catches malformed OHLC, all-zero prices, and extreme
   single-day moves (>60% move = likely unadjusted split or wrong instrument)
2. Computes the set of S&P 500 membership-window overlays still missing from
   bars
3. Cross-references with `equity_unavailable.json` to mark explained gaps
4. Reads `equity_bars.throttled.txt` to flag rate-limited tickers

**Output files** (in `lean_project/data/`):

| File | Description |
|------|-------------|
| `missing-data.txt` | Human-readable table of all exclusions with reasons |
| `missing-data.json` | Machine-readable categories |

**Exclusion categories:**

| Category | Meaning | Severity |
|----------|---------|----------|
| `broken` | Failed the quality gate — malformed/unadjusted data | **Action required** (manual review) |
| `missing_window` | In S&P 500 window but absent AND not documented as unavailable | **Action required** (gap! re-run pipeline) |
| `documented_unavailable` | Absent but explained in `equity_unavailable.json` | Expected |
| `throttled` | Yahoo rate-limited; re-run the download | Wait + retry |

> **If `missing_window` is non-empty after all three download/repair steps,
> you have an unexplained data gap — do NOT proceed to embedding.** Investigate
> those tickers manually.

---

### Step 5 — `embed_data.py`

Compresses all JSON data files into self-contained Python modules
(zlib + base64) so the Lean algorithm has zero file I/O at runtime.

**Pre-embedding validation (hard fail on these):**

- `equity_bars.json` must span `[DATA_START, BACKTEST_END]` — HARD FAIL
- Every **current** S&P 500 member (`end_date` is None in CSV) must have bars
  covering the full window — HARD FAIL (catches leftover throttled tickers)
- `fundamentals_history.json` coverage is WARN-only (separate concern)

**Generated modules** (in `lean_project/data/`):

| Source JSON | Embedded Module |
|-------------|----------------|
| `equity_bars.json` | `equity_bars.py` → `load_equity_bars()` |
| `damodaran_erp.json` | `damodaran_erp_json.py` → `load_damodaran_erp()` |
| `fundamentals_history.json` | `fundamentals_history.py` → `load_fundamentals_history()` |
| `damodaran_erp_history.json` | `damodaran_erp_history.py` → `load_damodaran_erp_history()` |
| `config/.env` values | `backtest_config.py` → `load_backtest_window()` |

Also updates `lean.json` `start-date` / `end-date` to match config.

---

## Recovery Flow for Common Scenarios

### Scenario A — "Throttled PENDING" tickers remain (Step 2)

This is the most common post-download state. Yahoo masks real data as "empty"
during rate-limiting, particularly for popular tickers (COIN, PLTR, BK, CMA
were throttled in the last run).

**Solution:** Wait 4–24 hours for Yahoo's rate limit to reset, then re-run:

```powershell
cd lean_project
python scripts/repair_equity_data.py
# Check: does the PENDING list shrink / disappear?
```

Re-run until `throttled PENDING` is empty, then proceed to Steps 3–5.

### Scenario B — Tickers in `equity_unavailable.json` but membership is still active

If a ticker appears in `equity_unavailable.json` (written by Step 3) but
should still be in the S&P 500 (its `end_date` is None or extends past
`BACKTEST_START`), it's a **real data gap** — not expected delisting.

**Investigate:**
1. Check the `reason` field in `equity_unavailable.json`
2. If `tiingo_foreign_collision` — the ticker resolves to a foreign listing
   on Tiingo but the real company is US-traded. Try downloading it manually:
   ```powershell
   python scripts/download_equity_data.py --tickers THE_TICKER
   ```
3. If `unrecoverable` — try `yfinance` directly with symbol variants:
   ```python
   import yfinance as yf
   yf.Ticker("THE_TICKER").history(start="...", auto_adjust=True)
   ```

### Scenario C — "broken" tickers (Step 4 quality gate)

The `missing-data.json` `broken` category flags tickers whose downloaded bars
fail the quality gate. In the current run, two examples:

| Ticker | Issue | Likely cause |
|--------|-------|-------------|
| `DEC` | `malformed_rows 691/1359` | ~51% of rows fail OHLC containment — likely wrong instrument or unadjusted data |
| `TNB` | `extreme_moves 305/784` | ~40% of days show >60% move — likely unadjusted split or mis-resolved symbol |

**Solution:** Remove these tickers from `equity_bars.json` manually and add
them to the exclusion report. They are **not** silently dropped — the
quality gate exists precisely to prevent corrupt data from poisoning the
backtest. If the data looks fixable at the source, re-download just that
ticker (see Scenario B).

### Scenario D — Full re-download after S&P 500 membership refresh

If the S&P 500 membership CSV is refreshed (new companies added, old ones
removed):

```powershell
cd lean_project
python scripts/download_equity_data.py --refresh-sp500  # downloads new full list
python scripts/repair_equity_data.py                    # retry throttles
python scripts/fetch_missing_delisted.py               # recover delisted
python scripts/fetch_missing_delisted.py --apply       # commit
python scripts/track_exclusions.py                     # verify coverage
python scripts/embed_data.py                           # embed
```

---

## Configuration

All backtest dates are controlled via `config/.env`:

```env
# Backtest window — single source of truth
BACKTEST_START="2020-01-01"
BACKTEST_END="2026-08-01"

# Warm-up days before BACKTEST_START (default: 252 trading days)
BACKTEST_WARMUP_DAYS="252"

# Optional floor for history fetch (default: earliest CSV membership start)
# BACKTEST_HISTORY_START="1996-01-02"

# SEC identity for edgartools
SEC_USER="Name email@domain.com"

# Tiingo API key for fallback data recovery
TIINGO_API_KEY="your_key_here"
```

Derived values (computed in `config/config.py`):

| Variable | Formula |
|----------|---------|
| `DATA_START` | `BACKTEST_START` − 252 trading days |
| `HISTORY_START` | `BACKTEST_HISTORY_START` env, else earliest CSV `start_date`, else `1996-01-02` |

---

## File Reference

### Data files (`lean_project/data/`)

| File | Generated by | Description |
|------|-------------|-------------|
| `sp500_ticker_start_end.csv` | (external download) | S&P 500 membership with start/end dates |
| `equity_bars.json` | `download_equity_data.py` | Source daily OHLCV bars |
| `equity_bars.py` | `embed_data.py` | Embedded bars (zlib+base64) |
| `fundamentals.json` | `download_equity_data.py` | Latest fundamentals snapshot |
| `fundamentals_history.json` | `download_edgartools_data.py` / yfinance | Quarterly PIT history |
| `fundamentals_history.py` | `embed_data.py` | Embedded quarterlies |
| `damodaran_erp.json` | `implied_erp/` pipeline | Country-level ERP |
| `damodaran_erp_history.json` | `implied_erp/` pipeline | PIT ERP history |
| `equity_unavailable.json` | `fetch_missing_delisted.py` | Tracked survivorship gaps |
| `equity_bars.throttled.txt` | `repair_equity_data.py` | Throttled ticker list |
| `missing-data.txt` | `track_exclusions.py` | Human exclusion report |
| `missing-data.json` | `track_exclusions.py` | Machine exclusion report |
| `*.bak.json` | download/repair/fetch scripts | Backup before overwrite |

### Scripts (`lean_project/scripts/`)

| Script | Purpose |
|--------|---------|
| `download_equity_data.py` | Step 1 — initial yfinance download |
| `repair_equity_data.py` | Step 2 — retry throttled/masked tickers |
| `fetch_missing_delisted.py` | Step 3 — recover via rename map + Tiingo |
| `track_exclusions.py` | Step 4 — generate coverage/exclusion report |
| `embed_data.py` | Step 5 — compress to Python modules |
| `convert_to_qc_format.py` | Convert JSON bars to QC zip format (for Lean CSV.zip bootstrap) |
| `download_edgartools_data.py` | Download quarterly PIT fundamentals from SEC EDGAR |

### Support modules (`lean_project/data/`)

| Module | Purpose |
|--------|---------|
| `sp500_data.py` | Membership loading, interval clipping, alias map builder |
| `delisted_aliases.py` | Curated RENAME_MAP + corporate-action screen |
| `bar_quality.py` | OHLC containment + extreme-move quality gate |
| `exclusions.py` | Exclusion categorization + report rendering |
| `corporate_actions.py` | Spinoff dates (excluded from quality gate) |
| `equity_bars.py` | Embedded bars (auto-generated — do not edit) |
