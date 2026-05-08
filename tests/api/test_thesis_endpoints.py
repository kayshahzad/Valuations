"""Memo retirement: POST returns 410, GETs remain read-only.

Per the dashboard-wiring change (D6), the free-text Thesis Builder is
retired. New writes are rejected with HTTP 410 Gone and a migration
message pointing to the dashboard. Existing GET endpoints continue
returning historical memo content for reference.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from api_main import app
    return TestClient(app)


# ── POST → 410 ───────────────────────────────────────────────────────────

def test_post_thesis_returns_410(client):
    """Any POST to /ticker/{T}/thesis must return 410 Gone."""
    payload = {
        "one_sentence": "Test thesis",
        "assumption_1": "a",
        "assumption_2": "b",
        "assumption_3": "c",
        "confirmation_12m": "x",
        "falsification": "y",
        "moat_powers": "z",
        "unit_economics": "w",
    }
    resp = client.post("/ticker/AAPL/thesis", json=payload)
    assert resp.status_code == 410
    detail = resp.json().get("detail", "")
    # Migration message must direct users to the dashboard
    assert "Qualitative Dashboard" in detail or "qualitative" in detail.lower()
    assert "thesis_synthesis" in detail or "thesis_synthesizer" in detail


def test_post_thesis_410_for_nonexistent_ticker(client):
    """Even tickers that don't exist still get the 410 (the endpoint is
    retired regardless of ticker validity)."""
    resp = client.post("/ticker/ZZZZZ/thesis", json={
        "one_sentence": "x", "assumption_1": "a", "assumption_2": "b",
        "assumption_3": "c", "confirmation_12m": "x", "falsification": "y",
        "moat_powers": "z", "unit_economics": "w",
    })
    assert resp.status_code == 410


# ── GET endpoints stay functional (read-only access to history) ─────────

def test_get_thesis_still_callable(client):
    """GET /ticker/{T}/thesis must still return (latest memo or {})."""
    resp = client.get("/ticker/AAPL/thesis")
    assert resp.status_code == 200
    # Body is either the latest memo dict or {} — both are valid post-retirement
    assert isinstance(resp.json(), dict)


def test_get_thesis_history_still_callable(client):
    """GET /ticker/{T}/thesis/history must still return a list."""
    resp = client.get("/ticker/AAPL/thesis/history")
    assert resp.status_code == 200
    body = resp.json()
    # Either historical entries or [] — both valid
    assert isinstance(body, list)


def test_get_thesis_pdf_returns_404_when_missing(client):
    """If no PDF file exists for the ticker, 404 — not 410.
    GETs are still functional; they're just empty for a never-built ticker."""
    resp = client.get("/ticker/ZZZZZ/thesis/pdf")
    # 404 (no thesis row OR no PDF file) — both acceptable
    assert resp.status_code == 404
