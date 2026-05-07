"""
aletheia/scenarios/sensitivity.py

Tornado / one-at-a-time sensitivity analysis. For a given ticker, perturb
each input by ±perturbation_pct and measure IV impact. Returns a sorted
DataFrame showing which assumptions matter most.

The 10 inputs (decision D2 from the Phase 3 plan):
  1. revenue_growth_y1_5
  2. revenue_growth_y6_10
  3. terminal_growth
  4. terminal_ebit_margin
  5. capex_pct_revenue
  6. discount_rate
  7. tax_rate
  8. terminal_margin_decay  (the ratio override, [0.5, 1.0])
  9. risk_free_rate         (passed through DCFEngine constructor)
 10. beta                   (passed through DCFEngine constructor)

For each input:
  - Compute baseline IV (no override; current production values for that ticker)
  - Run with override = baseline × (1 + perturbation_pct), record IV_high
  - Run with override = baseline × (1 − perturbation_pct), record IV_low
  - impact = |IV_high − IV_low| / baseline_IV  (sensitivity magnitude)

Outputs are sorted by impact descending so the analyst sees which
assumptions dominate IV for the specific ticker.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Dict, List, Optional, Tuple

import pandas as pd

from aletheia.contracts.interfaces import CalculationInput, ValuationProfile
from aletheia.utils.calc_input_builder import make_calc_input
from aletheia.tools.dcf_engine import DCFEngine


# Inputs we can perturb via ValuationProfile (require no DCFEngine constructor change)
PROFILE_INPUTS = (
    "growth_rate",                       # ≈ revenue_growth_y1_5
    "terminal_growth",
    "terminal_margin_decay",
    "terminal_ebit_margin_override",
    "capex_pct_revenue_override",
    "discount_rate_override",
    "tax_rate_override",
)

# Inputs requiring DCFEngine constructor params (rf, beta)
ENGINE_INPUTS = ("risk_free_rate", "beta")

DEFAULT_TORNADO_INPUTS = PROFILE_INPUTS + ENGINE_INPUTS


def _baseline_iv(calc: CalculationInput) -> Tuple[float, dict]:
    """Compute baseline IV + reference values for each perturbation input."""
    engine = DCFEngine(verbose=False)
    result = engine.run(calc)
    base_iv = result.intrinsic_per_share(result.base.enterprise_value, result.net_debt) or 0.0
    if not base_iv:
        return 0.0, {}
    a = result.base.assumptions
    refs = {
        "growth_rate":                    calc.valuation_profile.growth_rate,
        "terminal_growth":                a.terminal_growth,
        "terminal_margin_decay":          calc.valuation_profile.terminal_margin_decay,
        "terminal_ebit_margin_override":  a.ebit_margin_terminal,
        "capex_pct_revenue_override":     a.capex_pct_revenue,
        "discount_rate_override":         a.wacc,
        "tax_rate_override":              a.tax_rate,
        "risk_free_rate":                 result.risk_free_rate,
        "beta":                           result.beta,
    }
    return float(base_iv), refs


def _run_with_override(
    calc: CalculationInput,
    field: str,
    override_value: float,
) -> Optional[float]:
    """Apply a single perturbed input and return resulting IV (None on error)."""
    if field in PROFILE_INPUTS:
        new_profile = replace(calc.valuation_profile, **{field: override_value})
        new_calc = CalculationInput(
            df=calc.df,
            classification=calc.classification,
            known_issues=calc.known_issues,
            valuation_profile=new_profile,
            lifecycle_thresholds=calc.lifecycle_thresholds,
        )
        try:
            r = DCFEngine(verbose=False).run(new_calc)
        except Exception:
            return None
    elif field in ENGINE_INPUTS:
        kwargs = {field: override_value}
        try:
            r = DCFEngine(verbose=False, **kwargs).run(calc)
        except Exception:
            return None
    else:
        return None

    if not r.base:
        return None
    iv = r.intrinsic_per_share(r.base.enterprise_value, r.net_debt)
    return float(iv) if iv else None


def tornado_analysis(
    ticker: str,
    perturbation_pct: float = 0.10,
    inputs: Tuple[str, ...] = DEFAULT_TORNADO_INPUTS,
    calc_input: Optional[CalculationInput] = None,
) -> pd.DataFrame:
    """
    Tornado sensitivity. Returns DataFrame with columns:
      input, baseline_value, low_value, low_iv, high_value, high_iv,
      impact_pct (sorted descending), iv_change_low_pct, iv_change_high_pct.
    """
    if calc_input is None:
        calc_input = make_calc_input(ticker)
    base_iv, refs = _baseline_iv(calc_input)
    if base_iv <= 0:
        return pd.DataFrame()

    rows = []
    for field in inputs:
        ref = refs.get(field)
        if ref is None or ref == 0:
            continue
        low_value = ref * (1 - perturbation_pct)
        high_value = ref * (1 + perturbation_pct)
        low_iv = _run_with_override(calc_input, field, low_value)
        high_iv = _run_with_override(calc_input, field, high_value)
        if low_iv is None or high_iv is None:
            continue
        impact = abs(high_iv - low_iv) / base_iv
        rows.append({
            "input":             field,
            "baseline_value":    ref,
            "low_value":         low_value,
            "low_iv":            low_iv,
            "high_value":        high_value,
            "high_iv":           high_iv,
            "iv_change_low_pct":  (low_iv - base_iv) / base_iv,
            "iv_change_high_pct": (high_iv - base_iv) / base_iv,
            "impact_pct":        impact,
        })

    df = pd.DataFrame(rows).sort_values("impact_pct", ascending=False).reset_index(drop=True)
    df.attrs["ticker"] = ticker
    df.attrs["baseline_iv"] = base_iv
    df.attrs["perturbation_pct"] = perturbation_pct
    return df


def render_tornado(df: pd.DataFrame) -> str:
    """ASCII bar-chart-style rendering of a tornado for terminal display."""
    if df.empty:
        return "(empty tornado — baseline IV could not be computed)"
    base_iv = df.attrs.get("baseline_iv", 0.0)
    pert = df.attrs.get("perturbation_pct", 0.0)
    ticker = df.attrs.get("ticker", "?")
    out = [f"\nTornado — {ticker}  (baseline IV ${base_iv:.2f}, ±{pert*100:.0f}% perturbation)\n"]
    out.append(f"  {'input':<32}{'low IV':>11}{'baseline':>11}{'high IV':>11}{'impact':>10}")
    out.append("  " + "-" * 75)
    max_impact = df["impact_pct"].max() if not df.empty else 1.0
    for _, r in df.iterrows():
        bar_len = int((r["impact_pct"] / max_impact) * 24) if max_impact > 0 else 0
        bar = "█" * bar_len
        out.append(f"  {r['input']:<32}{'$%.2f' % r['low_iv']:>11}{'$%.2f' % base_iv:>11}"
                   f"{'$%.2f' % r['high_iv']:>11}{r['impact_pct']*100:>9.1f}% {bar}")
    return "\n".join(out)
