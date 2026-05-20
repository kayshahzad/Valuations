"""Librarian agent — fetches SEC filing text into LangGraph state.

Two filings consumed downstream:

  - **10-K** (Item 1 + Item 1A) → ``state["raw_10k_text"]`` for
    qualitative_synthesis_agent (narrative) + qualitative_extraction
    bundle Phase B (3 LLM_AUGMENTED dims).
  - **DEF 14A** (truncated proxy statement) → ``state["raw_def14a_text"]``
    for qualitative_extraction bundle Phase C (2 management dims).

Filing bodies are persisted into ``valuation_data/raw/sec/filings/``
keyed by ticker + accession number. Idempotent — re-runs check the
cache before hitting SEC. Three benefits:

  1. **Audit trail**: ``cat valuation_data/raw/sec/filings/AAPL/10K_*.md``
     shows exactly what the LLM saw.
  2. **Cost / latency**: re-runs of the same filing skip the SEC
     download + edgartools' filing-body parse.
  3. **Rate-limit insulation**: SEC enforces ~10 req/sec; the cache
     means a universe sweep on already-seen filings spends only the
     small "filings list" lookups, not full body downloads.

Cache invalidation is by accession number — when SEC issues a new
filing (next fiscal year), the next run picks up the new accession
and writes a fresh cache file alongside the old one. Old files are
kept for historical lookup.
"""

from pathlib import Path
from typing import Tuple

from langchain_core.messages import HumanMessage
from edgar import set_identity, Company
from config import SEC_IDENTITY
from aletheia.utils.tracing import tracer


# Truncation budgets — both filings can be huge (10-K easily 200K+
# chars; DEF 14A 400K+). We send only the leading sections that
# contain the analytical content we need. Matches the existing 10-K
# behavior; DEF 14A budget chosen to capture board structure +
# ownership + CD&A summary (typically in the first 80K).
_TEN_K_ITEM_BUDGET = 40000        # per-item (Item 1 + Item 1A)
_TEN_K_FALLBACK_BUDGET = 80000    # when per-item extraction fails
_DEF14A_CHAR_BUDGET = 80000

# Filing cache root — one subdirectory per ticker, one file per
# accession number. Same directory tree as other SEC raw data.
_FILINGS_CACHE_ROOT = Path("valuation_data/raw/sec/filings")


def _cache_path(ticker: str, form_label: str, accession: str) -> Path:
    """Map (ticker, form, accession) → cache file path.

    ``form_label`` is the short form used in the filename
    ("10K" / "DEF14A" — no spaces). ``accession`` is the SEC's
    canonical 18-char dash-separated identifier (e.g.
    "0001308179-26-000008") used as the cache key.
    """
    safe_accession = accession.replace("/", "_")
    return _FILINGS_CACHE_ROOT / ticker / f"{form_label}_{safe_accession}.md"


def _load_or_fetch_10k(filings, ticker: str) -> Tuple[str, str]:
    """Return ``(text, source_label)`` for the latest 10-K.

    Cache layout: ``raw/sec/filings/{TICKER}/10K_{accession}.md``.
    The cache stores the formatted-for-LLM string (concatenated
    Item 1 + Item 1A or the fallback truncated markdown), not the
    raw filing — saves a re-parse on every cache hit.
    """
    latest_10k = filings[0]
    accession = latest_10k.accession_no
    cache_path = _cache_path(ticker, "10K", accession)

    if cache_path.exists():
        try:
            text = cache_path.read_text(encoding="utf-8")
            print(f"  ✓ 10-K cache hit: {cache_path.name} "
                  f"({len(text):,} chars)")
            return text, "cache"
        except OSError as e:
            print(f"  ⚠ 10-K cache read failed ({e}); refetching")

    # Cache miss → fetch from edgartools, then write
    try:
        doc    = latest_10k.obj()
        item1  = str(doc['Item 1'])[:_TEN_K_ITEM_BUDGET]  if 'Item 1'  in doc else ""
        item1a = str(doc['Item 1A'])[:_TEN_K_ITEM_BUDGET] if 'Item 1A' in doc else ""
        text = f"ITEM 1: BUSINESS\n{item1}\n\nITEM 1A: RISK FACTORS\n{item1a}"
    except Exception:
        # Per-item extraction failed (older filings, foreign issuers).
        # Fall back to truncated full markdown.
        text = (latest_10k.markdown() or "")[:_TEN_K_FALLBACK_BUDGET]

    _write_cache(cache_path, text, ticker, "10-K")
    print(f"  ✓ 10-K fetched: accession={accession} ({len(text):,} chars)")
    return text, "fetched"


def _load_or_fetch_def14a(proxy_filings, ticker: str) -> Tuple[str, str]:
    """Return ``(text, source_label)`` for the latest DEF 14A.

    Cache layout: ``raw/sec/filings/{TICKER}/DEF14A_{accession}.md``.
    Stores the truncated text (first 80K of markdown), not the full
    proxy — matches the budget used downstream.
    """
    latest_proxy = proxy_filings[0]
    accession = latest_proxy.accession_no
    cache_path = _cache_path(ticker, "DEF14A", accession)

    if cache_path.exists():
        try:
            text = cache_path.read_text(encoding="utf-8")
            print(f"  ✓ DEF 14A cache hit: {cache_path.name} "
                  f"({len(text):,} chars)")
            return text, "cache"
        except OSError as e:
            print(f"  ⚠ DEF 14A cache read failed ({e}); refetching")

    proxy_md = latest_proxy.markdown() or ""
    text = proxy_md[:_DEF14A_CHAR_BUDGET]
    _write_cache(cache_path, text, ticker, "DEF 14A")
    print(f"  ✓ DEF 14A fetched: accession={accession} "
          f"({len(proxy_md):,} chars total, cached first "
          f"{len(text):,} for extraction)")
    return text, "fetched"


def _write_cache(path: Path, text: str, ticker: str, form_label: str) -> None:
    """Best-effort cache write — failure logs but doesn't propagate.
    The Stage 4 run completes regardless; only re-run latency is hurt
    by a write failure."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    except OSError as e:
        print(f"  ⚠ {form_label} cache write failed for {ticker}: {e}")


def librarian_agent(state):
    print("---LIBRARIAN AGENT (Live SEC Data)---")
    ticker = state["ticker"]

    # Set Identity for SEC
    set_identity(SEC_IDENTITY)

    try:
        company = Company(ticker)

        # ── 10-K Content (with persistent cache) ────────────────────
        raw_text = "Data unavailable."
        try:
            filings = company.get_filings(form="10-K")
            if filings:
                raw_text, _ = _load_or_fetch_10k(filings, ticker)
        except Exception as sec_e:
            print(f"SEC Error (10-K): {sec_e}")
            raw_text = f"SEC Fetch Failed: {sec_e}"

        # ── DEF 14A Content (Phase C, with persistent cache) ───────
        def14a_text = ""
        try:
            proxy_filings = company.get_filings(form="DEF 14A")
            if proxy_filings:
                def14a_text, _ = _load_or_fetch_def14a(proxy_filings, ticker)
            else:
                print(f"  ⚠ No DEF 14A filings found for {ticker}")
        except Exception as proxy_e:
            print(f"SEC Error (DEF 14A): {proxy_e}")

        output = {
            "raw_10k_text":    raw_text,
            "raw_def14a_text": def14a_text,
            "messages": [HumanMessage(
                content=f"Librarian: Retrieved 10-K + DEF 14A text for {ticker}."
            )]
        }
        tracer.log_step("Librarian", state, output)
        return output
    except Exception as e:
        error_output = {
            "messages": [HumanMessage(content=f"Librarian: Failed to retrieve data for {ticker}. Error: {e}")]
        }
        tracer.log_step("Librarian", state, error_output)
        return error_output
