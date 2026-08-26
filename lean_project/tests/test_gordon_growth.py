"""Tests for Gordon growth intrinsic P/B."""

from __future__ import annotations

import pytest

from valuation.gordon_growth import intrinsic_pb_2stage


def test_normal_case():
    # Typical S&P value name
    val = intrinsic_pb_2stage(roe=0.15, g_start=0.08, g_term=0.03, r=0.09)
    assert val > 0
    assert 0.5 < val < 10


def test_years_stage1_zero_raises():
    with pytest.raises(ValueError, match="years_stage1"):
        intrinsic_pb_2stage(0.15, 0.08, 0.03, 0.09, years_stage1=0)


def test_r_le_g_term_raises():
    with pytest.raises(ValueError, match="must exceed terminal growth"):
        intrinsic_pb_2stage(0.15, 0.08, 0.05, 0.04)


def test_denom_tiny_raises():
    # r - g_term < 0.005 -> should raise (low-beta explosion guard)
    with pytest.raises(ValueError, match="too small"):
        intrinsic_pb_2stage(0.15, 0.04, 0.04, 0.044)


def test_payout_clamp_greater_than_roe():
    # g_start > roe => payout negative without clamp, but clamp -> 0
    # With clamp, implied P/B should still be finite and positive (only terminal)
    val = intrinsic_pb_2stage(roe=0.10, g_start=0.30, g_term=0.03, r=0.09)
    assert val > 0
    # Without clamp it would be negative or huge negative
    assert val < 5  # clamped terminal only


def test_years_stage1_one():
    val = intrinsic_pb_2stage(roe=0.15, g_start=0.05, g_term=0.03, r=0.08, years_stage1=1)
    assert val > 0


def test_negative_roe_payout_zero():
    # roe <=0 -> payout 0 per code, should still return finite (maybe 0)
    val = intrinsic_pb_2stage(roe=0.0, g_start=0.05, g_term=0.02, r=0.08)
    assert val == 0 or val > 0  # payout 0 => pv_stage1 0, terminal 0
