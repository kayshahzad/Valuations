"""Tests for the numeric-fidelity checker.

Catches the post-regen failure modes:
  - Fabricated $0 bear cases (JNJ/ABT/MRK pattern)
  - Decision-condition prose↔threshold mismatch (JNJ/KO/TXN pattern)
  - Implied/historical CAGR drift in case prose
"""

from __future__ import annotations

from typing import Any, Dict

import pytest
from pydantic import BaseModel

from aletheia.agents.thesis_synthesizer import _check_numeric_fidelity


def _claim(text: str, signals=None):
    """Build a minimal CitedClaim-shaped object."""
    class _Claim:
        def __init__(self, c, cs):
            self.claim = c
            self.cited_signals = cs or ["mock"]
    return _Claim(text, signals or ["mock"])


def _dc(trigger: str, observable: str, action="hold", priority="amber"):
    """Build a minimal DecisionCondition-shaped object."""
    class _DC:
        def __init__(self, t, o, a, p):
            self.trigger = t
            self.observable = o
            self.action = a
            self.priority = p
    return _DC(trigger, observable, action, priority)


def _thesis(bull_text="", base_text="", bear_text="", dcs=None):
    class _T:
        def __init__(self):
            self.bull_case = _claim(bull_text or "bull")
            self.base_case = _claim(base_text or "base")
            self.bear_case = _claim(bear_text or "bear")
            self.decision_conditions = dcs or []
    return _T()


def _state(bear_ips=None, base_ips=None, bull_ips=None,
           impl_cagr=None, hist_cagr=None) -> Dict[str, Any]:
    return {
        "phase2_valuation": {
            "three_scenario_dcf": {
                "bear": {"intrinsic_per_share": bear_ips},
                "base": {"intrinsic_per_share": base_ips},
                "bull": {"intrinsic_per_share": bull_ips},
            },
            "implied_cagr":    impl_cagr,
            "historical_cagr": hist_cagr,
        },
    }


# ── $0 fabrication detection ─────────────────────────────────────────

def test_zero_dollar_in_bear_when_actual_is_182_flagged():
    """JNJ-style: bear claims $0/share but engine bear is $181.85."""
    t = _thesis(bear_text="The absolute bear case DCF points to $0/share.")
    s = _state(bear_ips=181.85, base_ips=279.0, bull_ips=365.0)
    v = _check_numeric_fidelity(t, s)
    assert any("$0" in x and "bear_case" in x for x in v), v


def test_zero_dollar_in_bear_when_actual_is_60_flagged():
    """ABT pattern."""
    t = _thesis(bear_text="catastrophic wipeout scenario ($0/share intrinsic)")
    s = _state(bear_ips=60.14)
    v = _check_numeric_fidelity(t, s)
    assert any("$0" in x and "bear_case" in x for x in v), v


def test_zero_dollar_passes_when_engine_bear_is_actually_zero():
    """If engine truly returns $0, the prose can say $0."""
    t = _thesis(bear_text="Bear case is $0/share given catastrophic write-down.")
    s = _state(bear_ips=0.0)
    v = _check_numeric_fidelity(t, s)
    assert not any("$0" in x and "bear_case" in x for x in v), v


def test_no_dollar_in_prose_no_violation():
    t = _thesis(bear_text="Severe downside risk from biosimilars.")
    s = _state(bear_ips=181.85)
    v = _check_numeric_fidelity(t, s)
    assert not v


# ── Decision-condition fidelity ─────────────────────────────────────

def test_dc_historical_with_threshold_zero_when_actual_is_2pct_flagged():
    """JNJ pattern: trigger says 'historical' (=2.4%) but threshold is 0."""
    t = _thesis(dcs=[_dc(
        trigger="Implied CAGR normalizes above 0%, indicating market is "
                "pricing in historical growth rates again.",
        observable="phase2.implied_cagr > 0.0",
    )])
    s = _state(hist_cagr=0.024)
    v = _check_numeric_fidelity(t, s)
    assert any("historical" in x and "0.0%" in x for x in v), v


def test_dc_historical_threshold_matches_no_violation():
    """Threshold matches historical → pass."""
    t = _thesis(dcs=[_dc(
        trigger="Implied CAGR normalizes above historical growth rates.",
        observable="phase2.implied_cagr > 0.024",
    )])
    s = _state(hist_cagr=0.024)
    v = _check_numeric_fidelity(t, s)
    assert not any("historical" in x and "threshold" in x for x in v)


def test_dc_no_historical_keyword_skipped():
    """Trigger doesn't claim historical → don't try to match historical."""
    t = _thesis(dcs=[_dc(
        trigger="Implied CAGR exceeds 25% — significant overpricing.",
        observable="phase2.implied_cagr > 0.25",
    )])
    s = _state(hist_cagr=0.024)
    v = _check_numeric_fidelity(t, s)
    assert not v


# ── Implied / historical CAGR drift in case prose ───────────────────

def test_implied_cagr_in_prose_must_match_actual():
    """Bear says 'implied CAGR 31.6%' but state has 5%."""
    t = _thesis(bear_text="The implied CAGR of 31.6% is unsustainable.")
    s = _state(impl_cagr=0.05)
    v = _check_numeric_fidelity(t, s)
    assert any("implied CAGR 31.6%" in x for x in v), v


def test_implied_cagr_within_1pp_tolerance_passes():
    """Tolerance: 31.6% prose vs 31.5% cited → within tolerance, pass."""
    t = _thesis(bear_text="The implied CAGR of 31.6% is unsustainable.")
    s = _state(impl_cagr=0.315)
    v = _check_numeric_fidelity(t, s)
    assert not any("implied CAGR" in x for x in v)


def test_historical_cagr_in_prose_must_match():
    t = _thesis(bull_text="Reasonable given historical CAGR of 10.0%.")
    s = _state(hist_cagr=0.024)
    v = _check_numeric_fidelity(t, s)
    assert any("historical 10.0%" in x for x in v), v


# ── No state → graceful no-op ────────────────────────────────────────

def test_no_state_no_violations():
    """If state is empty (e.g. initial pipeline pass), don't flag false positives."""
    t = _thesis(bear_text="$0/share")
    v = _check_numeric_fidelity(t, {})
    # state lacks bear IPS → fidelity check can't compare → no violation
    assert not v
