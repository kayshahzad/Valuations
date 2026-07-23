#!/usr/bin/env python3
"""Phase-0 golden gate (fix-plan task 0.5.2).

Locks a small, engine-diverse set of tickers to their CURRENT deterministically-
recomputed values and re-asserts them within tolerance. This is the pre-merge
gate every Phase-1 PR runs.

IMPORTANT — the locked values are a *fresh current baseline*, not the stale
convergence goldens in project memory (data has been re-ingested since those
were set: e.g. EQIX now ≈$940 vs memory $1,078). The gate's job is to catch
FUTURE drift caused by Phase-1 code changes, not to reproduce historical numbers.

Values are locked in the `pure` override state (apply_overrides=False) — the
authoritative regression signal, isolating code changes from analyst what-ifs.

    python scripts/validate_goldens.py --lock     # (re)generate expected_values.json
    python scripts/validate_goldens.py            # assert current == locked
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.build_oracle import _snapshot_ticker  # noqa: E402

EXPECTED_PATH = ROOT / "tests" / "golden" / "expected_values.json"

# Engine-diverse golden set (intersected with the universe at runtime).
# Rationale per ticker in the comment.
GOLDEN_CANDIDATES = [
    "AAPL",   # FCFF mega-cap
    "MSFT",   # FCFF compounder (high MV/BV — WACC-weighting sensitivity, Phase 4)
    "EQIX",   # REIT engine
    "ET",     # MLP engine
    "NEE",    # rate-base engine (regulated utility — A=L+E override)
    "CNC",    # residual-income insurer (memory golden)
    "UNH",    # DDM insurer (float)
    "TSM",    # foreign filer + persisted overrides (pure≠overrides)
    "AMD",    # negative-EBITDA / operating-loss path (exercises 1e sign)
    "BRK-B",  # embedded-value engine
    "V",      # KNOWN_ISSUES bypass (specialized, IV None expected)
]

# Per-field relative tolerance for the assert pass.
TOL = {
    "wacc_base": 0.005, "base_ips": 0.005, "ips_base": 0.005,
    "clean_NOPAT": 0.005, "clean_EBITDA": 0.005, "derived_ROIC": 0.01,
    "justified_ev_ebitda": 0.01,
}
DEFAULT_TOL = 0.01


def _extract(snap: dict) -> dict:
    """Pull the locked invariants from a pure-state snapshot."""
    val = snap.get("valuation", {}) or {}
    cl_fy = (snap.get("cleaned", {}) or {}).get("latest_fy", {}) or {}
    base = val.get("base") or {}
    out = {
        "engine": val.get("engine"),
        "wacc_base": val.get("wacc_base"),
        "base_ips": base.get("ips"),
        "ips_base": val.get("ips_base"),
        "clean_NOPAT": cl_fy.get("clean_NOPAT"),
        "clean_EBITDA": cl_fy.get("clean_EBITDA"),
        "derived_ROIC": cl_fy.get("derived_ROIC"),
        "justified_ev_ebitda": (val.get("multiple") or {}).get("justified_ev_ebitda"),
    }
    return out


def _goldens() -> list[str]:
    from config.ticker_classification import get_extended_universe
    uni = set(get_extended_universe())
    present = [t for t in GOLDEN_CANDIDATES if t in uni]
    missing = [t for t in GOLDEN_CANDIDATES if t not in uni]
    if missing:
        print(f"  note: not in universe, skipped: {missing}", file=sys.stderr)
    return present


def lock() -> int:
    exp = {"_note": "pure-state (apply_overrides=False) current baseline; "
                    "supersedes stale memory goldens", "tickers": {}}
    for t in _goldens():
        exp["tickers"][t] = _extract(_snapshot_ticker(t, apply_overrides=False))
        print(f"  locked {t}: {exp['tickers'][t]}", file=sys.stderr)
    EXPECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(EXPECTED_PATH, "w") as fh:
        json.dump(exp, fh, indent=2, sort_keys=True)
    print(f"\nwrote {EXPECTED_PATH} ({len(exp['tickers'])} goldens)")
    return 0


def check() -> int:
    if not EXPECTED_PATH.exists():
        print(f"ERROR: {EXPECTED_PATH} missing — run --lock first", file=sys.stderr)
        return 2
    exp = json.load(open(EXPECTED_PATH))["tickers"]
    breaches = []
    for t, want in exp.items():
        got = _extract(_snapshot_ticker(t, apply_overrides=False))
        for k, wv in want.items():
            gv = got.get(k)
            if isinstance(wv, str) or wv is None or gv is None:
                if wv != gv:
                    breaches.append((t, k, wv, gv, "changed"))
                continue
            tol = TOL.get(k, DEFAULT_TOL)
            rel = abs(gv - wv) / max(abs(wv), 1e-30)
            if rel > tol:
                breaches.append((t, k, wv, gv, f"{rel:.2%}>{tol:.1%}"))
    if breaches:
        print("GOLDEN BREACHES:")
        for t, k, wv, gv, why in breaches:
            print(f"  {t:6} {k:22} {wv} → {gv}  ({why})")
        print(f"\n{len(breaches)} breach(es) across {len(exp)} goldens — FAIL")
        return 1
    print(f"All {len(exp)} goldens within tolerance — PASS")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lock", action="store_true", help="regenerate expected_values.json")
    args = ap.parse_args()
    return lock() if args.lock else check()


if __name__ == "__main__":
    raise SystemExit(main())
