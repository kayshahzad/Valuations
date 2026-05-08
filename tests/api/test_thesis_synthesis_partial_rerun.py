"""Phase 7 — partial-rerun endpoints: staleness + refresh.

Uses TestClient against the live app. Refresh tests stub the LLM call so
they don't hit Google. Staleness tests run against the live serving JSON
(the post-regen 25-ticker baseline) so they exercise real fingerprint
comparison logic.
"""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from api_main import app
    return TestClient(app)


# ── Staleness endpoint ───────────────────────────────────────────────────

def test_staleness_for_nonexistent_ticker(client):
    """No serving JSON → graceful response, not 404."""
    resp = client.get("/ticker/ZZZZZ/thesis_synthesis/staleness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["thesis_present"] is False
    assert body["reason"] == "no_report_yet"
    assert body["is_stale"] is False


def test_staleness_for_real_ticker_returns_fingerprints(client):
    """A live-universe ticker (NVDA has natural 4-dim coverage post-regen)."""
    # Use a ticker likely to have a serving JSON. If NVDA isn't ingested,
    # the test gracefully reports thesis_present=False instead of crashing.
    resp = client.get("/ticker/NVDA/thesis_synthesis/staleness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "NVDA"
    # Either thesis is present (with fingerprints) or it predates Phase 7
    # fingerprint stamping (in which case is_stale=True with that reason)
    if body["thesis_present"]:
        assert "current_fp" in body
        assert "thesis_fp" in body
        # Reason is one of the expected enum values
        assert body["reason"] in {
            "thesis_matches_current_dashboard",
            "dashboard_state_changed_since_thesis_generation",
            "thesis_predates_fingerprint_stamping",
        }


def test_staleness_when_thesis_predates_fingerprint(client, tmp_path, monkeypatch):
    """Reports generated before Phase 7 won't have a fingerprint stamped.
    Endpoint must report is_stale=True with reason=predates_fingerprint."""
    import aletheia.agents.state_hydration as sh_mod

    # Build a synthetic serving JSON in tmp_path with thesis_synthesis
    # but NO _metadata.dashboard_state_fingerprint
    report = {
        "ticker": "FAKE1",
        "1_economic_reality": {},
        "4_valuation_synthesis": {
            "thesis_synthesis": {
                "thesis_statement": "legacy thesis",
                "_metadata": {
                    "code_git_sha": "abc",
                    "generated_at": "2024-01-01T00:00:00+00:00",
                    # no dashboard_state_fingerprint
                },
            },
        },
    }
    fake_dir = tmp_path / "serving"
    fake_dir.mkdir()
    (fake_dir / "FAKE1_report.json").write_text(json.dumps(report))
    monkeypatch.setattr(sh_mod, "_SERVING_DIR", fake_dir)

    resp = client.get("/ticker/FAKE1/thesis_synthesis/staleness")
    assert resp.status_code == 200
    body = resp.json()
    assert body["thesis_present"] is True
    assert body["is_stale"] is True
    assert body["reason"] == "thesis_predates_fingerprint_stamping"


# ── Refresh endpoint — error paths ───────────────────────────────────────

def test_refresh_404_when_no_serving_json(client, monkeypatch):
    """No serving JSON → 404 with message directing caller to full pipeline."""
    # The endpoint pre-checks GOOGLE_API_KEY before checking the JSON.
    # Set a dummy key so we exercise the 404 path, not the 503 path.
    monkeypatch.setenv("GOOGLE_API_KEY", "dummy-test-key")
    resp = client.post("/ticker/ZZZZZ/thesis_synthesis/refresh")
    assert resp.status_code == 404
    detail = resp.json()["detail"]
    assert "full pipeline" in detail.lower()


def test_refresh_503_when_no_api_key(client, monkeypatch):
    """No GOOGLE_API_KEY → 503 BEFORE running the synthesizer.

    This protects against the data-loss bug where the synthesizer would
    silently fall back to mock and overwrite the real thesis.
    """
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    resp = client.post("/ticker/NVDA/thesis_synthesis/refresh")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "GOOGLE_API_KEY" in detail
    assert "mock" in detail.lower()


def test_refresh_503_when_synthesizer_returns_mock(
    client, tmp_path, monkeypatch,
):
    """If the synthesizer falls back to mock for any reason (validation
    failure across both retries, etc.), the endpoint must refuse to
    overwrite the existing thesis on disk and return 503."""
    import aletheia.agents.state_hydration as sh_mod
    import aletheia.agents.thesis_synthesizer as ts_mod

    monkeypatch.setenv("GOOGLE_API_KEY", "dummy-test-key")

    # Build a minimal serving JSON
    report = {
        "ticker": "MOCK1",
        "1_economic_reality": {
            "moat": {"score": 6.0, "has_pricing_power": True},
            "value_chain": {"upstream_leak": False},
            "strategic_context": {"terminal_haircut": False},
            "industry_structure": {"cyclicality_z_score": 0.5},
            "business_model": {},
        },
        "3_capital_structure_risk": {"concentration_risk": False},
        "4_valuation_synthesis": {
            "phase2_valuation": {
                "wacc": 0.08,
                "three_scenario_dcf": {
                    "base": {"intrinsic_per_share": 100, "margin_of_safety": 0.05},
                    "bear": {"intrinsic_per_share": 70, "margin_of_safety": -0.3},
                    "bull": {"intrinsic_per_share": 130, "margin_of_safety": 0.3},
                },
                "reverse_dcf": {"implied_cagr_10y": 0.12, "historical_cagr": 0.08,
                                "signal": "fair_value"},
                "multiple_decomposition": {},
            },
            "contrarian_analysis": {"bias_detected": "none", "sentiment_score": 0,
                                    "bear_case_summary": "ok"},
            "investment_thesis": {
                "pillar_scores": {"position_tier": "starter",
                                  "position_size_pct": 5,
                                  "conviction_score": 3,
                                  "capped_total": 12},
                # Pre-existing real thesis that must NOT be overwritten
                # (note: thesis_synthesis lives at the synthesis level, not
                # nested in investment_thesis — we set it on the parent below)
            },
            "thesis_synthesis": {
                "thesis_statement": "REAL thesis must survive failed refresh",
                "_metadata": {"dashboard_state_fingerprint": "realfp00000000"},
            },
            "agent_scenarios": [],
        },
    }
    fake_dir = tmp_path / "serving"
    fake_dir.mkdir()
    serving_path = fake_dir / "MOCK1_report.json"
    serving_path.write_text(json.dumps(report))
    monkeypatch.setattr(sh_mod, "_SERVING_DIR", fake_dir)

    # Stub synthesizer to return mock-fallback output
    def mock_synthesizer(_state):
        return {
            "thesis_synthesis": {
                "thesis_statement": "Mock thesis (no API key or call failure)",
                "bull_case": {"claim": "Mock", "cited_signals": ["mock"]},
                "bear_case": {"claim": "Mock", "cited_signals": ["mock"]},
                "base_case": {"claim": "Mock", "cited_signals": ["mock"]},
                "decision_conditions": [
                    {"trigger": "mock", "observable": "mock",
                     "action": "hold", "priority": "green"}
                    for _ in range(3)
                ],
                "thesis_confidence": "insufficient_signal",
                "time_horizon": "1_year",
                "position_sizing_implications": "mock",
                "required_analyst_judgment": [],
                "update_conditions": ["mock"],
                "_quality_flags": ["mock_fallback"],
            },
            "messages": [],
        }

    monkeypatch.setattr(ts_mod, "thesis_synthesizer_agent", mock_synthesizer)

    resp = client.post("/ticker/MOCK1/thesis_synthesis/refresh")
    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert "mock" in detail.lower()

    # CRITICAL: the real thesis on disk must be untouched
    on_disk = json.loads(serving_path.read_text())
    surviving = on_disk["4_valuation_synthesis"]["thesis_synthesis"]
    assert surviving["thesis_statement"] == "REAL thesis must survive failed refresh"
    assert surviving["_metadata"]["dashboard_state_fingerprint"] == "realfp00000000"


# ── Refresh endpoint — success path with stubbed LLM ─────────────────────

def test_refresh_uses_hydrated_state_no_full_rerun(client, tmp_path, monkeypatch):
    """Refresh must NOT re-run librarian/calc_node/qualitative_synthesis.
    Verify by stubbing thesis_synthesizer to record the state it received,
    then asserting that state was hydrated (not freshly computed)."""
    import aletheia.agents.state_hydration as sh_mod
    import aletheia.agents.thesis_synthesizer as ts_mod

    # Synthesize a serving JSON that the refresh will load
    report = {
        "ticker": "FAKE2",
        "1_economic_reality": {
            "moat": {"score": 6.0, "has_pricing_power": True},
            "value_chain": {"upstream_leak": False, "strategic_leverage": 7,
                           "substitution_risk_score": 4},
            "strategic_context": {"terminal_haircut": False},
            "industry_structure": {"cyclicality_z_score": 0.5, "is_peak": False},
            "business_model": {},
        },
        "3_capital_structure_risk": {"concentration_risk": False},
        "4_valuation_synthesis": {
            "phase2_valuation": {
                "wacc": 0.085,
                "three_scenario_dcf": {
                    "base": {"intrinsic_per_share": 100, "margin_of_safety": 0.05},
                    "bear": {"intrinsic_per_share": 70, "margin_of_safety": -0.3},
                    "bull": {"intrinsic_per_share": 130, "margin_of_safety": 0.3},
                },
                "reverse_dcf": {
                    "implied_cagr_10y": 0.12, "historical_cagr": 0.08,
                    "signal": "fair_value",
                },
                "multiple_decomposition": {"premium_pct": 0.1, "signal": "fair",
                                           "value_creation": "yes"},
            },
            "contrarian_analysis": {
                "bias_detected": "none", "sentiment_score": 0,
                "bear_case_summary": "ok",
            },
            "investment_thesis": {
                "pillar_scores": {"position_tier": "starter",
                                  "position_size_pct": 5,
                                  "conviction_score": 3, "capped_total": 12},
            },
            "agent_scenarios": [],
        },
    }
    fake_dir = tmp_path / "serving"
    fake_dir.mkdir()
    (fake_dir / "FAKE2_report.json").write_text(json.dumps(report))
    monkeypatch.setattr(sh_mod, "_SERVING_DIR", fake_dir)

    captured = {}

    def stub_synthesizer(state):
        captured["state"] = state
        return {
            "thesis_synthesis": {
                "thesis_statement": "Refreshed thesis statement.",
                "bull_case": {"claim": "x", "cited_signals": ["phase2.x"]},
                "bear_case": {"claim": "y", "cited_signals": ["contrarian.bias_detected"]},
                "base_case": {"claim": "z", "cited_signals": ["phase2.three_scenario_dcf.base"]},
                "decision_conditions": [
                    {"trigger": "t", "observable": "o", "action": "hold", "priority": "green"},
                    {"trigger": "t2", "observable": "o2", "action": "exit", "priority": "red"},
                    {"trigger": "t3", "observable": "o3", "action": "trim", "priority": "amber"},
                ],
                "thesis_confidence": "low",
                "time_horizon": "1_year",
                "position_sizing_implications": "starter",
                "required_analyst_judgment": [],
                "update_conditions": ["change"],
                "_metadata": {
                    "dashboard_state_fingerprint": "newfingerprint01",
                    "generated_at": "2026-05-08T12:00:00+00:00",
                    "coverage_state": "zero",
                },
            },
            "messages": [],
        }

    monkeypatch.setattr(
        "api_main.thesis_synthesizer_agent",
        stub_synthesizer,
        raising=False,
    )
    # The endpoint imports inside the function — patch at module level too
    monkeypatch.setattr(ts_mod, "thesis_synthesizer_agent", stub_synthesizer)
    import importlib
    import api_main
    importlib.reload  # noqa — placeholder to ensure module is loaded

    resp = client.post("/ticker/FAKE2/thesis_synthesis/refresh")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "refreshed"
    assert body["thesis_synthesis"]["thesis_statement"] == "Refreshed thesis statement."

    # State the synthesizer saw must come from hydration — phase2 wac/cagr
    # values are exactly what the serving JSON had
    state = captured["state"]
    assert state["phase2_valuation"]["wacc"] == 0.085
    assert state["phase2_valuation"]["implied_cagr"] == 0.12
    # qualitative_dashboard injected by dashboard_fetch_node
    assert "qualitative_dashboard" in state

    # Serving JSON must have been patched
    patched = json.loads((fake_dir / "FAKE2_report.json").read_text())
    new_thesis = patched["4_valuation_synthesis"]["thesis_synthesis"]
    assert new_thesis["thesis_statement"] == "Refreshed thesis statement."
    # Phase 2 untouched
    assert patched["4_valuation_synthesis"]["phase2_valuation"]["wacc"] == 0.085
