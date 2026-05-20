"""Embedded-value engine — for ``embedded_value_required`` filers.

Phase A.7. BRK-B today; future insurance conglomerates fall under
this engine. Uses the book-value compounding framework documented in
``aletheia.calculations.formulas.embedded_value``:

  Future_BVPS = BVPS_0 × (1 + ROE × retention)^N
  IV_per_Share = Future_BVPS × Target_P/B / (1 + Ke)^N

Orchestration only — math lives in the central formula module.
"""

from __future__ import annotations

from typing import Optional

from aletheia.calculations.formulas import (
    book_value_compounding_decomposition as _decompose,
    book_value_compounding_intrinsic as _bvc_intrinsic,
    cost_of_equity as _cost_of_equity,
)
from aletheia.calculations.specialized_inputs import load_specialized_inputs
from aletheia.contracts.interfaces import CalculationInput
from aletheia.tools.valuation_engines.base import ValuationResult


_DEFAULT_MRP: float = 0.0475


_REQUIRED_PARAMS = (
    "book_value_per_share",
    "expected_roe_pct",
    "target_pb",
    "horizon_years",
)


class EmbeddedValueEngine:
    """Book-value compounding for insurance conglomerates."""

    def compute_intrinsic_value(
        self, calc_input: CalculationInput,
    ) -> ValuationResult:
        ticker = calc_input.classification.ticker
        fy = self._latest_fy(calc_input)

        inputs = load_specialized_inputs(ticker)
        if inputs is None or inputs.model != "embedded_value":
            return _empty_result(
                ticker, fy,
                warning=(
                    f"No embedded-value inputs configured for {ticker} "
                    f"(model={inputs.model if inputs else 'missing'!r}). "
                    "Add a book-value + ROE entry to "
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
                    f"Embedded-value inputs incomplete for {ticker}: "
                    f"missing {missing}. Source citation: {inputs.source[:80]}..."
                ),
                inputs_snapshot={
                    "config_entry":  inputs.params,
                    "missing_keys":  missing,
                    "as_of_date":    inputs.as_of_date,
                },
            )

        bvps = float(inputs.params["book_value_per_share"])
        expected_roe = float(inputs.params["expected_roe_pct"]) / 100.0
        target_pb = float(inputs.params["target_pb"])
        horizon_years = int(inputs.params["horizon_years"])
        payout_ratio = float(inputs.params.get("payout_ratio_pct") or 0.0) / 100.0

        market = _extract_market_context(calc_input)
        rf = market.get("risk_free_rate")
        beta = market.get("beta")
        current_price = market.get("current_price")
        shares = market.get("shares_diluted")

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
                    f"missing risk_free_rate or beta."
                ),
                inputs_snapshot={
                    "config_entry": inputs.params,
                    "rf": rf, "beta": beta,
                },
            )

        ips = _bvc_intrinsic(
            book_value_per_share=bvps,
            expected_roe=expected_roe,
            cost_of_equity=ke,
            target_pb=target_pb,
            horizon_years=horizon_years,
            payout_ratio=payout_ratio,
        )
        if ips is None:
            return _empty_result(
                ticker, fy,
                warning=(
                    f"Embedded-value formula returned None for {ticker}. "
                    f"Check inputs: BVPS, ROE, target_pb, horizon_years."
                ),
                inputs_snapshot={
                    "config_entry":    inputs.params,
                    "cost_of_equity":  ke,
                },
            )

        mos: Optional[float] = None
        if current_price and current_price > 0:
            mos = (ips - current_price) / current_price

        equity_value = ips * shares if (ips and shares) else None

        breakdown = _decompose(
            book_value_per_share=bvps,
            expected_roe=expected_roe,
            cost_of_equity=ke,
            target_pb=target_pb,
            horizon_years=horizon_years,
            payout_ratio=payout_ratio,
        )

        warnings = []
        if inputs.analyst_notes:
            warnings.append(f"Analyst note: {inputs.analyst_notes[:200]}")

        return ValuationResult(
            ticker=ticker,
            fiscal_year=fy,
            engine="embedded_value",
            intrinsic_per_share=ips,
            equity_value=equity_value,
            current_price=current_price,
            margin_of_safety=mos,
            inputs_snapshot={
                "book_value_per_share":  bvps,
                "expected_roe":          expected_roe,
                "target_pb":             target_pb,
                "horizon_years":         horizon_years,
                "payout_ratio":          payout_ratio,
                "cost_of_equity":        ke,
                "risk_free_rate":        rf,
                "beta":                  beta,
                "mrp":                   _DEFAULT_MRP,
                "ke_override_used":      ke_override_pct is not None,
                "shares_diluted":        shares,
                "as_of_date":            inputs.as_of_date,
                "source":                inputs.source,
            },
            notes=(
                "Book-value compounding: BVPS × (1+ROE×retention)^N "
                "× target_P/B / (1+Ke)^N. Captures BRK-B's "
                "consolidated economics across insurance + operating "
                "subsidiaries + equity portfolio."
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
        engine="embedded_value",
        intrinsic_per_share=None,
        equity_value=None,
        current_price=inputs_snapshot.get("current_price"),
        margin_of_safety=None,
        inputs_snapshot=inputs_snapshot,
        notes="Embedded-value valuation unavailable — see warnings.",
        warnings=[warning],
    )


__all__ = ["EmbeddedValueEngine"]
