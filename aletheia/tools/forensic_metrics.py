"""
aletheia/tools/forensic_metrics.py

Standalone deterministic calculations for forensic accounting and operational metrics.
Extracted from forensic.py to ensure testability and isolation.
"""

def compute_operating_leverage_score(
    gross_margin_pct: float, ebit_margin_pct: float,
    *, ticker: str = "?",
) -> float:
    """
    Operating leverage = how much gross profit flows through to EBIT.
    Formula: ebit_margin / gross_margin → scaled to 0-10.
    Source: DuckDB derived_EBIT_Margin_Pct and derived_GrossMargin_Pct.

    Returns the neutral 5.0 fallback when inputs are missing or
    nonsensical (zero/negative gross margin) — and emits a structured
    soft-flag so receipts capture that the score is a fallback, not a
    real measurement. Prevents the LLM-thesis pattern of citing
    "Operating leverage 5.0/10" as a signal when it's actually a stub.
    """
    from aletheia.calculations._guards import _flag_unusual

    if gross_margin_pct is None or (isinstance(gross_margin_pct, float)
                                    and gross_margin_pct != gross_margin_pct):
        _flag_unusual(
            value=gross_margin_pct, field_name="gross_margin_pct",
            ticker=ticker, fn="compute_operating_leverage_score",
            note="NaN/None gross_margin — returning neutral 5.0 fallback",
        )
        return 5.0
    if gross_margin_pct <= 0:
        _flag_unusual(
            value=gross_margin_pct, field_name="gross_margin_pct",
            ticker=ticker, fn="compute_operating_leverage_score",
            note="zero/negative gross margin (loss-making) — neutral 5.0 fallback",
        )
        return 5.0
    if ebit_margin_pct is None:
        _flag_unusual(
            value=None, field_name="ebit_margin_pct",
            ticker=ticker, fn="compute_operating_leverage_score",
            note="None ebit_margin — returning neutral 5.0 fallback",
        )
        return 5.0
    ratio = ebit_margin_pct / gross_margin_pct
    return round(max(0.0, min(10.0, ratio * 10.0)), 1)
