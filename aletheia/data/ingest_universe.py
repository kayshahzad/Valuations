import sys
import json
import time
from pathlib import Path
from datetime import datetime

from config.ticker_classification import UNIVERSE
from aletheia.data.cleaning_engine import CleaningEngine
from aletheia.data.database import InvestmentDatabase
from aletheia.data.edgar_client import CANONICAL_DIR, RAW_DIR, SecEdgar

def ingest_universe():
    print("=================================================================")
    print("  PHASE 4.5: SEC MULTI-YEAR HISTORICAL INGESTION")
    print("=================================================================")
    
    # Pre-flight check: cache staleness
    sec_client = SecEdgar()
    ticker_cik_map = sec_client.get_ticker_cik_map()
    
    stale_or_missing = []
    
    for ticker in UNIVERSE.keys():
        # resolve CIK
        cik = None
        if ticker in ["BRK-B", "BRK.B"]: cik = "0001067983"
        elif ticker in ["GOOGL", "GOOG"]: cik = "0001652044"
        else:
            cik = ticker_cik_map.get(ticker)
            if cik: cik = cik.zfill(10)
            
        if not cik:
            print(f"✗ Failed to resolve CIK for {ticker}")
            sys.exit(1)
            
        raw_path = RAW_DIR / f"CIK{cik}.json"
        
        if not raw_path.exists():
            stale_or_missing.append(ticker)
            continue
            
        # Check staleness
        mtime = raw_path.stat().st_mtime
        age_days = (time.time() - mtime) / 86400.0
        
        if age_days > 7.0:
            stale_or_missing.append(ticker)
            
    if stale_or_missing:
        print("\n❌ PRE-FLIGHT CHECK FAILED: Stale or missing caches detected.")
        print(f"The following tickers have missing or >7 days old SEC companyfacts:")
        print(f"  {', '.join(stale_or_missing)}")
        print("\nTo avoid SEC rate limits during batch processing, please run:")
        print(f"  python3 -m aletheia.data.edgar_client --ticker {' '.join(stale_or_missing)} --force")
        print("\nThen re-run this script.")
        sys.exit(1)
        
    print("✓ Pre-flight check passed. All 25 tickers have fresh SEC caches.")
    
    db = InvestmentDatabase(verbose=False)
    engine = CleaningEngine(verbose=False)
    
    year_depth = {}
    
    for ticker in sorted(UNIVERSE.keys()):
        print(f"\nProcessing {ticker}...")
        
        records = engine.clean_all_years(ticker)
        
        if not records:
            print(f"  ✗ Engine produced no records for {ticker}")
            year_depth[ticker] = 0
            continue
            
        for record in records:
            db.upsert_record(record)
            
        # Canonical Parquet
        df = db.get_latest(ticker)
        if not df.empty:
            parquet_path = CANONICAL_DIR / f"{ticker}.parquet"
            df.to_parquet(parquet_path, index=False)
        
        years_count = len(records)
        year_depth[ticker] = years_count
        print(f"  ✓ Saved {years_count} fiscal years.")
        
    db.close()
    
    print("\n=================================================================")
    print("  INGESTION COMPLETE: YEAR-DEPTH REPORT")
    print("=================================================================")
    for ticker, count in year_depth.items():
        flag = " ⚠" if count < 3 else ""
        print(f"  {ticker:>6}: {count:>2} years{flag}")
        
    # Write report artifact
    report_path = Path("/Users/kashifshahzad/.gemini/antigravity/brain/426b6bda-c00f-4f62-bd7a-2230751ceedc/year_depth_report.md")
    if report_path.exists():
        with open(report_path, "w") as f:
            f.write("# Phase 4.5: Year-Depth Report\n\n")
            f.write("| Ticker | Years Ingested | Status |\n")
            f.write("|--------|----------------|--------|\n")
            for ticker, count in year_depth.items():
                status = "PASS" if count >= 3 else "FAIL (KNOWN_ISSUE)"
                f.write(f"| {ticker} | {count} | {status} |\n")

if __name__ == "__main__":
    ingest_universe()
