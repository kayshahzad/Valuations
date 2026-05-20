"""Architecture lock — prevents qualitative-tab re-fragmentation.

The Phase A-D qualitative-tab wiring completed every catalog dim:
  - DETERMINISTIC dims (5): computed by ``aletheia.qualitative.computers``
  - LLM_AUGMENTED dims (5): extracted by Phase B (10-K bundle) and
    Phase C (DEF 14A bundle) extractors
  - HITL dims (9): analyst-submitted via the qualitative_input form
  - PENDING_DATA dims (0): structurally eliminated in Phase D

This test enforces the wiring invariants so a future engineer can't
silently:
  - Add a catalog dim without registering its producer
  - Add an extractor / computer without a matching catalog entry
  - Drift the BUNDLE_DIMS tuple from the bundle schema's top-level
    fields
  - Re-introduce a PENDING_DATA dim (regresses the Phase D promise)

Same enforcement pattern as ``test_no_resurrected_agents.py`` and
``test_single_formula_source.py``. Pure import + AST inspection; no
I/O, no DB, no LLM.
"""

from aletheia.qualitative.computers import COMPUTERS
from aletheia.qualitative.extractors import (
    BUNDLE_DIMS, DEF14A_BUNDLE_DIMS, EXTRACTORS,
)
from aletheia.qualitative.extractors.def14a_schemas import (
    Def14aExtractionBundle,
)
from aletheia.qualitative.extractors.schemas import (
    QualitativeExtractionBundle,
)
from aletheia.qualitative.types import SourceCategory
from config.qualitative_dimensions import DIMENSIONS


# ── Invariant 1: every DETERMINISTIC dim has a registered computer ──


def test_every_deterministic_dim_has_computer():
    """A catalog entry marked DETERMINISTIC must have an entry in
    ``aletheia.qualitative.computers.COMPUTERS``. Otherwise the
    runner has no way to produce its score and the dashboard shows
    a permanent ``not_assessed`` state for that dim across the
    universe — silent coverage rot."""
    missing = []
    for dim_id, entry in DIMENSIONS.items():
        if entry.source_category != SourceCategory.DETERMINISTIC:
            continue
        if dim_id not in COMPUTERS:
            missing.append(dim_id)

    assert not missing, (
        "DETERMINISTIC catalog dims without a registered computer:\n  "
        + "\n  ".join(missing)
        + "\nAdd the computer to aletheia/qualitative/computers/ and "
          "register it in COMPUTERS, OR change the catalog dim's "
          "source_category."
    )


def test_every_computer_has_catalog_entry():
    """The reverse — no orphaned computer. If you register a
    computer in COMPUTERS without a matching catalog dim, the
    runner will write rows the dashboard never displays."""
    unknown = []
    for dim_id in COMPUTERS:
        if dim_id not in DIMENSIONS:
            unknown.append(dim_id)
        elif DIMENSIONS[dim_id].source_category != SourceCategory.DETERMINISTIC:
            unknown.append(
                f"{dim_id} (catalog says "
                f"{DIMENSIONS[dim_id].source_category.value}, not deterministic)"
            )
    assert not unknown, (
        "Computers registered without a matching DETERMINISTIC "
        "catalog entry:\n  " + "\n  ".join(unknown)
    )


# ── Invariant 2: every LLM_AUGMENTED dim has a registered producer ──


def test_every_llm_augmented_dim_has_producer():
    """A catalog entry marked LLM_AUGMENTED must be produced by
    EITHER the Phase B bundle (BUNDLE_DIMS), the Phase C bundle
    (DEF14A_BUNDLE_DIMS), or a per-dim extractor in EXTRACTORS.
    No orphans — every LLM_AUGMENTED slot has an extractor that
    can fill it."""
    bundle_set = set(BUNDLE_DIMS) | set(DEF14A_BUNDLE_DIMS) | set(EXTRACTORS)
    missing = []
    for dim_id, entry in DIMENSIONS.items():
        if entry.source_category != SourceCategory.LLM_AUGMENTED:
            continue
        if dim_id not in bundle_set:
            missing.append(dim_id)

    assert not missing, (
        "LLM_AUGMENTED catalog dims without a registered extractor:\n  "
        + "\n  ".join(missing)
        + "\nAdd the dim to BUNDLE_DIMS / DEF14A_BUNDLE_DIMS / "
          "EXTRACTORS and extend the corresponding schema + prompt."
    )


def test_every_bundle_dim_has_catalog_entry():
    """The reverse — no bundle field that doesn't have a catalog dim.
    Both BUNDLE_DIMS + DEF14A_BUNDLE_DIMS members must be LLM_AUGMENTED
    in the catalog."""
    orphans = []
    for dim_id in (*BUNDLE_DIMS, *DEF14A_BUNDLE_DIMS):
        if dim_id not in DIMENSIONS:
            orphans.append(f"{dim_id} (not in catalog)")
        elif DIMENSIONS[dim_id].source_category != SourceCategory.LLM_AUGMENTED:
            orphans.append(
                f"{dim_id} (catalog says "
                f"{DIMENSIONS[dim_id].source_category.value}, not "
                "llm_augmented)"
            )
    assert not orphans, (
        "Bundle dim members without matching LLM_AUGMENTED catalog "
        "entries:\n  " + "\n  ".join(orphans)
    )


# ── Invariant 3: bundle tuples align with schema fields ───────────


def test_phase_b_bundle_dims_match_schema():
    """BUNDLE_DIMS (Phase B) must equal QualitativeExtractionBundle's
    top-level fields. Drift means a dim is registered in one but not
    the other — the fan-out misses it and the persistence path skips
    it silently."""
    schema_fields = set(QualitativeExtractionBundle.model_fields.keys())
    bundle_dims = set(BUNDLE_DIMS)
    assert bundle_dims == schema_fields, (
        f"BUNDLE_DIMS {bundle_dims} != QualitativeExtractionBundle "
        f"fields {schema_fields}.\n"
        f"  In BUNDLE_DIMS but not schema: {bundle_dims - schema_fields}\n"
        f"  In schema but not BUNDLE_DIMS: {schema_fields - bundle_dims}"
    )


def test_phase_c_def14a_bundle_dims_match_schema():
    """DEF14A_BUNDLE_DIMS (Phase C) must equal Def14aExtractionBundle's
    top-level fields."""
    schema_fields = set(Def14aExtractionBundle.model_fields.keys())
    bundle_dims = set(DEF14A_BUNDLE_DIMS)
    assert bundle_dims == schema_fields, (
        f"DEF14A_BUNDLE_DIMS {bundle_dims} != "
        f"Def14aExtractionBundle fields {schema_fields}.\n"
        f"  In DEF14A_BUNDLE_DIMS but not schema: "
        f"{bundle_dims - schema_fields}\n"
        f"  In schema but not DEF14A_BUNDLE_DIMS: "
        f"{schema_fields - bundle_dims}"
    )


# ── Invariant 4: no PENDING_DATA dims (Phase D promise) ────────────


def test_zero_pending_data_dims():
    """Phase D wired the last PENDING_DATA dim (industry_concentration).
    Re-introducing one is allowed via PR but must be paired with a
    plan to wire it — this test fires loudly so the regression is
    caught at the catalog-edit level, not in production via missing
    dashboard coverage."""
    pending = [
        d for d, e in DIMENSIONS.items()
        if e.source_category == SourceCategory.PENDING_DATA
    ]
    assert not pending, (
        "Catalog has PENDING_DATA dims again — Phase D eliminated "
        "them all. Either wire the dim (DETERMINISTIC/HITL/"
        "LLM_AUGMENTED) or document the reintroduction in a PR "
        "comment and remove this test:\n  " + "\n  ".join(pending)
    )


# ── Invariant 5: no duplicate dim_ids + bundle disjointness ────────


def test_bundle_dims_disjoint():
    """A dim_id can be in at most one bundle. Cross-bundle
    membership would route the same dim through both the 10-K and
    DEF 14A LLM calls — wasted Gemini dollars + ambiguous
    provenance."""
    overlap = set(BUNDLE_DIMS) & set(DEF14A_BUNDLE_DIMS)
    assert not overlap, (
        f"Dims appear in BOTH BUNDLE_DIMS and DEF14A_BUNDLE_DIMS: "
        f"{overlap}. Each dim belongs to exactly one bundle "
        "(different source filings ⇒ different LLM calls)."
    )


def test_bundle_dims_disjoint_from_per_dim_registry():
    """A dim_id should not be in BOTH a bundle and the per-dim
    EXTRACTORS registry — that would persist two rows per Stage 4
    run, racing for the latest assessment."""
    bundle_all = set(BUNDLE_DIMS) | set(DEF14A_BUNDLE_DIMS)
    overlap = bundle_all & set(EXTRACTORS)
    assert not overlap, (
        f"Dims registered both in a bundle AND in EXTRACTORS: "
        f"{overlap}. Pick one path — bundles for shared-filing "
        "consolidation; EXTRACTORS for per-dim single-call extractors."
    )


def test_computers_disjoint_from_extractors():
    """A dim can't be both DETERMINISTIC (computer) AND LLM_AUGMENTED
    (extractor). The catalog enforces this via source_category, but
    we cross-check the registries too as a belt-and-braces guard."""
    bundle_all = set(BUNDLE_DIMS) | set(DEF14A_BUNDLE_DIMS) | set(EXTRACTORS)
    overlap = set(COMPUTERS) & bundle_all
    assert not overlap, (
        f"Dims registered in BOTH COMPUTERS and an extractor/bundle: "
        f"{overlap}. Each dim has exactly one source path."
    )


# ── Coverage stat — informational ──────────────────────────────────


def test_full_catalog_coverage_summary():
    """Smoke check that the wiring math adds up. Not a hard
    invariant — just a regression net so coverage changes are
    visible in CI output. Compares against the recorded Phase-D
    coverage state (16 producers across 19 dims; 9 HITL waiting on
    analyst input)."""
    n_total = len(DIMENSIONS)
    by_src = {sc: 0 for sc in SourceCategory}
    for e in DIMENSIONS.values():
        by_src[e.source_category] += 1

    # Pin the post-Phase-D coverage as a regression net. If you
    # legitimately change the catalog, update this assertion in the
    # same PR — that forces the test to flag catalog-size changes
    # for review.
    assert n_total == 19, (
        f"Catalog has {n_total} dims; expected 19. If you've added "
        "or removed a dim, update this assertion in the same PR."
    )
    # Phase D delivered: 5 DETERMINISTIC, 5 LLM_AUGMENTED, 9 HITL, 0 PENDING
    assert by_src[SourceCategory.DETERMINISTIC] == 5
    assert by_src[SourceCategory.LLM_AUGMENTED] == 5
    assert by_src[SourceCategory.HITL] == 9
    assert by_src[SourceCategory.PENDING_DATA] == 0
