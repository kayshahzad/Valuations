import json
import pandas as pd
from pathlib import Path
from datetime import datetime
from langchain_core.messages import HumanMessage
from aletheia.utils.config import load_config
from aletheia.utils.tracing import tracer

class CanonicalLoader:
    """
    Loads standardized financial data from the Canonical Parquet Layer.
    """
    def __init__(self):
        self.base_path = Path("valuation_data/canonical/financials")
        
    def load_financials(self, ticker: str) -> pd.DataFrame:
        """
        Reads {ticker}.parquet and returns a DataFrame.
        """
        path = self.base_path / f"{ticker.upper()}.parquet"
        if not path.exists():
            tracer.log_step("CanonicalLoader", {"ticker": ticker, "path": str(path)}, {"status": "file_not_found"})
            return pd.DataFrame()
            
        try:
            df = pd.read_parquet(path)
            # Ensure proper types
            df["period_end_date"] = pd.to_datetime(df["period_end_date"])
            df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0)
            
            tracer.log_step("CanonicalLoader", {"ticker": ticker}, {"status": "loaded", "rows": len(df)})
            return df
        except Exception as e:
            tracer.log_step("CanonicalLoader", {"ticker": ticker}, {"status": "error", "error": str(e)})
            print(f"❌ Error loading canonical data for {ticker}: {e}")
            return pd.DataFrame()

class FREDLoader:
    """
    Loads Macro Data from local FRED repository.
    """
    def __init__(self):
        self.base_path = Path("valuation_data/raw/fred/series")
        
    def get_latest_value(self, series_id: str, default=0.0):
        path = self.base_path / f"{series_id}.json"
        if not path.exists():
            return default
            
        try:
            with open(path, "r") as f:
                data = json.load(f)
            # Observations usually sorted, take last
            obs = data.get("observations", [])
            if not obs: return default
            
            val = float(obs[-1].get("value", 0))
            tracer.log_step("FREDLoader", {"series": series_id}, {"value": val})
            return val / 100.0 if val > 0 else default 
        except:
            return default

class ServingBaseWriter:
    """
    Writes the Base Fact JSON to the Serving Layer.
    """
    def __init__(self):
        self.base_path = Path("valuation_data/serving/base")
        self.base_path.mkdir(parents=True, exist_ok=True)
        
    def save_base(self, ticker: str, data: dict):
        path = self.base_path / f"{ticker.upper()}_base.json"
        try:
            with open(path, "w") as f:
                json.dump(data, f, indent=2)
            tracer.log_step("ServingBaseWriter", {"ticker": ticker}, {"status": "saved", "path": str(path)})
            # Also save to state via return
        except Exception as e:
            tracer.log_step("ServingBaseWriter", {"ticker": ticker}, {"status": "failed", "error": str(e)})

class FinancialEngine:
    """
    Core math capability. Handles TTM aggregation.
    """
    def __init__(self, df: pd.DataFrame):
        self.df = df
        
    def get_ttm(self, target_date: datetime.date) -> dict:
        """
        Calculates Trailing Twelve Months (TTM) or Latest Available.
        """
        k_df = self.df[self.df["form"] == "10-K"].sort_values("period_end_date")
        if k_df.empty: return {} 
        
        latest_k = k_df.iloc[-1]
        
        # For now, sticking to Latest Annual as the "Truth" anchor
        # until 10-Q rolling logic is robust.
        base_ttm = self._pivot_period(latest_k["period_end_date"])
        
        # Inject Meta
        base_ttm["_meta"] = {
            "period_end_date": latest_k["period_end_date"].isoformat(),
            "form": latest_k["form"],
            "fy": int(latest_k["fy"]) if str(latest_k["fy"]).isdigit() else 0
        }
        return base_ttm

    def _pivot_period(self, date) -> dict:
        subset = self.df[self.df["period_end_date"] == date]
        return dict(zip(subset["standard_tag"], subset["value"]))

class EconomicRealityTranslator:
    """
    Translates Accounting Data to Economic Data including Ratios.
    """
    def __init__(self):
        self.config = load_config()
        self.fred_loader = FREDLoader()

    def translate(self, data: dict):
        # 1. Map Keys (Aligned to new Lowercase Maps)
        rev = data.get("revenue", 0)
        cogs = data.get("cogs", 0)
        medical_costs = data.get("medical_claims", 0)
        
        # --- OPEX Enforcement (Module 1.2) ---
        operating_expenses = data.get("operatingexpenses", 0) # Priority 1
        sga = data.get("sellinggeneralandadministrativeexpense", 0)
        rnd = data.get("researchanddevelopmentexpense", 0) # Needed for Cap Adjust too
        
        if operating_expenses > 0:
            opex = operating_expenses
        else:
            opex = sga + rnd
            
        # Sector Logic
        if medical_costs > 0:
            # Insurance / Managed Care Schema
            cost_of_sales = medical_costs
            sector_type = "Insurance"
        else:
            # Standard Manufacturing Schema
            cost_of_sales = cogs
            sector_type = "General"

        # 2. Earnings Economics
        op_inc = data.get("ebit") or (rev - cost_of_sales - opex)
        dep = data.get("depreciation") or (rev * 0.03) 
        
        # Hard Validation Gate
        # Reconcile: Rev - Costs - Opex = EBIT
        # We assume Opex excludes D&A usually in our map, or D&A is separate.
        # Let's check the gap.
        implied_ebit = rev - cost_of_sales - opex
        
        reconcile_gap = implied_ebit - op_inc
        # If gap is huge (e.g. > 5% of Rev), we have a data integrity failure.
        validation_status = "PASS"
        if abs(reconcile_gap) > (rev * 0.05):
            print(f"❌ DATA INTEGRITY ERROR: EBIT Reconciliation Failed. Gap {reconcile_gap/1e9:.1f}B")
            validation_status = "FAIL"
            
        if opex == 0 and (rev - cost_of_sales) > op_inc:
             # Gross Profit > EBIT, but Opex is 0? Suspicious.
             print("❌ DATA INTEGRITY ERROR: Zero OPEX detected despite Gross Profit > EBIT.")
             validation_status = "FAIL"
            
        # R&D needed for capitalization adjustment. 
        # Check if rnd was extracted
        rnd_adj = data.get("r&d", rnd) # Use priority or mapped legacy key
        
        ebitda_adj = rnd_adj 
        ebitda_eco = op_inc + dep + ebitda_adj
        ebit_eco = ebitda_eco - dep
        
        # 3. Balance Sheet
        assets = data.get("assets", 0)
        
        # --- Liquidity Roll-up (Module 1.1) ---
        cash_raw = data.get("cash", 0)
        securities_avail_sale = data.get("availableforsalesecuritiescurrent", 0) + data.get("availableforsalesecuritiesnoncurrent", 0)
        # Assuming 'cash' might already include equivalents, but 'marketable securities' are often separate in XML
        # If our source data maps 'CashAndCashEquivalentsAtCarryingValue' to 'cash', we add securities.
        cash_rolled_up = cash_raw + securities_avail_sale
        
        equity = data.get("equity", 0)
        
        # Debt logic
        long_term_debt = data.get("debt_long", 0)
        current_debt = data.get("debt_current", 0) # Proxy
        total_liab = data.get("liabilities", 0)
        total_debt_agg = data.get("total_debt", 0)
        
        # If detailed debt missing, estimate from Liab
        if long_term_debt == 0 and current_debt == 0:
             total_debt = total_liab - equity # Rough
        else:
             total_debt = long_term_debt + current_debt
             
        invested_capital_adj = rnd_adj * 3
        
        # --- Invested Capital Logic (Module 2) ---
        # Formula: (Total_Assets - Excess_Cash) - (Total_Liabilities - Total_Debt)
        
        # Excess Cash Rule: Cash above 2% of Revenue
        required_cash = rev * 0.02
        excess_cash = max(0, cash_rolled_up - required_cash)
        
        operating_assets = assets - excess_cash
        operating_liabilities = total_liab - total_debt
        invested_capital = (operating_assets - operating_liabilities) + invested_capital_adj
        
        # 4. Rates
        market_rate = self.fred_loader.get_latest_value("BAMLH0A0HYM2", default=0.07)
        interest_dynamic = total_debt * market_rate
        
        # 5. Ratios
        gross_margin = (rev - cogs) / rev if rev else 0.0
        op_margin = op_inc / rev if rev else 0.0
        
        net_income = data.get("NetIncome", 0)
        
        # Return Structured "Base" Schema
        return {
            "financials": {
                "income_statement": {
                    "revenue": rev,
                    "cogs": cogs,
                    "gross_profit": rev - cogs,
                    "opex": opex,
                    "ebit": ebit_eco,
                    "net_income": data.get("net_income", 0),
                    "ebitda": ebitda_eco
                },
                "balance_sheet": {
                    "cash": cash_rolled_up,
                    "debt_current": current_debt,
                    "debt_long": long_term_debt,
                    "total_debt": total_debt,
                    "equity": equity,
                    "assets": assets,
                    "liabilities": total_liab,
                    "invested_capital": invested_capital
                },
                "cash_flow": {
                    "depreciation": dep,
                    "capex": data.get("capex") or (rev * 0.05)
                }
            },
            "ratios": {
                "gross_margin": (rev-cogs)/rev if (rev and sector_type!="Insurance") else 0.0,
                "medical_loss_ratio": cost_of_sales/rev if (rev and sector_type=="Insurance") else 0.0,
                "operating_margin": op_margin,
                "debt_to_equity": total_debt / equity if equity else 0.0,
                "tax_rate": self.config.tax_rate_global
            },
            "economics": {
                "risk_free_rate": self.fred_loader.get_latest_value("DGS10", default=0.04),
                "cost_of_debt_market": market_rate,
                "interest_expense_eco": interest_dynamic,
                "capitalization_adjustments": {"R&D Asset": invested_capital_adj}
            },
            "meta": {
                **data.get("_meta", {}),
                "validation_status": validation_status,
                "reconciliation_gap": reconcile_gap,
                "sector_type": sector_type
            }
        }

def intake_agent(state):
    """
    The Intake Agent: Canonical -> Serving Base.
    Generates the Single Source of Truth for all other agents.
    """
    print("---INTAKE AGENT (Canonical -> Serving Base)---")
    ticker = state.get("ticker", "AAPL")
    
    return {"serving_base": {}, "messages": [HumanMessage(content=f"Intake: DB is source of truth.")]}
