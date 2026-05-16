"""Per-ticker data-provider pins.

Some filers are empirically known to produce wrong results through one
provider but not the other. Pinning them overrides the sidebar
selector — the UI status chip surfaces the override + reason so the
analyst sees why the global setting doesn't apply.

Each entry: ``ticker -> (provider, reason)``. Provider must be one of
``"fmp" | "xbrl" | "hybrid"`` (the same names the registry resolves).

Format kept narrow on purpose. If a ticker needs richer routing logic
(e.g., per-fiscal-year pins or per-engine overrides), extend the data
structure rather than overloading the reason string.
"""

from __future__ import annotations

from typing import Dict, Optional, Tuple


PROVIDER_PINS: Dict[str, Tuple[str, str]] = {
    # NEE — NextEra Energy. Regulated-utility consolidation puts
    # subsidiary equity (preferred stock, regulatory liabilities,
    # trust-preferred securities) outside the parent's TotalEquity
    # tag. FMP's TotalEquity systematically misses these, causing the
    # A=L+E identity to drift ~33% every year. XBRL companyfacts
    # provides the missing components.
    # Empirically validated: 10/21 universe BS residual cases pre-fix.
    # "NEE": ("xbrl", "regulated utility — FMP TotalEquity misses preferreds"),
}


def get_pin(ticker: str) -> Optional[Tuple[str, str]]:
    """Return ``(provider, reason)`` if ticker is pinned, else None."""
    return PROVIDER_PINS.get(ticker.upper())
