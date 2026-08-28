from universe.pit_data import fundamental_as_of, rolling_beta


HIST = {
    # PIT history keyed by filing_date (SEC availability), with period for audit.
    # Q1 period 2023-03-31 filed 2023-05-15, Q2 period 2023-06-30 filed 2023-08-14, etc.
    # Lag ~45 days; using period as key would be look-ahead.
    "AAPL": {
        "2023-05-15": {"period": "2023-03-31", "filed": "2023-05-15", "book_value": 3.92, "roe": 1.50, "eps": 6.10},
        "2023-08-14": {"period": "2023-06-30", "filed": "2023-08-14", "book_value": 4.01, "roe": 1.61, "eps": 6.32},
        "2023-11-03": {"period": "2023-09-30", "filed": "2023-11-03", "book_value": 4.20, "roe": 1.70, "eps": 6.55},
    }
}

# Legacy layout (period-keyed, no filed field) — kept for backward compat.
HIST_LEGACY = {
    "AAPL": {
        "2023-03-31": {"book_value": 3.92, "roe": 1.50, "eps": 6.10},
        "2023-06-30": {"book_value": 4.01, "roe": 1.61, "eps": 6.32},
        "2023-09-30": {"book_value": 4.20, "roe": 1.70, "eps": 6.55},
    }
}


def test_fundamental_as_of_picks_latest_quarter_at_or_before_date():
    snap = fundamental_as_of(HIST, "AAPL", "2023-08-15")
    assert snap["book_value"] == 4.01  # Aug filing used before Nov filing


def test_fundamental_as_of_exact_quarter_date_inclusive():
    snap = fundamental_as_of(HIST, "AAPL", "2023-11-03")
    assert snap["book_value"] == 4.20


def test_fundamental_as_of_respects_filing_date_not_period():
    """Look-ahead guard: period 2023-03-31 ended but not yet filed at 2023-04-15.

    Old period-keyed code would return the Q1 snapshot at 2023-04-15 (look-ahead).
    PIT with filing_date must return None until filing_date 2023-05-15.
    """
    assert fundamental_as_of(HIST, "AAPL", "2023-04-15") is None
    snap = fundamental_as_of(HIST, "AAPL", "2023-05-15")
    assert snap["book_value"] == 3.92


def test_fundamental_as_of_legacy_period_keyed_is_rejected():
    """Legacy period-keyed data (no filed/filing_date) is now rejected — no look-ahead fallback.

    HIST_LEGACY outer keys are fiscal period ends; without a filing_date they
    would be ~45d early. After L4 fix, fundamental_as_of must return None
    (run download_edgartools_data.py to regenerate with filing-keyed data).
    """
    assert fundamental_as_of(HIST_LEGACY, "AAPL", "2023-04-15") is None
    assert fundamental_as_of(HIST_LEGACY, "AAPL", "2023-11-03") is None


def test_fundamental_as_of_uses_filed_field_when_key_differs():
    """Robustness: outer key may differ from inner filed field; PIT uses filed."""
    hist_mixed = {
        "AAPL": {
            # Key is filing date, but inner filed is authoritative
            "2023-05-15": {"period": "2023-03-31", "filed": "2023-05-15", "book_value": 3.92, "roe": 1.50, "eps": 6.10},
        }
    }
    # Before filing, None
    assert fundamental_as_of(hist_mixed, "AAPL", "2023-05-14") is None
    assert fundamental_as_of(hist_mixed, "AAPL", "2023-05-15")["book_value"] == 3.92


def test_fundamental_as_of_none_before_first_quarter():
    assert fundamental_as_of(HIST, "AAPL", "2022-12-31") is None


def test_fundamental_as_of_missing_ticker_none():
    assert fundamental_as_of(HIST, "MSFT", "2023-06-30") is None


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
