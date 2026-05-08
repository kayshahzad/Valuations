from langchain_core.messages import HumanMessage
from aletheia.utils.tracing import tracer
from aletheia.utils.config import load_config
from aletheia.data.database import InvestmentDatabase
from aletheia.tools.dcf_engine import compute_wacc, _compute_beta, _fetch_risk_free_rate
import pandas as pd
import numpy as np
import re
import json

from aletheia.tools.capital_structure import CapitalStructureRiskEngine

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
        row = record_df[record_df["fiscal_year"] == record_df["fiscal_year"].max()].iloc[0].to_dict()
    finally:
        db.close()

    # Get market cap via market_data for accurate debt/equity ratio
    from aletheia.data.market_data import get_market_cap
    try:
        market_cap = get_market_cap(ticker)
        if market_cap <= 0:
            market_cap = row.get("raw_TotalEquity", 1.0)
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
    
    # WACC Floor — only as catastrophic-fallback, not as routine override.
    # Previously this clipped any WACC < 9% UP to 9%, which (a) created a
    # visible discrepancy with the analytically-correct phase2.wacc that
    # the dashboard displays, and (b) was masking the real bug — a too-low
    # beta. With the sector-aware β floor in dcf_engine._compute_beta now
    # raising defensive-name betas to plausible levels, this hard floor
    # mostly never binds. Keeping it only for the catastrophic case where
    # WACC came back ≤ 0 (data error).
    if wacc is None or wacc <= 0:
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

