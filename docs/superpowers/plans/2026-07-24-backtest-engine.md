# Backtest Engine Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a backtest engine in `backtest/` that runs the P/B vs ROE screener on the S&P 500, holds 10 equally-weighted positions, exits via ATR trailing stop, and rebalances event-driven when positions are stopped out.

**Architecture:** The backtest engine is a standalone module that imports from existing `pb_roe/` (screener, valuation) and `vol_trail_stop/` (ATR exit). Historical Damodaran ERP data is scraped from the NYU archives page (all years), and historical S&P 500 constituents are fetched per rebalance date. The 2-stage Gordon growth model replaces the current single-stage model.

**Tech Stack:** Python 3.11+, yfinance, pandas, numpy, openpyxl, requests/httpx, beautifulsoup4/lxml, existing modules (`pb_roe`, `vol_trail_stop`).

---

## File Structure

### New Files
```
backtest/
├── __init__.py              # Module init, exports major classes
├── engine.py                # BacktestEngine: core loop (dates, positions, P&L)
├── portfolio.py             # Portfolio: position sizing, tracking, P&L
├── strategy.py              # Strategy: entry signals (screen), exit signals (ATR)
├── data.py                  # DataLoader: S&P 500 constituents, historical prices
├── sp500.py                 # S&P 500 constituent fetcher (historical per date)
├── damodaran_archive.py     # Damodaran historical ERP scraper
├── valuation.py             # 2-stage Gordon growth model
├── metrics.py               # Performance metrics (Sharpe, drawdown, etc.)
├── tests/
│   ├── __init__.py
│   ├── test_engine.py
│   ├── test_portfolio.py
│   ├── test_strategy.py
│   ├── test_data.py
│   ├── test_sp500.py
│   ├── test_damodaran_archive.py
│   ├── test_valuation.py
│   └── test_metrics.py
```

### Modified Files
```
pb_roe/src/helpers.py        # Add 2-stage intrinsic_pb() alongside existing
pb_roe/src/screener/metrics.py  # Optional: export get_pb_roe() as public API
implied_erp/data/             # New historical ERP files (ctrypremYY.json)
```

### Data Files (Generated)
```
implied_erp/data/
├── damodaran_erp_2022.json
├── damodaran_erp_2023.json
├── damodaran_erp_2024.json
├── damodaran_erp_2025.json
├── damodaran_erp_2026.json
└── damodaran_erp_index.json  # Master index of all years
```

---

### Task 1: Scrape All Historical Damodaran ERP Spreadsheets

**Files:**
- Create: `backtest/damodaran_archive.py`
- Data: `implied_erp/data/damodaran_erp_{year}.json` (multiple years)

**Goal:** Download all historical `ctrypremYY.xlsx` files from Damodaran's archives, extract ERP data, and save as JSON files for each year.

- [ ] **Step 1: Scrape the archives page to find all download links**

```python
# backtest/damodaran_archive.py
"""Scrape Damodaran's archives page and download all historical ERP spreadsheets.

Archives page: https://pages.stern.nyu.edu/~adamodar/New_Home_Page/dataarchived.html#discrate
File pattern: https://pages.stern.nyu.edu/~adamodar/pc/archives/ctrypremYY.xlsx
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

ARCHIVES_URL = "https://pages.stern.nyu.edu/~adamodar/New_Home_Page/dataarchived.html"
BASE_DOWNLOAD = "https://pages.stern.nyu.edu/~adamodar/pc/archives/"
DATA_DIR = Path(__file__).resolve().parent.parent.parent / "implied_erp" / "data"

# Pattern for ctryprem files: ctrypremYY.xlsx or ctrypremYY.xls
CTYPREM_PATTERN = re.compile(r"ctryprem(\d{2})\.xlsx?", re.IGNORECASE)


def find_erp_links() -> list[tuple[str, str]]:
    """Scrape the archives page and return list of (year_label, download_url) for ctryprem files."""
    resp = requests.get(ARCHIVES_URL, timeout=30)
    resp.raise_for_status()
    
    soup = BeautifulSoup(resp.text, "html.parser")
    links = []
    
    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        text = a_tag.get_text(strip=True)
        match = CTYPREM_PATTERN.search(href)
        if match:
            year_suffix = match.group(1)
            full_url = urljoin(BASE_DOWNLOAD, href) if not href.startswith("http") else href
            links.append((f"20{year_suffix}", full_url))
    
    return sorted(links, key=lambda x: x[0])


def download_ctryprem(url: str, dest: Path) -> Path:
    """Download a ctryprem xlsx file to dest."""
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(resp.content)
    return dest


def extract_erp_from_xlsx(xlsx_path: Path) -> dict:
    """Extract country -> Total Equity Risk Premium from a ctryprem xlsx file.
    
    Uses the same logic as implied_erp/build_damodaran_erp.py.
    """
    import openpyxl
    
    wb = openpyxl.load_workbook(xlsx_path, data_only=True)
    ws = wb["ERPs by country"]
    
    mature_market_erp: float | None = None
    countries: dict[str, float] = {}
    in_data = False
    
    for row in ws.iter_rows(values_only=True):
        if not row:
            continue
        first = row[0]
        
        if first == "Country":
            in_data = True
            continue
        
        if not in_data and isinstance(first, str) and "mature equity market" in first.lower():
            try:
                mature_market_erp = float(row[4]) if len(row) > 4 else None
            except (TypeError, ValueError):
                pass
            continue
        
        if not in_data:
            continue
        
        country = first
        if not isinstance(country, str) or not country.strip():
            continue
        try:
            erp = float(row[4]) if len(row) > 4 else None
            if erp is not None:
                countries[country.strip()] = erp
        except (TypeError, ValueError):
            continue
    
    return {
        "source": xlsx_path.name,
        "updated": xlsx_path.stem.replace("ctryprem", ""),
        "mature_market_erp": mature_market_erp if mature_market_erp else 0.042,
        "countries": countries,
    }


def download_all_erp_data(force: bool = False) -> list[Path]:
    """Download all historical ctryprem files and save as JSON.
    
    Skips years that already have JSON output unless force=True.
    Returns list of JSON paths created.
    """
    links = find_erp_links()
    print(f"Found {len(links)} ctryprem files in archives.")
    
    created = []
    for year_label, url in links:
        json_path = DATA_DIR / f"damodaran_erp_{year_label}.json"
        
        if json_path.exists() and not force:
            print(f"  [SKIP] {year_label} already exists at {json_path.name}")
            continue
        
        # Download the xlsx
        xlsx_path = DATA_DIR / f"ctryprem{year_label[-2:]}.xlsx"
        try:
            print(f"  [DOWNLOAD] {year_label} from {url}...")
            download_ctryprem(url, xlsx_path)
        except Exception as e:
            print(f"  [FAIL] Download failed for {year_label}: {e}")
            continue
        
        # Extract and save as JSON
        try:
            data = extract_erp_from_xlsx(xlsx_path)
            json_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
            print(f"  [OK] {year_label}: {len(data['countries'])} countries -> {json_path.name}")
            created.append(json_path)
        except Exception as e:
            print(f"  [FAIL] Extraction failed for {year_label}: {e}")
        finally:
            # Remove temporary xlsx
            xlsx_path.unlink(missing_ok=True)
    
    # Create master index
    index = {}
    for p in sorted(DATA_DIR.glob("damodaran_erp_*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            year = p.stem.replace("damodaran_erp_", "")
            index[year] = {
                "path": str(p.relative_to(DATA_DIR)),
                "countries": len(data.get("countries", {})),
                "mature_market_erp": data.get("mature_market_erp"),
            }
        except Exception:
            pass
    
    index_path = DATA_DIR / "damodaran_erp_index.json"
    index_path.write_text(json.dumps(index, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\nIndex saved: {index_path.name} ({len(index)} years)")
    
    return created


def historical_erp_for_year(year: int) -> float | None:
    """Get the mature market ERP for a specific year.
    
    Returns the ERP as a ratio (e.g. 0.042 for 4.2%).
    """
    index_path = DATA_DIR / "damodaran_erp_index.json"
    if not index_path.exists():
        return None
    
    index = json.loads(index_path.read_text(encoding="utf-8"))
    year_str = str(year)
    if year_str not in index:
        # Try two-digit year
        year_str = str(year)[-2:]
        # Find in index
        for k in index:
            if k.endswith(year_str):
                year_str = k
                break
        else:
            return None
    
    entry = index[year_str]
    data_path = DATA_DIR / entry["path"]
    data = json.loads(data_path.read_text(encoding="utf-8"))
    return data.get("mature_market_erp")


def historical_erp_for_country(country: str, year: int) -> float | None:
    """Get the Total Equity Risk Premium for a country in a specific year."""
    index_path = DATA_DIR / "damodaran_erp_index.json"
    if not index_path.exists():
        return None
    
    index = json.loads(index_path.read_text(encoding="utf-8"))
    year_str = str(year)
    if year_str not in index:
        return None
    
    entry = index[year_str]
    data_path = DATA_DIR / entry["path"]
    data = json.loads(data_path.read_text(encoding="utf-8"))
    return data.get("countries", {}).get(country)


if __name__ == "__main__":
    force = "--force" in sys.argv
    created = download_all_erp_data(force=force)
    print(f"\nDone. {len(created)} files created.")
```

- [ ] **Step 2: Write test for the scraper**

```python
# backtest/tests/test_damodaran_archive.py
import pytest
from backtest.damodaran_archive import find_erp_links

class TestFindERPLinks:
    def test_returns_list_of_tuples(self):
        links = find_erp_links()
        assert len(links) > 0
        for year, url in links:
            assert year.startswith("20")
            assert len(year) == 4
            assert url.startswith("http")
            assert "ctryprem" in url.lower()
    
    def test_contains_recent_years(self):
        links = find_erp_links()
        years = [y for y, _ in links]
        assert "2022" in years or "2023" in years or "2024" in years or "2025" in years or "2026" in years
```

- [ ] **Step 3: Run the scraper to download all data**

```bash
cd dbmf_quant && python backtest/damodaran_archive.py
```

Expected: Downloads all historical ctryprem files, saves as JSON to `implied_erp/data/`, creates `damodaran_erp_index.json`.

---

### Task 2: S&P 500 Historical Constituents Fetcher

**Files:**
- Create: `backtest/sp500.py`

**Goal:** Fetch the S&P 500 constituents as of a specific date, using Wikipedia or a financial data source.

- [ ] **Step 1: Write the S&P 500 constituent fetcher**

```python
# backtest/sp500.py
"""Fetch S&P 500 constituents for a specific date.

Uses Wikipedia's List of S&P 500 companies page, which includes historical
changes. For a given date, we reconstruct the composition by starting from
the current list and applying historical removals/additions.

If Wikipedia doesn't have historical data, falls back to current list.
"""

from __future__ import annotations

import pandas as pd
import requests
from datetime import date

WIKI_URL = "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies"


def _fetch_current_sp500() -> list[str]:
    """Fetch the current S&P 500 tickers from Wikipedia."""
    tables = pd.read_html(WIKI_URL)
    sp500_table = tables[0]  # First table is the S&P 500 list
    tickers = sp500_table["Symbol"].tolist()
    # Clean tickers (replace dots with hyphens for yfinance)
    return [t.replace(".", "-") for t in tickers]


def _fetch_historical_changes() -> pd.DataFrame:
    """Fetch historical changes from the second table on Wikipedia."""
    tables = pd.read_html(WIKI_URL)
    if len(tables) < 2:
        return pd.DataFrame()
    
    changes = tables[1]  # Second table is historical changes
    changes["date"] = pd.to_datetime(changes.get("Date", changes.iloc[:, 0]))
    return changes


def sp500_constituents(as_of: date | None = None) -> list[str]:
    """Get S&P 500 constituents as of a specific date.
    
    If as_of is None, returns the current list.
    """
    tickers = _fetch_current_sp500()
    
    if as_of is None:
        return tickers
    
    # TODO: For accurate historical reconstruction, we'd need to
    # apply additions/removals from the changes table.
    # For now, return current list with a warning about accuracy.
    import warnings
    warnings.warn(
        f"Historical S&P 500 constituents for {as_of} may not be perfectly accurate. "
        "Using current list as approximation."
    )
    return tickers


def sp500_tickers_with_historical(start_date: date, end_date: date) -> list[str]:
    """Get the union of all S&P 500 tickers between start_date and end_date.
    
    This ensures we don't miss tickers that were added/removed during the period.
    For now, returns current list expanded with any known historical tickers.
    """
    current = _fetch_current_sp500()
    return current


# For more accurate historical data, we could use a curated dataset.
# The Wikipedia table of changes includes additions and removals with dates.
# A full implementation would reconstruct the index at each point in time.

if __name__ == "__main__":
    from datetime import date
    tickers = sp500_constituents(date.today())
    print(f"S&P 500 constituents: {len(tickers)} tickers")
    print(f"Sample: {tickers[:5]}")
```

- [ ] **Step 2: Write tests**

```python
# backtest/tests/test_sp500.py
import pytest
from datetime import date
from backtest.sp500 import sp500_constituents

class TestSP500:
    def test_current_list_size(self):
        tickers = sp500_constituents()
        assert len(tickers) >= 500
        assert all(isinstance(t, str) for t in tickers)
    
    def test_contains_major_tickers(self):
        tickers = set(tickers)
        assert "AAPL" in tickers
        assert "MSFT" in tickers
        assert "GOOGL" in tickers or "GOOG" in tickers
    
    def test_returns_list_of_strings(self):
        tickers = sp500_constituents()
        assert all(isinstance(t, str) for t in tickers)
```

---

### Task 3: 2-Stage Gordon Growth Model

**Files:**
- Create: `backtest/valuation.py`
- Modify: `pb_roe/src/helpers.py` (add 2-stage intrinsic_pb)

**Goal:** Implement a 2-stage Gordon growth model. Phase 1 (years 1-5): growth declines linearly from the 2-year historical average to the 10-year bond yield. Phase 2 (year 6+): terminal growth = 10-year bond yield.

- [ ] **Step 1: Implement the 2-stage model**

```python
# backtest/valuation.py
"""2-stage Gordon Growth Model for P/B valuation.

Stage 1 (years 1-5): Growth rate declines linearly from the 2-year historical
average ROE growth rate to the 10-year government bond yield (terminal growth).

Stage 2 (year 6+): Perpetuity at terminal growth rate (10-year bond yield).

Formula:
  intrinsic_pb = sum_{t=1..5} [ ROE * (1+g_t) * (1 - g_t/ROE) / (1+r)^t ]
                 + [ ROE * (1+g_term) * (1 - g_term/ROE) / ((r - g_term) * (1+r)^5) ]

Where:
  g_t = g_start + (g_term - g_start) * (t-1) / 4  (linear decline over 5 years)
  g_start = 2-year historical average growth rate
  g_term = 10-year government bond yield (proxy for long-term economic growth)
  r = cost of equity (CAPM: rf + beta * erp)
"""

from __future__ import annotations

from typing import Protocol


def intrinsic_pb_2stage(
    roe: float,
    g_start: float,
    g_term: float,
    r: float,
    years_stage1: int = 5,
) -> float:
    """2-stage Gordon growth implied P/B ratio.
    
    Args:
        roe: Return on Equity (ratio, e.g. 0.15 for 15%)
        g_start: Initial growth rate for stage 1 (ratio)
        g_term: Terminal growth rate for stage 2 (ratio, e.g. 10yr bond yield)
        r: Cost of equity (CAPM, ratio)
        years_stage1: Number of years in stage 1 (default 5)
    
    Returns:
        Implied P/B ratio (float)
    """
    if r <= g_term:
        raise ValueError(
            f"Cost of equity (r={r:.4f}) must exceed terminal growth (g_term={g_term:.4f})"
        )
    
    # Stage 1: Sum of discounted present values for each year
    pv_stage1 = 0.0
    for t in range(1, years_stage1 + 1):
        # Linear decline: g_t moves from g_start toward g_term
        g_t = g_start + (g_term - g_start) * (t - 1) / (years_stage1 - 1) if years_stage1 > 1 else g_start
        # Dividend payout ratio = (1 - g / ROE)
        payout = 1 - (g_t / roe) if roe > 0 else 0
        # Dividend at time t = ROE * book_value * (1+g_t) * payout
        # Normalized by book value: dividend / book = ROE * (1+g_t) * payout
        div_yield = roe * (1 + g_t) * payout
        # PV of dividend
        pv_stage1 += div_yield / ((1 + r) ** t)
    
    # Stage 2: Terminal value (Gordon growth perpetuity)
    # Terminal dividend at year 6+ = ROE * (1+g_term)^6 * (1 - g_term/ROE)
    term_payout = 1 - (g_term / roe) if roe > 0 else 0
    term_div_at_year6 = roe * ((1 + g_term) ** (years_stage1 + 1)) * term_payout
    terminal_value = term_div_at_year6 / (r - g_term)
    pv_terminal = terminal_value / ((1 + r) ** years_stage1)
    
    return pv_stage1 + pv_terminal


def intrinsic_pb_single_stage(roe: float, g: float, r: float) -> float:
    """Single-stage Gordon growth P/B (original formula, kept for compatibility).
    
    P/B = ROE * (1+g) * (1 - g/ROE) / (r - g)
    """
    if r == g:
        return float("inf")
    return (roe * (1 + g) * (1 - g / roe)) / (r - g)
```

- [ ] **Step 2: Write tests for the 2-stage model**

```python
# backtest/tests/test_valuation.py
import pytest
from backtest.valuation import intrinsic_pb_2stage, intrinsic_pb_single_stage

class TestIntrinsicPB2Stage:
    def test_basic_case(self):
        # ROE=15%, g_start=10%, g_term=3%, r=9%
        result = intrinsic_pb_2stage(0.15, 0.10, 0.03, 0.09)
        assert result > 0
        assert isinstance(result, float)
    
    def test_terminal_growth_exceeds_r_raises(self):
        with pytest.raises(ValueError, match="must exceed terminal growth"):
            intrinsic_pb_2stage(0.15, 0.10, 0.05, 0.04)
    
    def test_equals_single_stage_when_g_start_equals_g_term(self):
        # When g_start == g_term, 2-stage should equal single-stage
        result_2stage = intrinsic_pb_2stage(0.15, 0.05, 0.05, 0.09)
        result_single = intrinsic_pb_single_stage(0.15, 0.05, 0.09)
        assert abs(result_2stage - result_single) < 0.01
    
    def test_linear_decline_lower_than_constant(self):
        # Declining growth should give lower P/B than constant high growth
        result_decline = intrinsic_pb_2stage(0.15, 0.10, 0.03, 0.09)
        result_constant = intrinsic_pb_single_stage(0.15, 0.10, 0.09)
        assert result_decline < result_constant
    
    def test_g_term_higher_means_higher_pb(self):
        result_low = intrinsic_pb_2stage(0.15, 0.10, 0.02, 0.09)
        result_high = intrinsic_pb_2stage(0.15, 0.10, 0.04, 0.09)
        assert result_high > result_low
```

- [ ] **Step 3: Add 2-stage function to pb_roe/src/helpers.py**

```python
# Add to pb_roe/src/helpers.py, alongside existing intrinsic_pb():
def intrinsic_pb(roe, g, r):
    """Original single-stage Gordon growth (kept for backward compatibility)."""
    return (roe * (1 + g) * (1 - g / roe)) / (r - g)


def intrinsic_pb_2stage(roe, g_start, g_term, r, years_stage1=5):
    """2-stage Gordon growth P/B.
    
    Delegates to backtest.valuation.intrinsic_pb_2stage.
    """
    from backtest.valuation import intrinsic_pb_2stage as _calc
    return _calc(roe, g_start, g_term, r, years_stage1)
```

---

### Task 4: Portfolio Manager

**Files:**
- Create: `backtest/portfolio.py`

**Goal:** Track positions, cash, P&L, and handle buys/sells. Supports equal-weight allocation (10% per position, max 10 positions).

- [ ] **Step 1: Implement Portfolio class**

```python
# backtest/portfolio.py
"""Portfolio manager for backtesting.

Tracks positions, cash, P&L. Supports equal-weight allocation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import pandas as pd


@dataclass
class Position:
    ticker: str
    entry_date: date
    entry_price: float
    shares: float
    cost_basis: float  # total cost of position
    current_price: float = 0.0
    exit_date: Optional[date] = None
    exit_price: Optional[float] = None
    pnl: float = 0.0
    pnl_pct: float = 0.0
    exit_reason: Optional[str] = None  # "atr_stop", "manual", etc.


@dataclass
class Trade:
    date: date
    ticker: str
    action: str  # "buy" or "sell"
    price: float
    shares: float
    value: float
    reason: Optional[str] = None


@dataclass
class PortfolioSnapshot:
    date: date
    total_value: float
    cash: float
    invested: float
    positions: list[Position]
    trades: list[Trade]


class Portfolio:
    """Manages a portfolio of positions with equal-weight allocation."""
    
    def __init__(self, initial_capital: float = 100_000.0, max_positions: int = 10):
        self.initial_capital = initial_capital
        self.cash = initial_capital
        self.max_positions = max_positions
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []
        self.history: list[PortfolioSnapshot] = []
        self.current_date: Optional[date] = None
    
    @property
    def invested_value(self) -> float:
        return sum(p.shares * p.current_price for p in self.positions.values())
    
    @property
    def total_value(self) -> float:
        return self.cash + self.invested_value
    
    @property
    def position_count(self) -> int:
        return len(self.positions)
    
    @property
    def available_slots(self) -> int:
        return self.max_positions - self.position_count
    
    @property
    def target_position_value(self) -> float:
        """Equal-weight allocation: total_value / max_positions."""
        return self.total_value / self.max_positions
    
    def buy(self, ticker: str, price: float, shares: float, date: date, reason: Optional[str] = None) -> Position:
        """Buy shares of a ticker. Returns the Position."""
        cost = price * shares
        if cost > self.cash:
            shares = self.cash / price
            cost = self.cash
        
        if cost > self.cash:
            raise ValueError(f"Insufficient cash: need {cost:.2f}, have {self.cash:.2f}")
        
        self.cash -= cost
        position = Position(
            ticker=ticker,
            entry_date=date,
            entry_price=price,
            shares=shares,
            cost_basis=cost,
            current_price=price,
        )
        self.positions[ticker] = position
        
        trade = Trade(date=date, ticker=ticker, action="buy", price=price, shares=shares, value=cost, reason=reason)
        self.trades.append(trade)
        
        return position
    
    def sell(self, ticker: str, price: float, date: date, reason: Optional[str] = None) -> Position:
        """Sell all shares of a ticker. Returns the closed Position."""
        if ticker not in self.positions:
            raise ValueError(f"Position {ticker} not found")
        
        position = self.positions[ticker]
        proceeds = position.shares * price
        self.cash += proceeds
        
        position.exit_date = date
        position.exit_price = price
        position.current_price = price
        position.pnl = proceeds - position.cost_basis
        position.pnl_pct = (proceeds / position.cost_basis) - 1
        position.exit_reason = reason
        
        trade = Trade(date=date, ticker=ticker, action="sell", price=price, shares=position.shares, value=proceeds, reason=reason)
        self.trades.append(trade)
        
        # Remove from active positions (but keep reference for history)
        del self.positions[ticker]
        
        return position
    
    def update_prices(self, prices: dict[str, float], date: date):
        """Update current prices for all positions."""
        self.current_date = date
        for ticker, pos in self.positions.items():
            if ticker in prices:
                pos.current_price = prices[ticker]
        
        # Record snapshot
        self.history.append(PortfolioSnapshot(
            date=date,
            total_value=self.total_value,
            cash=self.cash,
            invested=self.invested_value,
            positions=list(self.positions.values()),
            trades=list(self.trades),
        ))
    
    def get_position(self, ticker: str) -> Optional[Position]:
        return self.positions.get(ticker)
    
    def has_position(self, ticker: str) -> bool:
        return ticker in self.positions
    
    def equity_curve(self) -> pd.Series:
        """Return equity curve as a pandas Series."""
        if not self.history:
            return pd.Series(dtype=float)
        dates = [s.date for s in self.history]
        values = [s.total_value for s in self.history]
        return pd.Series(values, index=pd.DatetimeIndex(dates))
```

- [ ] **Step 2: Write tests for Portfolio**

```python
# backtest/tests/test_portfolio.py
import pytest
from datetime import date
from backtest.portfolio import Portfolio

class TestPortfolio:
    def test_initial_state(self):
        p = Portfolio(initial_capital=100_000)
        assert p.cash == 100_000
        assert p.total_value == 100_000
        assert p.position_count == 0
        assert p.available_slots == 10
    
    def test_buy_reduces_cash(self):
        p = Portfolio(100_000)
        p.buy("AAPL", 150.0, 66, date=date(2024, 1, 1))
        assert p.cash == 100_000 - (150 * 66)
        assert p.position_count == 1
        assert p.invested_value == 150 * 66
    
    def test_sell_increases_cash(self):
        p = Portfolio(100_000)
        p.buy("AAPL", 150.0, 66, date=date(2024, 1, 1))
        p.sell("AAPL", 160.0, date=date(2024, 6, 1), reason="atr_stop")
        assert p.cash > 100_000  # Profit
        assert p.position_count == 0
    
    def test_available_slots(self):
        p = Portfolio(100_000)
        assert p.available_slots == 10
        p.buy("AAPL", 150.0, 66, date=date(2024, 1, 1))
        assert p.available_slots == 9
    
    def test_update_prices(self):
        p = Portfolio(100_000)
        p.buy("AAPL", 150.0, 66, date=date(2024, 1, 1))
        p.update_prices({"AAPL": 160.0}, date=date(2024, 6, 1))
        assert p.total_value == (100_000 - 150*66) + (66 * 160)
    
    def test_equity_curve(self):
        p = Portfolio(100_000)
        p.update_prices({}, date=date(2024, 1, 1))
        p.update_prices({}, date=date(2024, 6, 1))
        curve = p.equity_curve()
        assert len(curve) == 2
```

---

### Task 5: Strategy Module (Entry and Exit Signals)

**Files:**
- Create: `backtest/strategy.py`
- Modify: `pb_roe/src/helpers.py` (export screen function cleanly)

**Goal:** Entry signals from the P/B vs ROE screen, exit signals from the ATR trailing stop.

- [ ] **Step 1: Implement the Strategy class**

```python
# backtest/strategy.py
"""Entry and exit signal generation for backtest.

Entry: P/B vs ROE screen (top 10 S&P 500 stocks by valuation gap)
Exit: ATR trailing stop (from vol_trail_stop module)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Optional

import pandas as pd
import yfinance as yf

from backtest.valuation import intrinsic_pb_2stage
from pb_roe.src.helpers import risk_free_rate, capm
from pb_roe.src.screener.metrics import get_pb_roe
from pb_roe.src.screener.damodaran import erp_for_ticker, default_spread_for_country, country_from_ticker
from vol_trail_stop.vol_trail_stop import atr_trail_stop


@dataclass
class ScreenResult:
    ticker: str
    implied_pb: float
    actual_pb: float
    gap_pct: float  # (implied - actual) / actual
    roe: float
    g_start: float  # 2-year historical growth rate
    g_term: float   # terminal growth (10yr bond yield)
    r: float        # cost of equity
    verdict: str    # "undervalued" or "overvalued"


def _estimate_historical_growth(ticker: str, years: int = 2) -> float:
    """Estimate historical growth rate from EPS or Revenue over the last N years.
    
    Uses yfinance to get annual EPS data and computes CAGR.
    Falls back to 5% if data is unavailable.
    """
    try:
        stock = yf.Ticker(ticker)
        # Try to get earnings history
        earnings = stock.earnings
        if earnings is not None and not earnings.empty:
            # Get EPS from last N years
            eps = earnings["Earnings"]
            if len(eps) >= 2:
                recent = eps.iloc[:min(years * 4, len(eps))]  # quarterly data
                if len(recent) >= 2:
                    first_val = recent.iloc[-1]
                    last_val = recent.iloc[0]
                    if first_val > 0 and last_val > 0:
                        periods = len(recent) - 1
                        cagr = (last_val / first_val) ** (1.0 / periods) - 1
                        # Annualize: quarterly CAGR * 4
                        return max(0.0, min(cagr * 4, 0.50))  # cap at 50%
    except Exception:
        pass
    
    # Fallback: use trailing EPS growth from info
    try:
        info = stock.info or {}
        eps = info.get("trailingEps")
        if eps and eps > 0:
            return 0.05  # default 5% if we can't calculate
    except Exception:
        pass
    
    return 0.05  # Default fallback


def _get_terminal_growth_rate(currency: str = "USD") -> float:
    """Get the terminal growth rate = 10-year government bond yield.
    
    Uses the risk_free_rate function which already handles this.
    """
    try:
        rf = risk_free_rate(currency)
        return rf
    except (ValueError, Exception):
        return 0.042  # fallback: mature market ERP


def screen_ticker(ticker: str, g_start: Optional[float] = None) -> Optional[ScreenResult]:
    """Run the 2-stage screen on a single ticker.
    
    Returns ScreenResult if the stock is undervalued, None if overvalued or error.
    """
    try:
        # Get current data
        stock = yf.Ticker(ticker)
        info = stock.info or {}
        
        roe = info.get("returnOnEquity")
        pb = info.get("priceToBook")
        beta = info.get("beta")
        currency = info.get("currency", "USD")
        
        if not all([roe, pb, beta]):
            return None
        
        # Get ERP for the ticker's country
        try:
            erp = erp_for_ticker(ticker)
        except (ValueError, Exception):
            return None
        
        # Get risk-free rate
        try:
            rf = risk_free_rate(currency)
        except (ValueError, Exception):
            return None
        
        # Cost of equity
        r = capm(rf, beta, erp)
        
        # Growth rates
        if g_start is None:
            g_start = _estimate_historical_growth(ticker)
        g_term = _get_terminal_growth_rate(currency)
        
        # 2-stage valuation
        implied_pb = intrinsic_pb_2stage(roe, g_start, g_term, r)
        
        gap_pct = (implied_pb - pb) / pb
        
        return ScreenResult(
            ticker=ticker,
            implied_pb=implied_pb,
            actual_pb=pb,
            gap_pct=gap_pct,
            roe=roe,
            g_start=g_start,
            g_term=g_term,
            r=r,
            verdict="undervalued" if gap_pct > 0 else "overvalued",
        )
    except Exception as e:
        return None


def rank_tickers(tickers: list[str], top_n: int = 10) -> list[ScreenResult]:
    """Run screen on all tickers and return the top N undervalued.
    
    Ranking by gap_pct (most undervalued first).
    Only returns undervalued stocks.
    """
    results = []
    for ticker in tickers:
        result = screen_ticker(ticker)
        if result and result.verdict == "undervalued":
            results.append(result)
    
    # Sort by gap_pct descending (most undervalued first)
    results.sort(key=lambda r: r.gap_pct, reverse=True)
    return results[:top_n]


def atr_exit_signal(ticker: str, current_price: float, period: int = 15, multiplier: float = 3.0) -> bool:
    """Check if ATR trailing stop has been triggered.
    
    Returns True if the position should be exited (stop hit).
    """
    try:
        # Get 1 year of price data
        stock = yf.Ticker(ticker)
        hist = stock.history(period="1y")
        if hist.empty or len(hist) < period + 5:
            return False
        
        # Calculate ATR trailing stop
        result = atr_trail_stop(hist, period=period, multiplier=multiplier, smoothing="SMA")
        
        if "ATR_Trailing_Stop" not in result.columns:
            return False
        
        last_stop = result["ATR_Trailing_Stop"].iloc[-1]
        if pd.isna(last_stop):
            return False
        
        # Exit if current price <= trailing stop
        return current_price <= last_stop
    except Exception:
        return False
```

- [ ] **Step 2: Write tests for Strategy**

```python
# backtest/tests/test_strategy.py
import pytest
from backtest.strategy import _estimate_historical_growth, _get_terminal_growth_rate, screen_ticker

class TestEstimateGrowth:
    def test_returns_float(self):
        result = _estimate_historical_growth("AAPL")
        assert isinstance(result, float)
        assert 0.0 <= result <= 0.50
    
    def test_fallback_for_invalid(self):
        result = _estimate_historical_growth("INVALID_TICKER")
        assert result == 0.05  # default fallback

class TestTerminalGrowth:
    def test_returns_positive_float(self):
        result = _get_terminal_growth_rate("USD")
        assert isinstance(result, float)
        assert result > 0

class TestScreenTicker:
    def test_apple_returns_result(self):
        result = screen_ticker("AAPL")
        assert result is not None
        assert result.ticker == "AAPL"
        assert result.actual_pb > 0
        assert result.gap_pct is not None
```

---

### Task 6: Historical Price Data Loader

**Files:**
- Create: `backtest/data.py`

**Goal:** Load historical price data for a universe of tickers over a date range.

- [ ] **Step 1: Implement DataLoader**

```python
# backtest/data.py
"""Historical data loader for backtesting.

Downloads and caches price data for a universe of tickers.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf


CACHE_DIR = Path(__file__).resolve().parent / ".cache"


class DataLoader:
    """Downloads and caches historical price data."""
    
    def __init__(self, use_cache: bool = True, cache_dir: Optional[Path] = None):
        self.use_cache = use_cache
        self.cache_dir = cache_dir or CACHE_DIR
        if use_cache:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
    
    def _cache_path(self, ticker: str) -> Path:
        return self.cache_dir / f"{ticker}.parquet"
    
    def download_ticker(
        self,
        ticker: str,
        start_date: date,
        end_date: date,
        force: bool = False,
    ) -> pd.DataFrame:
        """Download price data for a single ticker.
        
        Uses cache if available. Returns DataFrame with OHLCV data.
        """
        cache_path = self._cache_path(ticker)
        
        if self.use_cache and cache_path.exists() and not force:
            df = pd.read_parquet(cache_path)
            # Check if cache covers the requested range
            if df.index[0] <= pd.Timestamp(start_date) and df.index[-1] >= pd.Timestamp(end_date):
                return df.loc[start_date:end_date]
        
        # Download from yfinance
        stock = yf.Ticker(ticker)
        df = stock.history(start=start_date, end=end_date + timedelta(days=1))
        
        if df.empty:
            return df
        
        if self.use_cache:
            df.to_parquet(cache_path)
        
        return df
    
    def download_universe(
        self,
        tickers: list[str],
        start_date: date,
        end_date: date,
    ) -> dict[str, pd.DataFrame]:
        """Download price data for a list of tickers.
        
        Returns dict of ticker -> DataFrame.
        """
        result = {}
        for ticker in tickers:
            try:
                df = self.download_ticker(ticker, start_date, end_date)
                if not df.empty:
                    result[ticker] = df
            except Exception:
                continue
        return result
    
    def get_prices_on_date(
        self,
        tickers: list[str],
        target_date: date,
    ) -> dict[str, float]:
        """Get closing prices for a list of tickers on a specific date.
        
        Returns dict of ticker -> close price.
        """
        prices = {}
        for ticker in tickers:
            try:
                df = self.download_ticker(ticker, target_date - timedelta(days=5), target_date)
                if not df.empty:
                    # Find the closest date <= target_date
                    mask = df.index <= pd.Timestamp(target_date)
                    if mask.any():
                        close = df.loc[mask, "Close"].iloc[-1]
                        prices[ticker] = float(close)
            except Exception:
                continue
        return prices
```

- [ ] **Step 2: Write tests for DataLoader**

```python
# backtest/tests/test_data.py
import pytest
from datetime import date
from backtest.data import DataLoader

class TestDataLoader:
    def test_download_single_ticker(self):
        loader = DataLoader(use_cache=False)
        df = loader.download_ticker("AAPL", date(2024, 1, 1), date(2024, 12, 31))
        assert not df.empty
        assert "Close" in df.columns
        assert len(df) > 200  # ~252 trading days
    
    def test_prices_on_date(self):
        loader = DataLoader(use_cache=False)
        prices = loader.get_prices_on_date(["AAPL", "MSFT"], date(2024, 6, 1))
        assert "AAPL" in prices
        assert prices["AAPL"] > 0
```

---

### Task 7: Core Backtest Engine

**Files:**
- Create: `backtest/engine.py`

**Goal:** Orchestrate the full backtest: load data, run initial screen, manage positions, check exits daily, rebalance event-driven.

- [ ] **Step 1: Implement the BacktestEngine**

```python
# backtest/engine.py
"""Core backtest engine for the P/B vs ROE strategy.

Flow:
1. On start date: Screen S&P 500, buy top 10 undervalued (equal weight)
2. Each day: Check ATR exits for all positions
3. When a position exits: On next available day, screen all S&P 500 
   again and fill the gap with the best undervalued stock
4. End: Return performance metrics
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import pandas as pd

from backtest.portfolio import Portfolio
from backtest.data import DataLoader
from backtest.sp500 import sp500_constituents
from backtest.strategy import screen_ticker, atr_exit_signal, rank_tickers
from backtest.metrics import calculate_metrics


@dataclass
class BacktestConfig:
    start_date: date
    end_date: date
    initial_capital: float = 100_000.0
    max_positions: int = 10
    atr_period: int = 15
    atr_multiplier: float = 3.0
    screen_top_n: int = 10
    use_cache: bool = True


@dataclass
class BacktestResult:
    config: BacktestConfig
    portfolio: Portfolio
    equity_curve: pd.Series
    trades: pd.DataFrame
    metrics: dict
    screen_results: list  # Results from initial screen


class BacktestEngine:
    """Main backtest engine."""
    
    def __init__(self, config: BacktestConfig):
        self.config = config
        self.data_loader = DataLoader(use_cache=config.use_cache)
        self.portfolio = Portfolio(
            initial_capital=config.initial_capital,
            max_positions=config.max_positions,
        )
        self.screen_results = []
    
    def run(self) -> BacktestResult:
        """Run the backtest."""
        cfg = self.config
        start = cfg.start_date
        end = cfg.end_date
        
        # Step 1: Get S&P 500 constituents
        print(f"Fetching S&P 500 constituents as of {start}...")
        universe = sp500_constituents(as_of=start)
        print(f"  Found {len(universe)} tickers")
        
        # Step 2: Initial screen - rank by valuation gap
        print(f"Running initial screen (top {cfg.screen_top_n})...")
        initial_picks = rank_tickers(universe, top_n=cfg.screen_top_n)
        self.screen_results = initial_picks
        print(f"  Selected {len(initial_picks)} stocks")
        for r in initial_picks:
            print(f"    {r.ticker}: gap={r.gap_pct:.2%}, roe={r.roe:.1%}, pb={r.actual_pb:.2f}")
        
        if not initial_picks:
            print("No undervalued stocks found. Aborting.")
            return self._empty_result()
        
        # Step 3: Buy initial positions
        print("\nBuying initial positions...")
        buy_date = start + timedelta(days=3)  # Allow time for data to settle
        prices = self.data_loader.get_prices_on_date(
            [r.ticker for r in initial_picks], buy_date
        )
        
        for pick in initial_picks:
            if pick.ticker in prices:
                price = prices[pick.ticker]
                target_value = self.portfolio.target_position_value
                shares = target_value / price
                self.portfolio.buy(pick.ticker, price, shares, buy_date, reason="initial_screen")
                print(f"  Bought {pick.ticker} at {price:.2f}")
            else:
                print(f"  Skipped {pick.ticker} (no price data)")
        
        # Step 4: Daily loop
        print(f"\nRunning daily loop from {start} to {end}...")
        current_date = start
        daily_count = 0
        needs_rebalance = False
        
        while current_date <= end:
            daily_count += 1
            if daily_count % 63 == 0:  # Every ~quarter
                print(f"  Processing {current_date}... (positions: {self.portfolio.position_count})")
            
            # Skip weekends
            if current_date.weekday() >= 5:
                current_date += timedelta(days=1)
                continue
            
            # Get current prices for all held positions
            held_tickers = list(self.portfolio.positions.keys())
            if held_tickers:
                current_prices = self.data_loader.get_prices_on_date(held_tickers, current_date)
                self.portfolio.update_prices(current_prices, current_date)
                
                # Check ATR exits for each position
                for ticker in held_tickers:
                    if ticker in current_prices:
                        price = current_prices[ticker]
                        should_exit = atr_exit_signal(
                            ticker, price,
                            period=cfg.atr_period,
                            multiplier=cfg.atr_multiplier,
                        )
                        if should_exit:
                            self.portfolio.sell(ticker, price, current_date, reason="atr_stop")
                            print(f"  [{current_date}] STOPPED OUT: {ticker} at {price:.2f}")
                            needs_rebalance = True
            
            # Rebalance if needed (fill gaps from exits)
            if needs_rebalance and self.portfolio.available_slots > 0:
                # Re-screen the universe
                new_picks = rank_tickers(universe, top_n=cfg.screen_top_n)
                # Filter out already held tickers
                new_picks = [p for p in new_picks if not self.portfolio.has_position(p.ticker)]
                # Take only as many as we need
                new_picks = new_picks[:self.portfolio.available_slots]
                
                if new_picks:
                    rebal_prices = self.data_loader.get_prices_on_date(
                        [p.ticker for p in new_picks], current_date
                    )
                    for pick in new_picks:
                        if pick.ticker in rebal_prices:
                            price = rebal_prices[pick.ticker]
                            target_value = self.portfolio.target_position_value
                            shares = target_value / price
                            self.portfolio.buy(pick.ticker, price, shares, current_date, reason="rebalance")
                            print(f"  [{current_date}] REBALANCE: Bought {pick.ticker} at {price:.2f}")
                
                needs_rebalance = False
            
            current_date += timedelta(days=1)
        
        # Step 5: Liquidate all positions at end
        print(f"\nLiquidating positions at {end}...")
        final_prices = self.data_loader.get_prices_on_date(
            list(self.portfolio.positions.keys()), end
        )
        for ticker in list(self.portfolio.positions.keys()):
            if ticker in final_prices:
                self.portfolio.sell(ticker, final_prices[ticker], end, reason="end_of_backtest")
        
        # Step 6: Calculate metrics
        print("\nCalculating performance metrics...")
        equity_curve = self.portfolio.equity_curve()
        trades_df = pd.DataFrame([vars(t) for t in self.portfolio.trades])
        metrics = calculate_metrics(equity_curve, trades_df, cfg.initial_capital)
        
        return BacktestResult(
            config=cfg,
            portfolio=self.portfolio,
            equity_curve=equity_curve,
            trades=trades_df,
            metrics=metrics,
            screen_results=self.screen_results,
        )
    
    def _empty_result(self) -> BacktestResult:
        equity_curve = self.portfolio.equity_curve()
        return BacktestResult(
            config=self.config,
            portfolio=self.portfolio,
            equity_curve=equity_curve,
            trades=pd.DataFrame(),
            metrics={},
            screen_results=[],
        )


def run_backtest(
    start_date: date,
    end_date: date,
    initial_capital: float = 100_000.0,
    atr_period: int = 15,
    atr_multiplier: float = 3.0,
    use_cache: bool = True,
) -> BacktestResult:
    """Convenience function to run a backtest with default config."""
    config = BacktestConfig(
        start_date=start_date,
        end_date=end_date,
        initial_capital=initial_capital,
        atr_period=atr_period,
        atr_multiplier=atr_multiplier,
        use_cache=use_cache,
    )
    engine = BacktestEngine(config)
    return engine.run()


if __name__ == "__main__":
    from datetime import date
    
    # Default: 3-year backtest
    end = date.today()
    start = date(end.year - 3, end.month, end.day)
    
    result = run_backtest(start, end)
    
    print("\n" + "=" * 60)
    print("BACKTEST RESULTS")
    print("=" * 60)
    print(f"Period: {start} to {end}")
    print(f"Initial Capital: ${result.config.initial_capital:,.2f}")
    print(f"Final Value: ${result.portfolio.total_value:,.2f}")
    print(f"Total Return: {result.metrics.get('total_return', 'N/A'):.2%}")
    print(f"Sharpe Ratio: {result.metrics.get('sharpe_ratio', 'N/A'):.2f}")
    print(f"Max Drawdown: {result.metrics.get('max_drawdown', 'N/A'):.2%}")
    print(f"Number of Trades: {len(result.trades)}")
    print(f"Win Rate: {result.metrics.get('win_rate', 'N/A'):.2%}")
```

- [ ] **Step 2: Write tests for Engine**

```python
# backtest/tests/test_engine.py
import pytest
from datetime import date
from backtest.engine import BacktestConfig, BacktestEngine

class TestBacktestEngine:
    def test_initialization(self):
        config = BacktestConfig(
            start_date=date(2024, 1, 1),
            end_date=date(2024, 12, 31),
        )
        engine = BacktestEngine(config)
        assert engine.config.start_date == date(2024, 1, 1)
        assert engine.portfolio.initial_capital == 100_000
    
    @pytest.mark.slow
    def test_full_run(self):
        """Full backtest run - marked slow because it downloads data."""
        result = engine.run()
        assert result.portfolio.total_value > 0
        assert result.metrics is not None
```

---

### Task 8: Performance Metrics Module

**Files:**
- Create: `backtest/metrics.py`

**Goal:** Calculate standard performance metrics (Sharpe, Sortino, max drawdown, CAGR, win rate, etc.).

- [ ] **Step 1: Implement metrics**

```python
# backtest/metrics.py
"""Performance metrics for backtest results."""

from __future__ import annotations

import numpy as np
import pandas as pd


def calculate_metrics(
    equity_curve: pd.Series,
    trades: pd.DataFrame,
    initial_capital: float,
    risk_free_rate: float = 0.042,
) -> dict:
    """Calculate performance metrics from equity curve and trades.
    
    Returns dict with keys:
    - total_return, cagr, volatility, sharpe_ratio, sortino_ratio
    - max_drawdown, max_drawdown_duration
    - win_rate, avg_win, avg_loss, profit_factor
    - num_trades
    """
    if equity_curve.empty:
        return {}
    
    metrics = {}
    
    # Total return
    final_value = equity_curve.iloc[-1]
    metrics["total_return"] = (final_value / initial_capital) - 1
    
    # CAGR
    days = (equity_curve.index[-1] - equity_curve.index[0]).days
    years = days / 365.25
    if years > 0:
        metrics["cagr"] = (final_value / initial_capital) ** (1 / years) - 1
    else:
        metrics["cagr"] = 0.0
    
    # Daily returns
    daily_returns = equity_curve.pct_change().dropna()
    
    if len(daily_returns) > 0:
        # Volatility (annualized)
        metrics["volatility"] = daily_returns.std() * np.sqrt(252)
        
        # Sharpe ratio
        excess_returns = daily_returns - risk_free_rate / 252
        if metrics["volatility"] > 0:
            metrics["sharpe_ratio"] = (excess_returns.mean() / daily_returns.std()) * np.sqrt(252)
        else:
            metrics["sharpe_ratio"] = 0.0
        
        # Sortino ratio (downside deviation)
        downside = daily_returns[daily_returns < 0]
        if len(downside) > 0:
            downside_std = downside.std() * np.sqrt(252)
            if downside_std > 0:
                metrics["sortino_ratio"] = (metrics["cagr"] - risk_free_rate) / downside_std
            else:
                metrics["sortino_ratio"] = 0.0
        else:
            metrics["sortino_ratio"] = float("inf")
        
        # Max drawdown
        cumulative = (1 + daily_returns).cumprod()
        running_max = cumulative.cummax()
        drawdown = (cumulative - running_max) / running_max
        metrics["max_drawdown"] = drawdown.min()
        
        # Max drawdown duration
        is_drawdown = drawdown < 0
        if is_drawdown.any():
            drawdown_periods = (~is_drawdown).cumsum()
            drawdown_lengths = is_drawdown.groupby(drawdown_periods).sum()
            metrics["max_drawdown_duration"] = drawdown_lengths.max() if not drawdown_lengths.empty else 0
        else:
            metrics["max_drawdown_duration"] = 0
    
    # Trade statistics
    if not trades.empty and "action" in trades.columns:
        buys = trades[trades["action"] == "buy"]
        sells = trades[trades["action"] == "sell"]
        
        metrics["num_trades"] = len(sells)
        
        if len(sells) > 0:
            # Match sells with corresponding buys
            pnls = []
            for _, sell in sells.iterrows():
                ticker = sell["ticker"]
                # Find the corresponding buy
                buy_row = buys[buys["ticker"] == ticker]
                if not buy_row.empty:
                    buy_value = buy_row.iloc[0]["value"]
                    sell_value = sell["value"]
                    pnl = (sell_value - buy_value) / buy_value
                    pnls.append(pnl)
            
            if pnls:
                pnls = np.array(pnls)
                wins = pnls[pnls > 0]
                losses = pnls[pnls <= 0]
                
                metrics["win_rate"] = len(wins) / len(pnls) if len(pnls) > 0 else 0.0
                metrics["avg_win"] = wins.mean() if len(wins) > 0 else 0.0
                metrics["avg_loss"] = losses.mean() if len(losses) > 0 else 0.0
                metrics["profit_factor"] = (
                    wins.sum() / abs(losses.sum()) if len(losses) > 0 and abs(losses.sum()) > 0 else float("inf")
                )
    
    return metrics
```

- [ ] **Step 2: Write tests for metrics**

```python
# backtest/tests/test_metrics.py
import pytest
import pandas as pd
import numpy as np
from datetime import date, timedelta
from backtest.metrics import calculate_metrics

class TestMetrics:
    def test_constant_growth(self):
        # Create a steadily increasing equity curve
        dates = pd.date_range("2024-01-01", periods=252, freq="D")
        values = 100_000 * (1.001 ** np.arange(252))  # ~0.1% per day
        equity = pd.Series(values, index=dates)
        
        trades = pd.DataFrame()
        metrics = calculate_metrics(equity, trades, 100_000)
        
        assert metrics["total_return"] > 0
        assert metrics["sharpe_ratio"] > 0
        assert metrics["max_drawdown"] <= 0
    
    def test_flat_line(self):
        dates = pd.date_range("2024-01-01", periods=10, freq="D")
        values = [100_000] * 10
        equity = pd.Series(values, index=dates)
        trades = pd.DataFrame()
        metrics = calculate_metrics(equity, trades, 100_000)
        
        assert metrics["total_return"] == 0.0
        assert metrics["max_drawdown"] == 0.0
```

---

### Task 9: `backtest/__init__.py` and Module Wiring

**Files:**
- Create: `backtest/__init__.py`

- [ ] **Step 1: Create module init**

```python
# backtest/__init__.py
"""DBMF Quant Backtest Engine.

Usage:
    from backtest import run_backtest
    from datetime import date
    
    result = run_backtest(
        start_date=date(2023, 1, 1),
        end_date=date(2025, 12, 31),
        initial_capital=100_000,
    )
    print(result.metrics)
"""

from backtest.engine import run_backtest, BacktestConfig, BacktestEngine, BacktestResult
from backtest.portfolio import Portfolio, Position, Trade
from backtest.metrics import calculate_metrics

__all__ = [
    "run_backtest",
    "BacktestConfig",
    "BacktestEngine",
    "BacktestResult",
    "Portfolio",
    "Position",
    "Trade",
    "calculate_metrics",
]
```

---

### Task 10: Backtest CLI Entry Point

**Files:**
- Create: `backtest/run.py`

- [ ] **Step 1: Create CLI runner**

```python
# backtest/run.py
"""CLI entry point for running backtests.

Usage:
    python backtest/run.py --start 2023-01-01 --end 2025-12-31 --capital 100000
    python backtest/run.py --years 3  # Default: 3 years from today
"""

from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from backtest.engine import run_backtest


def main():
    ap = argparse.ArgumentParser(description="Run DBMF Quant backtest")
    ap.add_argument("--start", type=str, help="Start date (YYYY-MM-DD)")
    ap.add_argument("--end", type=str, help="End date (YYYY-MM-DD)")
    ap.add_argument("--years", type=int, default=3, help="Backtest duration in years (default: 3)")
    ap.add_argument("--capital", type=float, default=100_000, help="Initial capital (default: 100,000)")
    ap.add_argument("--atr-period", type=int, default=15, help="ATR period (default: 15)")
    ap.add_argument("--atr-multiplier", type=float, default=3.0, help="ATR multiplier (default: 3.0)")
    ap.add_argument("--top-n", type=int, default=10, help="Top N stocks to pick (default: 10)")
    ap.add_argument("--no-cache", action="store_true", help="Disable data caching")
    ap.add_argument("--json", action="store_true", help="Output results as JSON")
    args = ap.parse_args()
    
    # Calculate dates
    if args.start and args.end:
        start = date.fromisoformat(args.start)
        end = date.fromisoformat(args.end)
    else:
        end = date.today()
        start = date(end.year - args.years, end.month, end.day)
    
    print(f"Running backtest: {start} to {end}")
    print(f"Initial capital: ${args.capital:,.2f}")
    print(f"ATR: period={args.atr_period}, multiplier={args.atr_multiplier}")
    print(f"Top N: {args.top_n}")
    print("-" * 60)
    
    result = run_backtest(
        start_date=start,
        end_date=end,
        initial_capital=args.capital,
        atr_period=args.atr_period,
        atr_multiplier=args.atr_multiplier,
        use_cache=not args.no_cache,
    )
    
    if args.json:
        # Serialize minimal result
        output = {
            "period": {"start": str(start), "end": str(end)},
            "config": {
                "initial_capital": args.capital,
                "atr_period": args.atr_period,
                "atr_multiplier": args.atr_multiplier,
                "top_n": args.top_n,
            },
            "metrics": {k: float(v) if isinstance(v, (np.floating,)) else v for k, v in result.metrics.items()},
            "num_trades": len(result.trades) if not result.trades.empty else 0,
            "initial_picks": [r.ticker for r in result.screen_results],
        }
        print(json.dumps(output, indent=2))
    else:
        print("\n" + "=" * 60)
        print("BACKTEST RESULTS")
        print("=" * 60)
        print(f"Period: {start} to {end}")
        print(f"Initial Capital: ${args.capital:,.2f}")
        print(f"Final Value: ${result.portfolio.total_value:,.2f}")
        print(f"Total Return: {result.metrics.get('total_return', 'N/A'):.2%}")
        print(f"CAGR: {result.metrics.get('cagr', 'N/A'):.2%}")
        print(f"Sharpe Ratio: {result.metrics.get('sharpe_ratio', 'N/A'):.2f}")
        print(f"Sortino Ratio: {result.metrics.get('sortino_ratio', 'N/A'):.2f}")
        print(f"Max Drawdown: {result.metrics.get('max_drawdown', 'N/A'):.2%}")
        print(f"Volatility: {result.metrics.get('volatility', 'N/A'):.2%}")
        print(f"Number of Trades: {result.metrics.get('num_trades', 'N/A')}")
        print(f"Win Rate: {result.metrics.get('win_rate', 'N/A'):.2%}")
        print(f"Profit Factor: {result.metrics.get('profit_factor', 'N/A'):.2f}")
        print(f"\nInitial Picks: {[r.ticker for r in result.screen_results]}")


if __name__ == "__main__":
    main()
```

---

## Implementation Order

| Order | Task | Description | Dependencies |
|-------|------|-------------|--------------|
| 1 | Task 1 | Scrape Damodaran historical ERP data | None |
| 2 | Task 3 | 2-stage Gordon growth model | None |
| 3 | Task 4 | Portfolio manager | None |
| 4 | Task 8 | Performance metrics | None |
| 5 | Task 2 | S&P 500 constituents fetcher | None |
| 6 | Task 6 | Data loader | None |
| 7 | Task 5 | Strategy module (entry + exit signals) | Task 1, 3 |
| 8 | Task 7 | Core backtest engine | Task 2, 4, 5, 6, 8 |
| 9 | Task 9 | Module init | Task 7 |
| 10 | Task 10 | CLI entry point | Task 7 |

---

## Verification Checklist

After implementation:

- [ ] `python backtest/damodaran_archive.py` downloads all historical ERP files
- [ ] `implied_erp/data/` contains `damodaran_erp_YYYY.json` for multiple years
- [ ] `backtest/tests/test_valuation.py` all pass (2-stage model)
- [ ] `backtest/tests/test_portfolio.py` all pass
- [ ] `backtest/tests/test_metrics.py` all pass
- [ ] `backtest/tests/test_sp500.py` all pass
- [ ] `backtest/tests/test_data.py` all pass
- [ ] `backtest/tests/test_strategy.py` all pass
- [ ] `python backtest/run.py --years 1 --capital 10000` runs a 1-year backtest
- [ ] ATR exits fire correctly when price hits trailing stop
- [ ] Event-driven rebalancing fills gaps after exits
- [ ] Performance metrics are calculated correctly

---

## Known Limitations & Future Work

1. **S&P 500 historical accuracy**: Current implementation uses current S&P 500 list. A future enhancement would reconstruct the index at each point in time using the Wikipedia changes table.

2. **Earnings season awareness**: The backtest doesn't yet wait for earnings season to re-screen. The event-driven rebalancing fills gaps immediately after ATR exits.

3. **Data caching**: Parquet cache speeds up repeated runs but can grow large. Consider adding cache expiry.

4. **Historical ERP**: The Damodaran scraper downloads all available years. Some early years may have different spreadsheet formats that need special handling.

5. **2-stage growth**: The linear decline assumption may not match all companies. Consider alternative decline patterns (H-model, 3-stage).

---

**Plan saved to:** `docs/superpowers/plans/2026-07-24-backtest-engine.md`

Two execution options:

1. **Subagent-Driven (recommended)** - I dispatch a fresh subagent per task, review between tasks, fast iteration
2. **Inline Execution** - Execute tasks in this session, batch execution with checkpoints

**Which approach?**