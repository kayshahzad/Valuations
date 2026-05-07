"""
scratch/run_backtest.py

Entry point: runs the backtest harness on the validated subset, produces
calibration tables and an initial findings report.

Usage:
    PYTHONPATH=. python3 scratch/run_backtest.py
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd

from aletheia.backtest import (
    fundamental_vs_momentum_comparison,
    run_backtest,
    signal_calibration_table,
    staleness_calibration,
)


OUT_DIR = Path("valuation_data/backtest")
TABLES_DIR = OUT_DIR / "calibration_tables"


def main():
    # Subset 1: tickers with both BS and IS validated against Schwab
    tickers = ["AAPL", "MSFT", "LLY", "COST"]

    print(f"\n{'='*64}\n  Aletheia Backtest — initial run\n{'='*64}")
    print(f"  Tickers : {tickers}")
    print(f"  Range   : 2020-01-01 → 2024-01-01 (quarterly cadence)")
    print(f"  Horizons: 6m, 12m\n")

    results = run_backtest(
        tickers=tickers,
        start_date=date(2020, 1, 1),
        end_date=date(2024, 1, 1),
        horizons_months=[6, 12],
        output_path=str(OUT_DIR / "results.parquet"),
        verbose=True,
    )

    if results.empty:
        print("No signals generated. Aborting calibration.")
        return

    TABLES_DIR.mkdir(parents=True, exist_ok=True)

    # Calibration: fundamental upside_pct
    upside_cal = signal_calibration_table(results, "upside_pct", "return_12m")
    upside_cal.to_csv(TABLES_DIR / "upside_pct_calibration.csv", index=False)

    # Calibration: momentum baseline
    mom_cal = signal_calibration_table(results, "price_momentum_180d", "return_12m")
    mom_cal.to_csv(TABLES_DIR / "momentum_calibration.csv", index=False)

    # Calibration: conviction score
    conv_cal = signal_calibration_table(results, "conviction_score", "return_12m")
    conv_cal.to_csv(TABLES_DIR / "conviction_calibration.csv", index=False)

    # Diagnostic: fundamental vs momentum
    fvm = fundamental_vs_momentum_comparison(results, return_column="return_12m")
    fvm.to_csv(TABLES_DIR / "fundamental_vs_momentum.csv", index=False)

    # Staleness
    staleness = staleness_calibration(results)
    staleness.to_csv(TABLES_DIR / "staleness_calibration.csv", index=False)

    print(f"\n{'='*64}\n  Calibration tables\n{'='*64}\n")
    print("=== UPSIDE % CALIBRATION (fundamental signal) ===")
    print(upside_cal.to_string(index=False))
    print("\n=== MOMENTUM CALIBRATION (control) ===")
    print(mom_cal.to_string(index=False))
    print("\n=== FUNDAMENTAL vs MOMENTUM (the diagnostic) ===")
    if not fvm.empty:
        for col, val in fvm.iloc[0].items():
            if isinstance(val, float):
                print(f"  {col:<42} {val:>+10.2%}")
            else:
                print(f"  {col:<42} {val:>10}")
    print("\n=== CONVICTION CALIBRATION ===")
    print(conv_cal.to_string(index=False))
    print("\n=== STALENESS CALIBRATION ===")
    print(staleness.to_string(index=False))

    _write_findings(results, upside_cal, mom_cal, fvm, conv_cal, staleness)
    print(f"\nFindings: {OUT_DIR / 'initial_findings.md'}")


def _write_findings(results, upside_cal, mom_cal, fvm, conv_cal, staleness):
    lines = []
    lines.append("# Backtest Initial Findings\n")
    lines.append(f"**Generated:** {date.today().isoformat()}  ")
    lines.append(f"**Sample:** {len(results)} signal observations  ")
    if not results.empty:
        lines.append(f"**Tickers:** {', '.join(sorted(results['ticker'].unique()))}  ")
        lines.append(f"**Date range:** {results['as_of_date'].min()} → {results['as_of_date'].max()}\n")

    lines.append("## 1. Does the fundamental signal predict subsequent returns?\n")
    if not upside_cal.empty:
        top = float(upside_cal.iloc[-1]["mean_return"])
        bot = float(upside_cal.iloc[0]["mean_return"])
        lines.append(f"- Top-quintile (highest `upside_pct`) mean 12m return: **{top:+.1%}**")
        lines.append(f"- Bottom-quintile mean 12m return: **{bot:+.1%}**")
        lines.append(f"- Spread: **{top - bot:+.1%}**")
        if top > bot + 0.05:
            lines.append(f"- **Result:** Top buckets meaningfully outperform bottom — engine signal carries directional information.\n")
        elif top > bot:
            lines.append(f"- **Result:** Modest positive ordering. Could be noise on small sample.\n")
        else:
            lines.append(f"- **Result:** Top buckets do NOT outperform bottom. Signal direction is wrong or noise dominates.\n")

    lines.append("## 2. Does it beat the momentum baseline?\n")
    if not fvm.empty:
        excess = float(fvm.iloc[0]["excess_spread"])
        fund_spread = float(fvm.iloc[0]["fundamental_spread"])
        mom_spread = float(fvm.iloc[0]["momentum_spread"])
        lines.append(f"- Fundamental top-bottom spread: **{fund_spread:+.1%}**")
        lines.append(f"- Momentum top-bottom spread:    **{mom_spread:+.1%}**")
        lines.append(f"- **Excess spread (fund − mom):  {excess:+.1%}**\n")
        if excess >= 0.03:
            lines.append("**Verdict:** Fundamental signal beats naive momentum by a meaningful margin. The engine appears to capture information beyond what's in price.\n")
        elif excess > 0:
            lines.append("**Verdict:** Fundamental signal modestly beats momentum, but margin is small relative to typical noise on this sample. Inconclusive — needs more tickers / longer window.\n")
        else:
            lines.append("**Verdict:** Fundamental signal does NOT beat momentum. The engine is producing analytical-shaped opinions that don't add value over a dumb price-trend rule. We have a different conversation to have.\n")

    lines.append("## 3. Signal performance by data staleness\n")
    if not staleness.empty:
        lines.append(staleness.to_markdown(index=False))
        lines.append("")
        lines.append("Interpretation: if returns vary meaningfully by `days_since_filing`, the signal is sensitive to the underlying data — fundamentals matter. If they're flat, the engine isn't actually using the financial data; it's a price-trend rule wearing a DCF costume.\n")

    lines.append("## 4. Surprises — large signal vs outcome divergences\n")
    if "return_12m" in results.columns and "upside_pct" in results.columns:
        # Where signal said BIG upside but return was negative, or vice versa
        df = results.dropna(subset=["return_12m"]).copy()
        if not df.empty:
            df["_signal_bucket"] = pd.qcut(
                df["upside_pct"].rank(method="first"), 5, labels=False, duplicates="drop"
            )
            biggest_misses = df[
                ((df["_signal_bucket"] >= 3) & (df["return_12m"] < -0.10))
                | ((df["_signal_bucket"] <= 1) & (df["return_12m"] > 0.30))
            ].sort_values("return_12m")
            if not biggest_misses.empty:
                lines.append(f"Top {min(8, len(biggest_misses))} signal/outcome divergences:\n")
                cols = ["ticker", "as_of_date", "fiscal_year_used", "current_price",
                        "base_iv", "upside_pct", "return_12m"]
                lines.append(biggest_misses[cols].head(8).to_markdown(index=False))
                lines.append("")
            else:
                lines.append("No major divergences found in this sample.\n")

    lines.append("## 5. Next steps\n")
    if not fvm.empty:
        excess = float(fvm.iloc[0]["excess_spread"])
        if excess >= 0.03:
            lines.append("- Expand validation work to remaining 18 tickers in the universe.")
            lines.append("- Validate D&A and CapEx (the unvalidated inputs to FCF / EBITDA / IV) against Schwab.")
            lines.append("- Revisit beta lookahead bias — current beta is a 5y window from today, not from as_of_date.")
            lines.append("- Add a third horizon (24m) once enough forward observation accumulates post-2024.\n")
        else:
            lines.append("- Don't expand universe before fixing the signal. Re-examine DCF assumptions.")
            lines.append("- Audit WACC / terminal growth defaults — historically these may have been set for the wrong regime.")
            lines.append("- Consider whether KNOWN_ISSUES bypasses (e.g., V missing diluted shares) are silently distorting the cohort.")
            lines.append("- Investigate if the calc layer is excessively price-sensitive (because `current_price` enters the EV/EBITDA bucketing).\n")

    Path("valuation_data/backtest").mkdir(parents=True, exist_ok=True)
    with open("valuation_data/backtest/initial_findings.md", "w") as f:
        f.write("\n".join(lines))


if __name__ == "__main__":
    main()
