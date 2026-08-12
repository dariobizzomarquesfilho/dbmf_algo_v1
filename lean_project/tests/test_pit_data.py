from universe.pit_data import fundamental_as_of, latest_price_as_of, rolling_beta


HIST = {
    "AAPL": {
        "2023-03-31": {"book_value": 3.92, "roe": 1.50, "eps": 6.10},
        "2023-06-30": {"book_value": 4.01, "roe": 1.61, "eps": 6.32},
        "2023-09-30": {"book_value": 4.20, "roe": 1.70, "eps": 6.55},
    }
}


def test_fundamental_as_of_picks_latest_quarter_at_or_before_date():
    snap = fundamental_as_of(HIST, "AAPL", "2023-08-15")
    assert snap["book_value"] == 4.01  # June value used before Sept report


def test_fundamental_as_of_exact_quarter_date_inclusive():
    snap = fundamental_as_of(HIST, "AAPL", "2023-09-30")
    assert snap["book_value"] == 4.20


def test_fundamental_as_of_none_before_first_quarter():
    assert fundamental_as_of(HIST, "AAPL", "2022-12-31") is None


def test_fundamental_as_of_missing_ticker_none():
    assert fundamental_as_of(HIST, "MSFT", "2023-06-30") is None


def test_latest_price_as_of_uses_bar_at_or_before_date():
    bars = {
        "2023-01-03": {"close": 100.0},
        "2023-02-01": {"close": 110.0},
        "2023-02-20": {"close": 120.0},
    }
    assert latest_price_as_of(bars, "2023-02-10") == 110.0
    assert latest_price_as_of(bars, "2023-02-20") == 120.0
    assert latest_price_as_of(bars, "2022-01-01") is None


def test_rolling_beta_returns_beta_alpha():
    import numpy as np

    np.random.seed(42)
    n = 300
    m = {}
    s = {}
    price_m = 100.0
    price_s = 100.0
    # market random walk, stock = 2x market + noise
    for i in range(n):
        month = (i // 28) + 1
        day = (i % 28) + 1
        d = f"2023-{month:02d}-{day:02d}"
        mr = np.random.normal(0.001, 0.02)
        sr = 2.0 * mr + np.random.normal(0, 0.01)
        price_m *= 1 + mr
        price_s *= 1 + sr
        m[d] = {"close": price_m}
        s[d] = {"close": price_s}
    as_of = f"2023-{((n - 1) // 28) + 1:02d}-{((n - 1) % 28) + 1:02d}"
    result = rolling_beta(s, m, as_of)
    assert result is not None
    beta, alpha = result
    assert abs(beta - 2.0) < 0.3


def test_rolling_beta_none_when_few_points():
    m = {"2023-01-01": {"close": 1.0}, "2023-01-02": {"close": 1.01}}
    s = {"2023-01-01": {"close": 1.0}, "2023-01-02": {"close": 1.02}}
    assert rolling_beta(s, m, "2023-01-02") is None  # < 30 aligned points
