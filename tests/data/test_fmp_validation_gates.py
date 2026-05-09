"""Tests for FMP validation gates A/B/D + Gate F aggregation logic.

Each gate is exercised with stubbed FMP responses (no live HTTP) so
tests are deterministic and CI-friendly. Pure-function comparison
primitives (drift_label, classify_drift) get their own unit coverage.
"""

from __future__ import annotations

from typing import Any, Dict
from unittest.mock import patch

import pytest

from aletheia.data.fmp_validation_core import _drift_label
from aletheia.data.fmp_validation import (
    IngestionValidationFailure,
    _classify_drift,
    build_receipt_block,
    validate_calc_output,
    validate_ingestion_record,
)


# ── Drift classification (the band thresholds) ───────────────────────────

def test_classify_strict_byte_perfect_at_0_3pct():
    # strict band: <0.5%/2%/blocking
    assert _classify_drift(0.003, "strict") == "byte_perfect"


def test_classify_strict_acceptable_at_1pct():
    assert _classify_drift(0.01, "strict") == "acceptable"


def test_classify_strict_structural_at_3pct():
    assert _classify_drift(0.03, "strict") == "structural_drift"


def test_classify_standard_byte_perfect_at_0_5pct():
    # standard band: <1%/5%/blocking
    assert _classify_drift(0.005, "standard") == "byte_perfect"


def test_classify_standard_acceptable_at_3pct():
    assert _classify_drift(0.03, "standard") == "acceptable"


def test_classify_standard_structural_at_6pct():
    assert _classify_drift(0.06, "standard") == "structural_drift"


def test_classify_definitional_byte_perfect_at_2pct():
    # definitional band: <5%/25%/blocking — tolerant of methodology differences
    assert _classify_drift(0.02, "definitional") == "byte_perfect"


def test_classify_definitional_structural_at_30pct():
    assert _classify_drift(0.30, "definitional") == "structural_drift"


def test_classify_sanity_only_never_structural():
    # sanity_only never triggers structural — only hard-bound checks would
    assert _classify_drift(2.0, "sanity_only") == "byte_perfect"


def test_classify_none_drift_is_n_a():
    assert _classify_drift(None, "strict") == "n_a"


# ── Drift-label primitive (carried over) ────────────────────────────────

def test_drift_label_both_none():
    flag, drift = _drift_label(None, None)
    assert (flag, drift) == ("—", None)


def test_drift_label_byte_perfect():
    flag, drift = _drift_label(100.0, 100.05)
    assert flag == "✓"
    assert abs(drift) < 0.01


def test_drift_label_5pct_structural():
    flag, drift = _drift_label(110.0, 100.0)
    assert flag == "✗"
    assert abs(drift - 0.10) < 1e-9


def test_drift_label_units_bug_100x():
    """100x off = obvious unit confusion, hits structural."""
    flag, drift = _drift_label(100.0, 1.0)
    assert flag == "✗"
    assert drift == 99.0


# ── Gate A — validate_ingestion_record ──────────────────────────────────

def _stub_fmp_data(rev=395_000_000_000, ni=99_000_000_000,
                   ta=355_000_000_000, ebitda=137_000_000_000,
                   fcf=110_000_000_000, net_debt=-50_000_000_000):
    """Build a stub FMP fetch result matching what _fetch_fmp_for_gate_a returns."""
    return ({
        "income": {
            "revenue":           rev,
            "netIncome":         ni,
            "ebitda":            ebitda,
            "operatingIncome":   ni * 1.2,
            "weightedAverageShsOutDil": 15_000_000_000,
            "reportedCurrency":  "USD",
        },
        "balance": {
            "totalAssets":              ta,
            "totalLiabilities":         ta * 0.8,
            "totalStockholdersEquity":  ta * 0.2,
            "cashAndCashEquivalents":   30_000_000_000,
        },
        "cashflow": {
            "operatingCashFlow": fcf * 1.05,
            "freeCashFlow":      fcf,
        },
        "key_metrics": {
            "netDebt": net_debt,
        },
        "ratios": {
            "grossProfitMargin":     0.45,
            "operatingProfitMargin": 0.30,
        },
        "enterprise_values": {
            "enterpriseValue":        3_000_000_000_000,
            "marketCapitalization":   3_050_000_000_000,
            # implied NetDebt = -50B → matches stub_record default
            "numberOfShares":         15_000_000_000,
        },
    }, None)


def _stub_record(rev=395_000_000_000, ni=99_000_000_000,
                 ta=355_000_000_000, ebitda=137_000_000_000,
                 fcf=110_000_000_000, net_debt=-50_000_000_000):
    """Build a column-keyed dict mimicking CleanedRecord's DB view."""
    return {
        "clean_Revenue":           rev,
        "raw_NetIncome":           ni,
        "derived_EBITDA":          ebitda,
        "raw_OperatingIncome":     ni * 1.2,
        "raw_SharesDiluted":       15_000_000_000,
        "raw_TotalAssets":         ta,
        "raw_TotalLiabilities":    ta * 0.8,
        "raw_TotalEquity":         ta * 0.2,
        "raw_Cash":                30_000_000_000,
        "raw_OperatingCF":         fcf * 1.05,
        "derived_FCF":             fcf,
        "derived_NetDebt":         net_debt,
        "derived_GrossMargin_Pct":  45.0,
        "derived_EBIT_Margin_Pct":  30.0,
    }


def test_gate_a_byte_perfect_pass():
    """Identical values → status validated, no blocking, no drift."""
    rec = _stub_record()
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=_stub_fmp_data()):
        r = validate_ingestion_record("AAPL", 2024, rec, is_latest_fy=True)
    assert r["status"] == "validated"
    assert r["blocking_fields"] == []
    statuses = {f["status"] for f in r["fields"].values()}
    assert "structural_drift" not in statuses


def test_gate_a_blocking_on_revenue_drift_6pct():
    """6% revenue drift → blocking_drift; standard tier blocking field."""
    rec = _stub_record(rev=395_000_000_000)
    fmp_off = _stub_fmp_data(rev=395_000_000_000 * 1.06)
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=fmp_off):
        r = validate_ingestion_record("AAPL", 2024, rec, is_latest_fy=True)
    assert r["status"] == "blocking_drift"
    assert "revenue" in r["blocking_fields"]


def test_gate_a_drift_only_on_warn_field():
    """Drift on a non-blocking field (e.g. total_liabilities) → status=drift."""
    rec = _stub_record()
    fmp_off = _stub_fmp_data()
    fmp_off[0]["balance"]["totalLiabilities"] *= 1.07   # 7% drift on a non-blocking field
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=fmp_off):
        r = validate_ingestion_record("AAPL", 2024, rec, is_latest_fy=True)
    assert r["status"] == "drift"
    assert r["blocking_fields"] == []


def test_gate_a_historical_fy_validates_but_never_blocks():
    """is_latest_fy=False: drift is recorded, but never escalates to
    blocking_drift — re-cleaning a historical row should not break
    ingestion just because FMP renormalized something."""
    rec = _stub_record(rev=395_000_000_000)
    fmp_off = _stub_fmp_data(rev=395_000_000_000 * 1.06)  # 6% drift
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=fmp_off):
        r = validate_ingestion_record("AAPL", 2020, rec, is_latest_fy=False)
    assert r["status"] == "drift"
    assert r["blocking_fields"] == []
    assert r["is_latest_fy"] is False
    assert r["fields"]["revenue"]["status"] == "structural_drift"


def test_gate_a_ev_identity_check_byte_perfect():
    """fmp.EV - fmp.MktCap = -50B implied NetDebt; ours = -50B → match."""
    rec = _stub_record(net_debt=-50_000_000_000)
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=_stub_fmp_data()):
        r = validate_ingestion_record("AAPL", 2024, rec, is_latest_fy=True)
    f = r["fields"]["net_debt_via_ev_identity"]
    assert f["fmp"] == -50_000_000_000
    assert f["status"] == "byte_perfect"
    assert f["blocking"] is False


def test_gate_a_ev_identity_catches_netdebt_drift():
    """If our NetDebt is off and key-metrics drift slips through, the
    EV identity (independent FMP source) still flags it."""
    rec = _stub_record(net_debt=-30_000_000_000)  # 40% off the implied -50B
    fmp = _stub_fmp_data(net_debt=-30_000_000_000)  # primary check passes (matched)
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=fmp):
        r = validate_ingestion_record("AAPL", 2024, rec, is_latest_fy=True)
    primary = r["fields"]["net_debt"]
    derived = r["fields"]["net_debt_via_ev_identity"]
    assert primary["status"] == "byte_perfect"
    assert derived["status"] == "structural_drift"


def test_gate_a_shares_eop_check_present():
    rec = _stub_record()
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=_stub_fmp_data()):
        r = validate_ingestion_record("AAPL", 2024, rec, is_latest_fy=True)
    f = r["fields"]["shares_outstanding_eop"]
    assert f["fmp"] == 15_000_000_000
    assert f["tier"] == "definitional"
    assert f["blocking"] is False


def test_gate_a_ev_checks_n_a_when_endpoint_missing():
    """Legacy plans without /enterprise-values: derived checks are n_a,
    not exceptions — Gate A still completes."""
    fmp_no_ev = _stub_fmp_data()
    fmp_no_ev[0]["enterprise_values"] = {}
    rec = _stub_record()
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=fmp_no_ev):
        r = validate_ingestion_record("AAPL", 2024, rec, is_latest_fy=True)
    assert r["fields"]["net_debt_via_ev_identity"]["status"] == "n_a"
    assert r["fields"]["shares_outstanding_eop"]["status"] == "n_a"


def test_gate_a_historical_fy_validated_when_no_drift():
    """is_latest_fy=False with byte-perfect match → validated."""
    rec = _stub_record()
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=_stub_fmp_data()):
        r = validate_ingestion_record("AAPL", 2020, rec, is_latest_fy=False)
    assert r["status"] == "validated"
    assert r["is_latest_fy"] is False


def test_gate_a_skipped_on_fmp_quota_exhausted():
    rec = _stub_record()
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=(None, "fmp_quota_exhausted")):
        r = validate_ingestion_record("AAPL", 2024, rec, is_latest_fy=True)
    assert r["status"] == "skipped"
    assert r["skip_reason"] == "fmp_quota_exhausted"


def test_gate_a_skipped_on_currency_mismatch():
    rec = _stub_record()
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_a",
               return_value=(None, "fmp_currency_mismatch:EUR")):
        r = validate_ingestion_record("ASML", 2024, rec, is_latest_fy=True)
    assert r["status"] == "skipped"
    assert r["skip_reason"] == "fmp_currency_mismatch:EUR"


def test_ingestion_validation_failure_carries_blocking_fields():
    """The exception type the cleaner catches must include the blocking
    field names so logs can show them."""
    fake_result = {
        "status": "blocking_drift",
        "blocking_fields": ["revenue", "ebitda"],
    }
    exc = IngestionValidationFailure(
        ticker="ZZZ", fiscal_year=2024, result=fake_result,
    )
    assert "revenue" in str(exc) and "ebitda" in str(exc)


# ── Gate B — validate_calc_output ────────────────────────────────────────

def _stub_phase2(beta=1.10, current_price=150.0, market_cap=2_500e9):
    return {
        "dcf": {
            "beta":          beta,
            "current_price": current_price,
            "market_cap":    market_cap,
        },
        "multiple_decomposition": {"market_ev_ebitda": 18.0, "market_p_e": 28.0},
        "three_scenario_dcf": {
            "base": {"intrinsic_per_share": 200.0},
        },
    }


def _stub_gate_b_fmp(beta=1.10, price=150.0, market_cap=2_500e9, dcf=180.0):
    return ({
        "profile":     {"beta": beta, "price": price, "mktCap": market_cap, "dcf": dcf},
        "key_metrics": {"evToEBITDA": 18.0},
        "ratios":      {"priceToEarningsRatio": 28.0},
    }, None)


def test_gate_b_byte_perfect_validated():
    p2 = _stub_phase2()
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_b",
               return_value=_stub_gate_b_fmp()):
        r = validate_calc_output("AAPL", p2)
    assert r["status"] == "validated"
    assert r["blocking_fields"] == []


def test_gate_b_beta_drift_under_definitional_band_passes():
    """Beta is `definitional` tier (band 5%/25%); 4% drift = byte_perfect."""
    p2 = _stub_phase2(beta=1.10)
    fmp = _stub_gate_b_fmp(beta=1.05)   # ~4.8% drift, inside definitional byte-perfect band
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_b",
               return_value=fmp):
        r = validate_calc_output("AAPL", p2)
    assert r["status"] == "validated"


def test_gate_b_beta_uses_sanity_only_tier_never_blocks():
    """Beta tier was demoted from `definitional + blocking` to `sanity_only`.

    Rationale: FMP's /profile.beta is unreliable on defensive names
    (audit found JNJ at 0.26, MRK at 0.20 vs Bloomberg ~0.5). Beta
    drift between sources is expected and the WACC chain is already
    validated via current_price + market_cap which are truth values.
    Beta gets stamped for forensic visibility but never blocks Gate F.
    """
    p2 = _stub_phase2(beta=1.50)
    fmp = _stub_gate_b_fmp(beta=1.05)  # ~43% drift
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_b",
               return_value=fmp):
        r = validate_calc_output("AAPL", p2)
    # Sanity-only tier always classifies drift as byte_perfect; never blocks
    assert r["fields"]["beta"]["status"] == "byte_perfect"
    assert r["fields"]["beta"]["blocking"] is False
    assert "beta" not in r["blocking_fields"]


def test_gate_b_skipped_on_no_api_key():
    p2 = _stub_phase2()
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_b",
               return_value=(None, "fmp_api_key_not_configured")):
        r = validate_calc_output("AAPL", p2)
    assert r["status"] == "skipped"
    assert r["skip_reason"] == "fmp_api_key_not_configured"


def test_gate_b_phase2_fallback_paths():
    """phase2.beta works as a fallback when phase2.dcf.beta is missing."""
    p2 = {
        "beta":          1.10,    # top-level fallback
        "three_scenario_dcf": {"base": {"intrinsic_per_share": 200.0}},
        "multiple_decomposition": {},
    }
    with patch("aletheia.data.fmp_validation._fetch_fmp_for_gate_b",
               return_value=_stub_gate_b_fmp()):
        r = validate_calc_output("AAPL", p2)
    # beta was resolved via fallback to phase2.beta
    assert r["fields"]["beta"]["ours"] == 1.10


# ── Gate D — build_receipt_block ────────────────────────────────────────

def test_gate_d_aggregates_all_three_sub_blocks():
    state = {"_calc_validation": {"status": "validated", "fields": {}, "blocking_fields": []}}
    serving = {
        "4_valuation_synthesis": {
            "investment_thesis": {"narrative": "Some narrative."},
            "phase2_valuation": {
                "three_scenario_dcf": {
                    "bear": {"intrinsic_per_share": 100.0},
                    "base": {"intrinsic_per_share": 150.0},
                    "bull": {"intrinsic_per_share": 200.0},
                },
                "reverse_dcf": {},
            },
        },
    }
    ing = {"status": "validated", "blocking_fields": [], "fields": {}}
    receipt = build_receipt_block("AAPL", 2024, state, serving, ingestion_receipt=ing)
    assert receipt["schema_version"] == 2
    assert receipt["ingestion"]["status"] == "validated"
    assert receipt["calc"]["status"] == "validated"
    assert receipt["final_assembly"]["status"] == "validated"
    assert receipt["summary"]["any_blocking"] is False


def test_gate_d_detects_scenario_inversion():
    """bear > base inverts → final_assembly drift (warn-only)."""
    serving = {
        "4_valuation_synthesis": {
            "investment_thesis": {"narrative": ""},
            "phase2_valuation": {
                "three_scenario_dcf": {
                    "bear": {"intrinsic_per_share": 200.0},   # higher than base
                    "base": {"intrinsic_per_share": 150.0},
                    "bull": {"intrinsic_per_share": 250.0},
                },
                "reverse_dcf": {},
            },
        },
    }
    receipt = build_receipt_block(
        "AAPL", 2024,
        state={"_calc_validation": {"status": "validated", "fields": {}, "blocking_fields": []}},
        serving_report=serving,
        ingestion_receipt={"status": "validated", "blocking_fields": [], "fields": {}},
    )
    assert receipt["final_assembly"]["status"] == "drift"
    assert receipt["final_assembly"]["checks"]["scenario_monotonicity"]["ok"] is False


def test_gate_d_detects_implied_cagr_drift_in_narrative():
    """Narrative says 'implied CAGR 15.0%' but cited is 8.0% → blocking_drift."""
    serving = {
        "4_valuation_synthesis": {
            "investment_thesis": {
                "narrative": "Despite implied CAGR of 15.0% the math doesn't pencil.",
            },
            "phase2_valuation": {
                "three_scenario_dcf": {
                    "bear": {"intrinsic_per_share": 100.0},
                    "base": {"intrinsic_per_share": 150.0},
                    "bull": {"intrinsic_per_share": 200.0},
                },
                "reverse_dcf": {"implied_cagr_10y": 0.08, "historical_cagr": 0.05},
            },
        },
    }
    receipt = build_receipt_block(
        "AAPL", 2024,
        state={"_calc_validation": {"status": "validated", "fields": {}, "blocking_fields": []}},
        serving_report=serving,
        ingestion_receipt={"status": "validated", "blocking_fields": [], "fields": {}},
    )
    assert receipt["final_assembly"]["status"] == "blocking_drift"
    fid = receipt["final_assembly"]["checks"]["numeric_fidelity"]
    assert fid["violations"] >= 1
    assert any("15.0%" in d for d in fid["details"])


def test_gate_d_summary_any_blocking_true_when_any_subblock_blocks():
    serving = {
        "4_valuation_synthesis": {
            "investment_thesis": {"narrative": ""},
            "phase2_valuation": {
                "three_scenario_dcf": {"bear": {"intrinsic_per_share": 1.0},
                                         "base": {"intrinsic_per_share": 2.0},
                                         "bull": {"intrinsic_per_share": 3.0}},
                "reverse_dcf": {},
            },
        },
    }
    receipt = build_receipt_block(
        "AAPL", 2024,
        state={"_calc_validation": {"status": "blocking_drift",
                                     "fields": {}, "blocking_fields": ["beta"]}},
        serving_report=serving,
        ingestion_receipt={"status": "validated", "blocking_fields": [], "fields": {}},
    )
    assert receipt["summary"]["any_blocking"] is True
