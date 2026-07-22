"""Secondary disclosure metrics — offline, mocked provider + segmentation.

Pins the deterministic assembly: RPO coverage, debt-maturity wall flag,
billings via the XBRL deferred-revenue fallback, R&D from the frame,
revenue-mix HHI, and per-metric self-suppression when a tag is absent.
"""
from __future__ import annotations

import pandas as pd
import pytest

from aletheia.tools.disclosure_metrics import (
    build_disclosure_metrics, _latest_segment_mix,
)


class _Cls:
    def __init__(self, t): self.ticker = t


class _CalcInput:
    def __init__(self, df, ticker="TEST"):
        self.df = df
        self.classification = _Cls(ticker)


class _FakeXbrl:
    """get_companyfact(ticker, tag, fy, period) -> canned value or None."""
    def __init__(self, facts):  # facts: {(tag, fy): value}
        self.facts = facts
    def get_companyfact(self, ticker, tag, fy, period="FY"):
        return self.facts.get((tag, fy))


def _df():
    return pd.DataFrame([
        {"fiscal_year": 2024, "period": "FY", "clean_Revenue": 90e9, "raw_RnD": 8e9},
        {"fiscal_year": 2025, "period": "FY", "clean_Revenue": 100e9, "raw_RnD": 12e9},
    ])


@pytest.fixture()
def patched(monkeypatch):
    def _apply(facts, seg=None):
        monkeypatch.setattr("aletheia.providers.registry.get_provider",
                            lambda *a, **k: _FakeXbrl(facts))
        monkeypatch.setattr("aletheia.data.fmp_client.fetch_revenue_product_segmentation",
                            lambda *a, **k: seg)
    return _apply


def test_full_assembly(patched):
    facts = {
        ("RevenueRemainingPerformanceObligation", 2025): 133e9,     # RPO
        ("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths", 2025): 30e9,
        ("LongTermDebtMaturitiesRepaymentsOfPrincipalInYearTwo", 2025): 20e9,
        ("LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive", 2025): 50e9,
        ("OperatingLeaseLiability", 2025): 5e9,
        ("ContractWithCustomerLiability", 2025): 20e9,
        ("ContractWithCustomerLiability", 2024): 15e9,
    }
    seg = [{"data": {"Cloud": 60e9, "Devices": 30e9, "Services": 10e9}}]
    patched(facts, seg)
    m = build_disclosure_metrics(_CalcInput(_df()))
    assert m["available"] is True
    # RPO 133B / rev 100B = 1.33x
    assert m["rpo"]["available"] and abs(m["rpo"]["coverage_ratio"] - 1.33) < 0.01
    # debt ladder: near-term (30+20)=50 of total 100 = 50% > 40% → wall
    assert m["debt_maturity"]["near_term_pct"] == 0.5 and m["debt_maturity"]["wall_flag"] is True
    # billings = rev 100 + Δdeferred (20-15)=5 → 105B, via XBRL fallback
    assert abs(m["billings"]["value"] - 105e9) < 1
    assert "xbrl" in m["provenance"]["billings"]
    # R&D from frame: 12/100 = 12%
    assert abs(m["rd_intensity"]["pct_revenue"] - 0.12) < 1e-9
    assert m["provenance"]["rd_intensity"].startswith("frame")
    # revenue mix HHI = .6^2+.3^2+.1^2 = .46, top Cloud 60%
    assert abs(m["revenue_mix"]["hhi"] - 0.46) < 0.01
    assert m["revenue_mix"]["top_segment"] == "Cloud"


def test_self_suppress_when_tags_absent(patched):
    patched({}, None)  # no XBRL facts, no segmentation
    m = build_disclosure_metrics(_CalcInput(_df()))
    for k in ("rpo", "debt_maturity", "leases", "pension", "billings", "revenue_mix"):
        assert m[k]["available"] is False
    # R&D still computes from the frame
    assert m["rd_intensity"]["available"] is True
    assert m["available"] is True  # R&D present


def test_debt_wall_flag_below_threshold(patched):
    facts = {
        ("LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths", 2025): 10e9,
        ("LongTermDebtMaturitiesRepaymentsOfPrincipalAfterYearFive", 2025): 90e9,
    }
    patched(facts, None)
    m = build_disclosure_metrics(_CalcInput(_df()))
    assert m["debt_maturity"]["near_term_pct"] == 0.1
    assert m["debt_maturity"]["wall_flag"] is False


def test_segment_mix_pure():
    mix = _latest_segment_mix([{"data": {"A": 70, "B": 30}}])
    assert mix["top_share"] == 0.7 and abs(mix["hhi"] - 0.58) < 1e-9
    assert _latest_segment_mix([]) is None
    assert _latest_segment_mix([{"data": {}}]) is None


def test_no_history_frame():
    m = build_disclosure_metrics(_CalcInput(pd.DataFrame()))
    assert m["available"] is False
