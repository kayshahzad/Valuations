"""Current-State events agent (Source 2) — LLM + Google Search grounding.

Pulls material events from the last ~180 days (guidance changes, clinical /
regulatory readouts, competitive moves, pricing/legal actions, capital &
management events) for a ticker, classified and materiality-scored, from
credible sources only. This is the piece that would have surfaced NVO's
CagriSema failure, the MFN pricing deal, and the LLY share loss.

Cost discipline: a grounded search is a paid LLM call, so results are cached
to disk (7-day TTL). Page loads read the cache (free); the live grounded
fetch runs only on an explicit refresh (``fetch_events(..., force=True)`` /
a UI button / Stage 4). Returns ``[]`` (never raises) when no API key, no
``google.genai``, or the search/parse fails — the consensus-based
reconciliation still works without events.

Output events match the shape ``current_state.build_current_state`` consumes:
``{date, category, headline, materiality, source, impact}`` where ``category``
is one of the adverse slugs the reconciler understands
(guidance_cut, clinical_failure, competitive, pricing_regulatory,
regulatory_legal, capital, management).
"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

_CACHE_DIR = Path("valuation_data/macro/current_state_events")
_TTL_SECONDS = 7 * 24 * 3600

_CATEGORIES = (
    "guidance_cut", "clinical_failure", "competitive",
    "pricing_regulatory", "regulatory_legal", "capital", "management",
)

_PROMPT = """You are an equity research analyst. Using web search, find the \
MATERIAL events for {company} (ticker {ticker}{sector_clause}) from the LAST \
180 DAYS that would change forward expectations.

Only include events from CREDIBLE sources: company press releases, SEC/regulator \
filings, Reuters, Bloomberg, FT, WSJ, or the company's official IR. Do NOT use \
social media, forums, or retail-trading sites. Ignore routine earnings calls \
with no guidance change, single analyst rating changes, and generic macro news.

Classify each event into exactly one category:
- guidance_cut        (forward guidance revised down / withdrawn)
- clinical_failure    (failed/negative trial, FDA rejection, label restriction) — also use for positive readouts that HELP a COMPETITOR
- competitive         (competitor launch/approval/trial win in the same class, market-share shift)
- pricing_regulatory  (government pricing action: MFN, IRA negotiation, formulary/coverage change)
- regulatory_legal    (antitrust, major litigation, investigation)
- capital             (major M&A, refinancing, buyback/dividend change)
- management          (C-suite change, >5% layoffs, strategic restructuring)

Score materiality 1-5 (5 = changes the thesis; 1 = minor). Only return events \
with materiality >= 3.

For EACH event set "direction" relative to {company}'s value:
- "adverse"   = hurts {company} (its guidance cut, its trial failed, a rival won, a price cut on its drug)
- "favorable" = helps {company} (its trial succeeded, its drug approved, it gained share, favorable coverage)
- "neutral"   = ambiguous / informational
A competitor's win in the same class is ADVERSE to {company}; {company}'s own \
approval/trial win is FAVORABLE.

Return ONLY a JSON array (no prose), each item:
{{"date":"YYYY-MM-DD","category":"<slug>","direction":"adverse|favorable|neutral","headline":"...","materiality":<1-5>,"source":"<publisher>","impact":"which assumption it affects"}}
If there are no material events, return []."""


def _cache_path(ticker: str) -> Path:
    return _CACHE_DIR / f"{ticker.upper()}.json"


def _read_cache(ticker: str) -> Optional[List[Dict[str, Any]]]:
    p = _cache_path(ticker)
    if not p.exists():
        return None
    try:
        blob = json.loads(p.read_text())
        if time.time() - blob.get("fetched_at", 0) > _TTL_SECONDS:
            return None
        return blob.get("events") or []
    except Exception:
        return None


def _write_cache(ticker: str, events: List[Dict[str, Any]]) -> None:
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _cache_path(ticker).write_text(json.dumps(
            {"fetched_at": time.time(), "events": events}, indent=2))
    except Exception:
        pass


def _parse_events(text: str) -> List[Dict[str, Any]]:
    """Extract the JSON array from the grounded response."""
    if not text:
        return []
    m = re.search(r"\[.*\]", text, re.DOTALL)
    if not m:
        return []
    try:
        raw = json.loads(m.group(0))
    except Exception:
        return []
    out = []
    for e in raw if isinstance(raw, list) else []:
        if not isinstance(e, dict):
            continue
        cat = str(e.get("category", "")).lower().strip()
        try:
            mat = int(e.get("materiality") or 0)
        except (TypeError, ValueError):
            mat = 0
        direction = str(e.get("direction", "")).lower().strip()
        if direction not in ("adverse", "favorable", "neutral"):
            # Default by category when the model omits direction: clearly-bad
            # categories are adverse; the rest neutral.
            direction = ("adverse"
                         if cat in ("guidance_cut", "clinical_failure", "regulatory_legal")
                         else "neutral")
        if cat in _CATEGORIES and mat >= 3 and e.get("headline"):
            out.append({
                "date": str(e.get("date", "")),
                "category": cat,
                "direction": direction,
                "headline": str(e.get("headline", ""))[:280],
                "materiality": max(1, min(5, mat)),
                "source": str(e.get("source", ""))[:120],
                "impact": str(e.get("impact", ""))[:200],
            })
    return out


def _grounded_fetch(ticker: str, company: str, sector: str) -> List[Dict[str, Any]]:
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        return []
    try:
        from google import genai
        from google.genai import types
        from config import MODEL_NAME
    except Exception:
        return []
    sector_clause = f", {sector} sector" if sector else ""
    prompt = _PROMPT.format(company=company or ticker, ticker=ticker,
                            sector_clause=sector_clause)
    try:
        client = genai.Client(api_key=api_key)
        resp = client.models.generate_content(
            model=MODEL_NAME,
            contents=prompt,
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                temperature=0.1,
            ),
        )
        return _parse_events(getattr(resp, "text", "") or "")
    except Exception as exc:
        print(f"  ⚠ current_state_events: grounded fetch failed for {ticker}: "
              f"{type(exc).__name__}: {exc}")
        return []


def fetch_events(
    ticker: str, company: str = "", sector: str = "", *, force: bool = False,
) -> List[Dict[str, Any]]:
    """Material last-180-day events for a ticker.

    Reads the 7-day disk cache unless ``force=True`` (which runs the paid
    grounded search and refreshes the cache). Returns ``[]`` on any failure.
    """
    if not force:
        cached = _read_cache(ticker)
        if cached is not None:
            return cached
    events = _grounded_fetch(ticker, company, sector)
    _write_cache(ticker, events)
    return events


def cached_events(ticker: str) -> List[Dict[str, Any]]:
    """Cache-only read (no LLM call) — for page loads."""
    return _read_cache(ticker) or []


__all__ = ["fetch_events", "cached_events"]
