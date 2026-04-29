import argparse
from aletheia.workflow.graph import create_workflow
from aletheia.utils.tracing import tracer
import os
import time

# Helper to format output
def print_report(final_report):
    print("\n" + "="*50)
    if hasattr(final_report, 'conviction_score'):
        # It's a Pydantic object
        print(f"    INVESTMENT THESIS (Structured)")
        print(f"    ---------------------------")
        print(f"    Conviction Score: {final_report.conviction_score}/10")
        print(f"    Margin of Safety: {final_report.margin_of_safety:.2f}%")
        print(f"    Growth Assessment: {final_report.growth_decay_assessment}")
        print(f"    Contrarian Rebuttal: {final_report.contrarian_rebuttal}")
    else:
        # Fallback string
        print(final_report)
    print("="*50 + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Aletheia-Intelligence: Multi-Agent Valuation")
    parser.add_argument("--ticker", type=str, required=True, help="Stock ticker symbol (e.g., AAPL)")
    args = parser.parse_args()
    
    ticker = args.ticker
    timestamp = int(time.time())
    
    # 1. Start Trace
    trace_id = f"trace_{ticker}_{timestamp}"
    tracer.start_trace(trace_id)
    
    print(f"Starting Valuation Sprint for {ticker}...")
    
    app = create_workflow()
    
    # Initial State
    initial_state = {
        "ticker": ticker,
        "messages": [],
        "financial_data": {},
        "valuation_report": {},
        "strategist_report": {},
        "contrarian_report": {},
        "final_report": ""
    }
    
    # Stream the graph execution
    final_state = app.invoke(initial_state)
    
    # 2. Print Result
    if "final_report" in final_state:
        print_report(final_state["final_report"])
    else:
        print("No final report generated.")
        
    # 3. Save Trace
    if not os.path.exists("audits"):
        os.makedirs("audits")
    trace_filename = f"audits/{trace_id}.json"
    tracer.save_traces(trace_filename)
    
    # 4. Export DCF Excel Model
    print(f"\nGenerating DCF Excel Model for {ticker}...")
    from aletheia.tools.dcf_excel_exporter import export_ticker
    export_ticker(ticker)

