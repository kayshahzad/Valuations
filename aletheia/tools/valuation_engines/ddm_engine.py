"""Dividend Discount engine — for ``ddm_required`` filers.

Phase A.7. Orchestrates two-stage DDM valuation:

  1. Load analyst inputs (current DPS, explicit growth, terminal
     growth, optional cost-of-equity override) from
     ``config/specialized_valuation_inputs.json``.
  2. Compute Ke via central CAPM (Rf + β × MRP) unless overridden.
  3. Call central ``ddm_intrinsic_value()`` for the per-share IV.
  4. Compute MoS vs current market price.

Zero formula logic — every numeric step delegates to
``aletheia.calculations.formulas``. Phase 5 architecture lock
enforces this.

Empty-state handling: when ``current_dps`` is zero or missing
(Centene-class filer that doesn't pay a dividend), engine returns
``intrinsic_per_share=None`` plus a warning naming the gap. The
router doesn't raise; the empty-state propagates to the serving
report so the dashboard can show "no IV — DDM doesn't apply for
this filer."
"""

from __future__ import annotations

from typing import Optional

from aletheia.calculations.formulas import (
    cost_of_equity as _cost_of_equity,
    ddm_decomposition as _decompose,
    ddm_intrinsic_value as _ddm_intrinsic_value,
)
from aletheia.calculations.specialized_inputs import load_specialized_inputs
from aletheia.contracts.interfaces import CalculationInput
from aletheia.tools.valuation_engines.base import ValuationResult


# Same MRP constant the rate-base engine + DCFEngine use.
_DEFAULT_MRP: float = 0.0475


# Required params per model — engine refuses to run when any is
# null (placeholder config entries don't produce fabricated IVs).
_REQUIRED_PARAMS = (
    "current_dps_annualized",
    "explicit_growth_pct",
    "explicit_years",
    "terminal_growth_pct",
)


class DdmEngine:
    """Two-stage DDM for banks, payment networks, managed care."""

    def compute_intrinsic_value(
        self, calc_input: CalculationInput,
    ) -> ValuationResult:
        ticker = calc_input.classification.ticker
        fy = self._latest_fy(calc_input)

        # ── Analyst inputs ────────────────────────────────────────
        inputs = load_specialized_inputs(ticker)
        if inputs is None or inputs.model != "ddm":
            return _empty_result(
                ticker, fy,
                warning=(
                    f"No DDM inputs configured for {ticker} (model="
                    f"{inputs.model if inputs else 'missing'!r}). "
                    "Add a rate-plan / dividend-policy entry to "
                    "config/specialized_valuation_inputs.json."
                ),
                inputs_snapshot={"config_entry": None},
            )

        if not inputs.has_required_inputs(*_REQUIRED_PARAMS):
            missing = [
                k for k in _REQUIRED_PARAMS
                if inputs.params.get(k) is None
            ]
            return _empty_result(
                ticker, fy,
                warning=(
                    f"DDM inputs incomplete for {ticker}: missing "
                    f"{missing}. Source citation: {inputs.source[:80]}..."
                ),
                inputs_snapshot={
                    "config_entry":  inputs.params,
                    "missing_keys":  missing,
                    "as_of_date":    inputs.as_of_date,
                },
            )

        # ── Convert percentages to decimals ───────────────────────
        current_dps = float(inputs.params["current_dps_annualized"])
        explicit_growth = float(inputs.params["explicit_growth_pct"]) / 100.0
        explicit_years = int(inputs.params["explicit_years"])
        terminal_growth = float(inputs.params["terminal_growth_pct"]) / 100.0

        # Zero or negative dividend → DDM undefined for this filer
        if current_dps <= 0:
            return _empty_result(
                ticker, fy,
                warning=(
                    f"{ticker} pays no dividend (DPS=${current_dps}). "
                    "DDM is undefined for non-dividend payers; needs "
                    "a residual-income or normalized-earnings model. "
                    "Use the analyst form to override with a synthetic "
                    "future-DPS assumption if appropriate."
                ),
                inputs_snapshot={
                    "config_entry":  inputs.params,
                    "current_dps":   current_dps,
                    "as_of_date":    inputs.as_of_date,
                },
            )

        # ── Market context (Rf, β, current price, shares) ────────
        market = _extract_market_context(calc_input)
        rf = market.get("risk_free_rate")
        beta = market.get("beta")
        current_price = market.get("current_price")

        # Optional Ke override from config (e.g., bank-specific risk
        # premium that pure CAPM understates)
        ke_override_pct = inputs.params.get("cost_of_equity_override_pct")
        if ke_override_pct is not None:
            ke = float(ke_override_pct) / 100.0
        else:
            ke = _cost_of_equity(
                risk_free_rate=rf, beta=beta,
                market_risk_premium=_DEFAULT_MRP,
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

        # ── Central DDM formula ──────────────────────────────────
        ips = _ddm_intrinsic_value(
            current_dps=current_dps,
            cost_of_equity=ke,
            explicit_growth=explicit_growth,
            explicit_years=explicit_years,
            terminal_growth=terminal_growth,
        )
        if ips is None:
            return _empty_result(
                ticker, fy,
                warning=(
                    f"DDM formula returned None for {ticker} — most "
                    f"likely Ke {ke:.4f} ≤ terminal_growth "
                    f"{terminal_growth:.4f}. Either raise terminal_growth "
                    f"or use cost_of_equity_override_pct in config."
                ),
                inputs_snapshot={
                    "config_entry":    inputs.params,
                    "cost_of_equity":  ke,
                    "rf": rf, "beta": beta, "mrp": _DEFAULT_MRP,
                },
            )

        # MoS
        mos: Optional[float] = None
        if current_price and current_price > 0:
            mos = (ips - current_price) / current_price

        # Decomposition for audit / UI
        breakdown = _decompose(
            current_dps=current_dps,
            cost_of_equity=ke,
            explicit_growth=explicit_growth,
            explicit_years=explicit_years,
            terminal_growth=terminal_growth,
        )

        warnings = []
        if inputs.analyst_notes:
            warnings.append(f"Analyst note: {inputs.analyst_notes[:200]}")

        # Equity value (rough): ips × shares — sourced from market
        shares = market.get("shares_diluted")
        equity_value = ips * shares if (ips and shares) else None

        return ValuationResult(
            ticker=ticker,
            fiscal_year=fy,
            engine="ddm",
            intrinsic_per_share=ips,
            equity_value=equity_value,
            current_price=current_price,
            margin_of_safety=mos,
            inputs_snapshot={
                "current_dps":      current_dps,
                "explicit_growth":  explicit_growth,
                "explicit_years":   explicit_years,
                "terminal_growth":  terminal_growth,
                "cost_of_equity":   ke,
                "risk_free_rate":   rf,
                "beta":             beta,
                "mrp":              _DEFAULT_MRP,
                "ke_override_used": ke_override_pct is not None,
                "shares_diluted":   shares,
                "as_of_date":       inputs.as_of_date,
                "source":           inputs.source,
            },
            notes=(
                "Two-stage Dividend Discount Model: explicit DPS "
                "growth period + terminal Gordon perpetuity."
            ),
            warnings=warnings,
            engine_specific={
                "decomposition":     breakdown,
                "analyst_notes":     inputs.analyst_notes,
                "config_source":     inputs.source,
                "next_review_date":  inputs.next_review_date,
            },
        )

    def _latest_fy(self, calc_input: CalculationInput) -> int:
        df = getattr(calc_input, "df", None)
        if df is not None and "fiscal_year" in df.columns and not df.empty:
            return int(df["fiscal_year"].max())
        return 0


def _extract_market_context(calc_input: CalculationInput) -> dict:
    """Same defensive market-data lookup as RateBaseEngine. Probes
    the calc_input's nested market context; falls back to live fetch."""
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
    return ValuationResult(
        ticker=ticker,
        fiscal_year=fy,
        engine="ddm",
        intrinsic_per_share=None,
        equity_value=None,
        current_price=inputs_snapshot.get("current_price"),
        margin_of_safety=None,
        inputs_snapshot=inputs_snapshot,
        notes="DDM valuation unavailable — see warnings.",
        warnings=[warning],
    )


__all__ = ["DdmEngine"]
