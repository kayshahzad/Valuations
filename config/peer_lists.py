"""Curated peer lists for bottom-up analysis (memo §4 / refinement P1).

The raw FMP sector is too coarse and our ingested universe is small, so a name's
true peers are often absent (LDOS's peers BAH/SAIC/CACI aren't ingested). A
curated peer list lets ``peer_stats`` pull each peer's revenue CAGR / EV/EBITDA /
margin directly from FMP (independent of our universe) — fixing market-vs-share,
the sector-relative multiple, and peer-margin context in one place.

Curated only for names where the FMP sector mislabels the peer set. Everything
else falls back to same-peer-group universe members (``peers_for``).
"""

from __future__ import annotations

from typing import Dict, List

PEER_LISTS: Dict[str, List[str]] = {
    # Government / defense IT services (filed under Technology — wrong peers).
    "LDOS": ["BAH", "SAIC", "CACI", "LHX", "GD"],
    "SAIC": ["LDOS", "BAH", "CACI", "GD", "ACN"],
    "CACI": ["SAIC", "LDOS", "BAH", "LHX"],
    "BAH":  ["SAIC", "LDOS", "CACI", "ACN"],
    # Defense primes.
    "LMT":  ["RTX", "NOC", "GD", "LHX", "BA"],
    "NOC":  ["LMT", "RTX", "GD", "LHX"],
    "GD":   ["LMT", "RTX", "NOC", "LHX"],
    "RTX":  ["LMT", "NOC", "GD", "HON"],
}


def curated_peers(ticker: str) -> List[str]:
    """Curated peer tickers for a name, or [] when none is curated."""
    return list(PEER_LISTS.get((ticker or "").upper(), []))


__all__ = ["PEER_LISTS", "curated_peers"]
