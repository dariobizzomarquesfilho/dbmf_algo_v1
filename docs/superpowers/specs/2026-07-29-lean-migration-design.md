# Design: Lean Migration (Approach A - Minimal Algorithm)

## Context
Migrate dbmf_quant's custom backtest engine to QuantConnect Lean framework. Current engine: P/B vs ROE screening (2-stage Gordon Growth) + ATR trailing stops on S&P 500. Goal: professional-grade engine, live trading path, fix ticker format bugs, exclude financials.

## Scope
**Approach A: Minimal Algorithm** - Single `QCAlgorithm` subclass with Fine universe filter. No Alpha Model framework.

## Architecture

```
lean_project/
├── main.py                          # QCAlgorithm: Initialize, OnData, OnSecuritiesChanged
├── data/
│   └── damodaran_erp.py             # PythonData reader for july26.json
├── universe/
│   └── pb_roe_universe.py           # Fine filter: exclude financials, rank by gap
├── valuation/
│   └── gordon_growth.py             # 2-stage intrinsic P/B (ported from backtest/)
├── indicators/
│   └── atr_trailing_stop.py         # ATR trailing stop logic (ported from vol_trail_stop/)
├── config.json                      # Lean CLI config (dates, cash, data folder)
└── scripts/
    └── compute_growth_cache.py      # Offline yfinance EPS CAGR cache generator
```

## Components

### 1. Core Algorithm (`main.py`)
- `Initialize()`: SetStartDate/EndDate/Cash, QC500 universe, IB brokerage model, DamodaranERP custom data, ATR indicators dict, trailing stops dict, daily Schedule.On for stop checks
- `FineSelection()`: Filter QC500 fine fundamentals → exclude financials (MorningstarSectorCode.FinancialServices) → require valid P/B, ROE, Beta, EPS → compute implied P/B via 2-stage Gordon Growth → rank by gap_pct → return top N symbols
- `OnSecuritiesChanged()`: Liquidate removed, initialize ATR for added, SetHoldings equal-weight (1/max_positions)
- `CheckAtrStops()`: Daily scheduled - update ATR indicators, calculate trailing stops, Liquidate if price <= stop
- `OnData()`: Handle DamodaranERP slice, update ERP cache

### 2. Custom Data: DamodaranERP (`data/damodaran_erp.py`)
- `PythonData` subclass reading `data/damodaran_erp.json` (country → ERP map)
- `GetSource()`: Local file subscription
- `Reader()`: Parse JSON, return objects with country, erp, date
- Registered via `AddData(DamodaranERP, "DAMODARAN_ERP", Resolution.Daily)`

### 3. Universe Filter (`universe/pb_roe_universe.py`)
- Fine selection logic extracted from FineSelection method
- Inputs: Morningstar P/B, ROE, Beta; growth_cache (EPS CAGR); rf_cache (^TNX); erp_cache (Damodaran)
- Output: Ranked symbols by gap_pct = (implied_pb - actual_pb) / actual_pb

### 4. Valuation (`valuation/gordon_growth.py`)
- Port `intrinsic_pb_2stage()` from `backtest/valuation.py` unchanged
- 2-stage: 5-year linear decline g_start → g_term, then perpetuity
- Inputs: roe, g_start, g_term, r
- Validation: r > g_term

### 5. ATR Trailing Stop (`indicators/atr_trailing_stop.py`)
- Port logic from `vol_trail_stop.vol_trail_stop.atr_trail_stop()`
- Use Lean's built-in `ATR` indicator + custom trailing stop calculation
- Smoothing: SMA (match current)

### 6. Growth Cache (`scripts/compute_growth_cache.py`)
- Offline script: download 2-year EPS history via yfinance for all QC500 tickers
- Compute CAGR, cap at 50%, floor at 0%
- Output: `data/growth_cache.json` (ticker → g_start)
- Run monthly, commit JSON, no yfinance calls during backtest

## Data Sources

| Data | Source | Frequency |
|------|--------|-----------|
| Price/Volume | QuantConnect (US Equity Daily) | Daily |
| P/B, ROE, Beta, Sector | Morningstar (Fine Fundamental) | Daily |
| ERP (Country Risk Premium) | DamodaranERP custom data | Quarterly (manual update) |
| Risk-Free Rate (^TNX) | QuantConnect history / custom | Daily |
| EPS CAGR (g_start) | yfinance (offline cache) | Monthly refresh |

## Fees & Taxes
- **Brokerage**: `SetBrokerageModel(BrokerageName.InteractiveBrokersBrokerage, AccountType.Margin)` - includes commissions, margin, fees
- **Taxes**: Default US citizen treatment (capital gains only). Portuguese tax (NHR, W-8BEN, 15% dividend withholding, 28% flat CGT) → future feature.

## Universe
- **Primary**: `QC500UniverseSelectionModel()` - QuantConnect's S&P 500 proxy (top 500 by dollar volume + fundamentals)
- Auto-reconstitutes monthly, uses Morningstar fundamentals
- Fine filter adds: exclude financials, rank by Gordon Growth gap
- **Future**: Exact historical S&P 500 via custom universe if needed

## Rebalancing
- **Event-driven**: On ATR stop hit → Liquidate → OnSecuritiesChanged fills gap with next best from FineSelection
- Equal-weight: `SetHoldings(symbol, 1/max_positions)`

## Metrics
- Built-in: Sharpe, Sortino, Max Drawdown, CAGR, Win Rate, Profit Factor
- Custom: Add via `OnEndOfAlgorithm()` if needed

## Migration Mapping

| Current (backtest/) | Lean Equivalent |
|---------------------|-----------------|
| `engine.py` daily loop | `OnData()` + `Schedule.On()` + `OnSecuritiesChanged()` |
| `data.py` yfinance + parquet | QC data + `History()` |
| `strategy.py` screen_ticker/rank | `FineSelection()` |
| `portfolio.py` | `Portfolio` + `SetHoldings()` |
| `valuation.py` | `valuation/gordon_growth.py` (ported) |
| `vol_trail_stop` | `indicators/atr_trailing_stop.py` + Lean `ATR` |
| `sp500.py` | `QC500UniverseSelectionModel()` |
| `metrics.py` | Built-in + custom |

## Acceptance Criteria
1. `lean backtest "PbRoeAtr"` runs successfully in Docker
2. Results comparable to current `python backtest/run.py` (same period, similar metrics)
3. Financials excluded from universe
4. ATR trailing stops trigger correctly
5. Damodaran ERP custom data loads and used in valuation
6. Growth cache loads and used for g_start
7. IB fee model applied
8. Output: equity curve, trades, metrics JSON
9. No quarterly rebalancing (deferred to future)

## Rebalancing Note
- Event-driven only (currently). Quarterly rebalance scheduled for future addition only.

## Out of Scope
- Alpha Model framework
- Portfolio Construction / Risk / Execution models
- Multi-asset (crypto, forex, futures)
- Live trading deployment
- Portuguese tax modeling
- Exact historical S&P 500 constituents (use QC500 proxy)
- Quarterly rebalancing (deferred)