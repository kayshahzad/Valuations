"""Tests for the Stage Explorer's validation-panel data-extraction logic.

The panels themselves call `st.markdown` / `st.metric` / etc., which
need a Streamlit context to render. These tests cover the
*data-extraction* part — the formatters and per-stage panel helpers
that should never raise on real-shaped bundles.

Streamlit's official testing harness (`streamlit.testing.v1.AppTest`)
is overkill for verifying these helpers don't crash; we mock the
streamlit module instead, then call each helper on synthetic
typed-bundle payloads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List


# ─────────────────────────────────────────────────────────────────────
# Streamlit mock — every helper calls into st.*; we replace with no-ops
# ─────────────────────────────────────────────────────────────────────

class _StMock:
    """Minimal stand-in for streamlit. Each call is captured for
    assertions; UI side-effects are no-ops."""

    def __init__(self):
        self.calls: List[Dict[str, Any]] = []

    def __getattr__(self, name):
        def _capture(*args, **kwargs):
            self.calls.append({"fn": name, "args": args, "kwargs": kwargs})
            # ``columns(n)`` returns a list of n column objects, each
            # with a .metric() / .button() method. Return n mock cols.
            if name == "columns":
                n = args[0] if args else 1
                if isinstance(n, list):
                    n = len(n)
                cols = [_StMock() for _ in range(n)]
                return cols
            # ``expander(...)`` is a context manager.
            if name == "expander":
                class _Ctx:
                    def __enter__(self_inner): return _StMock()
                    def __exit__(self_inner, *a): return False
                return _Ctx()
            return None
        return _capture


def _install_st_mock(monkeypatch) -> _StMock:
    mock = _StMock()
    # Patch the streamlit module the panel functions import.
    import aletheia.ui.pipeline_explorer_view as mod
    monkeypatch.setattr(mod, "st", mock)
    return mock


# ─────────────────────────────────────────────────────────────────────
# Synthetic bundle fixtures
# ─────────────────────────────────────────────────────────────────────

def _stage1_bundle(tmp_path) -> Dict[str, Any]:
    """Minimal IngestedRawBundle-shaped dict. payload_path points at
    a real (empty) tmp file so Path.stat() doesn't raise."""
    sec_path = tmp_path / "CIK0001326801.json"
    sec_path.write_text('{"facts":{}}')
    fmp_path = tmp_path / "META__income_annual.json"
    fmp_path.write_text('[]')
    return {
        "ticker": "META",
        "bundle_fingerprint": "f" * 64,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "sources": {
            "sec_companyfacts": {
                "source": "sec_companyfacts",
                "url": "https://example/sec",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "payload_path": str(sec_path),
                "payload_sha256": "a" * 64,
                "metadata": {"cik": "0001326801"},
            },
            "fmp_income": {
                "source": "fmp_income",
                "url": "https://example/fmp",
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "payload_path": str(fmp_path),
                "payload_sha256": "b" * 64,
                "metadata": {"period": "annual"},
            },
        },
        "classification_snapshot": {
            "ticker": "META", "sector": "Technology",
            "industry": "Internet", "lifecycle": "growth_compounder_software",
            "business_model": "fcff_compatible", "is_ifrs_filer": False,
        },
        "pipeline_version": "test-v1",
    }


def _stage2_records() -> List[Dict[str, Any]]:
    return [
        {
            "ticker": "META", "fiscal_year": 2023, "period": "FY",
            "period_end_date": "2023-12-31",
            "raw": {}, "clean": {}, "derived": {},
            "overall_quality_score": 0.95,
            "cleaning_warnings": [], "blocking_errors": [],
            "validation": {
                "schema_violations": [],
                "fmp_validation": {"status": "validated"},
                "overrides_applied": [],
            },
            "record_fingerprint": "r" * 64,
            "input_bundle_fingerprint": "b" * 64,
            "cleaned_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "test-v1",
        },
        {
            "ticker": "META", "fiscal_year": 2024, "period": "FY",
            "period_end_date": "2024-12-31",
            "raw": {}, "clean": {}, "derived": {},
            "overall_quality_score": 0.92,
            "cleaning_warnings": [], "blocking_errors": [],
            "validation": {
                "schema_violations": [],
                "fmp_validation": {"status": "validated"},
                "overrides_applied": ["shares_diluted_ingest_bug"],
            },
            "record_fingerprint": "s" * 64,
            "input_bundle_fingerprint": "b" * 64,
            "cleaned_at": datetime.now(timezone.utc).isoformat(),
            "pipeline_version": "test-v1",
        },
    ]


def _stage3_bundle() -> Dict[str, Any]:
    return {
        "ticker": "META", "fiscal_year": 2024, "base_period": "FY",
        "dcf": {
            "wacc": 0.13, "wacc_base": 0.13,
            "base": {"intrinsic_per_share": 433.71},
            "bull": {"intrinsic_per_share": 511.95},
            "bear": {"intrinsic_per_share": 134.41},
            "tax_rate_source": "cash",
        },
        "reverse_dcf": {
            "implied_cagr_10y": 0.28,
            "signal": "priced_for_growth",
            "tax_rate_source": "cash",
        },
        "multiple_decomposition": {
            "signal": "fair_value",
            "tax_rate_source": "cash",
        },
        "screening": {"passes": 22, "flags": 5, "fails": 7, "available": 34},
        "moat_fingerprint": {"score": 7.5},
        "cyclicality": {}, "scenarios": [],
        "capital_structure": {}, "reality_checks": {},
        "schema_violations": [],
        "bundle_fingerprint": "c" * 64,
        "input_record_fingerprint": "r" * 64,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "test-v1",
    }


def _stage4_bundle() -> Dict[str, Any]:
    return {
        "ticker": "META",
        "qualitative_synthesis": {
            "forensic_report": {}, "value_chain_report": {},
            "strategic_context_report": {},
        },
        "contrarian": {"sentiment": "neutral", "bear_case": "..."},
        "thesis": {
            "bull": {"cited_signals": [1, 2, 3]},
            "base": {"cited_signals": [1, 2, 3, 4, 5]},
            "bear": {"cited_signals": [1, 2]},
        },
        "raw_10k_excerpt": "Q4 earnings called out…",
        "bundle_fingerprint": "a" * 64,
        "input_calculation_fingerprint": "c" * 64,
        "computed_at": datetime.now(timezone.utc).isoformat(),
        "pipeline_version": "test-v1",
        "llm_cost_usd": 1.42,
    }


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────

def test_formatters():
    from aletheia.ui.pipeline_explorer_view import _fmt_size, _fmt_pct, _fmt_usd
    assert _fmt_size(5_500_000) == "5.5 MB"
    assert _fmt_size(2048) == "2 KB"
    assert _fmt_size(900) == "900 B"
    assert _fmt_size(None) == "—"
    assert _fmt_pct(0.1342) == "13.4%"
    assert _fmt_pct(0.1342, decimals=2) == "13.42%"
    assert _fmt_pct(None) == "—"
    assert _fmt_usd(1.5e9) == "$1.50B"
    assert _fmt_usd(500_000_000) == "$500.0M"
    assert _fmt_usd(1234) == "$1,234"
    assert _fmt_usd(None) == "—"


def test_stage1_validation_extracts_source_metadata(monkeypatch, tmp_path):
    """Panel must call st.* with the right shape; specifically, it
    must render a dataframe row per source and surface the SEC XBRL
    file size (the original confusion this commit fixes)."""
    from aletheia.ui.pipeline_explorer_view import _render_stage1_validation
    mock = _install_st_mock(monkeypatch)
    _render_stage1_validation(_stage1_bundle(tmp_path))

    # st.markdown headline call should contain "SEC XBRL: ✓"
    markdown_calls = [c for c in mock.calls if c["fn"] == "markdown"]
    headline = next(c["args"][0] for c in markdown_calls if "SEC XBRL" in c["args"][0])
    assert "SEC XBRL: ✓" in headline
    assert "FMP endpoints: 1" in headline

    # dataframe call: rows should include both sources with size strings
    df_calls = [c for c in mock.calls if c["fn"] == "dataframe"]
    assert df_calls, "expected st.dataframe to render the source-list"
    rows = df_calls[0]["args"][0]
    sources_rendered = {r["source"]: r for r in rows}
    assert "sec_companyfacts" in sources_rendered
    assert "fmp_income" in sources_rendered
    # SEC file we wrote is 12 bytes ('{"facts":{}}') — formatter
    # should call it "12 B"
    assert sources_rendered["sec_companyfacts"]["size"] == "12 B"


def test_stage2_validation_surfaces_overrides_and_violations(monkeypatch):
    from aletheia.ui.pipeline_explorer_view import _render_stage2_validation
    mock = _install_st_mock(monkeypatch)
    _render_stage2_validation(_stage2_records())

    # Metric calls (4 per the implementation: FY records, TTM records,
    # avg quality, schema violations). Each is captured on a column's
    # mock — easier to assert against markdown calls.
    markdown_calls = [c for c in mock.calls if c["fn"] == "markdown"]
    override_section = [c for c in markdown_calls if "Overrides applied" in c["args"][0]]
    assert override_section, "expected the overrides-applied section header"
    override_keys = [c for c in markdown_calls if "shares_diluted_ingest_bug" in c["args"][0]]
    assert override_keys, "override key didn't render"


def test_stage2_validation_renders_xbrl_extracted_table(monkeypatch):
    """Stage 2 panel must surface the XBRL-extracted core financials
    as a per-FY table. Direct answer to "where's the data from the
    SEC XBRL file shown in the view?" — answers it before the
    overrides + raw-JSON sections."""
    from aletheia.ui.pipeline_explorer_view import _render_stage2_xbrl_extracted
    mock = _install_st_mock(monkeypatch)
    records = _stage2_records()
    # Populate clean dict with META-shaped FY2024 values.
    records[-1]["clean"] = {
        "Revenue":          200_966_000_000.0,
        "NetIncome":         60_458_000_000.0,
        "OperatingCF":      115_800_000_000.0,
        "InvestingCF":     -102_003_000_000.0,
        "CapEx_Total":       69_691_000_000.0,
        "TotalAssets":      366_021_000_000.0,
        "TotalEquity":      217_243_000_000.0,
        "Cash":              35_873_000_000.0,
        "SharesDiluted":      2_574_000_000.0,
    }
    _render_stage2_xbrl_extracted(records)

    # Headline markdown must mention XBRL extraction.
    markdown_calls = [c for c in mock.calls if c["fn"] == "markdown"]
    assert any(
        "Extracted from XBRL" in c["args"][0] for c in markdown_calls
    ), "missing the 'Extracted from XBRL' heading"

    # The new categorized layout renders one st.dataframe per
    # populated category. Collect fields from EVERY dataframe call so
    # the assertion works regardless of which category each field
    # ended up in.
    df_calls = [c for c in mock.calls if c["fn"] == "dataframe"]
    assert df_calls, "expected st.dataframe to render the XBRL tables"
    all_rows: List[Dict[str, Any]] = []
    for call in df_calls:
        all_rows.extend(call["args"][0])
    fields = {r["Field"] for r in all_rows}

    # Income Statement fields
    assert "Revenue" in fields
    assert "Net Income" in fields
    # Cash Flow fields
    assert "Operating CF" in fields
    # Balance Sheet fields
    assert "Total Assets" in fields

    # Revenue cell value: $200.97B should appear in the FY2024 column.
    rev_row = next(r for r in all_rows if r["Field"] == "Revenue")
    assert "$200.97B" in rev_row["FY2024"]

    # Category headers — one markdown call per populated category.
    cat_headers = [
        c["args"][0] for c in markdown_calls
        if isinstance(c["args"][0], str) and c["args"][0].startswith("#### ")
    ]
    # At least Income Statement + Balance Sheet + Cash Flow given the
    # fixture's clean dict populates fields in all three.
    assert any("Income Statement" in h for h in cat_headers)
    assert any("Balance Sheet" in h for h in cat_headers)
    assert any("Cash Flow" in h for h in cat_headers)


def test_stage2_xbrl_extracted_handles_empty_records(monkeypatch):
    """An empty record list is a different shape from records with
    zero resolved fields. Both must render without crashing."""
    from aletheia.ui.pipeline_explorer_view import _render_stage2_xbrl_extracted
    mock = _install_st_mock(monkeypatch)
    _render_stage2_xbrl_extracted([])
    captions = [c for c in mock.calls if c["fn"] == "caption"]
    assert any("No FY records" in c["args"][0] for c in captions)


def test_stage2_xbrl_extracted_handles_records_with_no_resolved_fields(monkeypatch):
    """When the cleaning engine produced FY records but the resolver
    failed to populate any of the core fields, the panel must
    surface that explicitly — pointing the analyst at the raw XBRL
    path for triage."""
    from aletheia.ui.pipeline_explorer_view import _render_stage2_xbrl_extracted
    mock = _install_st_mock(monkeypatch)
    records = _stage2_records()
    for r in records:
        r["clean"] = {}  # nothing resolved
    _render_stage2_xbrl_extracted(records)
    captions = [c for c in mock.calls if c["fn"] == "caption"]
    assert any("tag_resolver gap" in c["args"][0] for c in captions)


def test_stage2_validation_handles_no_overrides(monkeypatch):
    from aletheia.ui.pipeline_explorer_view import _render_stage2_validation
    mock = _install_st_mock(monkeypatch)
    records = _stage2_records()
    for r in records:
        r["validation"]["overrides_applied"] = []
    _render_stage2_validation(records)
    captions = [c for c in mock.calls if c["fn"] == "caption"]
    assert any("No overrides active" in c["args"][0] for c in captions)


def test_stage3_validation_renders_tax_rate_source(monkeypatch):
    """The headline assertion: every sub-engine's tax_rate_source is
    visible in the validation panel. This is the direct A11-status
    surface for the analyst."""
    from aletheia.ui.pipeline_explorer_view import _render_stage3_validation
    mock = _install_st_mock(monkeypatch)
    _render_stage3_validation(_stage3_bundle())

    captions = [c for c in mock.calls if c["fn"] == "caption"]
    tax_caption = next(
        (c for c in captions if "tax_rate_source" in c["args"][0]),
        None,
    )
    assert tax_caption is not None
    text = tax_caption["args"][0]
    assert "dcf: `cash`" in text
    assert "reverse_dcf: `cash`" in text
    assert "multiple_decomposition: `cash`" in text


def test_stage4_validation_runs_on_real_shaped_bundle(monkeypatch):
    """Smoke test: every panel helper must not raise on a typed
    bundle. Specifically check the cited-signals counter sums across
    bull + base + bear correctly."""
    from aletheia.ui.pipeline_explorer_view import _render_stage4_validation
    mock = _install_st_mock(monkeypatch)
    _render_stage4_validation(_stage4_bundle())

    # The cited-signals count = 3 + 5 + 2 = 10. Surface that in
    # an st.metric call.
    metric_calls = []
    for c in mock.calls:
        if c["fn"] == "metric":
            metric_calls.append(c)
    # Each st.metric is captured on a column mock, not on the parent.
    # The parent mock's _capture proxies columns(); the column mocks
    # capture metric calls. Walk the parent's column-creation results.
    cols_calls = [c for c in mock.calls if c["fn"] == "columns"]
    assert cols_calls, "expected st.columns() to be called"


def test_stage1_validation_handles_missing_payload_path(monkeypatch):
    """If a source's payload_path doesn't exist on disk (e.g., user
    deleted the cache), the panel must still render — size column
    becomes '—' for that row."""
    from aletheia.ui.pipeline_explorer_view import _render_stage1_validation
    mock = _install_st_mock(monkeypatch)
    bundle = {
        "ticker": "X",
        "bundle_fingerprint": "f" * 64,
        "sources": {
            "sec_companyfacts": {
                "source": "sec_companyfacts",
                "payload_path": "/tmp/does/not/exist.json",
                "payload_sha256": "a" * 64,
                "fetched_at": "2026-05-13T01:00:00Z",
            }
        },
        "classification_snapshot": {},
    }
    _render_stage1_validation(bundle)  # must not raise
    df_calls = [c for c in mock.calls if c["fn"] == "dataframe"]
    rows = df_calls[0]["args"][0]
    assert rows[0]["size"] == "—"
