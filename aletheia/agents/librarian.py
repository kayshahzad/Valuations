from langchain_core.messages import HumanMessage
from edgar import set_identity, Company
from config import SEC_IDENTITY
from aletheia.utils.tracing import tracer

def librarian_agent(state):
    print("---LIBRARIAN AGENT (Live SEC Data)---")
    ticker = state["ticker"]
    
    # Set Identity for SEC
    set_identity(SEC_IDENTITY)
    
    try:
        # Fetch 10-K Content (edgartools)
        raw_text = "Data unavailable."
        try:
            company = Company(ticker)
            filings = company.get_filings(form="10-K")
            if filings:
                latest_10k = filings[0]
                
                try:
                    doc    = latest_10k.obj()
                    item1  = str(doc['Item 1'])[:40000]  if 'Item 1'  in doc else ""
                    item1a = str(doc['Item 1A'])[:40000] if 'Item 1A' in doc else ""
                    raw_text = f"ITEM 1: BUSINESS\n{item1}\n\nITEM 1A: RISK FACTORS\n{item1a}"
                except Exception:
                    # Fallback to full markdown truncation if item extraction fails
                    raw_text = (latest_10k.markdown() or "")[:80000]
                
        except Exception as sec_e:
            print(f"SEC Error: {sec_e}")
            raw_text = f"SEC Fetch Failed: {sec_e}"

        output = {
            "raw_10k_text": raw_text,
            "messages": [HumanMessage(content=f"Librarian: Retrieved 10-K text for {ticker}.")]
        }
        tracer.log_step("Librarian", state, output)
        return output
    except Exception as e:
        error_output = {
            "messages": [HumanMessage(content=f"Librarian: Failed to retrieve data for {ticker}. Error: {e}")]
        }
        tracer.log_step("Librarian", state, error_output)
        return error_output
