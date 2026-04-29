
import json
import pandas as pd
import unittest
from pathlib import Path

# Paths
PARQUET_PATH = Path("valuation_data/canonical/financials/AAPL.parquet")
SERVING_PATH = Path("valuation_data/serving/base/AAPL_base.json")

class TestServingFullCoverage(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """Load Data Once"""
        # 1. Load Parquet (Source of Truth)
        if PARQUET_PATH.exists():
            cls.df = pd.read_parquet(PARQUET_PATH)
            # Filter for Latest 10-K
            k_df = cls.df[cls.df["form"] == "10-K"].sort_values("period_end_date")
            cls.latest_date = k_df.iloc[-1]["period_end_date"]
            cls.canon_df = cls.df[cls.df["period_end_date"] == cls.latest_date]
            # Create Lookup Dict: {standard_tag: value}
            cls.canon = dict(zip(cls.canon_df["standard_tag"], cls.canon_df["value"]))
        else:
            cls.canon = {}

        # 2. Load Serving Base (Artifact Under Test)
        if SERVING_PATH.exists():
            with open(SERVING_PATH, "r") as f:
                cls.serving = json.load(f)
        else:
            cls.serving = {}
            
    def test_files_exist(self):
        self.assertTrue(self.canon, "Canonical Data not loaded")
        self.assertTrue(self.serving, "Serving Data not loaded")

    def test_income_statement_coverage(self):
        """Validate every field in Income Statement"""
        if not self.serving: return
        print("\n📊 AUDIT: INCOME STATEMENT")
        print(f"{'FIELD':<20} | {'SERVING':<20} | {'CANONICAL':<20} | {'STATUS'}")
        print("-" * 80)
        
        inc = self.serving["financials"]["income_statement"]
        
        # Direct Mapping: Serving Key -> Canonical Key
        direct_map = {
            "revenue": "revenue",
            "cogs": "cogs",
            "opex": "opex",
            "net_income": "net_income"
            # Gross Profit, EBIT, EBITDA are calculated
        }
        
        for s_key, c_key in direct_map.items():
            s_val = inc.get(s_key, 0)
            c_val = self.canon.get(c_key, 0)
            self._assert_match(s_key, s_val, c_val)

        # Calculated Fields
        rev = inc.get("revenue", 0)
        cogs = inc.get("cogs", 0)
        dep = self.serving["financials"]["cash_flow"].get("depreciation", 0)
        
        # Gross Profit = Rev - COGS
        gp = inc.get("gross_profit", 0)
        calc_gp = rev - cogs
        self._assert_match("gross_profit (calc)", gp, calc_gp)
        
        # EBIT (Eco) = Rev - COGS - Opex - Dep (Implicit in Intake logic)
        # Actually Intake Logic: ebit_eco = (Rev - Cogs - Opex) OR Canonical EBIT
        # Let's check logic alignment. 
        # Intake: op_inc = data.get("ebit") or (rev - cogs - opex)
        # Intake: ebitda_eco = op_inc + dep + rnd
        # Intake: ebit_eco = ebitda_eco - dep  (= op_inc + rnd)
        # Let's simplify: check if it matches Canonical EBIT if available, or recalc
        
        # For AAPL, we have 'ebit' in canonical.
        canon_ebit = self.canon.get("ebit", 0)
        s_ebit = inc.get("ebit", 0)
        # Note: serving ebit might include R&D add-back if logic dictates.
        # AAPL R&D is usually in Opex.
        # Let's see if they match directly first.
        self._assert_match("ebit", s_ebit, canon_ebit, tolerance=1.0) # Might fail if R&D adjust exists

    def test_balance_sheet_coverage(self):
        """Validate every field in Balance Sheet"""
        if not self.serving: return
        print("\n⚖️ AUDIT: BALANCE SHEET")
        print(f"{'FIELD':<20} | {'SERVING':<20} | {'CANONICAL':<20} | {'STATUS'}")
        print("-" * 80)
        
        bs = self.serving["financials"]["balance_sheet"]
        
        direct_map = {
            "cash": "cash",
            "assets": "assets",
            "equity": "equity",
            "liabilities": "liabilities",
            "debt_current": "debt_current",
            "debt_long": "debt_long"
        }
        
        for s_key, c_key in direct_map.items():
            s_val = bs.get(s_key, 0)
            c_val = self.canon.get(c_key, 0)
            self._assert_match(s_key, s_val, c_val)
            
        # Total Debt
        # Intake Logic: if long+curr > 0 then sum, else liab - equity.
        # For AAPL we have debt details.
        t_debt = bs.get("total_debt", 0)
        c_long = self.canon.get("debt_long", 0)
        c_curr = self.canon.get("debt_current", 0)
        calc_debt = c_long + c_curr if (c_long+c_curr) > 0 else (self.canon.get("liabilities",0) - self.canon.get("equity",0))
        self._assert_match("total_debt (calc)", t_debt, calc_debt)

    def test_ratio_coverage(self):
        """Validate Ratios"""
        if not self.serving: return
        print("\n➗ AUDIT: RATIOS")
        print(f"{'FIELD':<20} | {'SERVING':<20} | {'CALCULATED':<20} | {'STATUS'}")
        print("-" * 80)
        
        ratios = self.serving["ratios"]
        inc = self.serving["financials"]["income_statement"]
        
        rev = inc.get("revenue", 1)
        cogs = inc.get("cogs", 0)
        op_inc = inc.get("ebit", 0) # Using EBIT as proxy for Op Income in this context
        
        # GM
        gm = ratios.get("gross_margin", 0)
        calc_gm = (rev - cogs) / rev
        self._assert_match("gross_margin", gm, calc_gm, is_percent=True)
        
        # Op Margin
        # Intake: op_inc / rev
        om = ratios.get("operating_margin", 0)
        # Note: Intake logic uses 'op_inc' variable which is derived. 
        # Ideally OM should be EBIT / Revenue.
        calc_om = op_inc / rev
        # Checking if they align.
        self._assert_match("operating_margin", om, calc_om, is_percent=True)

    def _assert_match(self, label, val1, val2, tolerance=1.0, is_percent=False):
        """Helper to print and assert"""
        diff = abs(val1 - val2)
        match = diff < tolerance
        
        if is_percent:
            v1_str = f"{val1:.1%}"
            v2_str = f"{val2:.1%}"
            # diff for percent is usually small
            match = diff < 0.001
        else:
            v1_str = f"{val1:,.0f}"
            v2_str = f"{val2:,.0f}"
            
        status = "✅ MATCH" if match else "❌ MISMATCH"
        print(f"{label:<20} | {v1_str:<20} | {v2_str:<20} | {status}")
        
        if not match:
             # We assume strict matching for this phase
             self.fail(f"Mismatch for {label}: {v1_str} vs {v2_str}")

if __name__ == "__main__":
    unittest.main()
