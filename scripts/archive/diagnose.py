"""
diagnose.py — run from project root with: PYTHONPATH=. python3 diagnose.py

Diagnoses three issues found in the first pipeline run:
1. Revenue NaN for 2009-2018 (pre-ASC 606 tag mapping)
2. ROIC NaN (OperatingIncome/EBIT not resolving)
3. Duplicate rows in flagged_for_review view

Run this, paste the full output back.
"""

import json
import pandas as pd
from pathlib import Path

print("=" * 60)
print("DIAGNOSTIC REPORT")
print("=" * 60)

# ── 1. What tags are actually in the canonical parquet for AAPL ───────────
print("\n[1] CANONICAL PARQUET — AAPL tag inventory by year")
print("-" * 60)

parquet_path = Path("valuation_data/canonical/financials/AAPL.parquet")
if parquet_path.exists():
    df = pd.read_parquet(parquet_path)
    print(f"Total rows: {len(df)}")
    print(f"Fiscal years available: {sorted(df['fy'].dropna().unique().astype(int).tolist())}")
    print(f"\nAll standard_tags present:")
    tag_counts = df.groupby('standard_tag')['fy'].count().sort_values(ascending=False)
    print(tag_counts.to_string())

    print(f"\nPer-year tag presence (which tags resolved per FY):")
    pivot = df.pivot_table(
        index='fy', columns='standard_tag', values='value',
        aggfunc='first'
    )
    print(pivot.to_string())
else:
    print(f"ERROR: {parquet_path} not found")

# ── 2. What raw XBRL tags does AAPL use for Revenue ──────────────────────
print("\n[2] RAW XBRL — AAPL Revenue-related tags")
print("-" * 60)

# Find CIK
cik_path = Path("valuation_data/raw/sec/company_tickers/company_tickers.json")
cik = None
if cik_path.exists():
    with open(cik_path) as f:
        tickers_data = json.load(f)
    for _, v in tickers_data.items():
        if v["ticker"].upper() == "AAPL":
            cik = str(v["cik_str"]).zfill(10)
            break

if cik:
    facts_path = Path(f"valuation_data/raw/sec/companyfacts/CIK{cik}.json")
    if facts_path.exists():
        with open(facts_path) as f:
            raw = json.load(f)

        us_gaap = raw.get("facts", {}).get("us-gaap", {})

        # Revenue-related tags
        revenue_keywords = [
            "Revenue", "Sales", "NetRevenue", "NetSales",
            "RevenueFromContract", "SalesRevenue"
        ]

        print("Revenue-related tags found in AAPL raw XBRL:")
        for tag in us_gaap.keys():
            if any(kw.lower() in tag.lower() for kw in revenue_keywords):
                units = us_gaap[tag].get("units", {}).get("USD", [])
                annual = [u for u in units if u.get("form") == "10-K"]
                if annual:
                    years = sorted(set(u.get("fy") for u in annual if u.get("fy")))
                    print(f"  {tag}: {len(annual)} 10-K filings, years={years}")

        # Operating income / EBIT tags
        print("\nOperating Income / EBIT tags found in AAPL raw XBRL:")
        ebit_keywords = ["OperatingIncome", "OperatingProfit", "EBIT", "IncomeLossFromOp"]
        for tag in us_gaap.keys():
            if any(kw.lower() in tag.lower() for kw in ebit_keywords):
                units = us_gaap[tag].get("units", {}).get("USD", [])
                annual = [u for u in units if u.get("form") == "10-K"]
                if annual:
                    years = sorted(set(u.get("fy") for u in annual if u.get("fy")))
                    print(f"  {tag}: {len(annual)} 10-K filings, years={years}")

        # Show all available tags (top 50 by filing count)
        print("\nAll XBRL tags in AAPL with 10-K filings (sorted by count):")
        tag_annual_counts = {}
        for tag, concept in us_gaap.items():
            units = concept.get("units", {}).get("USD", [])
            annual_count = len([u for u in units if u.get("form") == "10-K"])
            if annual_count > 0:
                tag_annual_counts[tag] = annual_count
        for tag, cnt in sorted(tag_annual_counts.items(), key=lambda x: -x[1])[:60]:
            print(f"  {tag}: {cnt}")
    else:
        print(f"ERROR: Raw facts not found at {facts_path}")
else:
    print("ERROR: CIK not found for AAPL")

# ── 3. Check the cleaned record directly ─────────────────────────────────
print("\n[3] CLEANED RECORD — what values are in clean/raw/derived for FY2023")
print("-" * 60)

try:
    from aletheia.data.cleaning_engine import CleaningEngine
    engine = CleaningEngine(verbose=False)
    record = engine.clean("AAPL", 2023)

    print("raw metrics (non-None):")
    for k, v in record.raw.items():
        if v is not None:
            print(f"  {k}: {v:,.0f}")

    print("\nclean metrics (non-None):")
    for k, v in record.clean.items():
        if v is not None:
            print(f"  {k}: {v}")

    print("\nderived metrics (non-None):")
    for k, v in record.derived.items():
        if v is not None:
            print(f"  {k}: {v}")

    print(f"\nQuality score: {record.overall_quality_score}")
    print(f"Warnings: {record.cleaning_warnings}")
    print(f"Flags: {len(record.flags)}")
    for flag in record.flags:
        print(f"  D{flag.domain} {flag.metric}: {flag.action} — {flag.reason[:80]}")

except Exception as e:
    print(f"ERROR running cleaning engine: {e}")
    import traceback
    traceback.print_exc()

# ── 4. Check the duplicate view issue ────────────────────────────────────
print("\n[4] DATABASE — flagged_for_review row count check")
print("-" * 60)

try:
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)

    cr_count = db.query("SELECT COUNT(*) as n FROM company_records").iloc[0]["n"]
    sr_count = db.query("SELECT COUNT(*) as n FROM screen_results").iloc[0]["n"]
    flag_count = db.query("SELECT COUNT(*) as n FROM cleaning_flags").iloc[0]["n"]
    view_count = db.query("SELECT COUNT(*) as n FROM flagged_for_review").iloc[0]["n"]
    latest_count = db.query("SELECT COUNT(*) as n FROM company_records_latest").iloc[0]["n"]

    print(f"company_records rows   : {cr_count}")
    print(f"company_records_latest : {latest_count}")
    print(f"screen_results rows    : {sr_count}")
    print(f"cleaning_flags rows    : {flag_count}")
    print(f"flagged_for_review rows: {view_count}")

    print("\nSample company_records_latest:")
    print(db.query(
        "SELECT ticker, fiscal_year, version, overall_quality_score, warning_count "
        "FROM company_records_latest ORDER BY fiscal_year"
    ).to_string())

    print("\nSample screen_results:")
    print(db.query(
        "SELECT ticker, fiscal_year, beneish_m_score, sloan_accrual_ratio, any_flagged "
        "FROM screen_results ORDER BY fiscal_year"
    ).to_string())

    db.close()
except Exception as e:
    print(f"ERROR: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("DIAGNOSTIC COMPLETE")
print("=" * 60)
