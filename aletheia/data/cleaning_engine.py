"""
aletheia/data/cleaning_engine.py

Phase 1 — 9-Domain Data Cleaning Engine
========================================
Sits between canonical_transformer.py and the database.
Reads the canonical Parquet output (long-format: one row per tag/value)
and applies all 10 cleaning domains sequentially.

Each domain produces:
  - An adjusted value (or the original if no adjustment needed)
  - A cleaning flag describing what was done
  - A confidence score (0.0–1.0)

The output is a CleanedRecord dataclass that flows to:
  - database.py (versioned storage)
  - quantitative_screens.py (Beneish, Sloan)
  - Phase 2 valuation engine

Usage:
    from aletheia.data.cleaning_engine import CleaningEngine
    engine = CleaningEngine()
    result = engine.clean("AAPL", 2023)
    print(result.summary())
"""

import os
import json
import datetime
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import pandas as pd
import numpy as np

warnings.filterwarnings("ignore", category=FutureWarning)

# Tag resolution layer — normalizes transformer output + enriches from raw XBRL
try:
    from aletheia.data.tag_resolver import TagResolver
    _tag_resolver = TagResolver()
except ImportError:
    _tag_resolver = None


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class CleaningFlag:
    """Records what a cleaning domain did to a specific metric."""
    domain: int
    domain_name: str
    metric: str
    raw_value: Optional[float]
    adjusted_value: Optional[float]
    action: str          # "adjusted", "removed", "flagged", "no_change", "missing"
    reason: str
    confidence: float    # 0.0 = uncertain, 1.0 = certain


@dataclass
class CleanedRecord:
    """
    The canonical cleaned record for one company / one fiscal year.
    This is what flows to the database and all downstream agents.
    """
    ticker: str
    fiscal_year: int
    period_end_date: Optional[str]
    period_end_date_missing: bool = False
    cleaned_at: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())
    version: int = 1

    # ── Raw resolved values (from canonical_transformer output) ──────────────
    raw: Dict[str, Optional[float]] = field(default_factory=dict)

    # ── Cleaned values (after all 10 domains) ───────────────────────────────
    clean: Dict[str, Optional[float]] = field(default_factory=dict)

    # ── Derived / computed metrics ───────────────────────────────────────────
    derived: Dict[str, Optional[float]] = field(default_factory=dict)
    derived_provenance: Dict[str, str] = field(default_factory=dict)

    # ── Cleaning audit trail ─────────────────────────────────────────────────
    flags: List[CleaningFlag] = field(default_factory=list)

    # ── Domain-level quality signals ─────────────────────────────────────────
    domain_scores: Dict[str, float] = field(default_factory=dict)

    # ── Aggregate quality ────────────────────────────────────────────────────
    overall_quality_score: float = 0.0   # 0.0–1.0
    cleaning_warnings: List[str] = field(default_factory=list)
    blocking_errors: List[str] = field(default_factory=list)

    def get_with_provenance(self, field_name: str) -> Tuple[Optional[float], str]:
        """
        Returns the value and its provenance ('raw', 'derived', or 'missing').
        Prioritizes raw over derived to ensure reported XBRL facts are preferred.
        """
        if field_name == "Depreciation":
            field_name = "Depreciation_Total"

        # Fields where derived assembly is more complete than a partial raw tag
        PREFER_DERIVED_FIELDS = {"Depreciation_Total"}
        
        if field_name in PREFER_DERIVED_FIELDS:
            val = self.derived.get(field_name)
            if val is not None:
                prov = self.derived_provenance.get(field_name, "derived")
                return val, prov
            
        val = self.raw.get(field_name)
        if val is not None:
            return val, "raw"
        
        val = self.derived.get(field_name)
        if val is not None:
            prov = self.derived_provenance.get(field_name, "derived")
            return val, prov
            
        return None, "missing"

    def get(self, key: str, fallback: float = None) -> Optional[float]:
        """Convenience: cleaned value first, then raw, then fallback."""
        return self.clean.get(key) or self.raw.get(key) or fallback

    def add_flag(self, flag: CleaningFlag):
        self.flags.append(flag)

    def warn(self, msg: str):
        self.cleaning_warnings.append(msg)

    def error(self, msg: str):
        self.blocking_errors.append(msg)

    def summary(self) -> str:
        lines = [
            f"CleanedRecord: {self.ticker} FY{self.fiscal_year}",
            f"  Quality score : {self.overall_quality_score:.2f}",
            f"  Raw metrics   : {len(self.raw)}",
            f"  Clean metrics : {len(self.clean)}",
            f"  Derived metrics: {len(self.derived)}",
            f"  Flags         : {len(self.flags)}",
            f"  Warnings      : {len(self.cleaning_warnings)}",
            f"  Errors        : {len(self.blocking_errors)}",
        ]
        if self.cleaning_warnings:
            for w in self.cleaning_warnings:
                lines.append(f"  ⚠ {w}")
        if self.blocking_errors:
            for e in self.blocking_errors:
                lines.append(f"  ✗ {e}")
        return "\n".join(lines)

    def to_flat_dict(self) -> dict:
        """Flat dict for database storage — one row per company/year."""
        row = {
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "period_end_date": self.period_end_date,
            "cleaned_at": self.cleaned_at,
            "version": self.version,
            "overall_quality_score": self.overall_quality_score,
            "warning_count": len(self.cleaning_warnings),
            "error_count": len(self.blocking_errors),
        }
        for k, v in self.clean.items():
            row[f"clean_{k}"] = v
        for k, v in self.raw.items():
            row[f"raw_{k}"] = v
        for k, v in self.derived.items():
            row[f"derived_{k}"] = v
        for k, v in self.domain_scores.items():
            row[f"domain_score_{k}"] = v
        return row


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_div(numerator: Optional[float], denominator: Optional[float],
              fallback: float = None) -> Optional[float]:
    """Safe division — returns fallback if either arg is None or denom is zero."""
    if numerator is None or denominator is None:
        return fallback
    if abs(denominator) < 1e-9:
        return fallback
    return numerator / denominator


def _pct_change(current: Optional[float], prior: Optional[float]) -> Optional[float]:
    """Year-over-year percentage change. Returns None if either value missing."""
    if current is None or prior is None or abs(prior) < 1e-9:
        return None
    return (current - prior) / abs(prior)


# ─────────────────────────────────────────────────────────────────────────────
# Issuer Overrides Registry
# ─────────────────────────────────────────────────────────────────────────────
# Maps ticker -> structural override for valuation convergence.
# Framework: Only applied when structural delta >15% and strictly documented
# against primary 10-K sources.
ISSUER_OVERRIDES = {
    "AMZN": {
        "metric": "FCF",
        "adjustment_logic": {"operation": "subtract", "raw_fact_tag": "FinanceLeasePrincipalPayments"},
        "effective_from_fy": 2019,
        "fallback_behavior": "default_formula", # if tag is missing, fall back to standard FCF
        "rationale": "AMZN stated FCF methodology subtracts finance lease repayments",
        "source": "AMZN 10-K FCF definition",
        "approver": "Kashif Shahzad",
        "approved_date": "2026-04-29"
    }
}


# ─────────────────────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────────────────────

class CleaningEngine:
    """
    Applies the 9-domain (+ tax = 10-domain) cleaning standard to a
    canonical Parquet record and returns a CleanedRecord.

    Canonical Parquet schema (long format from canonical_transformer.py):
        ticker | period_end_date | stmt_type | standard_tag | value |
        source_accession | form | fy | fp | raw_file_hash |
        transformed_at | resolved_tag

    The engine pivots this to wide format internally, applies all domains,
    and emits a CleanedRecord.
    """

    # Tags we expect from canonical_transformer. Missing ones are noted.
    EXPECTED_TAGS = [
        "Revenue", "COGS", "SG&A", "R&D", "NetIncome",
        "EBIT", "OperatingIncome", "Depreciation", "CapEx",
        "TotalAssets", "TotalLiabilities", "TotalEquity",
        "Cash", "LongTermDebt", "LiabilitiesCurrent",
    ]

    # Non-recurring item keywords to hunt in filing text
    # Used by Domain 1 NLP flag (filing text search is optional/async)
    NON_RECURRING_KEYWORDS = [
        "restructuring", "impairment", "write-down", "write-off",
        "integration costs", "streamlining", "goodwill impairment",
        "asset impairment", "severance", "one-time", "non-recurring",
        "special charge", "discontinued operations",
    ]

    def __init__(
        self,
        canonical_dir: str = "valuation_data/canonical/financials",
        raw_dir: str = "valuation_data/raw/sec",
        verbose: bool = True,
    ):
        self.canonical_dir = Path(canonical_dir)
        self.raw_dir = Path(raw_dir)
        self.verbose = verbose

    # ─────────────────────────────────────────────────────────────────────────
    # Public API
    # ─────────────────────────────────────────────────────────────────────────

    def clean(self, ticker: str, fiscal_year: int,
              prior_year_record: Optional["CleanedRecord"] = None) -> CleanedRecord:
        """
        Main entry point. Cleans one company for one fiscal year.

        Args:
            ticker: e.g. "AAPL"
            fiscal_year: e.g. 2023
            prior_year_record: previous year's CleanedRecord for YoY comparisons
                               (used by Domains 8, 9, 10). Pass None if first year.

        Returns:
            CleanedRecord with all domains applied.
        """
        if self.verbose:
            print(f"\n{'='*60}")
            print(f"  Cleaning: {ticker} FY{fiscal_year}")
            print(f"{'='*60}")

        # 1. Load canonical parquet → pivot to wide dict
        raw_wide, period_end_date = self._load_and_pivot(ticker, fiscal_year)
        if raw_wide is None:
            rec = CleanedRecord(ticker=ticker, fiscal_year=fiscal_year,
                                period_end_date=None)
            rec.error(f"No canonical data found for {ticker} FY{fiscal_year}")
            return rec

        # 2. Initialise record
        record = CleanedRecord(
            ticker=ticker,
            fiscal_year=fiscal_year,
            period_end_date=period_end_date,
        )
        record.raw = dict(raw_wide)
        record.clean = dict(raw_wide)  # start with raw; domains adjust in-place

        # 2b. VALIDATION GATE (Layers 1-3)
        try:
            from aletheia.data.data_quality_validator import DataQualityValidator
            validator = DataQualityValidator()
            validation_result = validator.validate(ticker, fiscal_year, raw_wide)
            if not validation_result["passed"]:
                for failure in validation_result["all_failures"]:
                    record.error(f"Validation Failure: {failure}")
                record.overall_quality_score = 0.0
                if self.verbose:
                    print(f"  ✗ VALIDATION GATE FAILED: {validation_result['all_failures']}")
                # Halt further cleaning domains
                return record
        except ImportError:
            pass

        # 3. Apply all 10 domains sequentially
        self._domain1_nonrecurring(record)
        self._domain2_jva_separation(record, ticker, fiscal_year)
        self._domain3_ebit_normalization(record)
        self._domain4_accounting_policy(record, ticker)
        self._domain5_lease_normalization(record, ticker, fiscal_year)
        self._domain6_pension_cleaning(record, ticker, fiscal_year)
        self._domain7_sbc_adjustment(record, ticker, fiscal_year)
        self._domain8_revenue_recognition(record, prior_year_record)
        self._domain9_working_capital(record, prior_year_record)
        self._domain10_tax_sustainability(record, prior_year_record)

        # 4. Compute derived metrics (EBITDA, FCF, NOPAT, etc.)
        self._compute_derived(record)

        # 4b. Recompute SBC_PctFCF now that FCF is available
        # Domain 7 runs before _compute_derived so FCF was None at that point.
        # We recompute here with the final derived FCF to ensure correctness.
        sbc = record.clean.get("SBC") or 0.0
        fcf_final = record.derived.get("FCF") or record.clean.get("FCF")
        if sbc > 0 and fcf_final and fcf_final > 0:
            sbc_pct_fcf = sbc / fcf_final * 100
            record.clean["SBC_PctFCF"] = sbc_pct_fcf
        elif sbc > 0 and fcf_final and fcf_final <= 0:
            # FCF negative — SBC% is technically infinite, flag as extreme
            record.clean["SBC_PctFCF"] = 999.0

        # 4c. Ingestion validation gate
        #     Runs after _compute_derived() so all derived fields are available.
        #     Records with critical errors are quarantined (not silently passed).
        try:
            from aletheia.data.ingestion_validator import (
                IngestionValidator, has_errors, failures_to_dict
            )
            validator = IngestionValidator()
            sector = getattr(self, "_sector_cache", {}).get(ticker, "")
            validation_failures = validator.validate(record, sector=sector)
            if validation_failures:
                record.clean["_validation_failures"] = failures_to_dict(
                    validation_failures
                )
                record.clean["_quarantined"] = has_errors(validation_failures)
                for vf in validation_failures:
                    if vf.severity == "error":
                        record.error(
                            f"VALIDATION ERROR — {vf.field}: {vf.reason}"
                        )
                    else:
                        record.warn(
                            f"VALIDATION WARNING — {vf.field}: {vf.reason}"
                        )
                if has_errors(validation_failures):
                    print(
                        f"  ⛔ QUARANTINED: {ticker} FY{fiscal_year} — "
                        f"{sum(1 for f in validation_failures if f.severity=='error')} "
                        f"error(s). Record flagged — check quarantine_records table."
                    )
        except ImportError:
            pass  # Validator not installed — safe skip

        # 5. Score overall quality
        self._score_quality(record)

        if self.verbose:
            print(record.summary())

        return record

    def clean_all_years(self, ticker: str) -> List[CleanedRecord]:
        """
        Cleans all available fiscal years for a ticker in chronological order,
        passing each year's record as prior_year to the next (enables YoY domains).
        """
        parquet_path = self.canonical_dir / f"{ticker.upper()}.parquet"
        
        years = set()
        if parquet_path.exists():
            try:
                df = pd.read_parquet(parquet_path)
                if "fy" in df.columns:
                    years.update(df["fy"].dropna().unique().astype(int).tolist())
            except:
                pass
        
        # Always check raw facts to see what years are available
        raw_facts = self._load_raw_facts(ticker)
        if raw_facts:
            us_gaap = raw_facts.get("facts", {}).get("us-gaap", {})
            ifrs = raw_facts.get("facts", {}).get("ifrs-full", {})
            all_facts = {}
            all_facts.update(ifrs)
            all_facts.update(us_gaap)
            for concept in all_facts.values():
                for unit_type, units in concept.get("units", {}).items():
                    if unit_type not in ("USD", "shares", "pure", "EUR", "TWD", "CAD", "GBP", "JPY", "CHF"):
                        continue
                    for unit in units:
                        if unit.get("form") in ("10-K", "20-F", "40-F") and unit.get("fy"):
                            years.add(int(unit["fy"]))
                        
        if not years:
            print(f"⚠ No data (parquet or raw) found for {ticker}")
            return []

        years = sorted(list(years))
        records = []
        prior = None
        for fy in years:
            rec = self.clean(ticker, int(fy), prior_year_record=prior)
            records.append(rec)
            prior = rec

        return records

    # ─────────────────────────────────────────────────────────────────────────
    # Data loading
    # ─────────────────────────────────────────────────────────────────────────

    def _load_and_pivot(self, ticker: str, fiscal_year: int
                        ) -> Tuple[Optional[Dict[str, float]], Optional[str]]:
        """
        Loads the canonical Parquet for a ticker and pivots to wide format
        for the requested fiscal year. If not found, starts with empty dict
        to rely entirely on tag_resolver.

        Returns:
            (wide_dict, period_end_date)
        """
        parquet_path = self.canonical_dir / f"{ticker.upper()}.parquet"
        wide = {}
        period_end_date = None

        if parquet_path.exists():
            try:
                df = pd.read_parquet(parquet_path)
                if "fy" in df.columns and "standard_tag" in df.columns:
                    fy_df = df[df["fy"] == fiscal_year].copy()
                    if not fy_df.empty:
                        for _, row in fy_df.iterrows():
                            tag = row.get("standard_tag")
                            val = row.get("value")
                            if tag and val is not None and not pd.isna(val):
                                wide[tag] = float(val)
                        if "period_end_date" in fy_df.columns:
                            dates = fy_df["period_end_date"].dropna()
                            if not dates.empty:
                                period_end_date = str(dates.iloc[-1])
            except Exception as e:
                pass

        # ── Enrich: normalize tag names + supplement from raw XBRL ──────────
        # This resolves the lowercase/PascalCase mismatch between
        # canonical_transformer output and cleaning engine expectations,
        # and fills in metrics the transformer did not capture (SBC, lease, etc.)
        # Also handles 100% extraction if parquet is missing.
        if _tag_resolver is not None:
            wide = _tag_resolver.enrich(wide, ticker, fiscal_year)
            if "period_end_date" in wide and wide["period_end_date"]:
                period_end_date = wide["period_end_date"]
                # Don't delete it from wide because validation might not care, but it's safe to keep

        return wide, period_end_date

    def _load_raw_facts(self, ticker: str) -> Optional[Dict]:
        """Loads the raw SEC companyfacts JSON for NLP-based checks."""
        # Need CIK first
        cik_path = self.raw_dir / "company_tickers" / "company_tickers.json"
        if not cik_path.exists():
            return None
        try:
            with open(cik_path) as f:
                tickers_data = json.load(f)
            cik = None
            for _, v in tickers_data.items():
                if v["ticker"].upper() == ticker.upper():
                    cik = str(v["cik_str"]).zfill(10)
                    break
            if not cik:
                return None
            facts_path = self.raw_dir / "companyfacts" / f"CIK{cik}.json"
            if not facts_path.exists():
                return None
            with open(facts_path) as f:
                return json.load(f)
        except Exception:
            return None

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 1 — Non-recurring item stripping (Liberti)
    # ─────────────────────────────────────────────────────────────────────────

    def _domain1_nonrecurring(self, record: CleanedRecord):
        """
        Domain 1: Strip non-recurring items from EBITDA / EBIT.

        What we can do automatically from structured XBRL:
          - Detect RestructuringCharges, ImpairmentLoss, GoodwillImpairment,
            DiscontinuedOperationGainLoss — these are discrete XBRL tags.
          - Add them back to OperatingIncome / EBIT to produce clean_EBIT.

        What requires NLP (flagged for human review):
          - Items buried in SG&A or other lines without discrete tags.

        Rule: non-recurring expenses are ADDED BACK (they reduce earnings);
              non-recurring income is DEDUCTED.
        """
        domain_name = "NonRecurring"
        adjustments = {}

        # These XBRL standard tags map to discrete non-recurring charges
        # that canonical_transformer may have captured.
        # We look for them in record.raw and accumulate the adjustment.
        non_recurring_charge_tags = [
            "RestructuringCharges",
            "ImpairmentLoss",
            "GoodwillImpairmentLoss",
            "AssetImpairmentCharges",
            "BusinessCombinationAcquisitionRelatedCosts",
            "SeveranceCosts",
        ]
        non_recurring_income_tags = [
            "GainLossOnDispositionOfAssets",
            "GainLossOnSaleOfBusiness",
        ]

        total_charge_addback = 0.0
        total_income_deduct = 0.0

        for tag in non_recurring_charge_tags:
            val = record.raw.get(tag)
            if val is not None and val != 0:
                total_charge_addback += abs(val)
                adjustments[tag] = abs(val)
                record.add_flag(CleaningFlag(
                    domain=1, domain_name=domain_name,
                    metric=tag,
                    raw_value=val,
                    adjusted_value=abs(val),
                    action="adjusted",
                    reason=f"Non-recurring charge {tag} added back to EBIT",
                    confidence=0.90,
                ))

        for tag in non_recurring_income_tags:
            val = record.raw.get(tag)
            if val is not None and val != 0:
                total_income_deduct += abs(val)
                adjustments[tag] = -abs(val)
                record.add_flag(CleaningFlag(
                    domain=1, domain_name=domain_name,
                    metric=tag,
                    raw_value=val,
                    adjusted_value=-abs(val),
                    action="adjusted",
                    reason=f"Non-recurring income {tag} deducted from EBIT",
                    confidence=0.85,
                ))

        # Apply to OperatingIncome / EBIT
        ebit_key = "OperatingIncome" if "OperatingIncome" in record.clean else "EBIT"
        raw_ebit = record.clean.get(ebit_key)

        if raw_ebit is not None:
            net_adjustment = total_charge_addback - total_income_deduct
            clean_ebit = raw_ebit + net_adjustment
            record.clean[f"clean_{ebit_key}"] = clean_ebit
            record.clean["NonRecurring_TotalAdjustment"] = net_adjustment

            if abs(net_adjustment) > 0:
                record.add_flag(CleaningFlag(
                    domain=1, domain_name=domain_name,
                    metric=ebit_key,
                    raw_value=raw_ebit,
                    adjusted_value=clean_ebit,
                    action="adjusted",
                    reason=(f"EBIT adjusted by {net_adjustment:+,.0f} "
                            f"({total_charge_addback:,.0f} charges added back, "
                            f"{total_income_deduct:,.0f} income deducted)"),
                    confidence=0.88,
                ))

                # If adjustment > 10% of EBIT, flag for human review
                if abs(net_adjustment) > abs(raw_ebit) * 0.10:
                    record.warn(
                        f"D1: Non-recurring adjustment of {net_adjustment:+,.0f} "
                        f"is >10% of EBIT ({raw_ebit:,.0f}). "
                        f"Review filing text for additional buried items."
                    )
        else:
            record.add_flag(CleaningFlag(
                domain=1, domain_name=domain_name,
                metric=ebit_key,
                raw_value=None, adjusted_value=None,
                action="missing",
                reason="OperatingIncome/EBIT not found in canonical record",
                confidence=0.0,
            ))

        record.domain_scores["D1_NonRecurring"] = (
            0.9 if total_charge_addback + total_income_deduct > 0 else 1.0
        )

        if self.verbose:
            print(f"  D1 NonRecurring: net_adj={total_charge_addback - total_income_deduct:+,.0f}")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 2 — JVA / Associate income separation (Liberti)
    # ─────────────────────────────────────────────────────────────────────────

    def _domain2_jva_separation(self, record: CleanedRecord,
                                ticker: str, fiscal_year: int):
        """
        Domain 2: JVA income is post-tax, post-interest (equity-level metric).
        It must NOT sit inside EBITDA (enterprise-level, pre-tax metric).

        Action:
          - Detect equity method investment income from raw facts.
          - Remove from EBIT calculation.
          - Store separately as JVA_Income for equity bridge addition.
          - Flag for separate PE multiple valuation.
        """
        domain_name = "JVA_Separation"

        # XBRL tags for equity method income
        jva_tags = [
            "IncomeLossFromEquityMethodInvestments",
            "EquityMethodInvestmentSummarizedFinancialInformationNetIncomeLoss",
            "GainLossOnInvestments",  # lower confidence — may not be JVA
        ]

        jva_income = None
        jva_source_tag = None

        for tag in jva_tags:
            val = record.raw.get(tag)
            if val is not None and val != 0:
                jva_income = float(val)
                jva_source_tag = tag
                break

        if jva_income is not None:
            record.clean["JVA_Income_Isolated"] = jva_income

            # Remove from EBIT if it was included
            ebit_key = "OperatingIncome" if "OperatingIncome" in record.clean else "EBIT"
            clean_ebit = record.clean.get(f"clean_{ebit_key}") or record.clean.get(ebit_key)

            if clean_ebit is not None:
                # JVA is typically reported BELOW operating income in GAAP,
                # but some companies disclose above — check if it was included.
                # Conservative: flag it, let human confirm.
                record.add_flag(CleaningFlag(
                    domain=2, domain_name=domain_name,
                    metric=jva_source_tag,
                    raw_value=jva_income,
                    adjusted_value=jva_income,
                    action="flagged",
                    reason=(
                        f"JVA income of {jva_income:,.0f} detected via {jva_source_tag}. "
                        f"Isolated to clean.JVA_Income_Isolated. "
                        f"Verify if included in OperatingIncome — if so, deduct and "
                        f"value separately using PE multiple."
                    ),
                    confidence=0.75 if jva_source_tag == "IncomeLossFromEquityMethodInvestments" else 0.50,
                ))
                record.warn(
                    f"D2: JVA income {jva_income:,.0f} ({jva_source_tag}) detected. "
                    f"Must be valued separately with PE multiple and added to equity bridge."
                )

            record.domain_scores["D2_JVA"] = 0.75
        else:
            record.clean["JVA_Income_Isolated"] = 0.0
            record.domain_scores["D2_JVA"] = 1.0

        if self.verbose:
            print(f"  D2 JVA: isolated={record.clean.get('JVA_Income_Isolated', 0):,.0f}")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 3 — EBIT normalization (Liberti)
    # ─────────────────────────────────────────────────────────────────────────

    def _domain3_ebit_normalization(self, record: CleanedRecord):
        """
        Domain 3: Normalize EBIT by removing:
          - Share of result in associates (post-tax equity number — inconsistent in pre-tax EBIT)
          - Impairment losses (already in D1; this is a belt-and-suspenders check)
          - Volatile other income/expense lines (coefficient of variation check)

        Also: compute NOPAT = clean_EBIT × (1 - effective_tax_rate)
        """
        domain_name = "EBIT_Normalization"

        ebit_key = "OperatingIncome" if "OperatingIncome" in record.clean else "EBIT"

        # Use D1-adjusted EBIT if available, else raw
        normalized_ebit = (
            record.clean.get(f"clean_{ebit_key}")
            or record.clean.get(ebit_key)
        )

        if normalized_ebit is None:
            revenue = record.clean.get("Revenue") or record.raw.get("Revenue")
            opex = record.raw.get("OperatingExpenses")
            if revenue is not None and opex is not None:
                normalized_ebit = revenue - opex
                
        if normalized_ebit is None:
            pretax = record.raw.get("PretaxIncome")
            if pretax is not None:
                normalized_ebit = pretax + (record.raw.get("InterestExpense") or 0.0)

        if normalized_ebit is None:
            record.add_flag(CleaningFlag(
                domain=3, domain_name=domain_name,
                metric="NormalizedEBIT",
                raw_value=None, adjusted_value=None,
                action="missing",
                reason="Cannot normalize EBIT — source metric missing",
                confidence=0.0,
            ))
            record.domain_scores["D3_EBITNorm"] = 0.0
            return

        # Other income / expense — flag if large relative to EBIT
        other_income = record.raw.get("OtherNonoperatingIncomeExpense") or 0.0
        revenue = record.clean.get("Revenue") or record.raw.get("Revenue") or 0.0

        if revenue > 0 and abs(other_income) > abs(normalized_ebit) * 0.15:
            record.warn(
                f"D3: Other income/expense ({other_income:,.0f}) is >15% of EBIT. "
                f"Likely contains volatile non-core items. Review for normalization."
            )

        # Produce normalized EBIT
        record.clean["NormalizedEBIT"] = normalized_ebit

        # NOPAT = NormalizedEBIT × (1 − cash_tax_rate)
        # Tax rate from Domain 10 — use placeholder 21% if not yet computed
        tax_rate = record.clean.get("CashTaxRate") or 0.21
        nopat = normalized_ebit * (1 - tax_rate)
        record.clean["NOPAT"] = nopat

        record.add_flag(CleaningFlag(
            domain=3, domain_name=domain_name,
            metric="NormalizedEBIT",
            raw_value=record.raw.get(ebit_key),
            adjusted_value=normalized_ebit,
            action="adjusted" if normalized_ebit != record.raw.get(ebit_key) else "no_change",
            reason=f"Normalized EBIT confirmed. NOPAT={nopat:,.0f} at tax_rate={tax_rate:.1%}",
            confidence=0.90,
        ))

        record.domain_scores["D3_EBITNorm"] = 1.0

        if self.verbose:
            print(f"  D3 EBIT: normalized={normalized_ebit:,.0f}, NOPAT={nopat:,.0f}")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 4 — Accounting policy harmonization
    # ─────────────────────────────────────────────────────────────────────────

    def _domain4_accounting_policy(self, record: CleanedRecord, ticker: str):
        """
        Domain 4: Flag accounting policy differences that affect comparability.

        Automated checks from XBRL:
          - Inventory method: LIFO vs FIFO (InventoryLIFOReserve present → LIFO user)
          - R&D capitalization: check CapitalizedComputerSoftwareNet
          - Goodwill amortization: GoodwillAndIntangibleAssetImpairment vs GoodwillImpairmentLoss

        These are flags — not automatic adjustments (policy changes require
        sector peer comparison to determine adjustment direction).
        """
        domain_name = "AccountingPolicy"
        flags_raised = 0

        # ── LIFO detection ────────────────────────────────────────────────────
        lifo_reserve = record.raw.get("InventoryLIFOReserve")
        if lifo_reserve is not None and abs(lifo_reserve) > 0:
            record.clean["InventoryMethod"] = "LIFO"
            record.add_flag(CleaningFlag(
                domain=4, domain_name=domain_name,
                metric="InventoryMethod",
                raw_value=lifo_reserve,
                adjusted_value=None,
                action="flagged",
                reason=(
                    f"LIFO user detected (InventoryLIFOReserve={lifo_reserve:,.0f}). "
                    f"During inflation, LIFO understates inventory values and overstates COGS. "
                    f"Adjust COGS by +LIFO_reserve_change for FIFO comparability."
                ),
                confidence=0.95,
            ))
            record.warn(
                f"D4: LIFO inventory method — may need FIFO restatement for peer comparison. "
                f"LIFO reserve: {lifo_reserve:,.0f}"
            )
            flags_raised += 1
        else:
            record.clean["InventoryMethod"] = "FIFO_or_NA"

        # ── R&D capitalization detection ─────────────────────────────────────
        capitalized_rd = record.raw.get("CapitalizedComputerSoftwareNet")
        if capitalized_rd is not None and abs(capitalized_rd) > 0:
            revenue = record.raw.get("Revenue") or 1.0
            cap_pct = abs(capitalized_rd) / abs(revenue) * 100
            record.clean["CapitalizedR&D"] = capitalized_rd
            record.add_flag(CleaningFlag(
                domain=4, domain_name=domain_name,
                metric="CapitalizedR&D",
                raw_value=capitalized_rd,
                adjusted_value=None,
                action="flagged",
                reason=(
                    f"R&D capitalization detected ({cap_pct:.1f}% of revenue). "
                    f"IFRS permissive; GAAP generally expenses. "
                    f"Add back amortization of capitalized R&D to EBIT for comparability."
                ),
                confidence=0.80,
            ))
            if cap_pct > 2.0:
                record.warn(f"D4: Capitalized R&D is {cap_pct:.1f}% of revenue — material")
            flags_raised += 1

        # ── Goodwill / intangible amortization ───────────────────────────────
        intangible_amort = record.raw.get("AmortizationOfIntangibleAssets")
        if intangible_amort is not None and abs(intangible_amort) > 0:
            record.clean["IntangibleAmortization"] = intangible_amort
            record.add_flag(CleaningFlag(
                domain=4, domain_name=domain_name,
                metric="IntangibleAmortization",
                raw_value=intangible_amort,
                adjusted_value=intangible_amort,
                action="flagged",
                reason=(
                    f"Intangible amortization of {intangible_amort:,.0f} noted. "
                    f"Liberti: add back goodwill amortization to EBIT for multiple analysis "
                    f"(non-economic charge). Included in clean_EBIT only if separately identified."
                ),
                confidence=0.85,
            ))

        record.domain_scores["D4_AccountingPolicy"] = max(0.0, 1.0 - flags_raised * 0.1)

        if self.verbose:
            print(f"  D4 Policy: {flags_raised} flags raised")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 5 — Lease and off-balance-sheet normalization
    # ─────────────────────────────────────────────────────────────────────────

    def _domain5_lease_normalization(self, record: CleanedRecord,
                                     ticker: str, fiscal_year: int):
        """
        Domain 5: ASC 842 / IFRS 16 lease normalization.

        Compute EBITDAR = EBITDA + operating lease payments.
        Add PV of operating lease commitments to debt in equity bridge.

        Post-ASC 842 (FY2019+): operating leases are on-balance-sheet as
        RightOfUseAsset and OperatingLeaseLiability.
        Pre-ASC 842: must reconstruct from rent expense.
        """
        domain_name = "LeaseNormalization"

        # Operating lease liability (post-ASC 842)
        op_lease_current = record.raw.get("OperatingLeaseLiabilityCurrent") or 0.0
        op_lease_noncurrent = record.raw.get("OperatingLeaseLiabilityNoncurrent") or 0.0
        total_op_lease = op_lease_current + op_lease_noncurrent

        # Operating lease cost (the income statement charge — proxy for rent)
        op_lease_cost = record.raw.get("OperatingLeaseCost") or 0.0

        # Right-of-use asset
        rou_asset = record.raw.get("OperatingLeaseRightOfUseAsset") or 0.0

        record.clean["OperatingLeaseLiability_Total"] = total_op_lease
        record.clean["OperatingLeaseCost"] = op_lease_cost
        record.clean["RightOfUseAsset"] = rou_asset

        # EBITDAR = EBITDA + lease payments (adds back the lease cost)
        ebitda = record.clean.get("EBITDA") or record.derived.get("EBITDA")
        if ebitda is not None and op_lease_cost > 0:
            record.clean["EBITDAR"] = ebitda + op_lease_cost
            record.add_flag(CleaningFlag(
                domain=5, domain_name=domain_name,
                metric="EBITDAR",
                raw_value=ebitda,
                adjusted_value=ebitda + op_lease_cost,
                action="adjusted",
                reason=(
                    f"EBITDAR = EBITDA ({ebitda:,.0f}) + lease cost ({op_lease_cost:,.0f}). "
                    f"Puts asset-owning and asset-leasing companies on equal footing."
                ),
                confidence=0.90,
            ))

        # Equity bridge: total_op_lease goes to debt section
        if total_op_lease > 0:
            record.clean["LeaseDebt_ForEquityBridge"] = total_op_lease
            record.add_flag(CleaningFlag(
                domain=5, domain_name=domain_name,
                metric="LeaseDebt_ForEquityBridge",
                raw_value=0.0,
                adjusted_value=total_op_lease,
                action="adjusted",
                reason=(
                    f"Operating lease liability ({total_op_lease:,.0f}) added to "
                    f"debt in equity bridge. Treat as debt-equivalent."
                ),
                confidence=0.92,
            ))

            if rou_asset > 0:
                lease_ratio = total_op_lease / (record.raw.get("TotalAssets") or 1.0)
                if lease_ratio > 0.20:
                    record.warn(
                        f"D5: Operating lease liabilities are {lease_ratio:.1%} of total assets. "
                        f"Material for retailers/airlines — use EBITDAR for multiple analysis."
                    )

        record.domain_scores["D5_Lease"] = 1.0 if total_op_lease >= 0 else 0.8

        if self.verbose:
            print(f"  D5 Leases: liability={total_op_lease:,.0f}, cost={op_lease_cost:,.0f}")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 6 — Pension and post-retirement obligations
    # ─────────────────────────────────────────────────────────────────────────

    def _domain6_pension_cleaning(self, record: CleanedRecord,
                                  ticker: str, fiscal_year: int):
        """
        Domain 6: Pension deficit is debt by another name.

        Steps:
          1. Extract funded status (pension deficit) → equity bridge debt item.
          2. Separate service cost (operating) from interest cost + expected return
             (financial) in the income statement.
          3. Flag discount rate assumption for normalization.
        """
        domain_name = "PensionCleaning"

        # Funded status: negative = underfunded (deficit = debt-like obligation)
        funded_status = record.raw.get("DefinedBenefitPlanFundedStatusOfPlan")
        pension_obligation = record.raw.get("DefinedBenefitPlanBenefitObligation")
        plan_assets = record.raw.get("DefinedBenefitPlanFairValueOfPlanAssets")

        # Compute deficit if funded status not directly available
        if funded_status is None and pension_obligation and plan_assets:
            funded_status = plan_assets - pension_obligation

        if funded_status is not None:
            pension_deficit = min(0.0, funded_status)  # Only deficit counts as debt
            record.clean["PensionDeficit_ForEquityBridge"] = abs(pension_deficit)

            if abs(pension_deficit) > 0:
                total_assets = record.raw.get("TotalAssets") or 1.0
                deficit_pct = abs(pension_deficit) / total_assets * 100

                record.add_flag(CleaningFlag(
                    domain=6, domain_name=domain_name,
                    metric="PensionDeficit",
                    raw_value=funded_status,
                    adjusted_value=abs(pension_deficit),
                    action="adjusted",
                    reason=(
                        f"Pension deficit of {abs(pension_deficit):,.0f} "
                        f"({deficit_pct:.1f}% of assets) treated as debt-like obligation. "
                        f"Deduct from EV in equity bridge."
                    ),
                    confidence=0.88,
                ))

                if deficit_pct > 5.0:
                    record.warn(
                        f"D6: Material pension deficit ({abs(pension_deficit):,.0f}, "
                        f"{deficit_pct:.1f}% of assets). "
                        f"Verify discount rate assumption — restate to IG bond yield for comparability."
                    )
        else:
            record.clean["PensionDeficit_ForEquityBridge"] = 0.0

        # Service cost (operating) vs interest cost (financial)
        service_cost = record.raw.get("DefinedBenefitPlanServiceCost")
        interest_cost = record.raw.get("DefinedBenefitPlanInterestCost")
        expected_return = record.raw.get("DefinedBenefitPlanExpectedReturnOnPlanAssets")

        if service_cost is not None:
            record.clean["PensionServiceCost_Operating"] = service_cost
        if interest_cost is not None:
            record.clean["PensionInterestCost_Financial"] = interest_cost
        if expected_return is not None:
            record.clean["PensionExpectedReturn_Financial"] = expected_return

        if interest_cost and service_cost:
            record.add_flag(CleaningFlag(
                domain=6, domain_name=domain_name,
                metric="PensionExpenseReclassification",
                raw_value=service_cost + interest_cost,
                adjusted_value=service_cost,
                action="adjusted",
                reason=(
                    f"Pension: service cost ({service_cost:,.0f}) stays in EBIT. "
                    f"Interest cost ({interest_cost:,.0f}) reclassified below EBIT."
                ),
                confidence=0.82,
            ))

        record.domain_scores["D6_Pension"] = 1.0

        if self.verbose:
            deficit = record.clean.get("PensionDeficit_ForEquityBridge", 0)
            print(f"  D6 Pension: deficit={deficit:,.0f}")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 7 — Stock-based compensation and dilution
    # ─────────────────────────────────────────────────────────────────────────

    def _domain7_sbc_adjustment(self, record: CleanedRecord,
                                ticker: str, fiscal_year: int):
        """
        Domain 7: SBC is a real economic cost — employees are compensated by
        diluting existing shareholders. GAAP excludes it from 'adjusted' metrics.

        Rules:
          - SBC must be included as an operating cost (GAAP floor).
          - Flag SBC as % of FCF: >5% yellow, >10% red.
          - Calculate net annual dilution after buybacks.
        """
        domain_name = "SBC_Dilution"

        sbc = record.raw.get("SBC") or record.raw.get("ShareBasedCompensation") or 0.0
        revenue = record.raw.get("Revenue") or 1.0
        fcf = record.derived.get("FCF") or record.clean.get("FCF")

        record.clean["SBC"] = sbc

        if sbc > 0:
            sbc_pct_rev = sbc / revenue * 100

            # SBC % of FCF
            if fcf and fcf > 0:
                sbc_pct_fcf = sbc / fcf * 100
                record.clean["SBC_PctFCF"] = sbc_pct_fcf

                severity = "no_change"
                if sbc_pct_fcf > 10:
                    severity = "flagged"
                    record.warn(
                        f"D7: SBC is {sbc_pct_fcf:.1f}% of FCF — quiet dilution. "
                        f"Treat as recurring operating cost in all DCF scenarios."
                    )
                elif sbc_pct_fcf > 5:
                    severity = "flagged"
                    record.warn(f"D7: SBC is {sbc_pct_fcf:.1f}% of FCF — elevated.")

                record.add_flag(CleaningFlag(
                    domain=7, domain_name=domain_name,
                    metric="SBC_PctFCF",
                    raw_value=sbc,
                    adjusted_value=sbc,
                    action=severity,
                    reason=f"SBC={sbc:,.0f} ({sbc_pct_fcf:.1f}% of FCF, {sbc_pct_rev:.1f}% of revenue)",
                    confidence=0.92,
                ))

        # Shares dilution: basic vs diluted
        shares_basic = record.raw.get("SharesOutstanding") or record.raw.get("CommonStockSharesOutstanding")
        shares_diluted = record.raw.get("SharesDiluted") or record.raw.get("WeightedAverageNumberOfDilutedSharesOutstanding")

        # If the resolver didn't pick up a direct shares tag, derive it from
        # diluted EPS: shares = NetIncome / DilutedEPS. Both must be in the
        # same currency. ADR filers like TSM expose DilutedEarningsLossPerShare
        # but no direct share-count tag.
        if not shares_diluted:
            net_income = record.raw.get("NetIncome")
            diluted_eps = (record.raw.get("DilutedEPS")
                           or record.raw.get("DilutedEarningsLossPerShare")
                           or record.raw.get("EarningsPerShareDiluted"))
            if net_income and diluted_eps and diluted_eps != 0:
                shares_diluted = net_income / diluted_eps

        if shares_diluted:
            record.clean["SharesDiluted"] = shares_diluted

        if shares_basic and shares_diluted and shares_basic > 0:
            dilution_pct = (shares_diluted - shares_basic) / shares_basic * 100
            record.clean["ShareDilution_Pct"] = dilution_pct
            # The dilution-presence warning was redundant: DCFEngine now
            # requires clean_SharesDiluted at run time and hard-fails on the
            # outstanding-share fallback. The dilution_pct value remains
            # available in clean for downstream consumers.

        # Buyback treadmill check: if buybacks ≈ SBC, net return is zero
        buybacks = record.raw.get("Buybacks") or record.raw.get("PaymentsForRepurchaseOfCommonStock") or 0.0
        if buybacks > 0 and sbc > 0:
            net_buyback = buybacks - sbc
            record.clean["NetBuyback_AfterSBC"] = net_buyback
            if net_buyback < buybacks * 0.30:
                record.warn(
                    f"D7: Buybacks ({buybacks:,.0f}) largely offset by SBC ({sbc:,.0f}). "
                    f"Net capital return to shareholders is only {net_buyback:,.0f}."
                )

        record.domain_scores["D7_SBC"] = 1.0

        if self.verbose:
            print(f"  D7 SBC: {sbc:,.0f} ({sbc / revenue * 100:.1f}% of revenue)")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 8 — Revenue recognition and timing
    # ─────────────────────────────────────────────────────────────────────────

    def _domain8_revenue_recognition(self, record: CleanedRecord,
                                     prior: Optional[CleanedRecord]):
        """
        Domain 8: Revenue recognition quality check.

        Signals of aggressive recognition:
          - AR growth persistently exceeding revenue growth (channel stuffing).
          - Deferred revenue declining faster than revenue growing.
          - Cash collected growing slower than revenue recognized.

        Requires prior year record for YoY comparison.
        """
        domain_name = "RevenueRecognition"

        revenue = record.raw.get("Revenue") or 0.0
        ar = record.raw.get("AccountsReceivable") or record.raw.get("AccountsReceivableNetCurrent") or 0.0
        deferred_rev = record.raw.get("DeferredRevenue") or record.raw.get("DeferredRevenueCurrent") or 0.0
        cash_ops = record.raw.get("OperatingCF") or record.raw.get("NetCashProvidedByUsedInOperatingActivities") or 0.0

        record.clean["AccountsReceivable"] = ar
        record.clean["DeferredRevenue"] = deferred_rev

        if prior is not None and revenue > 0:
            prior_revenue = prior.raw.get("Revenue") or 0.0
            prior_ar = prior.raw.get("AccountsReceivable") or prior.raw.get("AccountsReceivableNetCurrent") or 0.0
            prior_deferred = prior.raw.get("DeferredRevenue") or prior.raw.get("DeferredRevenueCurrent") or 0.0

            # Revenue growth
            rev_growth = _pct_change(revenue, prior_revenue)

            # AR growth
            ar_growth = _pct_change(ar, prior_ar) if prior_ar > 0 else None

            if rev_growth is not None and ar_growth is not None:
                ar_rev_spread = ar_growth - rev_growth
                record.clean["AR_RevGrowth_Spread"] = ar_rev_spread

                if ar_rev_spread > 0.10:
                    record.add_flag(CleaningFlag(
                        domain=8, domain_name=domain_name,
                        metric="AR_RevGrowth_Spread",
                        raw_value=ar_growth,
                        adjusted_value=rev_growth,
                        action="flagged",
                        reason=(
                            f"AR growing {ar_rev_spread:.1%} faster than revenue. "
                            f"Potential aggressive recognition or channel stuffing. "
                            f"AR: {ar_growth:.1%}, Revenue: {rev_growth:.1%}"
                        ),
                        confidence=0.80,
                    ))
                    record.warn(
                        f"D8: AR growth ({ar_growth:.1%}) exceeds revenue growth ({rev_growth:.1%}) "
                        f"by {ar_rev_spread:.1%}. Investigate recognition timing."
                    )

            # Cash collection quality: operating CF vs revenue
            if revenue > 0 and cash_ops > 0:
                cash_conversion = cash_ops / revenue
                record.clean["CashCollectionRatio"] = cash_conversion
                if rev_growth and rev_growth > 0.10 and cash_conversion < 0.10:
                    record.warn(
                        f"D8: Revenue growing {rev_growth:.1%} but cash collection "
                        f"ratio is only {cash_conversion:.1%}. Revenue quality concern."
                    )

            # Deferred revenue signal (SaaS backlog health)
            if prior_deferred > 0:
                deferred_growth = _pct_change(deferred_rev, prior_deferred)
                record.clean["DeferredRevenue_Growth"] = deferred_growth
                if deferred_growth is not None and rev_growth is not None:
                    if deferred_growth < -0.15 and rev_growth > 0.10:
                        record.warn(
                            f"D8: Deferred revenue declining ({deferred_growth:.1%}) while "
                            f"revenue growing ({rev_growth:.1%}). Backlog being drawn down — "
                            f"future revenue visibility decreasing."
                        )

        record.domain_scores["D8_Revenue"] = 1.0

        if self.verbose:
            spread = record.clean.get("AR_RevGrowth_Spread", 0) or 0
            print(f"  D8 Revenue: AR/Rev spread={spread:.1%}")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 9 — Working capital and cash flow normalization
    # ─────────────────────────────────────────────────────────────────────────

    def _domain9_working_capital(self, record: CleanedRecord,
                                 prior: Optional[CleanedRecord]):
        """
        Domain 9: Separate structural WC from cyclical swings.
        Split maintenance capex from growth capex.
        Compute normalized FCF.
        """
        domain_name = "WorkingCapital"

        # Current assets and liabilities for NWC
        current_assets = record.raw.get("CurrentAssets") or record.raw.get("AssetsCurrent") or 0.0
        current_liab = record.raw.get("LiabilitiesCurrent") or 0.0
        cash = record.raw.get("Cash") or record.raw.get("CashAndCashEquivalentsAtCarryingValue") or 0.0

        # NWC = (Current Assets - Cash) - Current Liabilities
        # Exclude cash from current assets — it is not an operating asset
        nwc = (current_assets - cash) - current_liab
        record.clean["NWC"] = nwc

        # Revenue for structural WC ratio
        revenue = record.raw.get("Revenue") or 1.0

        # Structural NWC ≈ 2–5% of revenue (rough proxy for working capital floor)
        structural_nwc_estimate = revenue * 0.03
        record.clean["StructuralNWC_Estimate"] = structural_nwc_estimate

        # YoY NWC change
        if prior is not None:
            prior_nwc = prior.clean.get("NWC") or 0.0
            delta_nwc = nwc - prior_nwc
            record.clean["DeltaNWC"] = delta_nwc

            # Large single-year NWC swing was previously warned here. DCFEngine
            # now uses structural NWC (3% of revenue) in all scenarios so the
            # advisory is built into the calc layer; the YoY swing is preserved
            # in clean["DeltaNWC"] for any downstream consumer that wants it.

        # Capex
        capex = record.raw.get("CapEx") or record.raw.get("capex") or record.raw.get("PaymentsToAcquirePropertyPlantAndEquipment")
        if capex is not None:
            capex = abs(capex)  # capex is usually negative in XBRL cash flow
        record.clean["CapEx_Total"] = capex

        # Depreciation as proxy for maintenance capex
        depreciation, dep_prov = self._compute_depreciation_total(record)
        record.clean["Depreciation_Total"] = depreciation

        if depreciation is None:
            print(f"❌ MISSING DATA: Depreciation is missing. No silent fallback applied.")
        if capex is None:
            print(f"❌ MISSING DATA: CapEx is missing. No silent fallback applied.")

        # Maintenance capex ≈ depreciation (conservative assumption)
        # Growth capex = total capex - maintenance capex
        if capex is not None and depreciation is not None:
            maintenance_capex = min(capex, depreciation)
            growth_capex = max(0.0, capex - maintenance_capex)
            record.clean["MaintenanceCapEx"] = maintenance_capex
            record.clean["GrowthCapEx"] = growth_capex

            # CapEx > 2x depreciation was previously warned. DCFEngine bull/base
            # scenarios already drive scenario-specific capex_pct_revenue from
            # the input ratio, so the "model growth investment" guidance is
            # built into the calc layer. MaintenanceCapEx / GrowthCapEx remain
            # in clean for downstream forensic consumers.
        else:
            maintenance_capex = None
            growth_capex = None
            record.clean["MaintenanceCapEx"] = None
            record.clean["GrowthCapEx"] = None

        record.domain_scores["D9_WorkingCapital"] = 1.0

        if self.verbose:
            m_capex_str = f"{maintenance_capex:,.0f}" if maintenance_capex is not None else "N/A"
            g_capex_str = f"{growth_capex:,.0f}" if growth_capex is not None else "N/A"
            print(f"  D9 WC: NWC={nwc:,.0f}, maint_capex={m_capex_str}, growth_capex={g_capex_str}")

    # ─────────────────────────────────────────────────────────────────────────
    # Domain 10 — Tax sustainability
    # ─────────────────────────────────────────────────────────────────────────

    def _domain10_tax_sustainability(self, record: CleanedRecord,
                                     prior: Optional[CleanedRecord]):
        """
        Domain 10: Tax rate quality and sustainability.

        Checks:
          - Cash tax rate vs GAAP effective rate divergence (>500bps = investigate).
          - NOL carryforward presence (temporary tax advantage).
          - OECD Pillar Two exposure flag.
        """
        domain_name = "TaxSustainability"

        # Income tax expense (GAAP)
        tax_expense = record.raw.get("IncomeTaxExpenseBenefit") or 0.0
        pretax_income = record.raw.get("IncomeLossFromContinuingOperationsBeforeIncomeTaxesExtraordinaryItemsNoncontrollingInterest") or 0.0

        # Cash taxes paid (from cash flow statement)
        cash_taxes = record.raw.get("CashTaxesPaid") or record.raw.get("IncomeTaxesPaid") or record.raw.get("IncomeTaxesPaidNet") or 0.0

        # GAAP effective tax rate
        gaap_tax_rate = _safe_div(tax_expense, pretax_income) if pretax_income != 0 else None

        # Cash tax rate (preferred for DCF)
        cash_tax_rate = _safe_div(cash_taxes, pretax_income) if pretax_income != 0 else None

        if gaap_tax_rate is not None:
            record.clean["GAAP_TaxRate"] = gaap_tax_rate
        if cash_tax_rate is not None:
            record.clean["CashTaxRate"] = cash_tax_rate
            # Update NOPAT with proper cash tax rate (corrects D3 placeholder)
            normalized_ebit = record.clean.get("NormalizedEBIT")
            if normalized_ebit is not None:
                record.clean["NOPAT"] = normalized_ebit * (1 - cash_tax_rate)

        # Divergence check: >500bps = investigate
        if gaap_tax_rate is not None and cash_tax_rate is not None:
            divergence_bps = abs(gaap_tax_rate - cash_tax_rate) * 10000
            record.clean["TaxRate_Divergence_BPS"] = divergence_bps

            if divergence_bps > 500:
                record.add_flag(CleaningFlag(
                    domain=10, domain_name=domain_name,
                    metric="TaxRate_Divergence",
                    raw_value=gaap_tax_rate,
                    adjusted_value=cash_tax_rate,
                    action="flagged",
                    reason=(
                        f"Tax rate divergence: GAAP={gaap_tax_rate:.1%}, "
                        f"Cash={cash_tax_rate:.1%} ({divergence_bps:.0f}bps). "
                        f">500bps requires investigation of NOL utilization or deferred taxes."
                    ),
                    confidence=0.90,
                ))
                record.warn(
                    f"D10: Tax rate divergence of {divergence_bps:.0f}bps. "
                    f"Use cash tax rate in DCF. Investigate NOL schedule."
                )

        # NOL carryforward presence
        nol = record.raw.get("OperatingLossCarryforwards") or record.raw.get("DeferredTaxAssetsOperatingLossCarryforwards")
        if nol and abs(nol) > 0:
            record.clean["NOL_Carryforward"] = nol
            record.add_flag(CleaningFlag(
                domain=10, domain_name=domain_name,
                metric="NOL_Carryforward",
                raw_value=nol,
                adjusted_value=nol,
                action="flagged",
                reason=(
                    f"NOL carryforward of {nol:,.0f} detected. "
                    f"Temporary tax advantage — model year of exhaustion as step-up in DCF."
                ),
                confidence=0.85,
            ))
            record.warn(f"D10: NOL carryforward {nol:,.0f} — tax advantage is temporary.")

        # OECD Pillar Two: flag if effective rate < 15%
        effective_rate = cash_tax_rate or gaap_tax_rate
        if effective_rate is not None and 0 < effective_rate < 0.15:
            record.add_flag(CleaningFlag(
                domain=10, domain_name=domain_name,
                metric="PillarTwo_Exposure",
                raw_value=effective_rate,
                adjusted_value=0.15,
                action="flagged",
                reason=(
                    f"Effective tax rate {effective_rate:.1%} is below OECD 15% minimum. "
                    f"Pillar Two exposure — model incremental tax cost in base case DCF."
                ),
                confidence=0.80,
            ))
            record.warn(
                f"D10: Effective rate {effective_rate:.1%} below Pillar Two 15% floor. "
                f"Quantify incremental tax cost for base case."
            )

        record.domain_scores["D10_Tax"] = 1.0

        if self.verbose:
            print(f"  D10 Tax: GAAP={gaap_tax_rate:.1%}, Cash={cash_tax_rate:.1%}"
                  if gaap_tax_rate and cash_tax_rate else "  D10 Tax: rates unavailable")

    # ─────────────────────────────────────────────────────────────────────────
    # Derived metrics
    # ─────────────────────────────────────────────────────────────────────────

    def _compute_depreciation_total(self, record: CleanedRecord) -> Tuple[Optional[float], str]:
        """
        Reconstruct full D&A from filer-reported components or canonical aggregate.
        Returns (value, provenance) where provenance is one of:
          - "raw_aggregate": filer reported the canonical combined tag
          - "derived_components": summed from individual components
          - "missing": neither aggregate nor any components resolved
        """
        aggregate = record.raw.get("Depreciation_Total_Aggregate")
        if aggregate is not None and aggregate != 0:
            return aggregate, "raw_aggregate"
        
        components = [
            record.raw.get("Depreciation_Tangible") or 0.0,
            record.raw.get("IntangibleAmortization") or 0.0,
            record.raw.get("FinanceLeaseAmortization") or 0.0,
            record.raw.get("CapitalizedSoftwareAmortization") or 0.0,
        ]
        if any(c > 0.0 for c in components):
            return sum(components), "derived_components"
        
        return None, "missing"

    def _compute_derived(self, record: CleanedRecord):
        """
        Compute key derived metrics from cleaned values.
        These feed the Phase 2 valuation engine.
        """
        r = record  # shorthand
        
        # --- Missing Field Fallbacks ---
        # Derive TotalLiabilities. Filers like LLY don't report a rolled-up
        # `Liabilities` tag in XBRL but do file `LiabilitiesCurrent` and
        # `LiabilitiesNoncurrent`, so prefer that summation. Fall back to
        # TotalAssets - TotalEquity only when neither component is present.
        if r.raw.get("TotalLiabilities") is None:
            lc = r.raw.get("LiabilitiesCurrent")
            lnc = r.raw.get("LiabilitiesNoncurrent")
            if lc is not None and lnc is not None:
                r.raw["TotalLiabilities"] = lc + lnc
            else:
                ta = r.raw.get("TotalAssets")
                te = r.raw.get("TotalEquity")
                if ta is not None and te is not None:
                    r.raw["TotalLiabilities"] = ta - te
        # Mirror to derived so legacy consumers still see the value
        if r.raw.get("TotalLiabilities") is not None and r.derived.get("TotalLiabilities") is None:
            r.derived["TotalLiabilities"] = r.raw["TotalLiabilities"]

        # Derive SG&A from components when the rolled-up tag isn't filed.
        # MSFT files SellingAndMarketingExpense + GeneralAndAdministrativeExpense
        # separately and does NOT file SellingGeneralAndAdministrativeExpense.
        # AAPL/LLY file the rolled-up tag (preferred path; this branch is a no-op).
        if r.raw.get("SG&A") is None:
            sm = r.raw.get("SellingAndMarketing")
            ga = r.raw.get("GeneralAndAdministrative")
            if sm is not None and ga is not None:
                r.raw["SG&A"] = sm + ga
            elif ga is not None:
                # Last-resort fallback: G&A alone is better than nothing for
                # filers who report only that component, but flag it as
                # under-reported for forensic agents to notice.
                r.raw["SG&A"] = ga

        # Derive OperatingIncome
        if r.raw.get("OperatingIncome") is None:
            rev = r.raw.get("Revenue")
            cogs = r.raw.get("COGS")
            
            # Strict guardrail: R&D missing from raw is treated as 0.0. 
            # If explicitly None but reported? We treat it as 0.0 if not present.
            rnd = r.raw.get("R&D") or 0.0
            iprd = r.raw.get("AcquiredInProcessRnD") or 0.0
            sga = r.raw.get("SG&A")
            opex = r.raw.get("OperatingExpenses")
            
            # Path 1: All traditional components present
            if rev is not None and cogs is not None and sga is not None:
                r.derived["OperatingIncome"] = rev - cogs - rnd - iprd - sga
                r.derived_provenance["OperatingIncome"] = "derived"
            # Path 2: Aggregated operating expenses present
            elif rev is not None and opex is not None:
                r.derived["OperatingIncome"] = rev - opex
                r.derived_provenance["OperatingIncome"] = "derived"
                
            nuc_fuel = r.raw.get("PaymentsForProceedsFromNuclearFuel")
            
        revenue = r.get("Revenue")
        ebit = r.clean.get("NormalizedEBIT") or r.derived.get("OperatingIncome") or r.raw.get("OperatingIncome") or r.raw.get("EBIT")
        if ebit is None and revenue is not None:
            if opex is not None:
                ebit = revenue - opex
        
        if ebit is None:
            pretax = r.raw.get("PretaxIncome")
            if pretax is not None:
                ebit = pretax + (r.raw.get("InterestExpense") or 0.0)
        dep_val, dep_prov = self._compute_depreciation_total(r)
        if dep_val is not None:
            r.derived["Depreciation_Total"] = dep_val
            r.derived_provenance["Depreciation_Total"] = dep_prov
            depreciation = dep_val
        else:
            depreciation = None
            
        capex_raw = r.clean.get("CapEx_Total") or r.raw.get("CapEx") or r.derived.get("CapEx")
        capex = abs(capex_raw) if capex_raw is not None else None
        if capex is not None:
            r.derived["CapEx"] = capex
        
        delta_nwc = r.clean.get("DeltaNWC") or 0.0
        cash_tax_rate = r.clean.get("CashTaxRate") or 0.21
        nopat = r.clean.get("NOPAT")
        total_assets = r.raw.get("TotalAssets") or 0.0
        total_equity = r.raw.get("TotalEquity") or 1.0
        long_term_debt = r.raw.get("LongTermDebt") or 0.0
        net_income = r.raw.get("NetIncome") or 0.0
        cash = r.raw.get("Cash") or 0.0
        cash_ops = r.raw.get("OperatingCF") or r.raw.get("NetCashProvidedByUsedInOperatingActivities") or r.clean.get("OperatingCF") or 0.0

        # EBITDA
        if ebit is not None:
            if depreciation is not None:
                ebitda = ebit + depreciation
            else:
                ebitda = None
            r.derived["EBITDA"] = ebitda
            # Also store in clean for Domain 5 to use
            r.clean["EBITDA"] = ebitda
            
            # Liberti EBITDA = EBITDA + Expensed R&D (treats R&D as capital investment)
            rd_expense = r.raw.get("R&D") or r.raw.get("ResearchAndDevelopmentExpense") or 0.0
            if ebitda is not None:
                r.derived["EBITDA_Liberti"] = ebitda + rd_expense
                r.clean["EBITDA_Liberti"] = ebitda + rd_expense

        # FCF = Operating CF - CapEx
        if cash_ops is not None:
            if capex is not None:
                capex_mag = abs(capex)
                fcf = cash_ops - capex_mag
                
                # Check for registry overrides
                override = ISSUER_OVERRIDES.get(r.ticker)
                if override and override["metric"] == "FCF" and r.fiscal_year >= override["effective_from_fy"]:
                    logic = override["adjustment_logic"]
                    if logic["operation"] == "subtract":
                        # AMZN FCF deducts Finance Lease Principal Repayments
                        # Map to known XBRL tags AMZN uses
                        adj_val = r.raw.get("FinanceLeasePrincipalPayments") or r.raw.get("RepaymentsOfDebtAndFinanceLeaseObligations")
                        if adj_val is not None:
                            fcf -= abs(adj_val)
                            r.add_flag(CleaningFlag(
                                domain=1, domain_name="Financials",
                                metric="FCF",
                                raw_value=fcf + abs(adj_val),
                                adjusted_value=fcf,
                                action="overridden",
                                reason=f"Registry Override: {override['rationale']}",
                                confidence=1.0
                            ))
                            if self.verbose:
                                print(f"[AUDIT] Applied {r.ticker} FCF override: -{abs(adj_val)}")
            else:
                fcf = None
            r.derived["FCF"] = fcf
            r.clean["FCF"] = fcf

        # FCF (CFA formula): NOPAT + D&A - CapEx - ΔNWC
        if nopat is not None:
            if depreciation is not None and capex is not None:
                fcff = nopat + depreciation - capex - delta_nwc
            else:
                fcff = None
            r.derived["FCFF"] = fcff

        # Gross margin
        cogs = r.raw.get("COGS")
        if cogs is None:
            cogs = r.raw.get("CostOfServices") or r.raw.get("MedicalClaims")
        
        if revenue and revenue > 0:
            if cogs is not None:
                gross_profit = revenue - cogs
                r.derived["GrossProfit"] = gross_profit
                r.derived["GrossMargin_Pct"] = gross_profit / revenue * 100
            else:
                r.derived["GrossProfit"] = None
                r.derived["GrossMargin_Pct"] = None
                if self.verbose:
                    print(f"[AUDIT] {r.ticker} missing COGS/CostOfServices; GrossMargin set to None")

            if ebit:
                r.derived["EBIT_Margin_Pct"] = ebit / revenue * 100
            ebitda_val = r.derived.get("EBITDA")
            if ebitda_val:
                r.derived["EBITDA_Margin_Pct"] = ebitda_val / revenue * 100

        # FCF margin
        fcf_val = r.derived.get("FCF")
        if fcf_val and revenue and revenue > 0:
            r.derived["FCF_Margin_Pct"] = fcf_val / revenue * 100

        # ROE
        if net_income and total_equity and total_equity != 0:
            r.derived["ROE"] = net_income / total_equity

        # Net Debt — enterprise-value definition.
        # Gross debt:
        #   long-term debt (noncurrent senior notes)
        # + short-term debt (commercial paper / ST notes)
        # + current portion of long-term debt (LT debt maturing < 12 mo)
        # + finance lease noncurrent (debt-equivalent obligations)
        # Liquid offsets:
        #   cash + short-term investments + long-term marketable securities
        # AAPL's $77B+ marketable-securities portfolio + $13B current LT-debt
        # piece were both invisible before this fix.
        st_debt = r.raw.get("ShortTermDebt") or 0.0
        current_lt_debt = r.raw.get("CurrentPortionLongTermDebt") or 0.0

        # Finance lease debt — total of current + noncurrent.
        # AAPL files them as separate tags; MSFT files only the consolidated
        # `FinanceLeaseLiability` total. Prefer the explicit decomposition
        # when both pieces are present; fall back to the consolidated total.
        fl_curr = r.raw.get("LeaseLiabilityCurrent_Finance")
        fl_nc = r.raw.get("LeaseLiabilityNoncurrent_Finance")
        fl_total = r.raw.get("FinanceLeaseLiability_Total")
        if fl_curr is not None or fl_nc is not None:
            finance_lease_total = (fl_curr or 0.0) + (fl_nc or 0.0)
        else:
            finance_lease_total = fl_total or 0.0

        gross_debt = long_term_debt + st_debt + current_lt_debt + finance_lease_total

        st_invest = r.raw.get("ShortTermInvestments") or 0.0
        lt_invest = r.raw.get("LongTermInvestments") or 0.0
        liquid_assets = cash + st_invest + lt_invest

        net_debt = gross_debt - liquid_assets
        r.derived["NetDebt"] = net_debt

        # Invested Capital (financing approach)
        if total_assets > 0:
            excess_cash = max(0.0, cash - (revenue or 0) * 0.02) if revenue else 0.0
            short_term_debt = r.raw.get("ShortTermDebt", 0.0) or 0.0
            total_debt = long_term_debt + short_term_debt
            
            # Financing-side definition: TotalDebt + TotalEquity - ExcessCash
            # Cleanly sidesteps operating liability distortions (e.g. ORCL deferred revenue trap)
            invested_capital_raw = total_debt + total_equity - excess_cash
            
            # Floor invested capital at 5% of revenue to prevent absurd ROICs 
            # for companies operating with negative working capital / high cash
            floor_ic = (revenue * 0.05) if revenue else 0.0
            invested_capital = max(floor_ic, invested_capital_raw)
                
            r.derived["InvestedCapital"] = invested_capital

            # ROIC = NOPAT / Invested Capital
            if nopat and invested_capital and invested_capital != 0:
                r.derived["ROIC"] = nopat / invested_capital

        if self.verbose:
            ebitda_str = f"{r.derived.get('EBITDA', 0):,.0f}" if r.derived.get("EBITDA") else "N/A"
            fcf_str = f"{r.derived.get('FCF', 0):,.0f}" if r.derived.get("FCF") else "N/A"
            roic_str = f"{r.derived.get('ROIC', 0):.1%}" if r.derived.get("ROIC") else "N/A"
            print(f"  Derived: EBITDA={ebitda_str}, FCF={fcf_str}, ROIC={roic_str}")

    # ─────────────────────────────────────────────────────────────────────────
    # Quality scoring
    # ─────────────────────────────────────────────────────────────────────────

    def _score_quality(self, record: CleanedRecord):
        """
        Aggregate all domain scores into an overall quality score.
        Also applies penalties for missing critical metrics and blocking errors.
        """
        if not record.domain_scores:
            record.overall_quality_score = 0.0
            return

        # Average domain scores
        base_score = sum(record.domain_scores.values()) / len(record.domain_scores)

        # Penalty: missing critical metrics
        critical_missing = 0
        for tag in ["Revenue", "NormalizedEBIT", "NOPAT"]:
            if record.clean.get(tag) is None and record.raw.get(tag) is None:
                critical_missing += 1

        # Penalty: warnings and errors
        warning_penalty = len(record.cleaning_warnings) * 0.02
        error_penalty = len(record.blocking_errors) * 0.10
        missing_penalty = critical_missing * 0.10

        final_score = max(0.0, min(1.0,
            base_score - warning_penalty - error_penalty - missing_penalty
        ))
        record.overall_quality_score = round(final_score, 3)
