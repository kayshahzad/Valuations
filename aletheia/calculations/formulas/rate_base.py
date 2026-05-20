"""Rate-base equity valuation — for regulated-utility filers.

Phase A.3 of the valuation-router refactor. Used by the
``RateBaseEngine`` in ``aletheia.tools.valuation_engines``.

The model:

  ``Equity Value = Σ_{t=1..N} [RB_t × ROE_allowed × (1+g_explicit)^t-1
                                / (1 + Ke)^t]
                 + Terminal_Value / (1 + Ke)^N``

  where Terminal_Value is a Gordon perpetuity capturing post-explicit
  rate-base growth at a terminal rate (typically GDP-level, ~2.5%):

  ``TV = [RB_N × ROE_allowed × (1 + g_terminal)] / (Ke − g_terminal)``

Two-stage rationale:

  Regulated utilities have a documented near-term capex plan
  (typically 4-5 years of regulator-approved rate-base growth) that
  is higher than long-run sustainable growth. Modeling those years
  explicitly, then dropping to a terminal-growth perpetuity, avoids
  the Gordon-divergence problem (utility CAPM Ke can be near or
  below the explicit-period g for low-beta names) while staying
  intellectually honest about the rate-plan-driven near-term
  trajectory.

  Pure Gordon (single perpetuity) would either:
    - blow up when Ke < g_explicit (NEE β=0.57 → Ke ~6.3% < g 7.5%)
    - require an arbitrary Ke floor (analyst convention) that
      contradicts the regulator's view of the utility's risk
"""

from __future__ import annotations

from typing import List, Optional, Tuple


def rate_base_equity_value(
    *,
    rate_base: float,                  # starting $ rate base
    allowed_roe: float,                # decimal, regulator-approved
    cost_of_equity: float,             # decimal, CAPM Ke
    explicit_growth: float,            # decimal, near-term rate-base growth
    explicit_years: int,               # rate-plan horizon (typically 4-5)
    terminal_growth: float = 0.025,    # decimal, GDP-level perpetuity
) -> Optional[float]:
    """Two-stage rate-base perpetuity equity value.

    Returns ``None`` when:
      - ``cost_of_equity <= terminal_growth`` (Gordon divergence in
        the terminal piece — no equilibrium valuation exists)
      - any required input is missing
      - ``explicit_years <= 0``

    Sign conventions:
      - ``rate_base`` is positive (dollar value of approved rate base)
      - All growth rates and Ke are decimals (0.075 = 7.5%, not 7.5)
      - Output is positive when valuation is well-defined; never
        clamped to zero — a "negative equity value" return would
        indicate inputs violating the formula's assumptions
    """
    if (
        rate_base is None or allowed_roe is None
        or cost_of_equity is None or explicit_growth is None
        or explicit_years is None or explicit_years <= 0
    ):
        return None
    if cost_of_equity <= terminal_growth:
        return None

    # Explicit period — compound rate base, capitalize each year's
    # allowed earnings, discount to PV
    pv_explicit = 0.0
    for t in range(1, explicit_years + 1):
        rb_t = rate_base * (1.0 + explicit_growth) ** t
        earnings_t = rb_t * allowed_roe
        pv_explicit += earnings_t / (1.0 + cost_of_equity) ** t

    # Terminal value at end of year N (using Gordon on year N+1
    # earnings under terminal_growth)
    rb_n = rate_base * (1.0 + explicit_growth) ** explicit_years
    earnings_n_plus_1 = rb_n * (1.0 + terminal_growth) * allowed_roe
    terminal_value = earnings_n_plus_1 / (cost_of_equity - terminal_growth)
    pv_terminal = terminal_value / (1.0 + cost_of_equity) ** explicit_years

    return pv_explicit + pv_terminal


def rate_base_decomposition(
    *,
    rate_base: float,
    allowed_roe: float,
    cost_of_equity: float,
    explicit_growth: float,
    explicit_years: int,
    terminal_growth: float = 0.025,
) -> Optional[dict]:
    """Same calculation as ``rate_base_equity_value`` but returns
    the year-by-year breakdown for audit / display. Used by the
    UI to render the explicit-period earnings trajectory + TV split.

    Returns ``None`` when the underlying formula is degenerate (see
    ``rate_base_equity_value``).
    """
    if cost_of_equity is None or cost_of_equity <= terminal_growth:
        return None
    if rate_base is None or allowed_roe is None or explicit_years is None:
        return None
    if explicit_years <= 0:
        return None

    yearly: List[Tuple[int, float, float, float]] = []   # (t, RB_t, earnings_t, pv_t)
    pv_explicit = 0.0
    for t in range(1, explicit_years + 1):
        rb_t = rate_base * (1.0 + explicit_growth) ** t
        earnings_t = rb_t * allowed_roe
        pv_t = earnings_t / (1.0 + cost_of_equity) ** t
        pv_explicit += pv_t
        yearly.append((t, rb_t, earnings_t, pv_t))

    rb_n = rate_base * (1.0 + explicit_growth) ** explicit_years
    earnings_n_plus_1 = rb_n * (1.0 + terminal_growth) * allowed_roe
    tv = earnings_n_plus_1 / (cost_of_equity - terminal_growth)
    pv_tv = tv / (1.0 + cost_of_equity) ** explicit_years

    total = pv_explicit + pv_tv
    return {
        "yearly": [
            {"year": t, "rate_base": rb, "earnings": e, "pv": pv}
            for (t, rb, e, pv) in yearly
        ],
        "terminal_value":      tv,
        "pv_terminal":         pv_tv,
        "pv_explicit":         pv_explicit,
        "equity_value":        total,
        "tv_share_of_equity":  pv_tv / total if total > 0 else None,
    }


__all__ = ["rate_base_equity_value", "rate_base_decomposition"]
