import sys
from pprint import pprint
from dotenv import load_dotenv

# Load Env (API Keys)
load_dotenv()

# Import Agents
from aletheia.agents.intake import intake_agent
from aletheia.agents.strategist import strategist_agent
from aletheia.agents.forensic import forensic_agent
from aletheia.agents.value_chain import value_chain_agent
from aletheia.agents.context import strategic_context_agent
from aletheia.agents.contrarian import contrarian_agent
from aletheia.agents.fundamentalist import fundamentalist_agent
from aletheia.agents.lead import lead_agent
from aletheia.utils.tracing import tracer

def run_pipeline(ticker="AAPL"):
    print(f"\n🚀 STARTING PIPELINE SPRINT FOR {ticker} 🚀\n")
    state = {"ticker": ticker, "dcf_config": {"target_debt_equity": 0.5}}
    
    # 1. Intake (The Base)
    out = intake_agent(state)
    state.update(out)
    if "serving_base" not in state:
        print("❌ Intake Failed!")
        return
        
    # 2. Parallel Specialists
    print("\n--- Running Specialists ---")
    
    out = strategist_agent(state)
    state.update(out)
    
    out = forensic_agent(state) 
    state.update(out)
    
    out = value_chain_agent(state)
    state.update(out)
    
    out = strategic_context_agent(state)
    state.update(out)
    
    out = contrarian_agent(state)
    state.update(out)
    
    # 3. Fundamentalist (Valuation)
    print("\n--- Running Valuation ---")
    out = fundamentalist_agent(state)
    state.update(out)
    
    # 4. Lead (Consolidation)
    print("\n--- Running Consolidation ---")
    out = lead_agent(state)
    state.update(out)
    
    ticker = state.get("ticker", "UNKNOWN")
    report_path = f"valuation_data/serving/latest/{ticker}_Executive_Report.html"
    print(f"\n✅ REPORT GENERATED: {report_path}")
    print("\n✅ PIPELINE COMPLETE")
    tracer.save_traces("full_pipeline_trace.json")
    return state

if __name__ == "__main__":
    if len(sys.argv) > 1 and not sys.argv[1].startswith("--"):
         # Usage: python3 run_valuation.py CNC
         target = sys.argv[1]
    elif len(sys.argv) > 2 and sys.argv[1] == "--ticker":
         target = sys.argv[2]
    else:
         target = "AAPL"
         
    run_pipeline(target.upper())
