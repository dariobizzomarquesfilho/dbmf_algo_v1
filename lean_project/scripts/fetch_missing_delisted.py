"""Recover the S&P 500 membership window gaps that the batch Yahoo download
dropped (the "failed data requests" set).

Two recovery sources, tried in order for each missing window member:

1. **Curated rename** (``data.delisted_aliases.RENAME_MAP``) — a predecessor whose
   same unbroken listing continues under a new ticker (e.g. ``CTL``->``LUMN``). The
   successor's continuous adjusted series is sliced across the rename and stored
   under the predecessor key. Review-only; no auto-derivation.
2. **Tiingo fallback** (keyed, ``TIINGO_API_KEY``) — for the names Yahoo hard-empty
   for. Mandatory US-exchange guard rejects foreign-listing collisions (e.g.
   ``BK``->TSX, ``MMC``/``COG``->ASX). Rejects tickers whose price rows look invalid.

Whatever neither source recovers is written to ``equity_unavailable.json`` — the
explicit, tracked survivorship-gap record. ``embed_data`` then turns a silent
12%-failure into an explicit, reviewed, stable one.

Idempotent + resumable: tickers already present in ``equity_bars.json`` are never
re-fetched; re-running only fills new gaps after a CSV refresh.

Usage:
    python -m scripts.fetch_missing_delisted            # dry-run: print the plan
    python -m scripts.fetch_missing_delisted --apply     # write recovered bars + unavailable
"""

from __future__ import annotations

import argparse
import csv
import json
import shutil
import ssl
import sys
import time
import urllib.request
from datetime import date, timedelta
from pathlib import Path
from typing import Optional, Iterator

# Add repo root so `import config` works (config/ is at repo root).
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import config  # loads .env (TIINGO_API_KEY, BACKTEST_START/END)

# Add lean_project so `from data...` works.
_LEAN_PROJECT = Path(__file__).resolve().parent.parent
if str(_LEAN_PROJECT) not in sys.path:
    sys.path.insert(0, str(_LEAN_PROJECT))
from data.sp500_data import load_sp500_membership, clip_to_membership

# Add this scripts dir to path so `from common import ...` works both when run
# directly (scripts/ on sys.path[0]) and when imported as scripts.fetch_*.
_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
from common import load_fundamentals_tickers
from data.delisted_aliases import (
    RENAME_MAP,
    EXCLUDED_COMPLEX,
    is_deliberate_rename,
    screen_successor,
    membership_window,
)

_DATA_DIR = _LEAN_PROJECT / "data"
_BARS_PATH = _DATA_DIR / "equity_bars.json"
_MEMBERSHIP_CSV = _DATA_DIR / "sp500_ticker_start_end.csv"
_UNAVAILABLE_PATH = _DATA_DIR / "equity_unavailable.json"

# Tiingo exchangeCode values that denote a genuine US listing. Anything else
# (TSX, ASX, LSE, ...) is a foreign-listing collision and is rejected — this is
# the reliable guard (verified: BK->"Canadian Banc Corp"/TSX, MMC/COG->ASX).
US_EXCHANGES = {
    "NYSE", "NASDAQ", "BATS", "NYSE ARCA", "NYSE AMERICAN", "NYSE MKT",
    "NASDAQ CAPITAL MARKET", "NASDAQ GLOBAL MARKET", "NASDAQ GLOBAL SELECT",
    "BATS EXCHANGE", "IEX", "CBOE", "CBOE BZX", "NYSE CHICAGO", "CBOE BZX EXCHANGE",
}

_TIINGO_BASE = "https://api.tiingo.com"


class TiingoRateLimited(Exception):
    """Raised when Tiingo returns 429/403/503 even after retries.

    Distinct from a genuine data miss (404 / empty): a rate-limited or
    quota-exhausted ticker is *recoverable later* and must be DEFERRED, never
    written to ``equity_unavailable.json`` (which is reserved for genuinely
    unrecoverable names like foreign-listing collisions).
    """


def _iter_tiingo_keys(keys: list) -> Iterator[str]:
    """Infinite round-robin iterator over the configured Tiingo keys.

    Yields keys in order, cycling forever, so callers can rotate through the
    pool on rate limits. Returns immediately (yields nothing) for an empty list.
    """
    if not keys:
        return
    i = 0
    while True:
        yield keys[i % len(keys)]
        i += 1


# ---------------------------------------------------------------------------
# TLS context (parity with how urllib is already used elsewhere)
# ---------------------------------------------------------------------------
def _ssl_context() -> ssl.SSLContext:
    try:
        import certifi

        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        print(
            "WARN: TLS verification disabled (certifi unavailable) — "
            "falling back to unverified context.",
            file=sys.stderr,
        )
        return ctx


# ---------------------------------------------------------------------------
# Missing-window computation
# ---------------------------------------------------------------------------
def compute_missing(
    bars: dict,
    membership: dict,
    win_start: str,
    win_end: str,
    fundamentals: Optional[set] = None,
) -> list[str]:
    """Window S&P members (overlap [win_start, win_end]) with no bars.

    When ``fundamentals`` is provided, only tickers present in that set are
    considered missing (the tradeable universe). ``^TNX``/``^GSPC`` are always
    excluded (they are handled separately).
    """
    missing = []
    for t, ivs in membership.items():
        if t in ("^TNX", "^GSPC"):
            continue
        if fundamentals is not None and t not in fundamentals:
            continue
        if not any(s <= win_end and (e is None or e >= win_start) for s, e in ivs):
            continue
        if t not in bars:
            missing.append(t)
    return missing


# ---------------------------------------------------------------------------
# Curated rename recovery
# ---------------------------------------------------------------------------
def _row_to_bar(row) -> dict:
    return {
        "open": float(row.get("Open", 0)),
        "high": float(row.get("High", 0)),
        "low": float(row.get("Low", 0)),
        "close": float(row.get("Close", 0)),
        "volume": float(row.get("Volume", 0)),
    }


def _yahoo_history(ticker: str, start: str, end: str) -> Optional[dict]:
    """Best-effort Yahoo adjusted history; None on any failure."""
    try:
        import yfinance as yf
    except ImportError:
        return None
    try:
        hist = yf.Ticker(ticker).history(
            start=start, end=end, auto_adjust=True, actions=False, raise_errors=False
        )
        if hist is None or hist.empty:
            return None
        return {
            d.strftime("%Y-%m-%d"): _row_to_bar(row) for d, row in hist.iterrows()
        }
    except Exception as e:  # noqa: BLE001
        print(f"  Yahoo history for {ticker} failed: {e}", file=sys.stderr)
        return None


def _yahoo_end_exclusive(end: str) -> str:
    """yfinance `end` is exclusive; return end + 1 day so the fetched window
    actually covers `end` (e.g. 2026-07-31 -> last bar 2026-07-31)."""
    return (date.fromisoformat(end) + timedelta(days=1)).isoformat()


def _rename_bars(
    pred: str,
    succ: str,
    bars_cache: dict,
    membership: dict,
    default_end: str,
) -> tuple[Optional[dict], Optional[str]]:
    """Recover ``pred`` bars by slicing the ``succ`` continuous series.

    Returns (bars_or_None, reason_or_None). The deliberate-rename wins over the
    corporate-action screen; only a COMPLEX *non-deliberate* candidate is dropped.
    """
    succ_bars = bars_cache.get(succ)
    if succ_bars is None:
        start, end = membership_window(membership[pred], default_end)
        succ_bars = _yahoo_history(succ, start, _yahoo_end_exclusive(end))
    if not succ_bars:
        return None, f"rename_failed:successor_{succ}_no_bars"

    _, exit_date = membership_window(membership[pred], default_end)
    if screen_successor(succ_bars, exit_date) == "COMPLEX" and not is_deliberate_rename(pred):
        return None, "rename_dropped:complex_corporate_action"

    clipped = clip_to_membership({pred: succ_bars}, membership, default_end).get(pred)
    if not clipped:
        return None, "rename_failed:no_membership_overlap"
    return clipped, None


# ---------------------------------------------------------------------------
# Tiingo fallback
# ---------------------------------------------------------------------------
def _tiingo_request(path: str, token: str, ctx: ssl.SSLContext, retries: int = 5):
    url = f"{_TIINGO_BASE}{path}" + (f"&token={token}" if "?" in path else f"?token={token}")
    req = urllib.request.Request(url, headers={"Content-Type": "application/json"})
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            # 429/403/503 = rate limit / transient — back off and retry.
            if e.code in (429, 403, 503) and attempt < retries - 1:
                wait = 1.0 * (2 ** attempt)
                print(f"  Tiingo HTTP {e.code} for {path}; backing off {wait:.1f}s",
                      file=sys.stderr)
                time.sleep(wait)
                continue
            if e.code in (429, 403, 503):
                # Quota/rate limit still hit after retries -> defer, don't fail.
                raise TiingoRateLimited(f"Tiingo HTTP {e.code} for {path}")
            print(f"  Tiingo HTTP {e.code} for {path}", file=sys.stderr)
            return None
        except Exception as e:  # noqa: BLE001
            if attempt < retries - 1:
                time.sleep(1.0 * (2 ** attempt))
                continue
            print(f"  Tiingo request failed for {path}: {e}", file=sys.stderr)
            return None
    return None


def _tiingo_to_bars(rows: list) -> Optional[dict]:
    bars = {}
    for r in rows:
        d = (r.get("date") or "")[:10]
        if not d:
            continue
        ac = r.get("adjClose")
        if ac is None:
            continue
        bars[d] = {
            "open": float(r.get("adjOpen", 0) or 0),
            "high": float(r.get("adjHigh", 0) or 0),
            "low": float(r.get("adjLow", 0) or 0),
            "close": float(ac),
            "volume": float(r.get("adjVolume", 0) or 0),
        }
    return bars or None


def _tiingo_fetch(
    ticker: str, start: str, end: str, keys: list, ctx: ssl.SSLContext, key_iter
) -> tuple[Optional[dict], Optional[str]]:
    """Fetch Tiingo daily prices with mandatory US-listing guard, rotating keys.

    Tries each configured key in round-robin order on 429/403/503. If every key
    is rate-limited, raises ``TiingoRateLimited`` (the caller defers the ticker
    rather than marking it unavailable). A genuine 404 / empty result for a key
    is returned immediately (no point retrying other keys for the same ticker).
    """
    if not keys:
        return None, "no_keys"
    last_rate = False
    for _ in range(len(keys)):
        token = next(key_iter)
        try:
            rows = _tiingo_request(
                f"/tiingo/daily/{ticker}/prices?startDate={start}&endDate={end}", token, ctx
            )
        except TiingoRateLimited:
            last_rate = True
            continue
        if not rows:
            return None, "tiingo_no_prices"
        bars = _tiingo_to_bars(rows)
        if not bars:
            return None, "tiingo_no_adjclose"

        try:
            meta = _tiingo_request(f"/tiingo/daily/{ticker}", token, ctx)
        except TiingoRateLimited:
            last_rate = True
            continue
        if meta is not None:
            ex = meta.get("exchangeCode")
            if ex is not None and ex not in US_EXCHANGES:
                return None, f"tiingo_foreign_collision:{ex}"
        return bars, None
    # Every key was rate-limited / quota-exhausted.
    raise TiingoRateLimited(f"All {len(keys)} Tiingo key(s) rate-limited for {ticker}")


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def recover_gaps(
    bars: dict,
    membership: dict,
    win_start: str,
    win_end: str,
    keys: list,
    ctx: ssl.SSLContext,
    pacing: float = 0.7,
) -> tuple[dict, list, list]:
    """Return (new_bars, unavailable_records, plan_rows).

    ``new_bars`` are NOT yet merged into ``bars``. ``plan_rows`` is a printable
    per-ticker plan: {ticker, source, verdict, reason}.

    ``keys`` is the list of Tiingo API keys (round-robin rotated on rate limits);
    an empty list disables the Tiingo fallback (degrades to curated-rename +
    unavailable, matching the legacy no-key path).
    """
    missing = compute_missing(
        bars, membership, win_start, win_end, fundamentals=load_fundamentals_tickers()
    )
    new_bars: dict = {}
    unavailable: list = []
    plan: list = []

    total = len(missing)
    print(f"Missing window members: {total}")

    key_iter = _iter_tiingo_keys(keys)

    for i, t in enumerate(sorted(missing), 1):
        if i % 25 == 0:
            print(f"  Processed {i}/{total}...")
        if pacing:
            time.sleep(pacing)
        start, end = membership_window(membership[t], win_end)

        # (a) Curated rename
        if t in RENAME_MAP:
            succ = RENAME_MAP[t]
            rb, reason = _rename_bars(t, succ, bars, membership, win_end)
            if rb is not None:
                new_bars[t] = rb
                plan.append({"ticker": t, "source": "rename", "verdict": "ok",
                             "reason": f"{t}->{succ}"})
                continue
            plan.append({"ticker": t, "source": "rename", "verdict": "fallthrough",
                         "reason": reason})

        # (b) Tiingo fallback (rotates keys on 429/403/503)
        if keys:
            try:
                tb, reason = _tiingo_fetch(t, start, end, keys, ctx, key_iter)
            except TiingoRateLimited:
                # Quota/rate-limited on every key: DEFER (recoverable later) —
                # never mark unavailable.
                plan.append({"ticker": t, "source": "tiingo", "verdict": "deferred",
                             "reason": "rate_limited_quota"})
                continue
            if tb is not None:
                new_bars[t] = tb
                plan.append({"ticker": t, "source": "tiingo", "verdict": "ok",
                             "reason": f"{start}..{end}"})
                continue
            plan.append({"ticker": t, "source": "tiingo", "verdict": "rejected",
                         "reason": reason})
        else:
            plan.append({"ticker": t, "source": "tiingo", "verdict": "skipped",
                         "reason": "no TIINGO_API_KEY"})

        # (c) Unavailable (genuinely unrecoverable only: foreign collision, 404, no data)
        rec_source = "tiingo" if keys else "none"
        if t in EXCLUDED_COMPLEX:
            reason = "excluded_complex"
        elif keys:
            reason = reason or "unrecoverable"  # carried from the rejected Tiingo call
        else:
            reason = "unrecoverable"
        unavailable.append({
            "ticker": t, "start": start, "end": end,
            "source": rec_source, "reason": reason,
        })
        plan.append({"ticker": t, "source": "unavailable", "verdict": "unavailable",
                     "reason": reason})

    return new_bars, unavailable, plan


def _print_plan(plan: list) -> None:
    by_src: dict[str, int] = {}
    for p in plan:
        by_src[p["source"]] = by_src.get(p["source"], 0) + 1
    print("\nPlan summary by source:")
    for src, n in sorted(by_src.items()):
        print(f"  {src}: {n}")
    print("\nPer-ticker:")
    for p in plan:
        print(f"  {p['ticker']:6s} {p['source']:10s} {p['verdict']:12s} {p['reason']}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Recover missing S&P 500 window bars.")
    ap.add_argument("--apply", action="store_true",
                    help="Write recovered bars + equity_unavailable.json (default: dry-run)")
    ap.add_argument("--bars-path", default=str(_BARS_PATH))
    ap.add_argument("--membership-csv", default=str(_MEMBERSHIP_CSV))
    ap.add_argument("--unavailable-path", default=str(_UNAVAILABLE_PATH))
    ap.add_argument("--pacing", type=float, default=0.7,
                    help="Seconds to sleep between network calls (avoid rate limits).")
    args = ap.parse_args()

    bars = json.load(open(args.bars_path, encoding="utf-8")) if Path(args.bars_path).exists() else {}
    membership = load_sp500_membership(args.membership_csv)

    ctx = _ssl_context()

    win_start, win_end = config.BACKTEST_START, config.BACKTEST_END

    # Round-robin pool of Tiingo keys (empty list => Tiingo fallback disabled,
    # degrading to curated-rename + unavailable, same as the legacy no-key path).
    keys = config.get_tiingo_keys()

    new_bars, unavailable, plan = recover_gaps(
        bars, membership, win_start, win_end, keys, ctx, pacing=args.pacing
    )

    _print_plan(plan)

    if not args.apply:
        print(
            "\nDRY-RUN: no files written. Re-run with --apply to persist. "
            f"Would recover {len(new_bars)} tickers, mark {len(unavailable)} unavailable."
        )
        return

    # Merge + clip each recovered set to its membership window, then persist.
    merged = dict(bars)
    for t, tb in new_bars.items():
        merged[t] = clip_to_membership({t: tb}, membership, win_end).get(t, tb)

    if Path(args.bars_path).exists():
        bak = Path(args.bars_path).with_name(Path(args.bars_path).stem + ".bak.json")
        shutil.copy2(args.bars_path, bak)
        print(f"Backed up existing bars -> {bak.name}")

    with open(args.bars_path, "w", encoding="utf-8") as f:
        json.dump(merged, f, indent=2)
    print(f"Wrote {args.bars_path} ({len(merged)} tickers)")

    with open(args.unavailable_path, "w", encoding="utf-8") as f:
        json.dump(unavailable, f, indent=2)
    print(f"Wrote {args.unavailable_path} ({len(unavailable)} unavailable)")


if __name__ == "__main__":
    main()
