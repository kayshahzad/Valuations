"""Tests for the L3 pure roll-forward primitives.

Each primitive is a pure (`beg + flows → implied_end`) function — no
side effects, no I/O. These tests pin the arithmetic so future refactors
of identity_checks or DCFEngine can route through the primitives with
confidence.
"""

from __future__ import annotations

import pytest


# ─────────────────────────────────────────────────────────────────────
# ppe_rollforward
# ─────────────────────────────────────────────────────────────────────

def test_ppe_basic():
    from aletheia.calculations.rollforward import ppe_rollforward
    # PPE_end = PPE_beg + CapEx − D&A
    assert ppe_rollforward(beg=100.0, capex=20.0, da=10.0) == 110.0


def test_ppe_with_acquisitions_and_impairments():
    from aletheia.calculations.rollforward import ppe_rollforward
    out = ppe_rollforward(
        beg=100.0, capex=20.0, da=10.0,
        acquisitions=5.0, impairments=3.0, cip_additions=2.0,
    )
    # 100 + 20 - 10 + 5 - 3 + 2 = 114
    assert out == 114.0


# ─────────────────────────────────────────────────────────────────────
# re_rollforward (equity-bridge)
# ─────────────────────────────────────────────────────────────────────

def test_re_basic():
    from aletheia.calculations.rollforward import re_rollforward
    # RE_end = RE_beg + NI − Div  (no buybacks, no equity bridge)
    out = re_rollforward(beg=100.0, ni=20.0, div=5.0)
    assert out == 115.0


def test_re_extended_meta_fy2024():
    """Empirical reconstruction from Phase 1.β:
       RE_beg=$82.07B, NI=$62.36B, Div=$5.07B, Buybacks=$30.12B,
       TaxWithhold=$13.77B, SBC=$16.69B, ΔAPIC=$9.98B → ~$102.18B
       (matches reported $102.51B within tolerance — 0.3% residual
       attributed to excise tax)."""
    from aletheia.calculations.rollforward import re_rollforward
    out = re_rollforward(
        beg=82.07e9, ni=62.36e9, div=5.07e9,
        buybacks=30.12e9, tax_withhold=13.77e9,
        sbc=16.69e9, delta_apic=9.98e9,
    )
    # Expected: 82.07 + 62.36 - 5.07 - 30.12 - 13.77 + 16.69 - 9.98
    expected = 82.07e9 + 62.36e9 - 5.07e9 - 30.12e9 - 13.77e9 + 16.69e9 - 9.98e9
    assert abs(out - expected) < 1e3


def test_re_extended_sign_robust():
    """Buybacks + tax_withhold are passed as positive magnitudes (cash-
    flow convention); the primitive abs()es them defensively so a
    negative-signed input doesn't double-charge RE."""
    from aletheia.calculations.rollforward import re_rollforward
    a = re_rollforward(beg=100, ni=50, buybacks=10)
    b = re_rollforward(beg=100, ni=50, buybacks=-10)  # negative input
    assert a == b == 140  # both subtract abs(10) = 10


# ─────────────────────────────────────────────────────────────────────
# cash_rollforward
# ─────────────────────────────────────────────────────────────────────

def test_cash_basic():
    from aletheia.calculations.rollforward import cash_rollforward
    out = cash_rollforward(beg=100, ocf=50, icf=-20, fcf=-10)
    assert out == 120


def test_cash_with_fx_effect():
    from aletheia.calculations.rollforward import cash_rollforward
    out = cash_rollforward(beg=100, ocf=50, icf=-20, fcf=-10, fx_effect=-5)
    assert out == 115


# ─────────────────────────────────────────────────────────────────────
# debt_rollforward
# ─────────────────────────────────────────────────────────────────────

def test_debt_basic():
    from aletheia.calculations.rollforward import debt_rollforward
    out = debt_rollforward(beg=100, issued=30, repaid=20)
    assert out == 110


def test_debt_with_cp_and_fx():
    from aletheia.calculations.rollforward import debt_rollforward
    out = debt_rollforward(
        beg=100, issued=30, repaid=20, cp_net=5, fx_translation=2,
    )
    assert out == 117


# ─────────────────────────────────────────────────────────────────────
# wc_rollforward
# ─────────────────────────────────────────────────────────────────────

def test_wc_identity_holds_zero_discrepancy():
    """When CF Δ matches BS Δ exactly, the identity-discrepancy is 0."""
    from aletheia.calculations.rollforward import wc_rollforward
    out = wc_rollforward(bs_change=100, cf_reported_change=100)
    assert out == 0


def test_wc_returns_signed_discrepancy():
    """Sign indicates which side is larger:
       positive → CF claims more change than BS shows
       negative → BS shows more change than CF reports."""
    from aletheia.calculations.rollforward import wc_rollforward
    assert wc_rollforward(bs_change=100, cf_reported_change=120) == 20
    assert wc_rollforward(bs_change=100, cf_reported_change=80) == -20


# ─────────────────────────────────────────────────────────────────────
# fcf_pathway_b
# ─────────────────────────────────────────────────────────────────────

def test_fcf_pathway_basic():
    from aletheia.calculations.rollforward import fcf_pathway_b
    # NOPAT + DA − CapEx − ΔNWC
    out = fcf_pathway_b(nopat=100, da=20, capex=15, delta_nwc=5)
    assert out == 100


def test_fcf_pathway_extended_with_sbc():
    """Phase-1 extended Pathway B adds SBC. Without SBC, SBC-heavy
    filers fail the FCF identity systematically."""
    from aletheia.calculations.rollforward import fcf_pathway_b
    out = fcf_pathway_b(nopat=100, da=20, capex=15, delta_nwc=5, sbc=10)
    assert out == 110


def test_fcf_pathway_capex_sign_robust():
    """CapEx is treated as a positive magnitude (abs)
    regardless of sign convention the caller used."""
    from aletheia.calculations.rollforward import fcf_pathway_b
    a = fcf_pathway_b(nopat=100, da=20, capex=15)
    b = fcf_pathway_b(nopat=100, da=20, capex=-15)  # negative input
    assert a == b == 105


def test_fcf_pathway_full_extended():
    """All optional terms applied."""
    from aletheia.calculations.rollforward import fcf_pathway_b
    out = fcf_pathway_b(
        nopat=100, da=20, capex=15, delta_nwc=5,
        sbc=10, deferred_tax=3, other_non_cash=2,
    )
    # 100 + 20 + 10 + 3 + 2 − 15 − 5 = 115
    assert out == 115
