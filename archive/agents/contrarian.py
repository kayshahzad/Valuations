import os
from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field
from langchain_community.tools import DuckDuckGoSearchRun
from config import MODEL_NAME, TEMPERATURE
from aletheia.utils.tracing import tracer

class ContrarianOutput(BaseModel):
    bias_detected: str = Field(description="Type of market bias (e.g., 'Herding', 'Overconfidence')")
    bear_case_summary: str = Field(description="Detailed summary of the negative sentiment and risks found in search results")
    sentiment_score: int = Field(description="Score from -10 (Extremely Negative) to +10 (Extremely Positive)")

def contrarian_agent(state):
    print("---CONTRARIAN AGENT (Gemini 2.0 + Search)---")
    ticker = state["ticker"]
    
    # 1. Search Live
    search = DuckDuckGoSearchRun()
    query = f"{ticker} stock bear case risks negative news analysis"
    try:
        raw_web_results = search.invoke(query)
    except Exception as e:
        raw_web_results = f"Search failed: {e}"
        
    # 2. Analyze with Gemini
    if not os.environ.get("GOOGLE_API_KEY"):
         return {
            "contrarian_report": {
                "raw_results": raw_web_results,
                "structured_analysis": {
                    "bias_detected": "Mock Bias (No API Key)",
                    "bear_case_summary": "Mock summary: Key missing.",
                    "sentiment_score": 0
                }
            },
            "messages": [HumanMessage(content="Contrarian: Generated (MOCK) analysis.")]
        }

    llm = ChatGoogleGenerativeAI(
        model=MODEL_NAME,
        temperature=TEMPERATURE
    )
    structured_llm = llm.with_structured_output(ContrarianOutput)
    
    prompt = ChatPromptTemplate.from_template("""
    You are The Contrarian. Your goal is to synthesize a "Dissenting Sentiment Summary".
    
    Read the following search results about {ticker} and IDENTIFY the strongest Bear Case arguments.
    Ignore generic noise. Focus on:
    - Regulatory risks
    - Competition
    - Valuation concerns
    - Executive issues
    
    SEARCH SNIPPETS:
    {results}
    
    Return the analysis in structured JSON.
    """)
    
    chain = prompt | structured_llm
    
    try:
        report: ContrarianOutput = chain.invoke({
            "ticker": ticker,
            "results": raw_web_results
        })
        
        output = {
            "contrarian_report": {
                "raw_results": raw_web_results,
                "structured_analysis": report.dict()
            },
            "messages": [HumanMessage(content="Contrarian: Generated live sentiment summary.")]
        }
        tracer.log_step("Contrarian", state, output)
        return output
    except Exception as e:
        error_output = {
            "contrarian_report": {
                "raw_results": raw_web_results,
                "error": str(e)
            },
            "messages": [HumanMessage(content=f"Contrarian: Failed to analyze. Error: {e}")]
        }
        tracer.log_step("Contrarian", state, error_output)
        return error_output
