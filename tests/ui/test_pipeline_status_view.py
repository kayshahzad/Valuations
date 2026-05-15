"""Tests for the Pipeline Status Matrix view.

The view assembles status-table data into a per-ticker row with stage
badges and (optionally) identity-audit summary. These tests verify the
data-extraction logic — formatters, classification lookup, and
session-cached identity-audit reads.
"""

from __future__ import annotations

from typing import Any, Dict, List


# ─────────────────────────────────────────────────────────────────────
# Streamlit mock — captures st.* calls for assertions
# ─────────────────────────────────────────────────────────────────────

class _StMock:
    def __init__(self):
        self.calls: List[Dict[str, Any]] = []
        # session_state must support dict-like access — many helpers
        # read st.session_state[key] OR st.session_state.attr.
        self.session_state: Dict[str, Any] = {}

    def __getattr__(self, name):
        if name == "session_state":
            return self.__dict__["session_state"]

        def _capture(*args, **kwargs):
            self.calls.append({"fn": name, "args": args, "kwargs": kwargs})
            if name == "columns":
                n = args[0] if args else 1
                if isinstance(n, list):
                    n = len(n)
                return [_StMock() for _ in range(n)]
            if name == "expander":
                class _Ctx:
                    def __enter__(self_inner): return _StMock()
                    def __exit__(self_inner, *a): return False
                return _Ctx()
            if name == "button":
                return False
            if name == "multiselect":
                return kwargs.get("default", [])
            return None
        return _capture


def _install_mock(monkeypatch) -> _StMock:
    mock = _StMock()
    import aletheia.ui.pipeline_status_view as mod
    monkeypatch.setattr(mod, "st", mock)
    return mock


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def test_fmt_relative_returns_dash_for_none():
    from aletheia.ui.pipeline_status_view import _fmt_relative
    assert _fmt_relative(None) == "—"
    assert _fmt_relative("") == "—"


def test_fmt_relative_handles_iso_timestamp():
    from aletheia.ui.pipeline_status_view import _fmt_relative
    # An obviously-old timestamp; should report d-ago
    out = _fmt_relative("2020-01-01T00:00:00+00:00")
    assert out.endswith("ago"), f"expected '... ago', got {out!r}"


def test_classification_returns_empty_for_unknown_ticker():
    from aletheia.ui.pipeline_status_view import _classification
    assert _classification("ZZZNOTREAL") == {}


def test_classification_includes_sector_for_universe_ticker():
    """Sector + lifecycle keys are present for any universe ticker.
    Specific values are config-driven so we only assert presence."""
    from aletheia.ui.pipeline_status_view import _classification
    c = _classification("AAPL")
    assert "sector" in c
    assert "lifecycle" in c


def test_identity_audit_summary_returns_none_without_bundle(monkeypatch):
    mock = _install_mock(monkeypatch)
    from aletheia.ui.pipeline_status_view import _identity_audit_summary
    assert _identity_audit_summary("META") is None


def test_identity_audit_summary_reads_session_bundle(monkeypatch):
    mock = _install_mock(monkeypatch)
    # Plant a Stage 3 bundle in session under the same key the
    # Stage Explorer uses, so the matrix sees it.
    mock.session_state["pipeline_explorer__META__stage3_calculate__bundle"] = {
        "accounting_identities": {
            "summary": {
                "n_checks": 119, "n_passed": 62,
                "n_expected_exception": 32,
                "n_failed": 0, "n_skipped": 25,
            },
        },
    }
    from aletheia.ui.pipeline_status_view import _identity_audit_summary
    s = _identity_audit_summary("META")
    assert s is not None
    assert s["n_passed"] == 62
    assert s["n_failed"] == 0


# ─────────────────────────────────────────────────────────────────────
# End-to-end matrix render
# ─────────────────────────────────────────────────────────────────────

def test_render_matrix_handles_empty_status_endpoint(monkeypatch):
    """When the /pipeline/status endpoint returns nothing (fresh DB),
    the view must still render — falling back to universe tickers
    with all-unknown badges, not raise."""
    mock = _install_mock(monkeypatch)
    import aletheia.ui.pipeline_status_view as mod
    monkeypatch.setattr(mod, "_fetch_status_matrix", lambda: [])

    mod.render_pipeline_status_matrix()
    # Verify it rendered without raising — the dataframe call carries
    # one row per universe ticker.
    df_calls = [c for c in mock.calls if c["fn"] == "dataframe"]
    assert df_calls, "render should produce at least one dataframe call"
    rows = df_calls[0]["args"][0]
    assert isinstance(rows, list)
    # Every row has the required columns
    if rows:
        first = rows[0]
        for col in ("Ticker", "Sector", "Lifecycle", "Stage 1",
                    "Stage 2", "Stage 3", "Stage 4",
                    "Identity Audit", "Last run"):
            assert col in first, f"missing column {col!r} in matrix row"


def test_render_matrix_displays_status_chips(monkeypatch):
    """Real status rows from the endpoint should translate into chip
    characters in the matrix rows."""
    mock = _install_mock(monkeypatch)
    import aletheia.ui.pipeline_status_view as mod
    monkeypatch.setattr(mod, "_fetch_status_matrix", lambda: [
        {"ticker": "META", "stage": "stage1_ingest", "status": "success",
         "last_run_at": "2026-05-13T00:00:00+00:00"},
        {"ticker": "META", "stage": "stage2_validate", "status": "failed",
         "last_run_at": "2026-05-13T00:01:00+00:00"},
        {"ticker": "META", "stage": "stage3_calculate", "status": "running",
         "last_run_at": None},
    ])

    mod.render_pipeline_status_matrix()
    df_calls = [c for c in mock.calls if c["fn"] == "dataframe"]
    assert df_calls
    rows = df_calls[0]["args"][0]
    meta_row = next((r for r in rows if r["Ticker"] == "META"), None)
    assert meta_row is not None
    assert meta_row["Stage 1"] == "🟢"
    assert meta_row["Stage 2"] == "🔴"
    assert meta_row["Stage 3"] == "🟡"
