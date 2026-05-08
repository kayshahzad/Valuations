"""dashboard_fetch_node — qualitative-dashboard state into LangGraph state.

Runs between contrarian and thesis_synthesizer. Reads the latest
assessment per dimension for the ticker from DuckDB, projects it through
`aletheia.qualitative.state_projection` into the structured shape
thesis_synthesizer consumes, and writes it to `state["qualitative_dashboard"]`.

Why a separate node (not inline in thesis_synthesizer):
  thesis_synthesizer is AST-locked against importing
  `aletheia.data.database`. The lock encodes "synthesizer is integration,
  not analysis" — adding direct DB access there would erode the discipline.
  Instead this node does the fetch and synthesizer reads from state.

Failure mode: if the DB read raises, the node logs and writes a minimal
zero-coverage projection so downstream agents don't crash. The dashboard
service being unavailable shouldn't block the pipeline — the thesis just
falls back to no-dashboard-coverage behavior (locked spec).
"""

from __future__ import annotations

import traceback
from typing import Any, Dict


def dashboard_fetch_node(state: Dict[str, Any]) -> Dict[str, Any]:
    print("---DASHBOARD FETCH NODE---")

    from config.qualitative_dimensions import (
        DIMENSIONS,
        CATEGORIES,
        category_composite_weights,
    )
    from aletheia.qualitative.state_projection import (
        dashboard_state_fingerprint,
        project_dimensions,
        project_categories,
        coverage_summary,
    )

    ticker = (state.get("ticker") or "").upper()
    if not ticker:
        return {"qualitative_dashboard": _empty_projection()}

    try:
        from aletheia.data.database import InvestmentDatabase
        from aletheia.qualitative.runner import _GIT_SHA as RUNTIME_GIT_SHA

        db = InvestmentDatabase(verbose=False)
        try:
            records = db.get_all_assessments_for_ticker(ticker)
        finally:
            db.close()

        dim_projection = project_dimensions(DIMENSIONS, records, RUNTIME_GIT_SHA)
        cat_projection = project_categories(
            DIMENSIONS, CATEGORIES, category_composite_weights, dim_projection
        )
        coverage = coverage_summary(dim_projection)
        fingerprint = dashboard_state_fingerprint(dim_projection)

        citable_composite_paths = [
            f"qualitative.{cat_id}_composite"
            for cat_id, c in cat_projection.items()
            if c["composite_score"] is not None
        ]

        n = coverage["n_assessed"]
        total = coverage["n_assessable"]
        print(f"  ✓ Dashboard projection: {n}/{total} assessed "
              f"(coverage={coverage['coverage_state']}, stale={coverage['n_stale']}, "
              f"fp={fingerprint})")

        return {
            "qualitative_dashboard": {
                "ticker":               ticker,
                "dimensions":           dim_projection,
                "categories":           cat_projection,
                "coverage":             coverage,
                "citable_dim_paths":    coverage["citable_dim_paths"],
                "citable_composite_paths": citable_composite_paths,
                "stale_paths":          coverage["stale_paths"],
                "state_fingerprint":    fingerprint,
                "available":            True,
            }
        }
    except Exception as exc:
        print(f"  ⚠ Dashboard fetch failed for {ticker}: {exc}")
        traceback.print_exc()
        return {"qualitative_dashboard": _empty_projection(ticker=ticker, error=str(exc))}


def _empty_projection(ticker: str = "", error: str = "") -> Dict[str, Any]:
    return {
        "ticker":     ticker,
        "dimensions": {},
        "categories": {},
        "coverage": {
            "n_assessable":      0,
            "n_assessed":        0,
            "n_stale":           0,
            "n_not_assessed":    0,
            "n_pending":         0,
            "coverage_state":    "zero",
            "stale_paths":       [],
            "citable_dim_paths": [],
        },
        "citable_dim_paths":       [],
        "citable_composite_paths": [],
        "stale_paths":             [],
        "state_fingerprint":       "",
        "available":               False,
        "error":                   error,
    }


__all__ = ["dashboard_fetch_node"]
