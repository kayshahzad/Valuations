"""
aletheia/data/edgar_client.py

SEC EDGAR Data Ingestion Client
=================================
Fetches raw XBRL company facts from SEC EDGAR, saves them locally,
and processes them through the cleaning engine into DuckDB.

This is the missing link between SEC filings and the Aletheia pipeline.
Run this LOCALLY (not in cloud) — SEC blocks cloud IP ranges.

Usage:
    # Add a single ticker
    python3 -m aletheia.data.edgar_client --ticker AMD

    # Add multiple tickers
    python3 -m aletheia.data.edgar_client --ticker AMD ASML TSM JPM

    # Check what's already ingested
    python3 -m aletheia.data.edgar_client --status

    # Force re-ingest (overwrite existing)
    python3 -m aletheia.data.edgar_client --ticker AMD --force

After running, the pipeline can process the ticker:
    PYTHONPATH=. python3 main.py --ticker AMD
"""

from __future__ import annotations

import json
import time
import argparse
import sys
from pathlib import Path
from typing import Optional, List, Dict, Any

import requests

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

RAW_DIR       = Path("valuation_data/raw/sec/companyfacts")
CANONICAL_DIR = Path("valuation_data/canonical/financials")
SEC_BASE      = "https://data.sec.gov"
RATE_LIMIT_S  = 0.15   # SEC allows ~10 requests/second; we use 6-7 to be safe

# SEC requires a meaningful User-Agent with contact info
# Edit this to your actual name/email
USER_AGENT = "Aletheia Investment Research research@aletheia.ai"

# Known CIK overrides for tickers with non-obvious mappings
CIK_OVERRIDES = {
    "BRK-B": "0001067983",   # Berkshire Hathaway (same CIK as BRK-A)
    "BRK.B": "0001067983",
    "GOOGL": "0001652044",   # Alphabet Class A
    "GOOG":  "0001652044",
}


# ─────────────────────────────────────────────────────────────────────────────
# SEC Company Lookup
# ─────────────────────────────────────────────────────────────────────────────

class Secedgar:
    """Minimal SEC EDGAR API client."""

    def __init__(self, user_agent: str = USER_AGENT):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self._ticker_cik_map: Optional[Dict[str, str]] = None

    def _get(self, url: str, retries: int = 3) -> Optional[dict]:
        for attempt in range(retries):
            try:
                time.sleep(RATE_LIMIT_S)
                r = self.session.get(url, timeout=30)
                r.raise_for_status()
                return r.json()
            except requests.HTTPError as e:
                if e.response.status_code == 429:
                    wait = 2 ** attempt
                    print(f"  Rate limited — waiting {wait}s...")
                    time.sleep(wait)
                elif e.response.status_code == 403:
                    print(f"  403 Forbidden — SEC blocks cloud IPs.")
                    print(f"  Run this script from your local machine, not a cloud server.")
                    return None
                else:
                    print(f"  HTTP {e.response.status_code}: {url}")
                    return None
            except Exception as e:
                print(f"  Request error (attempt {attempt+1}): {e}")
                if attempt == retries - 1:
                    return None
                time.sleep(1)
        return None

    def get_ticker_cik_map(self) -> Dict[str, str]:
        """Download the full SEC ticker→CIK mapping."""
        if self._ticker_cik_map:
            return self._ticker_cik_map
        print("Fetching SEC ticker→CIK map...")
        data = self._get("https://www.sec.gov/files/company_tickers.json")
        if not data:
            return {}
        mapping = {}
        for entry in data.values():
            ticker = entry.get("ticker", "").upper()
            cik    = str(entry.get("cik_str", "")).zfill(10)
            if ticker:
                mapping[ticker] = cik
        self._ticker_cik_map = mapping
        print(f"  Loaded {len(mapping)} ticker→CIK mappings")
        return mapping

    def resolve_cik(self, ticker: str) -> Optional[str]:
        """Resolve ticker to zero-padded 10-digit CIK."""
        ticker = ticker.upper()
        if ticker in CIK_OVERRIDES:
            return CIK_OVERRIDES[ticker]
        mapping = self.get_ticker_cik_map()
        cik = mapping.get(ticker)
        if not cik:
            print(f"  ✗ CIK not found for {ticker}")
            return None
        return cik.zfill(10)

    def fetch_company_facts(self, cik: str) -> Optional[dict]:
        """Fetch full XBRL company facts for a given CIK."""
        url = f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        return self._get(url)


# ─────────────────────────────────────────────────────────────────────────────
# Ingestion pipeline
# ─────────────────────────────────────────────────────────────────────────────

class EdgarIngester:
    """
    Full ingestion pipeline:
      1. Resolve ticker → CIK
      2. Fetch XBRL facts from SEC → save raw JSON
      3. Process through CleaningEngine → save to DuckDB
      4. Build canonical Parquet for intake agent
    """

    def __init__(self, user_agent: str = USER_AGENT, force: bool = False):
        self.sec    = SecEDAR(user_agent)
        self.force  = force
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        CANONICAL_DIR.mkdir(parents=True, exist_ok=True)

    def ingest(self, ticker: str) -> bool:
        """Full ingestion for one ticker. Returns True on success."""
        ticker = ticker.upper()
        print(f"\n{'='*60}")
        print(f"  Ingesting: {ticker}")
        print(f"{'='*60}")

        # ── Step 1: Resolve CIK ───────────────────────────────────────────────
        print(f"Step 1: Resolving CIK for {ticker}...")
        cik = self.sec.resolve_cik(ticker)
        if not cik:
            print(f"  ✗ Cannot resolve CIK for {ticker}. Skipping.")
            return False
        print(f"  ✓ CIK: {cik}")

        # ── Step 2: Fetch and save raw XBRL facts ─────────────────────────────
        raw_path = RAW_DIR / f"CIK{cik}.json"
        if raw_path.exists() and not self.force:
            print(f"Step 2: Raw data already exists at {raw_path} (use --force to refresh)")
            facts = json.loads(raw_path.read_text())
        else:
            print(f"Step 2: Fetching XBRL facts from SEC EDGAR...")
            facts = self.sec.fetch_company_facts(cik)
            if not facts:
                print(f"  ✗ Failed to fetch facts for {ticker}")
                return False
            raw_path.write_text(json.dumps(facts, indent=2))
            entity = facts.get("entityName", ticker)
            us_gaap_count = len(facts.get("facts", {}).get("us-gaap", {}))
            print(f"  ✓ Saved {raw_path} ({entity}, {us_gaap_count} GAAP tags)")

        # ── Step 3: Validate key tags exist ───────────────────────────────────
        print(f"Step 3: Validating XBRL tag coverage...")
        us_gaap = facts.get("facts", {}).get("us-gaap", {})
        self._report_tag_coverage(ticker, us_gaap)

        # ── Step 4: Process through CleaningEngine into DuckDB ────────────────
        print(f"Step 4: Running cleaning engine...")
        try:
            from aletheia.data.cleaning_engine import CleaningEngine
            from aletheia.data.database import InvestmentDatabase

            engine = CleaningEngine(verbose=True)
            records = engine.clean_all_years(ticker)

            if not records:
                print(f"  ✗ CleaningEngine returned no records for {ticker}")
                print(f"    This usually means the tag_resolver doesn't recognise {ticker}'s XBRL tags.")
                print(f"    See Step 3 output above to identify which tags need adding to tag_resolver.py")
                return False

            db = InvestmentDatabase(verbose=False)
            for record in records:
                db.upsert_record(record)
            db.close()

            print(f"  ✓ Saved {len(records)} years of cleaned data for {ticker}")

        except Exception as e:
            print(f"  ✗ Cleaning engine error: {e}")
            import traceback
            traceback.print_exc()
            return False

        # ── Step 5: Build canonical Parquet for intake agent ──────────────────
        print(f"Step 5: Building canonical Parquet...")
        try:
            import pandas as pd
            from aletheia.data.database import InvestmentDatabase

            db = InvestmentDatabase(verbose=False)
            df = db.get_latest(ticker)
            db.close()

            if df.empty:
                print(f"  ✗ No data in DB for {ticker} after cleaning")
                return False

            parquet_path = CANONICAL_DIR / f"{ticker}.parquet"
            df.to_parquet(parquet_path, index=False)
            print(f"  ✓ Saved {parquet_path} ({len(df)} records, "
                  f"FY{int(df.fiscal_year.min())}–FY{int(df.fiscal_year.max())})")

        except Exception as e:
            print(f"  ✗ Parquet export error: {e}")
            return False

        print(f"\n  ✅ {ticker} ingestion complete. Run:")
        print(f"     PYTHONPATH=. python3 main.py --ticker {ticker}")
        return True

    def _report_tag_coverage(self, ticker: str, us_gaap: dict):
        """Check which critical tags exist and which are missing."""
        CRITICAL_TAGS = {
            "Revenue": [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "NetRevenues", "SalesRevenueNet",
                "SalesRevenueGoodsNet", "RevenueNet",
            ],
            "OperatingCashFlow": [
                "NetCashProvidedByUsedInOperatingActivities",
                "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            ],
            "CapEx": [
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "CapitalExpendituresIncurringObligation",
                "PaymentsForCapitalImprovements",
            ],
            "NetIncome": [
                "NetIncomeLoss",
                "ProfitLoss",
                "NetIncomeLossAvailableToCommonStockholdersBasic",
            ],
            "TotalAssets": ["Assets"],
            "TotalEquity": [
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ],
        }

        found = {}
        missing = []
        for concept, candidates in CRITICAL_TAGS.items():
            matched = None
            for tag in candidates:
                if tag in us_gaap:
                    annual = [v for v in us_gaap[tag].get("units", {}).get("USD", [])
                              if v.get("form") == "10-K"]
                    if annual:
                        latest = max(annual, key=lambda x: x["end"])
                        matched = (tag, latest["val"] / 1e9, latest["end"])
                        break
            if matched:
                found[concept] = matched
                print(f"  ✓ {concept:20} {matched[0]}: ${matched[1]:.1f}B ({matched[2]})")
            else:
                missing.append(concept)
                print(f"  ✗ {concept:20} NOT FOUND — needs tag_resolver.py addition")

        if missing:
            print(f"\n  ⚠ Missing tags for: {', '.join(missing)}")
            print(f"  Add these to aletheia/data/tag_resolver.py:")
            for concept in missing:
                actual_tags = [t for t in us_gaap.keys()
                               if any(kw in t.lower() for kw in concept.lower().split())]
                if actual_tags:
                    print(f"    {concept}: {actual_tags[:3]}")
        else:
            print(f"  ✓ All critical tags found for {ticker}")

    def status(self):
        """Show ingestion status for all tickers."""
        print("\n=== INGESTION STATUS ===\n")
        raw_files = list(RAW_DIR.glob("CIK*.json"))
        parquet_files = list(CANONICAL_DIR.glob("*.parquet"))

        print(f"Raw SEC files:      {len(raw_files)} companies")
        print(f"Canonical Parquet:  {len(parquet_files)} tickers")
        print(f"  {', '.join(p.stem for p in sorted(parquet_files))}")

        try:
            from aletheia.data.database import InvestmentDatabase
            db = InvestmentDatabase(verbose=False)
            tickers_in_db = db.query(
                "SELECT ticker, COUNT(*) as years, MAX(fiscal_year) as latest "
                "FROM cleaned_financials GROUP BY ticker ORDER BY ticker"
            )
            db.close()
            if not tickers_in_db.empty:
                print(f"\nDuckDB records:")
                for _, row in tickers_in_db.iterrows():
                    print(f"  {row['ticker']:>6}: {int(row['years'])} years "
                          f"(latest FY{int(row['latest'])})")
        except Exception as e:
            print(f"\nDuckDB status error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# Fix typo in class name above
# ─────────────────────────────────────────────────────────────────────────────
SecEDAR = SecEdgar = SecEDAGAR = None  # will be fixed below

class SecEdgar(SecEDAGAR if False else object):
    pass

# Patch the name
import sys as _sys
_mod = _sys.modules[__name__]

class SecEdgar:
    """Minimal SEC EDGAR API client."""

    def __init__(self, user_agent: str = USER_AGENT):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": user_agent,
            "Accept-Encoding": "gzip, deflate",
        })
        self._ticker_cik_map: Optional[Dict[str, str]] = None

    def _get(self, url: str, retries: int = 3) -> Optional[dict]:
        for attempt in range(retries):
            try:
                time.sleep(RATE_LIMIT_S)
                r = self.session.get(url, timeout=30)
                r.raise_for_status()
                return r.json()
            except requests.HTTPError as e:
                if e.response.status_code == 429:
                    wait = 2 ** attempt
                    print(f"  Rate limited — waiting {wait}s...")
                    time.sleep(wait)
                elif e.response.status_code == 403:
                    print(f"\n  403 Forbidden.")
                    print(f"  SEC EDGAR blocks cloud/server IP ranges.")
                    print(f"  Run this script from your LOCAL machine:\n")
                    print(f"    python3 -m aletheia.data.edgar_client --ticker {url.split('CIK')[1][:10]}\n")
                    return None
                else:
                    print(f"  HTTP {e.response.status_code}: {url}")
                    return None
            except Exception as e:
                print(f"  Request error (attempt {attempt+1}): {e}")
                if attempt == retries - 1:
                    return None
                time.sleep(1)
        return None

    def get_ticker_cik_map(self) -> Dict[str, str]:
        if self._ticker_cik_map:
            return self._ticker_cik_map
        print("Fetching SEC ticker→CIK map...")
        data = self._get("https://www.sec.gov/files/company_tickers.json")
        if not data:
            return {}
        mapping = {}
        for entry in data.values():
            ticker = entry.get("ticker", "").upper()
            cik    = str(entry.get("cik_str", "")).zfill(10)
            if ticker:
                mapping[ticker] = cik
        self._ticker_cik_map = mapping
        print(f"  ✓ Loaded {len(mapping)} ticker→CIK mappings")
        return mapping

    def resolve_cik(self, ticker: str) -> Optional[str]:
        ticker = ticker.upper()
        if ticker in CIK_OVERRIDES:
            return CIK_OVERRIDES[ticker]
        mapping = self.get_ticker_cik_map()
        cik = mapping.get(ticker)
        if not cik:
            print(f"  ✗ CIK not found for {ticker}")
            return None
        return cik.zfill(10)

    def fetch_company_facts(self, cik: str) -> Optional[dict]:
        url = f"{SEC_BASE}/api/xbrl/companyfacts/CIK{cik}.json"
        return self._get(url)


class EdgarIngester:
    """
    Full ingestion pipeline:
      1. Resolve ticker → CIK
      2. Fetch XBRL facts from SEC → save raw JSON
      3. Process through CleaningEngine → save to DuckDB
      4. Build canonical Parquet for intake agent
    """

    def __init__(self, user_agent: str = USER_AGENT, force: bool = False):
        self.sec   = SecEdgar(user_agent)
        self.force = force
        RAW_DIR.mkdir(parents=True, exist_ok=True)
        CANONICAL_DIR.mkdir(parents=True, exist_ok=True)

    def ingest(self, ticker: str) -> bool:
        ticker = ticker.upper()
        print(f"\n{'='*60}")
        print(f"  Ingesting: {ticker}")
        print(f"{'='*60}")

        # Step 1: Resolve CIK
        print(f"Step 1: Resolving CIK...")
        cik = self.sec.resolve_cik(ticker)
        if not cik:
            return False
        print(f"  ✓ CIK: {cik}")

        # Step 2: Fetch raw XBRL
        raw_path = RAW_DIR / f"CIK{cik}.json"
        if raw_path.exists() and not self.force:
            print(f"Step 2: Using cached raw data ({raw_path})")
            facts = json.loads(raw_path.read_text())
        else:
            print(f"Step 2: Fetching from SEC EDGAR...")
            facts = self.sec.fetch_company_facts(cik)
            if not facts:
                return False
            raw_path.write_text(json.dumps(facts, indent=2))
            entity = facts.get("entityName", ticker)
            n_tags = len(facts.get("facts", {}).get("us-gaap", {}))
            print(f"  ✓ Saved {raw_path.name} ({entity}, {n_tags} GAAP tags)")

        # Step 3: Validate tags
        print(f"Step 3: Validating XBRL tag coverage...")
        us_gaap_raw = facts.get("facts", {}).get("us-gaap", {})
        ifrs_raw = facts.get("facts", {}).get("ifrs-full", {})
        us_gaap = {}
        us_gaap.update(ifrs_raw)
        us_gaap.update(us_gaap_raw)
        
        all_found = self._report_tag_coverage(ticker, us_gaap)

        if not all_found:
            print(f"\n  ⚠ Some tags missing — cleaning engine may produce partial data.")
            print(f"  Add missing tags to aletheia/data/tag_resolver.py then re-run.")

        # Step 4: Clean and store
        print(f"Step 4: Running cleaning engine...")
        try:
            from aletheia.data.cleaning_engine import CleaningEngine
            from aletheia.data.database import InvestmentDatabase

            engine  = CleaningEngine(verbose=False)
            records = engine.clean_all_years(ticker)

            if not records:
                print(f"  ✗ No records produced — tag_resolver needs updating for {ticker}")
                return False

            db = InvestmentDatabase(verbose=False)
            for record in records:
                db.upsert_record(record)
            db.close()
            print(f"  ✓ {len(records)} fiscal years cleaned and stored in DuckDB")

        except Exception as e:
            print(f"  ✗ Cleaning error: {e}")
            import traceback; traceback.print_exc()
            return False

        # Step 5: Canonical Parquet
        print(f"Step 5: Writing canonical Parquet...")
        try:
            from aletheia.data.database import InvestmentDatabase
            db = InvestmentDatabase(verbose=False)
            df = db.get_latest(ticker)
            db.close()

            if df.empty:
                print(f"  ✗ DB empty after cleaning — check cleaning engine logs")
                return False

            parquet_path = CANONICAL_DIR / f"{ticker}.parquet"
            df.to_parquet(parquet_path, index=False)
            fy_min = int(df.fiscal_year.min())
            fy_max = int(df.fiscal_year.max())
            rev    = df[df.fiscal_year == df.fiscal_year.max()].iloc[0].get("clean_Revenue", 0) or 0
            print(f"  ✓ {parquet_path.name} — FY{fy_min}–FY{fy_max}, "
                  f"latest revenue ${rev/1e9:.1f}B")

        except Exception as e:
            print(f"  ✗ Parquet error: {e}")
            return False

        print(f"\n  ✅ {ticker} ready. Run:")
        print(f"     PYTHONPATH=. python3 main.py --ticker {ticker}")
        return True

    def _report_tag_coverage(self, ticker: str, us_gaap: dict) -> bool:
        CRITICAL = {
            "Revenue": [
                "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax",
                "Revenues", "NetRevenues", "SalesRevenueNet",
                "SalesRevenueGoodsNet", "RevenueNet", "NetSales",
            ],
            "OperatingCF": [
                "NetCashProvidedByUsedInOperatingActivities",
                "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
            ],
            "CapEx": [
                "PaymentsToAcquirePropertyPlantAndEquipment",
                "CapitalExpendituresIncurringObligation",
            ],
            "NetIncome": [
                "NetIncomeLoss", "ProfitLoss",
                "NetIncomeLossAvailableToCommonStockholdersBasic",
            ],
            "TotalAssets": ["Assets"],
            "TotalEquity": [
                "StockholdersEquity",
                "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
            ],
        }
        all_found = True
        for concept, candidates in CRITICAL.items():
            matched = None
            for tag in candidates:
                if tag in us_gaap:
                    annual = []
                    for unit_type in ("USD", "EUR", "TWD", "CAD", "GBP", "JPY", "CHF"):
                        for v in us_gaap[tag].get("units", {}).get(unit_type, []):
                            if v.get("form") in ("10-K", "20-F", "40-F"):
                                annual.append(v)
                    if annual:
                        latest = max(annual, key=lambda x: x["end"])
                        matched = (tag, latest["val"] / 1e9, latest["end"])
                        break
            if matched:
                print(f"  ✓ {concept:<15} {matched[0]}: ${matched[1]:.1f}B ({matched[2]})")
            else:
                all_found = False
                # Show what AMD actually has for this concept
                guesses = [t for t in us_gaap
                           if any(kw in t.lower() for kw in concept.lower().split()
                                  if len(kw) > 3)][:3]
                print(f"  ✗ {concept:<15} not found. Candidates in filing: {guesses or 'none'}")
        return all_found

    def status(self):
        print("\n=== INGESTION STATUS ===\n")
        raw_files     = list(RAW_DIR.glob("CIK*.json"))
        parquet_files = list(CANONICAL_DIR.glob("*.parquet"))
        print(f"Raw SEC JSON files:  {len(raw_files)}")
        print(f"Canonical Parquets:  {len(parquet_files)}")
        if parquet_files:
            print(f"  Tickers: {', '.join(sorted(p.stem for p in parquet_files))}")
        try:
            from aletheia.data.database import InvestmentDatabase
            db = InvestmentDatabase(verbose=False)
            summary = db.query(
                "SELECT ticker, COUNT(*) as years, MAX(fiscal_year) as latest_fy "
                "FROM cleaned_financials GROUP BY ticker ORDER BY ticker"
            )
            db.close()
            if not summary.empty:
                print(f"\nDuckDB — {len(summary)} tickers:")
                for _, row in summary.iterrows():
                    print(f"  {row['ticker']:>6}: {int(row['years'])} yrs "
                          f"(latest FY{int(row['latest_fy'])})")
        except Exception as e:
            print(f"DuckDB error: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="SEC EDGAR ingestion for Aletheia pipeline",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m aletheia.data.edgar_client --ticker AMD
  python3 -m aletheia.data.edgar_client --ticker AMD ASML TSM JPM BRK-B
  python3 -m aletheia.data.edgar_client --status
  python3 -m aletheia.data.edgar_client --ticker AMD --force

IMPORTANT: Run from your LOCAL machine.
SEC EDGAR blocks cloud/server IP ranges (403 Forbidden).

After ingestion, run the analysis pipeline:
  PYTHONPATH=. python3 main.py --ticker AMD
        """
    )
    parser.add_argument("--ticker",  nargs="+", help="Ticker(s) to ingest")
    parser.add_argument("--force",   action="store_true", help="Re-fetch even if cached")
    parser.add_argument("--status",  action="store_true", help="Show ingestion status")
    parser.add_argument("--user-agent", default=USER_AGENT,
                        help="SEC User-Agent string (required: name + email)")
    args = parser.parse_args()

    ingester = EdgarIngester(user_agent=args.user_agent, force=args.force)

    if args.status:
        ingester.status()
        return

    if not args.ticker:
        parser.print_help()
        return

    results = {}
    for ticker in args.ticker:
        results[ticker] = ingester.ingest(ticker)
        if len(args.ticker) > 1:
            time.sleep(0.5)   # brief pause between tickers

    print(f"\n{'='*60}")
    print(f"  INGESTION SUMMARY")
    print(f"{'='*60}")
    for ticker, ok in results.items():
        status = "✅ Success" if ok else "❌ Failed"
        print(f"  {ticker:>6}: {status}")

    failed = [t for t, ok in results.items() if not ok]
    if failed:
        print(f"\n  Failed tickers: {', '.join(failed)}")
        print(f"  Check tag_resolver.py for missing XBRL tags.")
        sys.exit(1)


if __name__ == "__main__":
    main()
