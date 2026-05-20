"""Architecture lock — valuation router + specialized-inputs coverage.

Enforced invariants:

  1. Every universe ticker with ``business_model in {routing_required,
     ddm_required, embedded_value_required}`` has an entry in
     ``config/specialized_valuation_inputs.json``. Catches the case
     where a new utility/bank/insurance ticker is added to the
     universe but the analyst-input config doesn't follow.

  2. Every entry in ``specialized_valuation_inputs.json`` corresponds
     to a real universe ticker. Catches stale entries when a ticker
     is removed from coverage.

  3. The router dispatch table has an entry (factory or None
     placeholder) for every ``business_model`` value used by any
     universe ticker. A new ``business_model`` enum value added
     without router-table wiring is a CI failure, not a runtime
     ``UnknownBusinessModelError`` for an analyst.

  4. Every routing_required ticker that the rate-base engine should
     handle has ``model == "rate_base"`` in the config. Mismatches
     surface here rather than as empty-state ValuationResults in
     production.

Same pattern as ``test_qualitative_wiring_lock.py``: pure import +
data inspection; no I/O, no DB, no LLM.
"""

from aletheia.calculations.specialized_inputs import (
    get_all_specialized_tickers, load_specialized_inputs,
)
from aletheia.tools.valuation_router import _DISPATCH_TABLE
from config.ticker_classification import get_extended_universe


# Business models that require specialized inputs (analyst-curated
# rate base, allowed ROE, dividend payout, etc.) rather than coming
# from the financial-data ingest.
_SPECIALIZED_MODELS = frozenset({
    "routing_required",
    "ddm_required",
    "embedded_value_required",
})


def test_every_specialized_universe_ticker_has_config_entry():
    """Universe ticker with specialized business_model → must have
    a config entry. Placeholders OK (Phase A.2 stubbed JPM/UNH/etc.);
    full inputs not required until the engine ships."""
    universe = get_extended_universe()
    specialized_tickers = {
        t for t, meta in universe.items()
        if meta.business_model in _SPECIALIZED_MODELS
    }
    configured = get_all_specialized_tickers()
    missing = specialized_tickers - configured
    assert not missing, (
        "Universe tickers with specialized business_model lack "
        "specialized_valuation_inputs.json entries:\n  "
        + "\n  ".join(sorted(missing))
        + "\nAdd a config entry (placeholder OK) so the architecture-"
          "lock invariant holds."
    )


def test_no_orphan_config_entries():
    """Reverse direction — every config entry corresponds to a real
    universe ticker. Catches stale entries after a ticker delist."""
    universe = set(get_extended_universe().keys())
    configured = get_all_specialized_tickers()
    orphans = configured - universe
    assert not orphans, (
        "specialized_valuation_inputs.json has entries for tickers "
        "not in the universe (stale config):\n  "
        + "\n  ".join(sorted(orphans))
    )


def test_router_dispatch_covers_every_universe_business_model():
    """Every distinct business_model in the universe must appear as
    a key in ``_DISPATCH_TABLE`` (factory or None placeholder).
    Otherwise calc_node hits ``UnknownBusinessModelError`` in
    production — that's an architecture bug, not a per-ticker
    issue."""
    universe = get_extended_universe()
    used_models = {meta.business_model for meta in universe.values()}
    missing = used_models - set(_DISPATCH_TABLE.keys())
    assert not missing, (
        "Business models in use but not in router dispatch table:\n  "
        + "\n  ".join(sorted(missing))
        + "\nAdd them to _DISPATCH_TABLE in valuation_router.py (a "
          "None placeholder is acceptable for unimplemented engines)."
    )


def test_config_model_matches_classification():
    """The config ``model`` field must align with the universe
    classification's ``business_model`` value. Each model maps to
    exactly one business_model class:

      config.model="rate_base"      ⟷ classification="routing_required"
      config.model="ddm"            ⟷ classification="ddm_required"
      config.model="embedded_value" ⟷ classification="embedded_value_required"

    Catches classification drift where a ticker is moved between
    classes but the config entry's model stays the same (or vice
    versa). Post-Phase-A.7 every specialized ticker should have
    matching pairs."""
    expected_pairing = {
        "rate_base":      "routing_required",
        "ddm":            "ddm_required",
        "embedded_value": "embedded_value_required",
    }
    universe = get_extended_universe()
    mismatches = []
    for ticker in get_all_specialized_tickers():
        inputs = load_specialized_inputs(ticker)
        expected_classification = expected_pairing.get(inputs.model)
        if expected_classification is None:
            continue   # unknown model — caught by a separate test
        meta = universe.get(ticker)
        if meta is None:
            mismatches.append(f"{ticker}: orphan config entry")
            continue
        if meta.business_model != expected_classification:
            mismatches.append(
                f"{ticker}: config model={inputs.model!r} expects "
                f"business_model={expected_classification!r} but "
                f"classification says {meta.business_model!r}"
            )
    assert not mismatches, (
        "Config ↔ classification mismatches:\n  " + "\n  ".join(mismatches)
    )


def test_every_config_entry_has_source_citation():
    """Every entry needs a non-empty ``source`` field — analyst
    inputs without provenance are unaccountable. Placeholder
    entries (Phase A.2 stubs) can have 'TBD' as the source body
    but the field must exist; full entries cite the regulatory
    order / 10-K page / CCAR disclosure."""
    missing_source = []
    for ticker in get_all_specialized_tickers():
        inputs = load_specialized_inputs(ticker)
        if not inputs.source or not inputs.source.strip():
            missing_source.append(ticker)
    assert not missing_source, (
        "Config entries missing source citation:\n  "
        + "\n  ".join(sorted(missing_source))
    )


def test_routing_required_config_model_must_be_one_of_known():
    """Every config entry's ``model`` field must be a known
    string. Prevents typo-introduced engine bypasses."""
    known_models = {"rate_base", "ddm", "embedded_value"}
    bad_models = []
    for ticker in get_all_specialized_tickers():
        inputs = load_specialized_inputs(ticker)
        if inputs.model not in known_models:
            bad_models.append(f"{ticker}: model={inputs.model!r}")
    assert not bad_models, (
        "Config entries with unknown model field:\n  "
        + "\n  ".join(bad_models)
        + f"\nValid models: {sorted(known_models)}"
    )
