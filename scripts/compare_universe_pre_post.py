"""Compare pre- vs post-Phase-1.7 universe traces.

For each ticker that has both an OLD and NEW modern-pipeline trace in
audits/, compares:
  1. Value distributions per field (was the LLM over-optimistic before?)
  2. Prose-anchoring rates per field (did the citation fix surface
     substitution_pressure / concentration_risk / growth_quality more?)
  3. Bear-case structural-risk citation presence (the specific lift
     Option 3 was meant to produce)

Pre = newest trace from BEFORE the Phase 1.7 commit timestamp.
Post = newest trace at or after that timestamp.

Output is a markdown report to stdout — easy to drop into a comment or
commit message.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _phase17_commit_unix_time() -> int:
    """Unix timestamp of the Phase 1.7 commit, used as pre/post divider."""
    out = subprocess.check_output(
        ["git", "log", "--format=%ct", "-1",
         "--grep=Phase 1.7: elicit orphaned categorical fields"],
        text=True,
    ).strip()
    if not out:
        # Fall back: search for the introducing commit
        out = subprocess.check_output(
            ["git", "log", "--format=%ct", "-1", "--all"],
            text=True,
        ).strip().split("\n")[0]
    return int(out)


def _load_trace_records() -> dict[str, dict]:
    """Walk audits/ once. Per ticker, return {pre, post} record dicts."""
    cutoff = _phase17_commit_unix_time()
    print(f"# Pre/Post cutoff = unix {cutoff}", file=sys.stderr)

    by_ticker: dict[str, dict] = {}
    for fname in os.listdir("audits"):
        m = re.match(r"trace_([A-Z\.\-]+)_(\d+)\.json", fname)
        if not m:
            continue
        ticker, ts = m.group(1), int(m.group(2))
        try:
            with open(f"audits/{fname}") as f:
                trace = json.load(f)
        except Exception:
            continue
        agents = {e.get("agent") for e in trace if isinstance(e, dict)}
        if "QualitativeSynthesis" not in agents or "ThesisSynthesizer" not in agents:
            continue
        rec = _extract_record(trace)
        if not rec:
            continue
        rec["_ts"] = ts
        bucket = "post" if ts >= cutoff else "pre"
        slot = by_ticker.setdefault(ticker, {})
        existing = slot.get(bucket)
        if existing is None or existing["_ts"] < ts:
            slot[bucket] = rec
    return by_ticker


def _extract_record(trace: list[dict]) -> dict | None:
    qs = next((e for e in trace if isinstance(e, dict) and e.get("agent") == "QualitativeSynthesis"), None)
    ts = next((e for e in trace if isinstance(e, dict) and e.get("agent") == "ThesisSynthesizer"), None)
    if not (qs and ts):
        return None
    qs_d = qs.get("output_delta") or {}
    fr = qs_d.get("forensic_report") or {}
    vc = qs_d.get("value_chain_report") or {}
    sc = qs_d.get("strategic_context_report") or {}
    ts_d = (ts.get("output_delta") or {}).get("thesis_synthesis") or {}

    parts = [ts_d.get("thesis_statement", "")]
    bear_signals = []
    for case in ("bull_case", "bear_case", "base_case"):
        c = ts_d.get(case) or {}
        if isinstance(c, dict):
            parts.append(c.get("claim", ""))
            if case == "bear_case":
                bear_signals = c.get("cited_signals") or []
    parts.append(ts_d.get("position_sizing_implications", ""))
    for k in ("required_analyst_judgment", "update_conditions"):
        v = ts_d.get(k) or []
        if isinstance(v, list):
            parts.extend(str(x) for x in v)

    return {
        "moat_score":            fr.get("moat_score"),
        "concentration_risk":    fr.get("concentration_risk"),
        "strategic_position":    vc.get("strategic_position"),
        "substitution_pressure": vc.get("substitution_pressure"),
        "growth_quality":        sc.get("growth_quality"),
        "prose":                 " ".join(parts),
        "bear_cited_signals":    bear_signals,
        "thesis_statement":      ts_d.get("thesis_statement", ""),
    }


# ── Anchor helpers ───────────────────────────────────────────────────────────

TOPIC_PATTERNS = {
    "moat_score":            r"\bmoat\b",
    "strategic_position":    r"\b(strategic[_ ]position|market[_ ]position|competitive[_ ]position|positioning|dominant|dominance|weak\s+market)\b",
    "substitution_pressure": r"\bsubstitut",
    "growth_quality":        r"\bgrowth[_ ]quality\b|\bquality[_ ]of[_ ]growth\b|\bdeferred[_ ]revenue\b|\bD8\b",
    "concentration_risk":    r"\bconcentration\b",
}


def _anchor_near_topic(value, prose: str, topic_pat: str, window: int = 80) -> bool:
    if value is None:
        return False
    if value == "uncertain":
        return False
    if isinstance(value, bool):
        return bool(re.search(topic_pat, prose, flags=re.I))
    if isinstance(value, (int, float)):
        for tm in re.finditer(topic_pat, prose, flags=re.I):
            lo, hi = max(0, tm.start() - window), tm.end() + window
            if re.search(rf"\b{value:g}\b", prose[lo:hi]):
                return True
        return False
    for tm in re.finditer(topic_pat, prose, flags=re.I):
        lo, hi = max(0, tm.start() - window), tm.end() + window
        if re.search(rf"\b{re.escape(str(value))}\b", prose[lo:hi], flags=re.I):
            return True
    return False


# ── Comparison report ────────────────────────────────────────────────────────

def _fmt_count(c: Counter, top: int = 6) -> str:
    return ", ".join(f"{k}={v}" for k, v in c.most_common(top))


def main() -> int:
    by_ticker = _load_trace_records()
    paired = {t: r for t, r in by_ticker.items() if "pre" in r and "post" in r}
    pre_only = [t for t, r in by_ticker.items() if "pre" in r and "post" not in r]
    post_only = [t for t, r in by_ticker.items() if "post" in r and "pre" not in r]

    print("# Universe Re-Run Comparison: pre-Phase-1.7 vs post")
    print()
    print(f"- Tickers with paired pre+post: **{len(paired)}**")
    print(f"- Pre-only (post run failed or missing): {len(pre_only)} — {pre_only}")
    print(f"- Post-only (new ticker, no pre baseline): {len(post_only)} — {post_only}")
    print()

    # 1. Value distribution shift
    print("## 1. Value distribution shift per field")
    print()
    print("| Field | Pre distribution | Post distribution |")
    print("|---|---|---|")
    for field in ("moat_score", "strategic_position", "substitution_pressure",
                  "growth_quality", "concentration_risk"):
        pre_vals = [r["pre"][field] for r in paired.values() if r["pre"].get(field) is not None]
        post_vals = [r["post"][field] for r in paired.values() if r["post"].get(field) is not None]
        # Numeric: round to 1 decimal for distribution
        if pre_vals and isinstance(pre_vals[0], (int, float)) and not isinstance(pre_vals[0], bool):
            pre_c = Counter(round(v, 1) for v in pre_vals)
            post_c = Counter(round(v, 1) for v in post_vals)
        else:
            pre_c = Counter(pre_vals)
            post_c = Counter(post_vals)
        print(f"| `{field}` | {_fmt_count(pre_c)} | {_fmt_count(post_c)} |")
    print()

    # 2. Prose-anchoring rates
    print("## 2. Prose-anchoring rate per field (value cited within 80 chars of topic)")
    print()
    print("| Field | Pre anchored | Post anchored | Δ |")
    print("|---|---|---|---|")
    for field in ("moat_score", "strategic_position", "substitution_pressure",
                  "growth_quality", "concentration_risk"):
        n_pre = n_post = pre_anchor = post_anchor = 0
        for t, r in paired.items():
            pv = r["pre"].get(field)
            if pv is not None:
                n_pre += 1
                if _anchor_near_topic(pv, r["pre"]["prose"], TOPIC_PATTERNS[field]):
                    pre_anchor += 1
            qv = r["post"].get(field)
            if qv is not None:
                n_post += 1
                if _anchor_near_topic(qv, r["post"]["prose"], TOPIC_PATTERNS[field]):
                    post_anchor += 1
        pre_pct = 100 * pre_anchor / n_pre if n_pre else 0
        post_pct = 100 * post_anchor / n_post if n_post else 0
        delta = post_pct - pre_pct
        sign = "+" if delta >= 0 else ""
        print(f"| `{field}` | {pre_anchor}/{n_pre} ({pre_pct:.1f}%) | "
              f"{post_anchor}/{n_post} ({post_pct:.1f}%) | {sign}{delta:.1f}pp |")
    print()

    # 3. Bear-case citation lift (Option 3 specific check)
    print("## 3. Bear-case citation of structural-risk anchors (Option 3 lift)")
    print()
    print("| Field cite-path | Pre tickers citing | Post tickers citing | Δ |")
    print("|---|---|---|---|")
    for cite_path in (
        "qualitative.value_chain.substitution_pressure",
        "qualitative.forensic.concentration_risk",
        "qualitative.strategic_context.growth_quality",
    ):
        n_pre = sum(1 for r in paired.values() if cite_path in (r["pre"]["bear_cited_signals"] or []))
        n_post = sum(1 for r in paired.values() if cite_path in (r["post"]["bear_cited_signals"] or []))
        delta = n_post - n_pre
        sign = "+" if delta >= 0 else ""
        print(f"| `{cite_path}` | {n_pre}/{len(paired)} | {n_post}/{len(paired)} | {sign}{delta} |")
    print()

    # 4. Per-ticker delta table — easy to spot anomalies
    print("## 4. Per-ticker value diff (only fields that changed)")
    print()
    n_changed = 0
    for ticker in sorted(paired):
        pre = paired[ticker]["pre"]
        post = paired[ticker]["post"]
        diffs = []
        for field in ("moat_score", "strategic_position", "substitution_pressure",
                      "growth_quality", "concentration_risk"):
            pv, qv = pre.get(field), post.get(field)
            if pv != qv:
                diffs.append(f"{field}: {pv}→{qv}")
        if diffs:
            n_changed += 1
            print(f"- **{ticker}**: " + "; ".join(diffs))
    if n_changed == 0:
        print("(no per-field deltas — Phase 1.7 didn't change anything 🚨)")
    print()
    print(f"({n_changed}/{len(paired)} tickers had at least one field change)")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
