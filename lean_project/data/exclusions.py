"""Aggregate every ticker that will NOT reach the backtest, for documentation.

The download pipeline (quality gate) and the embed pipeline (window-membership
guard) each drop or skip tickers for different reasons. This module
consolidates them into one record so a disclaimer / coverage note can be
generated at any time, independent of re-running the download.

Categories:
* ``broken``            — failed the bar-quality gate (impossible OHLC from a
                          mis-resolved yfinance instrument, all-zero prices,
                          implausible extreme moves). Excluded from equity_bars.
* ``missing_window``     — an S&P 500 member overlapping the backtest window but
                          absent from the bars AND not documented as unavailable
                          (unexplained gap — must be recovered or documented).
* ``documented_unavailable`` — absent but explained in equity_unavailable.json
                          (genuine delisting / foreign-listing collision, etc.).

Yahoo rate-limiting is reported by ``repair_equity_data.py`` as its PENDING
list on the console; throttled names are recoverable by re-running that script
and are not tracked here.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from data.bar_quality import ticker_quality_verdict


def load_unavailable(path: Optional[str]) -> dict:
    """Load equity_unavailable.json -> {ticker: reason}."""
    if not path or not Path(path).exists():
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            recs = json.load(f)
        return {r["ticker"]: r.get("reason", "") for r in recs}
    except Exception:
        return {}


def collect_fundamentals_gap(
    membership: dict,
    fundamentals: dict,
    win_start: str,
    win_end: str,
) -> dict[str, str]:
    """Compute S1 survivorship gap: window members without fundamentals.

    Simple set subtraction: ``window_members - fundamentals_keys``. No decoupling;
    bars and fundamentals are independent sources with different coverage.

    * ``window_members``: tickers with at least one membership interval
      overlapping ``[win_start, win_end]`` (excludes ^TNX/^GSPC).
    * ``fundamentals``: dict loaded from fundamentals_history.json (keys are tickers).

    Returns ``{ticker: reason}`` for each window member absent from fundamentals.
    """
    fundamentals_keys = set(fundamentals.keys()) if fundamentals else set()
    gap: dict[str, str] = {}
    for t, ivs in membership.items():
        if t in ("^TNX", "^GSPC"):
            continue
        if not any(s <= win_end and (e is None or e >= win_start) for s, e in ivs):
            continue
        if t not in fundamentals_keys:
            gap[t] = "window member absent from fundamentals_history (no SEC XBRL 2009+ / edgar match)"
    return gap


def collect_exclusions(
    bars: dict,
    membership: dict,
    win_start: str,
    win_end: str,
    unavailable_path: Optional[str] = None,
    fundamentals: Optional[dict] = None,
) -> dict:
    """Return categorized exclusions across the whole universe.

    ``bars`` is the equity_bars dict, ``membership`` the S&P 500 interval map.
    If ``fundamentals`` is provided (fundamentals_history dict), also computes
    ``window_members_without_fundamentals`` via simple set subtraction
    (``window_members - fundamentals_keys``) for S1 survivorship documentation.
    """
    unavailable = load_unavailable(unavailable_path)

    broken: dict[str, str] = {}
    for t, tb in bars.items():
        is_bad, why = ticker_quality_verdict(tb)
        if is_bad:
            broken[t] = why

    missing_window: dict[str, str] = {}
    documented: dict[str, str] = {}
    for t, ivs in membership.items():
        if t in ("^TNX", "^GSPC"):
            continue
        if not any(s <= win_end and (e is None or e >= win_start) for s, e in ivs):
            continue
        if t not in bars:
            if t in unavailable:
                documented[t] = unavailable[t]
            else:
                missing_window[t] = "absent from bars, not documented"

    result: dict = {
        "broken": broken,
        "missing_window": missing_window,
        "documented_unavailable": documented,
    }
    if fundamentals is not None:
        result["window_members_without_fundamentals"] = collect_fundamentals_gap(
            membership, fundamentals, win_start, win_end
        )
    return result


def render_text_report(
    info: dict,
    n_bars: int,
    window: tuple,
    source: str,
    generated_utc: Optional[str] = None,
) -> str:
    """Render a human-readable missing-data.txt."""
    if generated_utc is None:
        generated_utc = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    lines = [
        "EQUITY BAR EXCLUSIONS — tickers excluded from the backtest",
        "================================================================",
        f"Generated (UTC): {generated_utc}",
        f"Backtest window: {window[0]} .. {window[1]}",
        f"Source bars:     {source} ({n_bars} tickers)",
        "",
        "DISCLAIMER: the tickers listed below are NOT part of the backtest's",
        "equity-bar universe. They were excluded by the data pipeline",
        "(broken yfinance auto_adjust output or missing data), not by the",
        "strategy. Backtest results therefore do not reflect any position",
        "that would have been taken in these names.",
        "",
    ]

    def _sec(title, items, reason_fn):
        lines.append(f"== {title} ==")
        if not items:
            lines.append("  (none)")
        for t in sorted(items):
            lines.append(f"  {t:<8} {reason_fn(t)}")
        lines.append("")

    _sec(
        "BROKEN (quality gate: malformed / all-zero / wrong-instrument data)",
        info["broken"],
        lambda t: info["broken"][t],
    )
    _sec(
        "MISSING WINDOW MEMBERS (in S&P 500 window, absent, UNEXPLAINED)",
        info["missing_window"],
        lambda t: info["missing_window"][t],
    )
    _sec(
        "DOCUMENTED UNAVAILABLE (absent but explained)",
        info["documented_unavailable"],
        lambda t: info["documented_unavailable"][t],
    )
    # S1 survivorship gap: window members vs fundamentals (bars and fundamentals are decoupled sources)
    if "window_members_without_fundamentals" in info:
        _sec(
            "WINDOW MEMBERS WITHOUT FUNDAMENTALS (S1 survivorship/coverage gap: window_members - fundamentals_keys)",
            info["window_members_without_fundamentals"],
            lambda t: info["window_members_without_fundamentals"][t],
        )
        lines.append(
            "NOTE: window_members_without_fundamentals is a COVERAGE gap (SEC XBRL 2009+ vs "
            "S&P 500 membership). Bars and fundamentals are independent sources; a ticker "
            "absent from fundamentals is simply not tradeable by the PIT screen (no look-ahead, "
            "no decoupling). See docs/data-limitations.md."
        )
        lines.append("")

    lines.append(
        "Totals: broken={broken} missing_window={missing} "
        "documented_unavailable={doc}".format(
            broken=len(info["broken"]),
            missing=len(info["missing_window"]),
            doc=len(info["documented_unavailable"]),
        )
    )
    if "window_members_without_fundamentals" in info:
        lines.append(
            "  window_members_without_fundamentals={gap}".format(
                gap=len(info["window_members_without_fundamentals"])
            )
        )
    return "\n".join(lines)


def write_reports(
    data_dir: str,
    info: dict,
    n_bars: int,
    window: tuple,
    source: str,
) -> Path:
    """Write missing-data.txt (human) and missing-data.json (machine) to data_dir."""
    data_dir = Path(data_dir)
    data_dir.mkdir(parents=True, exist_ok=True)
    txt = render_text_report(info, n_bars, window, source)
    (data_dir / "missing-data.txt").write_text(txt, encoding="utf-8")
    (data_dir / "missing-data.json").write_text(
        json.dumps(info, indent=2), encoding="utf-8"
    )
    return data_dir / "missing-data.txt"
