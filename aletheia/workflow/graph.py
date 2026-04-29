from langgraph.graph import StateGraph, END
from aletheia.state import AgentState
from aletheia.agents.librarian import librarian_agent
from aletheia.agents.fundamentalist import fundamentalist_agent
from aletheia.agents.valuation_node import valuation_node
from aletheia.agents.strategist import strategist_agent
from aletheia.agents.contrarian_v2 import contrarian_agent
from aletheia.agents.forensic import forensic_agent
from aletheia.agents.value_chain import value_chain_agent
from aletheia.agents.context import strategic_context_agent
from aletheia.agents.lead import lead_agent

def create_workflow():
    workflow = StateGraph(AgentState)
    
    # Add Nodes
    workflow.add_node("librarian", librarian_agent)
    workflow.add_node("forensic", forensic_agent)
    workflow.add_node("value_chain", value_chain_agent)
    workflow.add_node("context", strategic_context_agent)
    workflow.add_node("strategist", strategist_agent)
    workflow.add_node("fundamentalist", fundamentalist_agent)
    workflow.add_node("valuation_node", valuation_node)
    workflow.add_node("contrarian", contrarian_agent)
    workflow.add_node("lead", lead_agent)
    
    # Set Entry Point
    workflow.set_entry_point("librarian")
    
    # Define Edges (Sequential for dependencies)
    # Librarian -> Forensic -> ValueChain -> Context -> Strategist -> Fundamentalist -> Valuation Node -> Contrarian -> Lead
    
    workflow.add_edge("librarian", "forensic")
    workflow.add_edge("forensic", "value_chain")
    workflow.add_edge("value_chain", "context")
    workflow.add_edge("context", "strategist")
    workflow.add_edge("strategist", "fundamentalist")
    workflow.add_edge("fundamentalist", "valuation_node")
    workflow.add_edge("valuation_node", "contrarian")
    workflow.add_edge("contrarian", "lead")
    
    workflow.add_edge("lead", END)
    
    return workflow.compile()
