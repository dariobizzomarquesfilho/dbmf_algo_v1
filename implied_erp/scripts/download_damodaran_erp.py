"""Download Damodaran ERP archive files for the Lean backtest pipeline.

Generates archive .xls URLs directly from the predictable naming pattern
(ctryprem00.xls … ctryprem25.xls) and downloads them alongside the
current 2026 .xlsx files into a local raw cache.

Usage:
    python download_damodaran_erp.py                  # fetch all, skip existing
    python download_damodaran_erp.py --force         # re-download everything
    python download_damodaran_erp.py --dry-run       # list URLs, download nothing
    python download_damodaran_erp.py --raw-dir path/to/raw
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

import urllib.request

BASE_PC = "https://pages.stern.nyu.edu/~adamodar/pc/"
ARCHIVE_BASE = BASE_PC + "archives/"

# Current 2026 datasets (user-provided; .xlsx format)
CURRENT_2026 = [
    ("ctryprem.xlsx", BASE_PC + "datasets/ctryprem.xlsx"),
    ("ctrypremApr26.xlsx", BASE_PC + "datasets/ctrypremApr26.xlsx"),
    ("ctrypremJuly26.xlsx", BASE_PC + "datasets/ctrypremJuly26.xlsx"),
]

# Archive files: ctryprem00.xls (year 2000) through ctryprem25.xls (year 2025)
def _archive_targets() -> list[tuple[str, str]]:
    """Generate (basename, url) for all archive .xls files."""
    return [
        (f"ctryprem{yy:02d}.xls", ARCHIVE_BASE + f"ctryprem{yy:02d}.xls")
        for yy in range(26)
    ]


def build_targets(raw_dir: Path) -> list[tuple[str, str, Path]]:
    """Build the list of (basename, url, dest_path) targets."""
    raw_dir.mkdir(parents=True, exist_ok=True)

    targets: list[tuple[str, str, Path]] = []
    seen: set[str] = set()

    # Archive .xls files (2000-2025)
    for basename, url in _archive_targets():
        dest = raw_dir / basename
        targets.append((basename, url, dest))
        seen.add(basename)

    # Current 2026 .xlsx files (override any same-named archive entry)
    for basename, url in CURRENT_2026:
        dest = raw_dir / basename
        if basename in seen:
            targets = [(b, u, d) for b, u, d in targets if b != basename]
        targets.append((basename, url, dest))
        seen.add(basename)

    return targets


def _download(url: str, dest: Path) -> bool:
    """Download a file via HTTP. Returns True on success, False on failure."""
    try:
        print(f"[downloader] Downloading {url} …", file=sys.stderr)
        urllib.request.urlretrieve(url, dest)
        return True
    except urllib.error.HTTPError as e:
        print(f"[downloader] 404/skipped: {url} ({e.code})", file=sys.stderr)
        return False
    except Exception as e:
        print(f"[downloader] error downloading {url}: {e}", file=sys.stderr)
        return False


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Download Damodaran ERP archive files for the Lean pipeline"
    )
    ap.add_argument(
        "--raw-dir",
        default=str(Path(__file__).resolve().parent.parent / "data" / "raw"),
        help="Directory to store downloaded raw files (default: implied_erp/data/raw)",
    )
    ap.add_argument(
        "--force",
        action="store_true",
        help="Re-download all files even if they already exist",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="List URLs without downloading",
    )
    args = ap.parse_args()

    raw_dir = Path(args.raw_dir)
    targets = build_targets(raw_dir)

    if args.dry_run:
        print(f"[dry-run] {len(targets)} files to download:", file=sys.stderr)
        for basename, url, dest in targets:
            exists = "exists" if dest.exists() else "missing"
            print(f"  {basename:30s} {exists:8s} ← {url}", file=sys.stderr)
        return

    downloaded = 0
    skipped = 0
    failed = 0

    for basename, url, dest in targets:
        if dest.exists() and not args.force:
            print(f"[skip] {basename} (already exists)", file=sys.stderr)
            skipped += 1
            continue

        success = _download(url, dest)
        if success:
            downloaded += 1
        else:
            failed += 1

        time.sleep(0.3)  # be polite to the server

    print(
        f"[done] downloaded={downloaded} skipped={skipped} failed={failed} "
        f"total_targets={len(targets)}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
