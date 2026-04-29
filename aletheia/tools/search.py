from langchain_community.tools import DuckDuckGoSearchRun
from langchain_community.utilities import DuckDuckGoSearchAPIWrapper

def search_sentiment(query: str) -> str:
    """
    Searches for news/sentiment regarding a query.
    """
    wrapper = DuckDuckGoSearchAPIWrapper(max_results=5)
    search = DuckDuckGoSearchRun(api_wrapper=wrapper)
    
    try:
        results = search.invoke(query)
        return results
    except Exception as e:
        return f"Error during search: {e}"
