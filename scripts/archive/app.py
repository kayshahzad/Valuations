import streamlit as st
import pandas as pd
import plotly.express as px
from typing import Dict, Any, List
import json
import os

# Aletheia Imports
from aletheia.workflow.graph import create_workflow
from aletheia.agents.fundamentalist import fundamentalist_agent
from aletheia.agents.lead import lead_agent
from aletheia.state import AgentState, DCFConfig

# Page Config
st.set_page_config(page_title="Aletheia Intelligence", layout="wide", page_icon="⚖️")

# --- CSS Styling ---
st.markdown("""
<style>
    .metric-card {
        background-color: #f0f2f6;
        padding: 20px;
        border-radius: 10px;
        margin-bottom: 20px;
        text-align: center;
    }
    .metric-value {
        font-size: 2em;
        font-weight: bold;
    }
    .metric-label {
        color: #555;
    }
</style>
""", unsafe_allow_html=True)

# --- Session State ---
if "base_state" not in st.session_state:
    st.session_state["base_state"] = None
if "current_state" not in st.session_state:
    st.session_state["current_state"] = None
if "pinned_scenarios" not in st.session_state:
    st.session_state["pinned_scenarios"] = []

# --- Helper Functions ---
def run_full_analysis(ticker: str):
    """Runs the full graph to get baseline data."""
    with st.spinner(f"Running full analysis for {ticker}... (Librarian, Contrarian, Strategist)"):
        workflow = create_workflow()
        initial = {"ticker": ticker, "messages": []}
        result = workflow.invoke(initial)
        st.session_state["base_state"] = result
        st.session_state["current_state"] = result
        
def update_valuation(dcf_config: DCFConfig):
    """Re-runs only Fundamentalist and Lead agents with new config."""
    if not st.session_state["base_state"]:
        return

    # Use a copy of base state to preserve original data (Librarian/Contrarian/Strategist)
    # Note: Deep copy might be safer but for MVP dict copy + config override is okay
    # We want to keep the messages history or reset it?
    # Let's start from base_state but inject the new config.
    
    # Simple shallow copy of top level keys is tricky if agents modify nested dicts in place.
    # Let's rely on the agents returning NEW dicts for their reports, which they do.
    state = st.session_state["base_state"].copy() 
    state["dcf_config"] = dcf_config
    
    # Re-run Fundamentalist
    with st.spinner("Recalculating Valuation..."):
        fund_result = fundamentalist_agent(state)
        # Manually merge output back to state (Graph usually does this)
        state["valuation_report"] = fund_result["valuation_report"]
        state["messages"].extend(fund_result["messages"])
        
        # Re-run Lead
        lead_result = lead_agent(state)
        state["final_report"] = lead_result["final_report"]
        state["messages"].extend(lead_result["messages"])
        
        st.session_state["current_state"] = state

def check_contrarian_warning(growth_rate: float, contrarian_report: dict):
    """Show warning if growth is aggressive vs Contrarian sentiment."""
    # Heuristic: If growth > 20% and Sentiment is negative
    sentiment_score = 0
    if "structured_analysis" in contrarian_report:
        sentiment_score = contrarian_report["structured_analysis"].get("sentiment_score", 0)
    
    if growth_rate > 0.20:
        msg = f"⚠️ High Growth Assumption ({growth_rate:.1%}) vs Sentiment ({sentiment_score})"
        if sentiment_score < 0:
            st.toast(msg, icon="🐻")
            st.warning(f"Contrarian Warning: You are projecting aggressive growth ({growth_rate:.1%}) while market sentiment is negative (Score: {sentiment_score}).\n\nBear Case: {contrarian_report['structured_analysis'].get('bear_case_summary', '')}")


# --- Sidebar ---
with st.sidebar:
    st.title("🎛️ Valuation Levers")
    
    ticker_input = st.text_input("Ticker", value="MSFT").upper()
    if st.button("Run Full Analysis"):
        run_full_analysis(ticker_input)
        
    st.markdown("---")
    
    # Only show levers if we have data
    if st.session_state["base_state"]:
        metrics = st.session_state["base_state"]["financial_data"]["metrics"]
        base_growth = metrics.get("revenue_growth", 0.10)
        base_margin = metrics.get("profit_margins", 0.20)
        
        # Levers
        wacc_input = st.slider("WACC (%)", 4.0, 15.0, 10.0, 0.1, help="Weighted Average Cost of Capital") / 100.0
        
        growth_input = st.slider("Initial Growth (%)", 0.0, 50.0, base_growth * 100, 0.5) / 100.0
        
        decay_input = st.slider("Growth Decay Rate", 0.01, 0.10, 0.02, 0.01)
        
        margin_input = st.slider("Target EBIT Margin (%)", 0.0, 50.0, base_margin * 100, 0.5) / 100.0
        
        reinvestment_input = st.slider("Reinvestment Rate (%)", 0.0, 50.0, 10.0, 1.0) / 100.0

        # Pin Scenario Button
        if st.button("📌 Pin Scenario"):
            scenario = {
                "name": f"Scenario {len(st.session_state['pinned_scenarios']) + 1}",
                "wacc": wacc_input,
                "growth": growth_input,
                "upside": st.session_state["current_state"]["valuation_report"].get("calculated_upside", 0)
            }
            st.session_state["pinned_scenarios"].append(scenario)
            st.success("Pinned!")

        # Auto-update logic
        dcf_config: DCFConfig = {
            "revenue_growth_initial": growth_input,
            "growth_decay_rate": decay_input,
            "target_ebit_margin": margin_input,
            "reinvestment_rate": reinvestment_input,
            "wacc_override": wacc_input
        }
        
        # Trigger update
        update_valuation(dcf_config)
        
        # Contrarian Check
        check_contrarian_warning(growth_input, st.session_state["base_state"]["contrarian_report"])

# --- Main Dashboard ---
st.title("Aletheia Intelligence: Valuation Dashboard")

if st.session_state["current_state"]:
    state = st.session_state["current_state"]
    fin_data = state["financial_data"]["metrics"]
    val_report = state["valuation_report"]
    final_report = state.get("final_report")
    
    # 1. Top Level Metrics
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Ticker", fin_data.get("ticker"))
    with col2:
        current_price = fin_data.get("current_price", 0)
        st.metric("Current Price", f"${current_price:.2f}")
    with col3:
        intrinsic = val_report.get("dcf_result", {}).get("enterprise_value", 0)
        # Enterprise Value is massive, maybe share price is better?
        # Fundamentalist returns EV. We need EqV -> Share Price.
        # Quick Hack: Fundamentalist calc'd upside directly.
        # Let's reverse calc share price from upside? 
        # Upside = (Intrinsic - MarketCap) / MarketCap
        # Intrinsic = Upside * MarketCap + MarketCap
        # Implied Price = Current * (1 + Upside)
        upside = val_report.get("calculated_upside", 0)
        implied_price = current_price * (1 + upside)
        st.metric("Implied Value", f"${implied_price:.2f}", delta=f"{upside:.2%}")
    with col4:
        if final_report:
             st.metric("Conviction", f"{final_report.conviction_score}/10")
        else:
            st.metric("Conviction", "N/A")

    # 2. Tabs for details
    tab_thesis, tab_dcf, tab_scenarios, tab_raw = st.tabs(["📜 Investment Thesis", "📊 DCF Analysis", "📌 Scenarios", "🔍 Raw Data"])
    
    with tab_thesis:
        if final_report:
            st.subheader("Growth Assessment")
            st.write(final_report.growth_decay_assessment)
            
            st.subheader("Contrarian Rebuttal")
            st.info(final_report.contrarian_rebuttal)
        else:
            st.warning("Lead Agent report pending...")
            
    with tab_dcf:
        dcf_res = val_report.get("dcf_result", {})
        projections = dcf_res.get("projections", [])
        
        if projections:
            df_proj = pd.DataFrame(projections)
            fig = px.bar(df_proj, x="year", y="fcf", color="stage", title="Projected Free Cash Flows (3-Stage Model)")
            st.plotly_chart(fig, use_container_width=True)
            
            with st.expander("Detailed Projections"):
                st.dataframe(df_proj)
                
        # Sensitivity
        matrix = val_report.get("sensitivity_matrix", {})
        if matrix:
            st.subheader("Sensitivity Analysis (Enterprise Value)")
            # Convert dict to clean 3x3 DF?
            # Keys are like 'wacc_base_growth_base'. 
            # Let's display raw for now or format nicely later.
            st.json(matrix)

    with tab_scenarios:
        if st.session_state["pinned_scenarios"]:
            sc_df = pd.DataFrame(st.session_state["pinned_scenarios"])
            st.dataframe(sc_df)
            
            # Comparison Chart
            fig_comp = px.bar(sc_df, x="name", y="upside", title="Scenario Comparison (Upside %)")
            st.plotly_chart(fig_comp)
        else:
            st.info("Pin scenarios from the sidebar to compare them here.")
            
    with tab_raw:
        st.json(state)
        
else:
    st.info("👈 Enter a ticker in the sidebar and click 'Run Full Analysis' to start.")
