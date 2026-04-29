
import json
import unittest
from pathlib import Path

# Paths
SUB_PATH = Path("valuation_data/raw/sec/submissions/CIK0000320193.json")
FACT_PATH = Path("valuation_data/raw/sec/companyfacts/CIK0000320193.json")

class TestSubmissionLinkage(unittest.TestCase):
    
    def setUp(self):
        """Load data once for testing."""
        with open(SUB_PATH, "r") as f:
            self.subs = json.load(f)
        
        with open(FACT_PATH, "r") as f:
            self.facts = json.load(f)
            
    def test_submission_presence_in_facts(self):
        """
        Validates that the latest 10-K filing in Submissions is present in Company Facts.
        """
        print("\n🚀 STARTING SUBMISSION LINKAGE TEST 🚀\n")
        
        # 1. Find Latest 10-K Accession
        filings = self.subs["filings"]["recent"]
        accession_numbers = filings["accessionNumber"]
        forms = filings["form"]
        report_dates = filings["reportDate"]
        
        latest_10k_acc = None
        latest_10k_date = None
        
        print(f"{'Form':<10} | {'Accession':<25} | {'Report Date':<15}")
        print("-" * 55)
        
        # Scan recent filings
        for i in range(min(50, len(accession_numbers))):
            form = forms[i]
            acc = accession_numbers[i]
            date = report_dates[i]
            if form == "10-K":
                if latest_10k_acc is None:
                    latest_10k_acc = acc
                    latest_10k_date = date
                    print(f"✅ FOUND LATEST 10-K: {acc} ({date})")
                else:
                    print(f"{form:<10} | {acc:<25} | {date:<15}")
        
        self.assertIsNotNone(latest_10k_acc, "FATAL: No 10-K found in recent submissions.")

        # 2. Verify Presence in Company Facts for Key Concepts
        # We need to handle Tag Migration (e.g. ASC 606 for Revenue)
        concept_groups = {
            "Revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
            "Assets": ["Assets"],
            "NetIncome": ["NetIncomeLoss", "ProfitLoss"],
            "Equity": ["StockholdersEquity", "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"]
        }
        us_gaap = self.facts["facts"]["us-gaap"]
        
        print("\n integrity Check Results (Submission -> Facts):")
        print(f"{'CONCEPT':<20} | {'ACCESSION':<25} | {'VALUE':<15} | {'STATUS'}")
        print("-" * 80)
        
        for group_name, tags in concept_groups.items():
            found_group = False
            value = None
            found_tag = None
            
            for tag in tags:
                if tag in us_gaap:
                    # Iterate through all available units (USD, shares, etc.)
                    for unit_key in us_gaap[tag]["units"]:
                        units = us_gaap[tag]["units"][unit_key]
                        for u in units:
                            if u.get("accn") == latest_10k_acc:
                                found_group = True
                                value = u.get("val")
                                found_tag = tag
                                break 
                        if found_group: break
                if found_group: break
            
            status = f"✅ LINKED ({found_tag})" if found_group else "❌ BROKEN"
            val_str = f"{value:,.0f}" if value is not None else "N/A"
            print(f"{group_name:<20} | {latest_10k_acc:<25} | {val_str:<15} | {status}")
            
            # Assertion
            self.assertTrue(found_group, f"Concept Group {group_name} missing for Accession {latest_10k_acc}")

if __name__ == "__main__":
    unittest.main()
