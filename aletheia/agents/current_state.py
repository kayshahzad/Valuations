"""Current-State Awareness Layer (Phase 1.5).

The engine values a company off historical financials + peer multiples. That
is rigorous but blind to *current operating reality* — the NVO failure
(June 2026): engine produced +137% MoS / CONVICTION while management had guided
to a revenue *decline* and analysts had cut estimates accordingly. The engine
called a rational repricing a "mispricing".

This layer reconciles the engine's forward growth assumption against what the
market currently expects (forward analyst consensus — the available proxy for
management guidance) and against material recent events, then raises flags. It
does NOT auto-override the engine; HIGH flags require analyst acknowledgment
before a memo is finalized.

Data sources actually available here:
  - FMP forward analyst estimates  → consensus forward revenue growth (Source 1/3 proxy)
  - yfinance fast_info             → 52-week range, beta (Source 4)
  - events (Source 2)              → passed in (LLM+web-search agent or manual);
                                     this module consumes them, doesn't fetch.

Growth-rate comparisons are currency-invariant, so the consensus check works
for non-USD filers (NVO/DKK) without FX conversion.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


def _slug(text: str, n: int = 48) -> str:
    """Stable lowercase token from free text (for flag keys)."""
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    return s[:n]

# Severity ladder.
HIGH, MEDIUM, LOW, NONE = "HIGH", "MEDIUM", "LOW", "NONE"
_SEV_RANK = {NONE: 0, LOW: 1, MEDIUM: 2, HIGH: 3}

# Engine-growth vs consensus deltas (percentage points).
_GROWTH_DELTA_HIGH = 0.05   # >5pp apart → HIGH
_GROWTH_DELTA_MED = 0.02    # >2pp apart → MEDIUM


@dataclass
class CurrentStateFlag:
    severity: str
    category: str
    message: str
    recommendation: str = ""
    source: str = ""
    key: str = ""  # stable id for acknowledgment persistence

    def to_dict(self) -> Dict[str, Any]:
        return {
            "severity": self.severity, "category": self.category,
            "message": self.message, "recommendation": self.recommendation,
            "source": self.source, "key": self.key,
        }


@dataclass
class CurrentStateResult:
    ticker: str
    consensus: Dict[str, Any] = field(default_factory=dict)
    microstructure: Dict[str, Any] = field(default_factory=dict)
    events: List[Dict[str, Any]] = field(default_factory=list)
    flags: List[CurrentStateFlag] = field(default_factory=list)
    reconciliation: List[Dict[str, Any]] = field(default_factory=list)
    # Reused / derived signals (display-only — do NOT feed pillar score yet).
    sector_valuation: Dict[str, Any] = field(default_factory=dict)
    policy_regulatory: Dict[str, Any] = field(default_factory=dict)
    market_signal: Dict[str, Any] = field(default_factory=dict)
    analyst_sentiment: Dict[str, Any] = field(default_factory=dict)
    pillar_score: Optional[int] = None
    pillar_reason: str = ""

    @property
    def max_severity(self) -> str:
        if not self.flags:
            return NONE
        return max((f.severity for f in self.flags), key=lambda s: _SEV_RANK.get(s, 0))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "consensus": self.consensus,
            "microstructure": self.microstructure,
            "events": self.events,
            "flags": [f.to_dict() for f in self.flags],
            "reconciliation": self.reconciliation,
            "sector_valuation": self.sector_valuation,
            "policy_regulatory": self.policy_regulatory,
            "market_signal": self.market_signal,
            "analyst_sentiment": self.analyst_sentiment,
            "pillar_score": self.pillar_score,
            "pillar_reason": self.pillar_reason,
            "max_severity": self.max_severity,
        }


def _consensus_forward_growth(
    ticker: str, latest_fy: Optional[int], latest_actual_rev: Optional[float],
) -> Dict[str, Any]:
    """Forward revenue-growth consensus from FMP analyst estimates.

    Returns Y1 growth (next FY vs latest reported), the forward CAGR over the
    available estimate horizon, and provenance. Growth is currency-invariant,
    so DKK estimates compared to a USD-converted engine are fine.
    """
    out: Dict[str, Any] = {"available": False}
    try:
        from aletheia.data import fmp_client
        est = fmp_client.fetch_analyst_estimates(ticker) or []
    except Exception:
        est = []
    # year -> avg revenue estimate
    by_year: Dict[int, float] = {}
    for r in est:
        d = str(r.get("date") or "")
        rev = r.get("revenueAvg")
        if len(d) >= 4 and d[:4].isdigit() and rev:
            try:
                by_year[int(d[:4])] = float(rev)
            except (TypeError, ValueError):
                pass
    if not by_year:
        return out

    years = sorted(by_year)
    # Anchor: the latest reported FY (from the cleaned data). The Y1 forward
    # year is anchor+1. If we don't know the anchor, use the estimate series'
    # own consecutive years.
    anchor = latest_fy if latest_fy in by_year else None
    if anchor is None:
        # Fall back to the earliest estimate year as the base.
        anchor = years[0]
    # Base on FMP's OWN value for the anchor year so the series stays in one
    # currency (the DB actual may be USD-converted while FMP estimates are in
    # the filer's native currency — mixing them corrupts the growth rate).
    base_rev = by_year.get(anchor)

    y1_year = anchor + 1
    y1_growth = None
    if base_rev and base_rev > 0 and by_year.get(y1_year):
        y1_growth = by_year[y1_year] / base_rev - 1.0

    # Forward CAGR over the estimate horizon (anchor -> last estimate year).
    fwd_cagr = None
    last_year = years[-1]
    if base_rev and base_rev > 0 and last_year > anchor and by_year.get(last_year):
        n = last_year - anchor
        fwd_cagr = (by_year[last_year] / base_rev) ** (1.0 / n) - 1.0

    out.update({
        "available": True,
        "anchor_year": anchor,
        "y1_year": y1_year,
        "y1_growth": y1_growth,
        "forward_cagr": fwd_cagr,
        "horizon_years": last_year - anchor,
        "source": "FMP analyst estimates (forward consensus)",
    })
    return out


def _microstructure(ticker: str) -> Dict[str, Any]:
    """52-week range position + beta from yfinance fast_info."""
    out: Dict[str, Any] = {}
    try:
        import yfinance as yf
        fi = yf.Ticker(ticker).fast_info
        last = float(fi.last_price) if fi.last_price else None
        hi = float(getattr(fi, "year_high", 0) or 0) or None
        lo = float(getattr(fi, "year_low", 0) or 0) or None
        out["price"] = last
        out["week52_high"] = hi
        out["week52_low"] = lo
        if last and hi and lo and hi > lo:
            out["pct_above_52w_low"] = (last - lo) / lo
            out["pct_below_52w_high"] = (hi - last) / hi
    except Exception:
        pass
    return out


def _own_5y_ev_ebitda(ticker: str) -> Optional[float]:
    """Company's own ~5-year-average EV/EBITDA (trimmed mean) from FMP key
    metrics history. Self-relative valuation anchor. None when unavailable."""
    try:
        from aletheia.data import fmp_client
        km = fmp_client.fetch_key_metrics(ticker) or []
        vals = []
        for r in km[:5]:
            v = r.get("evToEBITDA")
            if isinstance(v, (int, float)) and 0 < v < 200:
                vals.append(float(v))
        if len(vals) < 3:
            return None
        vals.sort()
        trimmed = vals[1:-1] if len(vals) >= 5 else vals  # drop hi/lo when ≥5
        return sum(trimmed) / len(trimmed)
    except Exception:
        return None


def _sector_relative_valuation(
    md: Optional[Dict[str, Any]], ticker: str = "",
) -> Dict[str, Any]:
    """Sector- AND self-relative valuation. REUSES the MultipleDecomposition
    tool's market vs sector-median EV/EBITDA, plus the company's own 5-year
    average EV/EBITDA (self-historical anchor).

    Display-only: returns a context block (premium/discount + interpretation).
    It does NOT raise a flag or feed the pillar score in this phase."""
    out: Dict[str, Any] = {"available": False}
    if not md:
        return out
    market = md.get("market_ev_ebitda")
    sector_med = md.get("sector_median_ev_ebitda")
    if not isinstance(market, (int, float)) or not isinstance(sector_med, (int, float)) \
            or market <= 0 or sector_med <= 0:
        return out
    premium_x = md.get("vs_sector_premium")
    if not isinstance(premium_x, (int, float)):
        premium_x = market - sector_med
    premium_pct = (market / sector_med - 1.0) if sector_med else None
    # Interpretation is descriptive only (not a buy/sell call).
    if premium_pct is None:
        label = "—"
    elif premium_pct > 0.25:
        label = "rich vs sector"
    elif premium_pct < -0.25:
        label = "cheap vs sector"
    else:
        label = "in line with sector"
    # Self-relative: current vs the company's own 5Y average multiple.
    own_5y = _own_5y_ev_ebitda(ticker) if ticker else None
    own_premium_pct = (market / own_5y - 1.0) if (own_5y and own_5y > 0) else None
    own_label = None
    if own_premium_pct is not None:
        own_label = ("above own 5Y avg" if own_premium_pct > 0.10
                     else "below own 5Y avg" if own_premium_pct < -0.10
                     else "near own 5Y avg")

    out.update({
        "available": True,
        "market_ev_ebitda": float(market),
        "sector_median_ev_ebitda": float(sector_med),
        "sector": md.get("sector"),
        "premium_x": float(premium_x) if isinstance(premium_x, (int, float)) else None,
        "premium_pct": float(premium_pct) if premium_pct is not None else None,
        "label": label,
        "own_5y_ev_ebitda": float(own_5y) if own_5y else None,
        "own_5y_premium_pct": own_premium_pct,
        "own_5y_label": own_label,
        "source": "MultipleDecomposition (market vs sector-median) + FMP own 5Y avg",
        "note": "Sector median is a static reference table, not a live peer set.",
    })
    return out


def _policy_regulatory_context(
    events: List[Dict[str, Any]],
    regulatory_exposure: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Policy/regulatory context, REUSING signals already produced by existing
    LLM calls — no new call:

      1. Cached events tagged ``pricing_regulatory`` / ``regulatory_legal``
         (from the grounded events agent).
      2. The ``regulatory_exposure`` qualitative dimension (LLM-extracted from
         the 10-K): a 1-7 score (higher = lower risk) + ``material_exposures``.

    Display-only aggregation; the underlying events already raise their own
    flags, so this adds context, not new flags."""
    out: Dict[str, Any] = {"available": False, "recent_actions": []}
    _REG_CATS = ("pricing_regulatory", "regulatory_legal")
    actions = []
    adverse = favorable = 0
    for ev in events or []:
        if (ev.get("category") or "").lower() not in _REG_CATS:
            continue
        direction = (ev.get("direction") or "").lower()
        if direction == "adverse":
            adverse += 1
        elif direction == "favorable":
            favorable += 1
        actions.append({
            "date": ev.get("date", ""),
            "category": ev.get("category", ""),
            "direction": direction or "neutral",
            "headline": ev.get("headline", ""),
            "materiality": ev.get("materiality"),
            "source": ev.get("source", ""),
        })
    # Net tone from the recent regulatory events.
    net = ("adverse" if adverse > favorable else
           "favorable" if favorable > adverse else
           "mixed" if (adverse or favorable) else "none")

    exposure_score = exposure_label = exposure_narrative = None
    material_exposures: List[Dict[str, Any]] = []
    if regulatory_exposure:
        sc = regulatory_exposure.get("score")
        exposure_score = float(sc) if isinstance(sc, (int, float)) else None
        if exposure_score is not None:
            # 1-7, higher = lower risk.
            exposure_label = ("low exposure" if exposure_score >= 6 else
                              "moderate exposure" if exposure_score >= 4 else
                              "high exposure")
        exposure_narrative = regulatory_exposure.get("narrative")
        sp = regulatory_exposure.get("source_payload") or {}
        if isinstance(sp, dict):
            mx = sp.get("material_exposures")
            if isinstance(mx, list):
                material_exposures = mx[:6]

    if not actions and exposure_score is None:
        return out
    out.update({
        "available": True,
        "recent_actions": actions,
        "net_event_direction": net,
        "exposure_score": exposure_score,           # 1-7 (higher = lower risk)
        "exposure_label": exposure_label,
        "exposure_narrative": exposure_narrative,
        "material_exposures": material_exposures,
        "source": "cached regulatory events + regulatory_exposure 10-K dimension",
    })
    return out


def _market_signal(ticker: str, microstructure: Dict[str, Any]) -> Dict[str, Any]:
    """Market-signal interpretation (Phase C). Extends the 52-week range
    position (already in microstructure) with momentum + moving-average
    context from cached FMP EOD prices. No LLM; cache-backed REST only.

    Display-only: describes what the market is pricing (derating vs priced
    for strength) — it does NOT raise a flag or feed the pillar score."""
    out: Dict[str, Any] = {"available": False}
    pct_below_high = microstructure.get("pct_below_52w_high")
    pct_above_low = microstructure.get("pct_above_52w_low")
    last = microstructure.get("price")

    ma50 = ma200 = ret_3m = ret_6m = None
    try:
        from aletheia.data import fmp_client
        rows = fmp_client.fetch_historical_prices(ticker, days=400) or []
        # Light EOD rows: {date, price}, most-recent first.
        prices = [float(r.get("price")) for r in rows
                  if isinstance(r.get("price"), (int, float))]
        if prices:
            if last is None:
                last = prices[0]
            if len(prices) >= 50:
                ma50 = sum(prices[:50]) / 50
            if len(prices) >= 200:
                ma200 = sum(prices[:200]) / 200
            # ~21 trading days/month.
            if len(prices) > 63 and prices[63]:
                ret_3m = last / prices[63] - 1.0
            if len(prices) > 126 and prices[126]:
                ret_6m = last / prices[126] - 1.0
    except Exception:
        pass

    # Need at least the 52-week position to say anything.
    if pct_below_high is None and pct_above_low is None and ma200 is None:
        return out

    dist_ma200 = (last / ma200 - 1.0) if (last and ma200) else None
    # Descriptive interpretation (not a recommendation).
    if pct_below_high is not None and pct_below_high <= 0.05:
        label = "near 52-week high — priced for strength"
    elif pct_above_low is not None and pct_above_low <= 0.10:
        label = "near 52-week low — market pricing weakness"
    elif dist_ma200 is not None and dist_ma200 < -0.05:
        label = "below 200-day MA — downtrend"
    elif dist_ma200 is not None and dist_ma200 > 0.05:
        label = "above 200-day MA — uptrend"
    else:
        label = "mid-range / neutral trend"
    out.update({
        "available": True,
        "price": float(last) if last else None,
        "pct_below_52w_high": pct_below_high,
        "pct_above_52w_low": pct_above_low,
        "ma50": float(ma50) if ma50 else None,
        "ma200": float(ma200) if ma200 else None,
        "dist_ma200": float(dist_ma200) if dist_ma200 is not None else None,
        "return_3m": float(ret_3m) if ret_3m is not None else None,
        "return_6m": float(ret_6m) if ret_6m is not None else None,
        "label": label,
        "source": "yfinance 52w range + FMP EOD prices (momentum/MA)",
    })
    return out


def _analyst_sentiment(ticker: str, current_price: Optional[float] = None) -> Dict[str, Any]:
    """Analyst-sentiment tracking (Phase D). Blends price-target consensus,
    rating distribution, and recent up/downgrade actions from FMP REST (all
    cache-backed; no LLM). Display-only — describes sentiment, no flag.

    Degrades gracefully: returns available=False if the FMP plan lacks the
    price-target / grades endpoints."""
    out: Dict[str, Any] = {"available": False}
    target_avg = implied_upside = None
    buy = hold = sell = None
    consensus = None
    recent_up = recent_down = 0
    target_high = target_low = dispersion = None
    try:
        from aletheia.data import fmp_client
        pts = fmp_client.fetch_price_target_summary(ticker) or {}
        # FMP keys vary; try the common ones.
        for k in ("lastMonthAvgPriceTarget", "lastQuarterAvgPriceTarget",
                  "avgPriceTarget", "priceTargetAverage"):
            v = pts.get(k)
            if isinstance(v, (int, float)) and v > 0:
                target_avg = float(v); break
        if target_avg and current_price:
            implied_upside = target_avg / current_price - 1.0

        # Dispersion (analyst disagreement) from the consensus high/low.
        ptc = fmp_client.fetch_price_target_consensus(ticker) or {}
        th, tl = ptc.get("targetHigh"), ptc.get("targetLow")
        tc = ptc.get("targetConsensus") or ptc.get("targetMedian") or target_avg
        if isinstance(th, (int, float)) and th > 0:
            target_high = float(th)
        if isinstance(tl, (int, float)) and tl > 0:
            target_low = float(tl)
        if target_high and target_low and tc:
            dispersion = (target_high - target_low) / float(tc)  # range ÷ consensus

        gc = fmp_client.fetch_grades_consensus(ticker) or {}
        buy = (gc.get("strongBuy") or 0) + (gc.get("buy") or 0)
        hold = gc.get("hold") or 0
        sell = (gc.get("sell") or 0) + (gc.get("strongSell") or 0)
        consensus = gc.get("consensus")

        hist = fmp_client.fetch_grades_historical(ticker) or []
        # Most-recent ~6 actions: count up/downgrades by action field.
        for h in hist[:6]:
            act = str(h.get("action") or h.get("newGrade") or "").lower()
            if "up" in act:
                recent_up += 1
            elif "down" in act:
                recent_down += 1
    except Exception:
        pass

    has_targets = target_avg is not None
    has_ratings = any(isinstance(x, (int, float)) and x for x in (buy, hold, sell))
    if not has_targets and not has_ratings:
        return out

    # Blended descriptive label.
    score = 0
    if implied_upside is not None:
        score += 1 if implied_upside > 0.10 else -1 if implied_upside < -0.10 else 0
    if has_ratings and (buy + hold + sell) > 0:
        skew = (buy - sell) / (buy + hold + sell)
        score += 1 if skew > 0.2 else -1 if skew < -0.2 else 0
    score += 1 if recent_up > recent_down else -1 if recent_down > recent_up else 0
    label = ("bullish" if score >= 2 else "bearish" if score <= -2 else "neutral")

    # Dispersion label: wide spread = low conviction in the consensus.
    disp_label = None
    if dispersion is not None:
        disp_label = ("wide (low consensus conviction)" if dispersion > 0.6
                      else "moderate" if dispersion > 0.3 else "tight")

    out.update({
        "available": True,
        "target_avg": target_avg,
        "target_high": target_high,
        "target_low": target_low,
        "dispersion": dispersion,
        "dispersion_label": disp_label,
        "implied_upside": implied_upside,
        "ratings": {"buy": buy, "hold": hold, "sell": sell},
        "consensus": consensus,
        "recent_upgrades": recent_up,
        "recent_downgrades": recent_down,
        "label": label,
        "source": "FMP price-target summary/consensus + analyst grades",
    })
    return out


def build_current_state(
    ticker: str,
    *,
    engine_y1_growth: Optional[float],
    latest_fy: Optional[int] = None,
    latest_actual_rev: Optional[float] = None,
    sector: str = "",
    events: Optional[List[Dict[str, Any]]] = None,
    multiple_decomposition: Optional[Dict[str, Any]] = None,
    regulatory_exposure: Optional[Dict[str, Any]] = None,
) -> CurrentStateResult:
    """Build the current-state reconciliation for a ticker.

    Args:
        engine_y1_growth: the engine's near-term revenue growth assumption
            (decimal) — typically base-case ``revenue_cagr_y1_5``.
        latest_fy / latest_actual_rev: anchor for the consensus Y1 growth.
        events: material recent events (Source 2), each a dict with at least
            ``date``, ``category``, ``headline``, ``materiality`` (1-5),
            ``source``. Supplied by the events agent or manual entry.
        multiple_decomposition: the MultipleDecomposition tool's output dict
            (reused for sector-relative valuation — no recompute).
        regulatory_exposure: the latest ``regulatory_exposure`` qualitative
            assessment (reused for policy/regulatory context — no new LLM call).
    """
    res = CurrentStateResult(ticker=ticker.upper())
    res.consensus = _consensus_forward_growth(ticker, latest_fy, latest_actual_rev)
    res.microstructure = _microstructure(ticker)
    res.events = list(events or [])
    # Reused / derived signals (display-only).
    res.sector_valuation = _sector_relative_valuation(multiple_decomposition, ticker)
    res.policy_regulatory = _policy_regulatory_context(res.events, regulatory_exposure)
    res.market_signal = _market_signal(ticker, res.microstructure)
    res.analyst_sentiment = _analyst_sentiment(
        ticker, res.microstructure.get("price"))

    # ── Check 1: engine growth vs consensus (the NVO check) ─────────────
    cons_y1 = res.consensus.get("y1_growth")
    if engine_y1_growth is not None and cons_y1 is not None:
        delta = engine_y1_growth - cons_y1
        rec = {
            "assumption": "Y1 revenue growth",
            "engine": engine_y1_growth,
            "signal": cons_y1,
            "signal_label": "analyst consensus",
            "delta": delta,
        }
        # Asymmetric severity: the dangerous direction is the engine being
        # OPTIMISTIC vs consensus (NVO: +11% engine, −4% consensus → the engine
        # calls a rational repricing a mispricing). Engine BELOW consensus is
        # conservative — informative, not alarming — so it's capped lower to
        # avoid false positives.
        ad = abs(delta)
        if delta > 0:  # engine above consensus (over-optimism risk)
            sev = HIGH if ad > _GROWTH_DELTA_HIGH else MEDIUM if ad > _GROWTH_DELTA_MED else NONE
            verb = "exceeds"
        else:          # engine below consensus (conservative)
            sev = MEDIUM if ad > _GROWTH_DELTA_HIGH else LOW if ad > _GROWTH_DELTA_MED else NONE
            verb = "trails"
        if sev != NONE:
            rec["recommendation"] = (
                f"OVERRIDE toward {cons_y1*100:.1f}%" if sev == HIGH
                else f"Review vs consensus {cons_y1*100:.1f}%")
            res.flags.append(CurrentStateFlag(
                sev, "growth_vs_consensus",
                f"Engine Y1 growth ({engine_y1_growth*100:+.1f}%) {verb} forward "
                f"analyst consensus ({cons_y1*100:+.1f}%) by {ad*100:.1f}pp."
                + (" Override required." if sev == HIGH else " Review recommended."),
                recommendation=rec["recommendation"],
                source=res.consensus.get("source", ""),
                key="growth_vs_consensus",
            ))
        res.reconciliation.append(rec)

    # ── Event-driven flags (Source 2) ───────────────────────────────────
    # Only ADVERSE material events raise risk flags (the dangerous direction).
    # FAVORABLE events are recorded as supportive context, not risk — e.g.
    # LLY's own oral-GLP-1 approval helps LLY and must NOT read as a HIGH risk.
    _AFFECTED = {
        "guidance_cut": "Y1/terminal growth",
        "clinical_failure": "long-term growth / moat",
        "competitive": "moat / substitution risk",
        "pricing_regulatory": "terminal margin",
        "regulatory_legal": "moat / risk",
        "capital": "capital structure",
        "management": "execution risk",
    }
    for ev in res.events:
        mat = ev.get("materiality") or 0
        cat = (ev.get("category") or "").lower()
        direction = (ev.get("direction") or "").lower()
        if direction not in ("adverse", "favorable", "neutral"):
            # Default by category (matches the events parser) so events lacking
            # an explicit direction — older caches, manual entry — are still
            # classified: clearly-bad categories adverse, the rest neutral.
            direction = ("adverse"
                         if cat in ("guidance_cut", "clinical_failure", "regulatory_legal")
                         else "neutral")
        if cat not in _AFFECTED or mat < 4:
            continue
        headline = ev.get("headline", "(event)")
        ev_key = f"{cat}:{_slug(headline)}"
        if direction == "favorable":
            res.flags.append(CurrentStateFlag(
                LOW, cat,
                f"✅ {ev.get('date','')}: {headline} "
                f"(favorable — supports the thesis)",
                recommendation="Supportive — confirm it's reflected, not double-counted",
                source=ev.get("source", ""),
                key=ev_key,
            ))
        elif direction == "adverse":
            res.flags.append(CurrentStateFlag(
                HIGH if mat >= 5 else MEDIUM, cat,
                f"{ev.get('date','')}: {headline}",
                recommendation=f"Revisit {_AFFECTED[cat]}",
                source=ev.get("source", ""),
                key=ev_key,
            ))
        else:  # neutral — watch item, doesn't drive severity hard
            res.flags.append(CurrentStateFlag(
                MEDIUM if mat >= 5 else LOW, cat,
                f"{ev.get('date','')}: {headline} (watch)",
                recommendation=f"Assess impact on {_AFFECTED[cat]}",
                source=ev.get("source", ""),
                key=ev_key,
            ))

    # ── Pillar score (6th pillar) ───────────────────────────────────────
    res.pillar_score, res.pillar_reason = _score_pillar(res)
    return res


def _score_pillar(res: CurrentStateResult) -> tuple:
    """Current-State pillar (1-5). 5 = aligned/no adverse events;
    1 = engine disconnected from reality / severe adverse events."""
    n_high = sum(1 for f in res.flags if f.severity == HIGH)
    n_med = sum(1 for f in res.flags if f.severity == MEDIUM)
    cons_y1 = res.consensus.get("y1_growth")
    if n_high >= 2:
        return 1, "Severe disconnect — multiple HIGH current-state flags."
    if n_high == 1:
        return 2, "Material adverse signal — engine assumptions exceed current reality."
    if n_med >= 2:
        return 3, "Mixed signals — several MEDIUM current-state flags."
    if n_med == 1:
        return 4, "Minor divergence from current signals."
    if cons_y1 is None and not res.events:
        return 3, "No current-state signals available (no consensus/events)."
    return 5, "Aligned with current signals; no material adverse events."


def compose_current_state(ticker: str) -> Optional[Dict[str, Any]]:
    """One-call composer: recompute the full current-state payload for a ticker
    deterministically (DCF engine + multiple decomposition + cached events +
    regulatory_exposure). No LLM call on this path. Returns the ``to_dict()``
    payload, or ``None`` on any failure (e.g. specialized non-FCFF engines).

    Single source of truth for surfaces that only have a ticker (HTML/MD report
    generation). The API read-path uses its own already-computed DCFResult to
    avoid a second engine run, but produces the identical shape.
    """
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        from aletheia.tools.multiple_decomposition import MultipleDecomposition
        from aletheia.agents.current_state_events import cached_events
        from aletheia.data.database import InvestmentDatabase

        calc = make_calc_input(ticker)
        result = DCFEngine(verbose=False).run(calc)
        base = getattr(result, "base", None)
        eng_y1 = getattr(getattr(base, "assumptions", None),
                         "revenue_cagr_y1_5", None)
        fy = getattr(result, "fy_fiscal_year", None) or getattr(result, "fiscal_year", None)

        md = None
        try:
            mdres = MultipleDecomposition(verbose=False).run(calc)
            md = {
                "market_ev_ebitda":        mdres.market_ev_ebitda,
                "sector":                  getattr(mdres, "sector", None),
                "sector_median_ev_ebitda": mdres.sector_median_ev_ebitda,
                "vs_sector_premium":       mdres.vs_sector_premium,
            }
        except Exception:
            md = None

        reg = None
        try:
            db = InvestmentDatabase(verbose=False)
            try:
                reg = db.get_latest_assessment(ticker, "regulatory_exposure")
            finally:
                db.close()
        except Exception:
            reg = None

        return build_current_state(
            ticker, engine_y1_growth=eng_y1,
            latest_fy=int(fy) if fy else None,
            events=cached_events(ticker),
            multiple_decomposition=md,
            regulatory_exposure=reg,
        ).to_dict()
    except Exception:
        return None


__all__ = [
    "build_current_state", "compose_current_state",
    "CurrentStateResult", "CurrentStateFlag",
    "HIGH", "MEDIUM", "LOW", "NONE",
]
