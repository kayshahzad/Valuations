"""/ticker/{T}/dcf returns 200 for pending tickers (no agent run).

ACN was added to the universe but never had its LangGraph agent run; before
the JSON-as-truth → DB-as-truth refactor the endpoint returned 404 because
`_load_report` couldn't find the JSON. After the refactor every fcff-compatible
ticker with cleaned DB rows must return a populated DCFResponse — that's the
core architectural guarantee.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from api_main import app
    return TestClient(app)


@pytest.mark.skipif(
    not __import__("pathlib").Path("valuation_data/database/investment.duckdb").exists(),
    reason="DuckDB not present",
)
class TestDCFEndpointPending:

    def test_acn_returns_200_with_populated_scenarios(self, client):
        """ACN is fcff_compatible mature with 16 years of cleaned data and
        no agent run. The endpoint must produce three scenarios from the DB."""
        r = client.get("/ticker/ACN/dcf")
        assert r.status_code == 200, f"expected 200, got {r.status_code}: {r.text}"
        data = r.json()
        assert data["ticker"] == "ACN"
        assert data["wacc"] is not None
        for s in ("bear", "base", "bull"):
            assert data[s] is not None, f"{s} scenario missing"
            assert data[s]["intrinsic_per_share"] is not None, f"{s} IPS missing"
            assert data[s]["ev"] is not None, f"{s} EV missing"

    def test_pending_ticker_does_not_404(self, client):
        """Any ticker with cleaned data must not 404 on /dcf — that was the
        ACN render-empty bug. Pick a different recently-added ticker as
        independent verification."""
        for ticker in ("KO", "HD", "PEP", "JNJ"):
            r = client.get(f"/ticker/{ticker}/dcf")
            # 200 (engine succeeded) or 422 (engine refused — schema
            # mismatch) are both acceptable; 404 is the regression we're
            # blocking.
            assert r.status_code != 404, (
                f"{ticker}/dcf returned 404 — DB-as-truth fallback broke. "
                f"Body: {r.text}"
            )

    def test_ready_ticker_still_works(self, client):
        """AAPL has both cleaned DB rows AND a saved report.json. The new
        path must produce a result regardless of the JSON's presence."""
        r = client.get("/ticker/AAPL/dcf")
        assert r.status_code == 200
        data = r.json()
        assert data["base"]["intrinsic_per_share"] is not None
        # Source header confirms we computed live, not read JSON
        assert r.headers.get("x-aletheia-source") == "db_compute"

    def test_schema_mismatch_returns_422_not_404(self, client):
        """AXP — card network — fails DCFEngine with MissingFieldError on
        OperatingIncome. The endpoint must surface this as 422 (engine
        refused) rather than 404 (no data), so the client can distinguish
        'never ingested' from 'wrong framework'."""
        r = client.get("/ticker/AXP/dcf")
        # Either 200 (if DCFEngine has been taught to handle AXP) or 422.
        # 404 specifically is the bug.
        assert r.status_code in (200, 422), f"got {r.status_code}: {r.text}"
        if r.status_code == 422:
            assert "non-FCFF" in r.json()["detail"] or "DCFEngine cannot" in r.json()["detail"]
