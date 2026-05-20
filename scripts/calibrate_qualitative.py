#!/usr/bin/env python3
"""Calibration runner for the qualitative deterministic computers.

Runs every computer against every ticker in the universe and prints a
markdown table for analyst review. Used during Step 2g to verify that
bucket cutoffs match analyst intuition before the framework goes into
use — once analysts start consuming scores, changing the cutoffs has a
higher cost (assessments captured under old cutoffs auto-flag stale).

Spot-check anchors (FY2025 data, code_version=1):
    AAPL    Quality compounder; expect ROIIC 6-7, buybacks 6-7, divs 5-6, cycle 6-7
    NVDA    Hyper-growth; expect ROIIC 6-7, buybacks 5-6, divs none, cycle 4-5 (peak)
    KO      Mature compounder; expect ROIIC 5-6, buybacks 5, divs 6-7, cycle 7
    JPM     Bank; ROIIC may not compute (schema), buybacks 5, divs 5-6, cycle 4
    CNC     Managed care; expect ROIIC 4-5, buybacks 4, divs none, cycle 5
    BRK-B   Conglomerate; ROIIC 5-6, buybacks 5-6, divs none, cycle 5-6

Run with:
    PYTHONPATH=. python scripts/calibrate_qualitative.py
    PYTHONPATH=. python scripts/calibrate_qualitative.py --no-write    # dry-run

Dry-run computes scores without persisting to the DB — useful when you
want to see "what would the runner produce" without bumping versions.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Dict, List

# Allow `python scripts/calibrate_qualitative.py` from the repo root
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from aletheia.data.database import InvestmentDatabase
from aletheia.qualitative.computers import COMPUTERS


# Ordered for the table; PENDING_DATA / LLM_AUGMENTED dims excluded
DETERMINISTIC_DIMS = ["roiic_trend", "buyback_discipline", "dividend_policy", "cyclicality"]

ANCHOR_TICKERS = ["AAPL", "NVDA", "KO", "JPM", "CNC", "BRK-B"]


def _format_score(score):
    if score is None:
        return "    —"
    return f"    {int(score)}"


def _run_dry(tickers: List[str]) -> Dict[str, Dict[str, object]]:
    """Compute scores without persisting. Used for calibration review."""
    db = InvestmentDatabase(verbose=False)
    out: Dict[str, Dict[str, object]] = {}
    try:
        for ticker in tickers:
            df = db.get_latest(ticker.upper())
            row: Dict[str, object] = {}
            for dim_id, computer in COMPUTERS.items():
                if df.empty:
                    row[dim_id] = None
                    continue
                try:
                    result = computer(df)
                    row[dim_id] = result.score if result is not None else None
                    row[f"{dim_id}__payload"] = (
                        result.source_payload if result is not None else None
                    )
                except Exception as e:
                    row[dim_id] = f"ERR:{type(e).__name__}"
            out[ticker] = row
    finally:
        db.close()
    return out


def _ticker_universe_union() -> List[str]:
    """Same union pattern as api_main but without importing FastAPI."""
    out = set()
    try:
        from config.ticker_classification import get_extended_universe
        out.update(get_extended_universe().keys())
    except Exception:
        pass
    try:
        import duckdb
        con = duckdb.connect("valuation_data/database/investment.duckdb", read_only=True)
        rows = con.execute("SELECT DISTINCT ticker FROM company_records").fetchall()
        out.update(r[0] for r in rows)
        con.close()
    except Exception:
        pass
    return sorted(out)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--anchors-only", action="store_true",
                   help="Only run on the 6 anchor tickers (fast).")
    p.add_argument("--no-write", action="store_true",
                   help="Dry-run: compute scores without persisting.")
    p.add_argument("--show-payloads", action="store_true",
                   help="Print source_payload alongside the score table.")
    args = p.parse_args()

    if args.anchors_only:
        tickers = ANCHOR_TICKERS
    else:
        tickers = _ticker_universe_union()

    print(f"# Qualitative calibration — {len(tickers)} tickers\n")

    if args.no_write:
        scores = _run_dry(tickers)
    else:
        # Use the runner so version churn semantics are exercised
        from aletheia.qualitative.runner import recompute_universe
        results = recompute_universe(tickers)
        scores = {
            t: {r["dimension_id"]: r["score"] for r in results[t]}
            for t in tickers
        }
        # Re-fetch payloads for display from a dry run pass (cheap)
        if args.show_payloads:
            payloads = _run_dry(tickers)
            for t in tickers:
                for dim in DETERMINISTIC_DIMS:
                    scores[t][f"{dim}__payload"] = payloads.get(t, {}).get(f"{dim}__payload")

    # Print markdown table
    header = "| Ticker | " + " | ".join(d.replace("_", " ").title() for d in DETERMINISTIC_DIMS) + " |"
    sep    = "|" + "|".join(["---"] * (len(DETERMINISTIC_DIMS) + 1)) + "|"
    print(header)
    print(sep)
    for ticker in tickers:
        row_scores = [_format_score(scores.get(ticker, {}).get(d)) for d in DETERMINISTIC_DIMS]
        print(f"| {ticker:6s} | " + " | ".join(row_scores) + " |")

    # Detail for anchors
    print("\n## Anchor detail\n")
    for t in ANCHOR_TICKERS:
        if t not in scores:
            continue
        print(f"### {t}")
        for dim in DETERMINISTIC_DIMS:
            sc = scores.get(t, {}).get(dim)
            print(f"- **{dim}**: score={sc}")
            if args.show_payloads:
                pl = scores.get(t, {}).get(f"{dim}__payload")
                if pl:
                    for k, v in pl.items():
                        print(f"    - {k}: {v}")
        print()


if __name__ == "__main__":
    main()
