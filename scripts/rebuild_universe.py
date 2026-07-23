#!/usr/bin/env python3
"""Deterministic no-LLM universe rebuild.

Re-cleans every universe ticker with CURRENT code and persists to DuckDB,
propagating all Stage-2 cleaning fixes (tax unification, AR trade-only,
negative-pretax guard, Gate A receipt, …) to every ticker's persisted numbers.
No LLM, no serving-report regeneration — only the deterministic
company_records the app computes from.

Prints a before/after mover report (latest-FY NOPAT / ROIC / AR changes >2%).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

os.environ.setdefault("ALETHEIA_GUARD_MODE", "shadow")
logging.disable(logging.WARNING)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DB = str(ROOT / "valuation_data/database/investment.duckdb")


def _snapshot() -> dict:
    import duckdb
    c = duckdb.connect(DB, read_only=True)
    try:
        rows = c.execute(
            "select ticker,fiscal_year,clean_NOPAT,derived_ROIC,raw_json "
            "from company_records_latest where period='FY'").fetchall()
    finally:
        c.close()
    out: dict = {}
    for t, fy, nopat, roic, rj in rows:
        if t not in out or fy > out[t][0]:
            ar = json.loads(rj).get("AccountsReceivable") if rj else None
            out[t] = (fy, nopat, roic, ar)
    return out


def main() -> int:
    from config.ticker_classification import get_extended_universe
    from aletheia.data.cleaning_engine import CleaningEngine
    from aletheia.data.database import InvestmentDatabase

    before = _snapshot()
    eng = CleaningEngine(verbose=False)
    db = InvestmentDatabase(verbose=False)
    tickers = sorted(get_extended_universe())

    persisted = failed = clean_fail = 0
    for i, t in enumerate(tickers, 1):
        try:
            recs = eng.clean_all_years(t)
        except Exception as e:
            clean_fail += 1
            print(f"  [{i}/{len(tickers)}] {t}: CLEAN FAILED {type(e).__name__}", file=sys.stderr)
            continue
        pf = 0
        for r in recs:
            try:
                db.upsert_record(r)
                persisted += 1
            except Exception:
                failed += 1
                pf += 1
        print(f"  [{i}/{len(tickers)}] {t}: {len(recs)} recs ({pf} rejected)", file=sys.stderr)
    db.close()

    after = _snapshot()

    print(f"\npersisted={persisted} rejected={failed} clean_fail={clean_fail}", file=sys.stderr)
    movers = []
    for t in sorted(set(before) & set(after)):
        for idx, name in ((1, "NOPAT"), (2, "ROIC"), (3, "AR")):
            o, n = before[t][idx], after[t][idx]
            if o and n and abs(n - o) > 0.02 * abs(o):
                movers.append((t, name, o, n, (n - o) / abs(o) * 100))
    print(f"\n=== movers (latest-FY, >2%): {len(movers)} ===")
    for t, name, o, n, pct in sorted(movers, key=lambda x: -abs(x[4])):
        fmt = (lambda v: f"{v / 1e9:8.2f}B") if name != "ROIC" else (lambda v: f"{v:8.4f} ")
        print(f"  {t:6} {name:6} {fmt(o)} -> {fmt(n)} ({pct:+6.1f}%)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
