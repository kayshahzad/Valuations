#!/usr/bin/env python3
"""Phase-0 oracle diff (fix-plan task 0.1.2).

Compares two `build_oracle.py` snapshots field-by-field and buckets every delta.
Sign-flips and ~2x magnitude changes (the CapEx-sign / FCF-double-count failure
modes from Phase 1) are surfaced loudly and force a non-zero exit, so this is a
usable pre-merge gate.

    python scripts/diff_oracle.py BASE.json CANDIDATE.json
    python scripts/diff_oracle.py BASE.json CAND.json --fail-on any   # strict

Exit code 0 = clean (nothing at/above --fail-on threshold); 1 = regressions.
"""
from __future__ import annotations

import argparse
import json
import sys

# Severity ranking (higher = worse). Buckets at/above the --fail-on level cause
# a non-zero exit.
SEV = {
    "identical": 0, "<0.1%": 1, "appeared": 1, "0.1-1%": 2,
    ">1%": 3, "disappeared": 3, "HALVED(~2x)": 4, "DOUBLED(~2x)": 4, "SIGN_FLIP": 5,
}
FAIL_LEVELS = {"any": 1, "material": 3, "sign-double": 4, "sign": 5}

# The `valuation.*` block is recomputed against LIVE price + beta (beta from a
# rolling price window), so it drifts run-to-run regardless of code — even in a
# same-instant A/B. The deterministic, code-driven regression signal is the
# `cleaned.*` block (DuckDB-backed): Revenue / EBIT / EBITDA / NOPAT / ROIC / ROE
# etc. — exactly what Phase 1's cleaning changes move after a re-ingest. Gate on
# it; treat valuation as informational unless --include-valuation.
def _is_excluded(path: str, include_valuation: bool) -> bool:
    return (".valuation." in path or path.endswith(".valuation")) and not include_valuation


def _flatten(obj, prefix=""):
    """Yield (dot_path, value) for every leaf; skip pure-metadata string keys."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from _flatten(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from _flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, obj


def _bucket(a, b):
    """Classify a numeric (or scalar) transition a -> b."""
    an = isinstance(a, (int, float)) and not isinstance(a, bool)
    bn = isinstance(b, (int, float)) and not isinstance(b, bool)
    if a is None and b is None:
        return "identical"
    if (a is None) != (b is None):
        return "appeared" if a is None else "disappeared"
    if not (an and bn):                       # non-numeric scalars (period, engine)
        return "identical" if a == b else ">1%"
    if a == b:
        return "identical"
    if a * b < 0:
        return "SIGN_FLIP"
    if a != 0:
        ratio = abs(b) / abs(a)
        if 1.8 <= ratio <= 2.2:
            return "DOUBLED(~2x)"
        if 0.45 <= ratio <= 0.55:
            return "HALVED(~2x)"
    rel = abs(b - a) / max(abs(a), abs(b), 1e-30)
    if rel < 1e-9:
        return "identical"
    if rel < 1e-3:
        return "<0.1%"
    if rel < 1e-2:
        return "0.1-1%"
    return ">1%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("base")
    ap.add_argument("candidate")
    ap.add_argument("--fail-on", choices=list(FAIL_LEVELS), default="material",
                    help="min severity that forces exit 1 (default: material = >1%)")
    ap.add_argument("--show", type=int, default=40, help="max detail lines")
    ap.add_argument("--include-valuation", action="store_true",
                    help="also gate the valuation.* block (live-price/beta dependent; "
                         "only meaningful when market inputs are pinned)")
    args = ap.parse_args()

    base = json.load(open(args.base))["tickers"]
    cand = json.load(open(args.candidate))["tickers"]

    counts: dict[str, int] = {k: 0 for k in SEV}
    findings = []  # (severity, ticker, path, a, b, bucket)
    tickers = sorted(set(base) | set(cand))
    for t in tickers:
        bf = dict(_flatten(base.get(t, {})))
        cf = dict(_flatten(cand.get(t, {})))
        for path in sorted(set(bf) | set(cf)):
            if _is_excluded(path, args.include_valuation):
                continue
            a, b = bf.get(path), cf.get(path)
            bkt = _bucket(a, b)
            counts[bkt] += 1
            if SEV[bkt] >= 2:            # collect 0.1-1% and worse for detail
                findings.append((SEV[bkt], t, path, a, b, bkt))

    findings.sort(key=lambda x: (-x[0], x[1], x[2]))
    fail_level = FAIL_LEVELS[args.fail_on]
    n_fail = sum(1 for f in findings if f[0] >= fail_level)

    print(f"Oracle diff: {args.base}  →  {args.candidate}")
    print(f"  tickers compared: {len(tickers)}")
    print("  buckets: " + " · ".join(
        f"{k}={counts[k]}" for k in sorted(counts, key=lambda x: SEV[x]) if counts[k]))
    if findings:
        print(f"\n  {'SEV':>13}  {'ticker':6}  path = base → cand")
        for sev, t, path, a, b, bkt in findings[:args.show]:
            def fmt(x):
                return f"{x:.6g}" if isinstance(x, (int, float)) and not isinstance(x, bool) else repr(x)
            print(f"  {bkt:>13}  {t:6}  {path} = {fmt(a)} → {fmt(b)}")
        if len(findings) > args.show:
            print(f"  … {len(findings) - args.show} more (raise --show)")
    verdict = "CLEAN" if n_fail == 0 else f"REGRESSIONS ({n_fail} ≥ '{args.fail_on}')"
    print(f"\n  verdict: {verdict}")
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
