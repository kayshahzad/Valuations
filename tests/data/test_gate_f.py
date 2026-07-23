"""Gate F (aggregate_universe) — universe-level validation aggregation.

Pins the threshold matrix branches, especially the TIER FILTER: only
strict/standard *blocking* fields can FAIL; definitional / non-blocking fields
(market_p_e, market_ev_ebitda, beta) are reported for context but never gated.
"""
from __future__ import annotations

import json

from aletheia.data.fmp_validation import aggregate_universe


def _field(*, tier, blocking, status, drift):
    return {"ours": 1.0, "fmp": 1.0, "drift_pct": drift, "tier": tier,
            "blocking": blocking, "status": status}


def _write(dirpath, ticker, *, calc_status="validated", fields=None,
           schema_version=2, ingestion_status="validated"):
    v = {
        "schema_version": schema_version, "ticker": ticker,
        "ingestion": {"status": ingestion_status},
        "calc": {"status": calc_status, "fields": fields or {}},
    }
    (dirpath / f"{ticker}_report.json").write_text(json.dumps({"_validation": v}))


def test_all_clean_passes(tmp_path):
    for t in ("AAA", "BBB", "CCC"):
        _write(tmp_path, t, calc_status="validated")
    r = aggregate_universe(tmp_path)
    assert r["verdict"] == "PASS"
    assert r["universe_n"] == 3


def test_definitional_and_nonblocking_drift_never_gates(tmp_path):
    # market_p_e / market_ev_ebitda: standard tier but blocking=False.
    # beta: definitional. All must be context-only, verdict PASS.
    for t in [f"T{i:02d}" for i in range(20)]:
        _write(tmp_path, t, calc_status="drift", fields={
            "market_p_e": _field(tier="standard", blocking=False, status="structural_drift", drift=2.1),
            "market_ev_ebitda": _field(tier="standard", blocking=False, status="structural_drift", drift=2.5),
            "beta": _field(tier="definitional", blocking=True, status="structural_drift", drift=-0.34),
        })
    r = aggregate_universe(tmp_path)
    assert r["verdict"] == "PASS", r["reasons"]
    assert r["gated_field_stats"] == {}
    assert set(r["context_fields"]) == {"market_p_e", "market_ev_ebitda", "beta"}


def test_systematic_gated_drift_fails(tmp_path):
    # A strict blocking field drifting on >=25% of the universe = systematic bug.
    for i in range(20):
        drift = 0.08 if i < 6 else 0.0        # 6/20 = 30% >= 25%
        status = "structural_drift" if i < 6 else "byte_perfect"
        _write(tmp_path, f"T{i:02d}", calc_status="drift", fields={
            "fcf": _field(tier="strict", blocking=True, status=status, drift=drift)})
    r = aggregate_universe(tmp_path)
    assert r["verdict"] == "FAIL"
    assert "fcf" in r["systematic_fields"]


def test_isolated_gated_drift_warns(tmp_path):
    # One strict blocking drift, below systematic threshold, no blocking_drift.
    for i in range(20):
        status = "structural_drift" if i == 0 else "byte_perfect"
        _write(tmp_path, f"T{i:02d}", calc_status="drift", fields={
            "fcf": _field(tier="strict", blocking=True, status=status, drift=0.08 if i == 0 else 0.0)})
    r = aggregate_universe(tmp_path)
    assert r["verdict"] == "WARN"
    assert r["systematic_fields"] == []


def test_blocking_drift_on_gated_field_fails(tmp_path):
    _write(tmp_path, "AAA", calc_status="validated")
    _write(tmp_path, "BBB", calc_status="blocking_drift", fields={
        "revenue": _field(tier="strict", blocking=True, status="structural_drift", drift=0.09)})
    r = aggregate_universe(tmp_path)
    assert r["verdict"] == "FAIL"
    assert r["blocking_reports"][0]["ticker"] == "BBB"


def test_high_skip_rate_fails(tmp_path):
    for i in range(10):
        _write(tmp_path, f"T{i}", calc_status="skipped" if i < 5 else "validated")
    r = aggregate_universe(tmp_path)              # 50% skipped > 40% ceiling
    assert r["verdict"] == "FAIL"
    assert r["skip_rate"] == 0.5


def test_report_only_caps_fail_to_warn(tmp_path):
    _write(tmp_path, "BBB", calc_status="blocking_drift", fields={
        "revenue": _field(tier="strict", blocking=True, status="structural_drift", drift=0.09)})
    r = aggregate_universe(tmp_path, report_only=True)
    assert r["verdict"] == "FAIL"
    assert r["effective_verdict"] == "WARN"


def test_malformed_and_old_schema_counted_not_crashed(tmp_path):
    _write(tmp_path, "AAA", calc_status="validated")
    _write(tmp_path, "OLD", schema_version=1)          # old schema
    (tmp_path / "BAD_report.json").write_text("{not json")
    r = aggregate_universe(tmp_path)
    assert r["universe_n"] == 1
    assert r["malformed"] == 2
