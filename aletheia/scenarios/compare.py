"""
aletheia/scenarios/compare.py

Side-by-side scenario comparison. Run multiple scenarios on the same
ticker, output a tidy DataFrame showing input assumptions next to final
IVs so the analyst can see exactly which assumptions drive the differences.

Two entry points:
  - compare_scenarios(ticker, scenarios) — programmatic; returns DataFrame
  - render_comparison(df) — markdown-table string for terminal/file display
"""

from __future__ import annotations

from typing import Any, Callable, List, Optional, Tuple

import pandas as pd

from aletheia.contracts.interfaces import CalculationInput, ScenarioOverride
from aletheia.scenarios.library import ScenarioFn, ScenarioRunResult, run_scenario
from aletheia.utils.calc_input_builder import make_calc_input


def compare_scenarios(
    ticker: str,
    scenarios: List[Tuple[str, ScenarioFn]],
    calc_input: Optional[CalculationInput] = None,
) -> pd.DataFrame:
    """
    Run each scenario and return a tidy DataFrame for comparison.

    `scenarios` is a list of (display_label, scenario_fn) tuples. The label
    can differ from the scenario function's internal name to support cases
    where the same scenario is run with different metadata.

    Returns DataFrame with one row per scenario:
        scenario, base_iv, current_price, upside_pct,
        wacc, terminal_growth, terminal_margin, capex_pct,
        rev_growth_y1_5, rev_growth_y6_10, rationale
    """
    if calc_input is None:
        calc_input = make_calc_input(ticker)

    rows = []
    for label, fn in scenarios:
        try:
            r: ScenarioRunResult = run_scenario(ticker, fn, calc_input=calc_input)
        except NotImplementedError as e:
            rows.append({
                "scenario": label,
                "status": f"STUB: {e}",
                "base_iv": None,
                "current_price": None,
                "upside_pct": None,
            })
            continue
        except Exception as e:
            rows.append({
                "scenario": label,
                "status": f"ERROR: {type(e).__name__}: {e}",
                "base_iv": None,
                "current_price": None,
                "upside_pct": None,
            })
            continue

        ov = r.override
        rows.append({
            "scenario":          label,
            "status":            "ok",
            "current_price":     r.current_price,
            "base_iv":           r.base_iv,
            "upside_pct":        r.upside_pct,
            "wacc":              r.base_wacc,
            "terminal_growth":   r.base_terminal_growth,
            "terminal_margin":   r.base_terminal_margin,
            "capex_pct":         r.base_capex_pct,
            "rev_growth_y1_5":   ov.revenue_growth_y1_5,
            "rev_growth_y6_10":  ov.revenue_growth_y6_10,
            "rationale":         r.rationale,
        })

    return pd.DataFrame(rows)


def render_comparison(df: pd.DataFrame) -> str:
    """Render the comparison DataFrame as a markdown-style table."""
    if df.empty:
        return "(empty comparison)"

    out_lines: List[str] = []

    # Header summary
    valid = df[df["status"] == "ok"]
    if not valid.empty:
        price = float(valid["current_price"].iloc[0])
        out_lines.append(f"Current price: ${price:,.2f}\n")

    # Main IV table
    iv_cols = ["scenario", "base_iv", "upside_pct", "wacc",
               "terminal_growth", "terminal_margin", "capex_pct",
               "rev_growth_y1_5", "rev_growth_y6_10"]
    show = df[df["status"] == "ok"][iv_cols].copy()
    if not show.empty:
        show["base_iv"]         = show["base_iv"].apply(lambda v: f"${v:,.2f}" if pd.notna(v) else "—")
        show["upside_pct"]      = show["upside_pct"].apply(lambda v: f"{v*100:+.1f}%" if pd.notna(v) else "—")
        show["wacc"]            = show["wacc"].apply(lambda v: f"{v*100:.2f}%" if pd.notna(v) else "—")
        show["terminal_growth"] = show["terminal_growth"].apply(lambda v: f"{v*100:.2f}%" if pd.notna(v) else "—")
        show["terminal_margin"] = show["terminal_margin"].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "—")
        show["capex_pct"]       = show["capex_pct"].apply(lambda v: f"{v*100:.1f}%" if pd.notna(v) else "—")
        show["rev_growth_y1_5"] = show["rev_growth_y1_5"].apply(
            lambda v: f"{v*100:.1f}%" if pd.notna(v) else "—"
        )
        show["rev_growth_y6_10"] = show["rev_growth_y6_10"].apply(
            lambda v: f"{v*100:.1f}%" if pd.notna(v) else "—"
        )
        out_lines.append(show.to_markdown(index=False))

    # Errored or stubbed scenarios
    skipped = df[df["status"] != "ok"]
    if not skipped.empty:
        out_lines.append("\n**Skipped scenarios:**\n")
        for _, r in skipped.iterrows():
            out_lines.append(f"- `{r['scenario']}`: {r['status']}")

    # Rationales
    if not valid.empty:
        out_lines.append("\n**Rationales:**\n")
        for _, r in valid.iterrows():
            out_lines.append(f"- **{r['scenario']}**: {r['rationale']}")

    return "\n".join(out_lines)
