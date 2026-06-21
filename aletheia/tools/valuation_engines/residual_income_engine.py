"""Residual-income engine — for ``residual_income_required`` filers.

Phase A.11. For no-dividend float / managed-care names (Centene) where:
  - DDM is undefined (no distribution), and
  - FCFF mis-frames the business — a thin operating margin on a huge, mostly
    pass-through premium-revenue base, combined with a low WACC, makes the FCFF
    terminal value explode (CNC FCFF ≈ $422 vs a ~$61 price).

Residual income values equity off BOOK + the PV of returns earned ABOVE the cost
of equity, on a NORMALIZED (ex-impairment) ROE — the right frame for a
balance-sheet-driven float business:

    IV = BVPS₀ + Σ PV[(ROE_norm − Ke)·BVPSₜ₋₁] + PV[terminal residual income]

Zero formula logic here — the math lives in
``aletheia.calculations.formulas.residual_income`` (shared with the bank
convergent-set diagnostic). The headline is the two-stage residual income; the
justified-P/B steady-state value is reported as a cross-check.

Normalized ROE resolution (the crux — GAAP ROE is impairment-distorted, e.g.
CNC's −30% FY2025): analyst ``normalized_roe_pct`` from config is primary; the
fallback is the mean of POSITIVE historical ROE (loss / impairment years
excluded). Empty-state when neither is available.
"""

from __future__ import annotations

import math
from typing import Optional

from aletheia.calculations.formulas import (
    cost_of_equity as _cost_of_equity,
    justified_pb as _justified_pb,
    residual_income_value as _residual_income_value,
)
from aletheia.calculations.specialized_inputs import load_specialized_inputs
from aletheia.contracts.interfaces import CalculationInput
from aletheia.tools.valuation_engines.base import ValuationResult, scenario_band
from aletheia.calculations.macro import market_risk_premium as _mrp


_DEFAULT_MRP: float = _mrp()      # Damodaran implied ERP (single source); 4.75% fallback
_MIN_KE_MINUS_G: float = 0.015
_DEFAULT_EXPLICIT_YEARS: int = 5
_DEFAULT_TERMINAL_GROWTH: float = 0.025
_REQUIRED_PARAMS = ()      # ROE has a deterministic fallback; nothing is hard-required


class ResidualIncomeEngine:
    """Two-stage residual income on a normalized ROE for no-dividend float
    businesses (managed care)."""

    def compute_intrinsic_value(
        self, calc_input: CalculationInput,
    ) -> ValuationResult:
        ticker = calc_input.classification.ticker
        fy = self._latest_fy(calc_input)
        inputs = load_specialized_inputs(ticker)
        params = (getattr(inputs, "params", None) or {}) if inputs else {}

        # ── Book value per share (the anchor) ────────────────────
        market = _market(calc_input)
        shares = (market.get("shares_diluted")
                  or self._latest(calc_input, "raw_SharesDiluted")
                  or self._latest(calc_input, "clean_SharesDiluted"))
        equity = self._latest(calc_input, "raw_TotalEquity")
        bvps0 = params.get("book_value_per_share")
        if bvps0 is None and equity and shares and shares > 0:
            bvps0 = equity / shares
        if bvps0 is None or bvps0 <= 0:
            return _empty(ticker, fy,
                          warning=f"{ticker}: no positive book value per share; "
                                  "residual income needs a book anchor.",
                          inputs_snapshot={"config_entry": params})

        # ── Normalized ROE (config primary, ex-impairment fallback) ──
        roe_norm = None
        roe_source = None
        if params.get("normalized_roe_pct") is not None:
            roe_norm = float(params["normalized_roe_pct"]) / 100.0
            roe_source = "config normalized_roe_pct"
        else:
            roe_norm, n = self._ex_impairment_roe(calc_input)
            roe_source = f"ex-impairment {n}yr avg of positive ROE" if roe_norm else None
        if roe_norm is None or roe_norm <= 0:
            return _empty(ticker, fy,
                          warning=(f"{ticker}: cannot resolve a normalized ROE "
                                   "(no config value and no positive historical "
                                   "ROE to average). GAAP ROE is impairment-distorted."),
                          inputs_snapshot={"config_entry": params})

        # ── Retention / Ke / terminal assumptions ────────────────
        payout = float(params.get("payout_ratio_pct", 0.0) or 0.0) / 100.0
        retention = max(0.0, min(1.0, 1.0 - payout))
        explicit_years = int(params.get("explicit_years") or _DEFAULT_EXPLICIT_YEARS)

        rf = market.get("risk_free_rate")
        beta = market.get("beta")
        current_price = market.get("current_price")
        ke_override = params.get("cost_of_equity_override_pct")
        ke = (float(ke_override) / 100.0 if ke_override is not None
              else _cost_of_equity(risk_free_rate=rf, beta=beta,
                                   market_risk_premium=_DEFAULT_MRP))
        if ke is None:
            return _empty(ticker, fy,
                          warning=f"{ticker}: cannot compute Ke (missing rf/beta).",
                          inputs_snapshot={"config_entry": params,
                                           "rf": rf, "beta": beta})

        terminal_growth = float(params.get("terminal_growth_pct", _DEFAULT_TERMINAL_GROWTH * 100)) / 100.0
        terminal_growth = max(0.0, min(terminal_growth, ke - _MIN_KE_MINUS_G))
        terminal_roe = roe_norm

        # ── Headline: two-stage residual income ──────────────────
        ri = _residual_income_value(
            bvps0=bvps0, roe=roe_norm, ke=ke, retention=retention,
            explicit_years=explicit_years, terminal_roe=terminal_roe,
            terminal_growth=terminal_growth)
        ips = ri["iv"]
        near_term_g = ri["near_term_growth"]

        # ── Cross-check: steady-state justified P/B on today's book ──
        jpb_mult = _justified_pb(roe=terminal_roe, ke=ke, growth=terminal_growth)
        iv_jpb_steady = (jpb_mult * bvps0) if jpb_mult is not None else None

        equity_value = ips * shares if shares else None
        mos = ((ips - current_price) / current_price
               if (ips and current_price and current_price > 0) else None)

        warnings = []
        if near_term_g >= ke:
            warnings.append(
                f"Near-term g = ROE·retention = {near_term_g:.1%} ≥ Ke {ke:.1%}: "
                "two-stage residual income (super-growth explicit phase fading to "
                "a terminal g < Ke) is the well-posed form; single-stage P/B undefined.")
        if inputs and getattr(inputs, "analyst_notes", None):
            warnings.append(f"Analyst note: {inputs.analyst_notes[:200]}")

        decomposition = dict(ri["decomposition"])
        decomposition.update({
            "iv_residual_income": ips,
            "implied_pb": ri["implied_pb"],
            "justified_pb_multiple": jpb_mult,
            "iv_justified_pb_steady": iv_jpb_steady,
            "near_term_growth": near_term_g,
            "roe_normalized": roe_norm,
            "ke": ke,
            "terminal_growth": terminal_growth,
        })

        # Bull/bear band — flex the dominant driver (normalized ROE) by a
        # configurable spread (default ±2pp), Ke fixed. ROE is the uncertain
        # operating lever; CNC's whole case turns on whether ROE clears Ke.
        roe_spread = float(params.get("scenario_roe_spread_pct", 2.0)) / 100.0

        def _ri_at(roe_val):
            r = _residual_income_value(
                bvps0=bvps0, roe=roe_val, ke=ke, retention=retention,
                explicit_years=explicit_years, terminal_roe=roe_val,
                terminal_growth=terminal_growth)
            return r["iv"]

        band = scenario_band(
            recompute=_ri_at, bear_value=roe_norm - roe_spread,
            bull_value=roe_norm + roe_spread, current_price=current_price,
            driver="normalized ROE", driver_unit="pp")

        return ValuationResult(
            ticker=ticker,
            fiscal_year=fy,
            engine="residual_income",
            intrinsic_per_share=ips,
            equity_value=equity_value,
            current_price=current_price,
            margin_of_safety=mos,
            inputs_snapshot={
                "valuation_basis":   "two-stage residual income on normalized ROE",
                "book_value_per_share": bvps0,
                "roe_normalized":    roe_norm,
                "roe_source":        roe_source,
                "payout":            payout,
                "retention":         retention,
                "explicit_years":    explicit_years,
                "terminal_growth":   terminal_growth,
                "cost_of_equity":    ke,
                "risk_free_rate":    rf,
                "beta":              beta,
                "mrp":               _DEFAULT_MRP,
                "ke_override_used":  ke_override is not None,
                "shares_diluted":    shares,
                "as_of_date":        getattr(inputs, "as_of_date", None),
                "source":            getattr(inputs, "source", None),
            },
            notes=(
                "Two-stage residual income (BVPS + PV of returns above Ke) on a "
                "NORMALIZED, ex-impairment ROE — the frame for a no-dividend "
                "float/managed-care business where DDM is undefined and FCFF "
                "mis-frames the thin-margin pass-through revenue."
            ),
            warnings=warnings,
            engine_specific={
                "decomposition":    decomposition,
                "scenario_band":    band,
                "analyst_notes":    getattr(inputs, "analyst_notes", None),
                "config_source":    getattr(inputs, "source", None),
                "next_review_date": getattr(inputs, "next_review_date", None),
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

    def _ex_impairment_roe(self, calc_input: CalculationInput, n: int = 5):
        """Mean of the last ``n`` POSITIVE historical ROEs — loss / impairment
        years (negative ROE) are excluded so a one-off writedown can't poison
        the normalized figure. Returns (roe, n_used) or (None, 0)."""
        df = getattr(calc_input, "df", None)
        if df is None or "derived_ROE" not in getattr(df, "columns", []):
            return None, 0
        vals = []
        for x in df.sort_values("fiscal_year")["derived_ROE"]:
            try:
                f = float(x)
                if not math.isnan(f) and f > 0:
                    vals.append(f)
            except (TypeError, ValueError):
                continue
        if not vals:
            return None, 0
        window = vals[-n:]
        return sum(window) / len(window), len(window)


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
        ticker=ticker, fiscal_year=fy, engine="residual_income",
        intrinsic_per_share=None, equity_value=None,
        current_price=inputs_snapshot.get("current_price"),
        margin_of_safety=None, inputs_snapshot=inputs_snapshot,
        notes="Residual-income valuation unavailable — see warnings.",
        warnings=[warning])


__all__ = ["ResidualIncomeEngine"]
