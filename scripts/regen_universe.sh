#!/usr/bin/env bash
# Regenerate all 40 universe reports under the new architecture.
#
# Sequence:
#   1. Pre-flight (API key, DB, ticker count)
#   2. Backup existing artifacts
#   3. Wipe serving/latest artifacts (keep DBs)
#   4. Recompute deterministic dashboard dims for all 40 tickers
#   5. Run pipeline sequentially per ticker (~2 min each, ~80 min total)
#   6. Post-run verification
#
# Failure handling: each ticker runs independently. If one fails, the
# loop continues; failures are listed at the end so you can re-run them.
#
# Cost: ~120 LLM calls (3 per ticker × 40). At Gemini Flash pricing,
# roughly $0.10-$0.30 total.

set -u
cd "$(dirname "$0")/.."

UNIVERSE="AAPL ABT ACN AMD AMZN ASML AXP BRK-B CAT CNC COST EMR GOOGL HD ITW JNJ JPM KO LLY LOW MCO MDT META MRK MSFT NEE NSC NVDA ORCL PEP PG QCOM SMCI TSLA TSM TXN UNH UNP V WMT"
TS=$(date +%Y%m%d_%H%M%S)
BACKUP_DIR="valuation_data/_backup_${TS}"
LOG="logs/regen_${TS}.log"
SUMMARY="logs/regen_${TS}_summary.txt"

mkdir -p logs

echo_step() { echo -e "\n\033[1;36m━━━ $* ━━━\033[0m" | tee -a "$LOG"; }
echo_ok()   { echo -e "\033[32m✓\033[0m $*" | tee -a "$LOG"; }
echo_warn() { echo -e "\033[33m⚠\033[0m $*" | tee -a "$LOG"; }
echo_err()  { echo -e "\033[31m✗\033[0m $*" | tee -a "$LOG"; }


# ──────────────────────────────────────────────────────────────────
# 1. Pre-flight
# ──────────────────────────────────────────────────────────────────
echo_step "1. Pre-flight"

# Load .env so GOOGLE_API_KEY is in this shell (and inherited by every
# subprocess we spawn). main.py doesn't auto-load .env on its own.
if [ -f .env ]; then
    set -a; . ./.env; set +a
fi

if [ -z "${GOOGLE_API_KEY:-}" ]; then
    echo_err "GOOGLE_API_KEY not set. Aborting — every pipeline run would fall back to mock and overwrite real data."
    exit 1
fi
echo_ok "GOOGLE_API_KEY: ${GOOGLE_API_KEY:0:8}... (${#GOOGLE_API_KEY} chars)"

DB_TICKERS=$(PYTHONPATH=. python3 -c "
from aletheia.data.database import InvestmentDatabase
db = InvestmentDatabase(verbose=False)
n = db._conn.execute('SELECT COUNT(DISTINCT ticker) FROM company_records_latest').fetchone()[0]
db.close()
print(n)
")
echo_ok "company_records_latest covers $DB_TICKERS distinct tickers"

UNIVERSE_COUNT=$(echo $UNIVERSE | wc -w | tr -d ' ')
echo_ok "Regen target: $UNIVERSE_COUNT tickers"

# Make sure no other pipeline is running
if pgrep -f "main.py --ticker" > /dev/null; then
    echo_err "Another main.py --ticker process is running. Aborting to avoid DuckDB write collision."
    pgrep -fl "main.py --ticker"
    exit 1
fi
echo_ok "No competing pipeline runs"


# ──────────────────────────────────────────────────────────────────
# 2. Backup
# ──────────────────────────────────────────────────────────────────
echo_step "2. Backup → $BACKUP_DIR"

mkdir -p "$BACKUP_DIR"
cp -R valuation_data/serving/latest "$BACKUP_DIR/serving_latest_pre_regen" 2>/dev/null || echo_warn "no serving/latest to back up"
cp -R valuation_data/theses          "$BACKUP_DIR/theses_pre_regen" 2>/dev/null || echo_warn "no theses to back up"
cp valuation_data/database/investment.duckdb "$BACKUP_DIR/investment.duckdb.snapshot"
echo_ok "Backup complete (size: $(du -sh "$BACKUP_DIR" | cut -f1))"


# ──────────────────────────────────────────────────────────────────
# 3. Wipe serving artifacts (keep DBs, keep memo PDFs as archive)
# ──────────────────────────────────────────────────────────────────
echo_step "3. Wipe serving/latest artifacts"

rm -f valuation_data/serving/latest/*_report.json
rm -f valuation_data/serving/latest/*_Executive_Report.html
rm -f valuation_data/serving/latest/*_Executive_Report.md
rm -f valuation_data/serving/latest/*_Detailed_Report.md
rm -f valuation_data/serving/latest/*_DCF_Model.xlsx
remaining=$(ls valuation_data/serving/latest/ 2>/dev/null | wc -l | tr -d ' ')
echo_ok "Wiped (remaining files in serving/latest: $remaining)"


# ──────────────────────────────────────────────────────────────────
# 4. Recompute deterministic dashboard dims for all 40 tickers
# ──────────────────────────────────────────────────────────────────
echo_step "4. Recompute deterministic dashboard dims for $UNIVERSE_COUNT tickers"

PYTHONPATH=. python3 - <<PYEOF | tee -a "$LOG"
from aletheia.data.database import InvestmentDatabase
from aletheia.qualitative.runner import recompute_deterministic

tickers = "$UNIVERSE".split()
db = InvestmentDatabase(verbose=False)
totals = {"written": 0, "unchanged": 0, "no_data": 0, "err": 0}
for t in tickers:
    try:
        results = recompute_deterministic(t, db=db)
        counts = {"written": 0, "unchanged": 0, "no_data": 0}
        for r in results:
            counts[r["status"]] = counts.get(r["status"], 0) + 1
        for k, v in counts.items():
            totals[k] += v
        flag = "✓" if counts["no_data"] == 0 else "⚠"
        print(f"  {flag} {t:6} written={counts['written']} unchanged={counts['unchanged']} no_data={counts['no_data']}")
    except Exception as exc:
        totals["err"] += 1
        print(f"  ✗ {t:6} EXCEPTION: {exc}")
db.close()
print(f"\nDeterministic recompute summary: written={totals['written']} unchanged={totals['unchanged']} no_data={totals['no_data']} errors={totals['err']}")
PYEOF


# ──────────────────────────────────────────────────────────────────
# 5. Mass-run pipeline (sequential — DuckDB is single-writer)
# ──────────────────────────────────────────────────────────────────
echo_step "5. Pipeline mass-run for $UNIVERSE_COUNT tickers (~80 minutes)"

START_TS=$(date +%s)
SUCCEEDED=()
FAILED=()
for T in $UNIVERSE; do
    T_START=$(date +%s)
    echo -e "\n\033[1;33m▶ $T\033[0m  (started $(date +%H:%M:%S))" | tee -a "$LOG"
    if PYTHONPATH=. python3 main.py --ticker "$T" >> "$LOG" 2>&1; then
        if [ -f "valuation_data/serving/latest/${T}_report.json" ]; then
            T_END=$(date +%s)
            T_ELAPSED=$((T_END - T_START))
            SUCCEEDED+=("$T")
            echo_ok "$T done in ${T_ELAPSED}s"
        else
            FAILED+=("$T (no JSON written)")
            echo_err "$T pipeline returned 0 but no JSON produced"
        fi
    else
        FAILED+=("$T (exit non-zero)")
        echo_err "$T pipeline failed (see $LOG for details)"
    fi
done

TOTAL_ELAPSED=$(($(date +%s) - START_TS))
TOTAL_MIN=$((TOTAL_ELAPSED / 60))
echo -e "\n━━━ Pipeline mass-run complete in ${TOTAL_MIN}m ━━━" | tee -a "$LOG"
echo_ok "Succeeded: ${#SUCCEEDED[@]} / $UNIVERSE_COUNT"
if [ ${#FAILED[@]} -gt 0 ]; then
    echo_err "Failed:    ${#FAILED[@]}"
    for f in "${FAILED[@]}"; do echo "    $f" | tee -a "$LOG"; done
fi


# ──────────────────────────────────────────────────────────────────
# 6. Post-run verification
# ──────────────────────────────────────────────────────────────────
echo_step "6. Verification"

PYTHONPATH=. python3 - <<PYEOF | tee -a "$SUMMARY"
import json
from pathlib import Path
import duckdb
import subprocess

UNIVERSE = "$UNIVERSE".split()
SERVING = Path("valuation_data/serving/latest")

# Companion files
def has(t, ext): return (SERVING / f"{t}_{ext}").exists()
report_present = [t for t in UNIVERSE if has(t, "report.json")]
print(f"\n[reports]   {len(report_present)} / {len(UNIVERSE)} serving JSONs")
missing = sorted(set(UNIVERSE) - set(report_present))
if missing:
    print(f"            missing: {missing}")
for ext in ("Executive_Report.html", "Detailed_Report.md", "DCF_Model.xlsx"):
    n = sum(1 for t in UNIVERSE if has(t, ext))
    flag = "✓" if n == len(UNIVERSE) else "✗"
    print(f"  {flag} {ext}: {n}/{len(UNIVERSE)}")

# Thesis quality
mock = []; no_thesis = []; no_fp = []; coverage_distribution = {}
confidence_distribution = {}
for t in report_present:
    r = json.loads((SERVING / f"{t}_report.json").read_text())
    ts = (r.get("4_valuation_synthesis") or {}).get("thesis_synthesis") or {}
    if not ts.get("thesis_statement"):
        no_thesis.append(t); continue
    flags = ts.get("_quality_flags") or []
    if "mock_fallback" in flags: mock.append(t)
    md = ts.get("_metadata") or {}
    if not md.get("dashboard_state_fingerprint"): no_fp.append(t)
    cov = md.get("coverage_state","?")
    coverage_distribution[cov] = coverage_distribution.get(cov, 0) + 1
    conf = ts.get("thesis_confidence","?")
    confidence_distribution[conf] = confidence_distribution.get(conf, 0) + 1

print(f"\n[thesis]    structured thesis present:   {len(report_present)-len(no_thesis)} / {len(report_present)}")
print(f"            fingerprint stamped:         {len(report_present)-len(no_thesis)-len(no_fp)} / {len(report_present)-len(no_thesis)}")
print(f"            mock_fallback (bad):         {len(mock)}")
if mock: print(f"              → {mock}")
if no_thesis: print(f"            no thesis_synthesis: {no_thesis}")

print(f"\n[coverage]  {dict(sorted(coverage_distribution.items()))}")
print(f"[confidence] {dict(sorted(confidence_distribution.items()))}")

# IPS/MoS populated (post-fix from commit 6375311)
no_ips = []
for t in report_present:
    r = json.loads((SERVING / f"{t}_report.json").read_text())
    base = (r.get("4_valuation_synthesis") or {}).get("phase2_valuation", {}).get("three_scenario_dcf", {}).get("base", {})
    if base.get("intrinsic_per_share") is None: no_ips.append(t)
print(f"\n[dcf]       base IPS populated: {len(report_present)-len(no_ips)} / {len(report_present)}")
if no_ips: print(f"            base IPS = None: {no_ips}")

# agent_runs row per ticker
db = duckdb.connect("valuation_data/database/investment.duckdb", read_only=True)
ar_tickers = {r[0] for r in db.execute("SELECT ticker FROM agent_runs_latest").fetchall()}
expected_sha = subprocess.check_output(["git","rev-parse","HEAD"]).decode().strip()
ar_present = [t for t in report_present if t in ar_tickers]
print(f"\n[agent_runs] rows present: {len(ar_present)} / {len(report_present)}")
ar_sha_drift = [(r[0], r[1]) for r in db.execute(
    "SELECT ticker, git_sha FROM agent_runs_latest WHERE ticker = ANY(?)",
    [list(report_present)]
).fetchall() if r[1] != expected_sha]
if ar_sha_drift:
    print(f"            git_sha drift (expected {expected_sha[:8]}): {ar_sha_drift}")

# Negative-equity tickers — ROE should be None
neg_eq_check = []
for t in report_present:
    r = json.loads((SERVING / f"{t}_report.json").read_text())
    ratios = r.get("2_financial_translation", {}).get("ratios", {})
    if ratios.get("roe_note"):
        neg_eq_check.append((t, ratios.get("roe"), ratios.get("roe_note")[:40]))
print(f"\n[neg-equity] tickers with ROE suppressed: {len(neg_eq_check)}")
for t, roe, note in neg_eq_check:
    print(f"            {t:6} roe={roe} ({note}…)")

db.close()

# ── Gate F — FMP validation aggregate (universe-level pass/fail) ────────
print("\n━━━ Gate F — FMP validation aggregate ━━━")
ing_status_counts = {"validated":0, "drift":0, "blocking_drift":0, "skipped":0, "not_run":0, "missing":0}
calc_status_counts = {"validated":0, "drift":0, "blocking_drift":0, "skipped":0, "not_run":0, "missing":0}
final_status_counts = {"validated":0, "drift":0, "blocking_drift":0, "skipped":0, "missing":0}
ingestion_blocking_tickers = []
calc_blocking_tickers = []
final_blocking_tickers = []
ing_skipped_reasons = {}
calc_skipped_reasons = {}

for t in report_present:
    r = json.loads((SERVING / f"{t}_report.json").read_text())
    v = r.get("_validation") or {}
    ing = v.get("ingestion") or {}
    calc = v.get("calc") or {}
    fa  = v.get("final_assembly") or {}

    ing_s = ing.get("status", "missing")
    ing_status_counts[ing_s] = ing_status_counts.get(ing_s, 0) + 1
    if ing_s == "blocking_drift":
        ingestion_blocking_tickers.append((t, ing.get("blocking_fields", [])))
    if ing_s == "skipped":
        ing_skipped_reasons[ing.get("skip_reason", "?")] = ing_skipped_reasons.get(ing.get("skip_reason", "?"), 0) + 1

    calc_s = calc.get("status", "missing")
    calc_status_counts[calc_s] = calc_status_counts.get(calc_s, 0) + 1
    if calc_s == "blocking_drift":
        calc_blocking_tickers.append((t, calc.get("blocking_fields", [])))
    if calc_s == "skipped":
        calc_skipped_reasons[calc.get("skip_reason", "?")] = calc_skipped_reasons.get(calc.get("skip_reason", "?"), 0) + 1

    fa_s = fa.get("status", "missing")
    final_status_counts[fa_s] = final_status_counts.get(fa_s, 0) + 1
    if fa_s == "blocking_drift":
        final_blocking_tickers.append((t, fa.get("checks", {}).get("numeric_fidelity", {}).get("details", [])))

print(f"  [ingestion]      {dict(sorted(ing_status_counts.items()))}")
if ing_skipped_reasons:
    print(f"                   skipped reasons: {ing_skipped_reasons}")
print(f"  [calc]           {dict(sorted(calc_status_counts.items()))}")
if calc_skipped_reasons:
    print(f"                   skipped reasons: {calc_skipped_reasons}")
print(f"  [final_assembly] {dict(sorted(final_status_counts.items()))}")

# Failure thresholds (locked):
#   any ingestion blocking_drift     → FAIL  (Gate A bypass — should be 0)
#   >25% of tickers calc blocking    → FAIL
#   >10% of tickers final blocking   → WARN
n_total = len(report_present) or 1
gate_f_status = "PASS"
warnings = []

if ingestion_blocking_tickers:
    gate_f_status = "FAIL"
    print(f"\n  ❌ INGESTION BLOCKING (Gate A bypass — investigate):")
    for t, fields in ingestion_blocking_tickers:
        print(f"     {t}: {fields}")

if calc_blocking_tickers:
    pct = len(calc_blocking_tickers) / n_total
    if pct > 0.25:
        gate_f_status = "FAIL"
        print(f"\n  ❌ CALC BLOCKING ({len(calc_blocking_tickers)}/{n_total} = {pct:.0%}, threshold 25%):")
    else:
        warnings.append(f"calc blocking on {len(calc_blocking_tickers)} ticker(s) (under 25% threshold)")
        print(f"\n  ⚠ calc blocking on {len(calc_blocking_tickers)} tickers (under 25% threshold):")
    for t, fields in calc_blocking_tickers[:10]:
        print(f"     {t}: {fields}")

if final_blocking_tickers:
    pct = len(final_blocking_tickers) / n_total
    if pct > 0.10:
        warnings.append(f"final-assembly fidelity violations on {len(final_blocking_tickers)} tickers (over 10%)")
        print(f"\n  ⚠ FINAL-ASSEMBLY VIOLATIONS ({len(final_blocking_tickers)}/{n_total} = {pct:.0%}):")
    else:
        print(f"\n  ⚠ final-assembly violations on {len(final_blocking_tickers)} tickers (under 10% threshold):")
    for t, details in final_blocking_tickers[:10]:
        print(f"     {t}: {details[:2]}")

# Summary JSON for machine consumption
summary_out = SERVING / "_validation_summary.json"
summary_out.write_text(json.dumps({
    "tickers_total":       len(UNIVERSE),
    "reports_present":     len(report_present),
    "ingestion":           ing_status_counts,
    "calc":                calc_status_counts,
    "final_assembly":      final_status_counts,
    "result":              gate_f_status,
    "warnings":            warnings,
    "ingestion_blocking":  [{"ticker": t, "fields": f} for t, f in ingestion_blocking_tickers],
    "calc_blocking":       [{"ticker": t, "fields": f} for t, f in calc_blocking_tickers],
    "final_blocking":      [{"ticker": t, "details": d[:3]} for t, d in final_blocking_tickers],
}, indent=2, default=str))

if gate_f_status == "PASS" and not warnings:
    print(f"\n  ✅ Gate F: PASS (all gates clean across {len(report_present)} tickers)")
elif gate_f_status == "PASS":
    print(f"\n  ⚠ Gate F: PASS with warnings: {warnings}")
else:
    print(f"\n  ❌ Gate F: FAIL — see blocking lists above")
print(f"  Summary written to {summary_out}")

print("\n━━━ verification done ━━━")
PYEOF

# Exit non-zero on Gate F FAIL so the calling shell knows
if grep -q '"result": "FAIL"' valuation_data/serving/latest/_validation_summary.json 2>/dev/null; then
    echo_err "Gate F FAILED — see logs/regen_${TS}_summary.txt"
    exit 1
fi

# ──────────────────────────────────────────────────────────────────
# 6b. Gate F — universe validation-drift aggregation (fix-plan 3.1)
#     The real aggregate_universe drift gate from fmp_validation.py's
#     contract: reads every fresh report's _validation block and applies
#     the tier-filtered threshold matrix (only strict/standard blocking
#     fields can FAIL). Distinct from the completeness check above.
#     WARN-only during rollout — --report-only records the verdict +
#     writes audits/gate_f_<date>.json but never aborts. Drop the flag
#     to enforce once thresholds are locked on a fresh regen (3.1.2).
# ──────────────────────────────────────────────────────────────────
echo_step "6b. Gate F — validation drift (aggregate_universe)"
PYTHONPATH=. python3 scripts/gate_f.py --report-only 2>&1 | tee -a "$SUMMARY" || true

echo -e "\n\033[1;32mAll done.\033[0m Logs: $LOG  ·  Summary: $SUMMARY"
echo "Next: review the summary, then check Streamlit Reports tab for any ticker."
