"""Cash-flow formulas — FCFF, FCF.

Phase 2 of the centralization refactor. Both adapters previously
computed FCFF differently — cleaning_engine used the full CFA-textbook
formula (``NOPAT + D&A − CapEx − ΔNWC``) while the FMP adapter aliased
FCFF to FCF (``OperatingCF − CapEx``). FMP exposes
``changeInWorkingCapital`` directly so the full formula is now feasible
on both paths; centralizing here closes the methodology gap.

FCF itself is identical on both paths (``OperatingCF − |CapEx|``);
moved here in Phase 3's mechanical consolidation but defined now to
keep the cash-flow primitives co-located.
"""

from __future__ import annotations

from typing import Optional


def fcf(
    *,
    operating_cf: Optional[float],
    capex: Optional[float],
) -> Optional[float]:
    """OperatingCF − |CapEx|

    CapEx sign convention varies: XBRL files it negative (cash
    outflow), FMP files it positive. The formula takes the absolute
    value so callers don't need to normalize.

    Returns ``None`` when ``operating_cf`` is missing — CapEx defaults
    to zero when missing (a clean cash-from-ops number without a
    matching investing-cash entry usually means the filer had zero
    capex that period, which is rare but legitimate for asset-light
    holdings).
    """
    if operating_cf is None:
        return None
    return operating_cf - abs(capex or 0.0)


def fcff(
    *,
    nopat: Optional[float],
    depreciation: Optional[float],
    capex: Optional[float],
    delta_nwc: Optional[float],
) -> Optional[float]:
    """NOPAT + D&A − |CapEx| − ΔNWC

    Full CFA-textbook Free Cash Flow to Firm. All four inputs are
    required — returning ``None`` when any is missing is intentional;
    callers that want a fallback can supply zero for ``delta_nwc``
    (the only input that's often legitimately absent on early-stage
    filers) but should NOT fall back on the other three.

    Sign conventions:
      - ``capex`` is taken as magnitude (negative XBRL or positive FMP
        both work)
      - ``delta_nwc`` follows the cleaning-engine convention: positive
        means NWC grew (cash consumed), negative means NWC shrank
        (cash freed). Subtracted directly.
    """
    if nopat is None or depreciation is None or capex is None:
        return None
    delta = delta_nwc if delta_nwc is not None else 0.0
    return nopat + depreciation - abs(capex) - delta
