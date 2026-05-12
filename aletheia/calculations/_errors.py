"""Error hierarchy for the calculation-layer validation framework.

Every calculation error carries enough context to be debuggable from
the message alone: ticker, function name, field, observed value,
expected value/range. Bare ``ValueError("bad input")`` is not
acceptable — when one of these fires in production, the on-call analyst
must be able to triage without re-running anything.

Layering:

    CalculationError              (base; always re-raisable)
    ├── CalculationInputError     (upstream data violated a guard)
    ├── CalculationOutputError    (model produced an implausible result)
    └── CalculationConsistencyError  (arithmetic identity was violated)

Callers (orchestration, agents, UI) catch ``CalculationError`` to mark a
field as ``unavailable`` and proceed; the three subclasses let the
caller distinguish "upstream data bad" from "model bug" from "identity
violation" in receipts and audit logs.
"""

from __future__ import annotations

from typing import Any, Optional


class CalculationError(Exception):
    """Base for all calculation-layer validation errors.

    Always carries ticker + function + field + value + expected so the
    message is self-contained. Subclasses distinguish the failure mode
    (input degradation vs. output implausibility vs. identity violation).
    """

    def __init__(
        self,
        message: str,
        *,
        ticker: str,
        fn: str,
        field: Optional[str] = None,
        value: Any = None,
        expected: Optional[str] = None,
    ) -> None:
        self.ticker = ticker
        self.fn = fn
        self.field = field
        self.value = value
        self.expected = expected
        super().__init__(self._format(message))

    def _format(self, message: str) -> str:
        ctx = f"[{self.ticker} :: {self.fn}]"
        if self.field is not None:
            ctx += f" field={self.field}"
        if self.value is not None:
            # Truncate huge values so the message stays readable
            repr_v = repr(self.value)
            if len(repr_v) > 80:
                repr_v = repr_v[:77] + "..."
            ctx += f" value={repr_v}"
        if self.expected:
            ctx += f" expected={self.expected}"
        return f"{ctx} {message}"

    def to_receipt(self) -> dict:
        """Structured form for persisting in receipts / audit logs."""
        return {
            "category":  self.__class__.__name__,
            "ticker":    self.ticker,
            "fn":        self.fn,
            "field":     self.field,
            "value":     self.value,
            "expected":  self.expected,
            "message":   str(self),
        }


class CalculationInputError(CalculationError):
    """An input field violated a guard (NaN, wrong sign, out of range).

    Blame is upstream: the data layer produced something the calc layer
    cannot consume. The fix belongs in cleaning_engine / TTM derivation /
    ingestion validator — NOT in the calculation function.
    """


class CalculationOutputError(CalculationError):
    """A computed output violated a sanity range.

    Inputs may have individually passed validation but combined to
    produce an implausible result (MDT-class bug: NaN EBIT coerced to 0
    silently produced 36.5% implied CAGR). The fix usually requires
    revisiting the model assumptions or input-degradation guards.
    """


class CalculationConsistencyError(CalculationError):
    """An arithmetic identity was violated.

    Examples:
      - EBITDA != EBIT + D&A within tolerance
      - FCF != OperatingCF - CapEx within tolerance
      - TotalAssets != TotalLiabilities + TotalEquity within tolerance
      - NetDebt != TotalDebt - Cash within tolerance

    Identity violations indicate either an ingest bug (wrong XBRL tag),
    a derivation bug, or a unit/sign error. They are the most reliable
    bug-catcher because they encode what MUST be true rather than what
    is typically expected.
    """
