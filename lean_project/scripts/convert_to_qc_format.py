"""Convert yfinance JSON bars to QuantConnect Lean native CSV.zip format.

Creates data/equity/usa/daily/<ticker>.zip files from equity_bars.json.
Each inner CSV has columns: date time, open, high, low, close, volume
(no header row, matching Lean's native format).
"""

from __future__ import annotations

import csv
import json
import os
import sys
import zipfile
from io import StringIO
from pathlib import Path


def convert(bars_json_path: str, output_dir: str):
    """Convert equity_bars.json to QC's daily equity CSV.zip format."""
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    with open(bars_json_path, "r", encoding="utf-8") as f:
        bars = json.load(f)

    print(f"Converting {len(bars)} tickers to QC format in {out_dir}")

    for i, (ticker, ticker_bars) in enumerate(bars.items(), 1):
        if i % 20 == 0:
            print(f"  Converted {i}/{len(bars)} tickers...", file=sys.stderr)

        buf = StringIO()
        # QuantConnect equity daily CSVs store OHLC pre-multiplied by 10000 (4
        # decimals of dollar precision); Lean divides by 1/10000 on read via
        # TradeBar.ParseEquity. Write scaled ints so fills/pricing are correct.
        for date_str in sorted(ticker_bars.keys()):
            b = ticker_bars[date_str]
            time_str = date_str.replace("-", "")
            o = int(round(float(b["open"]) * 10000))
            h = int(round(float(b["high"]) * 10000))
            l = int(round(float(b["low"]) * 10000))
            c = int(round(float(b["close"]) * 10000))
            buf.write(f"{time_str} 00:00,{o},{h},{l},{c},{int(b.get('volume', 0))}\n")

        csv_content = buf.getvalue()
        zip_path = out_dir / f"{ticker.lower()}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(f"{ticker.lower()}.csv", csv_content)

    print(f"Done. Created {len(bars)} .zip files in {out_dir}")


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent / "data"
    convert(
        str(base / "equity_bars.json"),
        str(base / "equity" / "usa" / "daily"),
    )
