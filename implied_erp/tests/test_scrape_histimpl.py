"""Tests for the histimpl.html US implied-ERP history parser.

Exercises the parsing logic on a tiny in-memory HTML fixture so it runs without
network.  Asserts the year range and percentage→decimal conversion.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from implied_erp.scripts.scrape_histimpl import (  # noqa: E402
    parse_html,
    YEAR_MIN,
    YEAR_MAX,
)


_FIXTURE_HTML = """
<html><body><table>
<thead><tr>
  <th>Year</th><th>Earnings Yield</th><th>Dividend Yield</th>
  <th>S&amp;P 500</th><th>Earnings*</th><th>Dividends*</th>
  <th>T.Bond Rate</th><th>Smoothed Growth</th><th>Implied
ERP       (FCFE)</th>
</tr></thead>
<tbody>
<tr><td>1959</td><td>5.0%</td><td>3.0%</td><td>50</td><td>3</td><td>2</td><td>2.5%</td><td>2.0%</td></tr>
<tr><td>1960</td><td>5.34%</td><td>3.41%</td><td>58.11</td><td>3.10</td><td>1.98</td><td>2.76%</td><td>2.45%</td><td>&nbsp;</td></tr>
<tr><td>1961</td><td>4.71%</td><td>2.85%</td><td>71.55</td><td>3.37</td><td>2.04</td><td>2.35%</td><td>2.41%</td><td>2.92%</td></tr>
<tr><td>2025</td><td>3.97%</td><td>1.15%</td><td>6845.50</td><td>271.52</td><td>78.51</td><td>4.18%</td><td>4.61%</td><td>4.23%</td></tr>
<tr><td>2026</td><td>4.0%</td><td>1.2%</td><td>7000</td><td>280</td><td>80</td><td>4.2%</td><td>4.6%</td><td>4.10%</td></tr>
</tbody></table>
<p>Update: Januaary 2026</p></body></html>
"""


def test_parse_fixture_year_range_and_decimal():
    history = parse_html(_FIXTURE_HTML)
    # 1960 row has an empty FCFE cell (NaN) -> dropped.
    assert "1960-01-01" not in history
    assert "2025-01-01" in history
    # 1959 (below YEAR_MIN) and 2026 (above YEAR_MAX) dropped.
    assert "1959-01-01" not in history
    assert "2026-01-01" not in history
    # 1961 + 2025 conversions.
    assert abs(history["1961-01-01"] - 0.0292) < 1e-9
    assert abs(history["2025-01-01"] - 0.0423) < 1e-9


def test_parse_fixture_bounds_dropped_rows():
    history = parse_html(_FIXTURE_HTML)
    years = [int(k[:4]) for k in history]
    assert min(years) >= YEAR_MIN
    assert max(years) <= YEAR_MAX
    # 1960 row has empty FCFE (NaN) → not present.
    assert "1960-01-01" not in history
    assert len(history) == 2  # only 1961 and 2025
