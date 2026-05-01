"""
aletheia/tools/capital_structure.py

Deterministic calculations for solvency, liquidity, and stress testing.
Extracted from strategist.py to ensure testability and isolation.
"""

import pandas as pd
import json
from aletheia.utils.config import load_config

class CapitalStructureRiskEngine:
    """
    Analyzes solvency, liquidity risks, and structural leverage using DuckDB data.
    """
    def __init__(self, state, row_data, market_cap):
        self.state = state
        self.row_data = row_data
        self.market_cap = market_cap
        self.config = load_config()
        
    def analyze_maturity_wall(self):
        # We don't have perfect Short Term Debt in the generic DuckDB schema yet, 
        # so we proxy with CurrentLiabilities if available, or just use 20% of Long Term Debt as current portion.
        long_term_debt = self.row_data.get("raw_LongTermDebt", 0.0)
        current_liabilities = self.row_data.get("raw_CurrentLiabilities", 0.0)
        
        # Fallback for nan values
        if pd.isna(long_term_debt): long_term_debt = 0.0
        if pd.isna(current_liabilities): current_liabilities = 0.0
        
        short_term_debt = current_liabilities * 0.2 # Proxy
        
        amort_rate = self.config.maturity_amortization_rate
        maturities_next_2_years = short_term_debt + (long_term_debt * amort_rate) 
        
        cash = self.row_data.get("raw_Cash", 1.0) # Avoid div/0
        if pd.isna(cash) or cash <= 0: cash = 1.0
        
        ratio = maturities_next_2_years / cash
        
        risk_score = min(10, int(ratio * 3))
        threshold = self.config.liquidity_ratio_safe
        liquidity_alert = ratio > threshold
            
        return {
            "maturities_next_2y": maturities_next_2_years,
            "cash": cash,
            "liquidity_ratio": ratio,
            "refinancing_risk_score": risk_score,
            "liquidity_alert": liquidity_alert
        }

    def get_dynamic_wacc_schedule(self, base_wacc, current_de, target_de):
        schedule = {}
        if target_de and current_de > 0 and abs(target_de - current_de) / current_de > 0.20:
             if target_de > current_de:
                 target_wacc = base_wacc * 0.95 
             else:
                 target_wacc = base_wacc * 1.05 
             schedule = {
                 1: base_wacc,
                 2: base_wacc,
                 3: base_wacc, 
                 4: (base_wacc + target_wacc)/2,
                 5: target_wacc
             }
        else:
            for i in range(1, 6):
                schedule[i] = base_wacc
        return schedule

    def run_break_the_company_audit(self, base_wacc):
        assets = self.row_data.get("raw_TotalAssets", 0.0)
        liabilities = self.row_data.get("raw_TotalLiabilities", 0.0)
        
        if pd.isna(assets): assets = 0.0
        
        # Fallback for liabilities
        if pd.isna(liabilities) or liabilities == 0.0:
            equity = self.row_data.get("raw_TotalEquity", 0.0)
            if not pd.isna(equity) and equity != 0.0:
                liabilities = assets - equity
                
        if pd.isna(liabilities): liabilities = 0.0
        
        tangible_book = assets - liabilities 
        
        ebitda = self.row_data.get("derived_EBITDA", 0.0)
        if pd.isna(ebitda): ebitda = 0.0
        
        rev_impact = self.config.stress_test_revenue_impact
        crash_ebitda = ebitda * rev_impact 
        
        wacc_impact = self.config.stress_test_wacc_impact
        crash_wacc = base_wacc + wacc_impact
        
        try:
            clean_data = json.loads(self.row_data.get("clean_json", "{}"))
        except Exception:
            clean_data = {}
        capex = abs(clean_data.get("CapEx", 0.0) or clean_data.get("CapEx_Total", 0.0))
        if pd.isna(capex): capex = 0.0
        maint_capex = capex * 0.5
        
        tax_rate = self.row_data.get("clean_CashTaxRate", 0.21)
        if pd.isna(tax_rate): tax_rate = 0.21
        
        fcf_crash = (crash_ebitda * (1 - tax_rate)) - maint_capex 
        
        earnings_power_value = fcf_crash / crash_wacc if crash_wacc > 0 else 0
        floor_value = max(tangible_book, earnings_power_value)
        
        return {
            "tangible_book_value": tangible_book,
            "crash_fcf": fcf_crash,
            "earnings_power_value": earnings_power_value,
            "floor_value": floor_value,
            "floor_price_per_share": 0 # Handled in DCF/Bridge
        }

    def check_double_leverage(self, financial_leverage_de):
        forensic_report = self.state.get("forensic_report", {})
        op_leverage_score = forensic_report.get("operating_leverage_score", 5)
        
        fin_leverage_score = min(10, financial_leverage_de * 4) 
        
        double_leverage_flag = False
        threshold = self.config.double_leverage_threshold
        if op_leverage_score > threshold and fin_leverage_score > threshold:
            double_leverage_flag = True
            
        return {
            "operating_leverage_score": op_leverage_score,
            "financial_leverage_score": fin_leverage_score,
            "double_leverage_flag": double_leverage_flag
        }
