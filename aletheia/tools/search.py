"""
aletheia/tools/search.py

Lightweight web-search helper used by the forensic / value_chain / context
agents to attach qualitative context (competitor moves, supplier stress,
patent risk, etc.) to their reports.

Implementation: pure-Python DuckDuckGo HTML scrape via `requests`. No external
API key, no native dependencies, no provider account. Real search results.

Behavior:
- search_sentiment(query, max_results=5) returns a dict shaped:
    {
        "query": str,
        "snippets": [{"title": str, "body": str, "href": str}, ...],
        "n_results": int,
        "sentiment": "neutral",      # placeholder — agents do their own scoring
        "source": "duckduckgo",
    }
- All three agent call sites wrap this in `try/except: pass` so any failure
  (network, rate-limit, parser drift) silently degrades to empty context
  rather than blocking the pipeline.
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import requests

logger = logging.getLogger(__name__)

_DDG_HTML_URL = "https://html.duckduckgo.com/html/"
_TIMEOUT_S = 6.0
_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_5) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)

# DuckDuckGo HTML result row pattern. Captures title, href, and body snippet.
# Tolerant to minor markup drift; the regex matches the result-link/snippet
# pair as it has appeared on the html.duckduckgo.com endpoint for years.
_RESULT_RE = re.compile(
    r'<a\s+rel="nofollow"\s+class="result__a"\s+href="([^"]+)"[^>]*>(.*?)</a>'
    r'.*?<a\s+class="result__snippet"[^>]*>(.*?)</a>',
    re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags(s: str) -> str:
    return _TAG_RE.sub("", s).strip()


def search_sentiment(query: str, max_results: int = 5) -> Dict[str, Any]:
    """
    Run a web search and return a normalized snippet bundle for agent context.

    Returns the empty-result shape on any error so callers (already wrapped in
    try/except in the agents) keep flowing.
    """
    empty: Dict[str, Any] = {
        "query": query,
        "snippets": [],
        "n_results": 0,
        "sentiment": "neutral",
        "source": "duckduckgo",
    }

    if not query or not isinstance(query, str):
        return empty

    try:
        resp = requests.post(
            _DDG_HTML_URL,
            data={"q": query, "kl": "us-en"},
            headers={"User-Agent": _USER_AGENT},
            timeout=_TIMEOUT_S,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.info("search_sentiment: DuckDuckGo request failed (%s)", e)
        return empty

    snippets: List[Dict[str, str]] = []
    for m in _RESULT_RE.finditer(resp.text):
        href, title_html, body_html = m.group(1), m.group(2), m.group(3)
        title = _strip_tags(title_html)
        body = _strip_tags(body_html)
        if not title and not body:
            continue
        snippets.append({"title": title, "body": body, "href": href})
        if len(snippets) >= max_results:
            break

    return {
        "query": query,
        "snippets": snippets,
        "n_results": len(snippets),
        "sentiment": "neutral",
        "source": "duckduckgo",
    }
