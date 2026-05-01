"""
aletheia/tools/forensic_metrics.py

Standalone deterministic calculations for forensic accounting and operational metrics.
Extracted from forensic.py to ensure testability and isolation.
"""

def compute_operating_leverage_score(gross_margin_pct: float, ebit_margin_pct: float) -> float:
    """
    Operating leverage = how much gross profit flows through to EBIT.
    Formula: ebit_margin / gross_margin → scaled to 0-10.
    Source: DuckDB derived_EBIT_Margin_Pct and derived_GrossMargin_Pct.
    """
    if not gross_margin_pct or gross_margin_pct <= 0:
        return 5.0
    if ebit_margin_pct is None:
        return 5.0
    ratio = ebit_margin_pct / gross_margin_pct
    return round(max(0.0, min(10.0, ratio * 10.0)), 1)
