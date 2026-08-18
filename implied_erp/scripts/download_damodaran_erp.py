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

# Archive files: ctryprem00 (year 2001) through ctryprem25 (year 2026).
# The NN is an OFFSET FROM 2001: ctryprem00 is the 2001 ERP (published
# Jan 1 2001, used during 2001). Its embedded 'Date of update' cell is only the
# in-year publication date and is ignored for anchoring (for .xls) / used for
# anchoring (for .xlsx). Recent archives are published as .xlsx, so try .xlsx
# first and fall back to .xls.
def _archive_targets() -> list[tuple[str, list[str]]]:
    """Generate (basename, [urls]) for all archive years.

    basename is the .xlsx form; urls are tried in order .xlsx then .xls.
    """
    out: list[tuple[str, list[str]]] = []
    for yy in range(26):
        name = f"ctryprem{yy:02d}"
        urls = [
            ARCHIVE_BASE + f"{name}.xlsx",
            ARCHIVE_BASE + f"{name}.xls",
        ]
        out.append((f"{name}.xlsx", urls))
    return out


def build_targets(raw_dir: Path) -> list[tuple[str, list[str]]]:
    """Build the list of (basename, [urls]) download jobs."""
    raw_dir.mkdir(parents=True, exist_ok=True)

    targets: list[tuple[str, list[str]]] = []
    seen: set[str] = set()

    # Archive files (2001-2026), try .xlsx then .xls
    for basename, urls in _archive_targets():
        targets.append((basename, urls))
        seen.add(basename)

    # Current 2026 .xlsx files (override any same-named archive entry)
    for basename, url in CURRENT_2026:
        if basename in seen:
            targets = [(b, u) for b, u in targets if b != basename]
        targets.append((basename, [url]))
        seen.add(basename)

    return targets


def _download(urls: list[str], raw_dir: Path) -> bool:
    """Download a file via HTTP, trying each URL in order.

    Writes to the filename taken from the successful URL (so a .xls fallback is
    saved with a .xls extension, not mislabeled .xlsx). Returns True on success.
    """
    for url in urls:
        basename = url.rsplit("/", 1)[-1]
        dest = raw_dir / basename
        try:
            print(f"[downloader] Downloading {url} …", file=sys.stderr)
            urllib.request.urlretrieve(url, dest)
            return True
        except urllib.error.HTTPError as e:
            print(f"[downloader] 404/skipped: {url} ({e.code})", file=sys.stderr)
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
        for basename, urls in targets:
            dests = [raw_dir / u.rsplit("/", 1)[-1] for u in urls]
            exists = "exists" if any(d.exists() for d in dests) else "missing"
            print(f"  {basename:30s} {exists:8s} ← {urls[0]} (fallback {urls[-1]})", file=sys.stderr)
        return

    downloaded = 0
    skipped = 0
    failed = 0

    for basename, urls in targets:
        dests = [raw_dir / u.rsplit("/", 1)[-1] for u in urls]
        if any(d.exists() for d in dests) and not args.force:
            print(f"[skip] {basename} (already exists)", file=sys.stderr)
            skipped += 1
            continue

        success = _download(urls, raw_dir)
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
