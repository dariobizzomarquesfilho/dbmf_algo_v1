"""Generate the equity-bar exclusion report (missing-data.txt / .json).

Scans the current equity_bars.json + membership + unavailable/throttled
artifacts and writes a consolidated record of every ticker that will NOT be
part of the backtest, for later documentation / disclaimer.

Usage (from lean_project):
    python scripts/track_exclusions.py
    python scripts/track_exclusions.py --bars-path data/equity_bars.json
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make the repo root importable (config) and lean_project importable (data.*).
_SCRIPT_DIR = Path(__file__).resolve().parent
_LEAN_PROJECT = _SCRIPT_DIR.parent
_REPO_ROOT = _LEAN_PROJECT.parent
for _p in (_REPO_ROOT, _LEAN_PROJECT):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


def main() -> int:
    ap = argparse.ArgumentParser(description="Generate equity-bar exclusion report")
    ap.add_argument(
        "--bars-path",
        default=str(_LEAN_PROJECT / "data" / "equity_bars.json"),
        help="Path to equity_bars.json (default: data/equity_bars.json)",
    )
    ap.add_argument(
        "--data-dir",
        default=str(_LEAN_PROJECT / "data"),
        help="Output dir for missing-data.txt / .json (default: data/)",
    )
    args = ap.parse_args()

    import json

    import config  # noqa: F401  (triggers .env / set_identity)
    from data.exclusions import (
        collect_exclusions,
        load_throttled,
        write_reports,
    )
    from data.sp500_data import load_sp500_membership

    bars_path = Path(args.bars_path)
    if not bars_path.exists():
        print(f"ERROR: bars file not found: {bars_path}", file=sys.stderr)
        return 1

    bars = json.load(open(bars_path, encoding="utf-8"))
    membership = load_sp500_membership(
        str(_LEAN_PROJECT / "data" / "sp500_ticker_start_end.csv")
    )

    unavailable_path = _LEAN_PROJECT / "data" / "equity_unavailable.json"
    throttled_path = _LEAN_PROJECT / "data" / "equity_bars.throttled.txt"
    throttled = load_throttled(str(throttled_path))

    info = collect_exclusions(
        bars,
        membership,
        config.BACKTEST_START,
        config.BACKTEST_END,
        unavailable_path=str(unavailable_path),
        throttled=throttled,
    )

    out = write_reports(
        args.data_dir,
        info,
        len(bars),
        (config.BACKTEST_START, config.BACKTEST_END),
        bars_path.name,
    )
    print(f"Wrote exclusion report: {out}")
    print(
        f"  broken={len(info['broken'])} missing_window={len(info['missing_window'])} "
        f"documented={len(info['documented_unavailable'])} throttled={len(info['throttled'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
