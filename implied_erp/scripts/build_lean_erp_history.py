"""Consolidate per-period ERP JSONs into a Lean-compatible PIT history file.

Reads all erp_*.json files from implied_erp/data/erp/ and writes a compact
lean_project/data/damodaran_erp_history.json containing only the US ERP
and mature-market ERP per date.  This is what gets embedded into Lean.

Usage:
    python build_lean_erp_history.py
    python build_lean_erp_history.py --erp-dir implied_erp/data/erp
    python build_lean_erp_history.py --out lean_project/data/damodaran_erp_history.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent.parent
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

DEFAULT_ERP_DIR = Path(__file__).resolve().parent.parent / "data" / "erp"
DEFAULT_OUT = _repo_root / "lean_project" / "data" / "damodaran_erp_history.json"


def _us_erp_for(period: dict) -> float | None:
    """Extract the US ERP from a single period's data.

    Mirrors the get_erp fallback chain:
      1. metadata us_erp field
      2. countries["United States" or "United States of America"]
         total_equity_risk_premium2 (preferred) or total_equity_risk_premium
      3. mature_market_erp
    """
    us_erp = period.get("us_erp")
    if us_erp is not None:
        return us_erp

    countries = period.get("countries", {})
    # Try both key variants used across different Damodaran file versions
    for us_key in ("United States", "United States of America"):
        us = countries.get(us_key)
        if isinstance(us, dict):
            # Prefer total_equity_risk_premium2 (more recent/accurate)
            for field in ("total_equity_risk_premium2", "total_equity_risk_premium"):
                val = us.get(field)
                if val is not None and isinstance(val, (int, float)):
                    return float(val)

    return period.get("mature_market_erp")


def build(erp_dir: Path) -> dict:
    """Read all erp_*.json files and return the consolidated history dict."""
    if not erp_dir.exists():
        raise FileNotFoundError(f"ERP dir does not exist: {erp_dir}")

    history: dict[str, dict] = {}

    for f in sorted(erp_dir.glob("erp_*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[warn] skipping {f.name}: {e}", file=sys.stderr)
            continue

        # Date key from filename: erp_2026-07-09.json → 2026-07-09
        date_key = f.stem.replace("erp_", "", 1)

        # Validate it looks like a date
        if len(date_key) != 10 or date_key[4] != "-" or date_key[7] != "-":
            print(
                f"[warn] skipping {f.name}: unrecognized date key '{date_key}'",
                file=sys.stderr,
            )
            continue

        us_erp = _us_erp_for(data)
        mature_erp = data.get("mature_market_erp")

        history[date_key] = {
            "us_erp": us_erp,
            "mature_market_erp": mature_erp,
        }

    return {"erp_history": dict(sorted(history.items()))}


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Build Lean ERP history from per-period extractions"
    )
    ap.add_argument(
        "--erp-dir",
        default=str(DEFAULT_ERP_DIR),
        help="Directory with erp_*.json files",
    )
    ap.add_argument(
        "--out",
        default=str(DEFAULT_OUT),
        help="Output path for damodaran_erp_history.json",
    )
    args = ap.parse_args()

    erp_dir = Path(args.erp_dir)
    out_path = Path(args.out)

    result = build(erp_dir)

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    n = len(result.get("erp_history", {}))
    print(f"[OK] {n} ERP periods → {out_path}", file=sys.stderr)


if __name__ == "__main__":
    main()
