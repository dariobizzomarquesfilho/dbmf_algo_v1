"""S&P 500 point-in-time membership utilities.

Loads ``sp500_ticker_start_end.csv`` and provides:
- ``load_sp500_membership()`` — returns ``{ticker: [(start_date, end_date_or_None), ...]}``
- ``intervals_active()`` — membership predicate for a ticker's intervals on a date
- ``clip_to_membership()`` — keep only bars inside membership intervals
- ``build_alias_map()`` — diagnostics-only rename predecessor/successor map
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import Optional


def load_sp500_membership(csv_path: str) -> dict[str, list[tuple[str, Optional[str]]]]:
    """Return {ticker: [(start_date, end_date_or_None), ...]}."""
    membership: dict[str, list[tuple[str, Optional[str]]]] = {}
    with open(csv_path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        if reader.fieldnames:
            reader.fieldnames = [h.strip() for h in reader.fieldnames]
        for row in reader:
            # Handle BOM/case variants
            ticker = (row.get("ticker") or row.get("Ticker") or "").strip()
            if not ticker:
                continue
            start = (row.get("start_date") or row.get("Start_date") or "").strip()
            end_raw = (row.get("end_date") or row.get("End_date") or "")
            end = end_raw.strip() or None
            if not start:
                continue
            membership.setdefault(ticker, []).append((start, end))
    return membership


def intervals_active(entries: list[tuple[str, Optional[str]]], date_str: str) -> bool:
    """Return True if ``date_str`` falls inside any (start, end_or_None) interval.

    ``end is None`` means the interval is open-ended (a current member). The
    comparison uses ISO ``YYYY-MM-DD`` strings, which compare correctly with
    ``<=``. This is the single membership predicate used for both index-entry
    enforcement and dynamic subscription; requiring ``date_str <= end`` (when
    ended) prevents long-dead historical members from being subscribed.
    """
    for start, end in entries:
        if start <= date_str and (end is None or date_str <= end):
            return True
    return False


def clip_to_membership(
    bars: dict,
    membership: dict[str, list[tuple[str, Optional[str]]]],
    end_default: str,
) -> dict:
    """Keep only bars inside any of the ticker's membership intervals.

    For each ticker, retains bars ``d`` where ``start <= d <= (end or
    end_default)`` for at least one membership interval. This (a) drops a
    successor's pre-rename prices Yahoo may return under the new ticker, (b)
    drops post-delisting ghost bars, and (c) correctly unions multi-interval
    tickers (e.g. AAL). Symbols absent from ``membership`` (e.g. ``^TNX``,
    ``^GSPC``) are returned unchanged.
    """
    clipped: dict = {}
    for ticker, ticker_bars in bars.items():
        intervals = membership.get(ticker)
        if not intervals:
            clipped[ticker] = ticker_bars
            continue
        keep = set()
        for start, end in intervals:
            e = end if end is not None else end_default
            for d in ticker_bars:
                if start <= d <= e:
                    keep.add(d)
        if keep:
            clipped[ticker] = {d: ticker_bars[d] for d in ticker_bars if d in keep}
    return clipped


def build_alias_map(
    membership: dict[str, list[tuple[str, Optional[str]]]],
    max_gap_days: int = 5,
) -> dict[str, str]:
    """Diagnostics-only map of rename predecessors to their near-day successor.

    A predecessor ``P`` (with ``end_date``) aliases successor ``S`` when ``S``
    starts within ``[end_date, end_date + max_gap_days]`` and ``S != P``. Used
    for reporting only — never to merge prices.
    """
    from datetime import date, timedelta

    def _parse(s: str) -> date:
        return date.fromisoformat(s)

    aliases: dict[str, str] = {}
    for ticker, intervals in membership.items():
        for start, end in intervals:
            if end is None:
                continue
            window_end = _parse(end) + timedelta(days=max_gap_days)
            best = None
            for s2, s2_intervals in membership.items():
                if s2 == ticker:
                    continue
                for s2_start, _s2_end in s2_intervals:
                    sd = _parse(s2_start)
                    if _parse(end) <= sd <= window_end:
                        if best is None or sd < best[0]:
                            best = (sd, s2)
            if best is not None:
                aliases[ticker] = best[1]
    return aliases
