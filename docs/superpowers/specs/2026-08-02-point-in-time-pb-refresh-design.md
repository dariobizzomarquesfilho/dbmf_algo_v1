# Point-in-Time Per-Company P/B Refresher — Design

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Each company's P/B — and the other screen inputs (book_value, roe, eps, beta) — reflect its own fundamentals as of the backtest date, refreshed per company (not one static snapshot for all 3 years), with fundamentals read only at buy decisions to minimize computation.

**Architecture:** Pre-computed embedded quarterly time series for the static fundamentals (book_value, roe, eps), plus an on-demand rolling beta computed from embedded daily bars at buy time. Screen looks up point-in-time values as of the backtest date. Zero network at runtime, no look-ahead.

**Context / Why:** Today `fundamentals.json` holds one static snapshot per ticker captured at download time. Over a 2023–2025 backtest this is wrong: a company's book value / ROE / EPS move every quarter, so a single snapshot produces incorrect P/B for most of the period. The user wants per-company, per-quarter refresh, restricted to buy scans (initial buy + after a stop triggers a re-buy) to save computation and yfinance limits.

**Practically:** This is a backtest inside Lean Docker with no network at runtime. All data is embedded at build time. "Per-company refresh" therefore becomes an embedded time series that the screen queries by quarter; the data itself is refreshed by re-running the download pipeline when new fundamentals are published.

---

## Design decisions (confirmed with user)

1. **Pre-computed embedded time series** (not runtime yfinance calls — those cause look-ahead bias in backtests).
2. **Full quarterly fundamentals** per ticker: `book_value`, `roe`, `eps` plus a **computed rolling `beta`**.
3. **`sector` / `industry` / `name` stay static** (they don't drift quarterly).
4. **Beta = trailing 252 trading days (1 year)** of daily returns vs `^GSPC`, **recomputed at each buy decision** (rolling, lazy). Alpha returned too (free from the same regression).
5. **Fundamentals read only when scanning for a buy**: at start, and after a stop triggers a re-entry.

---

## Data model

### New source file: `data/fundamentals_history.json`
Embedded counterpart: `data/fundamentals_history.py` (zlib+base64, via `embed_json`).

```json
{
  "AAPL": {
    "2023-03-31": {"book_value": 3.92, "roe": 1.50, "eps": 6.10},
    "2023-06-30": {"book_value": 4.01, "roe": 1.61, "eps": 6.32},
    "...": "every quarter-end 2023-03-31 .. 2025-12-31"
  },
  "MSFT": { "...": "own quarter-end cadence" }
}
```

Each ticker carries its own quarter-end dates (from yfinance `quarterly_balance_sheet` + `quarterly_financials`). A company with a Sept fiscal period updates in Sept; one with a Dec period updates in Dec — genuine per-company cadence.

### `^GSPC` market reference
`download_equity_data.py` must also download and embed `^GSPC` daily bars (currently only `^TNX` is fetched as an index). Used solely for beta regression runtime.

### Static snapshot (`fundamentals.json`) — unchanged
Keeps `sector`, `industry`, `name`, `book_value`, `price`, `market_cap`, `dollar_volume`, etc. Price/static book_value remain available but the screen will prefer the point-in-time series.

---

## Point-in-time lookup

`universe/pb_roe_universe.py` gains a helper:

```python
def fundamental_as_of(hist: dict, ticker: str, date_str: str) -> Optional[dict]:
    """Latest quarter snapshot with quarter_end <= date."""

    qs = [
        q for q in hist.get(ticker, {})
        if q <= date_str  # ISO YYYY-MM-DD strings compare correctly
    ]
    return hist[ticker][max(qs)] if qs else None
```

- If no quarter has landed yet for a ticker, it is skipped for that screen (no stale fallback). It is picked up automatically once its quarter lands — per your requirement.
- P/B stays dynamic: `pb = current_price / snapshot["book_value"]` where `current_price` is the latest embedded bar close as of the screen date.

---

## Rolling beta (computed at buy time)

yfinance exposes only a single current `info["beta"]` — there is no historical beta series. We compute it from the embedded daily bars, only when a buy scan runs.

```python
import numpy as np

def rolling_beta(stock_bars: dict, market_bars: dict, as_of: str,
                 window: int = 252) -> Optional[tuple]:
    """Return (beta, alpha) from trailing `window` daily returns up to as_of.

    Align stock and market on common trading dates <= as_of, take the last
    `window` aligned days, regress stock returns on market returns.
    Returns None if fewer than ~30 aligned points available.
    """
    dates = sorted(d for d in stock_bars if d in market_bars and d <= as_of)
    if len(dates) < 30:
        return None
    dates = dates[-(window + 1):]
    s = np.array([stock_bars[d]["close"] for d in dates], dtype=float)
    m = np.array([market_bars[d]["close"] for d in dates], dtype=float)
    sr = np.diff(s) / s[:-1]
    mr = np.diff(m) / m[:-1]
    beta, alpha = np.polyfit(mr, sr, 1)   # beta = slope, alpha = intercept
    return float(beta), float(alpha)
```

- **252 trading days** (~1 year) window, rolling: recomputed per buy date from the trailing year — point-in-time, no look-ahead.
- Runs **only when scanning for a buy** (start + post-stop).
- Market proxy `^GSPC`.
- `<30` aligned points → treat as insufficient data, skip ticker.

---

## Runtime trigger (buy-scan gating)

`main.py` currently calls `run_fine_selection()` inside `DailyRebalance()` every day. Fundamentals are only *needed* when a buy can happen:

1. **Initial scan** — first daily rebalance (no positions).
2. **Post-stop** — a position is liquidated by the ATR stop, the list has room, so re-screen to refill.

So `DailyRebalance` already routes through buy logic; we ensure the fundamental lookup + beta computation happens only on those buy paths (not on pure stop-check days). Practically, `_check_stops()` never touches fundamentals (only prices/ATR), and `run_fine_selection` is gated to days where the selected set differs or room exists to add positions. This matches "only compute when buying, less computation".

**Explicit gating rule** (this is the crux): the expensive work — per-ticker `fundamental_as_of()` lookup + `rolling_beta()` regression over the whole universe — runs only when there is an actual buy opportunity:
- the portfolio has fewer positions than `max_positions` (initial fill, or after a stop freed a slot), **or**
- the current held set would otherwise churn (a candidate improves the score).

On a day where every slot is held and no stop fired, skip the fundamental/beta pass entirely and only check stops. This bounds the regression work to low-frequency events, not every trading day.

---

## Files to change

- **Modify** `lean_project/scripts/download_equity_data.py`
  - add `^GSPC` to download list
  - build `fundamentals_history.json` (per-ticker quarterly book_value/roe/eps from yfinance quarterly statements)
  - keep `equity_bars.json` (now includes `^GSPC` bars)
- **New** `lean_project/data/fundamentals_history.json` + generated `lean_project/data/fundamentals_history.py`
- **Modify** `lean_project/scripts/embed_data.py` — embed the new JSON via `embed_json`
- **Modify** `lean_project/universe/pb_roe_universe.py`
  - add `fundamental_as_of()` helper
  - add `rolling_beta()` helper (numpy)
  - `run_fine_selection()` reads point-in-time book_value/roe/eps + computed beta
- **Modify** `lean_project/main.py`
  - load `fundamentals_history` + `^GSPC` bars
  - gate fundamental reads / beta computation to buy scans (initial + post-stop)
  - pass history/bars through to `run_fine_selection`
- **Modify** `lean_project/README.md` — document the refresh model and quarterly re-download cadence

## Verification

1. Run full pipeline: `download_equity_data.py` → `embed_data.py` → `lean backtest`.
2. Confirm `^GSPC` present in `equity_bars.json` (`751` bars).
3. Confirm `fundamentals_history.json` has per-quarter entries for most tickers.
4. Sanity-check computed beta vs `info["beta"]` for a few tickers (same ballpark).
5. Backtest logs: AAPL's P/B changes after its Sept report; uses June value for Jul–Aug (point-in-time). Confirm ~505 data requests succeed.
6. Confirm ATR stops unchanged and no look-ahead (stop uses `as_of_date` filtered bars).
