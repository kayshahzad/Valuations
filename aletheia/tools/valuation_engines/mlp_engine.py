"""MLP engine — for ``mlp_required`` filers (midstream master limited
partnerships: Energy Transfer, Enterprise Products, etc.).

Phase A.10. A midstream MLP is a fee-based toll-road: stable contracted EBITDA,
very high leverage, and a cash-distribution policy. FCFF mis-frames it — the
growth capex that builds fee-generating assets reads as value destruction, and
the $60B+ net debt is invisible until the EV→equity bridge. So:

  1. Load analyst inputs (target EV/EBITDA multiple; optional distribution
     params) from ``config/specialized_valuation_inputs.json``.
  2. HEADLINE: EV/EBITDA multiple → equity = EV − net debt → IV per unit. EBITDA
     and net debt come from the cleaned frame; the multiple is the analyst lever.
  3. CROSS-CHECK: a two-stage distribution-discount value (the income view),
     computed via the shared DDM formula when a distribution per unit is given.
  4. Leverage context (net-debt/EBITDA) — the central risk for a midstream name.

Zero formula logic here — every numeric step delegates to
``aletheia.calculations.formulas`` (Phase 5 architecture lock).

Empty-state: when ``target_ev_ebitda`` is missing or EBITDA is non-positive, the
engine returns ``intrinsic_per_share=None`` + a warning naming the gap; the
router doesn't raise.
"""

from __future__ import annotations

import math
from typing import Optional

from aletheia.calculations.formulas import (
    cost_of_equity as _cost_of_equity,
    ddm_intrinsic_value as _ddm_intrinsic_value,
    ev_ebitda_decomposition as _ev_decompose,
    ev_ebitda_equity_value as _ev_ebitda_equity_value,
)
from aletheia.calculations.specialized_inputs import load_specialized_inputs
from aletheia.contracts.interfaces import CalculationInput
from aletheia.tools.valuation_engines.base import ValuationResult, scenario_band
from aletheia.calculations.macro import market_risk_premium as _mrp


_DEFAULT_MRP: float = _mrp()      # Damodaran implied ERP (single source); 4.75% fallback
_REQUIRED_PARAMS = ("target_ev_ebitda",)


class MlpEngine:
    """EV/EBITDA-anchored valuation for midstream MLPs, with a distribution
    cross-check."""

    def compute_intrinsic_value(
        self, calc_input: CalculationInput,
    ) -> ValuationResult:
        ticker = calc_input.classification.ticker
        fy = self._latest_fy(calc_input)

        inputs = load_specialized_inputs(ticker)
        if inputs is None or inputs.model != "mlp":
            return _empty(
                ticker, fy,
                warning=(f"No MLP inputs configured for {ticker} (model="
                         f"{inputs.model if inputs else 'missing'!r}). Add an "
                         f"\"mlp\" entry to specialized_valuation_inputs.json."),
                inputs_snapshot={"config_entry": None})

        if not inputs.has_required_inputs(*_REQUIRED_PARAMS):
            missing = [k for k in _REQUIRED_PARAMS if inputs.params.get(k) is None]
            return _empty(
                ticker, fy,
                warning=(f"MLP inputs incomplete for {ticker}: missing {missing}. "
                         f"Source: {inputs.source[:80]}..."),
                inputs_snapshot={"config_entry": inputs.params,
                                 "missing_keys": missing})

        # ── Frame inputs: EBITDA + net debt ──────────────────────
        ebitda = self._latest(calc_input, "derived_EBITDA")
        net_debt = self._latest(calc_input, "derived_NetDebt")
        if ebitda is None or ebitda <= 0:
            return _empty(
                ticker, fy,
                warning=(f"{ticker}: no positive EBITDA in frame "
                         f"({ebitda}); cannot apply an EV/EBITDA multiple."),
                inputs_snapshot={"config_entry": inputs.params, "ebitda": ebitda})
        if net_debt is None:
            return _empty(
                ticker, fy,
                warning=f"{ticker}: net debt unavailable; cannot bridge EV→equity.",
                inputs_snapshot={"config_entry": inputs.params})

        # ── Market context ───────────────────────────────────────
        market = _market(calc_input)
        rf = market.get("risk_free_rate")
        beta = market.get("beta")
        current_price = market.get("current_price")
        units = market.get("shares_diluted") or self._latest(calc_input, "raw_SharesDiluted")
        if not units or units <= 0:
            return _empty(
                ticker, fy,
                warning=f"{ticker}: unit count unavailable; cannot compute per-unit IV.",
                inputs_snapshot={"config_entry": inputs.params})

        target_mult = float(inputs.params["target_ev_ebitda"])

        # ── HEADLINE: EV/EBITDA → equity → per unit ──────────────
        equity_value = _ev_ebitda_equity_value(
            ebitda=ebitda, ev_ebitda_multiple=target_mult, net_debt=net_debt)
        ips = equity_value / units if equity_value is not None else None
        decomposition = _ev_decompose(
            ebitda=ebitda, ev_ebitda_multiple=target_mult,
            net_debt=net_debt, units=units)

        warnings = []
        ndte = decomposition.get("net_debt_to_ebitda") if decomposition else None
        if ndte is not None and ndte > 4.5:
            warnings.append(
                f"High leverage: net-debt/EBITDA {ndte:.1f}× — the equity is a "
                "thin residual on a large enterprise value; multiple sensitivity "
                "is amplified.")

        # ── Cost of equity (for the distribution cross-check) ────
        ke_override = inputs.params.get("cost_of_equity_override_pct")
        ke = (float(ke_override) / 100.0 if ke_override is not None
              else _cost_of_equity(risk_free_rate=rf, beta=beta,
                                   market_risk_premium=_DEFAULT_MRP))

        # ── CROSS-CHECK: two-stage distribution discount (income view) ──
        distribution_leg = None
        dpu = inputs.params.get("current_dpu")
        if dpu is not None and ke is not None:
            dpu = float(dpu)
            d_growth = float(inputs.params.get("distribution_growth_pct", 0.0)) / 100.0
            d_years = int(inputs.params.get("explicit_years", 5) or 5)
            d_terminal = float(inputs.params.get("terminal_growth_pct", 2.5)) / 100.0
            dist_ips = _ddm_intrinsic_value(
                current_dps=dpu, cost_of_equity=ke, explicit_growth=d_growth,
                explicit_years=d_years, terminal_growth=d_terminal)
            vs_headline = (dist_ips / ips - 1.0) if (dist_ips and ips) else None
            distribution_leg = {
                "method": "two-stage distribution discount (DPU)",
                "current_dpu": dpu,
                "distribution_growth": d_growth,
                "explicit_years": d_years,
                "terminal_growth": d_terminal,
                "cost_of_equity": ke,
                "intrinsic_per_unit": dist_ips,
                "distribution_yield": (dpu / current_price
                                       if current_price else None),
                "vs_headline_pct": vs_headline,
            }
            # A distribution-discount value far ABOVE the asset-based headline is
            # the MLP distribution-sustainability signal: the DDM takes the payout
            # at face value, but a levered midstream funds part of it with debt/
            # equity rather than covered cash. Trust the EV/EBITDA anchor.
            if vs_headline is not None and vs_headline > 0.25:
                warnings.append(
                    f"Distribution-discount value (${dist_ips:,.0f}/unit) sits "
                    f"{vs_headline:.0%} above the EV/EBITDA headline (${ips:,.0f}) — "
                    "the income view takes the payout at face value; for a levered "
                    "MLP the asset-based anchor is the conservative read.")

        # Surface the distribution cross-check inside the decomposition so it
        # rides the served `valuation_decomposition` path (the UI/report only
        # carry decomposition, not the full engine_specific).
        if decomposition is not None:
            decomposition["distribution_leg"] = distribution_leg

        mos = ((ips - current_price) / current_price
               if (ips and current_price and current_price > 0) else None)

        # Bull/bear band — flex the EV/EBITDA multiple (default ±1.0×), the
        # dominant valuation lever for a midstream name (peer-multiple dispersion).
        mult_spread = float(inputs.params.get("scenario_ev_ebitda_spread", 1.0))

        def _ips_at(mult):
            eq = _ev_ebitda_equity_value(
                ebitda=ebitda, ev_ebitda_multiple=mult, net_debt=net_debt)
            return (eq / units) if (eq is not None and units) else None

        band = scenario_band(
            recompute=_ips_at, bear_value=target_mult - mult_spread,
            bull_value=target_mult + mult_spread, current_price=current_price,
            driver="EV/EBITDA multiple", driver_unit="×")

        if inputs.analyst_notes:
            warnings.append(f"Analyst note: {inputs.analyst_notes[:200]}")

        return ValuationResult(
            ticker=ticker,
            fiscal_year=fy,
            engine="mlp",
            intrinsic_per_share=ips,
            equity_value=equity_value,
            current_price=current_price,
            margin_of_safety=mos,
            inputs_snapshot={
                "valuation_basis":   "EV/EBITDA multiple (equity = EV − net debt)",
                "ebitda":            ebitda,
                "target_ev_ebitda":  target_mult,
                "net_debt":          net_debt,
                "shares_diluted":    units,
                "cost_of_equity":    ke,
                "risk_free_rate":    rf,
                "beta":              beta,
                "mrp":               _DEFAULT_MRP,
                "ke_override_used":  ke_override is not None,
                "as_of_date":        inputs.as_of_date,
                "source":            inputs.source,
            },
            notes=(
                "Midstream MLP: EV/EBITDA multiple on stable fee-based EBITDA, "
                "net of debt → equity per unit (FCFF mis-frames the growth capex "
                "and hides the leverage). Distribution-discount leg is the income "
                "cross-check."
            ),
            warnings=warnings,
            engine_specific={
                "decomposition":     decomposition,
                "distribution_leg":  distribution_leg,
                "scenario_band":     band,
                "analyst_notes":     inputs.analyst_notes,
                "config_source":     inputs.source,
                "next_review_date":  inputs.next_review_date,
            },
        )

    # ── helpers ────────────────────────────────────────────────────

    def _latest_fy(self, calc_input: CalculationInput) -> int:
        df = getattr(calc_input, "df", None)
        if df is not None and "fiscal_year" in df.columns and not df.empty:
            return int(df["fiscal_year"].max())
        return 0

    def _latest(self, calc_input: CalculationInput, col: str) -> Optional[float]:
        df = getattr(calc_input, "df", None)
        if df is None or col not in getattr(df, "columns", []):
            return None
        s = df.sort_values("fiscal_year") if "fiscal_year" in df.columns else df
        for v in reversed(list(s[col])):
            try:
                if v is not None and not math.isnan(float(v)):
                    return float(v)
            except (TypeError, ValueError):
                continue
        return None


def _market(calc_input: CalculationInput) -> dict:
    market_data = (getattr(calc_input, "market_snapshot", None)
                   or getattr(calc_input, "market", None) or {})
    if hasattr(market_data, "__dict__"):
        market_data = market_data.__dict__
    ticker = calc_input.classification.ticker
    if not market_data:
        try:
            from aletheia.data.market_data import (
                get_current_price, get_shares_outstanding,
                get_risk_free_rate, get_beta,
            )
            return {
                "risk_free_rate": get_risk_free_rate(),
                "beta":           get_beta(ticker),
                "current_price":  get_current_price(ticker),
                "shares_diluted": get_shares_outstanding(ticker),
            }
        except Exception:
            return {}
    return {
        "risk_free_rate": market_data.get("risk_free_rate"),
        "beta":           market_data.get("beta"),
        "current_price":  market_data.get("current_price") or market_data.get("last_price"),
        "shares_diluted": market_data.get("shares_diluted") or market_data.get("shares"),
    }


def _empty(ticker: str, fy: int, *, warning: str, inputs_snapshot: dict) -> ValuationResult:
    return ValuationResult(
        ticker=ticker, fiscal_year=fy, engine="mlp",
        intrinsic_per_share=None, equity_value=None,
        current_price=inputs_snapshot.get("current_price"),
        margin_of_safety=None, inputs_snapshot=inputs_snapshot,
        notes="MLP valuation unavailable — see warnings.", warnings=[warning])


__all__ = ["MlpEngine"]
