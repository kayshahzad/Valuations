"""Run qualitative_synthesis on a list of tickers and save outputs.

Used for the Week 1.5 quality gate. Saves to
`tests/quality_gate/new_outputs/{TICKER}.json` so the comparison
harness can diff against `tests/quality_gate/baselines/{TICKER}.json`.

Run with:
    PYTHONPATH=. python scripts/run_qualitative_synthesis.py --tickers AAPL NVDA COST
    PYTHONPATH=. python scripts/run_qualitative_synthesis.py --all
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

from aletheia.agents.qualitative_synthesis import qualitative_synthesis_agent
from aletheia.agents.librarian import librarian_agent


SAMPLE_TICKERS = [
    "AAPL", "NVDA", "JPM", "COST", "CNC",
    "MSFT", "TSLA", "BRK-B", "META",
]
OUT_DIR = Path("tests/quality_gate/new_outputs")


def run_one(ticker: str) -> dict:
    state = {"ticker": ticker, "messages": []}

    print(f"\n=== {ticker} ===")
    t0 = time.time()
    state.update(librarian_agent(state))
    librarian_s = time.time() - t0

    t0 = time.time()
    out = qualitative_synthesis_agent(state)
    synthesis_s = time.time() - t0

    print(f"  librarian: {librarian_s:.1f}s   synthesis: {synthesis_s:.1f}s")

    fr = out.get("forensic_report") or {}
    vc = out.get("value_chain_report") or {}
    sc = out.get("strategic_context_report") or {}

    n_scenarios = (
        len(fr.get("scenarios", []))
        + len(vc.get("scenarios", []))
        + len(sc.get("scenarios", []))
    )
    print(f"  moat={fr.get('moat_score')}  upstream_leak={vc.get('upstream_value_leak')}  "
          f"z={sc.get('revenue_z_score', 0):+.2f}  scenarios={n_scenarios}")

    return {
        "ticker": ticker,
        "librarian_s": librarian_s,
        "synthesis_s": synthesis_s,
        "n_scenarios": n_scenarios,
        "forensic_report": fr,
        "value_chain_report": vc,
        "strategic_context_report": sc,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--tickers", nargs="+", help="Tickers to run")
    p.add_argument("--all", action="store_true",
                   help=f"Run on all 9 sample tickers: {SAMPLE_TICKERS}")
    args = p.parse_args()

    if args.all:
        tickers = SAMPLE_TICKERS
    elif args.tickers:
        tickers = [t.upper() for t in args.tickers]
    else:
        p.print_help()
        return

    OUT_DIR.mkdir(parents=True, exist_ok=True)

    results = []
    for ticker in tickers:
        try:
            r = run_one(ticker)
            (OUT_DIR / f"{ticker}.json").write_text(
                json.dumps(r, indent=2, default=str)
            )
            results.append(r)
        except Exception as e:
            print(f"  ✗ {ticker} failed: {e}")
            results.append({"ticker": ticker, "error": str(e)})

    # Summary
    print()
    print("=" * 60)
    print(f"Summary: {len(results)} tickers")
    total_lib = sum(r.get("librarian_s", 0) for r in results)
    total_syn = sum(r.get("synthesis_s", 0) for r in results)
    print(f"  total librarian: {total_lib:.1f}s")
    print(f"  total synthesis: {total_syn:.1f}s")
    print(f"  avg synthesis per ticker: {total_syn/max(1,len(results)):.1f}s")
    print()
    print(f"Outputs: {OUT_DIR}")


if __name__ == "__main__":
    main()
