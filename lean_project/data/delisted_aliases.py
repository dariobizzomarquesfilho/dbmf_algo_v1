"""Curated rename map + corporate-action screen for delisted / renamed S&P 500
constituents that Yahoo no longer serves as a continuous series.

This module is the *review-only* source of truth for recovering tickers that the
batch Yahoo download dropped (the "failed data requests" gap). Two mechanisms:

1. ``RENAME_MAP`` — predecessor -> successor pairs that are a **pure ticker rename**
   (the same unbroken listing continues under a new symbol, e.g. ``CTL``->``LUMN``).
   The successor's continuous Yahoo series is sliced across the rename and stored
   under the predecessor key. **Hand-curated and reviewed** — auto-derivation is
   deliberately NOT done (it is unsafe: ``WRK``->``SW`` resolves to Smurfit Kappa's
   2008 series, ``RTN``->``RTX`` would duplicate ``UTX``->``RTX``).

2. ``EXCLUDED_COMPLEX`` — merger / spinoff names the user explicitly excluded
   (they already have a "sell before corporate action" trigger). These are never
   recovered here; they fall through to the Tiingo fallback or the unavailable
   bucket.

``screen_successor`` is a second line of defence: it flags any successor series
that shows a stock split / outsized dividend within +/-N trading days of the
predecessor's index-exit date. A deliberate rename in ``RENAME_MAP`` always wins
over the screen; the screen only ever *drops* a candidate that is BOTH ``COMPLEX``
AND not a deliberate rename.
"""

from __future__ import annotations

from typing import Optional

# ---------------------------------------------------------------------------
# Pure renames (predecessor -> successor). Each verified live against Yahoo:
# the successor's continuous adjusted series covers the predecessor's
# membership interval, with NO split/outsized-dividend (corporate action) on the
# exit day — i.e. a clean continuation, not a price stitch across an action.
# ---------------------------------------------------------------------------
RENAME_MAP: dict[str, str] = {
    # CenturyLink -> Lumen. Exit 2020-09-18. Screen: clean.
    "CTL": "LUMN",
    # Anthem -> Elevance Health. Exit 2022-06-28. Screen: clean.
    "ANTM": "ELV",
    # Ball Corp (old) -> Ball Corp (new ticker BALL). Exit 2022-05-10. Clean.
    "BLL": "BALL",
    # PerkinElmer -> Revvity. Exit 2023-05-16. Screen: clean.
    "PKI": "RVTY",
    # Easterly Government Properties predecessor -> EG (Realty Income reorg).
    # Exit 2023-07-10. Screen: clean.
    "RE": "EG",
    # Willis Towers Watson -> WTW. Exit 2022-01-10. Screen: clean.
    "WLTW": "WTW",
    # Symantec -> Gen Digital (via NortonLifeLock). Exit 2022-11-08. Clean.
    "NLOK": "GEN",
    # AmerisourceBergen -> Cencora. Exit 2023-08-30. Screen: clean.
    "ABC": "COR",
    # Gap (old) -> Gap (new ticker GAP). Exit 2022-02-02. Screen: clean.
    "GPS": "GAP",
    # FleetCor -> Corpay. Exit 2024-03-25. Screen: clean.
    "FLT": "CPAY",
    # Healthpeak -> DOC. Exit 2024-03-04. Screen: clean.
    "PEAK": "DOC",
    # HollyFrontier -> HF Sinclair. Exit 2021-06-04. Screen: clean.
    "HFC": "DINO",
    # First Republic -> FRCB (receivership ticker). Exit 2023-05-04. Clean.
    "FRC": "FRCB",
    # Fiserv ticker change FI -> FISV (continuous listing). Exit 2025-11-11. Clean.
    "FI": "FISV",
}

# ---------------------------------------------------------------------------
# Merger / spinoff names the user explicitly excluded. These are NOT recovered
# here (the strategy sells before the corporate action). Documented so a future
# reader knows *why* they are skipped. ``RTN``/``JWN`` appear twice in the source
# list but a set de-dupes them.
# ---------------------------------------------------------------------------
EXCLUDED_COMPLEX: set[str] = {
    "UTX", "RTN", "ARNC", "FBHS", "ADS", "MYL", "DISCA", "DISCK", "VIAC",
    "ABMD", "AGN", "ALXN", "CERN", "CXO", "DRE", "ETFC", "FLIR", "KSU",
    "MXIM", "NBL", "PBCT", "PXD", "SEE", "TIF", "VAR", "WCG", "WRK",
    "XEC", "XLNX", "ATVI", "CTLT", "CDAY", "DAY", "CTRA", "PARA", "JWN",
    "HBI", "HOLX", "IPG", "JNPR", "NLSN", "CTXS", "MRO", "CMA", "DFS",
    "ANSS", "K", "WBA", "HES", "SIVB", "TWTR", "DISH",
}


def is_deliberate_rename(predecessor: str) -> bool:
    """True if ``predecessor`` is a hand-curated pure rename (always kept)."""
    return predecessor in RENAME_MAP


def screen_successor(
    successor_bars: dict[str, dict],
    exit_date: str,
    window_days: int = 20,
    threshold: float = 0.20,
) -> str:
    """Flag a successor series as ``"clean"`` or ``"COMPLEX"``.

    ``COMPLEX`` means the successor's adjusted daily close shows a corporate-
    action discontinuity — a single-day absolute return beyond ``threshold``
    (a split or an outsized dividend) — within ``window_days`` trading days of
    ``exit_date``. Adjusted data removes splits but leaves dividend gaps, so a
    large dividend (or a split that survived adjustment) appears as a sudden
    jump.

    Returns ``"clean"`` when no such discontinuity exists near the exit.
    """
    if not successor_bars:
        return "clean"

    dates = sorted(successor_bars.keys())
    try:
        exit_idx = dates.index(exit_date)
    except ValueError:
        nearest = min(
            range(len(dates)),
            key=lambda i: abs(_days_between(dates[i], exit_date)),
        )
        exit_idx = nearest

    lo = max(0, exit_idx - window_days)
    hi = min(len(dates) - 1, exit_idx + window_days)

    prev_close = None
    for d in dates[lo:hi + 1]:
        close = float(successor_bars[d].get("close", 0) or 0)
        if prev_close not in (None, 0) and close > 0:
            ret = (close - prev_close) / prev_close
            if abs(ret) > threshold:
                return "COMPLEX"
        prev_close = close

    return "clean"


def _days_between(a: str, b: str) -> int:
    from datetime import date

    def _parse_iso(s: str) -> date:
        # Accept both YYYY-MM-DD and YYYYMMDD
        if len(s) == 8 and s.isdigit():
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        return date.fromisoformat(s)

    da = _parse_iso(a)
    db = _parse_iso(b)
    return (da - db).days


def membership_window(
    intervals: list[tuple[str, Optional[str]]],
    default_end: str,
) -> tuple[str, Optional[str]]:
    """Union of membership intervals -> (earliest_start, latest_end_or_default).

    If any interval is open-ended (end is None) the union is open-ended — we
    return None and the caller substitutes ``default_end``. Otherwise the
    latest end date is the union end.
    """
    starts = [s for s, _ in intervals]
    ends = [e for _, e in intervals]
    start = min(starts)
    # If any interval is open (still a member) the union is open.
    if any(e is None for e in ends):
        return start, default_end
    end = max(ends)  # type: ignore[arg-type]  # all str here
    return start, end
