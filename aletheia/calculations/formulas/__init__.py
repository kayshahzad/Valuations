"""Centralized financial-formula module — single source of truth.

Every formula that's computed in more than one place historically lives
here exactly once. Both the FMP-provider path
(``aletheia.validation.fmp_stage3_adapter``) and the XBRL-provider path
(``aletheia.data.cleaning_engine``) call the same functions, so
cross-provider drift on derived metrics is impossible by construction.

Public surface (``__all__``): functions in this list are the ONLY ones
callers outside ``aletheia.calculations.formulas`` may import. The
architecture lock (``tests/architecture/test_single_formula_source.py``,
Phase 5) enforces this — see also Rule 2 in the centralization plan.

The companion metadata layer is
``aletheia.calculations.derivation_registry``. Each function's
docstring first line MUST exactly match its registry entry's ``formula``
field; a pytest assertion (Phase 5) keeps docs↔code in sync.

Phase 1 scope: NOPAT, InvestedCapital, ROIC. Subsequent phases extend
this module — see ``docs/methodology_changes/`` for the per-phase
methodology memos.
"""

from aletheia.calculations.formulas.balance_sheet import (
    gross_debt,
    liquid_assets,
    net_debt,
)
from aletheia.calculations.formulas.cash_flow import (
    fcf,
    fcff,
)
from aletheia.calculations.formulas.cost_of_capital import (
    cost_of_debt,
    cost_of_equity,
    wacc,
)
from aletheia.calculations.formulas.derived_inputs import (
    invested_capital,
    nopat,
)
from aletheia.calculations.formulas.dividend_discount import (
    ddm_decomposition,
    ddm_intrinsic_value,
)
from aletheia.calculations.formulas.embedded_value import (
    book_value_compounding_decomposition,
    book_value_compounding_intrinsic,
)
from aletheia.calculations.formulas.income_statement import (
    ebitda,
)
from aletheia.calculations.formulas.margins import (
    ebit_margin_pct,
    ebitda_margin_pct,
    fcf_margin_pct,
    gross_margin_pct,
)
from aletheia.calculations.formulas.rate_base import (
    rate_base_decomposition,
    rate_base_equity_value,
)
from aletheia.calculations.formulas.ratios import (
    roe,
    roic,
)
from aletheia.calculations.formulas.valuation_multiples import (
    cash_conversion_ratio,
    current_ratio,
    debt_to_equity,
    dividend_yield,
    ev_to_ebit,
    ev_to_ebitda,
    ev_to_fcf,
    interest_coverage,
    justified_ev_ebitda,
    net_debt_to_ebitda,
    price_to_book,
    price_to_earnings,
    price_to_sales,
)


__all__ = [
    # Balance sheet
    "gross_debt",
    "liquid_assets",
    "net_debt",
    # Cash flow
    "fcf",
    "fcff",
    # Cost of capital
    "cost_of_debt",
    "cost_of_equity",
    "wacc",
    # Derived inputs
    "invested_capital",
    "nopat",
    # Income statement
    "ebitda",
    # Margins
    "ebit_margin_pct",
    "ebitda_margin_pct",
    "fcf_margin_pct",
    "gross_margin_pct",
    # Rate-base (regulated utilities)
    "rate_base_equity_value",
    "rate_base_decomposition",
    # Dividend discount (banks, managed care, payment networks)
    "ddm_intrinsic_value",
    "ddm_decomposition",
    # Embedded value / book-value compounding (insurance conglomerates)
    "book_value_compounding_intrinsic",
    "book_value_compounding_decomposition",
    # Ratios
    "roe",
    "roic",
    # Valuation multiples
    "cash_conversion_ratio",
    "current_ratio",
    "debt_to_equity",
    "dividend_yield",
    "ev_to_ebit",
    "ev_to_ebitda",
    "ev_to_fcf",
    "interest_coverage",
    "justified_ev_ebitda",
    "net_debt_to_ebitda",
    "price_to_book",
    "price_to_earnings",
    "price_to_sales",
]
