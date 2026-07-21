"""Bottom-up extraction evidence-grounding: schema, verifier, backward-compat.

Offline only — no LLM. Pins the 'required evidence' contract: on a
grounded extraction every filing-specific factual claim must be tied to a
verbatim 10-K quote, and unsourced claims are surfaced. Legacy caches
(pre-evidence) are never retroactively flagged.
"""
from __future__ import annotations

from aletheia.agents.business_extraction import (
    BusinessAB, BusinessEvidence, claim_is_sourced, evidence_claim_keys,
    is_grounded_extraction, verify_business_evidence, _EVIDENCE_SCHEMA_VERSION,
)


def _grounded(**over):
    base = {
        "evidence_schema_version": _EVIDENCE_SCHEMA_VERSION,
        "product_lines": [{"name": "Fusion Middleware"}, {"name": "Autonomous DB"}],
        "major_customers": [],
        "segment_economics": [],
        "customer_concentration": "no single customer >10% of revenue",
        "evidence_quotes": [
            {"claim": "product_lines:fusion middleware", "quote": "Fusion Middleware is...", "source": "Item 1"},
            {"claim": "customer_concentration", "quote": "No single customer accounted for 10%...", "source": "Item 1"},
        ],
    }
    base.update(over)
    return base


def test_schema_accepts_evidence_quotes():
    m = BusinessAB(evidence_quotes=[BusinessEvidence(claim="product_lines:X", quote="q", source="Item 1")])
    assert m.evidence_quotes[0].claim == "product_lines:X"


def test_long_quote_truncated_not_rejected():
    m = BusinessEvidence(claim="unit_cost", quote="Z" * 500)
    assert len(m.quote) == 300 and m.quote.endswith("…")


def test_verifier_flags_unsourced_item():
    cov = verify_business_evidence(_grounded())
    assert cov["grounded"] is True
    assert "product_lines:Fusion Middleware" in cov["sourced"]
    assert "customer_concentration" in cov["sourced"]
    # Autonomous DB has no supporting quote -> flagged
    assert "product_lines:Autonomous DB" in cov["unsourced"]


def test_field_level_quote_covers_list_items():
    # A bare 'product_lines' quote covers all its items (lenient).
    data = _grounded(evidence_quotes=[{"claim": "product_lines", "quote": "...", "source": "Item 1"},
                                      {"claim": "customer_concentration", "quote": "...", "source": "Item 1"}])
    cov = verify_business_evidence(data)
    assert cov["unsourced"] == []


def test_blank_scalar_not_required():
    data = _grounded(customer_concentration="", evidence_quotes=[
        {"claim": "product_lines:fusion middleware", "quote": "...", "source": "Item 1"},
        {"claim": "product_lines:autonomous db", "quote": "...", "source": "Item 1"},
    ])
    cov = verify_business_evidence(data)
    assert "customer_concentration" not in cov["unsourced"]  # blank asserts nothing
    assert cov["unsourced"] == []


def test_legacy_extraction_not_flagged():
    # No evidence_schema_version -> legacy -> nothing checked/flagged.
    legacy = {"product_lines": [{"name": "X"}], "customer_concentration": "concentrated"}
    assert is_grounded_extraction(legacy) is False
    cov = verify_business_evidence(legacy)
    assert cov == {"grounded": False, "sourced": [], "unsourced": []}


def test_claim_is_sourced_normalizes_case():
    keys = evidence_claim_keys({"evidence_quotes": [{"claim": "Product_Lines:AWS"}]})
    assert claim_is_sourced(keys, "product_lines", "aws") is True
    assert claim_is_sourced(keys, "product_lines", "azure") is False
