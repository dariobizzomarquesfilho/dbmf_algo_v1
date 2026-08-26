"""Bootstrap equity CSV.zip files from embedded bars.

Lean's backtest time loop only advances on data events. The Docker container
has NO external .csv.zip files (Lean CLI packages only .py files), so
AddEquity subscriptions produce no bars and scheduled events never fire.

This module writes the embedded equity bars to the standard QC data folder
(data/equity/usa/daily/<ticker>.csv.zip) on startup, so the DefaultDataProvider
finds them and the backtest runs normally.

Call bootstrap() in Initialize() BEFORE AddEquity.
"""

from __future__ import annotations

import csv
import os
import sys
import zipfile
from io import StringIO
from pathlib import Path

from data.equity_bars import load_equity_bars


def _find_data_dirs(base_dir: str = None) -> list:
    """Enumerate candidate Lean data folders.

    The engine's data root may be:
    - the algorithm's own project data dir (bind-mounted from host),
      e.g. /LeanCLI/data
    - cwd/data
    - the baked-in /Lean/Data (image COPY ./Lean/Data/ /Lean/Data/)

    Write to every candidate so the engine's data root is covered.
    """
    base = Path(base_dir) if base_dir else Path(os.getcwd())
    candidates = [
        base / "data",
        base / "Data",
        Path("/Lean/Data"),
        Path("/LeanCLI/data"),
        Path("/LeanCLI/Data"),
        Path(__file__).resolve().parent.parent / "data",  # project_root/data
    ]
    seen = set()
    dirs = []
    for c in candidates:
        try:
            r = c.resolve()
        except Exception:
            r = c.absolute()
        if str(r) in seen:
            continue
        seen.add(str(r))
        dirs.append(c)
    return dirs


def bootstrap(base_dir: str = None) -> int:
    """Write embedded equity bars to QC data folder(s). Returns count written."""
    bars = load_equity_bars()
    total_expected = len([t for t, tb in bars.items() if tb])
    written_tickers: set[str] = set()
    written = 0
    for data_dir in _find_data_dirs(base_dir):
        daily_dir = data_dir / "equity" / "usa" / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)

        # QC requires map_files for equity data
        map_dir = data_dir / "equity" / "usa" / "map_files"
        map_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale old-format files (.csv.zip with header row) and orphan
        # .zip for tickers no longer in bars (ghost data from a prior embed).
        for stale in daily_dir.glob("*.csv.zip"):
            try:
                stale.unlink()
            except OSError:
                pass
        # Remove orphan .zip not in current bars
        valid_zips = {t.lower() + ".zip" for t in bars.keys()}
        for existing in daily_dir.glob("*.zip"):
            if existing.name not in valid_zips:
                try:
                    existing.unlink()
                except OSError:
                    pass
        # Remove orphan map_files not in current bars
        valid_maps = {t.lower() + ".csv" for t in bars.keys()}
        for existing in map_dir.glob("*.csv"):
            if existing.name not in valid_maps:
                try:
                    existing.unlink()
                except OSError:
                    pass

        for ticker, ticker_bars in bars.items():
            if not ticker_bars:
                continue
            # QC resolves daily equity as lowercase <ticker>.zip (no .csv suffix)
            ticker_lc = ticker.lower()
            zip_path = daily_dir / f"{ticker_lc}.zip"
            try:
                _write_csv_zip(zip_path, ticker_lc, ticker_bars)
                _write_map_file(map_dir / f"{ticker_lc}.csv", ticker_lc, ticker_bars)
                written += 1
                written_tickers.add(ticker_lc)
            except (TypeError, ValueError, OverflowError, OSError):
                continue
    # Return unique tickers actually written (de-duplicated), not per-dir inflated count
    return len(written_tickers) if written_tickers else total_expected


def _write_csv_zip(zip_path: Path, ticker: str, ticker_bars: dict) -> None:
    """Write a QC-style daily equity CSV.zip for one ticker.

    Matches Lean's native format exactly (see Lean/Data/equity/usa/daily/aapl.zip):
    - outer zip: <ticker>.zip (lowercase)
    - inner csv: <ticker>.csv
    - NO header row
    - columns: date time, open, high, low, close, volume
      where date = YYYYMMDD, time = "00:00"
    """
    import math
    buf = StringIO()
    # QuantConnect equity daily CSVs store OHLC pre-multiplied by 10000 (4 decimals
    # of dollar precision); Lean divides by _scaleFactor=1/10000 on read via
    # TradeBar.ParseEquity. Writing raw yfinance values would fill orders 10000x too
    # cheap. The embedded equity_bars module stays unscaled (used by SetMarketPrice);
    # only the on-disk .zip Lean reads is scaled.
    for date_str in sorted(ticker_bars.keys()):
        b = ticker_bars[date_str]
        try:
            ov = float(b.get("open", 0))
            hv = float(b.get("high", 0))
            lv = float(b.get("low", 0))
            cv = float(b.get("close", 0))
            vv = b.get("volume", 0)
            if not all(math.isfinite(v) for v in (ov, hv, lv, cv)):
                continue
            if any(v <= 0 for v in (ov, hv, lv, cv)):
                # Skip zero/negative OHLC (halted/delisted bad print) rather
                # than writing a bar that Lean would treat as valid.
                continue
            vv_int = int(float(vv)) if vv is not None else 0
            if not math.isfinite(vv_int):
                vv_int = 0
        except (TypeError, ValueError):
            continue
        time_str = date_str.replace("-", "")
        o = int(round(ov * 10000))
        h = int(round(hv * 10000))
        l = int(round(lv * 10000))
        c = int(round(cv * 10000))
        buf.write(f"{time_str} 00:00,{o},{h},{l},{c},{vv_int}\n")
    csv_content = buf.getvalue()
    # Atomic write: write to temp then replace
    tmp = zip_path.with_suffix(".tmp.zip")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{ticker}.csv", csv_content)
    tmp.replace(zip_path)


def _write_map_file(map_path: Path, ticker: str, ticker_bars: dict) -> None:
    """Write a QC map file for the ticker (see map_files/aapl.csv).

    Format: <YYYYMMDD>,<fromTicker>,<toTicker>. We never rename,
    so write a valid identity mapping (start/end -> ticker). The previous
    ",Q" rows were malformed and could mis-map the symbol.
    """
    dates = sorted(ticker_bars.keys())
    if not dates:
        return
    start = dates[0].replace("-", "")
    end = dates[-1].replace("-", "")
    # Deduplicate single-bar ticker (start==end)
    if start == end:
        content = f"{start},{ticker},{ticker}\n"
    else:
        content = f"{start},{ticker},{ticker}\n{end},{ticker},{ticker}\n"
    map_path.write_text(content, encoding="utf-8")
