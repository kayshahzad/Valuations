"""Application default data-source configuration.

Hybrid is the canonical primary source: FMP for the high-coverage flow
records (Revenue, OpCF, CapEx, EBITDA, etc.) plus XBRL companyfacts for
specialty tags that FMP doesn't expose (AssetImpairmentCharges,
GoodwillImpairmentLoss, IntangibleAssetImpairmentCharge,
RestructuringCharges). The XBRL enrichment lets the FMP adapter's
ex-impairment derivations use discrete tags instead of the kitchen-sink
``otherExpenses`` bucket — eliminates false-positive ex-impairment
numbers on filers where ``otherExpenses`` mixes charges with offsetting
credits (e.g., Macy's FY2025).

Override per-environment via the ``ALETHEIA_PROVIDER`` env var
(``fmp`` | ``xbrl`` | ``hybrid``), or per-session via the UI sidebar
selector (writes ``st.session_state["provider"]``).
"""

DEFAULT_PROVIDER: str = "hybrid"
