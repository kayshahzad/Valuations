"""
aletheia/data/tag_resolver.py

Tag Resolution Layer
====================
Bridges the gap between canonical_transformer.py output (lowercase tags like
`revenue`, `ebit`, `cash`) and the cleaning engine expectations (PascalCase
like `Revenue`, `OperatingIncome`, `Cash`).

Also handles the XBRL tag → standard tag mapping for tags that the
canonical transformer did not resolve (SBC, lease, pension, JVA, D&A
components, etc.) by reading directly from the raw XBRL facts.

This is the translation layer — it runs BEFORE the cleaning engine
and enriches the canonical record with the full set of metrics.

Responsibilities (extraction only):
- Sector-aware priority list selection (driven by UNIVERSE.sector)
- IFRS taxonomy dispatch (driven by UNIVERSE.is_ifrs_filer)
- Resolution strategy dispatch (first/max/sum)
- Period_end_date anchored to Revenue's extraction
- D&A components extracted independently; aggregation belongs to cleaning_engine
- Tag misses logged for universe-level diagnostic visibility
- period_end_date_missing flag set when Revenue resolves without a date

Non-responsibilities (live elsewhere):
- Derivations like Depreciation_Total, OperatingIncome fallback (cleaning_engine)
- Bounds and contracts (ingestion_validator)
- Calc-layer policy (aletheia/tools/)

Usage (called internally by CleaningEngine._load_and_pivot):
    resolver = TagResolver()
    wide_dict = resolver.enrich(wide_dict, ticker, fiscal_year)
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from config.tag_mappings import (
    FIELD_MAPPINGS,
    CANONICAL_ALIASES,
    RESOLUTION_STRATEGY,
)
from config.sign_conventions import get_sign_convention
from config.ticker_classification import UNIVERSE


# Required fields whose absence triggers a tag_miss log entry.
# D&A is handled separately because it's now component-based — the resolver
# logs a single miss only when ALL D&A sources fail to resolve.
REQUIRED_FIELDS = [
    "Revenue",
    "OperatingIncome",
    "NetIncome",
    "OperatingCF",
    "CapEx",
    "TotalAssets",
    "TotalLiabilities",
    "Cash",
]

# D&A component fields. Cleaning engine derives Depreciation_Total from these.
# The resolver only checks "did at least one of these resolve" for tag_miss
# purposes — the cleaning engine decides whether the combination is sufficient.
DA_COMPONENT_FIELDS = [
    "Depreciation_Total_Aggregate",
    "Depreciation_Tangible",
    "IntangibleAmortization",
    "FinanceLeaseAmortization",
    "CapitalizedSoftwareAmortization",
]

# Currency units accepted from XBRL units sections.
# DKK: Novo Nordisk (NVO) and other Danish filers report their 20-F under
# IFRS in Danish kroner; without DKK here every financial fact is skipped and
# the filer cleans to an empty record (the NVO FY2021-2025 hole).
ACCEPTED_UNITS = ("USD", "shares", "pure", "EUR", "TWD", "CAD", "GBP", "JPY", "CHF", "DKK")
# Composite per-share units (e.g. "USD/shares", "TWD/shares") — used for EPS facts.
PER_SHARE_UNITS = ("USD/shares", "TWD/shares", "EUR/shares", "GBP/shares", "JPY/shares", "CAD/shares", "CHF/shares", "DKK/shares")

# Tags where SEC filings sometimes report a 9-12 month subset rather than full
# fiscal year. Wider duration tolerance for these tags only.
DA_TAGS_LOOSE_DURATION = {
    "DepreciationDepletionAndAmortization",
    "DepreciationAndAmortization",
    "DepreciationAmortizationAndAccretionNet",
    "DepreciationAndAmortisationExpense",
}


class TagResolver:
    """
    Enriches the canonical wide_dict with:
    1. Normalized PascalCase keys (maps 'revenue' → 'Revenue', etc.)
    2. Supplemental metrics from raw XBRL (SBC, leases, pension, JVA, D&A components)
    3. period_end_date anchored to Revenue's extraction
    4. period_end_date_missing flag when no date can be anchored
    """

    def __init__(self, raw_dir: str = "valuation_data/raw/sec"):
        self.raw_dir = Path(raw_dir)
        self._cik_cache: Dict[str, Optional[str]] = {}

    # ─────────────────────────────────────────────────────────────────────
    # Public entry points
    # ─────────────────────────────────────────────────────────────────────

    def enrich(
        self,
        wide_dict: Dict[str, float],
        ticker: str,
        fiscal_year: int,
    ) -> Dict[str, float]:
        """
        Full enrichment pipeline: normalize keys then supplement from XBRL.
        Sets period_end_date_missing=True if no date could be anchored.
        Logs missing required fields and missing period_end_date for diagnostics.
        """
        normalized = self.normalize_keys(wide_dict)
        enriched = self.enrich_from_xbrl(normalized, ticker, fiscal_year)

        # Behavioral flag: downstream CAGR computation excludes records with
        # period_end_date_missing=True from lookbacks. This is the strict-fail
        # mechanism that protects empirical CAGR from time-window drift.
        if "period_end_date" not in enriched or not enriched.get("period_end_date"):
            enriched["period_end_date_missing"] = True
            self._log_missing_period_end_date(enriched, ticker, fiscal_year)
        else:
            enriched["period_end_date_missing"] = False

        self._log_missing_tags(enriched, ticker, fiscal_year)
        return enriched

    def normalize_keys(self, wide_dict: Dict[str, float]) -> Dict[str, float]:
        """
        Step 1: Normalize all canonical tag names to PascalCase standard names.
        Handles both lowercase (transformer output) and PascalCase (direct XBRL).
        """
        normalized = {}
        for raw_key, value in wide_dict.items():
            clean_key = CANONICAL_ALIASES.get(raw_key, raw_key)
            if clean_key and value is not None:
                # Preserve non-zero values over zero values when both keys collide
                if clean_key not in normalized or (
                    normalized[clean_key] == 0 and value != 0
                ):
                    normalized[clean_key] = value
        return normalized

    def enrich_from_xbrl(
        self,
        wide_dict: Dict[str, float],
        ticker: str,
        fiscal_year: int,
    ) -> Dict[str, float]:
        """
        Step 2: Pull supplemental metrics directly from raw XBRL facts for
        fields the transformer did not capture.

        Only fills gaps — never overwrites valid (non-null, non-zero) values.
        """
        raw_facts = self._load_raw_facts(ticker)
        if raw_facts is None:
            return wide_dict

        facts = raw_facts.get("facts", {})
        us_gaap = facts.get("us-gaap", {})
        ifrs = facts.get("ifrs-full", {})
        dei = facts.get("dei", {})

        # Combined namespace lookup. US-GAAP overrides IFRS when both present
        # because most filers in the universe are US-GAAP. The IFRS sector key
        # in tag_mappings handles ADR/foreign filers via is_ifrs_filer dispatch.
        # `dei` (Document and Entity Information) is checked last as fallback —
        # filers like V report SharesOutstanding only in the dei namespace.
        combined_facts = {}
        combined_facts.update(dei)
        combined_facts.update(ifrs)
        combined_facts.update(us_gaap)

        enriched = dict(wide_dict)
        sector_key, is_ifrs = self._get_classification_keys(ticker)

        for clean_name, rules in FIELD_MAPPINGS.items():
            # Never overwrite a valid value already set by canonical transformer
            if enriched.get(clean_name) not in (None, 0.0):
                continue

            priority_list = self._select_priority_list(rules, sector_key, is_ifrs)
            if not priority_list:
                continue

            sign_convention = get_sign_convention(clean_name)
            strategy = RESOLUTION_STRATEGY.get(clean_name, "first")

            best_val, best_end = self._resolve_field(
                priority_list=priority_list,
                combined_facts=combined_facts,
                fiscal_year=fiscal_year,
                sign_convention=sign_convention,
                strategy=strategy,
            )

            if best_val is not None:
                enriched[clean_name] = best_val

                # Anchor period_end_date EXCLUSIVELY to Revenue's extraction.
                # Other fields' end dates are intentionally ignored to prevent
                # restated balance sheet items from contaminating the anchor.
                if clean_name == "Revenue" and best_end:
                    enriched["period_end_date"] = best_end

        return enriched

    # ─────────────────────────────────────────────────────────────────────
    # Classification dispatch
    # ─────────────────────────────────────────────────────────────────────

    def _get_classification_keys(self, ticker: str) -> Tuple[Optional[str], bool]:
        """
        Read sector and IFRS-filer status directly from UNIVERSE.

        Returns (sector_key_lowercase, is_ifrs_filer).
        Returns (None, False) for tickers not in UNIVERSE — those fall through
        to the default priority list with US-GAAP semantics.
        """
        classification = UNIVERSE.get(ticker)
        if classification is None:
            return None, False
        return classification.sector.lower(), classification.is_ifrs_filer

    def _select_priority_list(
        self,
        rules: dict,
        sector_key: Optional[str],
        is_ifrs: bool,
    ) -> List[str]:
        """
        Priority list selection (replace semantics):

        1. If is_ifrs_filer and rules has 'ifrs' key, use IFRS list.
        2. Otherwise, if rules has a key matching sector_key, use sector list.
        3. Otherwise, fall back to default.

        Replace semantics mean a sector-specific list fully supersedes the
        default; it does NOT prepend or extend. If a sector list needs default
        tags as fallback, those tags must be repeated in the sector list.
        Future work: introduce explicit 'extends' pattern in tag_mappings.
        """
        if is_ifrs and "ifrs" in rules:
            return rules["ifrs"]
        if sector_key and sector_key in rules:
            return rules[sector_key]
        return rules.get("default", [])

    # ─────────────────────────────────────────────────────────────────────
    # Field resolution
    # ─────────────────────────────────────────────────────────────────────

    def _resolve_field(
        self,
        priority_list: List[str],
        combined_facts: dict,
        fiscal_year: int,
        sign_convention: Optional[str],
        strategy: str,
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Walk the priority list and resolve a single field's value.
        Returns (value, end_date) or (None, None) if no tag resolves.

        Strategies:
        - "first": return the first valid match in priority order
        - "max":   evaluate all candidates, return the one with maximum value
        - "sum":   evaluate all candidates, return their sum (uses latest end_date)
        """
        candidates: List[Tuple[float, Optional[str]]] = []

        for xbrl_tag in priority_list:
            if xbrl_tag not in combined_facts:
                continue

            val, end_date = self._extract_value(
                combined_facts[xbrl_tag], fiscal_year, tag_name=xbrl_tag
            )
            if val is None:
                continue

            # Normalize sign for cash outflows etc.
            if sign_convention == "abs":
                val = abs(val)

            if val != 0:
                candidates.append((val, end_date))
                if strategy == "first":
                    break

        if not candidates:
            return None, None

        if strategy == "max":
            return max(candidates, key=lambda x: x[0])
        if strategy == "sum":
            total = sum(v for v, _ in candidates)
            latest_end = max((e for _, e in candidates if e), default=None)
            return total, latest_end
        # "first" or unknown strategy
        return candidates[0]

    def _extract_value(
        self,
        concept: dict,
        fiscal_year: int,
        tag_name: str = "",
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Extract the value for a given concept and fiscal year from XBRL units.

        Filtering rules:
        - Form must be 10-K, 20-F, or 40-F (annual filings)
        - fy field must equal fiscal_year, or fiscal_year+1 for offset filers
          (some 20-F filers have SEC fy label offset by +1 from actual FY end)
        - Domestic 10-K filings cannot use the +1 offset (prevents leakage)
        - Duration must be 330-400 days for most tags, 300-400 for D&A tags
          (some filers report partial-year D&A in 10-Ks)
        - Latest end_date wins to avoid prior-year restated values
        - Currency conversion to USD applied if foreign currency unit detected

        Returns (value, end_date) or (None, None) if no valid extraction.
        """
        best_val_exact = None
        best_end_date_exact = ""
        best_val_offset = None
        best_end_date_offset = ""
        best_unit = "USD"

        min_days = 300 if tag_name in DA_TAGS_LOOSE_DURATION else 330
        max_days = 400

        for unit_type, units in concept.get("units", {}).items():
            if unit_type not in ACCEPTED_UNITS and unit_type not in PER_SHARE_UNITS:
                continue

            for u in units:
                filing_fy = u.get("fy")
                if u.get("form") not in ("10-K", "20-F", "40-F"):
                    continue
                if filing_fy not in (fiscal_year, fiscal_year + 1):
                    continue

                is_offset = (filing_fy == fiscal_year + 1)

                # Prevent offset leakage for domestic filers
                if is_offset and u.get("form") == "10-K":
                    continue

                # Audit log when offset fallback fires (foreign filer reconciliation)
                if is_offset:
                    print(
                        f"[AUDIT] Using FY+1 fallback for {tag_name} "
                        f"(found in {filing_fy} {u.get('form')})"
                    )

                start = u.get("start")
                end = u.get("end")
                val = u.get("val")

                if val is None or not end:
                    continue

                # Point-in-time metric: take latest end_date
                if not start:
                    if not is_offset:
                        if end > best_end_date_exact:
                            best_end_date_exact = end
                            best_val_exact = float(val)
                            best_unit = unit_type
                    else:
                        if end > best_end_date_offset:
                            best_end_date_offset = end
                            best_val_offset = float(val)
                            best_unit = unit_type
                    continue

                # Duration metric: enforce annual duration window
                try:
                    s_date = datetime.strptime(start, "%Y-%m-%d")
                    e_date = datetime.strptime(end, "%Y-%m-%d")
                    duration = (e_date - s_date).days

                    if min_days <= duration <= max_days:
                        if not is_offset:
                            if end > best_end_date_exact:
                                best_end_date_exact = end
                                best_val_exact = float(val)
                                best_unit = unit_type
                        else:
                            if end > best_end_date_offset:
                                best_end_date_offset = end
                                best_val_offset = float(val)
                                best_unit = unit_type
                except (ValueError, TypeError):
                    # Date parse failure: fall back to end_date-based selection
                    if not is_offset:
                        if end > best_end_date_exact:
                            best_end_date_exact = end
                            best_val_exact = float(val)
                            best_unit = unit_type
                    else:
                        if end > best_end_date_offset:
                            best_end_date_offset = end
                            best_val_offset = float(val)
                            best_unit = unit_type

        # Prefer exact fiscal_year match over offset fallback
        if best_val_exact is not None:
            best_val = best_val_exact
            best_end = best_end_date_exact
        else:
            best_val = best_val_offset
            best_end = best_end_date_offset

        # Period-end-year fallback: when fy-based filtering finds nothing,
        # walk all facts and accept one whose period end_date's calendar year
        # equals the target fiscal_year. This recovers comparative-period data
        # filed in later 10-Ks (e.g. NVDA's FY2022 CapEx reported as a 3-year
        # comparative inside the FY2024 filing, with fy=2024 but end=2022-01-30).
        if best_val is None:
            best_period_end = ""
            for unit_type, units in concept.get("units", {}).items():
                if unit_type not in ACCEPTED_UNITS and unit_type not in PER_SHARE_UNITS:
                    continue
                for u in units:
                    if u.get("form") not in ("10-K", "20-F", "40-F"):
                        continue
                    start = u.get("start")
                    end = u.get("end")
                    val = u.get("val")
                    if val is None or not end:
                        continue
                    # Match by period-end calendar year against target fiscal_year
                    try:
                        end_dt = datetime.strptime(end, "%Y-%m-%d")
                    except (ValueError, TypeError):
                        continue
                    if end_dt.year != fiscal_year:
                        continue
                    # Duration check (skip for point-in-time tags)
                    if start:
                        try:
                            s_dt = datetime.strptime(start, "%Y-%m-%d")
                            duration = (end_dt - s_dt).days
                            if not (min_days <= duration <= max_days):
                                continue
                        except (ValueError, TypeError):
                            continue
                    # Latest end_date wins
                    if end > best_period_end:
                        best_period_end = end
                        best_val = float(val)
                        best_end = end
                        best_unit = unit_type

        # Currency conversion if needed.
        # Per-share composite units (e.g. "TWD/shares") convert the numerator
        # currency only — "USD/shares" is already USD-denominated.
        if best_val is not None:
            if best_unit in PER_SHARE_UNITS:
                numerator_ccy = best_unit.split("/")[0]
                if numerator_ccy != "USD":
                    from aletheia.data.fx_converter import convert_to_usd
                    best_val = convert_to_usd(best_val, numerator_ccy, fiscal_year)
            elif best_unit != "USD" and best_unit not in ("shares", "pure"):
                from aletheia.data.fx_converter import convert_to_usd
                best_val = convert_to_usd(best_val, best_unit, fiscal_year)

        return best_val, (best_end or None)

    # ─────────────────────────────────────────────────────────────────────
    # Logging
    # ─────────────────────────────────────────────────────────────────────

    def _log_missing_tags(
        self,
        enriched: Dict[str, float],
        ticker: str,
        fiscal_year: int,
    ):
        """
        Audit log for missing critical XBRL tags.
        Writes one JSONL entry per missing field to:
          valuation_data/logs/tag_misses.jsonl
        """
        sector_key, is_ifrs = self._get_classification_keys(ticker)
        period_end_date = enriched.get("period_end_date", "unknown")

        missing: List[str] = []
        for field in REQUIRED_FIELDS:
            val = enriched.get(field)
            if val is None or val == 0:
                missing.append(field)

        # D&A handled specially: log a single miss only if ALL components are absent
        da_present = any(
            enriched.get(f) not in (None, 0)
            for f in DA_COMPONENT_FIELDS
        )
        if not da_present:
            missing.append("D&A_AllComponents")

        if not missing:
            return

        log_path = Path("valuation_data/logs/tag_misses.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        with open(log_path, "a") as f:
            for field in missing:
                attempted = self._collect_attempted_tags(field, sector_key, is_ifrs)
                entry = {
                    "ticker": ticker,
                    "fiscal_year": fiscal_year,
                    "period_end_date": period_end_date,
                    "missing_field": field,
                    "sector_key": sector_key,
                    "is_ifrs_filer": is_ifrs,
                    "attempted_xbrl_tags": attempted,
                }
                f.write(json.dumps(entry) + "\n")

    def _collect_attempted_tags(
        self,
        field: str,
        sector_key: Optional[str],
        is_ifrs: bool,
    ) -> List[str]:
        """Return the priority list of XBRL tags that the resolver attempted."""
        if field == "D&A_AllComponents":
            # Aggregate all D&A component priority lists for diagnostic visibility
            attempted = []
            for component in DA_COMPONENT_FIELDS:
                rules = FIELD_MAPPINGS.get(component, {})
                attempted.extend(self._select_priority_list(rules, sector_key, is_ifrs))
            return attempted

        rules = FIELD_MAPPINGS.get(field, {})
        return self._select_priority_list(rules, sector_key, is_ifrs)

    def _log_missing_period_end_date(
        self,
        enriched: Dict[str, float],
        ticker: str,
        fiscal_year: int,
    ):
        """
        Strict-fail visibility for missing period_end_date.

        Records flagged here will have period_end_date_missing=True, which
        causes dcf_engine._compute_historical_cagr to exclude the year from
        lookbacks. This log gives universe-level visibility on how often the
        strict-fail mechanism is firing.
        """
        log_path = Path("valuation_data/logs/missing_period_end_date.jsonl")
        log_path.parent.mkdir(parents=True, exist_ok=True)

        entry = {
            "ticker": ticker,
            "fiscal_year": fiscal_year,
            "revenue_resolved": enriched.get("Revenue") is not None,
        }
        with open(log_path, "a") as f:
            f.write(json.dumps(entry) + "\n")

    # ─────────────────────────────────────────────────────────────────────
    # CIK and raw facts loading
    # ─────────────────────────────────────────────────────────────────────

    def _get_cik(self, ticker: str) -> Optional[str]:
        """Resolve ticker → CIK with caching."""
        if ticker in self._cik_cache:
            return self._cik_cache[ticker]

        cik_path = self.raw_dir / "company_tickers" / "company_tickers.json"
        if not cik_path.exists():
            self._cik_cache[ticker] = None
            return None

        try:
            with open(cik_path) as f:
                data = json.load(f)
            for _, v in data.items():
                if v["ticker"].upper() == ticker.upper():
                    cik = str(v["cik_str"]).zfill(10)
                    self._cik_cache[ticker] = cik
                    return cik
        except Exception:
            pass

        self._cik_cache[ticker] = None
        return None

    def _load_raw_facts(self, ticker: str) -> Optional[dict]:
        """Load raw SEC companyfacts JSON for a ticker."""
        cik = self._get_cik(ticker)
        if not cik:
            return None
        facts_path = self.raw_dir / "companyfacts" / f"CIK{cik}.json"
        if not facts_path.exists():
            return None
        try:
            with open(facts_path) as f:
                return json.load(f)
        except Exception:
            return None