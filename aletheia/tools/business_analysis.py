"""Bottom-up business analysis (memo §4 expansion) — Phase 0.

The deterministic / reuse half of the bottom-up layer:

  - Growth-source decomposition (organic vs M&A) — surfaces the split the DCF
    engine already computes internally (``_organic_cagr_ex_breaks``) but only
    prints to the console. Market-vs-share is deferred (needs a market-growth
    reference; Phase 5).
  - A coverage map of the 12 bottom-up dimensions: which are populated today
    (from existing extracted fields / qualitative dims) vs pending the Phase 2-3
    LLM extraction. Lets the memo show the bottom-up scaffold honestly.

No LLM call. The richer per-field content (TAM $, named contracts, R&D pipeline,
CAC/LTV) is added in later phases via structured extraction.
"""

from __future__ import annotations

from typing import Any, Dict, Optional


def _raw_cagr_from_revs(revs: list) -> Optional[float]:
    """Point-to-point raw CAGR over the last ≤5 fiscal years."""
    if len(revs) < 4:
        return None
    k = min(5, len(revs) - 1)
    base = revs[-1 - k]
    return (revs[-1] / base) ** (1.0 / k) - 1.0 if base > 0 else None


# Per-process cache of peer-group-median historical CAGR (from the universe DB)
# — keyed by peer group so it's derived once per group per process.
_SECTOR_GROWTH_CACHE: Dict[str, Optional[float]] = {}


def _sector_market_growth(peer_group: Optional[str], exclude_ticker: str) -> Optional[float]:
    """Market-growth reference = median historical revenue CAGR of same-PEER-GROUP
    universe peers (our own DB). Using the normalized peer group (not the raw
    FMP sector) means e.g. a defense/govt-IT name is compared to its real peers,
    not to high-growth semis. Cached per group; None when <3 peers."""
    if not peer_group:
        return None
    if peer_group in _SECTOR_GROWTH_CACHE:
        return _SECTOR_GROWTH_CACHE[peer_group]
    median = None
    try:
        import statistics
        from config.ticker_classification import get_extended_universe
        from config.business_analysis_templates import peer_group_for
        from aletheia.data.database import InvestmentDatabase
        peers = [t for t, c in get_extended_universe().items()
                 if t != exclude_ticker.upper()
                 and peer_group_for(t, getattr(c, "sector", "") or "",
                                    getattr(c, "industry", "") or "",
                                    getattr(c, "lifecycle", "") or "",
                                    getattr(c, "business_model", "") or "") == peer_group]
        cagrs = []
        if peers:
            db = InvestmentDatabase(verbose=False)
            try:
                for p in peers:
                    try:
                        df = db.get_latest(p)
                        if df is None or "clean_Revenue" not in getattr(df, "columns", []):
                            continue
                        d = df
                        if "period" in d.columns:
                            d = d[d["period"] == "FY"]
                        d = d.dropna(subset=["clean_Revenue"]).sort_values("fiscal_year")
                        revs = [float(x) for x in d["clean_Revenue"].tolist()]
                        c = _raw_cagr_from_revs(revs)
                        if c is not None and -0.5 < c < 2.0:
                            cagrs.append(c)
                    except Exception:
                        continue
            finally:
                db.close()
        if len(cagrs) >= 3:
            median = statistics.median(cagrs)
    except Exception:
        median = None
    _SECTOR_GROWTH_CACHE[peer_group] = median
    return median


# Per-process cache of FMP-backed peer stats, keyed by ticker.
_PEER_STATS_CACHE: Dict[str, Dict[str, Any]] = {}


def _peers_for(ticker: str, peer_group: Optional[str]) -> list:
    """Peer tickers for a name: curated list first (true peers, even if not in
    our universe), else same-peer-group universe members."""
    try:
        from config.peer_lists import curated_peers
        cur = curated_peers(ticker)
        if cur:
            return cur
    except Exception:
        pass
    if not peer_group:
        return []
    try:
        from config.ticker_classification import get_extended_universe
        from config.business_analysis_templates import peer_group_for
        return [t for t, c in get_extended_universe().items()
                if t != (ticker or "").upper()
                and peer_group_for(t, getattr(c, "sector", "") or "",
                                   getattr(c, "industry", "") or "",
                                   getattr(c, "lifecycle", "") or "",
                                   getattr(c, "business_model", "") or "") == peer_group]
    except Exception:
        return []


def peer_stats(ticker: str, peer_group: Optional[str] = None) -> Dict[str, Any]:
    """FMP-backed peer-set statistics: median revenue CAGR (market-growth proxy),
    median EV/EBITDA (sector-relative multiple), and median operating margin
    (peer-margin context) across the curated/group peer set. Cached per ticker;
    available=False when <3 peers resolve. Deterministic, FMP cache-backed."""
    key = (ticker or "").upper()
    if key in _PEER_STATS_CACHE:
        return _PEER_STATS_CACHE[key]
    out: Dict[str, Any] = {"available": False, "peers": []}
    try:
        import statistics
        from aletheia.data import fmp_client
        peers = _peers_for(key, peer_group)
        cagrs, mults, margins, used = [], [], [], []
        for p in peers[:8]:
            try:
                inc = fmp_client.fetch_income_statements(p) or []
                revs = [float(r.get("revenue")) for r in inc
                        if isinstance(r.get("revenue"), (int, float))]
                # FMP income statements are most-recent first.
                if len(revs) >= 4:
                    k = min(5, len(revs) - 1)
                    if revs[k] > 0:
                        c = (revs[0] / revs[k]) ** (1.0 / k) - 1.0
                        if -0.5 < c < 2.0:
                            cagrs.append(c)
                if inc and inc[0].get("revenue") and inc[0].get("operatingIncome") is not None:
                    m = float(inc[0]["operatingIncome"]) / float(inc[0]["revenue"])
                    if -0.5 < m < 0.8:
                        margins.append(m)
                km = fmp_client.fetch_key_metrics(p) or []
                ev = km[0].get("evToEBITDA") if km else None
                if isinstance(ev, (int, float)) and 0 < ev < 200:
                    mults.append(float(ev))
                used.append(p)
            except Exception:
                continue
        out = {
            "available": len(used) >= 3,
            "peers": peers,
            "peers_used": used,
            "market_growth_median": statistics.median(cagrs) if len(cagrs) >= 3 else None,
            "ev_ebitda_median": statistics.median(mults) if len(mults) >= 3 else None,
            "op_margin_median": statistics.median(margins) if len(margins) >= 3 else None,
            "source": "curated/group peer set via FMP (cached)",
        }
    except Exception:
        out = {"available": False, "peers": []}
    _PEER_STATS_CACHE[key] = out
    return out


def build_growth_decomposition(calc) -> Dict[str, Any]:
    """Decompose historical revenue growth into organic vs M&A/regime-break.

    Reuses ``dcf_engine._organic_cagr_ex_breaks``. Returns ``available=False``
    when there's too little history. ``ma_contribution_pp`` is the percentage
    points of raw CAGR attributable to break years (raw − organic)."""
    out: Dict[str, Any] = {"available": False}
    try:
        from aletheia.tools.dcf_engine import _organic_cagr_ex_breaks
        df = getattr(calc, "df", None)
        if df is None or "clean_Revenue" not in getattr(df, "columns", []):
            return out
        d = df
        if "period" in d.columns:
            d = d[d["period"] == "FY"]
        d = d.dropna(subset=["clean_Revenue"]).sort_values("fiscal_year")
        revs = [float(x) for x in d["clean_Revenue"].tolist()]
        if len(revs) < 4:
            return out
        # Raw point-to-point CAGR over the last ≤5 fiscal years.
        k = min(5, len(revs) - 1)
        base = revs[-1 - k]
        raw_cagr = (revs[-1] / base) ** (1.0 / k) - 1.0 if base > 0 else None

        organic, break_years = _organic_cagr_ex_breaks(d)
        if organic is not None and break_years:
            ma_pp = (raw_cagr - organic) if raw_cagr is not None else None
            split = "organic + M&A/regime breaks"
        else:
            organic = raw_cagr  # no break → all organic
            ma_pp = 0.0
            break_years = []
            split = "all organic (no transformative break detected)"
        # Market-vs-share split (Phase 5): organic growth above the peer-group
        # market-growth reference is share gain; below is share loss / lagging.
        cls = getattr(calc, "classification", None)
        ticker = getattr(cls, "ticker", "") or ""
        try:
            from config.business_analysis_templates import peer_group_for
            pg = peer_group_for(ticker, getattr(cls, "sector", "") or "",
                                getattr(cls, "industry", "") or "",
                                getattr(cls, "lifecycle", "") or "",
                                getattr(cls, "business_model", "") or "")
        except Exception:
            pg = getattr(cls, "sector", None)
        # Prefer the curated/FMP peer-set median (true peers, e.g. BAH/SAIC/CACI
        # for LDOS); fall back to same-peer-group universe members.
        ps = peer_stats(ticker, pg) if ticker else {"available": False}
        market_ref = ps.get("market_growth_median")
        ref_src = "curated peer set (FMP)"
        if market_ref is None:
            market_ref = _sector_market_growth(pg, ticker)
            ref_src = "same-peer-group universe-median"
        share_gain = None
        share_label = None
        if isinstance(organic, (int, float)) and isinstance(market_ref, (int, float)):
            share_gain = organic - market_ref
            share_label = ("gaining share" if share_gain > 0.01
                           else "losing share / lagging market" if share_gain < -0.01
                           else "tracking the market")
        out.update({
            "available": raw_cagr is not None,
            "raw_cagr": raw_cagr,
            "organic_cagr": organic,
            "ma_contribution_pp": ma_pp,
            "break_years": break_years,
            "lookback_years": k,
            "split": split,
            "market_growth_ref": market_ref,
            "share_gain_pp": share_gain,
            "share_label": share_label,
            "market_ref_basis": (f"{ref_src} historical CAGR ({pg})"
                                 if market_ref is not None else None),
            "source": "DCF organic/M&A break detection + sector market-growth proxy",
        })
    except Exception:
        return {"available": False}
    return out


# The 12 bottom-up dimensions, with how each is sourced today.
_COVERAGE = [
    ("A. What it sells", "Product / service portfolio", "business_model.revenue_segments"),
    ("A. What it sells", "Major customers / contracts", "business_model.key_customers"),
    ("A. What it sells", "Distribution channels", None),
    ("B. Market size", "TAM sizing", None),
    ("B. Market size", "Market share / position", "dim:market_position"),
    ("B. Market size", "Whitespace / adjacent TAMs", None),
    ("C. Unit economics", "Operating leverage", "business_model.operating_leverage_analysis"),
    ("C. Unit economics", "CAC / LTV / cohorts", None),
    ("C. Unit economics", "Margin trajectory by segment", None),
    ("D. Growth source", "Organic vs M&A", "growth_decomposition"),
    ("D. Growth source", "Market vs share", None),
    ("E. Innovation", "Disruption / R&D posture", "dim:technology_disruption_risk"),
    ("E. Innovation", "Acquisition strategy", "dim:capital_allocation_track_record"),
    ("E. Innovation", "New product launches", None),
    ("F. Industry", "Lifecycle stage", "classification.lifecycle"),
    ("F. Industry", "Competitive intensity", "dim:industry_concentration"),
    ("F. Industry", "Regulatory trajectory", "dim:regulatory_exposure"),
]


# Coverage dimensions the LLM extraction (themes A+B+C+E) can satisfy.
_AB_FIELDS = {
    # A + B (Phase 2)
    "Product / service portfolio": "product_lines",
    "Major customers / contracts": "major_customers",
    "Distribution channels": "distribution_channels",
    "TAM sizing": "tam_estimate",
    "Market share / position": "market_share",
    "Whitespace / adjacent TAMs": "whitespace_runway",
    # C + E (Phase 3)
    "Operating leverage": "operating_leverage",
    "CAC / LTV / cohorts": "cac_ltv",
    "Margin trajectory by segment": "segment_margin_trajectory",
    "New product launches": "new_product_launches",
}


def _present(source: Optional[str], bm: Dict[str, Any], dims: Dict[str, Any],
             gd: Dict[str, Any], lifecycle: Optional[str],
             ab: Dict[str, Any], dimension: str = "") -> bool:
    # Phase-2 extraction fills several A/B dimensions directly.
    ab_field = _AB_FIELDS.get(dimension)
    if ab_field and ab.get(ab_field):
        return True
    if dimension == "Market vs share":
        return gd.get("share_gain_pp") is not None
    if not source:
        return False
    if source == "growth_decomposition":
        return bool(gd.get("available"))
    if source == "classification.lifecycle":
        return bool(lifecycle)
    if source.startswith("dim:"):
        return source.split(":", 1)[1] in dims
    if source.startswith("business_model."):
        key = source.split(".", 1)[1]
        v = bm.get(key)
        return bool(v)
    return False


def build_business_analysis(report: Optional[Dict[str, Any]], ticker: str,
                            calc=None) -> Dict[str, Any]:
    """Assemble the bottom-up block: growth decomposition + a coverage map of the
    12 dimensions (present today vs pending LLM extraction)."""
    out: Dict[str, Any] = {"available": False}
    bm = (((report or {}).get("1_economic_reality") or {}).get("business_model") or {})
    lifecycle = getattr(getattr(calc, "classification", None), "lifecycle", None)

    dims: Dict[str, Any] = {}
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            dims = db.get_all_assessments_for_ticker(ticker) or {}
        finally:
            db.close()
    except Exception:
        dims = {}

    gd = build_growth_decomposition(calc) if calc is not None else {"available": False}

    # Phase-2 LLM extraction (themes A+B), read from disk cache (no LLM here).
    ab: Dict[str, Any] = {}
    try:
        from aletheia.agents.business_extraction import cached_business_ab
        ab = cached_business_ab(ticker) or {}
    except Exception:
        ab = {}

    # Sector-specific emphasis template (Phase 4) — which dimensions matter most.
    cls = getattr(calc, "classification", None)
    template: Dict[str, Any] = {}
    try:
        from config.business_analysis_templates import template_for
        template = template_for(
            ticker,
            getattr(cls, "sector", "") or "",
            getattr(cls, "industry", "") or "",
            lifecycle or "",
            getattr(cls, "business_model", "") or "")
    except Exception:
        template = {}
    priority = set(template.get("priority_dimensions") or [])

    coverage = []
    n_present = 0
    for theme, dimension, source in _COVERAGE:
        present = _present(source, bm, dims, gd, lifecycle, ab, dimension)
        n_present += int(present)
        coverage.append({
            "theme": theme, "dimension": dimension,
            "status": "present" if present else "pending",
            "priority": dimension in priority,   # sector-emphasized
            "source": ("business_ab extraction" if _AB_FIELDS.get(dimension) and ab.get(_AB_FIELDS[dimension])
                       else source or "needs extraction"),
        })
    # Sort priority dimensions to the top so the sector's key items lead.
    coverage.sort(key=lambda c: (not c["priority"]))

    # Peer-set stats (curated/FMP) — market growth, multiple, margin context.
    pg_key = (template.get("peer_group") if template else None)
    ps = peer_stats(ticker, pg_key)

    out.update({
        "available": True,
        "growth_decomposition": gd,
        "peer_stats": ps,
        "extracted": ab or None,          # themes A+B+C+E (LLM)
        "coverage": coverage,
        "n_present": n_present,
        "n_total": len(coverage),
        "lifecycle": lifecycle,
        "sector_template": {
            "key": template.get("key"),
            "label": template.get("label"),
            "peer_group": template.get("peer_group"),
            "emphasis": template.get("emphasis") or [],
        } if template else None,
    })
    return out


def compose_business_analysis(ticker: str,
                              report: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """One-call composer for ticker-only surfaces (report rebuild). No LLM."""
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        calc = make_calc_input(ticker)
        return build_business_analysis(report, ticker, calc=calc)
    except Exception:
        return None


__all__ = ["build_growth_decomposition", "build_business_analysis",
           "compose_business_analysis"]
