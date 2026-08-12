"""Pre-download S&P 500 equity data from yfinance for Lean backtesting.

Downloads:
1. Daily OHLCV bars (config.DATA_START warm-up window to config.BACKTEST_END)
   → data/equity_bars.json
2. Snapshot fundamental data per ticker → data/fundamentals.json
3. Quarterly fundamentals history → data/fundamentals_history.json (yfinance, ~7 quarters)

Usage:
    python scripts/download_equity_data.py
    python scripts/download_equity_data.py --tickers AAPL MSFT GOOG
    python scripts/download_equity_data.py --history-only  # only download quarterly history
    python scripts/download_equity_data.py --refresh-sp500  # re-download S&P 500 list
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import yfinance as yf

# Add repo root to path so `import config` works (config/ is at repo root)
_SCRIPT_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _SCRIPT_DIR.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
import config  # loads .env and sets edgar identity

# Add lean_project to path so `from data.sp500_data import ...` works
_LEAN_PROJECT = _SCRIPT_DIR.parent
if str(_LEAN_PROJECT) not in sys.path:
    sys.path.insert(0, str(_LEAN_PROJECT))
from data.sp500_data import load_sp500_membership, clip_to_membership

# Fetch horizon is decoupled from the backtest window: we pull every CSV ticker
# from HISTORY_START (earliest membership start, unless BACKTEST_HISTORY_START
# overrides) through BACKTEST_END. This retains pre-2019 historical constituents
# for longer-window backtests later. DATA_START stays only for the coverage guard.
BACKTEST_START = config.HISTORY_START
BACKTEST_END = config.BACKTEST_END

_DATA_DIR = _SCRIPT_DIR.parent / "data"
_SP500_CSV = _DATA_DIR / "sp500_ticker_start_end.csv"
_SP500_GITHUB_URL = "https://raw.githubusercontent.com/fja05680/sp500/master/sp500_ticker_start_end.csv"


def _refresh_sp500_csv() -> None:
    """Re-download sp500_ticker_start_end.csv from GitHub."""
    import urllib.request
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(_SP500_GITHUB_URL, timeout=30) as resp:
        data = resp.read().decode("utf-8")
    _SP500_CSV.write_text(data, encoding="utf-8")


def get_sp500_tickers(refresh: bool = False) -> list:
    """Return full S&P 500 tickers plus ^TNX and ^GSPC for risk-free rate and beta.

    Loads from local ``sp500_ticker_start_end.csv`` by default.
    Pass ``refresh=True`` to re-download from GitHub first.
    Falls back to a hardcoded subset if the local CSV is missing/corrupt.
    """
    if refresh or not _SP500_CSV.exists():
        try:
            _refresh_sp500_csv()
        except Exception:
            pass

    try:
        import csv
        membership = {}
        with open(_SP500_CSV, newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                ticker = row["ticker"].strip()
                if not ticker:
                    continue
                start = row["start_date"].strip()
                end = row["end_date"].strip() or None
                membership.setdefault(ticker, []).append((start, end))
        tickers = sorted(membership.keys())
        tickers.append("^TNX")
        tickers.append("^GSPC")
        return tickers
    except Exception:
        pass

    # Fallback: hardcoded subset
    return [
        "AAPL", "MSFT", "AMZN", "GOOGL", "META", "TSLA", "BRK-B", "JNJ",
        "JPM", "V", "PG", "UNH", "HD", "MA", "NVDA", "DIS", "PYPL",
        "NFLX", "ADBE", "CRM", "CMCSA", "PEP", "KO", "ABT", "CSCO",
        "PFE", "TMO", "COST", "AVGO", "ACN", "TXN", "LOW", "NEE", "UPS",
        "QCOM", "IBM", "AMD", "INTC", "NOW", "MDLZ", "ADP", "T", "VZ",
        "CL", "LLY", "SBUX", "MCD", "CAT", "DE", "AXP", "GS", "BLK",
        "SPGI", "ICE", "TGT", "MU", "LRCX", "AMAT", "KLAC", "SNPS", "CDNS",
        "FTNT", "PANW", "GILD", "BKNG", "ISRG", "EA", "TTWO",
        "CMG", "MNST", "CSGP", "KMX", "CTAS", "STZ", "PAYC",
        "^GSPC",
        "^TNX",
    ]


def _extract_bars(data, ticker: str) -> pd.DataFrame:
    """Extract OHLCV DataFrame for a single ticker from yfinance download.
    Handles both flat and MultiIndex column formats (yfinance 1.x).
    """
    if isinstance(data.columns, pd.MultiIndex):
        try:
            return data.xs(ticker, axis=1, level=1)
        except KeyError:
            # ticker not in this data
            return pd.DataFrame()
    return data


def _row_to_bar(row) -> dict:
    """Convert a DataFrame row to a bar dict."""
    return {
        "open": float(row.get("Open", 0)),
        "high": float(row.get("High", 0)),
        "low": float(row.get("Low", 0)),
        "close": float(row.get("Close", 0)),
        "volume": float(row.get("Volume", 0)),
    }


def download_daily_bars(tickers: list, output_path: str, membership=None, end_default=None):
    """Download daily OHLCV bars for each ticker and save as JSON.

    Bars fetched with ``auto_adjust=True`` (adjusted) for a stable, continuous
    return series across splits. If ``membership`` is supplied, bars are clipped
    to the union of each ticker's membership intervals before writing.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    bars = {}
    total = len(tickers)
    errored = []  # genuine exceptions (network/rate-limit) — real problem
    empty = []    # no data returned (typically delisted — expected)

    for i, ticker in enumerate(tickers, 1):
        if i % 25 == 0:
            print(f"  Downloaded {i}/{total} tickers...", file=sys.stderr)

        try:
            data = yf.download(
                ticker,
                start=BACKTEST_START,
                end=BACKTEST_END,
                progress=False,
                threads=False,
                auto_adjust=True,
            )
            if data is None or data.empty:
                empty.append(ticker)
                continue
            ticker_data = _extract_bars(data, ticker)
            if ticker_data.empty:
                empty.append(ticker)
                continue
            ticker_bars = {}
            for date, row in ticker_data.iterrows():
                date_str = date.strftime("%Y-%m-%d")
                ticker_bars[date_str] = _row_to_bar(row)
            if ticker_bars:
                bars[ticker] = ticker_bars
            else:
                empty.append(ticker)
        except Exception as e:
            print(f"  Warn: {ticker} failed: {e}", file=sys.stderr)
            errored.append(ticker)

    if membership is not None:
        bars = clip_to_membership(bars, membership, end_default or BACKTEST_END)

    # Safety: back up any existing file before overwriting. The full run is
    # large and an interruption/partial failure would otherwise destroy data.
    if out.exists():
        try:
            bak = out.with_name(out.stem + ".bak.json")
            shutil.copy2(out, bak)
            print(f"Backed up existing {out.name} -> {bak.name}")
        except Exception as e:
            print(f"WARN: could not back up {out}: {e}")

    with open(out, "w", encoding="utf-8") as f:
        json.dump(bars, f, indent=2)
    print(f"Equity bars saved: {out} ({len(bars)} tickers)")
    if empty:
        print(
            f"INFO: {len(empty)}/{total} tickers returned no bars "
            f"(likely delisted/defunct — expected for historical membership).",
            file=sys.stderr,
        )

    if errored:
        print(
            f"WARN: {len(errored)}/{total} tickers errored "
            f"(rate-limited/interrupted?). Backtest window may be incomplete. "
            f"Errors: {', '.join(errored[:20])}"
            + (" ..." if len(errored) > 20 else ""),
            file=sys.stderr,
        )


def download_fundamentals(tickers: list, output_path: str):
    """Download fundamental snapshot for each ticker and save as JSON."""
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    fundamentals = {}
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        if i % 25 == 0:
            print(f"  Fundamentals {i}/{total} tickers...", file=sys.stderr)

        try:
            stock = yf.Ticker(ticker)
            info = stock.info

            pb = info.get("priceToBook")
            roe = info.get("returnOnEquity")
            eps = info.get("trailingEps")
            market_cap = info.get("marketCap")
            dollar_volume = info.get("fiveDayAverageVolume")
            name = info.get("shortName")
            sector = info.get("sector")
            industry = info.get("industry")
            price = info.get("currentPrice")
            book_value = info.get("bookValue")
            revenue = info.get("totalRevenue")
            net_income = info.get("netIncomeToCommon")

            # Skip if we can't get price
            if price is None or price <= 0:
                continue

            # Store tickers even if bookValue/priceToBook are invalid
            # (yfinance returns negative values for ~33 S&P 500 tickers).
            # The screening logic will skip these per-screen — they may
            # have valid bookValue in a future quarter after refresh.
            # P/B is always computed dynamically as current_price / book_value
            # at screen time; never from a stale priceToBook snapshot.
            if pb is None or pb <= 0:
                if book_value is not None and book_value > 0:
                    pb = price / book_value

            fundamentals[ticker] = {
                "name": name,
                "sector": sector,
                "industry": industry,
                "price": price,
                "book_value": book_value,
                "pb": pb,
                "roe": roe,
                "eps": eps,
                "market_cap": market_cap,
                "dollar_volume": dollar_volume,
                "revenue": revenue,
                "net_income": net_income,
            }
        except Exception as e:
            print(f"  Warn: {ticker} fundamentals failed: {e}", file=sys.stderr)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(fundamentals, f, indent=2)
    print(f"Fundamentals saved: {out} ({len(fundamentals)} tickers)")


def _row_get(df, col, keys):
    """Return float value of first matching row key at column ``col``, else None."""
    for k in keys:
        if k in df.index:
            try:
                return float(df.loc[k, col])
            except (KeyError, TypeError, ValueError):
                return None
    return None


def download_fundamentals_history(tickers: list, output_path: str):
    """Download per-ticker quarterly book_value/roe/eps snapshot series.

    book_value = common stock equity / shares outstanding (per quarter).
    roe = quarterly net income / common stock equity.
    eps = diluted EPS (falls back to basic EPS).
    One entry per quarter-end the ticker reported, so cadence is per-company.

    Note: yfinance only returns the most recent ~7 quarters per ticker.
    For point-in-time screening covering 2023-2025, ensure the download
    is run with sufficient historical data available. Re-run periodically
    as new quarterly reports become available.
    """
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    hist = {}
    total = len(tickers)

    for i, ticker in enumerate(tickers, 1):
        if i % 25 == 0:
            print(f"  History {i}/{total} tickers...", file=sys.stderr)

        try:
            stock = yf.Ticker(ticker)
            bs = stock.quarterly_balance_sheet
            fin = stock.quarterly_financials
            if bs is None or bs.empty or fin is None or fin.empty:
                continue
            try:
                shares = stock.get_shares_full()
            except Exception:
                shares = None

            quarters = {}
            for col in bs.columns:
                date_str = col.strftime("%Y-%m-%d")
                eq = _row_get(bs, col, ("Common Stock Equity", "Stockholders Equity"))
                if eq is None:
                    continue

                book_value = None
                if shares is not None and not shares.empty:
                    shares_idx = shares.index.tz_localize(None) if hasattr(shares.index, "tz") and shares.index.tz is not None else shares.index
                    prior = shares[shares_idx <= col]
                    if not prior.empty:
                        book_value = eq / float(prior.iloc[-1])

                roe = None
                if eq > 0:
                    ni = _row_get(fin, col, ("Net Income",))
                    if ni is not None:
                        roe = ni / eq

                eps = _row_get(fin, col, ("Diluted EPS", "Basic EPS"))

                # Skip entries where all values are missing or NaN
                def _valid(v):
                    return v is not None and not (isinstance(v, float) and math.isnan(v))

                if not any(_valid(v) for v in [book_value, roe, eps]):
                    continue
                quarters[date_str] = {
                    "book_value": book_value if _valid(book_value) else None,
                    "roe": roe if _valid(roe) else None,
                    "eps": eps if _valid(eps) else None,
                }

            if quarters:
                hist[ticker] = quarters
        except Exception as e:
            print(f"  Warn: {ticker} history failed: {e}", file=sys.stderr)

    with open(out, "w", encoding="utf-8") as f:
        json.dump(hist, f, indent=2)
    print(f"Fundamentals history saved: {out} ({len(hist)} tickers)")


def main():
    parser = argparse.ArgumentParser(description="Download S&P 500 equity data for Lean backtesting")
    parser.add_argument("--tickers", type=str, nargs="+", help="Specific tickers to download")
    parser.add_argument("--bars-only", action="store_true", help="Only download bars, skip fundamentals")
    parser.add_argument("--fundamentals-only", action="store_true", help="Only download fundamentals, skip bars")
    parser.add_argument("--history-only", action="store_true", help="Only download fundamentals history, skip bars and snapshot")
    parser.add_argument("--refresh-sp500", action="store_true", help="Re-download S&P 500 list from GitHub")
    script_dir = Path(__file__).resolve().parent.parent / "data"
    parser.add_argument("--bars-path", type=str, default=str(script_dir / "equity_bars.json"))
    parser.add_argument("--fundamentals-path", type=str, default=str(script_dir / "fundamentals.json"))
    parser.add_argument("--history-path", type=str, default=str(script_dir / "fundamentals_history.json"))
    parser.add_argument("--start-date", type=str, default=BACKTEST_START)
    parser.add_argument("--end-date", type=str, default=BACKTEST_END)
    args = parser.parse_args()

    tickers = args.tickers if args.tickers else get_sp500_tickers(refresh=args.refresh_sp500)
    # Deduplicate while preserving order
    seen = set()
    tickers = [x for x in tickers if not (x in seen or seen.add(x))]

    print(f"Downloading data for {len(tickers)} tickers...")
    print(f"Period: {BACKTEST_START} to {BACKTEST_END}")

    membership = load_sp500_membership(str(_SP500_CSV))

    if not args.fundamentals_only:
        download_daily_bars(
            tickers, args.bars_path, membership=membership, end_default=BACKTEST_END
        )

    if not args.bars_only:
        download_fundamentals(tickers, args.fundamentals_path)

    if args.history_only or (not args.bars_only and not args.fundamentals_only):
        download_fundamentals_history(tickers, args.history_path)

    print("Done.")


if __name__ == "__main__":
    main()