"""Market-context block for the memo — the institutional 'tape' around a name.

Four deterministic, FMP-backed surfaces that triangulate the engine's intrinsic
view against how the market and sell-side currently see the stock:

  1. Earnings-surprise history — EPS actual vs consensus by quarter (beat/miss
     streak + magnitude). The leading read on execution vs expectations.
  2. Ratings consolidation — sell-side rating distribution + recent individual
     firm actions + price-target summary. (Independent-research houses —
     CFRA/Morningstar/Argus/Market Edge — are a separate licensed feed we don't
     carry; a slot is left for them.)
  3. ESG — placeholder, renders a rating when an MSCI/Sustainalytics feed is
     connected; honest 'not connected' otherwise (no fabricated score).
  4. Recent material news — top headlines in the trailing window, surfaced as
     readable, dated items.

No LLM. Every builder is fail-soft (returns ``available: False`` on any error)
so the memo degrades gracefully when an endpoint is missing.
"""

from __future__ import annotations

import datetime
from typing import Any, Dict, List, Optional


def _f(v) -> Optional[float]:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── 1. Earnings-surprise history ────────────────────────────────────────────

def build_earnings_surprises(ticker: str, n: int = 8) -> Dict[str, Any]:
    """EPS actual vs consensus over the last ``n`` reported quarters."""
    out: Dict[str, Any] = {"available": False, "quarters": []}
    try:
        from aletheia.data import fmp_client
        rows = fmp_client.fetch_earnings(ticker) or []
        reported = [r for r in rows if _f(r.get("epsActual")) is not None]
        reported.sort(key=lambda r: r.get("date", ""), reverse=True)  # most-recent first
        quarters: List[Dict[str, Any]] = []
        for r in reported[:n]:
            act, est = _f(r.get("epsActual")), _f(r.get("epsEstimated"))
            surprise = ((act - est) / abs(est)) if (act is not None and est not in (None, 0)) else None
            label = None
            if surprise is not None:
                label = ("beat" if surprise > 0.01 else "miss" if surprise < -0.01 else "in-line")
            quarters.append({
                "date": r.get("date"),
                "eps_actual": act,
                "eps_estimated": est,
                "surprise_pct": surprise,
                "label": label,
            })
        if not quarters:
            return out
        graded = [q for q in quarters if q["label"]]
        n_beat = sum(1 for q in graded if q["label"] == "beat")
        n_miss = sum(1 for q in graded if q["label"] == "miss")
        surprises = [q["surprise_pct"] for q in graded if q["surprise_pct"] is not None]
        # Consecutive beat streak from the most recent quarter.
        streak = 0
        for q in graded:
            if q["label"] == "beat":
                streak += 1
            else:
                break
        out = {
            "available": True,
            "quarters": quarters,
            "n_reported": len(graded),
            "n_beat": n_beat,
            "n_miss": n_miss,
            "beat_rate": (n_beat / len(graded)) if graded else None,
            "avg_surprise_pct": (sum(surprises) / len(surprises)) if surprises else None,
            "beat_streak": streak,
            "source": "FMP earnings (EPS actual vs consensus)",
        }
    except Exception:
        return {"available": False, "quarters": []}
    return out


# ── 2. Ratings consolidation ────────────────────────────────────────────────

def build_ratings_consolidation(ticker: str) -> Dict[str, Any]:
    """Sell-side rating panel: consensus distribution + recent firm actions +
    price-target summary. Independent-research houses left as a (None) slot."""
    out: Dict[str, Any] = {"available": False}
    try:
        from aletheia.data import fmp_client
        cons = fmp_client.fetch_grades_consensus(ticker) or {}
        grades = fmp_client.fetch_grades(ticker) or []
        pt = fmp_client.fetch_price_target_summary(ticker) or {}
        ptc = fmp_client.fetch_price_target_consensus(ticker) or {}

        # Recent individual firm actions (most-recent first, de-dup per firm).
        recent, seen = [], set()
        for g in grades:
            firm = g.get("gradingCompany")
            if not firm or firm in seen:
                continue
            seen.add(firm)
            recent.append({
                "firm": firm,
                "action": g.get("action"),            # upgrade / downgrade / maintain / initiate
                "grade": g.get("newGrade"),
                "from": g.get("previousGrade"),
                "date": g.get("date"),
            })
            if len(recent) >= 8:
                break
        n_up = sum(1 for g in grades[:30] if (g.get("action") or "").lower() == "upgrade")
        n_down = sum(1 for g in grades[:30] if (g.get("action") or "").lower() == "downgrade")

        dist = {k: cons.get(k) for k in ("strongBuy", "buy", "hold", "sell", "strongSell")
                if cons.get(k) is not None}
        out = {
            "available": bool(dist or recent or pt),
            "consensus": cons.get("consensus"),
            "distribution": dist or None,
            "recent_actions": recent,
            "recent_upgrades_30": n_up,
            "recent_downgrades_30": n_down,
            "price_target": {
                "avg": _f(pt.get("lastQuarterAvgPriceTarget")) or _f(pt.get("lastYearAvgPriceTarget")),
                "high": _f(ptc.get("targetHigh")),
                "low": _f(ptc.get("targetLow")),
                "n_analysts": pt.get("lastQuarterCount") or pt.get("lastYearCount"),
            } if (pt or ptc) else None,
            # Slot for independent-research houses (CFRA/Morningstar/Argus/Market
            # Edge) — populate when a licensed feed is connected.
            "independent_research": None,
            "source": "FMP sell-side grades + price targets",
            "note": ("Sell-side analyst data. Independent-research ratings "
                     "(CFRA/Morningstar/Argus/Market Edge) require a separate "
                     "licensed feed not currently connected."),
        }
    except Exception:
        return {"available": False}
    return out


# ── 3. ESG (placeholder — renders when a feed is connected) ──────────────────

def build_esg(ticker: str) -> Dict[str, Any]:
    """ESG ratings. No licensed ESG feed (MSCI/Sustainalytics) is connected;
    structure is ready so a future feed populates rating/score directly."""
    return {
        "available": False,
        "msci_rating": None,        # e.g. "AA"
        "sustainalytics_risk": None,  # e.g. 21.4 (lower = lower risk)
        "providers": ["MSCI", "Sustainalytics"],
        "note": "No licensed ESG feed connected — institutional ESG context "
                "(MSCI rating / Sustainalytics risk) will render here once a "
                "feed is wired.",
    }


# ── 4. Recent material news ─────────────────────────────────────────────────

def build_recent_news(ticker: str, *, days: int = 90, top: int = 5,
                      as_of: Optional[datetime.date] = None) -> Dict[str, Any]:
    """Top company headlines in the trailing ``days`` window, as dated items."""
    out: Dict[str, Any] = {"available": False, "items": []}
    try:
        from aletheia.data import fmp_client
        rows = fmp_client.fetch_stock_news(ticker, limit=40) or []
        ref = as_of or datetime.date.today()
        cutoff = ref - datetime.timedelta(days=days)
        items: List[Dict[str, Any]] = []
        for r in rows:
            ds = str(r.get("publishedDate") or "")[:10]
            try:
                d = datetime.date.fromisoformat(ds)
            except ValueError:
                continue
            if d < cutoff:
                continue
            items.append({
                "date": ds,
                "title": (r.get("title") or "").strip(),
                "publisher": r.get("publisher") or r.get("site") or "",
                "url": r.get("url") or "",
            })
            if len(items) >= top:
                break
        out = {
            "available": bool(items),
            "items": items,
            "window_days": days,
            "source": "FMP stock news",
        }
    except Exception:
        return {"available": False, "items": []}
    return out


# ── Composer ─────────────────────────────────────────────────────────────────

def compose_market_context(ticker: str) -> Dict[str, Any]:
    """One-call market-context bundle for the /dcf payload + report rebuild."""
    return {
        "earnings_surprises": build_earnings_surprises(ticker),
        "ratings": build_ratings_consolidation(ticker),
        "esg": build_esg(ticker),
        "news": build_recent_news(ticker),
    }


__all__ = [
    "build_earnings_surprises", "build_ratings_consolidation",
    "build_esg", "build_recent_news", "compose_market_context",
]
