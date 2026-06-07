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

    # ── Mega-cap tech / internet ────────────────────────────────────────
    "MSFT":  ["GOOGL", "AMZN", "ORCL", "AAPL", "CRM"],
    "AAPL":  ["MSFT", "GOOGL", "AMZN", "META", "SONY"],
    "GOOGL": ["META", "MSFT", "AMZN", "AAPL", "NFLX"],
    "META":  ["GOOGL", "SNAP", "PINS", "AMZN", "MSFT"],
    "AMZN":  ["MSFT", "GOOGL", "WMT", "AAPL", "BABA"],
    # Enterprise software.
    "ORCL":  ["MSFT", "CRM", "SAP", "IBM", "NOW"],
    # IT services / consulting.
    "ACN":   ["IBM", "INFY", "CTSH", "WIT", "DXC"],

    # ── Semiconductors & equipment ──────────────────────────────────────
    "NVDA":  ["AVGO", "AMD", "QCOM", "TSM", "TXN"],
    "AMD":   ["NVDA", "INTC", "QCOM", "AVGO", "TXN"],
    "QCOM":  ["AVGO", "TXN", "NXPI", "MRVL", "MU"],
    "TXN":   ["ADI", "NXPI", "MCHP", "AVGO", "ON"],
    "MU":    ["WDC", "STX", "AVGO", "QCOM", "TXN"],
    "TSM":   ["INTC", "GFS", "UMC", "ASML", "AVGO"],
    "ASML":  ["AMAT", "LRCX", "KLAC", "TER", "KLIC"],
    "CRDO":  ["MRVL", "AVGO", "COHR", "LSCC", "ANET"],
    "APH":   ["TEL", "GLW", "JBL", "CLS", "FLEX"],
    # Hardware / EMS / servers.
    "SMCI":  ["DELL", "HPE", "NTAP", "ANET", "CLS"],
    "HPE":   ["DELL", "IBM", "CSCO", "NTAP", "SMCI"],
    "CLS":   ["JBL", "FLEX", "SANM", "BHE", "APH"],

    # ── Healthcare / pharma / devices ───────────────────────────────────
    "LLY":   ["NVO", "MRK", "ABBV", "PFE", "AZN"],
    "NVO":   ["LLY", "MRK", "ABBV", "PFE", "AZN"],
    "MRK":   ["PFE", "ABBV", "LLY", "BMY", "JNJ"],
    "JNJ":   ["PFE", "MRK", "ABBV", "LLY", "ABT"],
    "ABT":   ["MDT", "SYK", "BDX", "BSX", "JNJ"],
    "MDT":   ["SYK", "BSX", "ABT", "BDX", "ZBH"],
    # Managed care.
    "UNH":   ["ELV", "HUM", "CI", "CVS", "CNC"],
    "CNC":   ["UNH", "ELV", "HUM", "CVS", "MOH"],

    # ── Financials / payments / data ────────────────────────────────────
    "JPM":   ["BAC", "C", "WFC", "GS", "MS"],
    "AXP":   ["V", "MA", "DFS", "COF", "JPM"],
    "V":     ["MA", "AXP", "PYPL", "FI", "GPN"],
    "MCO":   ["SPGI", "FDS", "MSCI", "ICE", "FICO"],
    "BRK-B": ["MKL", "L", "CB", "TRV", "PGR"],

    # ── Industrials / machinery / rails ─────────────────────────────────
    "CAT":   ["DE", "CMI", "PCAR", "CNHI", "EMR"],
    "EMR":   ["ITW", "ROK", "ETN", "HON", "PH"],
    "ITW":   ["EMR", "ROK", "DOV", "PH", "ETN"],
    "UNP":   ["NSC", "CSX", "CNI", "CP"],
    "NSC":   ["UNP", "CSX", "CP", "CNI"],
    "DAL":   ["UAL", "AAL", "LUV", "ALK"],

    # ── Consumer staples / retail / beverages ───────────────────────────
    "PG":    ["KMB", "CL", "UL", "CHD", "CLX"],
    "KO":    ["PEP", "KDP", "MNST", "CELH", "MDLZ"],
    "PEP":   ["KO", "KDP", "MDLZ", "MNST", "GIS"],
    "CELH":  ["MNST", "KDP", "KO", "PEP", "BRBR"],
    "COST":  ["WMT", "TGT", "BJ", "KR", "DG"],
    "WMT":   ["COST", "TGT", "KR", "AMZN", "DG"],
    "HD":    ["LOW", "TSCO", "FND", "WMT", "TGT"],
    "LOW":   ["HD", "TSCO", "FND", "WSM", "TGT"],
    "CHWY":  ["AMZN", "W", "ETSY", "TGT", "WMT"],

    # ── Energy / utilities ──────────────────────────────────────────────
    "ET":    ["KMI", "WMB", "OKE", "EPD", "MPLX"],
    "NEE":   ["DUK", "SO", "D", "AEP", "EXC"],

    # ── Autos / EV ──────────────────────────────────────────────────────
    "TSLA":  ["GM", "F", "RIVN", "LCID", "TM"],
}


def curated_peers(ticker: str) -> List[str]:
    """Curated peer tickers for a name, or [] when none is curated."""
    return list(PEER_LISTS.get((ticker or "").upper(), []))


__all__ = ["PEER_LISTS", "curated_peers"]
