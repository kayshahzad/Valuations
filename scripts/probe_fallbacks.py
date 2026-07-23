#!/usr/bin/env python3
"""Phase-0 fallback hit-count probe (fix-plan task 0.3.2).

Re-cleans each universe ticker OFFLINE (is_latest_fy=False → no Gate A / no live
FMP) with the value-neutral instrumentation active, and aggregates how often each
HOT fallback actually fires across the universe — turning the static site map
(0.2.1) into a measured blast-radius: `site × tickers affected`, split into
fabricated-zero (dangerous) vs genuine-miss.

    python scripts/probe_fallbacks.py            # -> scratch/fallback_hits.json + docs/fallback_hits.md
    python scripts/probe_fallbacks.py --years 3  # re-clean last N FY per ticker
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from collections import defaultdict
from pathlib import Path

os.environ.setdefault("ALETHEIA_GUARD_MODE", "shadow")
logging.disable(logging.WARNING)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _fy_list(ticker: str, n: int) -> list[int]:
    import duckdb
    con = duckdb.connect(str(ROOT / "valuation_data/database/investment.duckdb"), read_only=True)
    try:
        rows = con.execute(
            "select distinct fiscal_year from company_records "
            "where ticker=? and period='FY' order by fiscal_year desc limit ?",
            [ticker, n]).fetchall()
        return [int(r[0]) for r in rows]
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=1, help="last N FY per ticker (default 1)")
    ap.add_argument("--json-out", default=str(ROOT / "scratch" / "fallback_hits.json"))
    ap.add_argument("--md-out", default=str(ROOT / "docs" / "fallback_hits.md"))
    args = ap.parse_args()

    from config.ticker_classification import get_extended_universe
    from aletheia.data.cleaning_engine import CleaningEngine

    tickers = sorted(get_extended_universe())
    eng = CleaningEngine(verbose=False)

    # site -> {tickers:set, events:int, raw_zero:int, miss:int, field:str}
    agg: dict = defaultdict(lambda: {"tickers": set(), "events": 0, "raw_zero": 0,
                                     "miss": 0, "field": None})
    n_clean = n_fail = 0
    for t in tickers:
        for fy in _fy_list(t, args.years):
            try:
                rec = eng.clean(t, fy, is_latest_fy=False)
            except Exception:
                n_fail += 1
                continue
            n_clean += 1
            for ev in rec.fallbacks_applied:
                a = agg[ev["site"]]
                a["field"] = ev["field"]
                a["tickers"].add(t)
                a["events"] += 1
                a["raw_zero"] += 1 if ev["raw_zero"] else 0
                a["miss"] += 0 if ev["raw_zero"] else 1
        print(f"  {t:6} done", file=sys.stderr)

    out = {"_meta": {"records_cleaned": n_clean, "records_failed": n_fail,
                     "years_per_ticker": args.years, "universe": len(tickers)},
           "sites": {s: {**v, "tickers": sorted(v["tickers"]),
                         "n_tickers": len(v["tickers"])}
                     for s, v in sorted(agg.items())}}
    Path(args.json_out).parent.mkdir(parents=True, exist_ok=True)
    json.dump(out, open(args.json_out, "w"), indent=2)

    # Markdown companion for the map.
    lines = [
        "# Fallback hit-counts — measured blast radius (task 0.3.2)",
        "",
        f"Re-cleaned {n_clean} records ({args.years} FY/ticker × {len(tickers)} tickers, "
        f"{n_fail} failed) with value-neutral instrumentation active.",
        "**`raw_zero`** = a real 0 was fabricated to a constant (dangerous); "
        "**`miss`** = value was genuinely absent.",
        "",
        "| site | field | tickers | events | fabricated-zero | miss |",
        "|---|---|---|---|---|---|",
    ]
    for s, v in sorted(out["sites"].items(), key=lambda x: -x[1]["n_tickers"]):
        lines.append(f"| `{s}` | `{v['field']}` | {v['n_tickers']} | {v['events']} "
                     f"| {v['raw_zero']} | {v['miss']} |")
    Path(args.md_out).write_text("\n".join(lines) + "\n")

    print(f"\ncleaned={n_clean} failed={n_fail} · {len(agg)} instrumented sites fired",
          file=sys.stderr)
    for s, v in sorted(agg.items(), key=lambda x: -len(x[1]["tickers"])):
        print(f"  {s:28} {v['field']:16} tickers={len(v['tickers']):2d} "
              f"events={v['events']:3d} fab0={v['raw_zero']:3d} miss={v['miss']:3d}",
              file=sys.stderr)
    print(f"wrote {args.json_out} + {args.md_out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
