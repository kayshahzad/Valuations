"""
config/industry_margins.py

Hand-curated industry-median EBIT margin reference table. Used by the
`margin_reverts_to_industry_median` scenario to construct a mean-reversion
override.

This is a static snapshot, not a live-computed peer median. Limitations:
  - Margins drift over time; refresh annually
  - Some "industry" labels in TickerClassification are coarse
  - Peer sets are illustrative; real margin medians require ingesting peer
    company financials and computing trimmed means (deferred to Phase 6+)

When a ticker's industry isn't in the table, the scenario falls back to
its lifecycle's typical margin band.

Sources of the values: 5-year median EBIT margins from public 10-K data
across the named peers, computed manually 2026-Q1 from Stockanalysis.com
and SEC filings. Numbers are decimal (0.30 = 30%).
"""

# Industry → (median EBIT margin, peer set used for the median)
# Peer set is documentation only; not used in computation.
INDUSTRY_MEDIAN_EBIT_MARGIN = {
    # Technology
    "Software":           (0.32, ("MSFT", "ORCL", "ADBE", "CRM", "INTU")),
    "Internet":           (0.25, ("GOOGL", "META", "NFLX", "BKNG")),
    "Hardware":           (0.20, ("AAPL", "DELL", "HPQ", "LOGI")),
    "E-Commerce/Cloud":   (0.08, ("AMZN", "EBAY", "MELI")),

    # Semiconductors
    "Semiconductors":             (0.25, ("NVDA", "AMD", "QCOM", "TXN", "AVGO", "INTC")),
    "Semiconductor Equipment":    (0.30, ("ASML", "AMAT", "LRCX", "KLAC")),

    # Healthcare
    "Pharmaceuticals":   (0.28, ("LLY", "MRK", "PFE", "BMY", "AZN", "NVS", "JNJ")),
    "Medical Devices":   (0.20, ("ABT", "MDT", "SYK", "ISRG", "BSX")),
    "Managed Care":      (None, None),  # DDM-required; margin scenario doesn't apply
    "Healthcare Plans":  (None, None),

    # Consumer
    "Retail":            (0.04, ("COST", "WMT", "TGT", "KR", "DG")),
    "Automotive":        (0.08, ("TSLA", "TM", "GM", "F", "STLA")),

    # Financials
    "Payments":          (0.55, ("V", "MA", "AXP")),  # toll-bridge economics
    "Banks":             (None, None),  # routing_required
    "Diversified":       (None, None),

    # Industrials / Utilities
    "Heavy Machinery":   (0.15, ("CAT", "DE", "PCAR", "URI")),
    "Utilities":         (None, None),  # routing_required
}


def get_industry_median_margin(industry: str):
    """
    Look up median EBIT margin for an industry. Returns the float or None
    if industry is missing or marked as DDM/routing-required.
    """
    entry = INDUSTRY_MEDIAN_EBIT_MARGIN.get(industry)
    if entry is None:
        return None
    margin, _peers = entry
    return margin
