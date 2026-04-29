
import json
import pandas as pd
import unittest
from pathlib import Path
from datetime import datetime

# Paths
FACT_PATH = Path("valuation_data/raw/sec/companyfacts/CIK0000320193.json")
PARQUET_PATH = Path("valuation_data/canonical/financials/AAPL.parquet")
MAP_PATH = Path("config/ACCOUNTING_MAPS.md")

class TestCanonicalIntegrity(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """Load Data Once"""
        # 1. Load Maps
        cls.priority_map = cls._load_maps()
        
        # 2. Load Raw Facts
        with open(FACT_PATH, "r") as f:
            cls.facts = json.load(f)
            
        # 3. Load Canonical Parquet
        if PARQUET_PATH.exists():
            cls.df = pd.read_parquet(PARQUET_PATH)
        else:
            cls.df = None

    @classmethod
    def _load_maps(cls):
        """Simple parser for ACCOUNTING_MAPS.md pipe table format"""
        pmap = {}
        if not MAP_PATH.exists(): return {}
        
        with open(MAP_PATH, "r") as f:
            for line in f:
                if line.strip().startswith("|") and "Tag" not in line and "---" not in line:
                    parts = [p.strip() for p in line.split("|")]
                    if len(parts) >= 3:
                        std_tag = parts[1]
                        tags = [t.strip() for t in parts[2].replace("`", "").split(",") if t.strip()]
                        if std_tag not in pmap: pmap[std_tag] = []
                        pmap[std_tag].extend(tags)
        # Add Global manually if needed or ensure parser covers it. 
        # The simple parser above covers the table sections. 
        # Let's add the hardcoded global one from the file if missed by table parser
        # "RevenueFromContractWithCustomerExcludingAssessedTax -> Revenue"
        # but that's legacy format. The table usually has it.
        # Let's double check map file content. 
        # Line 4: - RevenueFromContract... -> Revenue. 
        # Line 20: | revenue | Revenues, RevenueFrom... |
        # The table (Line 20) covers it. So table parser is sufficient.
        return pmap

    def test_parquet_exists(self):
        self.assertIsNotNone(self.df, "Canonical Parquet file not found.")
        self.assertFalse(self.df.empty, "Canonical Parquet is empty.")

    def test_latest_year_integrity(self):
        """
        Verifies that for the latest fiscal year in Parquet (Long Format), 
        values match the raw Company Facts based on the resolved_tag.
        """
        if self.df is None or self.df.empty: return

        # 1. Get Latest Date
        self.df['date_str'] = self.df['period_end_date'].astype(str)
        dates = sorted(self.df['date_str'].unique(), reverse=True)
        if not dates: return
        
        target_date = dates[0] # e.g. "2025-09-27"
        
        print(f"\n🚀 AUDITING PARQUET (Date: {target_date}) 🚀")
        print(f"{'STD TAG':<20} | {'RESOLVED TAG':<40} | {'PARQUET VAL':<15} | {'RAW VAL':<15} | {'STATUS'}")
        print("-" * 110)
        
        # 2. Filter for Target Date
        latest_df = self.df[self.df['date_str'] == target_date]
        
        us_gaap = self.facts["facts"]["us-gaap"]
        
        for _, row in latest_df.iterrows():
            std_tag = row['standard_tag']
            resolved_tag = row['resolved_tag']
            parquet_val = row['value']
            
            # Skip unmapped/nulls if any
            if pd.isna(resolved_tag) or resolved_tag == "None":
                print(f"{std_tag:<20} | {'(No Match)':<40} | {parquet_val:<15} | {'N/A':<15} | ⚠️ SKIP")
                continue

            # 3. Find Expected Value in Raw using the Resolved Tag Lineage
            found_val = None
            if resolved_tag in us_gaap:
                units = us_gaap[resolved_tag]["units"].get("USD", [])
                for u in units:
                    if u.get("end") == target_date:
                        found_val = u.get("val")
                        break
            
            # 4. Assert
            p_str = f"{parquet_val:,.0f}" if pd.notna(parquet_val) else "NaN"
            r_str = f"{found_val:,.0f}" if found_val is not None else "Not Found"
            
            status = "✅ MATCH" if found_val == parquet_val else "❌ MISMATCH"
            print(f"{std_tag:<20} | {resolved_tag[:40]:<40} | {p_str:<15} | {r_str:<15} | {status}")
            
            if found_val is not None:
                self.assertEqual(parquet_val, found_val, f"Mismatch for {std_tag} (via {resolved_tag})")

if __name__ == "__main__":
    unittest.main()
