
import json
import pandas as pd
import unittest
from pathlib import Path

# Paths
PARQUET_PATH = Path("valuation_data/canonical/financials/AAPL.parquet")
SERVING_PATH = Path("valuation_data/serving/base/AAPL_base.json")

class TestServingIntegrity(unittest.TestCase):
    
    @classmethod
    def setUpClass(cls):
        """Load Data Once"""
        # 1. Load Parquet
        if PARQUET_PATH.exists():
            cls.df = pd.read_parquet(PARQUET_PATH)
            # Filter for Latest 10-K to match Intake Logic
            k_df = cls.df[cls.df["form"] == "10-K"].sort_values("period_end_date")
            cls.latest_date = k_df.iloc[-1]["period_end_date"]
            cls.canonical_period = cls.df[cls.df["period_end_date"] == cls.latest_date]
            # Convert to dictionary {standard_tag: value}
            cls.canon_dict = dict(zip(cls.canonical_period["standard_tag"], cls.canonical_period["value"]))
        else:
            cls.df = None

        # 2. Load Serving Base
        if SERVING_PATH.exists():
            with open(SERVING_PATH, "r") as f:
                cls.serving = json.load(f)
        else:
            cls.serving = None

    def test_files_exist(self):
        self.assertIsNotNone(self.df, "Canonical Parquet not found")
        self.assertIsNotNone(self.serving, "Serving Base JSON not found")

    def test_direct_passthroughs(self):
        """Verify fields that should be direct copies."""
        if not self.serving: return

        print(f"\n🚀 AUDITING SERVING LAYER (Date: {self.latest_date}) 🚀")
        print(f"{'FIELD':<20} | {'SERVING VAL':<20} | {'CANONICAL VAL':<20} | {'STATUS'}")
        print("-" * 90)

        # Mapping: Serving Path -> Canonical Tag
        # Serving structure: financials -> income_statement / balance_sheet
        fin = self.serving["financials"]
        
        checks = [
            (fin["income_statement"].get("revenue"), "revenue", "Revenue"),
            (fin["income_statement"].get("net_income"), "net_income", "Net Income"),
            (fin["balance_sheet"].get("assets"), "assets", "Assets"),
            (fin["balance_sheet"].get("liabilities"), "liabilities", "Liabilities"),
            (fin["balance_sheet"].get("equity"), "equity", "Equity"),
            (fin["balance_sheet"].get("cash"), "cash", "Cash"),
        ]

        for s_val, c_tag, label in checks:
            c_val = self.canon_dict.get(c_tag, 0)
            
            s_str = f"{s_val:,.0f}"
            c_str = f"{c_val:,.0f}"
            status = "✅ MATCH" if abs(s_val - c_val) < 1.0 else "❌ MISMATCH"
            
            print(f"{label:<20} | {s_str:<20} | {c_str:<20} | {status}")
            self.assertAlmostEqual(s_val, c_val, delta=1.0, msg=f"Mismatch for {label}")

    def test_calculated_logic(self):
        """Verify fields that are calculated in the Intake Agent."""
        if not self.serving: return
        
        print(f"\n🧩 AUDITING CALCULATED FIELDS 🧩")
        print(f"{'FIELD':<20} | {'SERVING VAL':<20} | {'RE-CALC VAL':<20} | {'STATUS'}")
        print("-" * 90)

        fin = self.serving["financials"]
        bs = fin["balance_sheet"]
        
        # 1. Invested Capital
        # Formula: (Assets - Cash) - (Liabilities - Total Debt) + Adjustments(0 for now)
        # Note: In Intake, adjustments = R&D * 3. We need to check if R&D is in canonical.
        
        assets = self.canon_dict.get("assets", 0)
        cash = self.canon_dict.get("cash", 0)
        liabs = self.canon_dict.get("liabilities", 0)
        
        # Debt Logic in Intake:
        # if long_term_debt == 0 and current_debt == 0: total_debt = liabilities - equity
        # else: total_debt = long + current
        db_long = self.canon_dict.get("debt_long", 0)
        db_curr = self.canon_dict.get("debt_current", 0)
        equity = self.canon_dict.get("equity", 0)
        
        if db_long == 0 and db_curr == 0:
            calc_debt = liabs - equity
        else:
            calc_debt = db_long + db_curr
            
        # R&D Adjustment
        rnd = self.canon_dict.get("r&d", 0)
        adj = rnd * 3
        
        # Re-calculate Invested Capital
        op_assets = assets - cash
        op_liabs = liabs - calc_debt
        recalc_ic = (op_assets - op_liabs) + adj
        
        serving_ic = bs.get("invested_capital")
        
        s_str = f"{serving_ic:,.0f}"
        c_str = f"{recalc_ic:,.0f}"
        status = "✅ MATCH" if abs(serving_ic - recalc_ic) < 1.0 else "❌ MISMATCH"
        
        print(f"{'Invested Capital':<20} | {s_str:<20} | {c_str:<20} | {status}")
        self.assertAlmostEqual(serving_ic, recalc_ic, delta=1.0, msg="Invested Capital Mismatch")
        
        # 2. Total Debt
        serving_debt = bs.get("total_debt")
        s_str = f"{serving_debt:,.0f}"
        c_str = f"{calc_debt:,.0f}"
        status = "✅ MATCH" if abs(serving_debt - calc_debt) < 1.0 else "❌ MISMATCH"
        print(f"{'Total Debt':<20} | {s_str:<20} | {c_str:<20} | {status}")
        self.assertAlmostEqual(serving_debt, calc_debt, delta=1.0, msg="Total Debt Mismatch")


if __name__ == "__main__":
    unittest.main()
