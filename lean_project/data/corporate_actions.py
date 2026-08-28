"""Curated S&P 500 spinoff corporate actions.

Each entry is ``(ex_date, added_ticker, parent_ticker)`` meaning: on the last
trading day BEFORE ``ex_date`` the ``parent_ticker`` position must be liquidated
(the spun-off ``added_ticker`` is a NEW constituent handled by membership, not a
price merger, so no stitching).

Source: S&P Dow Jones Indices change announcements + Wikipedia "List of S&P 500
companies" Selected changes, cross-checked against S&P methodology (the spin-off
is added at a zero price at the close of the day before the ex-date and **no
price adjustment is applied to the parent**, so the parent's return series gaps
on the ex-date — it must be exited before).

Rows marked ``[verify]`` in the original research were best-effort dated; confirm
exact ex-dates/parents from S&P Dow Jones announcements before relying on them
for live trading. Only the 2020-2026 rows affect the current backtest window.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Iterable, Set, Tuple

from data.nys_calendar import is_nyse_open

# (ex_date, added_ticker, parent_ticker)
SPINOFFS: list[Tuple[str, str, str]] = [
    ("2007-10-01", "TDC", "NCR"),
    ("2007-07-02", "COV", "TYC"),
    ("2007-07-02", "DFS", "MS"),
    ("2008-03-31", "PM", "MO"),
    ("2011-01-03", "MMI", "MOT"),
    ("2011-10-31", "XYL", "ITT"),
    ("2011-12-20", "TRIP", "EXPE"),
    ("2012-04-23", "PSX", "COP"),
    ("2012-10-01", "ADT", "TYC"),
    ("2012-10-01", "MDLZ", "KFT"),
    ("2013-01-02", "ABBV", "ABT"),
    ("2013-06-21", "ZTS", "PFE"),
    ("2013-12-02", "ALLE", "IR"),
    ("2014-05-01", "NAVI", "SLM"),
    ("2015-07-01", "BXLT", "BAX"),
    ("2015-07-20", "PYPL", "EBAY"),
    ("2015-11-02", "HPE", "HPQ"),
    ("2017-08-08", "BHF", "MET"),
    ("2019-03-19", "FOXA", "FOX"),
    ("2019-04-02", "DOW", "DWDP"),
    ("2019-06-03", "CTVA", "DWDP"),
    ("2020-04-03", "OTIS", "UTX"),
    ("2020-04-03", "CARR", "UTX"),
    ("2020-10-09", "VNT", "FTV"),
    ("2021-06-03", "OGN", "MRK"),
    ("2022-02-02", "CEG", "EXC"),
    ("2022-12-15", "MBC", "FBHS"),
    ("2023-01-04", "GEHC", "GE"),
    ("2023-08-25", "KVUE", "JNJ"),
    ("2023-10-02", "VLTO", "DHR"),
    ("2024-04-01", "SOLV", "MMM"),
    ("2024-04-02", "GEV", "GE"),
    ("2024-09-30", "AMTM", "AECOM"),
    ("2025-10-30", "SOLS", "HON"),
    ("2025-11-03", "Q", "DD"),
    ("2026-06-01", "FDXF", "FDX"),
    ("2026-06-29", "HONA", "HON"),
]


def _parse(d: str) -> date:
    return date.fromisoformat(d)


def last_trading_day(d: date) -> date:
    """Return the last NYSE trading day <= ``d``.

    Uses the NYSE calendar (``data.nys_calendar.is_nyse_open``) so holidays
    such as Good Friday are treated as non-trading days. Corporate-action
    exit timing (spinoff / index removal) is therefore NYSE-aware.
    """
    cur = d
    while not is_nyse_open(cur):
        cur -= timedelta(days=1)
    return cur


def spinoff_parent_exits(date_str: str) -> Set[str]:
    """Return the set of parent tickers to liquidate on ``date_str``.

    A parent is exited on the last trading day BEFORE its spinoff ex-date.
    """
    today = _parse(date_str)
    exits: Set[str] = set()
    for ex_date, _added, parent in SPINOFFS:
        exit_day = last_trading_day(_parse(ex_date) - timedelta(days=1))
        if today == exit_day:
            exits.add(parent)
    return exits


def membership_end_exits(
    membership: dict,
    held_tickers: Iterable[str],
    date_str: str,
) -> Set[str]:
    """Tickers in ``held_tickers`` whose active membership interval has ended.

    A held ticker is exited if its active interval has an ``end_date`` and today
    is on/after the last trading day before that end_date (i.e. the
    rename/merger/delisting has effectively occurred).
    """
    today = _parse(date_str)
    exits: Set[str] = set()
    for ticker in held_tickers:
        for start, end in membership.get(ticker, []):
            if start <= date_str and (end is None or date_str <= end):
                if end is not None and today >= last_trading_day(_parse(end)):
                    exits.add(ticker)
    return exits


def corporate_action_exits(
    membership: dict,
    held_tickers: Iterable[str],
    date_str: str,
) -> Set[str]:
    """Union of membership-ending exits and spinoff-parent exits."""
    exits = membership_end_exits(membership, held_tickers, date_str)
    exits |= spinoff_parent_exits(date_str)
    return exits
