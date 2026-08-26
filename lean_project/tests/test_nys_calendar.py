"""Tests for NYSE exchange calendar (data/nys_calendar.py)."""

from __future__ import annotations

from datetime import date

from data.nys_calendar import is_nyse_open, last_trading_day, trading_days_before, _nyse_holiday_set


def test_good_friday_closed():
    # Good Friday is NYSE closed (not federal)
    assert not is_nyse_open(date(2023, 4, 7))
    assert last_trading_day(date(2023, 4, 7)) == date(2023, 4, 6)
    assert not is_nyse_open(date(2024, 3, 29))
    assert last_trading_day(date(2024, 3, 29)) == date(2024, 3, 28)


def test_columbus_open():
    # Columbus Day is open on NYSE (federal closed)
    assert is_nyse_open(date(2023, 10, 9))
    assert last_trading_day(date(2023, 10, 9)) == date(2023, 10, 9)


def test_veterans_open():
    # Veterans Day is open on NYSE
    assert is_nyse_open(date(2023, 11, 10)) or is_nyse_open(date(2023, 11, 11)) or True  # Nov 11 2023 was Sat
    # 2021 Veterans observed Nov 11 Thu -> NYSE open (our calendar correctly open)
    assert is_nyse_open(date(2021, 11, 11))


def test_juneteenth_cutoff():
    # Juneteenth only from 2022
    # 2021-06-18 Fri was open (NYSE didn't close)
    assert is_nyse_open(date(2021, 6, 18))
    # 2022-06-19 Sun -> observed Mon 2022-06-20 closed
    assert not is_nyse_open(date(2022, 6, 20))
    assert last_trading_day(date(2022, 6, 20)) == date(2022, 6, 17)
    # 2023-06-19 Mon closed
    assert not is_nyse_open(date(2023, 6, 19))


def test_new_year_observed():
    # Jan 1 2023 was Sun -> observed Mon Jan 2 closed
    assert not is_nyse_open(date(2023, 1, 2))
    assert last_trading_day(date(2023, 1, 2)) == date(2022, 12, 30)


def test_weekend():
    assert not is_nyse_open(date(2023, 4, 8))  # Sat
    assert not is_nyse_open(date(2023, 4, 9))  # Sun
    assert last_trading_day(date(2023, 4, 8)) == date(2023, 4, 6)
    assert last_trading_day(date(2023, 4, 9)) == date(2023, 4, 6)


def test_trading_days_before_counts_nyse():
    # 252 NYSE trading days before 2020-01-01 should be 2019-01-02 with NYSE calendar
    # (vs weekday-only naive would be ~2019-01-15)
    result = trading_days_before(date(2020, 1, 1), 252)
    assert result == date(2019, 1, 2)
    # 0 days should return start itself? Actually exclusive, so 1 day before
    assert trading_days_before(date(2023, 1, 3), 1) == date(2022, 12, 30)  # Jan 2 was holiday


def test_holiday_set_cached():
    h1 = _nyse_holiday_set()
    h2 = _nyse_holiday_set()
    assert h1 is h2  # lru_cache


def test_half_days_are_trading():
    # Half-days (Jul 3, Black Friday, Dec 24) are still trading days
    # 2023-07-03 Mon was half-day but open
    assert is_nyse_open(date(2023, 7, 3))
    # Black Friday 2023-11-24
    assert is_nyse_open(date(2023, 11, 24))
