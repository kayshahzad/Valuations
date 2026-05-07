"""
scratch/run_extended_backtest.py

Extended backtest: calibrates all 7 signals (upside_pct, conviction,
roic_wacc_spread, ev_ebitda_gap_pct, moat_fingerprint, rdcf_growth_gap,
beneish_m_score) plus the momentum baseline against forward returns at
6m, 12m, and 24m horizons. Produces per-signal calibration tables, a
multi-signal spread comparison, a correlation matrix, and an updated
findings doc.

AAPL is excluded until the cumulative-split-adjustment fix lands in the
cleaning engine (see initial_findings.md §6 for context). Restricted
subset MSFT/LLY/COST per spec.

Usage:
    PYTHONPATH=. python3 scratch/run_extended_backtest.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from aletheia.backtest import (
    multi_signal_comparison,
    run_backtest,
    signal_correlation_matrix,
    calibrate_signal,
    fundamental_vs_momentum_comparison,
    staleness_calibration,
)


OUT_DIR = Path("valuation_data/backtest")
TABLES_DIR = OUT_DIR / "calibration_tables"

# All numeric signals to calibrate. Beneish is added separately (binary).
NUMERIC_SIGNALS = [
    "upside_pct",
    "conviction_score",
    "roic_wacc_spread",
    "ev_ebitda_gap_pct",
    "moat_fingerprint_score",
    "rdcf_growth_gap",
    "beneish_m_score",
    "price_momentum_180d",
]


def main():
    # AAPL re-included after split-adjustment fix.
    tickers = ["AAPL", "MSFT", "LLY", "COST"]

    print(f"\n{'='*72}\n  Aletheia Extended Backtest — multi-signal calibration\n{'='*72}")
    print(f"  Tickers : {tickers}  (AAPL re-enabled, split-adjustment applied)")
    print(f"  Range   : 2020-01-01 → 2024-01-01 (quarterly)")
    print(f"  Horizons: 6m, 12m, 24m\n")

    results = run_backtest(
        tickers=tickers,
        start_date=date(2020, 1, 1),
        end_date=date(2024, 1, 1),
        horizons_months=[6, 12, 24],
        output_path=str(OUT_DIR / "results_v3.parquet"),
        verbose=True,
    )

    if results.empty:
        print("No signals generated; aborting.")
        return

    TABLES_DIR.mkdir(parents=True, exist_ok=True)

    # 1. Per-signal calibration tables
    cal_files = {
        "upside_pct": "upside_pct_calibration.csv",
        "conviction_score": "conviction_calibration.csv",
        "roic_wacc_spread": "roic_wacc_spread_calibration.csv",
        "ev_ebitda_gap_pct": "ev_ebitda_gap_calibration.csv",
        "moat_fingerprint_score": "moat_fingerprint_calibration.csv",
        "rdcf_growth_gap": "rdcf_growth_gap_calibration.csv",
        "price_momentum_180d": "momentum_calibration.csv",
    }
    for sig_col, fname in cal_files.items():
        cal = calibrate_signal(results, sig_col, return_column="return_12m")
        cal.to_csv(TABLES_DIR / fname, index=False)

    # Beneish — binary flag bucketing
    if "beneish_flagged" in results.columns and results["beneish_flagged"].notna().any():
        beneish_cal = calibrate_signal(
            results, "beneish_flagged", return_column="return_12m",
            bucket_method="binary",
        )
        beneish_cal.to_csv(TABLES_DIR / "beneish_flag_calibration.csv", index=False)
    else:
        print("  [note] beneish_flagged is empty — skipping that calibration table")

    # 2. Multi-signal spread comparison vs momentum
    spread_table = multi_signal_comparison(
        results,
        signal_columns=[c for c in NUMERIC_SIGNALS if c != "price_momentum_180d"],
        return_column="return_12m",
    )
    spread_table.to_csv(TABLES_DIR / "multi_signal_summary.csv", index=False)

    # 3. Correlation matrix
    corr = signal_correlation_matrix(results, NUMERIC_SIGNALS)
    corr.to_csv(TABLES_DIR / "signal_correlation_matrix.csv")

    # 4. Staleness (already had this)
    staleness = staleness_calibration(results, return_column="return_12m")
    staleness.to_csv(TABLES_DIR / "staleness_calibration.csv", index=False)

    # 5. Same diagnostic from initial backtest, for continuity
    fvm = fundamental_vs_momentum_comparison(results, return_column="return_12m")
    fvm.to_csv(TABLES_DIR / "fundamental_vs_momentum.csv", index=False)

    # ── Print summary ────────────────────────────────────────────────────────
    print(f"\n{'='*72}\n  CALIBRATION RESULTS\n{'='*72}\n")
    print("=== MULTI-SIGNAL SPREAD COMPARISON (12m) ===")
    print("(spread_vs_momentum > 0 = signal beats baseline)\n")
    if not spread_table.empty:
        for _, row in spread_table.iterrows():
            sig = row["signal_name"]
            top = row.get("top_bucket_return")
            bot = row.get("bottom_bucket_return")
            spread = row.get("spread")
            xs = row.get("spread_vs_momentum")
            n = row.get("n_observations")
            top_s = f"{top*100:+.1f}%" if pd.notna(top) else "    N/A"
            bot_s = f"{bot*100:+.1f}%" if pd.notna(bot) else "    N/A"
            sp_s = f"{spread*100:+.1f}%" if pd.notna(spread) else "    N/A"
            xs_s = f"{xs*100:+.1f}%" if pd.notna(xs) else "    N/A"
            print(f"  {sig:<25}  top={top_s:>7}  bot={bot_s:>7}  "
                  f"spread={sp_s:>7}  vs_mom={xs_s:>7}  n={n}")

    print("\n=== CORRELATION MATRIX ===")
    if not corr.empty:
        print(corr.round(2).to_string())

    print("\n=== STALENESS CALIBRATION ===")
    print(staleness.to_string(index=False))

    _write_extended_findings(results, spread_table, corr, staleness, fvm)
    print(f"\nFindings: {OUT_DIR / 'extended_findings.md'}")


def _write_extended_findings(results, spread_table, corr, staleness, fvm):
    lines = []
    lines.append("# Backtest — Extended Findings (Multi-Signal Calibration)\n")
    lines.append(f"**Generated:** {date.today().isoformat()}  ")
    lines.append(f"**Sample:** {len(results)} signal observations  ")
    lines.append(f"**Tickers:** {', '.join(sorted(results['ticker'].unique()))} "
                 f"(AAPL excluded — pre-split share-count contamination)  ")
    lines.append(f"**Date range:** {results['as_of_date'].min()} → {results['as_of_date'].max()}  ")
    lines.append(f"**Horizons:** 6m, 12m, 24m (24m available for all observations as of {date.today()})\n")

    lines.append("## 1. Which signals beat momentum?\n")
    if not spread_table.empty:
        lines.append("Ranked by `spread_vs_momentum` (signal top-bottom spread minus the "
                     "momentum baseline's top-bottom spread). Positive = the signal carries "
                     "information beyond price trend.\n")
        cols = ["signal_name", "top_bucket_return", "bottom_bucket_return", "spread",
                "spread_vs_momentum", "n_observations"]
        formatted = spread_table[cols].copy()
        for c in ["top_bucket_return", "bottom_bucket_return", "spread", "spread_vs_momentum"]:
            formatted[c] = formatted[c].apply(
                lambda v: f"{v*100:+.2f}%" if pd.notna(v) else "N/A"
            )
        lines.append(formatted.to_markdown(index=False))
        lines.append("")
        # Identify winners
        winners = spread_table[
            (spread_table["spread_vs_momentum"].notna())
            & (spread_table["spread_vs_momentum"] > 0.03)
        ]
        if not winners.empty:
            lines.append(f"**Signals with > +3% edge over momentum:** "
                         f"{', '.join(winners['signal_name'].tolist())}\n")
        else:
            lines.append("**No signal beats momentum by a meaningful margin (+3%) on this sample.**\n")

    lines.append("## 2. Signal correlations\n")
    if not corr.empty:
        lines.append("Pearson correlation among signals. Pairs with `|r| < 0.3` carry independent "
                     "information and could be combined.\n")
        lines.append(corr.round(2).to_markdown())
        lines.append("")

    lines.append("## 3. Per-ticker signal performance\n")
    if "return_12m" in results.columns:
        per_ticker = []
        for tic in sorted(results["ticker"].unique()):
            sub = results[results["ticker"] == tic]
            row = {"ticker": tic, "n": len(sub),
                   "mean_return_12m": sub["return_12m"].mean()}
            for sig in ["upside_pct", "conviction_score", "roic_wacc_spread",
                        "moat_fingerprint_score", "rdcf_growth_gap", "beneish_m_score"]:
                if sig in sub.columns:
                    row[f"{sig}_mean"] = sub[sig].mean()
            per_ticker.append(row)
        ptd = pd.DataFrame(per_ticker)
        for c in ptd.columns:
            if c in ("ticker", "n"):
                continue
            ptd[c] = ptd[c].apply(
                lambda v: f"{v*100:+.1f}%" if pd.notna(v) and abs(v) < 5 else
                          (f"{v:.2f}" if pd.notna(v) else "N/A")
            )
        lines.append(ptd.to_markdown(index=False))
        lines.append("")

    lines.append("## 4. Staleness\n")
    if not staleness.empty:
        lines.append(staleness.to_markdown(index=False))
        lines.append("")

    lines.append("## 5. Conviction vs its components\n")
    if not spread_table.empty:
        # Pull conviction's spread vs each component
        comp_cols = ["roic_wacc_spread", "moat_fingerprint_score", "ev_ebitda_gap_pct"]
        conv_row = spread_table[spread_table["signal_name"] == "conviction_score"]
        if not conv_row.empty:
            cs = float(conv_row.iloc[0]["spread"]) if pd.notna(conv_row.iloc[0]["spread"]) else None
            comp_spreads = {}
            for c in comp_cols:
                cr = spread_table[spread_table["signal_name"] == c]
                if not cr.empty and pd.notna(cr.iloc[0]["spread"]):
                    comp_spreads[c] = float(cr.iloc[0]["spread"])
            if cs is not None and comp_spreads:
                best_comp = max(comp_spreads.items(), key=lambda kv: kv[1])
                lines.append(f"- Conviction composite spread: {cs*100:+.2f}%")
                lines.append(f"- Best component spread:        {best_comp[0]} = {best_comp[1]*100:+.2f}%")
                if cs > best_comp[1]:
                    lines.append("- **Composite outperforms its best component** — the weighting is doing real work.\n")
                else:
                    lines.append("- **Composite underperforms its best component** — the weighting is diluting signal.\n")

    lines.append("## 6. Next steps\n")
    if not spread_table.empty:
        winners = spread_table[
            (spread_table["spread_vs_momentum"].notna())
            & (spread_table["spread_vs_momentum"] > 0.03)
        ]
        if not winners.empty:
            lines.append("- Validate the winning signals on a wider universe (add ASML/SMCI/GOOGL "
                         "income-statement reconciliation first).")
            lines.append("- Build a position-sizing layer keyed off the winning signal(s).")
            lines.append("- Investigate whether multi-signal combination via simple regression "
                         "improves edge beyond any single signal.\n")
        else:
            lines.append("- No signal demonstrated edge over momentum on this 3-ticker sample.")
            lines.append("- Audit the DCF terminal-decay defaults (see initial_findings §6) — "
                         "calibrating IV more aggressively for quality compounders may surface edge.")
            lines.append("- Consider whether the right signals are being computed at all — momentum "
                         "captures inflection; this system captures levels.\n")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUT_DIR / "extended_findings.md", "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
