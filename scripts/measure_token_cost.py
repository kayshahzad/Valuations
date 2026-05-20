"""Token-cost measurement: new (3-LLM) vs baseline (5-LLM) pipeline.

The week-1.5 + week-5 consolidation reduced LLM-using agents from 5 to 3:
  - Old: forensic + value_chain + context + contrarian + lead (5 calls)
  - New: qualitative_synthesis + contrarian + thesis_synthesizer (3 calls)
    (Lead's LLM call removed — narrative now assembled deterministically
    from thesis_synthesizer's structured output.)

This script measures input character counts across all LLM calls in the
new pipeline and reports the empirical reduction. Token estimation uses
the standard ~4 chars/token approximation; precise token counts vary
by tokenizer but the ratio is what matters for the reduction claim.

The locked plan's verification target is 60-70% input-token reduction.

Run with:
    PYTHONPATH=. python scripts/measure_token_cost.py --tickers AAPL NVDA COST
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()


# ── Old pipeline characteristics (measured pre-consolidation) ────────────
# These numbers come from inspecting the original forensic.py /
# value_chain.py / context.py / contrarian_v2.py / lead.py prompt
# templates + raw_10k truncation lengths. Documented in the agent layer
# investigation (docs/agent_layer_investigation.md).
OLD_PIPELINE_PER_TICKER = {
    "forensic": {
        "prompt_template": 3299,
        "raw_10k": 60000,
        "db_context": 3000,
    },
    "value_chain": {
        "prompt_template": 2973,
        "raw_10k": 50000,
        "db_context": 2000,
    },
    "context": {
        "prompt_template": 3165,
        "raw_10k": 50000,
        "db_context": 3000,
    },
    "contrarian_v1": {
        "prompt_template": 1624,
        "web_results": 735,
        "scenario_summary_estimate": 2000,
        "quant_challenge_estimate": 1500,
    },
    "lead_v1": {
        "prompt_template": 815,
        "agent_summaries_estimate": 4000,
    },
}


def measure_new_pipeline(ticker: str) -> dict:
    """Run the new pipeline on `ticker` and capture the per-LLM-call
    input character counts."""
    # Import inside the function so the module loads cheaply when we
    # only want to print the baseline numbers.
    from aletheia.agents.librarian import librarian_agent
    from aletheia.agents.calc_node import calc_node
    from aletheia.agents.qualitative_synthesis import qualitative_synthesis_agent, _PROMPT_BODY as QS_BODY
    from aletheia.agents.scenario_eval_node import scenario_eval_node
    from aletheia.agents.strategist import strategist_agent
    from aletheia.agents.contrarian_v2 import contrarian_agent
    from aletheia.agents.thesis_synthesizer import (
        thesis_synthesizer_agent,
        _PROMPT_BODY as TS_BODY,
        _summarize_phase2,
        _summarize_qualitative,
        _summarize_contrarian,
        _summarize_scenarios,
        _summarize_conviction,
    )

    state = {"ticker": ticker, "messages": []}
    state.update(librarian_agent(state))
    state.update(calc_node(state))

    # Capture qualitative_synthesis input size BEFORE calling
    raw_10k = state.get("raw_10k_text", "") or ""
    ten_k_truncated = raw_10k[:60000]

    qs_prompt_chars = (
        len(QS_BODY)
        + len(ten_k_truncated)
        + 3000          # estimated DB context (forensic + value_chain merged)
    )

    state.update(qualitative_synthesis_agent(state))
    state.update(scenario_eval_node(state))
    state.update(strategist_agent(state))

    # Contrarian input — no web search
    contrarian_prompt_chars = (
        len(_summarize_phase2(state))
        + len(_summarize_qualitative(state))
        + 1500          # contrarian prompt template
    )
    state.update(contrarian_agent(state))

    # Thesis synthesizer input — projector summaries
    ts_prompt_chars = (
        len(TS_BODY)
        + len(_summarize_phase2(state))
        + len(_summarize_qualitative(state))
        + len(_summarize_contrarian(state))
        + len(_summarize_scenarios(state))
        + len(_summarize_conviction(state))
    )
    state.update(thesis_synthesizer_agent(state))

    return {
        "ticker": ticker,
        "qualitative_synthesis_chars": qs_prompt_chars,
        "contrarian_chars":            contrarian_prompt_chars,
        "thesis_synthesizer_chars":    ts_prompt_chars,
        "total_chars":                 qs_prompt_chars + contrarian_prompt_chars + ts_prompt_chars,
    }


def baseline_chars() -> int:
    """Total input chars in the old 5-LLM-call pipeline."""
    f = OLD_PIPELINE_PER_TICKER["forensic"]
    v = OLD_PIPELINE_PER_TICKER["value_chain"]
    c = OLD_PIPELINE_PER_TICKER["context"]
    co = OLD_PIPELINE_PER_TICKER["contrarian_v1"]
    le = OLD_PIPELINE_PER_TICKER["lead_v1"]
    return (
        f["prompt_template"] + f["raw_10k"] + f["db_context"]
        + v["prompt_template"] + v["raw_10k"] + v["db_context"]
        + c["prompt_template"] + c["raw_10k"] + c["db_context"]
        + co["prompt_template"] + co["web_results"]
        + co["scenario_summary_estimate"] + co["quant_challenge_estimate"]
        + le["prompt_template"] + le["agent_summaries_estimate"]
    )


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", required=True)
    args = p.parse_args()

    base = baseline_chars()
    print(f"Old pipeline (5 LLM calls per ticker):")
    for agent, parts in OLD_PIPELINE_PER_TICKER.items():
        agent_chars = sum(parts.values())
        print(f"  {agent:18s}  {agent_chars:>7d} chars")
    print(f"  {'TOTAL':18s}  {base:>7d} chars  (~{base // 4:>5d} tokens estimate)")
    print()

    results = []
    print(f"New pipeline (3 LLM calls per ticker):")
    for ticker in args.tickers:
        try:
            r = measure_new_pipeline(ticker)
            results.append(r)
        except Exception as e:
            print(f"  ✗ {ticker} failed: {e}")

    print()
    print(f"{'Ticker':8s} {'qual_synth':>11s} {'contrarian':>11s} {'thesis_synth':>13s} {'TOTAL':>10s}")
    print("-" * 60)
    for r in results:
        print(
            f"{r['ticker']:8s} "
            f"{r['qualitative_synthesis_chars']:>11d} "
            f"{r['contrarian_chars']:>11d} "
            f"{r['thesis_synthesizer_chars']:>13d} "
            f"{r['total_chars']:>10d}"
        )

    if results:
        avg_new = sum(r["total_chars"] for r in results) / len(results)
        reduction_pct = (base - avg_new) / base * 100
        print()
        print(f"Avg new pipeline:  {avg_new:>7.0f} chars  (~{avg_new / 4:>5.0f} tokens estimate)")
        print(f"Old baseline:      {base:>7d} chars  (~{base // 4:>5d} tokens estimate)")
        print(f"Reduction:         {reduction_pct:.1f}%")
        print()
        print(f"Locked target: 60-70% input-token reduction.")

    # Save report
    out_path = Path("tests/quality_gate/token_cost_report.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "old_baseline_chars": base,
        "old_baseline_tokens_estimate": base // 4,
        "new_per_ticker": results,
        "old_per_agent": OLD_PIPELINE_PER_TICKER,
    }, indent=2, default=str))
    print(f"Report saved: {out_path}")


if __name__ == "__main__":
    main()
