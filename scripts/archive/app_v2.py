# ... (Imports remain the same)
import streamlit as st
import pandas as pd
import plotly.graph_objects as go
from typing import Dict, Any

# Aletheia Imports
from aletheia.workflow.graph import create_workflow
from aletheia.agents.fundamentalist import fundamentalist_agent
from aletheia.agents.strategist import strategist_agent
from aletheia.state import DCFConfig

# Page Config
st.set_page_config(page_title="Aletheia Pro-Forma Engine", layout="wide", page_icon="🏦")

# --- CSS Styling ---
st.markdown("""
<style>
    .big-font { font-size: 20px !important; }
    .warning-box {
        background-color: #ffcccc;
        padding: 15px;
        border-left: 5px solid #ff0000;
        border-radius: 5px;
        color: #990000;
        margin-bottom: 20px;
    }
    .metric-container {
        padding: 10px;
        border-radius: 5px;
        background-color: #f0f2f6;
        border: 1px solid #d6d6d6;
    }
</style>
""", unsafe_allow_html=True)

# --- Session State ---
if "base_state" not in st.session_state:
    st.session_state["base_state"] = None
if "current_state" not in st.session_state:
    st.session_state["current_state"] = None

def run_analysis(ticker: str):
    with st.spinner(f"Agents are researching {ticker}..."):
        workflow = create_workflow()
        result = workflow.invoke({"ticker": ticker, "messages": []})
        st.session_state["base_state"] = result
        st.session_state["current_state"] = result

def recalculate(config: DCFConfig):
    if not st.session_state["base_state"]: return
    
    with st.status("Recalculating Economic Engine...", expanded=True) as status:
        # Clone and update
        state = st.session_state["base_state"].copy()
        state["dcf_config"] = config
        
        status.update(label="Applying Configuration Overrides...", state="running")
        # 1. Run Strategist (for WACC)
        strat_res = strategist_agent(state)
        state["strategist_report"] = strat_res["strategist_report"]
        state["messages"].extend(strat_res["messages"])
        
        status.update(label="Updating Solvency Stress Tests...", state="running")
        # 2. Run Fundamentalist
        res = fundamentalist_agent(state)
        state["valuation_report"] = res["valuation_report"]
        state["messages"].extend(res["messages"])
        
        st.session_state["current_state"] = state
        status.update(label="Simulation Complete!", state="complete")

# --- Sidebar ---
with st.sidebar:
    st.header("🔍 Analysis Setup")
    ticker = st.text_input("Ticker", "MSFT").upper()
    if st.button("Run Full Analysis"):
        run_analysis(ticker)
        
    st.markdown("---")
    
    if st.session_state["current_state"]:
        st.header("🎛️ Pro-Forma Drivers")
        
        # Get defaults
        metrics = st.session_state["base_state"]["financial_data"]["metrics"]
        base_growth = metrics.get("revenue_growth", 0.16)
        
        with st.form(key='simulation_form'):
            # 1. Growth
            growth_input = st.slider("Initial Revenue Growth (%)", 0.0, 0.50, base_growth, 0.005, format="%.1f%%")
            
            # Advanced Config (Sovereign)
            with st.expander("Advanced Configuration (Sovereign)"):
                st.caption("Override Global Assumptions (config/CONFIG.md)")
                
                # Load Defaults
                from aletheia.utils.config import load_config
                config_defaults = load_config()
                
                tax_rate_global = st.number_input("Global Tax Rate", 0.0, 0.50, float(config_defaults.tax_rate_global), 0.01)
                wacc_floor = st.number_input("WACC Floor", 0.0, 0.15, float(config_defaults.wacc_floor), 0.01)
                terminal_growth_cap = st.number_input("Terminal Growth Cap", 0.0, 0.10, float(config_defaults.terminal_growth_cap), 0.01)
                liquidity_threshold = st.number_input("Liquidity Ratio Safe", 0.0, 5.0, float(config_defaults.liquidity_ratio_safe), 0.1)
            
            # 2. CapEx
            capex_input = st.slider("CapEx % of Sales", 0.0, 0.50, 0.229, 0.005, format="%.1f%%")
            
            # 3. Capital Structure
            st.subheader("⚖️ Capital Structure")
            current_de = metrics.get("debt_to_equity", 0)
            if current_de > 10: current_de = current_de / 100.0
            
            target_de = st.slider("Target Debt/Equity Ratio", 0.0, 5.0, float(current_de), 0.1)
            
            # 4. WACC Control
            manual_wacc = st.checkbox("Manual WACC Override?")
            
            wacc_override = None
            if manual_wacc:
                wacc_input = st.slider("WACC (%)", 0.04, 0.15, 0.10, 0.001, format="%.1f%%")
                wacc_override = wacc_input
            else:
                # Show calculated WACC from current state (informative only inside form, static until recalc)
                calc_wacc = st.session_state["current_state"]["strategist_report"].get("wacc", 0.10)
                st.caption(f"Last Calculated WACC: {calc_wacc:.2%}")
            
            submit_btn = st.form_submit_button(label='🚀 Run Scenario Analysis')

        if submit_btn:
             # Trigger Recalc
            config: DCFConfig = {
                "revenue_growth_initial": growth_input,
                "capex_percent_sales": capex_input,
                "wacc_override": wacc_override,
                "target_debt_equity": target_de, 
                "growth_decay_rate": 0.02, 
                "target_ebit_margin": None,
                "reinvestment_rate": 0.0,
                # Sovereign Overrides
                "tax_rate_global": tax_rate_global,
                "wacc_floor": wacc_floor,
                "terminal_growth_cap": terminal_growth_cap,
                "liquidity_ratio_safe": liquidity_threshold
            }
            recalculate(config)

# --- Main Page ---
st.title("Aletheia Pro-Forma Engine")

if st.session_state["current_state"]:
    state = st.session_state["current_state"]
    val_report = state["valuation_report"]
    dcf_res = val_report.get("dcf_result", {})
    metrics = state["financial_data"]["metrics"]
    
    # 1. Contrarian Alert (Responsive)
    dcf_config = state.get("dcf_config") or {}
    if dcf_config.get("revenue_growth_initial", 0) > 0.25:
        contrarian_text = state["contrarian_report"].get("structured_analysis", {}).get("bear_case_summary", "")
        st.markdown(f"""
        <div class="warning-box">
            <h4>🐻 Contrarian Warning: Aggressive Growth Detected!</h4>
            <p>You are projecting growth > 25%. The Contrarian Agent notes:</p>
            <i>"{contrarian_text}"</i>
            <br><br>
            <b>Historical Context:</b> Reminiscent of the Dot-Com crash valuations where high growth was extrapolated indefinitely.
        </div>
        """, unsafe_allow_html=True)
    
    # NEW: Scenario Comparison Metrics
    st.subheader("📊 Scenario Analysis")
    col1, col2, col3 = st.columns(3)
    
    intrinsic_val = dcf_res.get("enterprise_value", 0)
    market_cap = metrics.get("market_cap", 1)
    
    # Calculate Delta
    delta = intrinsic_val - market_cap
    delta_pct = (delta / market_cap)
    
    with col1:
        st.metric("Market Cap (Current)", f"${market_cap/1e9:.2f}B")
        
    with col2:
        st.metric("Intrinstic Value (Simulated)", f"${intrinsic_val/1e9:.2f}B")
        
    with col3:
        st.metric("Upside / Downside", f"{delta_pct:.1%}", delta_color="normal")

    st.markdown("---")

    # --- 1. Economic Reality Engine (Business Understanding) ---
    with st.expander("1. Economic Reality Engine (Business Quality)", expanded=True):
        vc_report = state.get("value_chain_report", {})
        vc_data = vc_report.get("value_chain_report", {})
        
        col1, col2 = st.columns([1, 1])
        
        with col1:
            st.markdown("#### 🏰 Moat Diagnostics")
            # Radar Chart for Moat
            categories = ['Strategic Leverage', 'Substitution Risk (Inverse)', 'Pricing Power']
            
            # Normalize scores to 0-10
            # Substitution Risk: 10 is bad (easy substitute), so we invert for "Moat" score (11-score)
            sub_score_inv = 11 - vc_data.get("substitution_risk_score", 5)
            lev_score = vc_data.get("strategic_leverage_score", 5)
            
            # Pricing Power is text, let's infer a score or use a placeholder if not numeric
            # For prototype, we'll map "High" -> 8, "Moderate" -> 5, "Low" -> 2
            pp_text = vc_data.get("pricing_power_assessment", "Moderate")
            pp_score = 5
            if "high" in pp_text.lower() or "strong" in pp_text.lower(): pp_score = 8
            elif "low" in pp_text.lower() or "weak" in pp_text.lower(): pp_score = 2
            
            r_values = [lev_score, sub_score_inv, pp_score]
            
            fig_radar = go.Figure(data=go.Scatterpolar(
                r=r_values,
                theta=categories,
                fill='toself',
                name='Moat Score'
            ))
            fig_radar.update_layout(
                polar=dict(
                    radialaxis=dict(visible=True, range=[0, 10])
                ),
                showlegend=False,
                height=300,
                margin=dict(t=20, b=20, l=40, r=40)
            )
            st.plotly_chart(fig_radar, use_container_width=True)
            
            st.caption(f"**Strategic Leverage**: {lev_score}/10 (Higher is Better)")
            st.caption(f"**Barrier to Entry**: {sub_score_inv}/10 (Higher is Harder to Displace)")

        with col2:
            st.markdown("#### ⛓️ Value Chain Structure")
            
            # Power Ratio
            p_ratio = vc_data.get("power_ratio", 1.0)
            p_color = "red" if p_ratio > 1.5 else "green"
            st.markdown(f"**Upstream Power Ratio**: <span style='color:{p_color}'>**{p_ratio:.2f}x**</span>", unsafe_allow_html=True)
            st.caption("Ratio of Supplier Margins to Target Margins. >1.5x indicates value leakage.")
            
            st.info(f"**Bottleneck Analysis**: {vc_data.get('bottleneck_analysis', 'N/A')}")
            
            st.markdown("---")
            st.markdown(f"**Pricing Power**: {pp_text}")
            if vc_data.get("pass_through_capability"):
                st.success("✅ Pass-Through Capability Detected")
            else:
                st.warning("⚠️ Improvement Needed in Cost Pass-Through")

    # --- 2. Financial Translation Engine (Accounting -> Truth) ---
    with st.expander("2. Financial Translation Engine (Accounting → Cash)", expanded=False):
        val_report = state.get("valuation_report", {})
        dcf_res = val_report.get("dcf_result", {})
        adjustments = dcf_res.get("economic_adjustments", {})
        
        st.markdown("#### 🌊 GAAP to Economic Earnings Bridge")
        
        # Simple Waterfall Data Construction
        # Start: Revenue -> EBITDA (Rep) -> Adj EBITDA -> EBIT (Eco)
        
        # We need to grab raw numbers. For prototype, we'll use pro-forma year 1 or base.
        # Let's use the Base Year data from Fundamentalist if available, or reconstruct from ProForma Y1
        projections = dcf_res.get("projections", [])
        if projections:
            base_year = projections[0] # Y1
            
            rev = base_year.get("net_sales", 0)
            # Reconstruct "Reported" for visual bridge (Simulation)
            # Assuming Reported EBITDA was lower by R&D capitalization amount
            rnd_adj = adjustments.get("R&D Asset", 0) / 3 # Approx annual flow added back
            
            ebitda_eco = base_year.get("ebitda", 0)
            ebitda_rep = ebitda_eco - rnd_adj
            
            fig_waterfall = go.Figure(go.Waterfall(
                name = "Financial Translation",
                orientation = "v",
                measure = ["absolute", "relative", "relative", "total"],
                x = ["Reported EBITDA", "R&D Capitalization", "Lease/Other Adj", "Economic EBITDA"],
                textposition = "outside",
                text = [f"${ebitda_rep/1e9:.1f}B", f"+${rnd_adj/1e9:.1f}B", "+$0.0B", f"${ebitda_eco/1e9:.1f}B"],
                y = [ebitda_rep, rnd_adj, 0, ebitda_eco],
                connector = {"line":{"color":"rgb(63, 63, 63)"}},
            ))
            fig_waterfall.update_layout(title = "Core Economic Profit Translation", showlegend = False, height=300)
            st.plotly_chart(fig_waterfall, use_container_width=True)
            
        st.markdown("#### 📉 Incremental ROIC (I-ROIC) History")
        roic_check = dcf_res.get("roic_check", {})
        roic_analysis = roic_check.get("agnostic_roic_analysis", {})
        
        if isinstance(roic_analysis, dict) and "history" in roic_analysis:
            hist_df = pd.DataFrame(roic_analysis["history"])
            if not hist_df.empty:
                # Format
                hist_df = hist_df[["year", "delta_nopat", "delta_ic", "i_roic", "status"]]
                st.dataframe(
                    hist_df.style.format({
                        "delta_nopat": "${:,.0f}", 
                        "delta_ic": "${:,.0f}", 
                        "i_roic": "{:.1%}"
                    }).applymap(lambda x: "color: red" if isinstance(x, (int, float)) and x < 0.05 else "color: black", subset=["i_roic"]),
                    use_container_width=True
                )
                
                cycle_roic = roic_analysis.get("cycle_iroic", 0)
                benchmark = roic_analysis.get("sector_benchmark", 0)
                
                c1, c2 = st.columns(2)
                c1.metric("Cycle I-ROIC", f"{cycle_roic:.1%}", delta=f"{cycle_roic-benchmark:.1%} vs Benchmark")
                c2.metric("Reinvestment Rate", "N/A (Calc)") # Placeholder or calc if avail

    # --- 3. Capital Structure & Risk Engine ---
    with st.expander("3. Capital Structure & Risk Engine", expanded=False):
        strat_report = state.get("strategist_report", {})
        risk_res = strat_report.get("strategist_report", {}).get("analysis", {}).get("risk_engine_results", {})
        
        # 3.1 Maturity Wall
        st.markdown("#### 🧱 Maturity Wall & Solvency")
        mat_analysis = risk_res.get("maturity_analysis", {})
        
        m_col1, m_col2 = st.columns([2, 1])
        with m_col1:
            # Simple Bar chart for Debt vs Cash
            debt_2y = mat_analysis.get("maturities_next_2y", 0)
            cash = mat_analysis.get("cash", 0)
            
            fig_solvency = go.Figure(data=[
                go.Bar(name='Cash', x=['Liquidity'], y=[cash], marker_color='green'),
                go.Bar(name='Maturities (2Y)', x=['Liquidity'], y=[debt_2y], marker_color='red')
            ])
            fig_solvency.update_layout(barmode='group', title="Liquidity vs Near-Term Obligations", height=250)
            st.plotly_chart(fig_solvency, use_container_width=True)
            
        with m_col2:
            st.metric("Liquidity Ratio", f"{mat_analysis.get('liquidity_ratio', 0):.2f}x")
            if mat_analysis.get("liquidity_alert"):
                st.error("🚨 Liquidity Crunch Alert!")
            else:
                st.success("✅ Solvency Check Passed")

        # 3.2 Downside Analysis
        st.markdown("---")
        st.markdown("#### 🛡️ Downside Protection (Break-the-Company)")
        downside = risk_res.get("downside_audit", {})
        
        floor_price = downside.get("floor_price_per_share", 0)
        curr_price = metrics.get("current_price", 0) # Need to get this from metrics if available, or infer
        # Infer Current Price from Market Cap / Shares if not explicit
        # metrics has 'current_price' often from yfinance
        if not curr_price:
            curr_price = metrics.get("market_cap", 1) / (state["financial_data"]["statements"]["balance_sheet"].get("CommonStockSharesOutstanding", 1) or 1) # Rough fallback if needed, but metrics usually has it.
            
        d_col1, d_col2, d_col3 = st.columns(3)
        d_col1.metric("Floor Price (Tangible/Earnings)", f"${floor_price:.2f}")
        d_col2.metric("Current Price", f"${metrics.get('current_price', 'N/A')}")
        
        # Safety Margin
        if isinstance(metrics.get("current_price"), (int, float)):
             margin = (metrics["current_price"] - floor_price) / metrics["current_price"]
             d_col3.metric("Downside Risk", f"-{margin:.1%}", delta_color="inverse")

    # --- Summary & Comparison (Preserved from Manual Mode) ---
    st.markdown("---")
    st.subheader("📊 Scenario Analysis & Valuation Summary")
    
    # ... (Keep existing comparison metrics)
    col1, col2, col3 = st.columns(3)
    
    intrinsic_val = dcf_res.get("enterprise_value", 0)
    market_cap = metrics.get("market_cap", 1)
    
    # Calculate Delta
    delta = intrinsic_val - market_cap
    delta_pct = (delta / market_cap)
    
    with col1:
        st.metric("Market Cap (Current)", f"${market_cap/1e9:.2f}B")
        
    with col2:
        st.metric("Intrinstic Value (Simulated)", f"${intrinsic_val/1e9:.2f}B")
        
    with col3:
        st.metric("Upside / Downside", f"{delta_pct:.1%}")


else:
    st.info("👈 Enter a ticker and click 'Run Full Analysis' to start the Pro-Forma Engine.")
