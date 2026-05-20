"""Rate-base DCF engine — for ``routing_required`` filers (regulated utilities).

Phase A.3. Orchestrates the valuation by:

  1. Loading analyst-curated inputs (rate base, allowed ROE, growth
     trajectory) from ``config/specialized_valuation_inputs.json``
     via ``aletheia.calculations.specialized_inputs.load_specialized_inputs``.
  2. Computing cost of equity via the central ``cost_of_equity()``
     (CAPM: ``Rf + β × MRP``) using live market data (Rf from
     ``risk_free_rate``, β from market-data snapshot, MRP constant).
  3. Calling the central ``rate_base_equity_value()`` two-stage
     formula.
  4. Dividing by diluted shares for the per-share IV; computing MoS
     vs current price.

Zero formula logic in this module — every numeric step is a call to
``aletheia.calculations.formulas``. The Phase 5 architecture lock
enforces this.

Empty-state handling: when analyst inputs are missing
(``params.rate_base_billions is None``) or when the central formula
returns ``None`` (degenerate Ke ≤ terminal_g), the engine returns a
``ValuationResult`` with ``intrinsic_per_share=None`` plus
populated ``warnings``. The router doesn't raise; the empty-state
flows through to the serving report so the dashboard can show "no
IV — needs analyst input."
"""

from __future__ import annotations

from typing import Optional

from aletheia.calculations.formulas import (
    cost_of_equity as _cost_of_equity,
    rate_base_decomposition as _decompose,
    rate_base_equity_value as _rate_base_equity_value,
)
from aletheia.calculations.specialized_inputs import (
    load_specialized_inputs,
    SpecializedInputs,
)
from aletheia.contracts.interfaces import CalculationInput
from aletheia.tools.valuation_engines.base import ValuationResult


# Damodaran-aligned MRP. Same constant the DCFEngine uses (kept in
# ``aletheia.tools.dcf_engine.MARKET_RISK_PREMIUM``). Refactored
# upstream constants can replace this when the rate-base engine
# starts reading from a shared source.
_DEFAULT_MRP: float = 0.0475


# Required param keys per model — engine refuses to run if any
# is missing from the config. Keeps placeholder entries (Phase A.2
# stubs for JPM/UNH/etc.) from producing fake IVs.
_REQUIRED_PARAMS = (
    "rate_base_billions",
    "allowed_roe_pct",
    "explicit_growth_pct",
    "explicit_years",
    "terminal_growth_pct",
)


class RateBaseEngine:
    """Two-stage rate-base DCF for regulated utilities."""

    def compute_intrinsic_value(
        self, calc_input: CalculationInput,
    ) -> ValuationResult:
        ticker = calc_input.classification.ticker
        fy = self._latest_fy(calc_input)

        # ── Load analyst-curated inputs ───────────────────────────
        inputs = load_specialized_inputs(ticker)
        if inputs is None or inputs.model != "rate_base":
            return _empty_result(
                ticker, fy,
                warning=(
                    f"No rate-base inputs configured for {ticker}. "
                    f"Add an entry to "
                    f"config/specialized_valuation_inputs.json."
                ),
                inputs_snapshot={"config_entry": None},
            )

        if not inputs.has_required_inputs(*_REQUIRED_PARAMS):
            missing = [k for k in _REQUIRED_PARAMS
                       if inputs.params.get(k) is None]
            return _empty_result(
                ticker, fy,
                warning=(
                    f"Rate-base inputs incomplete for {ticker}: "
                    f"missing {missing}. "
                    f"Source citation: {inputs.source[:80]}..."
                ),
                inputs_snapshot={
                    "config_entry":  inputs.params,
                    "missing_keys":  missing,
                    "as_of_date":    inputs.as_of_date,
                },
            )

        # ── Convert percentages to decimals ───────────────────────
        rate_base = float(inputs.params["rate_base_billions"]) * 1e9
        allowed_roe = float(inputs.params["allowed_roe_pct"]) / 100.0
        explicit_growth = float(inputs.params["explicit_growth_pct"]) / 100.0
        explicit_years = int(inputs.params["explicit_years"])
        terminal_growth = float(inputs.params["terminal_growth_pct"]) / 100.0

        # ── Market context (Rf, β, current price, shares) ────────
        market = _extract_market_context(calc_input)
        rf = market.get("risk_free_rate")
        beta = market.get("beta")
        current_price = market.get("current_price")
        shares = market.get("shares_diluted")

        # ── Cost of equity via central CAPM formula ──────────────
        ke = _cost_of_equity(
            risk_free_rate=rf, beta=beta, market_risk_premium=_DEFAULT_MRP,
        )
        if ke is None:
            return _empty_result(
                ticker, fy,
                warning=(
                    f"Cannot compute cost of equity for {ticker}: "
                    f"missing risk_free_rate or beta in market data."
                ),
                inputs_snapshot={
                    "config_entry": inputs.params,
                    "rf": rf, "beta": beta,
                },
            )

        # ── Central rate-base formula ────────────────────────────
        equity_value = _rate_base_equity_value(
            rate_base=rate_base,
            allowed_roe=allowed_roe,
            cost_of_equity=ke,
            explicit_growth=explicit_growth,
            explicit_years=explicit_years,
            terminal_growth=terminal_growth,
        )
        if equity_value is None:
            return _empty_result(
                ticker, fy,
                warning=(
                    f"Rate-base formula returned None for {ticker} — "
                    f"Ke {ke:.4f} likely ≤ terminal_growth "
                    f"{terminal_growth:.4f}. Increase Ke (check β / "
                    f"MRP) or lower terminal_growth."
                ),
                inputs_snapshot={
                    "config_entry":      inputs.params,
                    "cost_of_equity":    ke,
                    "rf": rf, "beta": beta, "mrp": _DEFAULT_MRP,
                },
            )

        # ── Per-share IV + MoS ───────────────────────────────────
        ips: Optional[float] = None
        if shares and shares > 0:
            ips = equity_value / shares

        mos: Optional[float] = None
        if ips and current_price and current_price > 0:
            mos = (ips - current_price) / current_price

        # ── Decomposition for the audit / UI panel ───────────────
        breakdown = _decompose(
            rate_base=rate_base,
            allowed_roe=allowed_roe,
            cost_of_equity=ke,
            explicit_growth=explicit_growth,
            explicit_years=explicit_years,
            terminal_growth=terminal_growth,
        )

        warnings = []
        if inputs.analyst_notes:
            warnings.append(f"Analyst note: {inputs.analyst_notes[:200]}")

        return ValuationResult(
            ticker=ticker,
            fiscal_year=fy,
            engine="rate_base",
            intrinsic_per_share=ips,
            equity_value=equity_value,
            current_price=current_price,
            margin_of_safety=mos,
            inputs_snapshot={
                "rate_base":         rate_base,
                "allowed_roe":       allowed_roe,
                "explicit_growth":   explicit_growth,
                "explicit_years":    explicit_years,
                "terminal_growth":   terminal_growth,
                "cost_of_equity":    ke,
                "risk_free_rate":    rf,
                "beta":              beta,
                "mrp":               _DEFAULT_MRP,
                "shares_diluted":    shares,
                "as_of_date":        inputs.as_of_date,
                "source":            inputs.source,
            },
            notes=(
                "Two-stage rate-base DCF: explicit period uses "
                "analyst-supplied rate-plan growth; terminal period "
                "reverts to GDP-level perpetuity."
            ),
            warnings=warnings,
            engine_specific={
                "decomposition":     breakdown,
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


def _extract_market_context(calc_input: CalculationInput) -> dict:
    """Pull risk-free rate, beta, current price, and diluted shares
    from the calc input. Resilient to missing fields — engine treats
    any missing input as a None and produces an empty-state result
    via the cost_of_equity / has_required_inputs gates."""
    # CalculationInput's market context lives on various nested
    # attributes depending on how it was built. Probe the common
    # paths defensively.
    market_data = (
        getattr(calc_input, "market_snapshot", None)
        or getattr(calc_input, "market", None)
        or {}
    )
    if hasattr(market_data, "__dict__"):
        market_data = market_data.__dict__

    # Fall back to live fetch via the market_data module — same path
    # DCFEngine uses for current price + shares.
    ticker = calc_input.classification.ticker
    if not market_data:
        try:
            from aletheia.data.market_data import (
                get_current_price, get_market_cap, get_shares_outstanding,
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
    """Build the no-IV result. Engine never raises for data gaps —
    the empty-state result flows through to the serving report so
    the dashboard / thesis synthesizer can surface the gap."""
    return ValuationResult(
        ticker=ticker,
        fiscal_year=fy,
        engine="rate_base",
        intrinsic_per_share=None,
        equity_value=None,
        current_price=inputs_snapshot.get("current_price"),
        margin_of_safety=None,
        inputs_snapshot=inputs_snapshot,
        notes="Rate-base valuation unavailable — see warnings.",
        warnings=[warning],
    )


__all__ = ["RateBaseEngine"]
