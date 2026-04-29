"""
tag_miss_audit.py

Runs across all tickers to identify:
1. Fallback values that fired (silent substitutions)
2. Tags that resolved to zero when they should be non-zero
3. Tags that are completely missing from the record

Run: python3 tag_miss_audit.py
Output: tag_misses.json + printed summary

Key insight: the tag_resolver has D&A tags mapped correctly.
The real issue is the canonical transformer writes a low/zero value
that the resolver then skips (never overwrites non-zero).
This script identifies WHICH specific fields have this problem
for WHICH tickers so we can fix the root cause.
"""

import json
import sys
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────

TICKERS = [
    "MSFT", "AAPL", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "SMCI",
    "LLY", "COST", "NEE", "CAT", "JPM", "BRK-B", "V", "WMT", "UNH",
    "ABT", "AMD", "ASML", "TSM", "QCOM", "ORCL", "TXN", "CNC",
]

REPORT_DIR = Path("valuation_data/serving/latest")
RAW_DIR    = Path("valuation_data/raw/sec")

# Fields that MUST be non-zero for any real company
# with the expected order of magnitude as % of revenue
REQUIRED_FIELDS = {
    # field_path_in_report          : (min_pct_of_rev, description)
    "clean_financials.ebitda_bn"    : (0.05,  "EBITDA must be >5% of revenue"),
    "clean_financials.fcf_bn"       : (None,  "FCF can be negative, flag if None"),
    "clean_financials.nopat_bn"     : (0.01,  "NOPAT must be >1% of revenue"),
    "ratios.roic"                   : (0.01,  "ROIC must be present"),
    "ratios.gross_margin_pct"       : (1.0,   "Gross margin pct must be >1"),
    "ratios.ebit_margin_pct"        : (0.1,   "EBIT margin pct must be >0.1"),
    "ratios.fcf_margin_pct"         : (None,  "FCF margin can be negative"),
    "ratios.sbc_pct_fcf"            : (None,  "SBC pct - flag if None"),
    "ratios.cash_tax_rate"          : (None,  "Tax rate - flag if None"),
}

# Fields where the 3% revenue fallback commonly fires
# D&A implied = EBITDA - EBIT. If ~= 3% of revenue, fallback fired.
FALLBACK_CHECKS = {
    "da_fallback": {
        "desc": "D&A 3% fallback",
        "compute": lambda r: _da_implied(r),
        "fallback_fn": lambda r: _rev(r) * 0.03,
        "tolerance": 0.5e9,  # $500M
    }
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def _get(report, path):
    """Navigate dot-separated path in nested dict."""
    obj = report.get("2_financial_translation", {})
    for k in path.split("."):
        obj = (obj or {}).get(k)
    return obj

def _rev(r):
    v = _get(r, "clean_financials.revenue_bn")
    return (v or 0) * 1e9

def _da_implied(r):
    ebitda = (_get(r, "clean_financials.ebitda_bn") or 0) * 1e9
    rev    = _rev(r)
    ebit_m = _get(r, "ratios.ebit_margin_pct") or 0
    # ebit_margin_pct stored as percent (e.g. 45.6) not decimal
    ebit   = rev * ebit_m / 100 if ebit_m > 1 else rev * ebit_m
    return ebitda - ebit if ebitda and ebit else None

def _check_raw_xbrl(ticker, fiscal_year, tag):
    """Check if a specific XBRL tag exists in raw companyfacts for a ticker."""
    try:
        cik_path = RAW_DIR / "company_tickers" / "company_tickers.json"
        with open(cik_path) as f:
            tickers_data = json.load(f)
        cik = None
        for _, v in tickers_data.items():
            if v["ticker"].upper() == ticker.upper():
                cik = str(v["cik_str"]).zfill(10)
                break
        if not cik:
            return None

        facts_path = RAW_DIR / "companyfacts" / f"CIK{cik}.json"
        if not facts_path.exists():
            return None

        with open(facts_path) as f:
            facts = json.load(f)

        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        ifrs    = facts.get("facts", {}).get("ifrs-full", {})

        # Check if tag exists
        if tag in us_gaap:
            units = us_gaap[tag].get("units", {}).get("USD", [])
            annual = [u for u in units if u.get("form") in ("10-K","20-F","40-F")
                      and u.get("fy") == fiscal_year]
            return {"found": True, "source": "us-gaap", "annual_entries": len(annual),
                    "sample_val": annual[0].get("val") if annual else None}
        elif tag in ifrs:
            return {"found": True, "source": "ifrs-full"}
        else:
            return {"found": False}
    except Exception as e:
        return {"error": str(e)}

# ── Main audit ────────────────────────────────────────────────────────────────

def audit():
    results = {}
    summary = {"fallback_fires": [], "missing_fields": [], "zero_fields": [],
               "none_fields": [], "clean": []}

    print(f"\n{'═'*70}")
    print("TAG MISS AUDIT — Full Universe")
    print(f"{'═'*70}\n")
    print(f"{'Ticker':>6} | {'Rev':>6} | {'EBITDA':>7} | {'D&A impl':>9} | "
          f"{'D&A 3%':>7} | {'FALLBACK?':>10} | {'SBC%FCF':>8} | {'TaxRate':>8}")
    print("─" * 80)

    for ticker in TICKERS:
        path = REPORT_DIR / f"{ticker}_report.json"
        if not path.exists():
            print(f"{ticker:>6} | NO REPORT")
            continue

        r = json.loads(path.read_text())
        fin    = r.get("2_financial_translation", {})
        cf     = fin.get("clean_financials", {})
        ratios = fin.get("ratios", {})
        fy     = cf.get("fiscal_year", 2024)

        rev_bn    = cf.get("revenue_bn", 0) or 0
        ebitda_bn = cf.get("ebitda_bn", 0) or 0
        da_impl   = _da_implied(r)
        da_fall   = rev_bn * 0.03
        sbc_pct   = ratios.get("sbc_pct_fcf")
        tax_rate  = ratios.get("cash_tax_rate")

        # Check if D&A fallback fired (within $500M tolerance)
        fallback_fired = (da_impl is not None and
                          abs(da_impl - da_fall * 1e9) < 0.5e9)

        ticker_issues = []

        if fallback_fired:
            ticker_issues.append({
                "type": "FALLBACK",
                "field": "Depreciation/D&A",
                "model_value": da_impl,
                "fallback_value": da_fall * 1e9,
                "fiscal_year": fy,
            })
            summary["fallback_fires"].append(ticker)

        if sbc_pct is None:
            ticker_issues.append({"type": "NONE", "field": "sbc_pct_fcf"})
            summary["none_fields"].append(f"{ticker}.sbc_pct_fcf")

        if tax_rate is None:
            ticker_issues.append({"type": "NONE", "field": "cash_tax_rate"})
            summary["none_fields"].append(f"{ticker}.cash_tax_rate")

        # Check other required fields
        for field_path, (min_pct, desc) in REQUIRED_FIELDS.items():
            val = _get(r, field_path)
            if val is None:
                ticker_issues.append({"type": "MISSING", "field": field_path, "desc": desc})
                summary["missing_fields"].append(f"{ticker}.{field_path}")
            elif val == 0:
                ticker_issues.append({"type": "ZERO", "field": field_path, "desc": desc})
                summary["zero_fields"].append(f"{ticker}.{field_path}")
            elif min_pct and val > 0 and rev_bn > 0:
                # Check plausibility
                ratio = (val * 1e9) / (rev_bn * 1e9) if "bn" in field_path else val / 100
                if ratio < min_pct and field_path not in ("ratios.fcf_margin_pct",):
                    ticker_issues.append({
                        "type": "IMPLAUSIBLE",
                        "field": field_path,
                        "value": val,
                        "pct_of_rev": ratio,
                        "min_expected": min_pct,
                    })

        if not ticker_issues:
            summary["clean"].append(ticker)

        results[ticker] = {
            "issues": ticker_issues,
            "fiscal_year": fy,
            "revenue_bn": rev_bn,
            "ebitda_bn": ebitda_bn,
            "da_implied_bn": da_impl / 1e9 if da_impl else None,
            "da_3pct_bn": da_fall,
            "fallback_fired": fallback_fired,
        }

        # Print row
        sbc_str  = f"{sbc_pct:.1f}%" if sbc_pct else "None ⚠"
        tax_str  = f"{tax_rate:.1%}" if tax_rate else "None ⚠"
        fall_str = "⚠ FALLBACK" if fallback_fired else "✓"
        da_str   = f"{da_impl/1e9:.1f}B" if da_impl else "N/A"

        print(f"{ticker:>6} | {rev_bn:>5.0f}B | {ebitda_bn:>6.1f}B | "
              f"{da_str:>9} | {da_fall:>6.1f}B | {fall_str:>10} | "
              f"{sbc_str:>8} | {tax_str:>8}")

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'═'*70}")
    print("SUMMARY")
    print(f"{'═'*70}")
    print(f"  Clean (no issues):      {len(summary['clean'])} tickers — {summary['clean']}")
    print(f"  D&A fallback fired:     {len(summary['fallback_fires'])} tickers — {summary['fallback_fires']}")
    print(f"  None fields:            {len(summary['none_fields'])}")
    for f in summary['none_fields']:
        print(f"    {f}")
    print(f"  Zero fields:            {len(summary['zero_fields'])}")
    for f in summary['zero_fields']:
        print(f"    {f}")
    print(f"  Missing fields:         {len(summary['missing_fields'])}")

    print(f"\n{'═'*70}")
    print("ROOT CAUSE ANALYSIS")
    print(f"{'═'*70}")
    print("""
The D&A fallback fires when:
  intake.py: dep = data.get("depreciation") or (rev * 0.03)

The tag_resolver DOES have DepreciationDepletionAndAmortization mapped.
But enrich_from_xbrl() skips tags already present (even if zero):
  if clean_name in enriched and enriched[clean_name] not in (None, 0.0):
      continue  ← this is correct

The real problem is the canonical_transformer writing a low/zero value
for "depreciation" which then blocks the XBRL enrichment.

FIX REQUIRED IN CLEANING_ENGINE / INTAKE:
  dep = data.get("depreciation")
  if not dep or dep == 0:
      # Do NOT fallback to 3% — instead flag as missing
      record.cleaning_warnings.append(...)
      dep = None
      # EBITDA = None (cannot compute without D&A)
      # This surfaces as a quality warning, not a silent wrong number
""")

    # Save full results
    with open("tag_miss_audit_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("Full results saved to: tag_miss_audit_results.json")

if __name__ == "__main__":
    audit()
