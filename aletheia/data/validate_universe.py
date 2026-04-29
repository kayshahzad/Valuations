"""
aletheia/data/validate_universe.py

Phase C — Universe Cross-Reference Check
==========================================
After running the ingestion pipeline, cross-references every ticker
against known-good public filing values in config/universe_reference.json.

Any ticker outside tolerance is flagged with the exact delta and the
field that caused the deviation. This is the final gate before the
agent sprint runs.

Usage:
    python3 -m aletheia.data.validate_universe
    python3 -m aletheia.data.validate_universe --ticker MSFT AAPL
    python3 -m aletheia.data.validate_universe --fix  # triggers re-ingest for failed tickers

Called automatically by run_full_universe.sh after the pipeline loop.
"""

import json
import sys
import argparse
from pathlib import Path
from datetime import datetime

import numpy as np


REFERENCE_PATH = Path("config/universe_reference.json")
REPORT_DIR     = Path("valuation_data/serving/latest")
LOG_PATH       = Path("valuation_data/logs/universe_validation.jsonl")


# ─────────────────────────────────────────────────────────────────────────────
# Field mapping: reference JSON key → DuckDB column name
# ─────────────────────────────────────────────────────────────────────────────

FIELD_MAP = {
    "revenue_bn":      ("clean_Revenue",          1e9),
    "ebitda_bn":       ("derived_EBITDA",          1e9),
    "fcf_bn":          ("derived_FCF",             1e9),
    "gross_margin_pct":("derived_GrossMargin_Pct", 1.0),
    "ebit_margin_pct": ("derived_EBIT_Margin_Pct", 1.0),
    "net_income_bn":   ("raw_NetIncome",           1e9),
    "roe":             ("derived_ROE",             1.0),
}


# ─────────────────────────────────────────────────────────────────────────────
# Validator
# ─────────────────────────────────────────────────────────────────────────────

def validate_ticker(ticker: str, reference: dict) -> dict:
    """
    Cross-reference one ticker's DuckDB data against the reference file.
    Returns a result dict with pass/fail per field.
    """
    result = {
        "ticker":       ticker,
        "validated_at": datetime.now().isoformat(),
        "status":       "pass",
        "fields":       {},
        "failures":     [],
    }

    # Find the reference entry (latest FY in reference)
    ticker_ref = reference.get(ticker)
    if not ticker_ref:
        result["status"] = "no_reference"
        result["failures"].append("No reference data — add to universe_reference.json")
        return result

    # Find latest FY in reference
    fy_keys = [k for k in ticker_ref if k.startswith("FY")]
    if not fy_keys:
        result["status"] = "no_reference"
        return result

    ref_fy_str = sorted(fy_keys)[-1]   # e.g. "FY2025"
    ref_fy     = int(ref_fy_str[2:])
    ref_data   = ticker_ref[ref_fy_str]
    tolerance  = ref_data.get("tolerance_pct", 0.05)

    # Load from DuckDB
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker)
        db.close()

        if df.empty:
            result["status"] = "no_db_data"
            result["failures"].append(f"No DuckDB data for {ticker}")
            return result

        # Find matching fiscal year
        if ref_fy in df["fiscal_year"].values:
            row = df[df["fiscal_year"] == ref_fy].iloc[0]
        else:
            # Use latest available
            row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]
            actual_fy = int(df["fiscal_year"].max())
            if abs(actual_fy - ref_fy) > 1:
                result["failures"].append(
                    f"FY mismatch: reference FY{ref_fy} but DB has FY{actual_fy}"
                )

    except Exception as e:
        result["status"] = "db_error"
        result["failures"].append(f"DB read failed: {e}")
        return result

    def _safe(col):
        v = row.get(col)
        return float(v) if v is not None and not (
            isinstance(v, float) and np.isnan(v)) else None

    # Check each field
    any_fail = False
    for ref_key, (db_col, scale) in FIELD_MAP.items():
        ref_val = ref_data.get(ref_key)
        if ref_val is None:
            continue  # Field not in reference — skip

        db_val_raw = _safe(db_col)
        if db_val_raw is None:
            field_result = {
                "status":    "missing",
                "ref_value": ref_val,
                "db_value":  None,
                "delta_pct": None,
            }
            result["fields"][ref_key] = field_result
            result["failures"].append(
                f"{ref_key}: MISSING in DuckDB (expected {ref_val})"
            )
            any_fail = True
            continue

        # Convert DB value to same unit as reference
        db_val = db_val_raw / scale

        delta_pct = abs(db_val - ref_val) / abs(ref_val) if ref_val != 0 else 0
        passed = delta_pct <= tolerance

        field_result = {
            "status":    "pass" if passed else "fail",
            "ref_value": ref_val,
            "db_value":  round(db_val, 3),
            "delta_pct": round(delta_pct * 100, 2),
            "tolerance_pct": tolerance * 100,
        }
        result["fields"][ref_key] = field_result

        if not passed:
            direction = "over" if db_val > ref_val else "under"
            result["failures"].append(
                f"{ref_key}: model={db_val:.2f} ref={ref_val:.2f} "
                f"delta={delta_pct:.1%} ({direction}stated) "
                f"tolerance={tolerance:.0%}"
            )
            any_fail = True

    result["status"] = "fail" if any_fail else "pass"
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Cross-reference DuckDB data against universe_reference.json"
    )
    parser.add_argument("--ticker", nargs="+", help="Specific tickers to validate")
    parser.add_argument("--fix",    action="store_true",
                        help="Re-ingest failed tickers automatically")
    parser.add_argument("--json",   action="store_true",
                        help="Output results as JSON")
    args = parser.parse_args()

    if not REFERENCE_PATH.exists():
        print(f"❌ Reference file not found: {REFERENCE_PATH}")
        print("   Create config/universe_reference.json first.")
        sys.exit(1)

    reference = json.loads(REFERENCE_PATH.read_text())
    tickers = args.ticker or [
        t for t in reference if not t.startswith("_")
    ]

    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    results      = []
    passed       = []
    failed       = []
    no_reference = []

    print(f"\n{'═'*70}")
    print(f"UNIVERSE CROSS-REFERENCE VALIDATION — {len(tickers)} tickers")
    print(f"{'═'*70}\n")
    print(f"{'Ticker':>7} │ {'Status':>8} │ {'Revenue':>10} │ {'EBITDA':>10} │ "
          f"{'FCF':>8} │ {'Failures'}")
    print("─" * 90)

    for ticker in tickers:
        result = validate_ticker(ticker, reference)
        results.append(result)

        # Log result
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(result) + "\n")

        # Summary row
        rev_field   = result["fields"].get("revenue_bn", {})
        ebitda_field= result["fields"].get("ebitda_bn", {})
        fcf_field   = result["fields"].get("fcf_bn", {})

        def _field_str(f):
            if not f:
                return "  —   "
            if f["status"] == "pass":
                return f"✅{f['db_value']:>6.1f}"
            elif f["status"] == "missing":
                return "  ❌ N/A"
            else:
                return f"❌{f['db_value']:>6.1f}"

        status_icon = {"pass": "✅ PASS", "fail": "❌ FAIL",
                       "no_db_data": "⚠ NO DB",
                       "no_reference": "⚠ NO REF",
                       "db_error": "⛔ ERROR"}.get(result["status"], result["status"])

        fail_summary = (
            result["failures"][0][:50] + "..."
            if len(result["failures"]) > 0
            else ""
        )

        print(f"{ticker:>7} │ {status_icon:>8} │ {_field_str(rev_field):>10} │ "
              f"{_field_str(ebitda_field):>10} │ {_field_str(fcf_field):>8} │ "
              f"{fail_summary}")

        if result["status"] == "pass":
            passed.append(ticker)
        elif result["status"] == "no_reference":
            no_reference.append(ticker)
        else:
            failed.append(ticker)

    # Summary
    print(f"\n{'═'*70}")
    print(f"SUMMARY")
    print(f"{'═'*70}")
    print(f"  ✅ Pass:         {len(passed)} tickers — {passed}")
    print(f"  ❌ Fail:         {len(failed)} tickers — {failed}")
    print(f"  ⚠  No reference: {len(no_reference)} tickers — {no_reference}")
    print(f"\nFull log: {LOG_PATH}")

    if failed:
        print(f"\n{'─'*70}")
        print("FAILURE DETAILS")
        print(f"{'─'*70}")
        for r in results:
            if r["status"] == "fail":
                print(f"\n  {r['ticker']}:")
                for fail in r["failures"]:
                    print(f"    ❌ {fail}")
                print(f"  Fix: Re-ingest {r['ticker']} after checking tag_misses.jsonl")
                print(f"       python3 -m aletheia.data.ingest --ticker {r['ticker']} --verbose")

    # Re-ingest if --fix
    if args.fix and failed:
        print(f"\n{'─'*70}")
        print(f"AUTO-FIX: Re-ingesting {len(failed)} failed tickers")
        print(f"{'─'*70}")
        import subprocess
        for ticker in failed:
            print(f"\n  Re-ingesting {ticker}...")
            subprocess.run(
                ["python3", "-m", "aletheia.data.ingest", "--ticker", ticker],
                check=False
            )
        print(f"\n✅ Re-ingest complete. Run validate_universe again to confirm.")

    if args.json:
        print(json.dumps(results, indent=2))

    # Exit code — non-zero if any failures
    sys.exit(0 if not failed else 1)


if __name__ == "__main__":
    main()
