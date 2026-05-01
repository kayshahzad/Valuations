"""
aletheia/tools/cyclicality.py

Calculates revenue Z-score and determines cyclicality haircuts.
Extracted from context.py agent to ensure determinism and testability.
"""

import numpy as np
import json
from typing import Tuple, Dict

def calculate_z_score(calc_input: 'CalculationInput') -> Tuple[float, bool, bool, float, Dict]:
    """
    Calculate revenue Z-score from DuckDB clean_Revenue multi-year series.

    Returns:
        (z_score, is_peak, applies_cyclical_haircut, avg_3yr, db_context_dict)
    """
    df = calc_input.df

    if df.empty or "clean_Revenue" not in df.columns:
        return 0.0, False, False, 0.0, {}

    rev_series = df.sort_values("fiscal_year")["clean_Revenue"].dropna()
    revenues = [float(r) for r in rev_series if r and not np.isnan(r)]

    if len(revenues) < 3:
        return 0.0, False, False, 0.0, {}

    mean = np.mean(revenues)
    std  = np.std(revenues)
    z_score = float((revenues[-1] - mean) / std if std > 0 else 0.0)
    
    meta = calc_input.classification
    classification = "cyclical" # default
    
    if meta:
        if meta.lifecycle == "cyclical_industrial":
            classification = "cyclical"
        elif meta.sector in ["Technology", "Healthcare", "Communication Services", "Financial Services", "Consumer Defensive"]:
                classification = "non_cyclical"
            elif meta.lifecycle in ["secular_hyper_growth", "hyper_growth"]:
                classification = "ambiguous"

        if classification == "non_cyclical":
            threshold = float('inf')
        elif classification == "ambiguous":
            threshold = 3.0
        else:
            threshold = 2.0
            
        applies_cyclical_haircut = bool(z_score > threshold)
        is_peak = bool(z_score > 2.0)
        avg_3yr = float(np.mean(revenues[-3:]))

        # Additional context from latest row
        latest_row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]

        def _g(col):
            v = latest_row.get(col)
            return float(v) if v is not None and not (
                isinstance(v, float) and np.isnan(v)) else None

        deferred_rev = None
        deferred_rev_growth = None
        d8_score = _g("domain_score_D8_Revenue")
        try:
            clean_data = json.loads(latest_row.get("clean_json") or "{}")
            deferred_rev = clean_data.get("DeferredRevenue")
            deferred_rev_growth = clean_data.get("DeferredRevenue_Growth")
        except Exception:
            pass

        intangible_amort = None
        try:
            raw_data = json.loads(latest_row.get("raw_json") or "{}")
            intangible_amort = raw_data.get("AmortizationOfIntangibleAssets")
        except Exception:
            pass

        db_context = {
            "fiscal_years": list(df.sort_values("fiscal_year")["fiscal_year"]),
            "revenues": revenues,
            "latest_revenue": revenues[-1],
            "revenue_cagr_3y": _g("derived_Revenue_CAGR_3Y"),
            "gross_margin_pct": _g("derived_GrossMargin_Pct"),
            "fcf_margin_pct": _g("derived_FCF_Margin_Pct"),
            "reinvestment_rate": _g("derived_ReinvestmentRate"),
            "roic": _g("derived_ROIC"),
            "wacc": _g("derived_WACC") or 0.09,
            "deferred_revenue": deferred_rev,
            "deferred_rev_growth": deferred_rev_growth,
            "d8_revenue_score": d8_score,
            "intangible_amort": float(intangible_amort) if intangible_amort else None,
            "data_quality": _g("overall_quality_score"),
        }

        return z_score, is_peak, applies_cyclical_haircut, avg_3yr, db_context
    except Exception as e:
        print(f"  ✗ Z-score calc failed: {e}")
        return 0.0, False, False, 0.0, {}
