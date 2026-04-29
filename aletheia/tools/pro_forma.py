"""
proforma.py
Sector-agnostic, investment-grade DCF / Pro-Forma engine.

Drop-in replacement for your existing ProFormaEngine (same constructor + generate_forecast()).

Key upgrades vs V1:
- Computes FCFF from EBIT (NOPAT), not from Net Income / Interest (enterprise DCF is unlevered).
- Margin-driven (EBIT margin) core, with optional cost-stack (cogs% + sga% + da%) fallback.
- Hard validation gates (fail loudly) + diagnostics + warnings.
- Upside is NOT computed here (needs market price + shares); keep that upstream or add it explicitly.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional
import math
from aletheia.utils.tracing import tracer



class DCFValidationError(ValueError):
    """Raised when inputs fail hard validation gates."""


@dataclass(frozen=True)
class ForecastRow:
    year: int
    revenue: float
    revenue_growth: float
    ebit_margin: float
    ebit: float
    nopat: float
    depreciation: float
    capex: float
    change_in_wc: float
    fcff: float
    discount_factor: float
    pv_fcff: float

    def to_dict(self) -> Dict[str, float]:
        return {
            "year": float(self.year),
            "revenue": float(self.revenue),
            "revenue_growth": float(self.revenue_growth),
            "ebit_margin": float(self.ebit_margin),
            "ebit": float(self.ebit),
            "nopat": float(self.nopat),
            "depreciation": float(self.depreciation),
            "capex": float(self.capex),
            "change_in_wc": float(self.change_in_wc),
            "fcff": float(self.fcff),
            "discount_factor": float(self.discount_factor),
            "pv_fcff": float(self.pv_fcff),
        }


class ProFormaEngine:
    """
    Sector-agnostic pro-forma + DCF engine that produces intrinsic Enterprise Value (EV)
    and Equity Value (EV - net_debt), plus year-by-year projections.

    Expected base_year_financials (dict) - flexible keys supported:
      - net_sales or revenue
      - ebit (preferred) OR (cogs + sga + da) to derive EBIT
      - da or depreciation
      - capex
      - change_in_wc (optional; if missing uses wc_change_percent_sales assumption)
      - net_debt (preferred) OR (debt/cash) if you want to compute upstream
      - interest_expense is ignored for FCFF (kept only if you want net income reporting)

    Expected assumptions (dict):
      - wacc (required)
      - tax_rate (required)
      - terminal_growth_rate (required)
      - revenue_growth_initial (default 0.05)
      - revenue_growth_decay (default 0.01)

      Operating profitability driver:
        A) Margin-driven (recommended):
           - ebit_margin_initial (optional; derived from base ebit / revenue if missing)
           - ebit_margin_target (optional)
           - ebit_margin_convergence_years (default = projection_years)

        B) Cost-stack-driven (optional fallback):
           - cogs_percent_sales
           - sga_percent_sales
           - da_percent_sales
           In this mode base EBIT margin can be inferred:
             EBIT margin = 1 - cogs% - sga% - da%

      Reinvestment:
        - capex_percent_sales (optional; else scales base capex with revenue if base capex provided)
        - da_percent_sales (optional; else scales base da with revenue if base da provided)
        - wc_change_percent_sales (optional; default 0.0)
    """

    def __init__(self, base_year_financials: Dict[str, float], assumptions: Dict[str, float]):
        self.base: Dict[str, float] = base_year_financials or {}
        self.assumptions: Dict[str, float] = assumptions or {}

    # -------------------------
    # Public API
    # -------------------------
    def generate_forecast(self, projection_years: int = 5) -> Dict[str, Any]:
        # Only start a new trace if we aren't already inside one (to avoid clearing logs)
        if not tracer.current_trace_id:
            tracer.start_trace(f"ProForma_DCF_{projection_years}yr")
            
        if projection_years <= 0:
            raise DCFValidationError("projection_years must be >= 1.")

        a = self.assumptions
        warnings: List[str] = []
        
        tracer.log_step("ProForma_Init", {"base": self.base, "assumptions": a}, {})


        # ---- Parse base inputs (flexible keying) ----
        revenue0 = float(self.base.get("net_sales", self.base.get("revenue", 0.0)) or 0.0)
        if revenue0 <= 0:
            raise DCFValidationError("Base revenue (net_sales or revenue) must be provided and > 0.")

        # Base year D&A / Capex
        da0 = self._get_first_present(self.base, ["da", "depreciation"], default=None)
        capex0 = self.base.get("capex", None)

        # Base year EBIT (preferred)
        ebit0 = self.base.get("ebit", None)
        if ebit0 is None:
            # Try to derive EBIT if a cost stack exists
            cogs0 = self.base.get("cogs", None)
            sga0 = self.base.get("sga", None)
            if cogs0 is not None and sga0 is not None and da0 is not None:
                ebit0 = float(revenue0) - float(cogs0) - float(sga0) - float(da0)
                warnings.append("Base EBIT derived from revenue - cogs - sga - da (verify mapping).")

        # ---- Required assumptions ----
        wacc = float(a.get("wacc", 0.0) or 0.0)
        tax_rate = float(a.get("tax_rate", 0.0) or 0.0)
        g_term = float(a.get("terminal_growth_rate", 0.0) or 0.0)
        self._validate_core(wacc, tax_rate, g_term)

        # ---- Growth path ----
        g0 = float(a.get("revenue_growth_initial", 0.05))
        decay = float(a.get("revenue_growth_decay", 0.01))
        if decay < 0:
            raise DCFValidationError("revenue_growth_decay must be >= 0.")
        growth_path = self._build_growth_path(g0, decay, g_term, projection_years)

        # ---- Operating profitability: margin-driven by default ----
        base_margin: Optional[float] = None
        if ebit0 is not None:
            base_margin = float(ebit0) / float(revenue0)
        else:
            if "ebit_margin_initial" in a and a.get("ebit_margin_initial") is not None:
                base_margin = float(a["ebit_margin_initial"])

        if base_margin is None:
            # last resort: infer from cost stack assumptions
            cogs_pct = a.get("cogs_percent_sales", None)
            sga_pct = a.get("sga_percent_sales", None)
            da_pct_for_margin = a.get("da_percent_sales", None)
            if cogs_pct is not None and sga_pct is not None and da_pct_for_margin is not None:
                base_margin = 1.0 - float(cogs_pct) - float(sga_pct) - float(da_pct_for_margin)
                warnings.append("Base EBIT margin derived from (1 - cogs% - sga% - da%) (verify for your sector).")

        if base_margin is None:
            raise DCFValidationError(
                "Provide base EBIT (preferred) or ebit_margin_initial, or a full cost stack % (cogs%, sga%, da%)."
            )

        self._validate_margin(base_margin)

        # Margin convergence (optional)
        margin_target = a.get("ebit_margin_target", None)
        conv_years = int(a.get("ebit_margin_convergence_years", projection_years) or projection_years)
        margin_path = self._build_margin_path(base_margin, margin_target, conv_years, projection_years)

        # ---- Reinvestment drivers ----
        da_pct = a.get("da_percent_sales", None)
        capex_pct = a.get("capex_percent_sales", None)
        wc_pct = float(a.get("wc_change_percent_sales", 0.0) or 0.0)

        use_pct_sales = (da_pct is not None) or (capex_pct is not None)

        if not use_pct_sales:
            # fallback: scale base D&A and Capex with revenue (requires base da/capex)
            if da0 is None or capex0 is None:
                raise DCFValidationError(
                    "Missing reinvestment inputs: provide (da_percent_sales and/or capex_percent_sales), "
                    "or provide base-year da/depreciation and capex to scale with revenue."
                )
            warnings.append(
                "Reinvestment modeled by scaling base D&A and Capex with revenue (percent-of-sales not provided)."
            )

        # If ratios are partially missing, attempt to infer from base year
        if use_pct_sales:
            if da_pct is None:
                if da0 is not None:
                    da_pct = float(da0) / float(revenue0)
                    warnings.append("da_percent_sales inferred from base-year D&A / revenue.")
                else:
                    da_pct = 0.0
                    warnings.append("da_percent_sales missing and base D&A missing; defaulting D&A to 0.")
            if capex_pct is None:
                if capex0 is not None:
                    capex_pct = float(capex0) / float(revenue0)
                    warnings.append("capex_percent_sales inferred from base-year Capex / revenue.")
                else:
                    capex_pct = 0.0
                    warnings.append("capex_percent_sales missing and base Capex missing; defaulting Capex to 0.")

        # ---- Forecast loop (FCFF from EBIT) ----
        projections: List[ForecastRow] = []
        revenue = float(revenue0)
        pv_sum = 0.0

        # ---- Audit Metrics Calculation Info ----
        ic_t = float(self.base.get("invested_capital", 0.0) or (revenue0 * 0.7)) # Fallback crude proxy
        ic_history = [ic_t]
        roic_path = []

        for t in range(1, projection_years + 1):
            revenue *= (1.0 + growth_path[t - 1])

            ebit_margin_t = float(margin_path[t - 1])
            ebit_t = revenue * ebit_margin_t

            # NOPAT (operating taxes, enterprise DCF)
            nopat_t = ebit_t * (1.0 - tax_rate)

            # Reinvestment
            if use_pct_sales:
                da_t = revenue * float(da_pct)  # type: ignore[arg-type]
                capex_t = revenue * float(capex_pct)  # type: ignore[arg-type]
                wc_t = revenue * float(wc_pct)
            else:
                scale = revenue / float(revenue0)
                da_t = float(da0) * scale  # type: ignore[arg-type]
                capex_t = float(capex0) * scale  # type: ignore[arg-type]
                # if base WC explicitly provided, scale it; else default 0
                if "change_in_wc" in self.base and self.base.get("change_in_wc") is not None:
                    wc_t = float(self.base.get("change_in_wc", 0.0) or 0.0) * scale
                else:
                    wc_t = 0.0

            # FCFF
            fcff_t = nopat_t + da_t - capex_t - wc_t

            # --- ROIC Audit & Optimism Tax (Task 2) ---
            reinvestment_t = (capex_t - da_t + wc_t)
            
            # ROIC = NOPAT_t / IC_{t-1}
            prev_ic = ic_history[-1]
            roic_t = nopat_t / prev_ic if prev_ic > 0 else 0.0
            roic_path.append(roic_t)

            # Optimism Tax Logic
            # Check for historical ROIC ceiling
            hist_roic_max = float(self.base.get("historical_roic_max", a.get("historical_roic_max", 0.50)) or 0.50)
            roic_threshold = hist_roic_max * 1.20
            
            optimism_tax = 0.0
            if roic_t > roic_threshold:
               # We are over-earning relative to capital base. 
               # Force higher reinvestment to dampen perceived efficiency.
               # How much extra capital is needed to bring ROIC down to threshold?
               # Target ROIC = Threshold. Target IC = NOPAT / Threshold.
               # Required Extra IC = Target IC - Prev IC.
               # But we can't change Prev IC. We treat it as a "Tax" on FCF this year.
               # "Optimism Tax" = (ROIC - Threshold) * Prev_IC
               optimism_tax = (roic_t - roic_threshold) * prev_ic
               warnings.append(f"Year {t}: ROIC {roic_t:.1%} > {roic_threshold:.1%}. Optimism Tax: ${optimism_tax:,.0f} applied.")
               
            # Apply Tax to Reinvestment (Reduces FCF, Increases IC for next year)
            reinvestment_t += optimism_tax
            fcff_t -= optimism_tax

            # Update IC (IC Growth Engine - Task 1)
            ic_t = prev_ic + reinvestment_t
            ic_history.append(ic_t)
            # ------------------

            df = 1.0 / ((1.0 + wacc) ** t)
            pv_fcff_t = fcff_t * df
            pv_sum += pv_fcff_t

            projections.append(
                ForecastRow(
                    year=t,
                    revenue=revenue,
                    revenue_growth=float(growth_path[t - 1]),
                    ebit_margin=ebit_margin_t,
                    ebit=ebit_t,
                    nopat=nopat_t,
                    depreciation=da_t,
                    capex=capex_t,
                    change_in_wc=wc_t,
                    fcff=fcff_t,
                    discount_factor=df,
                    pv_fcff=pv_fcff_t,
                )
            )

        # ---- Economic Gravity: Terminal Year Check (Task 2) ----
        # g = ROIC * RR  =>  RR_required = g / ROIC
        # We check Year 5 (Terminal inputs).
        
        last_nopat = projections[-1].nopat
        last_ic_start = ic_history[-2] 
        roic_term = last_nopat / last_ic_start if last_ic_start > 0 else 0.0
        
        explicit_rr_required = 0.0
        gravity_adjustment = 0.0
        
        if roic_term > 0 and g_term > 0:
            # Reinvestment Rate required to sustain g given ROIC
            rr_required = g_term / roic_term
            
            # Actual Reinvestment in Year 5
            last_reinvest = (projections[-1].capex - projections[-1].depreciation + projections[-1].change_in_wc)
            rr_actual = last_reinvest / last_nopat if last_nopat > 0 else 0.0
            
            if rr_actual < rr_required:
                # We are faking growth! Force reinvestment up.
                reinvest_needed = last_nopat * rr_required
                gravity_adjustment = reinvest_needed - last_reinvest
                
                # Adjust Year 5 FCFF logic (downward)
                projections[-1] = replace(
                    projections[-1], 
                    fcff=projections[-1].fcff - gravity_adjustment,
                    pv_fcff=(projections[-1].fcff - gravity_adjustment) * projections[-1].discount_factor
                )
                warnings.append(
                    f"⚠️ Economic Gravity: Year 5 Reinvestment too low (RR {rr_actual:.1%} < Required {rr_required:.1%}). "
                    f"Adjusted FCFF down by ${gravity_adjustment:,.0f}."
                )
                
                # Recalculate PV Sum for Year 5
                old_y5_pv = (projections[-1].fcff + gravity_adjustment) * projections[-1].discount_factor
                new_y5_pv = projections[-1].pv_fcff
                pv_sum = pv_sum - old_y5_pv + new_y5_pv
                
                # Terminal Reconciliation (Task 3)
                # Ensure the gravity adjustment (which is extra reinvestment) updates the final IC.
                # ic_history[-1] holds the IC at END of Year 5.
                ic_history[-1] += gravity_adjustment

        # ---- Terminal value ----
        last_fcff = projections[-1].fcff
        fcff_next = last_fcff * (1.0 + g_term)

        denom = (wacc - g_term)
        if denom <= 0:
            raise DCFValidationError("Invalid terminal value: WACC must be greater than terminal growth rate.")

        terminal_value = fcff_next / denom
        discounted_terminal_value = terminal_value / ((1.0 + wacc) ** projection_years)

        enterprise_value = pv_sum + discounted_terminal_value

        # Equity bridge (expects net_debt = debt - cash)
        net_debt = float(self.base.get("net_debt", 0.0) or 0.0)
        equity_value = enterprise_value - net_debt

        # Sector check (Task 3)
        sector_type = a.get("sector_type", "General")
        if sector_type == "Float_Heavy":
             # Interest handled as operating - Assuming NOPAT derivation correct (EBIT is Net Operating)
             # Just ensures we don't accidentally try to 'unlever' NOPAT further if logic existed.
             pass

        # ---- Diagnostics ----
        tv_pct = discounted_terminal_value / enterprise_value if enterprise_value != 0 else math.nan
        if not math.isnan(tv_pct) and tv_pct > 0.75:
            warnings.append(
                f"Terminal value contributes {tv_pct:.0%} of EV (high). Consider longer explicit forecast or revise drivers."
            )

        implied_terminal_multiple_fcf = (terminal_value / last_fcff) if last_fcff != 0 else math.nan
        implied_terminal_multiple_nopat = (
            terminal_value / projections[-1].nopat if projections[-1].nopat != 0 else math.nan
        )

        reinvestment_rate = None
        if projections[-1].nopat != 0:
            reinvestment_rate = (
                (projections[-1].capex - projections[-1].depreciation + projections[-1].change_in_wc)
                / projections[-1].nopat
            )
            
        # ROIC Optimism Check (Task 4)
        if len(roic_path) > 1 and (roic_path[-1] - roic_path[0]) > 0.05:
            warnings.append("WARNING: Economically Optimistic. ROIC expands > 500bps over forecast.")

        result = {
            "enterprise_value": float(enterprise_value),
            "equity_value": float(equity_value),
            "terminal_value": float(terminal_value),
            "discounted_terminal_value": float(discounted_terminal_value),
            "sum_pv_cash_flows": float(pv_sum),
            "audit_metrics": {
                 "roic_path": [float(r) for r in roic_path],
                 "invested_capital_path": [float(i) for i in ic_history],
                 "gravity_adjustment": float(gravity_adjustment)
            },
            "projections": [r.to_dict() for r in projections],
            "diagnostics": {
                "base_revenue": float(revenue0),
                "base_ebit": float(ebit0) if ebit0 is not None else None,
                "base_ebit_margin": float(base_margin),
                "terminal_value_pct_of_ev": float(tv_pct) if not math.isnan(tv_pct) else None,
                "implied_terminal_multiple_fcf": (
                    float(implied_terminal_multiple_fcf) if not math.isnan(implied_terminal_multiple_fcf) else None
                ),
                "implied_terminal_multiple_nopat": (
                    float(implied_terminal_multiple_nopat) if not math.isnan(implied_terminal_multiple_nopat) else None
                ),
                "reinvestment_rate_terminal_year": float(reinvestment_rate) if reinvestment_rate is not None else None,
                "assumptions_used": {
                    "wacc": float(wacc),
                    "tax_rate": float(tax_rate),
                    "terminal_growth_rate": float(g_term),
                    "revenue_growth_initial": float(g0),
                    "revenue_growth_decay": float(decay),
                    "ebit_margin_target": float(margin_target) if margin_target is not None else None,
                    "ebit_margin_convergence_years": int(conv_years),
                    "da_percent_sales": float(da_pct) if da_pct is not None else None,
                    "capex_percent_sales": float(capex_pct) if capex_pct is not None else None,
                    "wc_change_percent_sales": float(wc_pct),
                },
                "warnings": warnings,
            },
        }
        
        # Log the full result including year-by-year projections
        tracer.log_step("ProForma_Complete", {}, result)
        return result


    # -------------------------
    # Helpers / validation
    # -------------------------
    @staticmethod
    def _get_first_present(d: Dict[str, Any], keys: List[str], default: Any = None) -> Any:
        for k in keys:
            if k in d and d[k] is not None:
                return d[k]
        return default

    @staticmethod
    def _validate_core(wacc: float, tax_rate: float, g_term: float) -> None:
        if wacc <= 0:
            raise DCFValidationError("WACC must be provided and > 0.")
        if not (0.0 <= tax_rate <= 0.50):
            raise DCFValidationError(f"Tax rate out of bounds: {tax_rate}. Expected within [0.0, 0.50].")
        if not (-0.02 <= g_term <= 0.05):
            raise DCFValidationError(f"Terminal growth rate out of bounds: {g_term}. Expected within [-0.02, 0.05].")
        if (wacc - g_term) < 0.01:
            raise DCFValidationError(
                f"Terminal spread too small: WACC - g = {wacc - g_term:.3f}. Require >= 0.01."
            )

    @staticmethod
    def _validate_margin(m: float) -> None:
        if not (-0.50 <= float(m) <= 0.60):
            raise DCFValidationError(f"EBIT margin out of bounds: {m}. Expected within [-0.50, 0.60].")

    @staticmethod
    def _build_growth_path(g0: float, decay: float, g_floor: float, years: int) -> List[float]:
        path: List[float] = []
        g = float(g0)
        for _ in range(years):
            path.append(g)
            g = max(float(g_floor), g - float(decay))
        return path

    @staticmethod
    def _build_margin_path(base: float, target: Optional[float], conv_years: int, years: int) -> List[float]:
        if target is None:
            return [float(base) for _ in range(years)]

        base_f = float(base)
        target_f = float(target)
        n = max(1, int(conv_years))
        path: List[float] = []

        for t in range(1, years + 1):
            if t <= n:
                m = base_f + (target_f - base_f) * (t / n)
            else:
                m = target_f
            # soft clamp
            m = min(max(m, -0.50), 0.60)
            path.append(float(m))
        return path
