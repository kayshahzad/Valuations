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

import re
from typing import Any, Dict, List, Optional


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


_SEGMENT_CACHE: Dict[str, Dict[str, Any]] = {}


def _match_segment_margin(name: str, extracted: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Fuzzy-match an FMP segment name to an extracted segment-economics row
    (case-insensitive substring either way)."""
    n = (name or "").lower().strip()
    if not n or not extracted:
        return None
    if n in extracted:
        return extracted[n]
    for k, v in extracted.items():
        if k and (k in n or n in k):
            return v
    return None


def segment_economics(ticker: str,
                      extracted: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    """Revenue mix by segment (deterministic, FMP) overlaid with per-segment
    operating margin / trajectory from the 10-K extraction where available
    (refinement P2). Drives the §4 'Margin trajectory by segment' dimension and
    the terminal-margin grounding in P4. Cached per ticker (mix only; the overlay
    is cheap)."""
    key = (ticker or "").upper()
    out: Dict[str, Any] = {"available": False, "segments": []}
    try:
        from aletheia.data import fmp_client
        seg = (_SEGMENT_CACHE.get(key)
               if key in _SEGMENT_CACHE
               else fmp_client.fetch_revenue_product_segmentation(key))
        if key not in _SEGMENT_CACHE:
            _SEGMENT_CACHE[key] = seg  # cache the raw FMP rows (None-safe)
        if not seg:
            return out
        latest = (seg[0] or {}).get("data") or {}
        prior = ((seg[1] or {}).get("data") or {}) if len(seg) > 1 else {}
        total = sum(v for v in latest.values() if isinstance(v, (int, float)) and v > 0)
        if total <= 0:
            return out
        ext = {(s.get("segment") or "").lower().strip(): s for s in (extracted or [])}
        segs: List[Dict[str, Any]] = []
        for name, rev in sorted(latest.items(), key=lambda kv: -(kv[1] or 0)):
            if not isinstance(rev, (int, float)) or rev <= 0:
                continue
            prev = prior.get(name)
            growth = (rev / prev - 1.0) if isinstance(prev, (int, float)) and prev > 0 else None
            em = _match_segment_margin(name, ext) or {}
            segs.append({
                "segment": name,
                "revenue": float(rev),
                "rev_pct": float(rev) / total,
                "yoy_growth": growth,
                "margin": em.get("operating_margin") or em.get("margin") or None,
                "margin_trend": em.get("margin_trend") or em.get("trend") or None,
                "margin_note": em.get("notes") or "",
            })
        out = {
            "available": True,
            "segments": segs,
            "n_segments": len(segs),
            "fiscal_year": (seg[0] or {}).get("fiscalYear"),
            "has_margins": any(s.get("margin") for s in segs),
            "source": "FMP revenue-product-segmentation (mix) + 10-K extraction (margins)",
        }
    except Exception:
        return {"available": False, "segments": []}
    return out


# Multipliers for "$X <scale>" → absolute dollars (TAM parse, P3).
_SCALE = {"trillion": 1e12, "tn": 1e12, "billion": 1e9, "bn": 1e9, "bil": 1e9,
          "million": 1e6, "mn": 1e6, "mm": 1e6}


def _parse_dollar(text: str) -> Optional[float]:
    """Best-effort parse of the first '$X billion/trillion/million' in free text.
    Returns absolute dollars, or None when not confidently parseable."""
    if not text:
        return None
    m = re.search(r"\$?\s*([0-9]+(?:\.[0-9]+)?)\s*(trillion|billion|million|tn|bn|bil|mn|mm)",
                  text, re.IGNORECASE)
    if not m:
        return None
    try:
        return float(m.group(1)) * _SCALE[m.group(2).lower()]
    except Exception:
        return None


def tam_assessment(ticker: str, extracted: Optional[Dict[str, Any]],
                   latest_revenue: Optional[float] = None) -> Dict[str, Any]:
    """TAM band + implied-share view (Tier-2 #1). The $ TAM band (low/base/high),
    sizing approach, methodology and confidence come from the 10-K extraction
    (BusinessAB). Implied share = revenue ÷ TAM is computed deterministically for
    each band edge. Always carries an explicit confidence so a rough figure never
    reads as fact; higher TAM → lower implied share, so the band inverts."""
    ab = extracted or {}
    tam_text = ab.get("tam_estimate") or ""
    out: Dict[str, Any] = {
        "available": bool(tam_text),
        "tam_estimate": tam_text or None,
        "tam_low": ab.get("tam_low") or None,
        "tam_high": ab.get("tam_high") or None,
        "tam_approach": ab.get("tam_approach") or None,
        "tam_methodology": ab.get("tam_methodology") or None,
        "tam_confidence": (ab.get("tam_confidence") or "").lower() or None,
        "market_share": ab.get("market_share") or None,
        "implied_share": None,
        "implied_share_low": None,    # at the HIGH TAM edge (smallest share)
        "implied_share_high": None,   # at the LOW TAM edge (largest share)
        "tam_usd": None,
    }
    rev = latest_revenue if isinstance(latest_revenue, (int, float)) and latest_revenue > 0 else None
    tam_usd = _parse_dollar(tam_text)
    if tam_usd and rev:
        out["tam_usd"] = tam_usd
        out["implied_share"] = rev / tam_usd
    if rev:
        lo_usd = _parse_dollar(ab.get("tam_low") or "")     # smaller TAM → larger share
        hi_usd = _parse_dollar(ab.get("tam_high") or "")    # larger TAM → smaller share
        if lo_usd:
            out["implied_share_high"] = rev / lo_usd
        if hi_usd:
            out["implied_share_low"] = rev / hi_usd
    return out


_MA_SPEND_CACHE: Dict[str, Dict[str, Any]] = {}

# A fiscal year is "material M&A" when cash paid for acquisitions exceeds this
# share of that year's revenue; the window is material overall when any single
# year clears YEAR_PCT or the cumulative spend clears CUM_PCT.
_MA_YEAR_PCT = 0.03
_MA_CUM_PCT = 0.08


def ma_spend(ticker: str, years: Optional[List[int]] = None) -> Dict[str, Any]:
    """Detect M&A activity from the cash-flow statement (FMP ``acquisitionsNet``)
    — the signal the revenue-spike break detector misses for bolt-ons on a large
    base (e.g. MSFT/Activision: $69B but only ~4% of revenue, no YoY spike).
    Flag-only: reports spend + which years, does NOT estimate the organic split.
    ``years`` restricts the window (the CAGR lookback); None uses the last 6."""
    key = (ticker or "").upper()
    cache_key = f"{key}:{years[0] if years else ''}-{years[-1] if years else ''}"
    if cache_key in _MA_SPEND_CACHE:
        return _MA_SPEND_CACHE[cache_key]
    out: Dict[str, Any] = {"material": False, "years": []}
    try:
        from aletheia.data import fmp_client
        cf = fmp_client.fetch_cash_flows(key) or []
        inc = fmp_client.fetch_income_statements(key) or []
        rev_by_year = {}
        for i in inc:
            y = i.get("calendarYear") or str(i.get("date", ""))[:4]
            if y and isinstance(i.get("revenue"), (int, float)):
                rev_by_year[str(y)] = float(i["revenue"])
        yrset = {str(y) for y in years} if years else None
        # Aggregate by year (FMP occasionally returns duplicate rows per year —
        # most-recent-first, so keep the first/latest value seen).
        spend_by_year: Dict[str, float] = {}
        for c in cf:
            y = c.get("calendarYear") or str(c.get("date", ""))[:4]
            if not y:
                continue
            y = str(y)
            if y in spend_by_year:
                continue
            if yrset is not None and y not in yrset:
                continue
            acq = c.get("acquisitionsNet")
            rev = rev_by_year.get(y)
            if not isinstance(acq, (int, float)) or not rev or rev <= 0:
                continue
            spend_by_year[y] = abs(float(acq))
        ma_years, total_spend, pcts = [], 0.0, []
        for y, spend in spend_by_year.items():
            rev = rev_by_year.get(y) or 0
            if rev <= 0:
                continue
            pct = spend / rev
            total_spend += spend
            if pct >= _MA_YEAR_PCT:
                ma_years.append({"year": int(y), "spend": spend, "pct_of_revenue": pct})
            pcts.append(pct)
        # Cumulative spend over the window vs the most recent revenue in window.
        ref_rev = max((rev_by_year.get(str(y)) or 0) for y in years) if years and rev_by_year else (
            max(rev_by_year.values()) if rev_by_year else 0)
        cum_pct = (total_spend / ref_rev) if ref_rev else None
        material = bool(ma_years) and (
            any(m["pct_of_revenue"] >= 0.05 for m in ma_years)
            or (cum_pct is not None and cum_pct >= _MA_CUM_PCT))
        out = {
            "material": material,
            "years": sorted(ma_years, key=lambda m: -m["year"]),
            "total_spend": total_spend,
            "cum_pct_of_revenue": cum_pct,
            "max_year_pct": max(pcts) if pcts else None,
            "source": "FMP cash-flow acquisitionsNet",
        }
    except Exception:
        out = {"material": False, "years": []}
    _MA_SPEND_CACHE[cache_key] = out
    return out


def _latest_revenue(calc) -> Optional[float]:
    """Most-recent FY revenue from the calc frame (for implied-share math)."""
    try:
        df = getattr(calc, "df", None)
        if df is None or "clean_Revenue" not in getattr(df, "columns", []):
            return None
        d = df
        if "period" in d.columns:
            d = d[d["period"] == "FY"]
        d = d.dropna(subset=["clean_Revenue"]).sort_values("fiscal_year")
        revs = [float(x) for x in d["clean_Revenue"].tolist()]
        return revs[-1] if revs else None
    except Exception:
        return None


def build_growth_decomposition(calc) -> Dict[str, Any]:
    """Decompose recent revenue growth into organic vs M&A.

    M&A is detected from cash-flow acquisition spend (``ma_spend``) over a
    consistent window — NOT from revenue spikes. The old revenue-spike
    heuristic mis-flagged early-stage organic hypergrowth as "M&A" (e.g. CRM
    FY2004-2007) and compared ``raw_cagr`` (recent window) against an organic
    geomean computed over a DIFFERENT span, yielding nonsensical negative M&A
    contributions. Flag-only: we know cash spent, not acquired revenue, so we
    don't fabricate an organic/inorganic split — ``raw_cagr`` is an upper bound
    on organic when M&A lands in the window. Returns ``available=False`` when
    there's too little history.

    Caveat: cash-flow detection cannot see ALL-STOCK acquisitions (e.g. CRM's
    Tableau, FY2020) — they leave no cash-flow footprint."""
    out: Dict[str, Any] = {"available": False}
    try:
        df = getattr(calc, "df", None)
        if df is None or "clean_Revenue" not in getattr(df, "columns", []):
            return out
        d = df
        if "period" in d.columns:
            d = d[d["period"] == "FY"]
        d = d.dropna(subset=["clean_Revenue"]).sort_values("fiscal_year")
        revs = [float(x) for x in d["clean_Revenue"].tolist()]
        fy_years = [int(x) for x in d["fiscal_year"].tolist()]
        if len(revs) < 4:
            return out
        # Raw point-to-point CAGR over the last ≤5 fiscal years.
        k = min(5, len(revs) - 1)
        base = revs[-1 - k]
        raw_cagr = (revs[-1] / base) ** (1.0 / k) - 1.0 if base > 0 else None
        window_years = fy_years[-(k + 1):]

        cls = getattr(calc, "classification", None)
        ticker = getattr(cls, "ticker", "") or ""

        # M&A detection via cash-flow acquisition spend (the reliable signal).
        # Wide lookback lists the real deals; the subset inside the CAGR window
        # drives separability of recent growth.
        wide_years = fy_years[-min(10, len(revs)):]
        mas = ma_spend(ticker, wide_years) if ticker else {"material": False}
        win_set = set(window_years)
        ma_in_window = sorted(m["year"] for m in mas.get("years", [])
                              if m["year"] in win_set)
        organic_is_upper_bound = False
        ma_separable = True
        if ma_in_window:
            # Material M&A inside the recent CAGR window. We know cash spent,
            # not acquired revenue, so we do NOT fabricate an organic/inorganic
            # split — raw CAGR is an upper bound on organic.
            organic = raw_cagr
            ma_pp = None
            ma_separable = False
            organic_is_upper_bound = True
            break_years = ma_in_window
            yrs = ", ".join(f"FY{y}" for y in ma_in_window)
            split = (f"M&A material in window ({yrs}) — cash spend known but "
                     f"acquired revenue not disclosed; organic ≤ raw CAGR")
        elif mas.get("material"):
            # Material M&A only in earlier years (outside the recent window) →
            # the recent CAGR is effectively organic.
            organic = raw_cagr
            ma_pp = 0.0
            break_years = []
            split = "all organic in window (material M&A only in earlier years)"
        else:
            organic = raw_cagr  # no material M&A spend → all organic
            ma_pp = 0.0
            break_years = []
            split = "all organic (no material M&A spend)"
        # Market-vs-share split (Phase 5): organic growth above the peer-group
        # market-growth reference is share gain; below is share loss / lagging.
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
            "ma_separable": ma_separable,
            "organic_is_upper_bound": organic_is_upper_bound,
            "ma_spend": mas if mas.get("material") else None,
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


# Dimensions that are genuinely N/A for whole business types (P5): a real "not
# applicable" should not read as "pending extraction". CAC/LTV/cohort economics
# only exist for subscription/consumer models.
_CAC_APPLICABLE_TEMPLATES = {"tech_saas", "consumer"}


def _coverage_status(source, bm, dims, gd, lifecycle, ab, dimension,
                     seg, tam, template_key):
    """Three-state coverage label (P5): 'present', 'n_a' (structurally not
    applicable / not disclosed), or 'pending' (extractable, not yet run).
    Returns (status, reason)."""
    # Present always wins (real data beats any N/A heuristic).
    if _present(source, bm, dims, gd, lifecycle, ab, dimension):
        return "present", ""
    # Segment margins: present when FMP mix carries extracted margins.
    if dimension == "Margin trajectory by segment" and (seg or {}).get("has_margins"):
        return "present", ""
    # TAM declared genuinely not estimable by the extraction → N/A, not pending.
    if dimension == "TAM sizing" and (tam or {}).get("tam_confidence") == "not_estimable":
        return "n_a", "market not sizeable even by triangulation"
    # CAC/LTV/cohorts only apply to subscription/consumer models.
    if dimension == "CAC / LTV / cohorts" and template_key not in _CAC_APPLICABLE_TEMPLATES:
        return "n_a", "not a subscription / consumer model"
    return "pending", ""


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
    template_key = template.get("key")

    # Peer-set stats (curated/FMP) — market growth, multiple, margin context.
    pg_key = (template.get("peer_group") if template else None)
    ps = peer_stats(ticker, pg_key)

    # Segment economics (P2): FMP revenue mix + extracted per-segment margins.
    seg = segment_economics(ticker, (ab or {}).get("segment_economics"))

    # TAM + implied share (P3): extracted TAM/confidence + deterministic share.
    tam = tam_assessment(ticker, ab or {}, _latest_revenue(calc) if calc is not None else None)

    coverage = []
    n_present = 0
    n_na = 0
    for theme, dimension, source in _COVERAGE:
        status, reason = _coverage_status(
            source, bm, dims, gd, lifecycle, ab, dimension, seg, tam, template_key)
        n_present += int(status == "present")
        n_na += int(status == "n_a")
        coverage.append({
            "theme": theme, "dimension": dimension,
            "status": status,
            "reason": reason,
            "priority": dimension in priority,   # sector-emphasized
            "source": ("business_ab extraction" if _AB_FIELDS.get(dimension) and ab.get(_AB_FIELDS[dimension])
                       else source or "needs extraction"),
        })
    # Sort priority dimensions to the top so the sector's key items lead.
    coverage.sort(key=lambda c: (not c["priority"]))

    out.update({
        "available": True,
        "growth_decomposition": gd,
        "peer_stats": ps,
        "segment_economics": seg,
        "tam": tam,
        "extracted": ab or None,          # themes A+B+C+E (LLM)
        "coverage": coverage,
        "n_present": n_present,
        "n_na": n_na,
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
