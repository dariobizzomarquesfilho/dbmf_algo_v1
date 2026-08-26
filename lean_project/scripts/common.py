"""Shared helpers for lean_project data scripts.

Deliberately avoids importing ``config`` (which requires a ``.env`` with
``SEC_USER``) so that pure data helpers stay importable in tests and
lightweight tooling without triggering edgar identity setup.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

# Indices that must always be present in the equity universe: ^TNX supplies the
# risk-free rate and ^GSPC the market beta. They are never fundamentals members
# but are required by the backtest, so they are unioned into every filtered list.
REQUIRED_INDICES = {"^TNX", "^GSPC"}

_DEFAULT_FUNDAMENTALS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "fundamentals_history.json"
)


def load_fundamentals_tickers(path: Optional[str] = None) -> set[str]:
    """Return the set of tickers that have PIT fundamentals history.

    = (``fundamentals_history.json`` keys) ∪ :data:`REQUIRED_INDICES`.

    Falls back to ``set(REQUIRED_INDICES)`` when the file is missing, empty, or
    invalid (never raises), so callers can always rely on the required indices
    being present.
    """
    p = Path(path) if path else _DEFAULT_FUNDAMENTALS_PATH
    try:
        with open(p, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or not data:
            return set(REQUIRED_INDICES)
        return set(data.keys()) | REQUIRED_INDICES
    except Exception:
        return set(REQUIRED_INDICES)
