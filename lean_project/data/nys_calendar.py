"""NYSE (XNYS) trading calendar — single source of truth.

Hard-coded NYSE holidays (no new dependency) so it works offline inside the
Lean Docker container and has zero per-call pandas overhead.

Rules (1996-2035):
- Weekends (Sat/Sun) closed.
- Good Friday closed (Easter algorithm).
- Observed New Year / July 4 / Christmas / Juneteenth shift Sat->Fri, Sun->Mon.
- Columbus Day and Veterans Day are OPEN (unlike USFederalHolidayCalendar).
- Juneteenth closed only from 2022-06-19 onward (first NYSE close 2022-06-20).
- Half-days (Jul 3, Black Friday, Dec 24) are still trading days — NOT holidays.

The module is cached (frozenset of ~300 dates) so per-call lookups are O(1)
instead of 50k pandas calendar constructions per backtest.
"""

from __future__ import annotations

from datetime import date, timedelta
from functools import lru_cache


_JUNETEENTH_START = date(2022, 6, 19)


def _easter_sunday(year: int) -> date:
    """Anonymous Gregorian Easter algorithm."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return date(year, month, day)


def _good_friday(year: int) -> date:
    return _easter_sunday(year) - timedelta(days=2)


def _observed(d: date) -> date:
    """Shift Sat->Fri, Sun->Mon (NYSE observed rule)."""
    if d.weekday() == 5:  # Saturday
        return d - timedelta(days=1)
    if d.weekday() == 6:  # Sunday
        return d + timedelta(days=1)
    return d


@lru_cache(maxsize=1)
def _nyse_holiday_set(start_year: int = 1990, end_year: int = 2035) -> frozenset[date]:
    holidays: set[date] = set()
    for y in range(start_year, end_year + 1):
        # Fixed holidays with observed shifts
        for m, d in [(1, 1), (7, 4), (12, 25)]:
            holidays.add(_observed(date(y, m, d)))
        # Juneteenth (only from 2022)
        if date(y, 6, 19) >= _JUNETEENTH_START:
            holidays.add(_observed(date(y, 6, 19)))
        # Good Friday
        holidays.add(_good_friday(y))
        # Moving holidays
        # MLK: 3rd Monday January (from 1998, but include all years for safety)
        # Presidents: 3rd Monday February
        # Memorial: last Monday May
        # Labor: 1st Monday September
        # Thanksgiving: 4th Thursday November
        # We compute these via weekday iteration to avoid pandas
        # MLK
        d = date(y, 1, 1)
        # Find 3rd Monday January
        while d.weekday() != 0:
            d += timedelta(days=1)
        d += timedelta(weeks=2)
        holidays.add(d)
        # Presidents
        d = date(y, 2, 1)
        while d.weekday() != 0:
            d += timedelta(days=1)
        d += timedelta(weeks=2)
        holidays.add(d)
        # Memorial: last Monday May
        d = date(y, 5, 31)
        while d.weekday() != 0:
            d -= timedelta(days=1)
        holidays.add(d)
        # Labor: 1st Monday September
        d = date(y, 9, 1)
        while d.weekday() != 0:
            d += timedelta(days=1)
        holidays.add(d)
        # Thanksgiving: 4th Thursday November
        d = date(y, 11, 1)
        while d.weekday() != 3:
            d += timedelta(days=1)
        d += timedelta(weeks=3)
        holidays.add(d)
        # Christmas observed already added via fixed above
    return frozenset(holidays)


def is_nyse_open(d: date) -> bool:
    """True if NYSE is open on ``d`` (weekday and not a holiday)."""
    if d.weekday() >= 5:
        return False
    return d not in _nyse_holiday_set()


def last_trading_day(d: date) -> date:
    """Last NYSE trading day <= ``d``."""
    cur = d
    holidays = _nyse_holiday_set()
    while cur.weekday() >= 5 or cur in holidays:
        cur -= timedelta(days=1)
    return cur


def trading_days_before(start: date, n: int) -> date:
    """Step back ``n`` NYSE trading days from ``start`` (exclusive)."""
    cur = start
    collected = 0
    holidays = _nyse_holiday_set()
    while collected < n:
        cur -= timedelta(days=1)
        if cur.weekday() < 5 and cur not in holidays:
            collected += 1
    return cur


__all__ = ["is_nyse_open", "last_trading_day", "trading_days_before", "_nyse_holiday_set"]
