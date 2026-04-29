"""
industry_routing.py

Maps tickers or SIC codes to specific industry domains.
This drives industry-specific fallback paths in tag_mappings.py and validation_rules.py.
"""

TICKER_TO_INDUSTRY = {
    "UNH": "healthcare",
    "CNC": "healthcare",
    "JPM": "bank",
    "NEE": "utility"
}

def get_industry(ticker: str) -> str:
    """Returns the industry classification for a given ticker."""
    return TICKER_TO_INDUSTRY.get(ticker.upper(), "default")
