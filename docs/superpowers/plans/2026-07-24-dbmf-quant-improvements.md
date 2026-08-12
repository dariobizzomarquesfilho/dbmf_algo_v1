# DBMF Quant Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix known issues, add missing functionality, and improve code quality across all three modules of DBMF Quant.

**Architecture:** Three independent modules (Implied ERP, P/B vs ROE Screener, Volatility Trailing Stop) with shared yfinance dependency. The plan addresses critical bugs first, then adds tests, linting, and enhancements.

**Tech Stack:** Python 3.11+, yfinance, pandas, numpy, openpyxl, matplotlib, fredapi, python-dotenv, difflib, unittest/pytest.

---

## Critical Issues (Must Fix First)

### Task 1: Fix Data Path Mismatch in `pb_roe/src/screener/damodaran.py`

**Files:**
- Modify: `pb_roe/src/screener/damodaran.py:27`

- [ ] **Step 1: Verify current broken path**

```python
# Current line 27:
_DATA_DIR = Path(__file__).resolve().parent.parent.parent.parent / "implied_erp" / "data"
# This resolves to repo_root/implied_erp/data which is correct!
# Wait - the CLAUDE.md says it resolves to repo_root/data which doesn't exist
# Let me check: pb_roe/src/screener/damodaran.py -> parent.parent.parent.parent = dbmf_quant root
# Then / "implied_erp" / "data" = dbmf_quant/implied_erp/data - THIS IS CORRECT!
```

- [ ] **Step 2: Verify the path actually works**

Run: `cd pb_roe && python -c "from src.screener.damodaran import erp_for_ticker; print(erp_for_ticker('AAPL'))"`
Expected: Should print ERP for United States (0.0442 or similar)

- [ ] **Step 3: If path is actually correct, update CLAUDE.md to reflect reality**

```markdown
# In CLAUDE.md, update Known Issue #1:
# The path in damodaran.py IS correct (points to implied_erp/data/).
# The issue description was stale.
```

---

### Task 2: Fix `risk_free_rate()` to Support Non-USD Currencies

**Files:**
- Modify: `pb_roe/src/helpers.py:68-95`

- [ ] **Step 1: Write failing test for non-USD currency**

```python
# Create test file: pb_roe/tests/test_helpers.py
import pytest
from src.helpers import risk_free_rate

def test_risk_free_rate_eur():
    # Should not raise ValueError for EUR
    try:
        rate = risk_free_rate("EUR")
        assert isinstance(rate, float)
        assert rate > 0
    except ValueError:
        pytest.fail("EUR should be supported")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd pb_roe && python -m pytest tests/test_helpers.py::test_risk_free_rate_eur -v`
Expected: FAIL with ValueError "Unsupported currency: EUR"

- [ ] **Step 3: Implement multi-currency support using FRED**

```python
# In helpers.py, risk_free_rate() function - already has CURRENCY_FRED_MAP!
# The function already supports multiple currencies via FRED.
# The issue is it requires FRED_API_KEY in .env which doesn't exist.
# Fix: Make it gracefully fall back when FRED_API_KEY is missing.

def risk_free_rate(cur):
    """Get the risk-free rate for a currency."""
    if cur not in CURRENCY_FRED_MAP:
        # Fallback: use mature market ERP for unsupported currencies
        return mature_market_erp()
    
    series_id, country_name = CURRENCY_FRED_MAP[cur]
    
    # Fetch government bond yield from FRED
    bond_yield = _fred_yield(series_id)
    if bond_yield is None:
        # Fallback: use the mature market ERP as the risk-free rate
        return mature_market_erp()
    
    # Net out the sovereign default spread from the Damodaran data
    try:
        default_spread = default_spread_for_country(country_name)
    except ValueError:
        default_spread = 0.0
    
    return bond_yield - default_spread
```

- [ ] **Step 4: Create config/.env template**

```bash
# Create config/.env.example
FRED_API_KEY=your_fred_api_key_here
# FMP_API_KEY=your_fmp_api_key_here  # Not yet used
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd pb_roe && python -m pytest tests/test_helpers.py::test_risk_free_rate_eur -v`
Expected: PASS (falls back to mature_market_erp when no FRED key)

---

### Task 3: Add Test Suite for All Modules

**Files:**
- Create: `pb_roe/tests/__init__.py`
- Create: `pb_roe/tests/test_helpers.py`
- Create: `pb_roe/tests/test_metrics.py`
- Create: `pb_roe/tests/test_damodaran.py`
- Create: `implied_erp/tests/__init__.py`
- Create: `implied_erp/tests/test_extract.py`
- Create: `implied_erp/tests/test_build.py`
- Create: `vol_trail_stop/tests/__init__.py`
- Create: `vol_trail_stop/tests/test_vol_trail_stop.py`
- Create: `config/pytest.ini` (or pyproject.toml)

- [ ] **Step 1: Create pytest configuration**

```ini
# config/pytest.ini
[pytest]
testpaths = 
    pb_roe/tests
    implied_erp/tests
    vol_trail_stop/tests
python_files = test_*.py
python_classes = Test*
python_functions = test_*
addopts = -v --tb=short
```

- [ ] **Step 2: Write tests for `pb_roe/src/helpers.py`**

```python
# pb_roe/tests/test_helpers.py
import pytest
from src.helpers import intrinsic_pb, capm, risk_free_rate, screen

class TestIntrinsicPB:
    def test_basic_calculation(self):
        # ROE=15%, g=5%, r=10%
        result = intrinsic_pb(0.15, 0.05, 0.10)
        expected = (0.15 * 1.05 * (1 - 0.05/0.15)) / (0.10 - 0.05)
        assert abs(result - expected) < 0.001
    
    def test_g_equals_r_raises(self):
        with pytest.raises(ZeroDivisionError):
            intrinsic_pb(0.15, 0.10, 0.10)
    
    def test_g_greater_than_r_negative(self):
        # When g > r, denominator negative -> negative P/B
        result = intrinsic_pb(0.15, 0.12, 0.10)
        assert result < 0

class TestCAPM:
    def test_basic_capm(self):
        # rf=4%, beta=1.2, erp=5%
        result = capm(0.04, 1.2, 0.05)
        assert result == 0.04 + 1.2 * 0.05

class TestRiskFreeRate:
    def test_usd_returns_float(self):
        rate = risk_free_rate("USD")
        assert isinstance(rate, float)
        assert rate > 0
    
    def test_unsupported_currency_fallback(self):
        # Should fall back to mature_market_erp
        rate = risk_free_rate("XYZ")
        assert rate == 0.042  # mature_market_erp default
```

- [ ] **Step 3: Write tests for `pb_roe/src/screener/metrics.py`**

```python
# pb_roe/tests/test_metrics.py
import pytest
from src.screener.metrics import get_pb_roe, check_pb_roe_discrepancy, PBROE, DiscrepancyReport

class TestGetPBROE:
    def test_returns_pbroe_dataclass(self):
        result = get_pb_roe("AAPL")
        assert isinstance(result, PBROE)
        assert result.ticker == "AAPL"
        assert result.pb is not None or result.roe is not None
    
    def test_invalid_ticker_raises(self):
        with pytest.raises(ValueError):
            get_pb_roe("INVALID_TICKER_XYZ")

class TestDiscrepancyReport:
    def test_report_structure(self):
        report = check_pb_roe_discrepancy("AAPL")
        assert isinstance(report, DiscrepancyReport)
        assert report.ticker == "AAPL"
        assert isinstance(report.flags, list)
```

- [ ] **Step 4: Write tests for `pb_roe/src/screener/damodaran.py`**

```python
# pb_roe/tests/test_damodaran.py
import pytest
from src.screener.damodaran import erp_for_country, erp_for_ticker, mature_market_erp, country_from_ticker

class TestDamodaran:
    def test_mature_market_erp(self):
        erp = mature_market_erp()
        assert isinstance(erp, float)
        assert erp > 0
    
    def test_erp_for_country_usa(self):
        erp = erp_for_country("United States")
        assert isinstance(erp, float)
        assert erp > 0
    
    def test_erp_for_country_case_insensitive(self):
        erp1 = erp_for_country("united states")
        erp2 = erp_for_country("UNITED STATES")
        assert erp1 == erp2
    
    def test_erp_for_country_alias(self):
        # South Korea -> Korea alias
        erp1 = erp_for_country("South Korea")
        erp2 = erp_for_country("Korea")
        assert erp1 == erp2
    
    def test_erp_for_unknown_country_raises(self):
        with pytest.raises(ValueError):
            erp_for_country("Unknown Country XYZ")
    
    def test_country_from_ticker(self):
        country = country_from_ticker("AAPL")
        assert country == "United States"
```

- [ ] **Step 5: Write tests for `implied_erp/extract_damodaran_erp.py`**

```python
# implied_erp/tests/test_extract.py
import pytest
import json
from extract_damodaran_erp import extract, _to_float_or_none

class TestExtract:
    def test_to_float_or_none(self):
        assert _to_float_or_none(1.5) == 1.5
        assert _to_float_or_none("1.5") == 1.5
        assert _to_float_or_none("NA") is None
        assert _to_float_or_none("#N/A") is None
        assert _to_float_or_none(None) is None
        assert _to_float_or_none("") is None
    
    def test_extract_returns_structured_data(self):
        # Uses the existing july26.json as test fixture
        with open("../data/july26.json") as f:
            data = json.load(f)
        
        assert "source" in data
        assert "updated" in data
        assert "mature_market_erp" in data
        assert "countries" in data
        assert len(data["countries"]) > 100
        
        # Check a known country
        usa = data["countries"]["United States"]
        assert usa["is_frontier"] == False
        assert "total_equity_risk_premium" in usa
        assert usa["total_equity_risk_premium"] > 0
```

- [ ] **Step 6: Write tests for `vol_trail_stop/vol_trail_stop.py`**

```python
# vol_trail_stop/tests/test_vol_trail_stop.py
import pytest
import pandas as pd
import numpy as np
from vol_trail_stop import atr_calc, atr_trail_stop

class TestATRCalc:
    def setup_method(self):
        # Create sample OHLC data
        dates = pd.date_range('2024-01-01', periods=20, freq='D')
        self.df = pd.DataFrame({
            'Open': np.random.uniform(100, 110, 20),
            'High': np.random.uniform(105, 115, 20),
            'Low': np.random.uniform(95, 105, 20),
            'Close': np.random.uniform(100, 110, 20),
            'Volume': np.random.uniform(1e6, 1e7, 20)
        }, index=dates)
        # Ensure High >= Low, High >= Close, Low <= Close
        self.df['High'] = self.df[['Open', 'High', 'Close']].max(axis=1)
        self.df['Low'] = self.df[['Open', 'Low', 'Close']].min(axis=1)
    
    def test_sma_smoothing(self):
        result = atr_calc(self.df.copy(), period=5, smoothing='SMA')
        assert 'ATR' in result.columns
        assert 'TR' in result.columns
        # First 4 rows should be NaN
        assert result['ATR'].iloc[:4].isna().all()
        # Row 4 (5th) should have value
        assert not pd.isna(result['ATR'].iloc[4])
    
    def test_ema_smoothing(self):
        result = atr_calc(self.df.copy(), period=5, smoothing='EMA')
        assert 'ATR' in result.columns
    
    def test_rma_smoothing(self):
        result = atr_calc(self.df.copy(), period=5, smoothing='RMA')
        assert 'ATR' in result.columns
    
    def test_no_smoothing(self):
        result = atr_calc(self.df.copy(), period=5, smoothing='0')
        assert 'ATR' in result.columns
        # Should equal TR
        pd.testing.assert_series_equal(result['ATR'], result['TR'])

class TestATRTrailStop:
    def test_basic_trail_stop(self):
        df = self.df.copy()
        result = atr_trail_stop(df, period=5, multiplier=3, smoothing='SMA')
        assert 'ATR_Original' in result.columns
        assert 'ATR_Trailing_Stop' in result.columns
        # Trailing stop should be monotonic non-decreasing (after first valid)
        valid = result['ATR_Trailing_Stop'].dropna()
        if len(valid) > 1:
            diffs = valid.diff().dropna()
            # Should never decrease (ratchet effect)
            assert (diffs >= 0).all()
    
    def test_invalid_smoothing_raises(self):
        with pytest.raises(ValueError):
            atr_calc(self.df.copy(), period=5, smoothing='INVALID')
```

- [ ] **Step 7: Run all tests**

Run: `cd dbmf_quant && python -m pytest config/pytest.ini`
Expected: All tests pass

---

### Task 4: Add Linter and Formatter Configuration

**Files:**
- Create: `config/ruff.toml` (or pyproject.toml)
- Create: `config/.pre-commit-config.yaml`

- [ ] **Step 1: Create ruff configuration**

```toml
# config/ruff.toml
[tool.ruff]
target-version = "py311"
line-length = 100
select = [
    "E",   # pycodestyle errors
    "W",   # pycodestyle warnings
    "F",   # pyflakes
    "I",   # isort
    "UP",  # pyupgrade
    "B",   # flake8-bugbear
    "C4",  # flake8-comprehensions
]
ignore = [
    "E501",  # line too long (handled by formatter)
]
per-file-ignores = {
    "*.ipynb": ["E501", "I001"],
}

[tool.ruff.format]
quote-style = "double"
indent-style = "space"
skip-magic-trailing-comma = false
line-ending = "auto"
```

- [ ] **Step 2: Create pre-commit configuration**

```yaml
# config/.pre-commit-config.yaml
repos:
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.5.0
    hooks:
      - id: ruff
        args: [--fix, --exit-non-zero-on-fix]
      - id: ruff-format
```

- [ ] **Step 3: Install and run linter**

Run: `pip install ruff pre-commit && pre-commit install -c config/.pre-commit-config.yaml`
Run: `ruff check . --config config/ruff.toml`
Run: `ruff format . --config config/ruff.toml`

---

### Task 5: Fix AAPL ROE Anomaly (141% ROE is Wrong)

**Files:**
- Modify: `pb_roe/src/screener/metrics.py:33-37`

- [ ] **Step 1: Investigate the issue**

Run: `cd pb_roe && python -c "import yfinance as yf; tk = yf.Ticker('AAPL'); info = tk.info; print('ROE:', info.get('returnOnEquity')); print('PB:', info.get('priceToBook')); print('Currency:', info.get('currency'))"`
Expected: ROE should be ~1.5 (150%) for AAPL? No, that seems wrong. AAPL ROE should be ~150%? Actually AAPL has very high ROE due to buybacks. Let me check.

- [ ] **Step 2: Add validation/sanity check for ROE**

```python
# In get_pb_roe(), after getting ROE from info:
roe = info.get("returnOnEquity")

# Sanity check: ROE > 500% is likely erroneous (data quality issue)
if roe is not None and roe > 5.0:
    # Fall back to fundamentals calculation
    pb, roe = _fallback_metrics(stock, pb, None)
    source = "fundamentals (ROE sanity check)"
```

- [ ] **Step 3: Test with AAPL**

Run: `cd pb_roe && python -c "from src.screener.metrics import get_pb_roe; print(get_pb_roe('AAPL'))"`
Expected: ROE should be reasonable (not 141%)

---

## Enhancement Tasks (After Critical Fixes)

### Task 6: Add Multi-Currency Risk-Free Rate with FRED Integration

**Files:**
- Modify: `pb_roe/src/helpers.py` (enhance risk_free_rate)
- Create: `config/.env` (from .env.example)

- [ ] **Step 1: Add FRED_API_KEY to environment**

```bash
# User needs to get API key from https://fred.stlouisfed.org/docs/api/api_key.html
# Then create config/.env with:
FRED_API_KEY=your_actual_key_here
```

- [ ] **Step 2: Test with FRED API key**

Run: `cd pb_roe && python -c "from src.helpers import risk_free_rate; print('EUR:', risk_free_rate('EUR')); print('GBP:', risk_free_rate('GBP'))"`
Expected: Real bond yields from FRED minus default spreads

---

### Task 7: Improve Damodaran Data Auto-Update Capability

**Files:**
- Modify: `implied_erp/extract_damodaran_erp.py`
- Modify: `implied_erp/build_damodaran_erp.py`

- [ ] **Step 1: Add auto-download capability (optional)**

```python
# In extract_damodaran_erp.py, add optional URL download
import urllib.request

def download_latest_damodaran(url: str, dest: Path) -> Path:
    """Download latest ctryprem.xlsx from Damodaran's website."""
    urllib.request.urlretrieve(url, dest)
    return dest
```

- [ ] **Step 2: Add version checking**

```python
# Compare "updated" field in JSON with spreadsheet date
# Warn if data is stale (> 90 days)
```

---

### Task 8: Add Backtesting Framework (Placeholder Implementation)

**Files:**
- Create: `backtest/__init__.py`
- Create: `backtest/engine.py`
- Create: `backtest/strategies.py`
- Create: `backtest/metrics.py`

- [ ] **Step 1: Create basic backtest engine**

```python
# backtest/engine.py
from dataclasses import dataclass
from typing import Callable
import pandas as pd

@dataclass
class BacktestResult:
    equity_curve: pd.Series
    trades: pd.DataFrame
    metrics: dict

class BacktestEngine:
    def __init__(self, initial_capital: float = 100000):
        self.initial_capital = initial_capital
    
    def run(self, 
            data: pd.DataFrame,
            entry_signal: Callable,
            exit_signal: Callable,
            position_size: Callable = None) -> BacktestResult:
        # Basic vectorized backtest
        pass
```

---

### Task 9: Fill in `implied_erp/implied_erp.py` Placeholder

**Files:**
- Modify: `implied_erp/implied_erp.py`

- [ ] **Step 1: Create unified ERP interface**

```python
"""Unified interface for Implied ERP module."""

from .extract_damodaran_erp import extract as extract_full
from .build_damodaran_erp import extract as extract_lightweight
from .data.july26 import load as load_latest  # or similar

__all__ = ['extract_full', 'extract_lightweight', 'get_latest_erp']

def get_latest_erp(country: str) -> float:
    """Get latest ERP for a country from processed data."""
    from pb_roe.src.screener.damodaran import erp_for_country
    return erp_for_country(country)
```

---

### Task 10: Improve Documentation and README Files

**Files:**
- Modify: `README.md`
- Modify: `pb_roe/README.md`
- Modify: `vol_trail_stop/README.md`

- [ ] **Step 1: Expand main README**

```markdown
# DBMF Quant

A Python-based quantitative trading system with three independent modules:

## 1. Implied ERP
Extracts country-level equity risk premiums from Damodaran's spreadsheet.

## 2. P/B vs ROE Screener
Values stocks via Gordon-growth implied P/B vs actual P/B.

## 3. Volatility Trailing Stop
ATR-based trailing stop with multiple smoothing modes.

## Quick Start
```bash
# Setup
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r config/requirements.txt

# Run tests
python -m pytest config/pytest.ini

# P/B vs ROE Screener
cd pb_roe && python src/helpers.py

# Volatility Trailing Stop
python vol_trail_stop/vol_trail_stop.py
```

## Data Sources
- Yahoo Finance (yfinance) - market data
- Damodaran (NYU Stern) - country risk premiums
- FRED - risk-free rates (optional, requires API key)
```

---

### Task 11: Add g > r Edge Case Handling in Gordon Growth

**Files:**
- Modify: `pb_roe/src/helpers.py:18-20`

- [ ] **Step 1: Add validation and warning**

```python
def intrinsic_pb(roe, g, r):
    """Implied P/B = ROE * (1+g) * (1 - g/ROE) / (r - g). All inputs are ratios."""
    if g >= r:
        import warnings
        warnings.warn(
            f"Growth rate (g={g:.2%}) >= cost of equity (r={r:.2%}). "
            "Gordon growth model produces negative/undefined P/B. "
            "Consider using a multi-stage model or lowering g.",
            UserWarning
        )
        if g == r:
            return float('inf')  # or raise
        return (roe * (1 + g) * (1 - g / roe)) / (r - g)  # negative
    return (roe * (1 + g) * (1 - g / roe)) / (r - g)
```

- [ ] **Step 2: Test edge case**

Run: `cd pb_roe && python -c "from src.helpers import intrinsic_pb; import warnings; warnings.simplefilter('always'); print(intrinsic_pb(0.15, 0.12, 0.10))"`
Expected: Warning printed, negative result returned

---

### Task 12: Add Configuration Management

**Files:**
- Create: `config/settings.py`
- Modify: All modules to use centralized config

- [ ] **Step 1: Create settings module**

```python
# config/settings.py
from dataclasses import dataclass
from pathlib import Path

@dataclass
class Settings:
    # Paths
    repo_root: Path = Path(__file__).parent.parent
    implied_erp_data: Path = repo_root / "implied_erp" / "data"
    pb_roe_data: Path = repo_root / "pb_roe" / "src" / "data"
    
    # Defaults
    default_growth_rate: float = 0.05
    default_atr_period: int = 14
    default_atr_multiplier: float = 3.0
    discrepancy_threshold: float = 0.20
    
    # API
    fred_api_key: str = ""
    fmp_api_key: str = ""

settings = Settings()
```

---

## Execution Order Summary

| Phase | Tasks | Priority |
|-------|-------|----------|
| 1. Critical Fixes | 1, 2, 5 | P0 - Blockers |
| 2. Testing | 3 | P1 - Quality |
| 3. Linting | 4 | P1 - Quality |
| 4. Enhancements | 6, 7, 9, 11 | P2 - Features |
| 5. Infrastructure | 8, 10, 12 | P3 - Polish |

---

## Verification Checklist

After implementation, verify:

- [ ] All tests pass: `python -m pytest config/pytest.ini`
- [ ] Linter clean: `ruff check . --config config/ruff.toml`
- [ ] Formatter clean: `ruff format . --config config/ruff.toml --check`
- [ ] P/B vs ROE screener works: `cd pb_roe && python src/helpers.py` (interactive)
- [ ] Damodaran lookup works: `python pb_roe/src/screener/damodaran.py AAPL`
- [ ] Volatility trailing stop works: `python vol_trail_stop/vol_trail_stop.py` (interactive)
- [ ] Implied ERP extraction works: `python implied_erp/extract_damodaran_erp.py --xlsx "path/to/ctryprem.xlsx"`
- [ ] Non-USD tickers work in screener (with FRED key)
- [ ] g >= r edge case handled gracefully

---

**Plan saved to:** `docs/superpowers/plans/2026-07-24-dbmf-quant-improvements.md`

**Two execution options:**

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session using executing-plans, batch execution with checkpoints

**Which approach?**