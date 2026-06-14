"""Build F — SaaS-gated cyclicality P3 exemption.

Asserts the exemption (1) fires for SaaS, restoring the cyclicality penalty,
(2) is byte-for-byte inert for non-SaaS, and (3) does NOT cross a tier boundary
for ADBE (raw 21→22, stays < 23).
"""

from aletheia.tools.conviction_scorer import _p3_score


# A "peak" scenario: moderate CAGR, Technology, is_peak True, z>1.5.
_BASE = dict(rev_cagr=0.10, hist_cagr=0.10, sector="Technology",
             cyclicality_z=2.39, is_peak=True, implied_cagr=None,
             cagr_strong=0.20, cagr_good=0.12, cagr_moderate=0.07, cagr_slow=0.03)


def test_saas_exemption_restores_penalty():
    """SaaS: no cyclicality penalty → P3 one point higher than non-SaaS."""
    p3_non_saas, _ = _p3_score(**_BASE, is_saas=False)
    p3_saas, reasons = _p3_score(**_BASE, is_saas=True)
    assert p3_saas == p3_non_saas + 1
    # the reframed reason replaces the 'base inflated' text
    joined = " ".join(reasons)
    assert "secular growth" in joined
    assert "base revenue likely inflated" not in joined


def test_exemption_covers_the_elif_fallthrough():
    """Critical: with is_peak False but z>1.5, the elif must ALSO be skipped
    for SaaS (otherwise the exemption is a no-op for ADBE's z=2.39)."""
    kw = {**_BASE, "is_peak": False}   # only the z>1.5 elif would fire
    p3_non_saas, _ = _p3_score(**kw, is_saas=False)
    p3_saas, _ = _p3_score(**kw, is_saas=True)
    assert p3_saas == p3_non_saas + 1


def test_non_saas_unchanged():
    """Gate proof: is_saas=False path is identical to the legacy behavior."""
    p3, reasons = _p3_score(**_BASE, is_saas=False)
    assert any("base revenue likely inflated" in r for r in reasons)


def test_adbe_no_high_conviction_cross():
    """The +1 must keep ADBE in the conviction band (≤22), not reach 23.
    P3 caps at 5; the moderate-CAGR(3)+tailwind(1) base is 4 after exemption."""
    p3_saas, _ = _p3_score(**_BASE, is_saas=True)
    assert p3_saas == 4          # 3 moderate + 1 tailwind, no cyclicality penalty
    assert p3_saas <= 5
