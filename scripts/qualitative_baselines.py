"""Capture baseline outputs for the qualitative-synthesis quality gate.

Reads `valuation_data/serving/latest/{TICKER}_report.json` for the 9 sample
tickers and projects out the agent-authored fields that qualitative_synthesis
must preserve or improve. Saves to `tests/quality_gate/baselines/`.

Three sources mapped per ticker:
  - forensic_report  → 1_economic_reality.{moat, business_model}
  - value_chain      → 1_economic_reality.value_chain
  - context          → 1_economic_reality.{industry_structure, strategic_context}
  - scenarios        → 4_valuation_synthesis.agent_scenarios

The baselines reflect output from the production 3-agent path. After
qualitative_synthesis ships, side-by-side comparison validates each
ticker's output against this snapshot:
  - Does moat evidence still cite specific 10-K facts?
  - Does value_chain.bottleneck_analysis still name actual suppliers?
  - Does strategic_context.summary still reference z-scores?
  - For NVDA / AAPL: are scenarios still proposed (and if so, do they
    cover similar themes)?

Run with:
    PYTHONPATH=. python scripts/qualitative_baselines.py
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


SAMPLE_TICKERS = [
    "AAPL", "NVDA", "JPM", "COST", "CNC",
    "MSFT", "TSLA", "BRK-B", "META",
]

REPORT_DIR    = Path("valuation_data/serving/latest")
BASELINE_DIR  = Path("tests/quality_gate/baselines")


def extract_qualitative_baseline(report: Dict[str, Any]) -> Dict[str, Any]:
    """Project the agent-authored fields from a saved report.

    Mirrors the output shape qualitative_synthesis must produce, so a
    field-by-field diff is meaningful.
    """
    er = report.get("1_economic_reality", {}) or {}
    val = report.get("4_valuation_synthesis", {}) or {}
    p2  = val.get("phase2_valuation", {}) or {}

    return {
        "ticker": report.get("ticker"),
        "generated_at": report.get("generated_at"),
        # Forensic projection
        "forensic": {
            "business_description":   (er.get("business_model") or {}).get("business_description"),
            "operating_leverage_score": (er.get("business_model") or {}).get("operating_leverage_score"),
            "operating_leverage_analysis": (er.get("business_model") or {}).get("cost_structure"),
            "revenue_segments":       (er.get("business_model") or {}).get("revenue_segments"),
            "key_customers":          (er.get("business_model") or {}).get("key_customers"),
            "competitive_landscape":  (er.get("business_model") or {}).get("competitive_landscape"),
            "regulatory_risk":        (er.get("business_model") or {}).get("regulatory_risk"),
            "moat": er.get("moat") or {},
        },
        # Value chain projection
        "value_chain": er.get("value_chain") or {},
        # Strategic context projection
        "industry_structure": er.get("industry_structure") or {},
        "strategic_context":  er.get("strategic_context") or {},
        # Scenarios from scenario_eval (post-DCF)
        "scenarios": [
            {
                "name":         s.get("name"),
                "scenario_type": s.get("scenario_type"),
                "proposed_by":  s.get("proposed_by"),
                "rationale":    s.get("rationale"),
                "overrides":    s.get("overrides_applied", {}),
                "ips_base":     s.get("intrinsic_per_share_base"),
                "upside_pct":   s.get("upside_pct_base"),
            }
            for s in (val.get("agent_scenarios") or [])
        ],
    }


def main() -> None:
    BASELINE_DIR.mkdir(parents=True, exist_ok=True)

    summary = {"captured": [], "missing": []}
    for ticker in SAMPLE_TICKERS:
        report_path = REPORT_DIR / f"{ticker}_report.json"
        if not report_path.exists():
            print(f"  ⊘ {ticker}: no saved report at {report_path}")
            summary["missing"].append(ticker)
            continue
        try:
            report = json.loads(report_path.read_text())
            baseline = extract_qualitative_baseline(report)
            out_path = BASELINE_DIR / f"{ticker}.json"
            out_path.write_text(json.dumps(baseline, indent=2, default=str))
            print(f"  ✓ {ticker}: captured ({len(baseline['scenarios'])} scenarios)")
            summary["captured"].append(ticker)
        except Exception as e:
            print(f"  ✗ {ticker}: failed — {e}")
            summary["missing"].append(ticker)

    summary_path = BASELINE_DIR / "_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2))
    print()
    print(f"Captured {len(summary['captured'])} of {len(SAMPLE_TICKERS)} baselines.")
    print(f"Output: {BASELINE_DIR}")


if __name__ == "__main__":
    main()
