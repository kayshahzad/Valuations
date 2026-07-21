"""10-K Part I executive-officers section extraction (librarian Build C).

Pure/offline — no network. Pins the heading-based slice that feeds the
management-roster extractor with the full C-suite (the DEF 14A alone
under-covers executives).
"""
from __future__ import annotations

from aletheia.agents.librarian import _extract_exec_officers_from_markdown as X


_MD = """## Item 1. Business
We make things. Blah blah.

## Item 1A. Risk Factors
Risks here.

## Information about our Executive Officers
The following are our executive officers as of the filing date:

Timothy D. Cook, 65 — Chief Executive Officer since 2011.
Kevan Parekh, 52 — Senior Vice President, Chief Financial Officer.
Sabih Khan, 57 — Chief Operating Officer.

## Item 2. Properties
Our HQ is in Cupertino.
"""


def test_extracts_between_heading_and_next_item():
    out = X(_MD)
    assert "Executive Officers" in out
    assert "Timothy D. Cook" in out
    assert "Kevan Parekh" in out
    assert "Sabih Khan" in out
    # stops before the next Item
    assert "Cupertino" not in out
    assert "Risk Factors" not in out


def test_returns_empty_when_no_heading():
    md = "## Item 1. Business\nStuff.\n## Item 2. Properties\nMore."
    assert X(md) == ""


def test_empty_input():
    assert X("") == ""
    assert X(None) == ""


def test_alternate_heading_form():
    md = ("## Item 4. Mine Safety\nn/a\n\n"
          "EXECUTIVE OFFICERS OF THE REGISTRANT\n"
          "Jane Roe, CFO.\n\nPART II\nOther stuff.")
    out = X(md)
    assert "Jane Roe" in out
    assert "Other stuff" not in out   # stops at PART II


def test_budget_cap():
    body = "Executive Officers of the Registrant\n" + ("x" * 50000)
    out = X(body, budget=5000)
    assert len(out) <= 5000
