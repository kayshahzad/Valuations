#!/usr/bin/env python3
"""Universe field-completeness audit.

Flags ticker-years where a critical financial field is blank *despite the
company existing that year* (revenue present). Catches provider-regressions
like an accidental xbrl-only run that leaves operating income / COGS holes
(see the 2026-07 LLY gap: filers that don't tag OperatingIncomeLoss + used the
legacy CostOfGoodsSold tag). The hybrid provider fills these from FMP; this
audit is the guard that they stay filled.

Usage:
  python -m scripts.audit_field_completeness [--db PATH] [--json OUT] [--quiet]

Exit code 0 = clean, 1 = unexplained blanks found (usable as a CI/post-pipeline gate).
"""
from __future__ import annotations

import argparse
import json
import sys
from typing import Dict, List

import duckdb

DEFAULT_DB = "valuation_data/database/investment.duckdb"

# Fields that must be present for any FY where the company had revenue.
# `operating_income` is satisfied by EITHER the reported or the normalized field
# (some filers never tag reported operating income; the normalized path fills it).
CORE_FIELDS = ["raw_Revenue", "raw_NetIncome", "raw_TotalAssets"]
OPERATING_INCOME_ANY = ["derived_OperatingIncome", "clean_NormalizedEBIT"]

# Fields to report on but NOT fail the build for (business-model dependent —
# e.g. banks/insurers don't report COGS).
SOFT_FIELDS = ["raw_COGS"]


def audit(db_path: str) -> Dict:
    con = duckdb.connect(db_path, read_only=True)
    cols = [c[1] for c in con.execute("PRAGMA table_info('company_records_latest')").fetchall()]
    tracked = [c for c in CORE_FIELDS + OPERATING_INCOME_ANY + SOFT_FIELDS if c in cols]
    df = con.execute(
        f"SELECT ticker, fiscal_year, {', '.join(tracked)} "
        "FROM company_records_latest WHERE period='FY'"
    ).df()

    # Only years where the company existed (revenue present) can be "unexplained".
    existed = df[df["raw_Revenue"].notna()]

    hard_blanks: List[Dict] = []
    for _, r in existed.iterrows():
        missing = [f for f in CORE_FIELDS if f in tracked and _isblank(r.get(f))]
        if all(f in tracked and _isblank(r.get(f)) for f in OPERATING_INCOME_ANY):
            missing.append("operating_income(any)")
        if missing:
            hard_blanks.append({"ticker": r["ticker"], "fiscal_year": int(r["fiscal_year"]),
                                "missing": missing})

    soft_blanks = {
        f: int(existed[f].isna().sum()) for f in SOFT_FIELDS if f in tracked
    }
    result = {
        "fy_rows": int(len(df)),
        "fy_rows_with_revenue": int(len(existed)),
        "hard_blank_count": len(hard_blanks),
        "hard_blanks": hard_blanks[:200],
        "soft_blank_counts": soft_blanks,
        "clean": len(hard_blanks) == 0,
    }
    return result


def _isblank(v) -> bool:
    return v is None or (isinstance(v, float) and v != v)  # None or NaN


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=DEFAULT_DB)
    ap.add_argument("--json", default=None, help="write full result JSON here")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    res = audit(args.db)
    if args.json:
        json.dump(res, open(args.json, "w"), indent=2)
    if not args.quiet:
        print(f"FY rows: {res['fy_rows']} ({res['fy_rows_with_revenue']} with revenue)")
        print(f"soft (business-model) blanks: {res['soft_blank_counts']}")
        if res["clean"]:
            print("✅ CLEAN — no unexplained blank core fields")
        else:
            print(f"❌ {res['hard_blank_count']} unexplained blank core-field rows:")
            for b in res["hard_blanks"][:40]:
                print(f"   {b['ticker']} FY{b['fiscal_year']}: {', '.join(b['missing'])}")
    return 0 if res["clean"] else 1


if __name__ == "__main__":
    sys.exit(main())
