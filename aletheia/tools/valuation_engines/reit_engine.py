"""REIT engine — for ``reit_required`` filers (equity REITs).

Phase A.9. REITs are not valued on FCFF: GAAP net income is depressed by
large non-cash real-estate depreciation, and the firm distributes most of
its cash, so the FCFF DCF both understates earnings and mis-frames the
capital return. The REIT-standard cash measure is AFFO (Adjusted Funds From
Operations) — FFO adjusted for maintenance capex / straight-line rent — which
is the REIT analog of free cash flow. This engine values the equity as a
two-stage AFFO-growth stream, reusing the central two-stage per-share formula
(``ddm_intrinsic_value`` is model-agnostic — it discounts any growing
per-share cash flow), fed with AFFO/share instead of dividends.

  1. Load analyst inputs (current AFFO/share, explicit growth, explicit
     years, terminal growth, optional Ke override) from
     ``config/specialized_valuation_inputs.json``.
  2. Compute Ke via central CAPM (Rf + β × MRP) unless overridden.
  3. Call central two-stage formula on AFFO/share for the per-share IV.
  4. Compute MoS vs current market price.

Zero formula logic — every numeric step delegates to
``aletheia.calculations.formulas``. Phase-5 architecture lock enforces this.

Empty-state handling: when AFFO/share is missing/≤0, or inputs are
incomplete, the engine returns ``intrinsic_per_share=None`` plus a warning
naming the gap. The router doesn't raise; the empty-state propagates to the
serving report so the dashboard shows "no IV — needs analyst REIT inputs".
"""

from __future__ import annotations

from typing import Optional

from aletheia.calculations.formulas import (
    cost_of_equity as _cost_of_equity,
    ddm_decomposition as _two_stage_decomposition,
    ddm_intrinsic_value as _two_stage_value,
)
from aletheia.calculations.specialized_inputs import load_specialized_inputs
from aletheia.contracts.interfaces import CalculationInput
from aletheia.tools.valuation_engines.base import ValuationResult


# Same MRP constant the other engines + DCFEngine use.
_DEFAULT_MRP: float = 0.0475

# Required params — engine refuses to run when any is null (placeholder
# config entries never produce a fabricated IV).
_REQUIRED_PARAMS = (
    "current_affo_per_share",
    "explicit_growth_pct",
    "explicit_years",
    "terminal_growth_pct",
)


class ReitEngine:
    """Two-stage AFFO-growth valuation for equity REITs."""

    def compute_intrinsic_value(
        self, calc_input: CalculationInput,
    ) -> ValuationResult:
        ticker = calc_input.classification.ticker
        fy = self._latest_fy(calc_input)

        # ── Analyst inputs ────────────────────────────────────────
        inputs = load_specialized_inputs(ticker)
        if inputs is None or inputs.model != "reit":
            return _empty_result(
                ticker, fy,
                warning=(
                    f"No REIT inputs configured for {ticker} (model="
                    f"{inputs.model if inputs else 'missing'!r}). Add a "
                    "model='reit' entry (AFFO/share + growth) to "
                    "config/specialized_valuation_inputs.json."
                ),
                inputs_snapshot={"config_entry": None},
            )

        if not inputs.has_required_inputs(*_REQUIRED_PARAMS):
            missing = [k for k in _REQUIRED_PARAMS if inputs.params.get(k) is None]
            return _empty_result(
                ticker, fy,
                warning=(
                    f"REIT inputs incomplete for {ticker}: missing {missing}. "
                    f"Source: {inputs.source[:80]}..."
                ),
                inputs_snapshot={
                    "config_entry": inputs.params,
                    "missing_keys": missing,
                    "as_of_date":   inputs.as_of_date,
                },
            )

        affo_ps = float(inputs.params["current_affo_per_share"])
        explicit_growth = float(inputs.params["explicit_growth_pct"]) / 100.0
        explicit_years = int(inputs.params["explicit_years"])
        terminal_growth = float(inputs.params["terminal_growth_pct"]) / 100.0

        if affo_ps <= 0:
            return _empty_result(
                ticker, fy,
                warning=(
                    f"{ticker} AFFO/share is {affo_ps} (≤0). AFFO must be "
                    "positive to value a REIT on a two-stage AFFO model."
                ),
                inputs_snapshot={"config_entry": inputs.params,
                                 "current_affo_per_share": affo_ps},
            )

        # ── Market context (Rf, β, current price, shares) ─────────
        market = _extract_market_context(calc_input)
        rf = market.get("risk_free_rate")
        beta = market.get("beta")
        current_price = market.get("current_price")

        ke_override_pct = inputs.params.get("cost_of_equity_override_pct")
        if ke_override_pct is not None:
            ke = float(ke_override_pct) / 100.0
        else:
            ke = _cost_of_equity(
                risk_free_rate=rf, beta=beta, market_risk_premium=_DEFAULT_MRP,
            )
        if ke is None:
            return _empty_result(
                ticker, fy,
                warning=(f"Cannot compute cost of equity for {ticker}: "
                         "missing risk_free_rate or beta in market data."),
                inputs_snapshot={"config_entry": inputs.params,
                                 "rf": rf, "beta": beta},
            )

        # ── Central two-stage formula, on AFFO/share ──────────────
        ips = _two_stage_value(
            current_dps=affo_ps,            # AFFO/share is the per-share cash flow
            cost_of_equity=ke,
            explicit_growth=explicit_growth,
            explicit_years=explicit_years,
            terminal_growth=terminal_growth,
        )
        if ips is None:
            return _empty_result(
                ticker, fy,
                warning=(
                    f"REIT formula returned None for {ticker} — most likely "
                    f"Ke {ke:.4f} ≤ terminal_growth {terminal_growth:.4f}. "
                    "Raise terminal_growth or set cost_of_equity_override_pct."
                ),
                inputs_snapshot={"config_entry": inputs.params,
                                 "cost_of_equity": ke, "rf": rf, "beta": beta},
            )

        mos: Optional[float] = None
        if current_price and current_price > 0:
            mos = (ips - current_price) / current_price

        breakdown = _two_stage_decomposition(
            current_dps=affo_ps, cost_of_equity=ke,
            explicit_growth=explicit_growth, explicit_years=explicit_years,
            terminal_growth=terminal_growth,
        )

        warnings = []
        if inputs.analyst_notes:
            warnings.append(f"Analyst note: {inputs.analyst_notes[:200]}")

        shares = market.get("shares_diluted")
        equity_value = ips * shares if (ips and shares) else None
        implied_p_affo = (ips / affo_ps) if affo_ps else None

        return ValuationResult(
            ticker=ticker,
            fiscal_year=fy,
            engine="reit",
            intrinsic_per_share=ips,
            equity_value=equity_value,
            current_price=current_price,
            margin_of_safety=mos,
            inputs_snapshot={
                "current_affo_per_share": affo_ps,
                "explicit_growth":        explicit_growth,
                "explicit_years":         explicit_years,
                "terminal_growth":        terminal_growth,
                "cost_of_equity":         ke,
                "risk_free_rate":         rf,
                "beta":                   beta,
                "mrp":                    _DEFAULT_MRP,
                "ke_override_used":       ke_override_pct is not None,
                "shares_diluted":         shares,
                "as_of_date":             inputs.as_of_date,
                "source":                 inputs.source,
            },
            notes=(
                "Two-stage AFFO-growth model (REIT analog of a DCF on FCF): "
                "explicit AFFO/share growth period + terminal Gordon perpetuity."
            ),
            warnings=warnings,
            engine_specific={
                "affo_per_share":     affo_ps,
                "implied_p_affo":     implied_p_affo,
                "decomposition":      breakdown,
                "analyst_notes":      inputs.analyst_notes,
                "config_source":      inputs.source,
                "next_review_date":   inputs.next_review_date,
            },
        )

    def _latest_fy(self, calc_input: CalculationInput) -> int:
        df = getattr(calc_input, "df", None)
        if df is not None and "fiscal_year" in df.columns and not df.empty:
            return int(df["fiscal_year"].max())
        return 0


def _extract_market_context(calc_input: CalculationInput) -> dict:
    """Probe the calc_input's nested market context; fall back to live fetch.
    Same defensive lookup the DDM / rate-base engines use."""
    market_data = (
        getattr(calc_input, "market_snapshot", None)
        or getattr(calc_input, "market", None)
        or {}
    )
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
                "risk_free_rate":  get_risk_free_rate(),
                "beta":            get_beta(ticker),
                "current_price":   get_current_price(ticker),
                "shares_diluted":  get_shares_outstanding(ticker),
            }
        except Exception:
            return {}

    return {
        "risk_free_rate":  market_data.get("risk_free_rate"),
        "beta":            market_data.get("beta"),
        "current_price":   market_data.get("current_price") or market_data.get("last_price"),
        "shares_diluted":  market_data.get("shares_diluted") or market_data.get("shares"),
    }


def _empty_result(
    ticker: str, fy: int, *, warning: str, inputs_snapshot: dict,
) -> ValuationResult:
    return ValuationResult(
        ticker=ticker,
        fiscal_year=fy,
        engine="reit",
        intrinsic_per_share=None,
        equity_value=None,
        current_price=inputs_snapshot.get("current_price"),
        margin_of_safety=None,
        inputs_snapshot=inputs_snapshot,
        notes="REIT valuation unavailable — see warnings.",
        warnings=[warning],
    )


__all__ = ["ReitEngine"]
