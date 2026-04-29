"""
aletheia/data/ingestion_validator.py

Phase B — Ingestion Validation Gate
=====================================
Validates every CleanedRecord after _compute_derived() and before
_score_quality(). Bad records are quarantined — never written to the
main DuckDB table.

Architecture:
  CleaningEngine.clean()
      → _compute_derived()
      → IngestionValidator.validate()    ← NEW
          → passes: proceed to _score_quality() → write to company_records
          → fails:  write to quarantine_records, log failures
      → _score_quality()

Design principles:
  1. All magnitude rules are hardcoded here — not in the LLM or cleaning engine
  2. Sector-aware bounds (low-margin retail, utilities, financials)
  3. Every failure is logged with ticker, FY, field, value, and reason
  4. Quarantined records are inspectable — not silently dropped

Usage:
    from aletheia.data.ingestion_validator import IngestionValidator
    validator = IngestionValidator()
    failures = validator.validate(record)
    if failures:
        record.quarantined = True
        for f in failures:
            record.error(f)
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
import json


# ─────────────────────────────────────────────────────────────────────────────
# Sector archetype overrides
# Low-margin retail, financials, utilities have different plausibility ranges
# ─────────────────────────────────────────────────────────────────────────────

# Sectors where EBITDA < 5% of revenue is structurally correct
LOW_MARGIN_SECTORS = {"Retail", "Consumer Defensive", "Healthcare Plans", "Managed Care"}

# Sectors where D&A > 15% of revenue is structurally correct
HIGH_DA_SECTORS = {"Utilities", "Energy", "Real Estate", "Industrials"}

# Sectors where ROIC is not the primary metric (use ROE instead)
FINANCIAL_SECTORS = {"Financials", "Banking", "Insurance", "Financial Services"}

# Known low-margin tickers (supplement sector lookup)
LOW_MARGIN_TICKERS = {"COST", "WMT", "UNH", "CNC", "CVS", "HUM"}
HIGH_DA_TICKERS    = {"NEE", "DUK", "SO", "D", "CAT", "DE"}


# ─────────────────────────────────────────────────────────────────────────────
# Validation result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationFailure:
    field:    str
    value:    Optional[float]
    reason:   str
    severity: str  # "error" (quarantine) | "warning" (flag but allow)

    def __str__(self):
        val_str = f"{self.value:,.0f}" if self.value else "None"
        return f"[{self.severity.upper()}] {self.field}={val_str}: {self.reason}"


# ─────────────────────────────────────────────────────────────────────────────
# Validator
# ─────────────────────────────────────────────────────────────────────────────

class IngestionValidator:
    """
    Validates a CleanedRecord after _compute_derived().

    Returns a list of ValidationFailure objects.
    Empty list = record passes. Non-empty = quarantine if any severity==error.
    """

    def validate(self, record, sector: str = "") -> list[ValidationFailure]:
        """
        Main entry point. Returns list of failures (empty = clean).

        Args:
            record: CleanedRecord from cleaning_engine.py
            sector: company sector string for archetype-aware bounds
        """
        ticker = record.ticker
        fy     = record.fiscal_year
        failures = []

        revenue = (
            record.clean.get("Revenue")
            or record.raw.get("Revenue")
            or 0.0
        )

        # Detect archetype from sector or ticker
        is_low_margin  = (sector in LOW_MARGIN_SECTORS
                          or ticker in LOW_MARGIN_TICKERS)
        is_high_da     = (sector in HIGH_DA_SECTORS
                          or ticker in HIGH_DA_TICKERS)
        is_financial   = (sector in FINANCIAL_SECTORS)

        # ── 1. Revenue must exist and be positive ─────────────────────────────
        failures += self._check_revenue(revenue, ticker, fy)
        if not revenue or revenue <= 0:
            # Cannot compute pct-of-revenue checks without revenue
            return failures

        # ── 2. Critical income statement fields ───────────────────────────────
        failures += self._check_ebit(record, revenue, ticker, fy)
        failures += self._check_ebitda(record, revenue, ticker, fy, is_low_margin)

        # ── 3. Reinvestment inputs — D&A and CapEx ────────────────────────────
        failures += self._check_da(record, revenue, ticker, fy, is_high_da)
        failures += self._check_capex(record, revenue, ticker, fy, is_high_da)

        # ── 4. Returns — ROIC / ROE ───────────────────────────────────────────
        failures += self._check_roic(record, ticker, fy, is_financial)

        # ── 5. Balance sheet plausibility ────────────────────────────────────
        failures += self._check_balance_sheet(record, revenue, ticker, fy)

        # ── 6. Sanity: EBITDA > EBIT (D&A always positive) ───────────────────
        failures += self._check_accounting_identities(record, ticker, fy)

        return failures

    # ── Individual checks ─────────────────────────────────────────────────────

    def _check_revenue(self, revenue, ticker, fy) -> list:
        if not revenue or revenue <= 0:
            return [ValidationFailure(
                field="clean_Revenue", value=revenue,
                reason=f"Revenue is None/zero for {ticker} FY{fy}. "
                       "XBRL revenue tag not resolved. Check tag_misses.jsonl.",
                severity="error"
            )]
        return []

    def _check_ebit(self, record, revenue, ticker, fy) -> list:
        ebit = (record.clean.get("NormalizedEBIT")
                or record.derived.get("EBIT_Margin_Pct"))
        # Check via derived margin
        ebit_margin = record.derived.get("EBIT_Margin_Pct")
        if ebit_margin is None:
            return [ValidationFailure(
                field="derived_EBIT_Margin_Pct", value=None,
                reason="EBIT margin could not be computed. "
                       "OperatingIncome tag likely missing.",
                severity="warning"
            )]
        # EBIT margin -50% to +80% covers almost all real companies
        if not (-50.0 <= ebit_margin <= 80.0):
            return [ValidationFailure(
                field="derived_EBIT_Margin_Pct", value=ebit_margin,
                reason=f"EBIT margin {ebit_margin:.1f}% is outside -50% to +80% range. "
                       "Likely COGS/OPEX tag mismatch.",
                severity="error"
            )]
        return []

    def _check_ebitda(self, record, revenue, ticker, fy, is_low_margin) -> list:
        ebitda = record.derived.get("EBITDA")
        if ebitda is None:
            return [ValidationFailure(
                field="derived_EBITDA", value=None,
                reason="EBITDA is None — D&A not resolved. "
                       "DCF cannot run. Check tag_misses.jsonl.",
                severity="error"
            )]
        margin = ebitda / revenue * 100
        min_pct = 1.0 if is_low_margin else 4.0
        if margin < min_pct:
            return [ValidationFailure(
                field="derived_EBITDA", value=ebitda,
                reason=f"EBITDA margin {margin:.1f}% below minimum {min_pct:.0f}% "
                       f"({'low-margin archetype' if is_low_margin else 'standard archetype'}). "
                       "Possible D&A understatement.",
                severity="warning"
            )]
        return []

    def _check_da(self, record, revenue, ticker, fy, is_high_da) -> list:
        da = (record.clean.get("Depreciation")
              or record.raw.get("Depreciation"))
        if da is None or da == 0:
            return [ValidationFailure(
                field="clean_Depreciation", value=da,
                reason=f"D&A is None/zero for {ticker} FY{fy}. "
                       "EBITDA will be understated. "
                       "Check tag_misses.jsonl for unresolved XBRL tag.",
                severity="error"
            )]
        pct = da / revenue * 100
        max_pct = 40.0 if is_high_da else 20.0
        if not (0.5 <= pct <= max_pct):
            return [ValidationFailure(
                field="clean_Depreciation", value=da,
                reason=f"D&A is {pct:.1f}% of revenue "
                       f"(expected 0.5%-{max_pct:.0f}% for this archetype). "
                       "Possible tag mismatch or wrong fiscal year.",
                severity="warning"
            )]
        return []

    def _check_capex(self, record, revenue, ticker, fy, is_high_da) -> list:
        capex = (record.clean.get("CapEx_Total")
                 or record.raw.get("CapEx"))
        if capex is None or capex == 0:
            return [ValidationFailure(
                field="clean_CapEx_Total", value=capex,
                reason=f"CapEx is None/zero for {ticker} FY{fy}. "
                       "FCF computation will be wrong. "
                       "Check tag_misses.jsonl.",
                severity="error"
            )]
        pct = capex / revenue * 100
        max_pct = 60.0 if is_high_da else 30.0
        if not (0.1 <= pct <= max_pct):
            return [ValidationFailure(
                field="clean_CapEx_Total", value=capex,
                reason=f"CapEx is {pct:.1f}% of revenue "
                       f"(expected 0.1%-{max_pct:.0f}% for this archetype). "
                       "Possible tag mismatch.",
                severity="warning"
            )]
        return []

    def _check_roic(self, record, ticker, fy, is_financial) -> list:
        roic = record.derived.get("ROIC")
        roe  = record.derived.get("ROE")
        if is_financial:
            # For financials, ROE is the right metric
            if roe is None:
                return [ValidationFailure(
                    field="derived_ROE", value=None,
                    reason="ROE is None for financial sector ticker. "
                           "NetIncome or TotalEquity tag missing.",
                    severity="warning"
                )]
            return []
        if roic is None:
            return [ValidationFailure(
                field="derived_ROIC", value=None,
                reason="ROIC is None — NOPAT or InvestedCapital missing. "
                       "Conviction scorer will use neutral default.",
                severity="warning"
            )]
        # ROIC outside -50% to +300% is almost certainly a data error
        if not (-0.50 <= roic <= 3.0):
            return [ValidationFailure(
                field="derived_ROIC", value=roic,
                reason=f"ROIC {roic:.1%} is outside -50% to +300% range. "
                       "Likely InvestedCapital denominator error.",
                severity="warning"
            )]
        return []

    def _check_balance_sheet(self, record, revenue, ticker, fy) -> list:
        failures = []
        assets = record.raw.get("TotalAssets")
        equity = record.raw.get("TotalEquity")
        if assets is None or assets <= 0:
            failures.append(ValidationFailure(
                field="raw_TotalAssets", value=assets,
                reason="TotalAssets is None/zero. Balance sheet incomplete.",
                severity="warning"
            ))
        if equity is None:
            failures.append(ValidationFailure(
                field="raw_TotalEquity", value=None,
                reason="TotalEquity is None. NetDebt and IC calculations affected.",
                severity="warning"
            ))
        return failures

    def _check_accounting_identities(self, record, ticker, fy) -> list:
        failures = []
        ebit  = record.derived.get("EBIT_Margin_Pct")
        ebitda_margin = record.derived.get("EBITDA_Margin_Pct")
        if ebit and ebitda_margin:
            if ebitda_margin < ebit:
                failures.append(ValidationFailure(
                    field="accounting_identity",
                    value=ebitda_margin - ebit,
                    reason=f"EBITDA margin ({ebitda_margin:.1f}%) < EBIT margin ({ebit:.1f}%). "
                           "D&A must be positive — identity violated. "
                           "Possible negative D&A value in XBRL.",
                    severity="error"
                ))
        return failures


# ─────────────────────────────────────────────────────────────────────────────
# Convenience: check if any failures are errors (vs warnings only)
# ─────────────────────────────────────────────────────────────────────────────

def has_errors(failures: list[ValidationFailure]) -> bool:
    return any(f.severity == "error" for f in failures)

def has_warnings(failures: list[ValidationFailure]) -> bool:
    return any(f.severity == "warning" for f in failures)

def failures_to_dict(failures: list[ValidationFailure]) -> list[dict]:
    return [
        {"field": f.field, "value": f.value,
         "reason": f.reason, "severity": f.severity}
        for f in failures
    ]
