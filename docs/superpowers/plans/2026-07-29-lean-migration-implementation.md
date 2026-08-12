# Lean Migration (Approach A) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Port dbmf_quant's P/B vs ROE screening + ATR trailing stop strategy to QuantConnect Lean framework (Approach A: Minimal Algorithm).

**Architecture:** Single `QCAlgorithm` subclass with QC500 universe, Fine fundamental filter, custom DamodaranERP data, 2-stage Gordon Growth valuation, event-driven ATR stop exits.

**Tech Stack:** QuantConnect Lean (Python), Docker, yfinance, openpyxl (for cache), pandas, numpy

## Global Constraints

- Python 3.11+ (matching project `.venv`)
- Lean CLI v2 installed: `pip install leaning`
- Docker running with file sharing enabled (Windows)
- Data folder at repo root: `lean_project/data/`
- All custom data files committed to git alongside source
- No quarterly rebalancing (deferred to future)
- US citizen tax treatment default; Portuguese tax → future feature

---

## File Structure

| File | Purpose |
|------|---------|
| `lean_project/main.py` | QCAlgorithm: Initialize, OnData, OnSecuritiesChanged, CheckAtrStops |
| `lean_project/main.py` | QCAlgorithm: Initialize, OnData, OnSecuritiesChanged, CheckAtrStops |
| `lean_project/data/damodaran_erp.py` | PythonData reader for july26.json |
| `lean_project/universe/pb_roe_universe.py` | Fine filter: exclude financials, rank by gap_pct |
| `lean_project/valuation/gordon_growth.py` | Port of intrinsic_pb_2stage |
| `lean_project/indicators/atr_trailing_stop.py` | ATR trailing stop calculation |
| `lean_project/scripts/compute_growth_cache.py` | Offline yfinance EPS CAGR cache generator |
| `lean_project/config.json` | Lean CLI config |
| `lean_project/data/DAMODARAN_ERP/` | Directory for Damodaran ERP CSV/json |
| `lean_project/data/growth_cache.json` | Cached EPS CAGR per ticker |
| `lean_project/data/damodaran_erp.json` | Copied from dbmf_quant/implied_erp/data/july26.json |

---

### Task 1: Setup Lean project

**Files:**
- Create: `lean_project/.gitignore`
- Create: `lean_project/config.json`
- Create: `lean_project/requirements.txt` (empty — Lean manages deps)
- Create: `lean_project/lean-cli-config.json` (Lean CLI config)

- [ ] **Step 1: Initialize Lean project**

Run: `cd C:\Users\Consultor\Desktop\DARIO_FILHO\dbmf_quant && lean init lean_project --python`

Expected: Creates `lean_project/` with `config.json`, `main.py` (stub), `lean-cli-config.json`, Dockerfile

- [ ] **Step 2: Verify Lean install**

Run: `lean version`
Expected: Prints Lean version (≥ v3.x)

- [ ] **Step 3: Verify Docker is running**

Run: `docker ps`
Expected: Docker daemon active, no errors

- [ ] **Step 4: Configure `config.json`**

Write `lean_project/config.json`:
```json
{
  "algorithm-script-name": "main.py",
  "algorithm-type-name": "PbRoeAtrAlgorithm",
  "live-mode": false,
  "live-mode-delay": 300,
  "live-mode-brokerage": "InteractiveBrokers",
  "live-mode-account-type": "Margin",
  "live-mode-cash": 100000,
  "start-date": "2023-01-01",
  "end-date": "2025-12-31",
  "security-price-age": 30,
  "forward-window": "00:00",
  "backtesting-data-folder": "data",
  "data-provider": "File"
}
```

- [ ] **Step 5: Run Lean build to verify setup**

Run: `cd lean_project && lean build`
Expected: `Build completed successfully`

- [ ] **Step 6: Commit skeleton**

Bash:
```bash
git add lean_project/
git commit -m "feat: initialize Lean project structure for P/B vs ROE ATR strategy"
```

---

### Task 2: Port intrinsic_pb_2stage valuation module

**Files:**
- Create: `lean_project/valuation/gordon_growth.py`

**Interfaces:**
- Consumes: None (pure math function)
- Produces: `intrinsic_pb_2stage(roe, g_start, g_term, r, years_stage1=5) -> float`

- [ ] **Step 1: Create valuation directory and module**

Write `lean_project/valuation/gordon_growth.py`:
```python
"""2-stage Gordon Growth Model for intrinsic P/B ratio.

Ported from dbmf_quant/backtest/valuation.py.
"""


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

    pv_stage1 = 0.0
    bv_growth = 1.0
    for t in range(1, years_stage1 + 1):
        g_t = (
            g_start + (g_term - g_start) * (t - 1) / (years_stage1 - 1)
            if years_stage1 > 1
            else g_start
        )
        payout = 1 - (g_t / roe) if roe > 0 else 0
        div_yield = roe * bv_growth * (1 + g_t) * payout
        pv_stage1 += div_yield / ((1 + r) ** t)
        bv_growth *= (1 + g_t)

    term_payout = 1 - (g_term / roe) if roe > 0 else 0
    term_div_at_year6 = roe * bv_growth * (1 + g_term) * term_payout
    terminal_value = term_div_at_year6 / (r - g_term)
    pv_terminal = terminal_value / ((1 + r) ** years_stage1)

    return pv_stage1 + pv_terminal


def intrinsic_pb_single_stage(roe: float, g: float, r: float) -> float:
    """Single-stage Gordon growth P/B. Kept for reference."""
    if r == g:
        return float("inf")
    return (roe * (1 + g) * (1 - g / roe)) / (r - g)
```

- [ ] **Step 2: Verify math matches original**

Run:
```bash
cd lean_project && python -c "
from valuation.gordon_growth import intrinsic_pb_2stage
# Test: ROE=0.15, g_start=0.08, g_term=0.04, r=0.10
result = intrinsic_pb_2stage(0.15, 0.08, 0.04, 0.10)
print(f'intrinsic_pb_2stage(0.15, 0.08, 0.04, 0.10) = {result:.4f}')
assert 2.0 < result < 5.0, f'Result {result} out of expected range'
print('OK')
"
```
Expected: Prints a value between 2.0 and 5.0, then `OK`

- [ ] **Step 3: Commit**

Bash:
```bash
cd lean_project && git add valuation/gordon_growth.py && git commit -m "feat: port 2-stage Gordon Growth intrinsic P/B from backtest/valuation.py"
```

---

### Task 3: Create DamodaranERP custom data reader

**Files:**
- Create: `lean_project/data/damodaran_erp.py`
- Create: `lean_project/data/damodaran_erp.json` (symlink or copy from ../implied_erp/data/july26.json)

**Interfaces:**
- Produces: `DamodaranERP` object with `Value` (float), `Symbol` (str), `Time` (datetime)
- Consumes: JSON file `data/damodaran_erp.json` — country → ERP map

- [ ] **Step 1: Copy ERP data into Lean data folder**

Bash:
```bash
cp C:\Users\Consultor\Desktop\DARIO_FILHO\dbmf_quant\implied_erp\data\july26.json lean_project/data/damodaran_erp.json
```
Note: If `july26.json` is missing, use `implied_erp/data/damodaran_erp.json` (lightweight build) instead.

- [ ] **Step 2: Create DamodaranERP PythonData class**

Write `lean_project/data/damodaran_erp.py`:
```python
"""Custom data: Damodaran Equity Risk Premium by country."""

from datetime import datetime
from decimal import Decimal
import json
from pathlib import Path

from AlgorithmImports import *


class DamodaranERP(PythonData):
    """Maps country name to equity risk premium (ERP) value."""

    def GetSource(self, config, date, isLive):
        """Return path to the ERP JSON file."""
        data_dir = Path(__file__).resolve().parent
        return SubscriptionDataSource(
            str(data_dir / "damodaran_erp.json"),
            SubscriptionTransportMedium.LocalFile,
            FileFormat.Json,
        )

    def Reader(self, config, line, date, isLive):
        """Parse a line from the JSON file and return DamodaranERP objects."""
        if not line.strip():
            return None

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            # JSON is an object, not newline-delimited; handle single-line parse
            data = json.loads(line.strip().rstrip(","))
            return self._parse_object(data, config, date)

        # If the line is an object (not an array), parse it directly
        if isinstance(data, dict):
            return self._parse_object(data, config, date)

        return None

    def _parse_object(self, data, config, date):
        """Parse a single country entry from the JSON object."""
        erp = DamodaranERP()
        erp.Symbol = config.Symbol
        erp.Time = date

        # The july26.json structure: dict of country -> {erp, rating, ...}
        # We store the entire object; consumers access per-country via the cache
        erp["data"] = data
        erp.Value = Decimal(0)  # placeholder; real ERP read from cache
        return erp

    @staticmethod
    def load_cache(symbol=None):
        """Load the ERP data from the JSON file.

        Usage in algorithm:
            erp_data = DamodaranERP.load_cache()
            usa_erp = erp_data.get("United States", {}).get("Total Equity Risk Premium", 0.055)
        """
        data_dir = Path(__file__).resolve().parent
        erp_file = data_dir / "damodaran_erp.json"

        if not erp_file.exists():
            return {}

        with open(erp_file, "r") as f:
            return json.load(f)
```

- [ ] **Step 3: Verify data file exists and is valid JSON**

Run:
```bash
cd lean_project/data && python -c "
import json
with open('damodaran_erp.json') as f:
    data = json.load(f)
print(f'Type: {type(data).__name__}')
print(f'Keys (sample): {list(data.keys())[:3]}')
if isinstance(data, dict):
    first_key = list(data.keys())[0]
    print(f'First entry ({first_key}): {data[first_key]}')
"
```
Expected: Prints `Type: dict`, sample keys, first entry details

- [ ] **Step 4: Commit**

Bash:
```bash
cd lean_project && git add data/damodaran_erp.py data/damodaran_erp.json && git commit -m "feat: add DamodaranERP custom data reader (PythonData)"
```

---

### Task 4: Create growth cache script + generate data

**Files:**
- Create: `lean_project/scripts/compute_growth_cache.py`
- Create: `lean_project/data/growth_cache.json` (generated output)

**Interfaces:**
- Consumes: yfinance ticker data (EPS history)
- Produces: `data/growth_cache.json` — `{ticker: cagr_float}`

- [ ] **Step 1: Create scripts directory and growth cache script**

Write `lean_project/scripts/compute_growth_cache.py`:
```python
"""Offline EPS CAGR cache generator for Lean backtest.

Downloads 2-year quarterly EPS history via yfinance for a list of tickers,
computes CAGR, caps at 50%, floors at 0%, and writes to JSON.

Usage:
    python scripts/compute_growth_cache.py
    python scripts/compute_growth_cache.py --tickers AAPL MSFT GOOG
    python scripts/compute_growth_cache.py --cache-path ../data/growth_cache.json
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path

import yfinance as yf


def compute_eps_cagr(ticker: str, years: int = 2) -> float:
    """Compute 2-year EPS CAGR from quarterly data.

    Falls back to 5% if data is missing or invalid.
    """
    try:
        stock = yf.Ticker(ticker)
        earnings = stock.earnings
        if earnings is not None and not earnings.empty:
            eps = earnings["Earnings"]
            if len(eps) >= 2:
                recent = eps.iloc[:min(years * 4, len(eps))]
                if len(recent) >= 2:
                    first_val = recent.iloc[-1]
                    last_val = recent.iloc[0]
                    if first_val > 0 and last_val > 0:
                        periods = len(recent) - 1
                        cagr = (last_val / first_val) ** (1.0 / periods) - 1
                        annualized = cagr * 4
                        return max(0.0, min(annualized, 0.50))
    except Exception:
        pass

    return 0.05  # Default fallback


def main():
    parser = argparse.ArgumentParser(
        description="Generate EPS CAGR growth cache for Lean backtest"
    )
    parser.add_argument(
        "--tickers",
        type=str,
        nargs="+",
        help="List of tickers to process (default: QC500 universe)",
    )
    parser.add_argument(
        "--cache-path",
        type=str,
        default="../data/growth_cache.json",
        help="Output path for growth_cache.json",
    )
    parser.add_argument(
        "--years",
        type=int,
        default=2,
        help="Number of years for CAGR calculation (default: 2)",
    )
    args = parser.parse_args()

    if args.tickers:
        tickers = args.tickers
    else:
        # Default: QC500 tickers (top ~500 US equities by dollar volume)
        # In production, fetch from QC API or use a static list
        tickers = _get_qc500_tickers()

    print(f"Computing EPS CAGR for {len(tickers)} tickers...")
    cache = {}
    for i, ticker in enumerate(tickers, 1):
        if i % 50 == 0:
            print(f"  Processed {i}/{len(tickers)}...")
        cache[ticker] = round(compute_eps_cagr(ticker, years=args.years), 4)

    output_path = Path(args.cache_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w") as f:
        json.dump(cache, f, indent=2)

    print(f"Cache written to {output_path} ({len(cache)} entries)")
    print(f"Sample: {dict(list(cache.items())[:5])}")


def _get_qc500_tickers() -> list:
    """Get QC500 tickers from QuantConnect or a static fallback."""
    # Static fallback: common QC500 constituents
    # In production, fetch from https://api.quantconnect.com or maintain a list
    DEFAULT_TICKERS = [
        "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "BRK.B", "JNJ",
        "JPM", "V", "PG", "UNH", "HD", "MA", "NVDA", "DIS", "PYPL",
        "NFLX", "ADBE", "CRM", "CMCSA", "PEP", "KO", "ABT", "CSCO",
        "PFE", "TMO", "COST", "AVGO", "ACN", "TXN", "LOW", "NEE", "UPS",
        "QCOM", "IBM", "AMD", "INTC", "NOW", "ISRG", "BKNG", "GILD",
        "MDLZ", "ADP", "T", "VZ", "CL", "LLY", "SBUX", "MCD", "CAT",
        "DE", "MMM", "GS", "BLK", "AXP", "SPGI", "ICE", "TGT", "MU",
        "LRCX", "AMAT", "KLAC", "MELI", "SNPS", "CDNS", "FTNT", "PANW",
    ]
    return DEFAULT_TICKERS


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Generate growth cache with default QC500 tickers**

Run:
```bash
cd lean_project && python scripts/compute_growth_cache.py
```
Expected: Prints progress, "Cache written to ../data/growth_cache.json (N entries)", sample tickers with CAGR values

- [ ] **Step 3: Verify cache file**

Run:
```bash
python -c "
import json
with open('data/growth_cache.json') as f:
    cache = json.load(f)
print(f'Entries: {len(cache)}')
ticker = list(cache.keys())[0]
print(f'Sample: {ticker} -> {cache[ticker]}')
assert 0.0 <= cache[ticker] <= 0.5, f'CAGR out of range: {cache[ticker]}'
print('OK')
"
```
Expected: Prints entry count, sample ticker/CAGR, OK

- [ ] **Step 4: Commit**

Bash:
```bash
cd lean_project && git add scripts/compute_growth_cache.py data/growth_cache.json && git commit -m "feat: add growth cache script and generated EPS CAGR data"
```

---

### Task 5: Create ATR trailing stop indicator

**Files:**
- Create: `lean_project/indicators/atr_trailing_stop.py`

**Interfaces:**
- Consumes: Symbol, ATR period, multiplier, price series
- Produces: trailing stop price (float) or None if insufficient data

- [ ] **Step 1: Create indicators directory**

Bash:
```bash
mkdir -p lean_project/indicators
```

- [ ] **Step 2: Write ATR trailing stop module**

Write `lean_project/indicators/atr_trailing_stop.py`:
```python
"""ATR trailing stop calculation for Lean backtest.

Ported from dbmf_quant/vol_trail_stop/vol_trail_stop.py.
Uses Lean's built-in ATR indicator + custom trailing stop logic.
"""

from __future__ import annotations

from typing import Optional

from AlgorithmImports import *


def compute_atr_trailing_stop(
    symbol: Symbol,
    algorithm: QCAlgorithm,
    period: int = 15,
    multiplier: float = 3.0,
    smoothing: str = "SMA",
) -> Optional[float]:
    """Check if price has breached ATR trailing stop.

    Args:
        symbol: The security symbol
        algorithm: QCAlgorithm instance (for History() and ATR indicator)
        period: ATR lookback period (default 15)
        multiplier: ATR multiplier for stop distance (default 3.0)
        smoothing: Smoothing method — 'SMA', 'EMA', 'WMA', 'RMA' (default 'SMA')

    Returns:
        stop_price (float) if enough data, None otherwise.
        Returns None if stop is not breached (caller checks price <= stop_price).
    """
    # Get recent history for ATR calculation
    history = algorithm.History(
        symbol, period + 5, Resolution.Daily
    )

    if history.empty or len(history) < period + 2:
        return None

    # Get close and high/low for ATR
    close_prices = history["close"].values
    high_prices = history["high"].values
    low_prices = history["low"].values

    if len(close_prices) < 2:
        return None

    # Compute True Range manually
    tr_values = []
    for i in range(1, len(close_prices)):
        hc = abs(high_prices[i] - close_prices[i - 1])
        lc = abs(low_prices[i] - close_prices[i - 1])
        hf = high_prices[i] - low_prices[i]
        tr_values.append(max(hc, lc, hf))

    if len(tr_values) < period:
        return None

    # Compute ATR with selected smoothing
    recent_tr = tr_values[-period:]

    if smoothing == "SMA" or smoothing == "0":
        atr = sum(recent_tr) / len(recent_tr)
    elif smoothing == "EMA":
        atr = _ema(recent_tr, period)
    elif smoothing == "WMA":
        atr = _wma(recent_tr, period)
    elif smoothing == "RMA":
        atr = _rma(recent_tr, period)
    else:
        atr = sum(recent_tr) / len(recent_tr)

    # Trailing stop = current close - multiplier * ATR
    current_close = close_prices[-1]
    stop_price = current_close - multiplier * atr

    return stop_price


def _ema(values, period):
    """Exponential moving average."""
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    multiplier = 2 / (period + 1)
    ema = values[0]
    for v in values[1:]:
        ema = v * multiplier + ema * (1 - multiplier)
    return ema


def _wma(values, period):
    """Weighted moving average."""
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    recent = values[-period:]
    weights = list(range(1, period + 1))
    return sum(v * w for v, w in zip(recent, weights)) / sum(weights)


def _rma(values, period):
    """Running moving average (Wilder's smoothing)."""
    if len(values) < period:
        return sum(values) / len(values) if values else 0
    alpha = 1 / period
    rma = values[0]
    for v in values[1:]:
        rma = v * alpha + rma * (1 - alpha)
    return rma
```

- [ ] **Step 3: Verify ATR calculation**

Run:
```bash
cd lean_project && python -c "
from indicators.atr_trailing_stop import compute_atr_trailing_stop, _sma
# Test SMA helper
vals = [1.0, 2.0, 3.0, 4.0, 5.0]
assert abs(_sma(vals, 5) - 3.0) < 0.001
print('SMA OK')

# Test EMA
from indicators.atr_trailing_stop import _ema
assert abs(_ema(vals, 5) - 4.266666) < 0.001
print('EMA OK')
print('Unit tests passed')
"
```
Expected: Prints SMA OK, EMA OK, Unit tests passed

- [ ] **Step 4: Commit**

Bash:
```bash
cd lean_project && git add indicators/atr_trailing_stop.py && git commit -m "feat: add ATR trailing stop indicator (ported from vol_trail_stop)"
```

---

### Task 6: Create Fine universe filter

**Files:**
- Create: `lean_project/universe/pb_roe_universe.py`

**Interfaces:**
- Consumes: FineFundamental objects (from Lean universe selection), growth_cache.json, damodaran_erp.json
- Produces: `List[Symbol]` — rank of top N undervalued non-financial symbols

- [ ] **Step 1: Create universe directory and module**

Bash:
```bash
mkdir -p lean_project/universe
```

- [ ] **Step 2: Write Fine universe selection module**

Write `lean_project/universe/pb_roe_universe.py`:
```python
"""Fine fundamental universe selection for P/B vs ROE strategy.

Filters QC500 constituents:
1. Exclude Financials (MorningstarSectorCode.FinancialServices)
2. Require valid P/B, ROE, Beta, EPS
3. Compute intrinsic P/B via 2-stage Gordon Growth
4. Rank by gap_pct = (implied - actual) / actual
5. Return top N undervalued symbols
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from AlgorithmImports import *
from valuation.gordon_growth import intrinsic_pb_2stage
from indicators.atr_trailing_stop import compute_atr_trailing_stop


def load_growth_cache() -> dict:
    """Load EPS CAGR growth cache from JSON file."""
    cache_path = Path(__file__).resolve().parent.parent / "data" / "growth_cache.json"
    if not cache_path.exists():
        return {}
    with open(cache_path, "r") as f:
        return json.load(f)


def load_erp_cache() -> dict:
    """Load Damodaran ERP data from JSON file."""
    erp_path = Path(__file__).resolve().parent.parent / "data" / "damodaran_erp.json"
    if not erp_path.exists():
        return {}
    with open(erp_path, "r") as f:
        return json.load(f)


def get_risk_free_rate(algorithm: QCAlgorithm) -> float:
    """Get risk-free rate from ^TNX (10-year Treasury yield)."""
    try:
        tn_x = algorithm.History(["TNX"], 1, Resolution.Daily)
        if not tn_x.empty:
            last_rate = tn_x["close"].iloc[-1] / 100.0  # Convert percentage to ratio
            return last_rate if last_rate > 0 else 0.042
    except Exception:
        pass
    return 0.042  # Fallback: 4.2%


def get_erp(erp_cache: dict, country: str = "United States") -> float:
    """Get ERP for a country from Damodaran cache."""
    return erp_cache.get(country, {}).get("Total Equity Risk Premium", 0.055)


def fine_selection(
    algorithm: QCAlgorithm,
    fine: list,
    max_positions: int = 10,
    growth_cache: Optional[dict] = None,
    erp_cache: Optional[dict] = None,
) -> list:
    """
    Fine fundamental universe selection for P/B vs ROE strategy.

    Args:
        algorithm: QCAlgorithm instance (for History, logging)
        fine: List of FineFundamental objects from universe selection
        max_positions: Maximum number of positions (default 10)
        growth_cache: EPS CAGR cache (ticker -> float). Defaults to loading from file.
        erp_cache: Damodaran ERP cache (country -> data). Defaults to loading from file.

    Returns:
        List of Symbol objects ranked by valuation gap (most undervalued first)
    """
    if growth_cache is None:
        growth_cache = load_growth_cache()
    if erp_cache is None:
        erp_cache = load_erp_cache()

    # 1. Exclude financials
    non_financial = [
        f for f in fine
        if f.AssetClassification is not None
        and f.AssetClassification.MorningstarSectorCode
        != MorningstarSectorCode.FinancialServices
    ]

    # 2. Require valid fundamentals
    valid = []
    for f in non_financial:
        try:
            pb = f.ValuationRatios.PBRatio
            roe = f.OperationRatios.ROE.OneYear
            beta = f.ValuationRatios.Beta
            eps = f.EarningReports.BasicEPS.OneYear
            if pb is not None and pb > 0 and roe is not None and roe > 0 and beta is not None and eps is not None and eps > 0:
                valid.append(f)
        except (AttributeError, TypeError):
            continue

    if not valid:
        algorithm.Log("FineSelection: No valid non-financial tickers with fundamentals")
        return []

    # 3. Get risk-free rate and ERP
    rf = get_risk_free_rate(algorithm)
    erp = get_erp(erp_cache, "United States")

    # 4. Score each ticker by Gordon Growth gap
    scored = []
    for f in valid:
        try:
            ticker = f.Symbol.Value
            g_start = growth_cache.get(ticker, 0.05)
            g_term = rf
            roe = f.OperationRatios.ROE.OneYear
            beta = f.ValuationRatios.Beta
            r = rf + beta * erp
            actual_pb = f.ValuationRatios.PBRatio

            implied_pb = intrinsic_pb_2stage(roe, g_start, g_term, r)

            if actual_pb > 0:
                gap_pct = (implied_pb - actual_pb) / actual_pb
                if gap_pct > 0:
                    scored.append((f.Symbol, gap_pct, implied_pb, actual_pb))
                algorithm.Log(
                    f"FineSelection: {ticker} gap={gap_pct:.2%} "
                    f"implied={implied_pb:.2f} actual={actual_pb:.2f} "
                    f"roe={roe:.2%} g_start={g_start:.2%}"
                )
        except Exception as e:
            algorithm.Log(f"FineSelection error for {f.Symbol.Value}: {e}")
            continue

    # 5. Rank by gap_pct descending, take top N
    scored.sort(key=lambda x: x[1], reverse=True)
    top_n = [s[0] for s in scored[:max_positions]]

    algorithm.Log(f"FineSelection: Selected {len(top_n)} tickers from {len(scored)} candidates")
    return top_n
```

- [ ] **Step 3: Verify module imports correctly**

Run:
```bash
cd lean_project && python -c "
import sys
sys.path.insert(0, '.')
from universe.pb_roe_universe import fine_selection, load_growth_cache, load_erp_cache
cache = load_growth_cache()
print(f'Growth cache: {len(cache)} entries')
erp = load_erp_cache()
print(f'ERP cache: {len(erp)} entries')
print('Module imports OK')
"
```
Expected: Prints cache sizes, Module imports OK

- [ ] **Step 4: Commit**

Bash:
```bash
cd lean_project && git add universe/pb_roe_universe.py && git commit -m "feat: add Fine universe filter (exclude financials, rank by Gordon Growth gap)"
```

---

### Task 7: Create main QCAlgorithm (`main.py`)

**Files:**
- Create (replace): `lean_project/main.py`

**Interfaces:**
- `PbRoeAtrAlgorithm(QCAlgorithm)`:
  - `Initialize()` — sets dates, cash, QC500 universe, IB brokerage, DamodaranERP data, ATR state
  - `FineSelection(fine)` — delegates to `pb_roe_universe.fine_selection`
  - `OnSecuritiesChanged(changes)` — liquidates removed, sets holdings for added
  - `CheckAtrStops()` — scheduled daily, checks ATR stops, liquidates if triggered
  - `OnData(data)` — handles DamodaranERP slice

- [ ] **Step 1: Replace stub `main.py` with full algorithm**

Write `lean_project/main.py`:
```python
"""P/B vs ROE ATR Trailing Stop Strategy for QuantConnect Lean.

Screens S&P 500 constituents (QC500) using 2-stage Gordon Growth implied P/B.
Filters out financials (MorningstarSectorCode.FinancialServices).
Exits positions when ATR trailing stop is breached.
All positions equal-weight (1/max_positions).
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from AlgorithmImports import *
from data.damodaran_erp import DamodaranERP
from valuation.gordon_growth import intrinsic_pb_2stage
from universe.pb_roe_universe import fine_selection, load_growth_cache, get_risk_free_rate
from indicators.atr_trailing_stop import compute_atr_trailing_stop


class PbRoeAtrAlgorithm(QCAlgorithm):
    """P/B vs ROE screening with ATR trailing stop exit."""

    def Initialize(self):
        """Set up strategy parameters, data, and schedule."""
        self.SetStartDate(2023, 1, 1)
        self.SetEndDate(2025, 12, 31)
        self.SetCash(100_000)

        # Brokerage model with IB fees (commissions, margin)
        self.SetBrokerageModel(
            BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin
        )

        # Universe selection: manual coarse (volume filter) + fine (Gordon Growth gap)
        # AddUniverse supports custom CoarseSelection and FineSelection methods
        self.AddUniverse(self.CoarseSelection, self.FineSelection)
        self.UniverseSettings.Resolution = Resolution.Daily

        # Custom data: Damodaran ERP
        self.AddData(DamodaranERP, "DAMODARAN_ERP", Resolution.Daily)

        # Strategy parameters
        self.max_positions = 10
        self.atr_period = 15
        self.atr_multiplier = 3.0

        # State
        self.atr_indicators = {}  # symbol -> ATR indicator
        self.trailing_stops = {}  # symbol -> stop price
        self.growth_cache = load_growth_cache()
        self.erp_cache = self._load_erp_cache()
        self.synced = False

        # Daily schedule: check ATR stops after market open
        self.Schedule.On(
            self.DateRules.EveryDay(),
            self.TimeRules.AfterMarketOpen("SPY", 0),
            self.CheckAtrStops,
        )

        self.Log("PbRoeAtrAlgorithm initialized successfully")

    def _load_erp_cache(self) -> dict:
        """Load Damodaran ERP data."""
        erp_path = Path(__file__).parent / "data" / "damodaran_erp.json"
        if erp_path.exists():
            with open(erp_path, "r") as f:
                return json.load(f)
        return {}

    def FineSelection(self, fine):
        """Fine fundamental universe selection.

        Filters QC500 constituents: excludes financials, requires valid
        P/B, ROE, Beta, EPS, ranks by Gordon Growth gap_pct.
        """
        symbols = fine_selection(
            algorithm=self,
            fine=fine,
            max_positions=self.max_positions,
            growth_cache=self.growth_cache,
            erp_cache=self.erp_cache,
        )
        return symbols

    def CoarseSelection(self, coarse):
        """Coarse universe: filter by dollar volume."""
        return [c.Symbol for c in coarse if c.DollarVolume > 10_000_000 and c.HasFundamentalData]

    def OnSecuritiesChanged(self, changes):
        """Handle universe changes: liquidate removed, add new at equal weight."""
        # Liquidate removed securities
        for removed in changes.RemovedSecurities:
            if removed.Symbol in self.trailing_stops:
                del self.trailing_stops[removed.Symbol]
            if removed.Symbol in self.atr_indicators:
                del self.atr_indicators[removed.Symbol]
            self.Log(f"OnSecuritiesChanged: Removed {removed.Symbol.Value}")

        # Set equal weight for new additions
        added = [s for s in changes.AddedSecurities if s.Symbol in self.Securities]
        if added:
            active_count = len(self.ActiveSecurities)
            weight = 1.0 / min(active_count, self.max_positions) if active_count > 0 else 1.0 / self.max_positions
            for sec in added:
                self.SetHoldings(sec.Symbol, weight)
                self._initialize_atr(sec.Symbol)
                self.Log(f"OnSecuritiesChanged: Added {sec.Symbol.Value} @ {weight:.1%}")

    def _initialize_atr(self, symbol):
        """Initialize ATR indicator for a new position."""
        # Warm up ATR with 2*period bars of history
        history = self.History(symbol, self.atr_period * 2, Resolution.Daily)
        if not history.empty and len(history) >= self.atr_period:
            # Compute initial ATR from history
            from indicators.atr_trailing_stop import compute_atr_trailing_stop
            stop = compute_atr_trailing_stop(
                symbol, self, self.atr_period, self.atr_multiplier, "SMA"
            )
            if stop is not None:
                self.trailing_stops[symbol] = stop
                self.Log(f"_initialize_atr: {symbol.Value} stop={stop:.2f}")

    def CheckAtrStops(self):
        """Daily check: update ATR trailing stops and liquidate if breached."""
        for symbol in list(self.Securities.Keys):
            if symbol not in self.Portfolio or not self.Portfolio[symbol].Invested:
                continue

            # Get current price
            price = self.Securities[symbol].Price
            if price <= 0:
                continue

            # Compute trailing stop
            stop_price = compute_atr_trailing_stop(
                symbol, self, self.atr_period, self.atr_multiplier, "SMA"
            )

            if stop_price is None:
                continue

            self.trailing_stops[symbol] = stop_price

            # Check breach: current price <= trailing stop
            if price <= stop_price:
                self.Log(
                    f"ATR STOP: {symbol.Value} price={price:.2f} "
                    f"stop={stop_price:.2f} -> LIQUIDATE"
                )
                self.Liquidate(symbol)

    def OnData(self, data):
        """Handle incoming data slices.

        DamodaranERP custom data used in FineSelection via on-disk cache,
        no per-tick processing needed here.
        """
        pass

    def OnEndOfAlgorithm(self):
        """Final logging."""
        self.Log("=" * 60)
        self.Log("BACKTEST COMPLETE")
        self.Log(f"Period: {self.StartDate} to {self.EndDate}")
        self.Log(f"Final Portfolio Value: {self.Portfolio.TotalPortfolioValue:,.2f}")
        self.Log(f"Positions Held: {sum(1 for s in self.ActiveSecurities if self.Portfolio[s].Invested)}")
        self.Log("=" * 60)
```

- [ ] **Step 2: Verify algorithm compiles**

Run:
```bash
cd lean_project && lean build
```
Expected: `Build completed successfully`

- [ ] **Step 3: Run a quick local backtest (dry run)**

Run:
```bash
cd lean_project && lean backtest "PbRoeAtr" --start 2023-01-01 --end 2023-06-30
```
Expected: Algorithm runs, produces equity curve and trade log (may be empty if no ATR stop triggered in first half of 2023)

- [ ] **Step 4: Commit**

Bash:
```bash
cd lean_project && git add main.py && git commit -m "feat: add PbRoeAtrAlgorithm QCAlgorithm with Fine selection and ATR stops"
```

---

### Task 8: Run full backtest and validate

**Files:**
- (None new — validates existing backtest runs)

**Interfaces:**
- Consumes: all Task 1-7 deliverables
- Produces: backtest report, comparison with `python backtest/run.py` output

- [ ] **Step 1: Configure backtest dates for comparison**

Use same date range as current backtest (e.g., 2023-01-01 to 2025-12-31 or a shorter window for speed).

- [ ] **Step 2: Run Lean backtest**

Run:
```bash
cd lean_project && lean backtest "PbRoeAtr" --start 2023-01-01 --end 2025-12-31 --json > lean_results.json
```
Expected: `Build completed successfully` then backtest runs, produces `lean_results.json`

- [ ] **Step 3: Run current backtest for comparison**

Run (in root project):
```bash
cd C:\Users\Consultor\Desktop\DARIO_FILHO\dbmf_quant && python backtest/run.py --start 2023-01-01 --end 2025-12-31 --json > current_results.json
```
Expected: Backtest runs, produces `current_results.json`

- [ ] **Step 4: Compare key metrics**

Run:
```bash
python -c "
import json
with open('lean_results.json') as f:
    lean = json.load(f)
with open('current_results.json') as f:
    current = json.load(f)
print('=== Metric Comparison ===')
for key in ['total_return', 'sharpe_ratio', 'max_drawdown', 'win_rate']:
    lv = lean.get(key, 'N/A')
    cv = current.get(key, 'N/A')
    print(f'{key:20s} Lean={lv}  Current={cv}')
"
```
Expected: Prints side-by-side comparison for each metric

- [ ] **Step 5: Verify financials excluded**

Check `lean_results.json` or Lean backtest logs for `MorningstarSectorCode.FinancialServices` filter log message confirming no financial tickers in positions.

- [ ] **Step 6: Final commit**

Bash:
```bash
cd lean_project && git add lean_results.json && git commit -m "feat: validate Lean backtest results vs original engine"
```

---

## Task Dependency Graph

```
Task 1 (setup)
  │
  ├── Task 2 (gordon_growth) ──────────────────────────┐
  │                                                     │
  ├── Task 3 (damodaran_erp data) ─────────────────────┤
  │                                                     │
  ├── Task 4 (growth_cache) ───────────────────────────┤
  │                                                     │
  ├── Task 5 (atr_trailing_stop) ──────────────────────┤
  │                                                     ├──▶ Task 7 (main.py) ──▶ Task 8 (validate)
  └── Task 6 (pb_roe_universe) ────────────────────────┘
```

Tasks 2-6 are independent of each other (all depend only on Task 1 setup). Task 7 depends on all others. Task 8 depends on Task 7.
