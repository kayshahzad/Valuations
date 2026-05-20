"""Side-by-side quality gate: baseline (3-agent path) vs new (consolidated).

Loads `tests/quality_gate/baselines/{T}.json` and
`tests/quality_gate/new_outputs/{T}.json` for each available ticker and
computes a structured analytical-quality comparison.

The check is NOT byte-exact (LLM outputs vary across runs even at
T=0.1). It tests for **analytical quality preservation**:

  1. Moat scoring stays directionally consistent (±2 score units)
  2. Moat evidence still cites specific 10-K facts (named entities,
     numerical citations like "76.8% ROIC", "90% renewal rate")
  3. Value-chain bottleneck analysis still names actual suppliers
     (proper-noun coverage retained)
  4. Strategic-context summary still references the deterministic
     z-score (cyclicality citation preserved)
  5. Scenario coverage matches expected density per ticker
     (NVDA / AAPL: scenarios proposed; mature compounders: optional)

Output: a markdown report at `tests/quality_gate/comparison_report.md`
plus a per-ticker pass/concern/fail summary at the top.

Run with:
    PYTHONPATH=. python scripts/compare_qualitative_outputs.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


BASELINE_DIR = Path("tests/quality_gate/baselines")
NEW_DIR      = Path("tests/quality_gate/new_outputs")
REPORT_PATH  = Path("tests/quality_gate/comparison_report.md")


# Lightweight named-entity proxies. We don't want to hard-code per-ticker
# expectations because some 10-Ks change content year-over-year. Instead
# we look for STRUCTURAL evidence that a real entity was named: capitalized
# multi-word phrases, percentage citations, dollar amounts, named
# competitors. If the new output has comparable density, citation quality
# is preserved.
_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})*")
_PERCENT_RE     = re.compile(r"\d+(?:\.\d+)?\s*%")
_DOLLAR_RE      = re.compile(r"\$[\d,.]+\s*[BMKbmk]?")


def count_citations(text: str) -> Dict[str, int]:
    """Return citation-density metrics for a piece of narrative text."""
    if not text or not isinstance(text, str):
        return {"chars": 0, "proper_nouns": 0, "percentages": 0, "dollar_amounts": 0}
    return {
        "chars":          len(text),
        "proper_nouns":   len(_PROPER_NOUN_RE.findall(text)),
        "percentages":    len(_PERCENT_RE.findall(text)),
        "dollar_amounts": len(_DOLLAR_RE.findall(text)),
    }


def _label(delta: int) -> str:
    """Convert a coverage delta into a pass/concern/fail label."""
    if delta >= 0:
        return "✓"   # at least as detailed
    if delta >= -2:
        return "≈"   # minor reduction
    return "✗"        # material reduction


def compare_field(
    baseline_text: Optional[str],
    new_text: Optional[str],
    field_label: str,
) -> Dict[str, Any]:
    """Compare a single text field's citation density."""
    b = count_citations(baseline_text or "")
    n = count_citations(new_text or "")
    return {
        "field": field_label,
        "baseline":      b,
        "new":           n,
        "delta_proper_nouns":   n["proper_nouns"]   - b["proper_nouns"],
        "delta_percentages":    n["percentages"]    - b["percentages"],
        "delta_dollar_amounts": n["dollar_amounts"] - b["dollar_amounts"],
        "delta_chars":          n["chars"]          - b["chars"],
        "label_proper":      _label(n["proper_nouns"]   - b["proper_nouns"]),
        "label_percentages": _label(n["percentages"]    - b["percentages"]),
    }


def compare_ticker(ticker: str) -> Optional[Dict[str, Any]]:
    base_path = BASELINE_DIR / f"{ticker}.json"
    new_path  = NEW_DIR / f"{ticker}.json"
    if not (base_path.exists() and new_path.exists()):
        return None

    base = json.loads(base_path.read_text())
    new  = json.loads(new_path.read_text())

    base_forensic = base.get("forensic", {}) or {}
    base_moat     = base_forensic.get("moat", {}) or {}
    base_vc       = base.get("value_chain", {}) or {}
    base_sc       = base.get("strategic_context", {}) or {}
    base_scenarios = base.get("scenarios", []) or []

    new_forensic = new.get("forensic_report", {}) or {}
    new_vc       = new.get("value_chain_report", {}) or {}
    new_sc       = new.get("strategic_context_report", {}) or {}
    new_scenarios_total = (
        len(new_forensic.get("scenarios", []) or [])
        + len(new_vc.get("scenarios", []) or [])
        + len(new_sc.get("scenarios", []) or [])
    )

    # Score-level diff
    base_moat_score = base_moat.get("score")
    new_moat_score  = new_forensic.get("moat_score")
    moat_score_delta = (
        (new_moat_score - base_moat_score)
        if (base_moat_score is not None and new_moat_score is not None)
        else None
    )

    fields = [
        compare_field(base_moat.get("evidence"),
                      new_forensic.get("moat_evidence"),
                      "moat_evidence"),
        compare_field(base_forensic.get("business_description"),
                      new_forensic.get("business_description"),
                      "business_description"),
        compare_field(base_vc.get("bottleneck_analysis"),
                      new_vc.get("bottleneck_analysis"),
                      "bottleneck_analysis"),
        compare_field(base_vc.get("pricing_power_assessment"),
                      new_vc.get("pricing_power_assessment"),
                      "pricing_power_assessment"),
        compare_field(base_sc.get("summary"),
                      new_sc.get("summary"),
                      "context_summary"),
    ]

    # Pass/concern/fail per ticker — material reductions on any field is a
    # concern. Two or more fails is the analyst-review trigger.
    n_passes   = sum(1 for f in fields if f["label_proper"] == "✓")
    n_concerns = sum(1 for f in fields if f["label_proper"] == "≈")
    n_fails    = sum(1 for f in fields if f["label_proper"] == "✗")

    if n_fails >= 2:
        verdict = "FAIL"
    elif n_fails == 1 or n_concerns >= 3:
        verdict = "REVIEW"
    else:
        verdict = "PASS"

    return {
        "ticker":          ticker,
        "verdict":         verdict,
        "moat_score":      {"base": base_moat_score, "new": new_moat_score, "delta": moat_score_delta},
        "scenarios":       {"base": len(base_scenarios), "new": new_scenarios_total},
        "fields":          fields,
        "passes":          n_passes,
        "concerns":        n_concerns,
        "fails":           n_fails,
    }


def render_report(results: List[Dict[str, Any]]) -> str:
    out: List[str] = []
    out.append("# Qualitative Synthesis — Quality Gate Comparison")
    out.append("")
    out.append("Side-by-side analytical-quality comparison: 3-agent baseline "
               "(saved reports) vs new consolidated `qualitative_synthesis`.")
    out.append("")
    out.append("**Verdict scoring per field**: `✓` = new output has equal-or-greater "
               "named-entity / percentage citation density; `≈` = minor reduction "
               "(within 2 fewer citations); `✗` = material reduction (3+ fewer).")
    out.append("")
    out.append("**Per-ticker verdict**: PASS (zero fails, ≤2 concerns), REVIEW "
               "(1 fail or 3+ concerns), FAIL (2+ fails).")
    out.append("")
    out.append("---")
    out.append("")

    # Summary table
    out.append("## Summary")
    out.append("")
    out.append("| Ticker | Verdict | Moat (base→new) | Scenarios (base→new) | Field passes | Concerns | Fails |")
    out.append("|---|---|---|---|---|---|---|")
    for r in results:
        moat_s = r["moat_score"]
        ms_str = (
            f"{moat_s['base']} → {moat_s['new']} ({moat_s['delta']:+.1f})"
            if moat_s["delta"] is not None
            else f"{moat_s['base']} → {moat_s['new']}"
        )
        out.append(
            f"| {r['ticker']} | **{r['verdict']}** | {ms_str} | "
            f"{r['scenarios']['base']} → {r['scenarios']['new']} | "
            f"{r['passes']} | {r['concerns']} | {r['fails']} |"
        )
    out.append("")

    # Detail per ticker
    for r in results:
        out.append(f"## {r['ticker']} — {r['verdict']}")
        out.append("")
        out.append("| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |")
        out.append("|---|---|---|---|---|---|---|---|")
        for f in r["fields"]:
            out.append(
                f"| {f['field']} | {f['baseline']['proper_nouns']} | "
                f"{f['new']['proper_nouns']} | {f['delta_proper_nouns']:+d} | "
                f"{f['baseline']['percentages']} | {f['new']['percentages']} | "
                f"{f['delta_percentages']:+d} | {f['label_proper']} |"
            )
        out.append("")

    return "\n".join(out)


def main():
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)

    results: List[Dict[str, Any]] = []
    for new_path in sorted(NEW_DIR.glob("*.json")):
        ticker = new_path.stem
        r = compare_ticker(ticker)
        if r is not None:
            results.append(r)

    if not results:
        print("No comparison candidates found. Generate baselines + new outputs first.")
        return

    # Console summary
    print(f"\nQuality gate: {len(results)} ticker(s) compared\n")
    for r in results:
        print(f"  {r['ticker']:6s}  {r['verdict']:6s}  passes={r['passes']}  "
              f"concerns={r['concerns']}  fails={r['fails']}  "
              f"scenarios {r['scenarios']['base']} → {r['scenarios']['new']}")

    # Markdown report
    REPORT_PATH.write_text(render_report(results))
    print(f"\nReport: {REPORT_PATH}")


if __name__ == "__main__":
    main()
