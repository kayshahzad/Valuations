from langchain_core.messages import HumanMessage
from aletheia.utils.tracing import tracer
from aletheia.utils.config import load_config
from aletheia.data.database import InvestmentDatabase
from aletheia.tools.dcf_engine import compute_wacc, _compute_beta, _fetch_risk_free_rate
import yfinance as yf
import pandas as pd
import numpy as np
import re

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
        
        capex = abs(self.row_data.get("clean_CapEx_Total", 0.0))
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

def strategist_agent(state):
    print("---STRATEGIST AGENT (Served)---")
    
    ticker = state.get("ticker", "UNKNOWN")
    config = load_config()
    
    # 1. Fetch from Database
    db = InvestmentDatabase(verbose=False)
    try:
        record_df = db.get_latest(ticker)
        if record_df.empty:
            return {"messages": [HumanMessage(content=f"Strategist: No DB data for {ticker}")]}
        row = record_df.iloc[0].to_dict()
    finally:
        db.close()

    # Get market cap via yfinance for accurate debt/equity ratio
    try:
        info = yf.Ticker(ticker).fast_info
        market_cap = float(info.market_cap or 0)
    except Exception:
        market_cap = row.get("raw_TotalEquity", 1.0)
        
    long_term_debt = row.get("raw_LongTermDebt", 0.0)
    cash = row.get("raw_Cash", 0.0)
    net_debt = row.get("derived_NetDebt", 0.0)
    
    if pd.isna(long_term_debt): long_term_debt = 0.0
    if pd.isna(cash): cash = 0.0
    if pd.isna(net_debt): net_debt = 0.0
    
    total_debt = max(net_debt + cash, long_term_debt)
    
    # Calculate current D/E Ratio based on Market Equity
    current_de_ratio = total_debt / market_cap if market_cap > 0 else 0.0
    
    # 2. Dynamic Cost of Capital (WACC)
    tax_rate = row.get("clean_CashTaxRate", 0.21)
    if pd.isna(tax_rate): tax_rate = 0.21
    
    interest_expense = long_term_debt * 0.04 # Approximation
    
    rf = _fetch_risk_free_rate()
    beta = _compute_beta(ticker)
    
    wacc, cost_of_equity, cost_of_debt, wacc_weights = compute_wacc(
        ticker=ticker,
        total_equity=market_cap,
        total_debt=total_debt,
        interest_expense=interest_expense,
        tax_rate=tax_rate,
        risk_free_rate=rf,
        beta=beta
    )
    
    # WACC Floor
    if wacc < config.wacc_floor:
        wacc = config.wacc_floor

    # Target D/E
    dcf_config = state.get("dcf_config") or {}
    target_de = dcf_config.get("target_debt_equity")
    applied_de_ratio = target_de if target_de is not None else current_de_ratio
    
    # 3. Risk Engine
    risk_engine = CapitalStructureRiskEngine(state, row, market_cap)
    
    maturity_analysis = risk_engine.analyze_maturity_wall()
    wacc_schedule = risk_engine.get_dynamic_wacc_schedule(wacc, current_de_ratio, applied_de_ratio)
    downside = risk_engine.run_break_the_company_audit(wacc)
    leverage_check = risk_engine.check_double_leverage(applied_de_ratio)
    
    if maturity_analysis["liquidity_alert"]:
        print(f"⚠️ Liquidity Alert: Near-term maturities exceed safe limit!")

    # 4. Output
    output = {
        "strategist_report": {
            "capital_stack": {
                "debt_current": 0.0, # Proxying
                "debt_long": long_term_debt,
                "equity": market_cap,
                "wacc": wacc,
                "cost_of_equity": cost_of_equity,
                "beta": beta
            },
            "risk_factors": {
                 "liquidity": maturity_analysis,
                 "downside": downside,
                 "leverage": leverage_check,
                 "wacc_schedule": wacc_schedule
            }
        },
        "messages": [HumanMessage(content=f"Strategist: Calculated WACC {wacc:.1%}.")]
    }
    tracer.log_step("Strategist", state, output)
    return output

