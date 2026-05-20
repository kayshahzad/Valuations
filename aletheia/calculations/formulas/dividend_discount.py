"""Dividend Discount Model — for ``ddm_required`` filers.

Phase A.7. Used by ``DdmEngine`` in
``aletheia.tools.valuation_engines``. Two-stage Gordon DDM:

  ``P0 = Σ_t=1..N [DPS_t / (1 + Ke)^t]  +  TV_N / (1 + Ke)^N``

  where:
    DPS_t  = DPS_0 × (1 + g_explicit)^t   for t in 1..N
    TV_N   = DPS_{N+1} / (Ke - g_terminal)
    DPS_{N+1} = DPS_N × (1 + g_terminal)

Two-stage rationale (same as the rate-base engine):

  Banks + payment networks + managed care typically have a near-term
  earnings-growth trajectory above the long-run sustainable rate
  (15+ years of dividend growth at JPM, 5 years at AXP, 8-12% EPS
  growth at UNH). Modeling those years explicitly then dropping to
  terminal growth avoids Gordon divergence when explicit_g > Ke,
  and reflects the real growth runway.

Edge case — zero or negative DPS:

  Filers in growth mode that don't pay dividends (CNC) can't be
  valued via DDM. The formula returns ``None`` rather than
  fabricating a value from a zero starting point. The engine
  surfaces the gap; analysts can populate a synthetic
  ``future_dps_initiation`` field for an alternative model.
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def ddm_intrinsic_value(
    *,
    current_dps: float,                 # annualized $ DPS today
    cost_of_equity: float,              # decimal, CAPM Ke
    explicit_growth: float,             # near-term DPS growth
    explicit_years: int,                # high-growth horizon (typically 5-10)
    terminal_growth: float = 0.040,     # decimal, GDP+inflation default
) -> Optional[float]:
    """Two-stage DDM per-share intrinsic value.

    Returns ``None`` when:
      - ``current_dps <= 0`` (filer doesn't pay a dividend; DDM
        undefined — caller should use a different model)
      - ``cost_of_equity <= terminal_growth`` (Gordon divergence in
        the terminal piece — no equilibrium valuation exists)
      - ``explicit_years <= 0`` or any required input is None
    """
    if (
        current_dps is None or current_dps <= 0
        or cost_of_equity is None
        or explicit_growth is None
        or explicit_years is None or explicit_years <= 0
    ):
        return None
    if cost_of_equity <= terminal_growth:
        return None

    # Explicit period: compound DPS at explicit_growth, discount each
    pv_explicit = 0.0
    for t in range(1, explicit_years + 1):
        dps_t = current_dps * (1.0 + explicit_growth) ** t
        pv_explicit += dps_t / (1.0 + cost_of_equity) ** t

    # Terminal value using Gordon on year N+1 dividend
    dps_n = current_dps * (1.0 + explicit_growth) ** explicit_years
    dps_n_plus_1 = dps_n * (1.0 + terminal_growth)
    terminal_value = dps_n_plus_1 / (cost_of_equity - terminal_growth)
    pv_terminal = terminal_value / (1.0 + cost_of_equity) ** explicit_years

    return pv_explicit + pv_terminal


def ddm_decomposition(
    *,
    current_dps: float,
    cost_of_equity: float,
    explicit_growth: float,
    explicit_years: int,
    terminal_growth: float = 0.040,
) -> Optional[dict]:
    """Same calculation as ``ddm_intrinsic_value`` but returns the
    year-by-year DPS / PV breakdown for audit / UI display. Returns
    ``None`` for the same degenerate cases."""
    if (current_dps is None or current_dps <= 0
            or cost_of_equity is None or cost_of_equity <= terminal_growth
            or explicit_years is None or explicit_years <= 0):
        return None

    yearly: List[Tuple[int, float, float]] = []
    pv_explicit = 0.0
    for t in range(1, explicit_years + 1):
        dps_t = current_dps * (1.0 + explicit_growth) ** t
        pv_t = dps_t / (1.0 + cost_of_equity) ** t
        pv_explicit += pv_t
        yearly.append((t, dps_t, pv_t))

    dps_n = current_dps * (1.0 + explicit_growth) ** explicit_years
    dps_n_plus_1 = dps_n * (1.0 + terminal_growth)
    tv = dps_n_plus_1 / (cost_of_equity - terminal_growth)
    pv_tv = tv / (1.0 + cost_of_equity) ** explicit_years

    total = pv_explicit + pv_tv
    return {
        "yearly": [
            {"year": t, "dps": dps, "pv": pv}
            for (t, dps, pv) in yearly
        ],
        "terminal_value":      tv,
        "pv_terminal":         pv_tv,
        "pv_explicit":         pv_explicit,
        "intrinsic_per_share": total,
        "tv_share":            pv_tv / total if total > 0 else None,
    }


__all__ = ["ddm_intrinsic_value", "ddm_decomposition"]
