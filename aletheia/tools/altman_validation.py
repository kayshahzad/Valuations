"""Validation universe for the Altman distress screen (Phase 1a).

A small set of KNOWN-distressed real companies, inputs frozen from their SEC
filings, screened on every run as a live canary. The tracked investment
universe of large-caps produces zero actionable-distress names by design, so the
firing path can't be falsified against it — these keep it continuously falsified.
If a regression breaks the path (a field-promotion change, a classification
tweak), these flip from actionable -> safe/abstain and the guard test fails
immediately.

Deliberately OUT of the investment universe: they exist only to exercise the
firing path, not to be valued. Each case pairs frozen inputs with the expected
verdict; ``run_validation()`` re-screens them and reports drift.
"""
from __future__ import annotations

from typing import Any, Dict, List

from aletheia.tools.altman_screen import AltmanScreen, screen_distress

# Each case: frozen SEC-derived inputs + the expected verdict.
VALIDATION_CASES: List[Dict[str, Any]] = [
    {
        "ticker": "W",
        "note": "Wayfair FY2024 — negative book equity from 12y accumulated deficit "
                "(not buybacks); corroborated by interest coverage, not EBIT/TA.",
        "latest": dict(
            ticker="W", fiscal_year=2024,
            raw_TotalAssets=2.87e9, raw_TotalLiabilities=5.71e9,
            raw_RetainedEarnings=-4.93e9, raw_TotalEquity=-2.84e9,
            raw_CurrentAssets=1.568e9, raw_LiabilitiesCurrent=2.055e9,
            clean_NormalizedEBIT=0.02e9,
            raw_LongTermDebt=2.931e9, raw_CurrentPortionLongTermDebt=0.039e9,
        ),
        "ni_history": [-0.414e9] * 12,
        "interest_expense": 0.17e9,
        "cls": {"ticker": "W", "sector": "Consumer Discretionary",
                "industry": "Internet Retail", "business_model": "fcff_compatible"},
        "expect_classification": "accumulated_deficit",
    },
    {
        "ticker": "RIVN",
        "note": "Rivian FY2024 — 5y of losses (young-and-losing); positive equity "
                "from IPO capital but real accumulated deficit + negative EBIT.",
        "latest": dict(
            ticker="RIVN", fiscal_year=2024,
            raw_TotalAssets=14.233e9, raw_TotalLiabilities=9.804e9,
            raw_RetainedEarnings=-27.367e9, raw_TotalEquity=5.899e9,
            raw_CurrentAssets=7.045e9, raw_LiabilitiesCurrent=3.353e9,
            clean_NormalizedEBIT=-3.585e9,
            raw_LongTermDebt=4.442e9, raw_CurrentPortionLongTermDebt=0.0,
        ),
        "ni_history": [-4.688e9, -6.752e9, -5.432e9, -4.747e9, -3.646e9],
        "interest_expense": 0.22e9,
        "cls": {"ticker": "RIVN", "sector": "Auto Manufacturers",
                "industry": "Automotive", "business_model": "fcff_compatible"},
        "expect_classification": "accumulated_deficit",
    },
]


def screen_case(case: Dict[str, Any]) -> AltmanScreen:
    return screen_distress(
        case["latest"], case["ni_history"], case["cls"],
        interest_expense=case.get("interest_expense"),
    )


def run_validation() -> List[Dict[str, Any]]:
    """Re-screen every case; return per-case drift report. ``ok`` is True when the
    case still fires actionable distress with the expected classification."""
    report = []
    for case in VALIDATION_CASES:
        r = screen_case(case)
        ok = (r.actionable_distress
              and r.classification == case["expect_classification"])
        report.append({"ticker": case["ticker"], "ok": ok,
                       "actionable": r.actionable_distress,
                       "classification": r.classification, "zone": r.zone,
                       "note": case["note"]})
    return report


__all__ = ["VALIDATION_CASES", "screen_case", "run_validation"]
