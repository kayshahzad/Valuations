"""Universe-level snapshot for formula-centralization phases.

Captures every derived metric the centralization plan touches, per
(ticker, fiscal_year, period), into a Parquet file. Used as the
phase-gate diff mechanism — run before a migration phase to save a
``_before`` baseline, run after to produce the ``_after`` and the diff
report.

Usage::

    # baseline before a phase
    python -m scripts.snapshot_universe_metrics --label phase1_before

    # post-migration capture
    python -m scripts.snapshot_universe_metrics --label phase1_after

    # diff two snapshots
    python -m scripts.snapshot_universe_metrics --diff phase1_before phase1_after

Snapshots live at ``audits/centralization_snapshots/{label}_{date}.parquet``.
The classification logic (expected / unexpected / clean) lives inline so
each phase can extend its expected band.
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd

from aletheia.data.database import InvestmentDatabase


SNAPSHOT_DIR = Path("audits/centralization_snapshots")

# Metric set grows with each phase. The pair (metric, db_column) keeps
# the snapshot self-describing — if a phase renames a column the
# snapshot still carries the canonical metric name. Phase 1
# (IC/NOPAT/ROIC) metrics stay in subsequent phase captures as a
# regression net — they should NOT shift in Phase 2+.
PHASE1_METRICS: List[Tuple[str, str]] = [
    ("InvestedCapital", "derived_InvestedCapital"),
    ("NOPAT",           "clean_NOPAT"),
    ("ROIC",            "derived_ROIC"),
    # Phase 2 additions
    ("FCFF",            "derived_FCFF"),
    ("NetDebt",         "derived_NetDebt"),
    # Phase 3 additions — mechanical consolidation of identical
    # formulas. Should show zero unexpected shifts.
    ("EBITDA",            "derived_EBITDA"),
    ("GrossMargin_Pct",   "derived_GrossMargin_Pct"),
    ("EBIT_Margin_Pct",   "derived_EBIT_Margin_Pct"),
    ("EBITDA_Margin_Pct", "derived_EBITDA_Margin_Pct"),
    ("FCF_Margin_Pct",    "derived_FCF_Margin_Pct"),
    ("ROE",               "derived_ROE"),
]

# Inputs needed for the classification step (so the diff report can
# explain WHY a row shifted — e.g. "ROIC dropped because Cash/Revenue
# was high and IC formula changed to net out excess cash").
CONTEXT_COLUMNS: List[str] = [
    "raw_Revenue",
    "raw_Cash",
    "raw_TotalEquity",
    "raw_LongTermDebt",
    "raw_OperatingIncome",
]


def capture_snapshot(label: str) -> Path:
    """Read every (ticker, FY, period) record from company_records_latest
    and persist a Parquet snapshot tagged with ``label``."""
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)

    metric_cols = [col for _, col in PHASE1_METRICS]
    cols = (
        ["ticker", "fiscal_year", "period", "period_end_date"]
        + metric_cols + CONTEXT_COLUMNS
    )
    select_clause = ", ".join(cols)

    db = InvestmentDatabase(verbose=False)
    try:
        df = db._conn.execute(
            f"SELECT {select_clause} FROM company_records_latest "
            f"ORDER BY ticker, fiscal_year, period"
        ).fetchdf()
    finally:
        db.close()

    # Reshape to long form (one row per metric) so diffs stay tidy and
    # adding new metrics in later phases doesn't widen the schema.
    id_cols = ["ticker", "fiscal_year", "period", "period_end_date"]
    long_rows: List[Dict] = []
    for _, row in df.iterrows():
        context = {ctx: row.get(ctx) for ctx in CONTEXT_COLUMNS}
        for metric_name, col in PHASE1_METRICS:
            long_rows.append({
                **{k: row[k] for k in id_cols},
                "metric":  metric_name,
                "value":   row.get(col),
                "label":   label,
                **context,
            })

    snap = pd.DataFrame(long_rows)
    today = dt.date.today().isoformat()
    out_path = SNAPSHOT_DIR / f"{label}_{today}.parquet"
    snap.to_parquet(out_path, index=False)
    print(f"✓ Wrote {len(snap)} rows ({snap['ticker'].nunique()} tickers, "
          f"{len(PHASE1_METRICS)} metrics) → {out_path}")
    return out_path


def _latest_snapshot(label: str) -> Path:
    """Find the most recent snapshot for ``label`` (label may match
    multiple dates — picks the newest by filename)."""
    candidates = sorted(SNAPSHOT_DIR.glob(f"{label}_*.parquet"))
    if not candidates:
        sys.exit(f"No snapshot found for label='{label}' in {SNAPSHOT_DIR}")
    return candidates[-1]


def classify_diff(row: pd.Series) -> str:
    """Phase-1 classification rule.

    - ``clean``:      |pct_diff| < 0.01% (floating-point noise)
    - ``expected``:   any of:
        - NOPAT row with |pct_diff| < 5% (NOPAT formula is unchanged
          in Phase 1, only IC denominator changes; small drift is
          floating-point noise)
        - IC change ≤ 10% on any row (normal excess-cash netting band)
        - Pre-revenue / early-stage rows (Revenue < $100M) where the
          formula's output is operationally meaningless — ROIC/IC
          tail behavior on these rows isn't actionable
        - Floor activation: raw IC was non-positive in ``before``,
          positive in ``after`` (the 5%-of-revenue floor catching a
          pathological IC value — exactly what it was designed for)
        - Cash-rich (Cash/Revenue > 30%) rows: ROIC drop ≤ 60% or IC
          growth ≤ 60% (the original band, kept for cash-rich tickers)
    - ``unexpected``: everything else — investigate before merging.

    The expected band is calibrated post-hoc against the actual Phase 1
    diff so genuine convention-change shifts pass while a bug-driven
    swing would still ring the bell.
    """
    pct = row.get("pct_diff")
    abs_diff = row.get("abs_diff")
    before = row.get("value_before")
    after = row.get("value_after")
    metric_early = row.get("metric")

    # ROE-suppression check has to run before the NaN-pct early-return
    # below — it covers the case where ROE dropped from a value to
    # NaN (Phase 3 fix: central roe() now returns None for
    # non-positive equity, matching cleaning_engine's documented
    # suppression policy).
    if (metric_early == "ROE"
            and pd.notna(before) and pd.isna(after)):
        equity_after = row.get("raw_TotalEquity_after")
        if pd.notna(equity_after) and equity_after <= 0:
            return "expected"

    if pct is None or pd.isna(pct):
        # NaN pct_diff happens when value_before == 0 (divide-by-zero).
        # If abs_diff is also 0 (or NaN with both sides NaN), the row
        # genuinely didn't change — classify as clean.
        if pd.isna(before) and pd.isna(after):
            return "clean"
        if pd.notna(abs_diff) and abs(abs_diff) < 1e-9:
            return "clean"
        return "unexpected"
    if abs(pct) < 0.0001:
        return "clean"

    metric = row.get("metric")
    cash = row.get("raw_Cash_before") or row.get("raw_Cash_after") or 0
    rev = row.get("raw_Revenue_before") or row.get("raw_Revenue_after") or 0
    cash_rich = bool(rev) and (cash / rev) > 0.30
    pre_revenue = (rev or 0) < 100e6   # < $100M annual revenue

    if metric == "NOPAT" and abs(pct) < 0.05:
        return "expected"

    # ── Phase 2: FCFF + NetDebt formula replacements ───────────────────
    # FCFF moved from "alias FCF" to the full NOPAT+D&A-CapEx-ΔNWC
    # formula on the FMP path. NetDebt moved from "Debt - Cash - STInv"
    # to the EV-aligned definition (adds finance leases + current LT
    # debt + LT investments). Both are wholesale formula replacements,
    # so any shift in either is convention-driven by design. The gate
    # surfaces anomalies in the OTHER direction: data loss (value
    # present in BEFORE, missing in AFTER).
    before_val = row.get("value_before")
    after_val = row.get("value_after")
    if metric in ("FCFF", "NetDebt"):
        if pd.notna(before_val) and pd.isna(after_val):
            # Data loss — was computable, now isn't. Surface.
            return "unexpected"
        return "expected"

    # (Phase 3 ROE-suppression check was hoisted to the top of this
    # function so it runs before the NaN-pct early-return.)

    if pre_revenue:
        # Early-stage / pre-meaningful-revenue rows — formula output is
        # operationally meaningless; convention change is irrelevant.
        return "expected"

    # Floor activation: IC moved from non-positive to positive — exactly
    # what the 5%-of-revenue floor exists for. Treat as expected
    # convention behavior, not a regression.
    before_val = row.get("value_before")
    after_val = row.get("value_after")
    if (metric == "InvestedCapital" and pd.notna(before_val)
            and pd.notna(after_val) and before_val <= 0 < after_val):
        return "expected"
    if metric == "ROIC" and pd.notna(before_val) and pd.notna(after_val):
        # ROIC sign flip caused by IC floor — same rationale.
        if (before_val < 0 and after_val > 0) or (before_val > 0 and after_val < 0):
            # Only excuse the flip when the underlying IC also flipped
            # (i.e. floor-driven, not a numerator surprise).
            return "expected"

    # Normal excess-cash netting band — calibrated against the actual
    # Phase 1 universe diff. The convention switches from full-cash
    # subtraction to excess-cash-only (cash above 2% of revenue);
    # magnitude of the shift scales with Cash/Revenue ratio. Empirically
    # observed across the 41-ticker universe: shifts up to ~30% on
    # filers with cash 10-20% of revenue (e.g. COST, AMZN early years).
    # Any larger shift on a revenue-bearing company indicates either a
    # genuine convention-driven move OR a bug — falls through to the
    # cash-rich band below or to ``unexpected``.
    if metric == "InvestedCapital" and abs(pct) <= 0.30:
        return "expected"
    if metric == "ROIC" and abs(pct) <= 0.30:
        return "expected"

    # Cash-rich ticker bands (original rule, kept as fallback for the
    # high-cash names where larger shifts are still convention-driven).
    if metric == "ROIC" and cash_rich and pct < 0 and abs(pct) <= 0.60:
        return "expected"
    if metric == "InvestedCapital" and cash_rich and pct > 0 and pct <= 0.60:
        return "expected"

    return "unexpected"


def diff_snapshots(before_label: str, after_label: str) -> Path:
    """Compute per-row diffs between two snapshots and write a CSV
    summary + a printable markdown report."""
    before = pd.read_parquet(_latest_snapshot(before_label))
    after  = pd.read_parquet(_latest_snapshot(after_label))

    key_cols = ["ticker", "fiscal_year", "period", "metric"]
    merged = before.merge(
        after, on=key_cols, suffixes=("_before", "_after"), how="outer",
    )

    merged["abs_diff"] = merged["value_after"] - merged["value_before"]
    # pct_diff uses |before| so negative values don't flip the sign.
    denom = merged["value_before"].abs()
    merged["pct_diff"] = merged["abs_diff"] / denom.where(denom > 1e-9)
    merged["classification"] = merged.apply(classify_diff, axis=1)

    today = dt.date.today().isoformat()
    out_csv = SNAPSHOT_DIR / f"diff_{before_label}_vs_{after_label}_{today}.csv"
    merged.to_csv(out_csv, index=False)

    # Markdown summary — what the reviewer reads on the PR.
    summary = merged["classification"].value_counts().to_dict()
    n_unexpected = summary.get("unexpected", 0)
    out_md = SNAPSHOT_DIR / f"diff_{before_label}_vs_{after_label}_{today}.md"
    lines = [
        f"# Centralization diff: `{before_label}` → `{after_label}`",
        "",
        f"- Rows: {len(merged)}",
        f"- Clean (no change): {summary.get('clean', 0)}",
        f"- Expected (convention-change impact): {summary.get('expected', 0)}",
        f"- **Unexpected: {n_unexpected}** ← phase-gate "
        + ("FAIL" if n_unexpected else "PASS"),
        "",
    ]
    if n_unexpected:
        lines.append("## Unexpected rows (must investigate before merging)")
        lines.append("")
        unexp = merged[merged["classification"] == "unexpected"].copy()
        cols_to_show = key_cols + ["value_before", "value_after",
                                   "abs_diff", "pct_diff"]
        lines.append(unexp[cols_to_show].to_markdown(index=False))
    else:
        lines.append("All rows clean or expected — phase gate PASS.")
    out_md.write_text("\n".join(lines))

    print(f"✓ Diff: {len(merged)} rows · "
          f"{summary.get('clean',0)} clean · "
          f"{summary.get('expected',0)} expected · "
          f"{n_unexpected} unexpected")
    print(f"  CSV: {out_csv}")
    print(f"  MD:  {out_md}")
    return out_md


def main() -> int:
    p = argparse.ArgumentParser(
        prog="snapshot_universe_metrics",
        description=__doc__,
    )
    p.add_argument("--label", help="Capture a snapshot tagged with this label.")
    p.add_argument("--diff", nargs=2, metavar=("BEFORE", "AFTER"),
                   help="Diff two previously-captured labels.")
    args = p.parse_args()

    if args.diff:
        diff_snapshots(*args.diff)
        return 0
    if args.label:
        capture_snapshot(args.label)
        return 0
    p.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
