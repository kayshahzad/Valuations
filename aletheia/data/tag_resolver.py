"""
aletheia/data/tag_resolver.py

Tag Resolution Layer
====================
Bridges the gap between canonical_transformer.py output (lowercase tags like
`revenue`, `ebit`, `cash`) and the cleaning engine expectations (PascalCase
like `Revenue`, `OperatingIncome`, `Cash`).

Also handles the XBRL tag → standard tag mapping for tags that the
canonical transformer did not resolve (SBC, lease, pension, etc.) by
reading directly from the raw XBRL facts.

This is the translation layer — it runs BEFORE the cleaning engine
and enriches the canonical record with the full set of metrics.

Usage (called internally by CleaningEngine._load_and_pivot):
    resolver = TagResolver()
    wide_dict = resolver.enrich(wide_dict, ticker, fiscal_year)
"""

import json
from pathlib import Path
from typing import Dict, Optional


from config.tag_mappings import FIELD_MAPPINGS, CANONICAL_ALIASES
from config.sign_conventions import get_sign_convention
from config.industry_routing import get_industry

class TagResolver:
    """
    Enriches the canonical wide_dict with:
    1. Normalized PascalCase keys (maps 'revenue' → 'Revenue', 'ebit' → 'OperatingIncome')
    2. Supplemental metrics from raw XBRL that the transformer did not capture
       (SBC, lease liabilities, pension, operating cash flows, etc.)
    """

    def __init__(self, raw_dir: str = "valuation_data/raw/sec"):
        self.raw_dir = Path(raw_dir)
        self._cik_cache: Dict[str, Optional[str]] = {}

    def normalize_keys(self, wide_dict: Dict[str, float]) -> Dict[str, float]:
        """
        Step 1: Normalize all canonical tag names to PascalCase standard names.
        Handles both lowercase (transformer output) and PascalCase (direct XBRL).
        """
        normalized = {}
        for raw_key, value in wide_dict.items():
            clean_key = CANONICAL_ALIASES.get(raw_key, raw_key)
            if clean_key and value is not None:
                if clean_key not in normalized or (normalized[clean_key] == 0 and value != 0):
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
        tags the transformer did not capture.

        Only fills gaps — never overwrites values already in wide_dict.
        """
        raw_facts = self._load_raw_facts(ticker)
        if raw_facts is None:
            return wide_dict

        facts = raw_facts.get("facts", {})
        us_gaap = facts.get("us-gaap", {})
        ifrs = facts.get("ifrs-full", {})
        
        combined_facts = {}
        combined_facts.update(ifrs)
        combined_facts.update(us_gaap)

        enriched = dict(wide_dict)
        industry = get_industry(ticker)

        from config.tag_mappings import RESOLUTION_STRATEGY
        
        for clean_name, rules in FIELD_MAPPINGS.items():
            # Never overwrite a valid value already set by canonical transformer
            if enriched.get(clean_name) not in (None, 0.0):
                continue
                
            priority_list = rules.get(industry, rules.get("default", []))
            sign_convention = get_sign_convention(clean_name)
            strategy = RESOLUTION_STRATEGY.get(clean_name, "first")
            
            candidates = []
            for xbrl_tag in priority_list:
                if xbrl_tag not in combined_facts:
                    continue
                    
                val = self._extract_value(combined_facts[xbrl_tag], fiscal_year, tag_name=xbrl_tag)
                
                if val is not None:
                    # Normalize sign for cash outflows
                    if sign_convention == "abs":
                        val = abs(val)
                        
                    if val != 0:
                        candidates.append(val)
                        if strategy == "first":
                            break # First valid match wins!
                            
            if candidates:
                if strategy == "max":
                    enriched[clean_name] = max(candidates)
                else:
                    enriched[clean_name] = candidates[0]

        return enriched

    def enrich(
        self,
        wide_dict: Dict[str, float],
        ticker: str,
        fiscal_year: int,
    ) -> Dict[str, float]:
        """
        Full enrichment pipeline: normalize keys then supplement from XBRL.
        This is the main entry point called by CleaningEngine._load_and_pivot.
        """
        normalized = self.normalize_keys(wide_dict)
        enriched = self.enrich_from_xbrl(normalized, ticker, fiscal_year)
        self._log_missing_tags(enriched, ticker, fiscal_year)
        return enriched

    def _log_missing_tags(self, enriched: Dict[str, float], ticker: str, fiscal_year: int):
        """Audit log for missing critical XBRL tags."""
        required_tags = [
            "Revenue", "OperatingIncome", "NetIncome", 
            "OperatingCF", "Depreciation", "CapEx", 
            "TotalAssets", "TotalLiabilities", "Cash"
        ]
        missing = [tag for tag in required_tags if tag not in enriched or enriched[tag] is None]
        
        if missing:
            log_path = Path("valuation_data/logs/tag_misses.jsonl")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with open(log_path, "a") as f:
                for tag in missing:
                    entry = {"ticker": ticker, "fiscal_year": fiscal_year, "missing_tag": tag}
                    f.write(json.dumps(entry) + "\n")

    def _extract_value(self, concept: dict, fiscal_year: int, tag_name: str = "") -> Optional[float]:
        """
        Extract the value for a given concept and fiscal year from XBRL units.
        Prefers 10-K filings and explicitly filters for full-year durations (~365 days)
        to avoid accidentally picking up Q4 standalone values sometimes filed in 10-Ks.
        It also selects the data point with the latest end date to ensure it grabs the 
        current year's data rather than a prior year's restated data.
        """
        from datetime import datetime

        best_val_exact = None
        best_end_date_exact = ""
        best_val_offset = None
        best_end_date_offset = ""
        best_unit = "USD"
        
        DA_TAGS = {
            "DepreciationDepletionAndAmortization",
            "DepreciationAndAmortization",
            "DepreciationAndAmortisationExpense",
        }
        min_days = 300 if tag_name in DA_TAGS else 330
        max_days = 400
        
        for unit_type, units in concept.get("units", {}).items():
            if unit_type not in ("USD", "shares", "pure", "EUR", "TWD", "CAD", "GBP", "JPY", "CHF"):
                continue
            
            for u in units:
                # Accept fiscal_year OR fiscal_year+1
                # Some foreign filers (e.g. ASML 20-F) have SEC fy label
                # offset by +1 from the actual fiscal year end date.
                filing_fy = u.get("fy")
                if u.get("form") not in ("10-K", "20-F", "40-F"):
                    continue
                if filing_fy not in (fiscal_year, fiscal_year + 1):
                    continue
                # Prefer exact match — track whether this is an offset match
                is_offset = (filing_fy == fiscal_year + 1)
                
                # Prevent offset leakage for domestic filers
                if is_offset and u.get("form") == "10-K":
                    continue
                
                # Log when the fallback fires to audit offset filers vs ghost records
                if is_offset:
                    print(f"[AUDIT] Using FY+1 fallback for {tag_name} (found in {filing_fy} {u.get('form')})")
                    
                start = u.get("start")
                end = u.get("end")
                val = u.get("val")
                
                if val is None or not end:
                    continue
                    
                # If it's a point-in-time metric (no start date), just take the latest end date
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
                    
                # If it's a duration metric, ensure it's roughly a year
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
                except Exception:
                    # Fallback if date parsing fails, just use end date
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
                        
        best_val = best_val_exact if best_val_exact is not None else best_val_offset
        if best_val is not None and best_unit != "USD" and best_unit not in ("shares", "pure"):
            from aletheia.data.fx_converter import convert_to_usd
            best_val = convert_to_usd(best_val, best_unit, fiscal_year)
            
        return best_val

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
