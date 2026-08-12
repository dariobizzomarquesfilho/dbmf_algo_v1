"""Build script: converts JSON data files to embedded Python modules.

Generates data/damodaran_erp.py, data/equity_bars.py, data/fundamentals.py,
data/backtest_config.py, and updates lean.json as self-contained modules
with no external JSON file deps.
"""
from __future__ import annotations

import json
import sys
import zlib
import base64
from datetime import datetime
from pathlib import Path


def embed_json(json_path: str, py_var_name: str, py_out_path: str) -> None:
    """Read JSON file, compress, base64-encode, and write as a Python module."""
    with open(json_path, "rb") as f:
        raw = f.read()
    comp = zlib.compress(raw, 9)
    b64 = base64.b64encode(comp).decode("ascii")

    func_name = f"load_{py_var_name.lower()}"
    const_name = f"_{py_var_name.upper()}_B64"
    lines = [
        '"""Auto-generated embedded data module. Do not edit directly."""',
        "from __future__ import annotations",
        "",
        "import base64",
        "import json",
        "import zlib",
        "",
        f"{const_name} = {repr(b64)}",
        "",
        f"def {func_name}() -> dict:",
        f'    """Decompress and parse embedded JSON data."""',
        f"    data = zlib.decompress(base64.b64decode({const_name}))",
        f"    return json.loads(data.decode('utf-8'))",
        "",
    ]

    out = Path(py_out_path)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {py_out_path} ({len(b64):,} chars b64)")


def embed_small_json(json_path: str, py_var_name: str, py_out_path: str) -> None:
    """For small JSON files, embed directly as a Python dict."""
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    lines = [
        '"""Auto-generated embedded data module. Do not edit directly."""',
        "from __future__ import annotations",
        "",
        f"{py_var_name} = {json.dumps(data, ensure_ascii=False)}",
        "",
    ]

    out = Path(py_out_path)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {py_out_path} ({Path(json_path).stat().st_size:,} bytes embedded)")


def embed_backtest_config(py_out_path: str) -> None:
    """Read BACKTEST_START/END from config and write an embedded module."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import config  # noqa: F401 — triggers set_identity()

    lines = [
        '"""Auto-generated embedded backtest window. Do not edit directly."""',
        "from __future__ import annotations",
        "",
        f"BACKTEST_START = {repr(config.BACKTEST_START)}",
        f"BACKTEST_END = {repr(config.BACKTEST_END)}",
        "",
        "def load_backtest_window() -> dict:",
        '    """Return the configured backtest window."""',
        "    return {\"start\": BACKTEST_START, \"end\": BACKTEST_END}",
        "",
    ]

    out = Path(py_out_path)
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {py_out_path}")


def embed_lean_json(lean_json_path: str) -> None:
    """Update lean.json start-date and end-date from config BACKTEST_START/END."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import config  # noqa: F401 — triggers set_identity()

    path = Path(lean_json_path)
    if not path.exists():
        print(f"Skip lean.json (not found: {path})")
        return

    with open(path, "r", encoding="utf-8") as f:
        lean = json.load(f)

    lean["start-date"] = config.BACKTEST_START
    lean["end-date"] = config.BACKTEST_END

    with open(path, "w", encoding="utf-8") as f:
        json.dump(lean, f, indent=4)
        f.write("\n")

    print(f"Updated {path} (start={config.BACKTEST_START}, end={config.BACKTEST_END})")


def _min_max_dates(bars: dict) -> tuple[Optional[str], Optional[str]]:
    """Return (earliest, latest) bar date across all tickers."""
    seen = set()
    for ticker_bars in bars.values():
        seen.update(ticker_bars.keys())
    if not seen:
        return None, None
    return min(seen), max(seen)


def validate_data_coverage(
    equity_bars_path: str,
    fundamentals_path: Optional[str] = None,
    membership_csv_path: Optional[str] = None,
) -> None:
    """Guard that embedded data actually covers the configured window.

    Equity bars HARD FAIL if they don't span [config.DATA_START,
    config.BACKTEST_END] (warm-up + full window). Fundamentals history only
    WARNS (its coverage gap is a separate, out-of-scope concern) but must not
    block embedding. A second pass asserts every CURRENT S&P 500 member
    (end_date None) has bars spanning [DATA_START, BACKTEST_END].
    """
    repo_root = Path(__file__).resolve().parent.parent.parent
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    import config  # noqa: F401 — triggers set_identity()

    # lean_project dir (for data.sp500_data membership helpers)
    lean_project = Path(__file__).resolve().parent.parent
    if str(lean_project) not in sys.path:
        sys.path.insert(0, str(lean_project))
    from data.sp500_data import load_sp500_membership  # noqa: E402

    print("Validating data coverage against configured window...")
    print(f"  Required range: {config.DATA_START} (warm-up) .. {config.BACKTEST_END}")

    # --- Equity bars: HARD FAIL ---
    try:
        with open(equity_bars_path, "r", encoding="utf-8") as f:
            bars = json.load(f)
    except FileNotFoundError:
        print(f"ERROR: equity bars file not found: {equity_bars_path}")
        sys.exit(1)

    earliest, latest = _min_max_dates(bars)
    if earliest is None:
        print("ERROR: equity_bars.json contains no bars")
        sys.exit(1)

    if earliest > config.DATA_START:
        print(
            f"ERROR: equity bars start at {earliest} but warm-up requires "
            f"<= {config.DATA_START}. Re-run download_equity_data.py."
        )
        sys.exit(1)

    # End check: BACKTEST_END may be a non-trading day (weekend/holiday), so
    # tolerate the last bar being within a few calendar days of BACKTEST_END.
    _end_diff = (
        datetime.strptime(config.BACKTEST_END, "%Y-%m-%d").date()
        - datetime.strptime(latest, "%Y-%m-%d").date()
    ).days
    if _end_diff > 4:
        print(
            f"ERROR: equity bars end at {latest} but backtest end requires "
            f">= {config.BACKTEST_END} (gap of {_end_diff} days). "
            f"Re-run download_equity_data.py."
        )
        sys.exit(1)
    print(f"  Equity bars OK: {earliest} .. {latest} ({len(bars)} tickers)")

    # --- Per-current-member guard (second pass) ---------------------------------
    # Every ticker that is still an S&P 500 member today (end_date None) MUST have
    # bars spanning [DATA_START, BACKTEST_END]. This turns partially-throttled
    # downloads (e.g. BK, CMA) into a HARD failure instead of a silent gap.
    csv_path = (
        Path(membership_csv_path)
        if membership_csv_path
        else lean_project / "data" / "sp500_ticker_start_end.csv"
    )
    if not csv_path.exists():
        print(f"WARN: membership CSV not found at {csv_path}; skipping per-member check")
    else:
        membership = load_sp500_membership(str(csv_path))
        data_start = config.DATA_START
        end_diff_max = 4  # tolerate non-trading final day (weekend/holiday)
        missing_current = []
        for ticker, intervals in membership.items():
            if not any(e is None for _s, e in intervals):
                continue  # not a current member
            tb = bars.get(ticker)
            if not tb:
                missing_current.append((ticker, "no bars at all"))
                continue
            dates = sorted(tb.keys())
            if dates[0] > data_start:
                missing_current.append((ticker, f"starts {dates[0]} > {data_start}"))
            if (datetime.strptime(config.BACKTEST_END, "%Y-%m-%d").date()
                    - datetime.strptime(dates[-1], "%Y-%m-%d").date()).days > end_diff_max:
                missing_current.append((ticker, f"ends {dates[-1]} < {config.BACKTEST_END}"))
        if missing_current:
            print(
                "ERROR: the following CURRENT S&P 500 members are missing or "
                "out-of-range in equity_bars.json (re-run download/repair):"
            )
            for ticker, reason in missing_current:
                print(f"  {ticker}: {reason}")
            sys.exit(1)
        print(f"  Per-current-member check OK ({len([t for t, _ in membership.items() if any(e is None for _, e in membership[t])])} current members covered)")

    # --- Fundamentals history: WARN ONLY ---
    if fundamentals_path and Path(fundamentals_path).exists():
        try:
            with open(fundamentals_path, "r", encoding="utf-8") as f:
                fh = json.load(f)
            n = len(fh)
            print(f"  Fundamentals history: {n} tickers embedded")
            if n < 10:
                print(
                    f"WARN: fundamentals_history covers only {n} symbols; "
                    f"the tradeable universe will be limited. This is a known "
                    f"out-of-scope gap — embedding proceeds."
                )
        except Exception as e:
            print(f"WARN: could not validate fundamentals_history: {e}")


if __name__ == "__main__":
    base = Path(__file__).resolve().parent.parent / "data"
    lean_json_path = Path(__file__).resolve().parent.parent / "lean.json"

    # Backtest window (single source of truth from config/.env)
    embed_backtest_config(str(base / "backtest_config.py"))

    # Update lean.json dates to match config
    embed_lean_json(str(lean_json_path))

    # Guard: fail fast on stale/missing equity coverage before embedding
    validate_data_coverage(
        str(base / "equity_bars.json"),
        str(base / "fundamentals_history.json"),
    )

    # Large files: compress + base64
    embed_json(str(base / "equity_bars.json"), "EQUITY_BARS", str(base / "equity_bars.py"))

    # Medium files: compress + base64
    embed_json(str(base / "damodaran_erp.json"), "DAMODARAN_ERP", str(base / "damodaran_erp_json.py"))
    if (base / "fundamentals_history.json").exists():
        embed_json(str(base / "fundamentals_history.json"), "FUNDAMENTALS_HISTORY", str(base / "fundamentals_history.py"))
    else:
        print("Skip fundamentals_history.json (not yet downloaded)")

    # ERP history (PIT series for Lean backtest)
    if (base / "damodaran_erp_history.json").exists():
        embed_json(
            str(base / "damodaran_erp_history.json"),
            "DAMODARAN_ERP_HISTORY",
            str(base / "damodaran_erp_history.py"),
        )
    else:
        print("Skip damodaran_erp_history.json (not yet built)")
