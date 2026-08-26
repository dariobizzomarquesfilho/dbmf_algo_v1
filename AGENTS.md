# AGENTS.md

## Setup

- Python 3.11+, Windows PowerShell, Docker required for Lean.
- Venv at `.venv` — activate with `.\.venv\Scripts\Activate.ps1`.
- Install: `pip install -r config/requirements.txt` (pins: `edgartools==5.52.0` provides `edgar`, `lean==1.0.228` for `lean backtest`, `yfinance`, `openpyxl`, `xlrd`, `python-dotenv`, `requests`).
- `config/.env` is required (not repo root). Must contain `SEC_USER="Name email@domain"` for `edgar.set_identity()` — `config/config.py` raises if missing. Optional: `BACKTEST_START`, `BACKTEST_END`, `BACKTEST_WARMUP_DAYS` (default 252), `BACKTEST_HISTORY_START`, `TIINGO_API_KEY`.
- Docker required for `lean backtest` (`quantconnect/lean:foundation` image); `lean` CLI is now pinned in `requirements.txt` so a single `pip install` restores it.

## Single Source of Truth: Backtest Window

`config/.env` → `config/config.py` → `lean_project/data/backtest_config.py` → `lean_project/lean.json`

- Never edit `lean.json` dates or `backtest_config.py` by hand — they are overwritten by `lean_project/scripts/embed_data.py`.
- After any `.env` date change, re-run `python lean_project/scripts/embed_data.py` from repo root (or `cd lean_project; python scripts/embed_data.py`).

## Embedded Data (Do Not Edit Generated `.py`)

All runtime data is `zlib+base64` embedded modules, not JSON/CSV at runtime:

- Source JSON: `lean_project/data/*.json` + `implied_erp/data/erp/*.json` + `damodaran_erp_history.json`
- Generated: `lean_project/data/equity_bars.py`, `fundamentals_history.py`, `damodaran_erp_history.py`, `damodaran_erp_history_us.py`, `backtest_config.py` (gitignored — do not edit, regenerate)
- Build: `python lean_project/scripts/embed_data.py` — validates coverage and hard-fails if `equity_bars.json` doesn't span `[DATA_START, BACKTEST_END]` or if `damodaran_erp_history.json` missing. Full re-embed after any JSON change.
- At startup `data/bootstrap_data.py` writes `data/equity/*.zip` + `map_files/*.csv` into Lean's data folder so the time loop advances.

## Pipeline Order (Dependency-sensitive)

Full pipeline: `.\run_pipeline.ps1` (logs to `logs/step*.log` + `run_pipeline.log`). Manual order:

```powershell
# 1. ERP PIT series (required before embed — no static fallback exists)
python implied_erp/scripts/download_damodaran_erp.py
python implied_erp/scripts/extract_all_damodaran_erp.py
python implied_erp/scripts/build_lean_erp_history.py
# optional annual fallback: python implied_erp/scripts/scrape_histimpl.py

# 2. Fundamentals + bars (run from lean_project/)
cd lean_project
python scripts/download_edgartools_data.py   # --tickers AAPL MSFT for single-ticker test
python scripts/download_equity_data.py       # yfinance; pulls HISTORY_START..BACKTEST_END
python scripts/repair_equity_data.py
python scripts/fetch_missing_delisted.py --apply
python scripts/track_exclusions.py           # soft report, non-fatal
python scripts/convert_to_qc_format.py
python scripts/embed_data.py                 # must be last; updates lean.json
```

- `download_edgartools_data.py` uses `config.DATA_START` (warm-up aware, derived via NYSE calendar) — not `BACKTEST_START` directly.
- Equity bars need `>= BACKTEST_WARMUP_DAYS` (252) trading days before `BACKTEST_START` (NYSE holidays via `data/nys_calendar.py`).

## Tests & Verification

```powershell
python -m pytest lean_project/tests implied_erp/tests          # all
python -m pytest lean_project/tests/test_pit_data.py -v        # single file
python -m pytest lean_project/tests/test_pit_data.py::test_fundamental_as_of_picks_latest_quarter_at_or_before_date -v  # single test
```

- No linter/formatter configured (`flake8`/`black`/`ruff` absent). Only pytest.
- `lean_project/tests/conftest.py` injects `lean_project` + repo root + `implied_erp` onto `sys.path`.
- `run_pipeline.ps1` runs tests as soft (warn, continue); backtest is hard-fail.

## Running the Backtest

```powershell
cd lean_project
lean backtest            # or: lean backtest --config lean.json
```

- Must `cd lean_project` first — `lean.json` paths are relative.
- `main.py:PbRoeAtrAlgorithm` is the only algorithm. `Initialize()` loads embedded modules, bootstraps CSVs, pre-subscribes members active at start date. `OnData()` → `_ensure_subscribed()` (adds tickers on their `intervals_active` add date) → `DailyRebalance()`. Lean `CoarseSelection`/`FineSelection` hooks are not used.
- Docker image `quantconnect/lean:foundation` required.

## Architecture Notes

- `lean_project/` is primary deliverable; `implied_erp/` is extraction pipeline + PIT builder; `config/` owns env/window.
- `universe/pb_roe_universe.py:run_fine_selection()` + `universe/pit_data.py` (PIT fundamentals/Beta/ERP/risk-free) — Lean backtest's screen uses only `resolve_erp_as_of()` (PIT history, then histimpl fallback). Empty universe if neither yields data — never invent ERP.
- `indicators/atr_trailing_stop.py` + `valuation/gordon_growth.py` (2-stage Gordon growth → intrinsic P/B, CAPM with `^TNX`).
- `data/sp500_data.py:intervals_active` + `sp500_ticker_start_end.csv` = PIT S&P 500 membership; bars clipped to intervals.
- Prices: must call `Security.SetMarketPrice()` from `equity_bars` dict; `Security.Price` is 0 at scheduled times otherwise. ATR/beta use embedded bars dict, not `algorithm.History()`.

## PIT / No Look-Ahead Rules

- Fundamentals: `fundamentals_history.json` = TTM per quarter from SEC 10-Q via `edgar` (edgartools). Access via `fundamental_as_of(ticker, as_of)` — latest quarter at-or-before date. If no quarter exists, skip ticker (do not fall back to static snapshot or future quarter).
- ERP: `resolve_erp_as_of(erp_history_cache, histimpl_cache, as_of)` — latest at-or-before date, spreadsheet PIT preferred, annual histimpl fallback. No current-date static file.
- Risk-free rate: `resolve_risk_free_rate(as_of)` from `^TNX` embedded bars, PIT only.
- S&P 500: `intervals_active` guard — never subscribe a ticker before its index-add date.

## Gotchas

- `.env` is gitignored; `SEC_USER` format is `"Full Name email@domain"` — SEC rejects without it.
- `lean_project/data/alternative/interest-rate/usa/interest-rate.csv` is cosmetic (Lean parse-fixed to `YYYY-MM-DD`); strategy uses `^TNX` bars for risk-free rate.
- `yfinance` returns negative `bookValue` for ~33 tickers (SBUX, MCD, etc.) — screen skips invalid `book_value` per-date, doesn't purge cache. P/B is always `current_price / book_value`.
- Known bar gaps: EA / FOX / FOXA / IR flagged at embed time until `download_equity_data.py` + `repair` fixes them.
- `lean_project/Lean/` is cloned Lean engine source (gitignored) — not project code.
- For extended context see `CLAUDE.md` and `README.md` (lean_project/README.md for backtest specifics, implied_erp/README.md for extraction).
