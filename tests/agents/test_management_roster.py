"""Management roster extraction: schema, evidence verifier, FMP name
cross-check, backward-compat. Offline only — no LLM, no network.

Pins the anti-hallucination contract: on a grounded extraction, a member
whose career history lacks a verbatim filing quote is flagged unsourced,
and an executive-typed name FMP doesn't know is flagged for review.
"""
from __future__ import annotations

from aletheia.agents.management_extraction import (
    ManagementRoster, RosterMember, crosscheck_roster_against_fmp,
    is_grounded_extraction, member_bio_is_sourced, verify_roster_evidence,
    _names_match,
)
from aletheia.agents.business_extraction import BusinessEvidence


def _roster():
    return {
        "evidence_schema_version": 1,
        "members": [
            {"name": "Jane A. Doe", "role": "CEO", "member_type": "both",
             "bio_summary": "Led operations since 2015.", "prior_roles": ["COO"]},
            {"name": "John Smith", "role": "CFO", "member_type": "executive",
             "bio_summary": "Prior CFO at ACME.", "prior_roles": []},
            {"name": "Ada Director", "role": "Director", "member_type": "director",
             "bio_summary": "On the board since 2019.", "prior_roles": []},
        ],
        "evidence_quotes": [
            {"claim": "roster:jane a. doe:bio", "quote": "Ms. Doe has led operations since 2015.", "source": "DEF 14A"},
            {"claim": "roster:ada director:bio", "quote": "Ms. Director has served since 2019.", "source": "DEF 14A"},
        ],
    }


def test_schema_accepts_members_and_evidence():
    m = ManagementRoster(
        members=[RosterMember(name="A B", role="CEO", member_type="executive")],
        evidence_quotes=[BusinessEvidence(claim="roster:a b:bio", quote="q", source="DEF 14A")],
    )
    assert m.members[0].name == "A B"


def test_verifier_flags_member_without_bio_quote():
    cov = verify_roster_evidence(_roster())
    assert cov["grounded"] is True
    assert "Jane A. Doe" in cov["sourced"]
    assert "Ada Director" in cov["sourced"]
    assert cov["unsourced"] == ["John Smith"]   # bio present, no quote


def test_member_with_no_history_not_required():
    data = {"evidence_schema_version": 1, "members": [
        {"name": "Roster Only", "role": "Director", "member_type": "director",
         "bio_summary": "", "prior_roles": []}], "evidence_quotes": []}
    cov = verify_roster_evidence(data)
    assert cov["unsourced"] == []   # nothing asserted about their history


def test_legacy_roster_not_flagged():
    legacy = {"members": [{"name": "X", "bio_summary": "invented history"}]}
    assert is_grounded_extraction(legacy) is False
    assert verify_roster_evidence(legacy) == {"grounded": False, "sourced": [], "unsourced": []}


def test_fmp_crosscheck_flags_unknown_exec_only():
    fmp = [{"name": "Jane Doe"}, {"name": "Someone Else"}]
    xc = crosscheck_roster_against_fmp(_roster(), fmp)
    assert xc["checked"] is True
    assert xc["unverified_execs"] == ["John Smith"]   # exec, unknown to FMP
    # Ada is a director — FMP doesn't cover the board, so never flagged


def test_fmp_crosscheck_degrades_without_data():
    xc = crosscheck_roster_against_fmp(_roster(), None)
    assert xc == {"checked": False, "unverified_execs": []}


def test_name_matching_variants():
    assert _names_match("Jane A. Doe", "Jane Doe") is True
    assert _names_match("Smith, John", "John Smith") is True
    assert _names_match("Robert Downey Jr.", "Robert Downey") is True
    assert _names_match("Jane Doe", "Bob Roberts") is False


def test_member_bio_sourced_prefix_and_field():
    keys = {"roster:jane a. doe:prior_roles"}
    assert member_bio_is_sourced(keys, "Jane A. Doe") is True   # any roster:{name}:* counts
    assert member_bio_is_sourced(keys, "Nobody") is False
