"""Contrarian web-search A/B test (Week 0.5 parallel-track item).

Runs the contrarian agent twice for each sample ticker — once with the
DuckDuckGo web-search query enabled, once with it disabled — holding all
upstream state (librarian, calc_node, qualitative_synthesis,
scenario_eval) constant. Saves both outputs to disk so the comparison
harness can compute deltas on bear-case length, specificity, bias
category, and sentiment score.

Decision rule (per the locked plan): if the with-search vs no-search
delta is < 10% across the meaningful axes, the DuckDuckGo dependency
should be dropped.

Run with:
    PYTHONPATH=. python scripts/contrarian_ab.py --tickers AAPL NVDA COST
    PYTHONPATH=. python scripts/contrarian_ab.py --all
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()


SAMPLE_TICKERS = [
    "AAPL", "NVDA", "JPM", "COST", "CNC",
    "MSFT", "TSLA", "BRK-B", "META",
]
OUT_DIR = Path("tests/quality_gate/contrarian_ab")


def _build_upstream_state(ticker: str) -> dict:
    """Run librarian → calc_node → qualitative_synthesis → scenario_eval
    for the ticker. Returns the accumulated state dict."""
    from aletheia.agents.librarian import librarian_agent
    from aletheia.agents.calc_node import calc_node
    from aletheia.agents.qualitative_synthesis import qualitative_synthesis_agent
    from aletheia.agents.scenario_eval_node import scenario_eval_node

    state = {"ticker": ticker, "messages": []}
    state.update(librarian_agent(state))
    state.update(calc_node(state))
    state.update(qualitative_synthesis_agent(state))
    state.update(scenario_eval_node(state))
    return state


def _run_contrarian(state: dict, disable_search: bool) -> dict:
    """Run contrarian against the given state. Caller should pass a
    deep copy of upstream state so the with/without runs don't mutate
    each other."""
    from aletheia.agents.contrarian_v2 import contrarian_agent

    state = copy.deepcopy(state)
    state["contrarian_disable_search"] = disable_search
    return contrarian_agent(state)


def run_one(ticker: str) -> dict:
    print(f"\n=== {ticker} ===")
    t0 = time.time()
    upstream = _build_upstream_state(ticker)
    upstream_s = time.time() - t0
    print(f"  upstream: {upstream_s:.1f}s")

    # With search
    t0 = time.time()
    out_with = _run_contrarian(upstream, disable_search=False)
    with_s = time.time() - t0
    cr_with = (out_with.get("contrarian_report") or {})
    sa_with = cr_with.get("structured_analysis", {}) or {}

    # Without search
    t0 = time.time()
    out_no = _run_contrarian(upstream, disable_search=True)
    no_s = time.time() - t0
    cr_no = (out_no.get("contrarian_report") or {})
    sa_no = cr_no.get("structured_analysis", {}) or {}

    print(f"  with-search:  {with_s:.1f}s  bias={sa_with.get('bias_detected', '?')[:30]}  "
          f"sentiment={sa_with.get('sentiment_score')}  "
          f"bear_chars={len(sa_with.get('bear_case_summary', ''))}")
    print(f"  no-search:    {no_s:.1f}s  bias={sa_no.get('bias_detected', '?')[:30]}  "
          f"sentiment={sa_no.get('sentiment_score')}  "
          f"bear_chars={len(sa_no.get('bear_case_summary', ''))}")

    return {
        "ticker": ticker,
        "with_search": {
            "bias_detected":     sa_with.get("bias_detected"),
            "bear_case_summary": sa_with.get("bear_case_summary"),
            "sentiment_score":   sa_with.get("sentiment_score"),
            "raw_web_results_chars": len(cr_with.get("raw_results", "") or ""),
            "elapsed_s":         with_s,
        },
        "no_search": {
            "bias_detected":     sa_no.get("bias_detected"),
            "bear_case_summary": sa_no.get("bear_case_summary"),
            "sentiment_score":   sa_no.get("sentiment_score"),
            "elapsed_s":         no_s,
        },
        "upstream_s": upstream_s,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+")
    p.add_argument("--all", action="store_true")
    args = p.parse_args()

    if args.all:
        tickers = SAMPLE_TICKERS
    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        p.print_help()
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    for ticker in tickers:
        try:
            r = run_one(ticker)
            (OUT_DIR / f"{ticker}.json").write_text(
                json.dumps(r, indent=2, default=str)
            )
        except Exception as e:
            print(f"  ✗ {ticker} failed: {e}")
            (OUT_DIR / f"{ticker}.error.txt").write_text(str(e))


if __name__ == "__main__":
    main()
