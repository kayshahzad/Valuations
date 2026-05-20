"""Compare contrarian A/B outputs (with-search vs no-search).

Loads `tests/quality_gate/contrarian_ab/{T}.json` for each ticker and
computes:

  1. Bias-category match     — same / different
  2. Sentiment-score delta   — points
  3. Bear-case length delta  — chars + percent
  4. Proper-noun overlap     — entities cited only with search
  5. Recency markers         — date references / "recent", "Q4 2024" etc.
                               that would only come from web search

Decision rule: if no axis differs by ≥10% AND bias matches AND sentiment
matches across all 9 tickers, drop the search entirely.

Run with:
    PYTHONPATH=. python scripts/compare_contrarian_ab.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Dict, List


AB_DIR      = Path("tests/quality_gate/contrarian_ab")
REPORT_PATH = Path("tests/quality_gate/contrarian_ab_report.md")


_PROPER_NOUN_RE = re.compile(r"\b[A-Z][a-zA-Z]{2,}(?:\s+[A-Z][a-zA-Z]{2,})*")
# Date / recency markers that are LIKELY web-search-driven (not from the
# 10-K text or the structured quant_challenge). FY2024 / Q3 2024 / "in
# 2024" / "recently" / "this quarter" etc.
_RECENCY_RE = re.compile(
    r"\b(?:Q[1-4]\s*20\d{2}|FY\s*20\d{2}|in\s+20\d{2}|recently|this\s+quarter|"
    r"last\s+quarter|past\s+(?:month|quarter|year)|earnings\s+call|guidance|"
    r"reported|disclosed)\b",
    re.IGNORECASE,
)


def _proper_nouns(text: str) -> set[str]:
    if not text:
        return set()
    return set(m.lower() for m in _PROPER_NOUN_RE.findall(text))


def _recency_markers(text: str) -> List[str]:
    if not text:
        return []
    return _RECENCY_RE.findall(text)


def compare_ticker(path: Path) -> Dict[str, Any]:
    d = json.loads(path.read_text())
    ticker = d["ticker"]
    w = d["with_search"]
    n = d["no_search"]

    w_bear = w.get("bear_case_summary", "") or ""
    n_bear = n.get("bear_case_summary", "") or ""

    # Length
    w_chars = len(w_bear)
    n_chars = len(n_bear)
    delta_pct = (n_chars - w_chars) / max(1, w_chars) * 100

    # Bias / sentiment
    bias_match = (w.get("bias_detected") == n.get("bias_detected"))
    sentiment_delta = (n.get("sentiment_score", 0) or 0) - (w.get("sentiment_score", 0) or 0)

    # Proper-noun coverage
    w_pns = _proper_nouns(w_bear)
    n_pns = _proper_nouns(n_bear)
    only_with_search = w_pns - n_pns
    only_without     = n_pns - w_pns
    shared           = w_pns & n_pns

    # Recency markers
    w_recency = _recency_markers(w_bear)
    n_recency = _recency_markers(n_bear)

    return {
        "ticker":          ticker,
        "w_chars":         w_chars,
        "n_chars":         n_chars,
        "delta_pct":       round(delta_pct, 1),
        "bias_match":      bias_match,
        "sentiment_delta": sentiment_delta,
        "shared_pns":      len(shared),
        "only_with":       len(only_with_search),
        "only_without":    len(only_without),
        "only_with_examples":   sorted(only_with_search)[:5],
        "only_without_examples": sorted(only_without)[:5],
        "w_recency":       len(w_recency),
        "n_recency":       len(n_recency),
        "w_search_chars":  w.get("raw_web_results_chars", 0),
    }


def main() -> None:
    rows = []
    for path in sorted(AB_DIR.glob("*.json")):
        rows.append(compare_ticker(path))

    if not rows:
        print(f"No A/B outputs in {AB_DIR}")
        return

    # Console summary
    print("\nContrarian A/B — with-search vs no-search\n")
    print(f"{'Ticker':7s} {'w_chars':>8s} {'n_chars':>8s} {'Δ%':>6s} "
          f"{'bias=':>6s} {'sentΔ':>6s} {'sharedPN':>9s} {'wOnly':>6s} {'nOnly':>6s} "
          f"{'wRec':>5s} {'nRec':>5s}")
    print("-" * 90)
    for r in rows:
        print(
            f"{r['ticker']:7s} {r['w_chars']:>8d} {r['n_chars']:>8d} "
            f"{r['delta_pct']:>+6.1f} "
            f"{('Y' if r['bias_match'] else 'N'):>6s} "
            f"{r['sentiment_delta']:>+6d} "
            f"{r['shared_pns']:>9d} {r['only_with']:>6d} {r['only_without']:>6d} "
            f"{r['w_recency']:>5d} {r['n_recency']:>5d}"
        )

    # Aggregate verdict
    n_match_bias = sum(1 for r in rows if r["bias_match"])
    n_zero_sent = sum(1 for r in rows if r["sentiment_delta"] == 0)
    avg_delta_pct = sum(abs(r["delta_pct"]) for r in rows) / len(rows)
    max_delta_pct = max(abs(r["delta_pct"]) for r in rows)
    avg_w_only = sum(r["only_with"] for r in rows) / len(rows)
    avg_recency_w = sum(r["w_recency"] for r in rows) / len(rows)
    avg_recency_n = sum(r["n_recency"] for r in rows) / len(rows)

    print()
    print("Aggregate")
    print(f"  Bias-category match:     {n_match_bias}/{len(rows)}")
    print(f"  Sentiment-score equal:   {n_zero_sent}/{len(rows)}")
    print(f"  Avg |Δ chars %|:         {avg_delta_pct:.1f}%")
    print(f"  Max |Δ chars %|:         {max_delta_pct:.1f}%")
    print(f"  Avg proper-nouns only-with-search: {avg_w_only:.1f}")
    print(f"  Avg recency markers (with):    {avg_recency_w:.1f}")
    print(f"  Avg recency markers (without): {avg_recency_n:.1f}")

    # Markdown report
    out: List[str] = []
    out.append("# Contrarian A/B — Web Search Value Test")
    out.append("")
    out.append("Each ticker run twice with identical upstream state; only the contrarian "
               "DuckDuckGo web-search query differs. Comparison axes:")
    out.append("")
    out.append("| Axis | Δ ≥ 10% triggers \"keep search\" |")
    out.append("|---|---|")
    out.append("| Bear-case length | n_chars vs w_chars |")
    out.append("| Bias-category    | match required for \"drop search\" |")
    out.append("| Sentiment score  | match required for \"drop search\" |")
    out.append("| Proper nouns only-with-search | named entities only the search produced |")
    out.append("| Recency markers  | dates / events that imply live data |")
    out.append("")
    out.append("## Per-ticker")
    out.append("")
    out.append("| Ticker | w_chars | n_chars | Δ% | bias= | sentΔ | sharedPN | wOnly | nOnly | wRec | nRec |")
    out.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        out.append(
            f"| {r['ticker']} | {r['w_chars']} | {r['n_chars']} | {r['delta_pct']:+.1f}% | "
            f"{'✓' if r['bias_match'] else '✗'} | {r['sentiment_delta']:+d} | "
            f"{r['shared_pns']} | {r['only_with']} | {r['only_without']} | "
            f"{r['w_recency']} | {r['n_recency']} |"
        )
    out.append("")
    out.append("## Aggregate")
    out.append("")
    out.append(f"- Bias-category match: **{n_match_bias}/{len(rows)}**")
    out.append(f"- Sentiment-score equal: **{n_zero_sent}/{len(rows)}**")
    out.append(f"- Avg |Δ bear-case chars|: **{avg_delta_pct:.1f}%**")
    out.append(f"- Max |Δ bear-case chars|: **{max_delta_pct:.1f}%**")
    out.append(f"- Avg unique proper-nouns surfaced ONLY by the search: **{avg_w_only:.1f}**")
    out.append(f"- Avg recency-marker count (with): **{avg_recency_w:.1f}** vs (without): **{avg_recency_n:.1f}**")
    out.append("")
    out.append("## Per-ticker proper-noun deltas (samples)")
    out.append("")
    for r in rows:
        if r["only_with_examples"] or r["only_without_examples"]:
            out.append(f"### {r['ticker']}")
            if r["only_with_examples"]:
                out.append(f"  - Only-with-search proper nouns: {r['only_with_examples']}")
            if r["only_without_examples"]:
                out.append(f"  - Only-without-search proper nouns: {r['only_without_examples']}")
            out.append("")

    REPORT_PATH.write_text("\n".join(out))
    print(f"\nReport: {REPORT_PATH}")


if __name__ == "__main__":
    main()
