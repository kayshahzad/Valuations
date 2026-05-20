"""Income-statement synthesized quantities.

Phase 3 of the centralization refactor. EBITDA synthesis (from
OperatingIncome + Depreciation_Total) had identical logic in both
adapter paths; consolidated here so future tweaks (e.g. should SBC be
added back?) happen in one place.
"""

from __future__ import annotations

from typing import Optional


def ebitda(
    *,
    operating_income: Optional[float],
    depreciation_total: Optional[float],
) -> Optional[float]:
    """OperatingIncome + Depreciation_Total

    Synthesized EBITDA when the filer doesn't disclose it directly.
    Returns ``None`` when EBIT is missing — depreciation defaults to
    zero (some filers, especially asset-light services, report no
    D&A line, which yields EBITDA = OperatingIncome).
    """
    if operating_income is None:
        return None
    return operating_income + (depreciation_total or 0.0)
