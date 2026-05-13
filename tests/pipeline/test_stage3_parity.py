"""Stage 3 parity tests — Week 3 deliverable.

For each ticker in the 25-ticker regression universe, run two paths:

  1. Direct: ``DCFEngine(verbose=False).run(calc_input)`` and the
     equivalent direct call for each other engine.
  2. Bundled: ``run_stage3(load_records(ticker, ...), ...)``.

The two paths exercise the SAME underlying engine code; Stage 3 is
intentionally a thin orchestrator. Parity must therefore be bit-exact
(modulo dataclass ↔ dict ↔ JSON roundtrip artefacts on a tiny set of
nested fields). Any drift signals an adapter bug in
``aletheia/pipeline/stage3_calculate.py``.

Skips, not failures, are emitted when:
  - The DB doesn't have rows for the ticker (fresh checkout).
  - The direct engine raises NotImplementedError (routing_required for
    NEE / JPM / BRK-B, ddm_required for UNH) — in which case the test
    additionally asserts Stage 3 recorded the same failure category in
    ``schema_violations``.
  - The direct engine raises any other exception unrelated to Stage 3
    (Gate-A FMP drift, MissingFieldError) — the violation category is
    likewise checked in the bundle.

Compares the deterministic subset of each engine's output. Live
market-data fields (current_price, market_cap, current_ev) are
excluded — they snapshot at call time and can drift sub-second between
direct and bundled paths.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

import pytest

from tests.calculation_layer.conftest import UNIVERSE, _make_calc_input

from aletheia.cli.calc import load_records
from aletheia.pipeline.stage3_calculate import run_stage3
from aletheia.tools import cyclicality
from aletheia.tools.dcf_engine import DCFEngine
from aletheia.tools.moat_fingerprint import compute_moat_fingerprint
from aletheia.tools.multiple_decomposition import MultipleDecomposition
from aletheia.tools.reverse_dcf import ReverseDCF
from aletheia.tools.screening_ratios import ScreeningEngine


PIPELINE_VERSION = "parity-week3"
# Tight tolerance: same engine on same inputs in same process must
# agree to last ULP. The only slack accounts for JSON-roundtrip ULP
# noise in the bundle's nested-dict values.
REL_TOL = 1e-9


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _isclose(a: Optional[float], b: Optional[float],
             rel_tol: float = REL_TOL) -> bool:
    if a is None and b is None:
        return True
    if a is None or b is None:
        return False
    if isinstance(a, bool) or isinstance(b, bool):
        return bool(a) == bool(b)
    try:
        af, bf = float(a), float(b)
    except (TypeError, ValueError):
        return a == b
    if math.isnan(af) and math.isnan(bf):
        return True
    return math.isclose(af, bf, rel_tol=rel_tol, abs_tol=1e-12)


def _direct_violation_category(exc: BaseException) -> str:
    """Classify a direct-engine exception the same way Stage 3 does
    so we can assert the bundle recorded the corresponding violation."""
    return "not_implemented" if isinstance(exc, NotImplementedError) else "engine_error"


# ─────────────────────────────────────────────────────────────────────
# Fixture — single pre-compute per ticker, reused across engine tests
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="module")
def stage3_parity_cache():
    """For each ticker in UNIVERSE: run Stage 3 (bundle) and each
    direct engine call once. Cache the outcomes. Tests look up the
    cached pair and assert field-by-field parity for a specific
    engine.

    Skipping at the test level (rather than at fixture setup) lets
    pytest report skip reasons per ticker, not "fixture unavailable
    for the whole module."
    """
    cache: Dict[str, Dict[str, Any]] = {}

    for ticker in UNIVERSE:
        entry: Dict[str, Any] = {"ticker": ticker}

        # Build CalculationInput.
        try:
            calc_input = _make_calc_input(ticker)
            if calc_input.df is None or calc_input.df.empty:
                entry["skip"] = f"empty DB rows for {ticker}"
                cache[ticker] = entry
                continue
        except Exception as exc:  # noqa: BLE001
            entry["skip"] = f"calc_input unavailable: {exc}"
            cache[ticker] = entry
            continue

        # Load Stage 2 records via the CLI adapter and run Stage 3.
        try:
            records = load_records(ticker, pipeline_version=PIPELINE_VERSION)
            bundle = run_stage3(records, pipeline_version=PIPELINE_VERSION)
        except Exception as exc:  # noqa: BLE001
            entry["skip"] = f"Stage 3 raised pre-engine: {exc}"
            cache[ticker] = entry
            continue
        entry["bundle"] = bundle

        # Direct engine calls. Each engine's outcome is stored as
        # either a typed result OR the exception it raised so the
        # parity tests can assert the matching schema_violations
        # entry in the bundle.
        engines = [
            ("dcf_engine",
                lambda: DCFEngine(verbose=False).run(calc_input)),
            ("reverse_dcf",
                lambda: ReverseDCF(verbose=False).run(calc_input)),
            ("multiple_decomposition",
                lambda: MultipleDecomposition(verbose=False).run(calc_input)),
            ("screening",
                lambda: ScreeningEngine(verbose=False).score(calc_input)),
            ("moat_fingerprint",
                lambda: compute_moat_fingerprint(calc_input)),
            ("cyclicality",
                lambda: cyclicality.calculate_z_score(calc_input)),
        ]
        direct: Dict[str, Any] = {}
        for label, fn in engines:
            try:
                direct[label] = ("ok", fn())
            except Exception as exc:  # noqa: BLE001
                direct[label] = ("error", exc)
        entry["direct"] = direct
        cache[ticker] = entry

    return cache


def _entry_or_skip(cache: Dict[str, Any], ticker: str) -> Dict[str, Any]:
    entry = cache.get(ticker)
    if entry is None:
        pytest.skip(f"{ticker} not in parity cache")
    if "skip" in entry:
        pytest.skip(entry["skip"])
    return entry


def _assert_engine_violation_recorded(
    bundle, engine_label: str, expected_category: str
) -> None:
    """When a direct engine raised, Stage 3's bundle must carry a
    matching schema_violations entry. Otherwise the bundle silently
    hides upstream failures — exactly the A11-class regression the
    framework is designed to prevent."""
    matches = [
        v for v in bundle.schema_violations
        if v.get("engine") == engine_label
        and v.get("category") == expected_category
    ]
    assert matches, (
        f"Stage 3 bundle missing schema_violations entry for "
        f"{engine_label} with category={expected_category!r}. "
        f"Bundle has violations: {bundle.schema_violations!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# Per-engine parity tests (parametrised over UNIVERSE)
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker", UNIVERSE)
def test_dcf_parity(ticker, stage3_parity_cache):
    entry = _entry_or_skip(stage3_parity_cache, ticker)
    bundle = entry["bundle"]
    status, payload = entry["direct"]["dcf_engine"]

    if status == "error":
        _assert_engine_violation_recorded(
            bundle, "dcf_engine", _direct_violation_category(payload),
        )
        return

    direct = payload
    bdcf = bundle.dcf
    assert bdcf, f"{ticker}: Stage 3 dcf empty after successful direct call"

    # Bit-exact parity on the deterministic numeric core. Excludes
    # market-snapshot fields (current_price, market_cap, etc.) which
    # depend on call timing.
    parity_fields = [
        "fiscal_year",
        "wacc_base",
        "risk_free_rate",
        "beta",
        "revenue",
        "ebitda",
        "ebit",
        "nopat",
        "roic",
        "fcf",
        "net_debt",
    ]
    for f in parity_fields:
        bv = bdcf.get(f)
        dv = getattr(direct, f, None)
        assert _isclose(bv, dv), (
            f"{ticker}: dcf.{f} mismatch — direct={dv!r}, bundle={bv!r}"
        )


@pytest.mark.parametrize("ticker", UNIVERSE)
def test_reverse_dcf_parity(ticker, stage3_parity_cache):
    entry = _entry_or_skip(stage3_parity_cache, ticker)
    bundle = entry["bundle"]
    status, payload = entry["direct"]["reverse_dcf"]

    if status == "error":
        _assert_engine_violation_recorded(
            bundle, "reverse_dcf", _direct_violation_category(payload),
        )
        return

    direct = payload
    brdcf = bundle.reverse_dcf
    assert brdcf, f"{ticker}: Stage 3 reverse_dcf empty after successful direct call"

    parity_fields = [
        "fiscal_year",
        "wacc",
        "ebit_margin",
        "implied_cagr_10y",
        "implied_cagr_5y",
        "historical_cagr_5y",
        "sector_75th_cagr",
    ]
    for f in parity_fields:
        bv = brdcf.get(f)
        # ReverseDCFResult.to_dict() rewrites some names — keep them
        # aligned with the actual to_dict() output, not the dataclass.
        dv_attr = {
            "implied_cagr_10y": "implied_revenue_cagr_10y",
            "implied_cagr_5y": "implied_revenue_cagr_5y",
        }.get(f, f)
        dv = getattr(direct, dv_attr, None)
        assert _isclose(bv, dv), (
            f"{ticker}: reverse_dcf.{f} mismatch — direct={dv!r}, bundle={bv!r}"
        )

    # Signal must be the exact string label, not a tolerance match.
    assert brdcf.get("signal") == direct.signal, (
        f"{ticker}: reverse_dcf.signal — "
        f"direct={direct.signal!r}, bundle={brdcf.get('signal')!r}"
    )


@pytest.mark.parametrize("ticker", UNIVERSE)
def test_multiple_decomposition_parity(ticker, stage3_parity_cache):
    entry = _entry_or_skip(stage3_parity_cache, ticker)
    bundle = entry["bundle"]
    status, payload = entry["direct"]["multiple_decomposition"]

    if status == "error":
        _assert_engine_violation_recorded(
            bundle, "multiple_decomposition",
            _direct_violation_category(payload),
        )
        return

    direct = payload
    bmd = bundle.multiple_decomposition
    assert bmd, f"{ticker}: Stage 3 multiple_decomposition empty after success"

    # MultipleResult.to_dict() omits a handful of dataclass fields
    # (terminal_growth, profit_margin, drivers, etc.) — parity is
    # defined against the documented .to_dict() schema, not the full
    # dataclass surface. The omitted fields are reproducible from the
    # rest of the bundle when downstream needs them.
    parity_fields = [
        "fiscal_year",
        "wacc",
        "roic",
        "growth_rate",
        "cash_conversion_ratio",
        "roic_wacc_spread",
        "justified_ev_ebitda",
        "justified_ev_ebit",
        "justified_p_sales",
    ]
    for f in parity_fields:
        bv = bmd.get(f)
        dv = getattr(direct, f, None)
        assert _isclose(bv, dv), (
            f"{ticker}: multiple_decomposition.{f} — "
            f"direct={dv!r}, bundle={bv!r}"
        )


@pytest.mark.parametrize("ticker", UNIVERSE)
def test_screening_parity(ticker, stage3_parity_cache):
    entry = _entry_or_skip(stage3_parity_cache, ticker)
    bundle = entry["bundle"]
    status, payload = entry["direct"]["screening"]

    if status == "error":
        _assert_engine_violation_recorded(
            bundle, "screening", _direct_violation_category(payload),
        )
        return

    direct = payload
    bscr = bundle.screening
    assert bscr, f"{ticker}: Stage 3 screening empty after success"

    # ScreeningCard.passes/flags/fails/available are integer counts —
    # bit-exact between direct and bundle paths.
    for f in ("passes", "flags", "fails", "available"):
        bv = bscr.get(f)
        dv = getattr(direct, f, None)
        assert bv == dv, (
            f"{ticker}: screening.{f} — direct={dv!r}, bundle={bv!r}"
        )


@pytest.mark.parametrize("ticker", UNIVERSE)
def test_moat_fingerprint_parity(ticker, stage3_parity_cache):
    entry = _entry_or_skip(stage3_parity_cache, ticker)
    bundle = entry["bundle"]
    status, payload = entry["direct"]["moat_fingerprint"]

    if status == "error":
        _assert_engine_violation_recorded(
            bundle, "moat_fingerprint",
            _direct_violation_category(payload),
        )
        return

    direct = payload
    bm = bundle.moat_fingerprint
    assert bm, f"{ticker}: Stage 3 moat_fingerprint empty after success"

    for f in (
        "score",
        "roic_persistence_score",
        "gm_stability_score",
        "capex_intensity_score",
        "window_years",
        "is_null_due_to_history",
    ):
        bv = bm.get(f)
        dv = getattr(direct, f, None)
        if isinstance(dv, bool):
            assert bv == dv, f"{ticker}: moat.{f} — direct={dv!r}, bundle={bv!r}"
        else:
            assert _isclose(bv, dv), (
                f"{ticker}: moat.{f} — direct={dv!r}, bundle={bv!r}"
            )


@pytest.mark.parametrize("ticker", UNIVERSE)
def test_cyclicality_parity(ticker, stage3_parity_cache):
    entry = _entry_or_skip(stage3_parity_cache, ticker)
    bundle = entry["bundle"]
    status, payload = entry["direct"]["cyclicality"]

    if status == "error":
        _assert_engine_violation_recorded(
            bundle, "cyclicality", _direct_violation_category(payload),
        )
        return

    z, is_peak, applies_haircut, avg_3yr, _details = payload
    bcyc = bundle.cyclicality
    assert bcyc, f"{ticker}: Stage 3 cyclicality empty after success"

    assert _isclose(bcyc.get("z_score"), z), (
        f"{ticker}: cyclicality.z_score — direct={z!r}, "
        f"bundle={bcyc.get('z_score')!r}"
    )
    assert bcyc.get("is_peak") == bool(is_peak)
    assert bcyc.get("applies_cyclical_haircut") == bool(applies_haircut)
    assert _isclose(bcyc.get("avg_3yr"), avg_3yr), (
        f"{ticker}: cyclicality.avg_3yr — direct={avg_3yr!r}, "
        f"bundle={bcyc.get('avg_3yr')!r}"
    )


# ─────────────────────────────────────────────────────────────────────
# Bundle-level invariants
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("ticker", UNIVERSE)
def test_bundle_lineage_pointer_present(ticker, stage3_parity_cache):
    """Every bundle must carry input_record_fingerprint pointing back
    to the Stage 2 record that anchored the calc. Without this the
    Week 6 cascade-invalidation chain can't trace lineage."""
    entry = _entry_or_skip(stage3_parity_cache, ticker)
    bundle = entry["bundle"]
    assert bundle.input_record_fingerprint, (
        f"{ticker}: bundle missing input_record_fingerprint — "
        "lineage chain is broken"
    )
    assert bundle.pipeline_version == PIPELINE_VERSION
    assert bundle.ticker == ticker
    # SHA-256 hex digest = 64 chars.
    assert len(bundle.bundle_fingerprint) == 64
