"""2-stage Gordon Growth Model for intrinsic P/B ratio.

Ported from dbmf_quant/backtest/valuation.py.
"""

from __future__ import annotations


def intrinsic_pb_2stage(
    roe: float,
    g_start: float,
    g_term: float,
    r: float,
    years_stage1: int = 5,
) -> float:
    """2-stage Gordon growth implied P/B ratio."""
    if years_stage1 <= 0:
        raise ValueError(f"years_stage1 must be positive, got {years_stage1}")
    if r <= g_term:
        raise ValueError(
            f"Cost of equity (r={r:.4f}) must exceed terminal growth (g_term={g_term:.4f})"
        )
    # Clamp payout to [0, 1] — negative payout (issuing equity to fund
    # growth > ROE) is not modeled as a dividend source; treat as zero payout.
    pv_stage1 = 0.0
    bv_growth = 1.0
    for t in range(1, years_stage1 + 1):
        g_t = (
            g_start + (g_term - g_start) * (t - 1) / (years_stage1 - 1)
            if years_stage1 > 1
            else g_start
        )
        payout = 1 - (g_t / roe) if roe > 0 else 0
        payout = max(0.0, min(1.0, payout))
        div_yield = roe * bv_growth * (1 + g_t) * payout
        pv_stage1 += div_yield / ((1 + r) ** t)
        bv_growth *= (1 + g_t)
    term_payout = 1 - (g_term / roe) if roe > 0 else 0
    term_payout = max(0.0, min(1.0, term_payout))
    term_div_at_t1 = roe * bv_growth * (1 + g_term) * term_payout
    # Guard tiny denominator which explodes terminal value for low-beta names
    denom = r - g_term
    if denom < 0.005:
        raise ValueError(f"r - g_term too small ({denom:.4f}); terminal value would explode")
    terminal_value = term_div_at_t1 / denom
    pv_terminal = terminal_value / ((1 + r) ** years_stage1)
    result = pv_stage1 + pv_terminal
    import math
    if not math.isfinite(result):
        raise ValueError(f"intrinsic P/B not finite: {result}")
    return result
