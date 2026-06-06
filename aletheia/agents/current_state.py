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


def build_current_state(
    ticker: str,
    *,
    engine_y1_growth: Optional[float],
    latest_fy: Optional[int] = None,
    latest_actual_rev: Optional[float] = None,
    sector: str = "",
    events: Optional[List[Dict[str, Any]]] = None,
) -> CurrentStateResult:
    """Build the current-state reconciliation for a ticker.

    Args:
        engine_y1_growth: the engine's near-term revenue growth assumption
            (decimal) — typically base-case ``revenue_cagr_y1_5``.
        latest_fy / latest_actual_rev: anchor for the consensus Y1 growth.
        events: material recent events (Source 2), each a dict with at least
            ``date``, ``category``, ``headline``, ``materiality`` (1-5),
            ``source``. Supplied by the events agent or manual entry.
    """
    res = CurrentStateResult(ticker=ticker.upper())
    res.consensus = _consensus_forward_growth(ticker, latest_fy, latest_actual_rev)
    res.microstructure = _microstructure(ticker)
    res.events = list(events or [])

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


__all__ = [
    "build_current_state", "CurrentStateResult", "CurrentStateFlag",
    "HIGH", "MEDIUM", "LOW", "NONE",
]
