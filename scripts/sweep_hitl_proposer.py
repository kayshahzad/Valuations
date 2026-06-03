"""Universe-wide cold-start sweep: run the HITL proposer on every
ticker, then print a review-ordering table ranked by LLM confidence
(low → high) per the D=b approval (review least-confident first).

Usage:
    python -m scripts.sweep_hitl_proposer            # entire universe
    python -m scripts.sweep_hitl_proposer AAPL MSFT  # subset

Cost note: one LLM call per ticker × universe (41 tickers default) ≈
$20-25 with Gemini 3.1 Pro. Re-runs are idempotent on
(input_fingerprint, code_git_sha) — same 10-K → no LLM spend.

Per-ticker outcomes:
    written           — new LLM proposal stored
    unchanged         — same 10-K + git sha as prior proposal → skipped
    drift_recorded    — analyst-owned dim; new proposal stored as drift
    error             — failed mid-sweep (logged, continues)
    no_10k            — librarian didn't return a 10-K (e.g. foreign filer)

After the sweep, prints the per-dim ranking by confidence so the
analyst knows which tickers to review first.
"""

from __future__ import annotations

import sys
import traceback
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config.ticker_classification import get_extended_universe


_10K_CACHE_ROOT = Path("valuation_data/raw/sec/filings")
_CONFIDENCE_ORDER = {"low": 0, "medium": 1, "high": 2}


def _load_cached_10k(ticker: str) -> Optional[str]:
    """Find the newest 10K_*.md file under the cached filings tree.
    Returns None when the librarian hasn't fetched a 10-K for this
    ticker (legitimate for some foreign filers)."""
    folder = _10K_CACHE_ROOT / ticker
    if not folder.exists():
        return None
    candidates = sorted(folder.glob("10K_*.md"))
    if not candidates:
        return None
    return candidates[-1].read_text(encoding="utf-8")


def _propose_for_ticker(ticker: str) -> Tuple[List[dict], str]:
    """Run the HITL proposer for one ticker via the existing
    qualitative_extraction helper. Returns (outcomes, status_label).
    status_label is 'ok' on a normal proposer run, 'no_10k' when no
    10-K was found locally, 'error' on exception (the proposer's
    persistence layer logs the detail)."""
    raw_10k = _load_cached_10k(ticker)
    if not raw_10k:
        return [], "no_10k"
    try:
        from aletheia.agents.qualitative_extraction import _run_hitl_proposer
        outcomes = _run_hitl_proposer(ticker, raw_10k)
        return outcomes, "ok"
    except Exception as exc:
        print(f"  ✗ {ticker}: {type(exc).__name__}: {exc}")
        traceback.print_exc()
        return [], "error"


def _review_priority_table(tickers: List[str]) -> List[Tuple[str, str, str, float]]:
    """Build a (ticker, dim_id, confidence, score) list ranked so that
    the analyst's review queue starts with the lowest-confidence
    proposals. Per D=b — least-confident gets attention first."""
    from aletheia.data.database import InvestmentDatabase
    rows: List[Tuple[str, str, str, float]] = []
    db = InvestmentDatabase(verbose=False)
    try:
        for t in tickers:
            assessments = db.get_all_assessments_for_ticker(t)
            for dim_id, rec in assessments.items():
                if rec.get("provenance") != "llm_proposed":
                    continue
                if rec.get("review_state") != "unreviewed":
                    continue
                conf = rec.get("confidence") or "low"
                rows.append((t, dim_id, conf, rec.get("score") or 0.0))
    finally:
        db.close()
    rows.sort(key=lambda r: (_CONFIDENCE_ORDER.get(r[2], 0), r[0], r[1]))
    return rows


def main(argv: List[str]) -> int:
    if argv:
        tickers = [t.upper() for t in argv]
    else:
        tickers = sorted(get_extended_universe().keys())
    print(f"Sweeping HITL proposer over {len(tickers)} ticker(s)…")

    status_counts: Counter = Counter()
    outcome_counts: Counter = Counter()
    per_ticker_status: Dict[str, str] = {}

    for i, ticker in enumerate(tickers, start=1):
        print(f"[{i:>2}/{len(tickers)}] {ticker} … ", end="", flush=True)
        outcomes, status = _propose_for_ticker(ticker)
        per_ticker_status[ticker] = status
        status_counts[status] += 1
        for o in outcomes:
            outcome_counts[o.get("status") or "unknown"] += 1
        print(f"{status} ({len(outcomes)} dims)")

    print()
    print("=" * 60)
    print("SWEEP COMPLETE")
    print("=" * 60)
    print(f"Tickers:       {dict(status_counts)}")
    print(f"Dim outcomes:  {dict(outcome_counts)}")
    print()

    # Review-priority table — lowest confidence first
    print("Review queue (lowest LLM confidence first, then alphabetical):")
    queue = _review_priority_table(tickers)
    if not queue:
        print("  (empty — no unreviewed LLM proposals; either all tickers "
              "already reviewed or no 10-K text available)")
    else:
        print(f"  {'#':>3}  {'ticker':>7}  {'dimension':>34}  "
              f"{'confidence':>10}  {'score':>5}")
        for i, (t, d, c, s) in enumerate(queue[:40], start=1):
            print(f"  {i:>3}  {t:>7}  {d:>34}  {c:>10}  {s:>5.2f}")
        if len(queue) > 40:
            print(f"  …({len(queue) - 40} more — full list saved to audit log)")

    # Save full queue to audits for reference
    from datetime import date
    audit_path = Path(f"audits/hitl_proposer_sweep_{date.today()}.txt")
    audit_path.parent.mkdir(parents=True, exist_ok=True)
    with audit_path.open("w") as fh:
        fh.write(f"# HITL proposer sweep — {date.today().isoformat()}\n")
        fh.write(f"# {len(tickers)} tickers, {len(queue)} unreviewed proposals\n\n")
        fh.write(f"{'ticker':<8}\t{'dimension':<34}\t{'confidence':<10}\t{'score':<5}\n")
        for t, d, c, s in queue:
            fh.write(f"{t:<8}\t{d:<34}\t{c:<10}\t{s:<5.2f}\n")
    print(f"\nFull queue written to {audit_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
