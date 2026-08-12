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
    written = 0
    for data_dir in _find_data_dirs(base_dir):
        daily_dir = data_dir / "equity" / "usa" / "daily"
        daily_dir.mkdir(parents=True, exist_ok=True)

        # QC requires map_files for equity data
        map_dir = data_dir / "equity" / "usa" / "map_files"
        map_dir.mkdir(parents=True, exist_ok=True)

        # Remove stale old-format files (.csv.zip with header row)
        for stale in daily_dir.glob("*.csv.zip"):
            try:
                stale.unlink()
            except OSError:
                pass

        for ticker, ticker_bars in bars.items():
            if not ticker_bars:
                continue
            # QC resolves daily equity as lowercase <ticker>.zip (no .csv suffix)
            ticker_lc = ticker.lower()
            zip_path = daily_dir / f"{ticker_lc}.zip"
            _write_csv_zip(zip_path, ticker_lc, ticker_bars)
            _write_map_file(map_dir / f"{ticker_lc}.csv", ticker_lc, ticker_bars)
            written += 1
    return written


def _write_csv_zip(zip_path: Path, ticker: str, ticker_bars: dict) -> None:
    """Write a QC-style daily equity CSV.zip for one ticker.

    Matches Lean's native format exactly (see Lean/Data/equity/usa/daily/aapl.zip):
    - outer zip: <ticker>.zip (lowercase)
    - inner csv: <ticker>.csv
    - NO header row
    - columns: date time, open, high, low, close, volume
      where date = YYYYMMDD, time = "00:00"
    """
    buf = StringIO()
    for date_str in sorted(ticker_bars.keys()):
        b = ticker_bars[date_str]
        time_str = date_str.replace("-", "")
        buf.write(f"{time_str} 00:00,{b['open']},{b['high']},{b['low']},{b['close']},{int(b.get('volume', 0))}\n")
    csv_content = buf.getvalue()
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr(f"{ticker}.csv", csv_content)


def _write_map_file(map_path: Path, ticker: str, ticker_bars: dict) -> None:
    """Write a QC map file for the ticker (see map_files/aapl.csv).

    Format: <YYYYMMDD>,<ticker>,Q  — first and last bar dates.
    """
    dates = sorted(ticker_bars.keys())
    if not dates:
        return
    start = dates[0].replace("-", "")
    end = dates[-1].replace("-", "")
    content = f"{start},{ticker},Q\n{end},{ticker},Q\n"
    map_path.write_text(content, encoding="utf-8")
