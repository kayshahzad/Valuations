#!/usr/bin/env python3
"""Gate F CLI (fix-plan 3.1.3 + 3.1.4).

The imperative shell around the pure ``aggregate_universe`` gate: prints a human
summary, writes the diff-able ``audits/gate_f_<date>.json`` artifact, and maps
the verdict to a process exit code so ``regen_universe.sh`` step 6 can branch.

    python scripts/gate_f.py                    # gate the live serving dir
    python scripts/gate_f.py --report-only      # never fails (WARN-only rollout)
    python scripts/gate_f.py --serving-dir DIR

Exit code: 0 for PASS/WARN (and always under --report-only); 1 for FAIL.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aletheia.data.fmp_validation import aggregate_universe  # noqa: E402

_C = {"PASS": "\033[32m", "WARN": "\033[33m", "FAIL": "\033[31m",
      "dim": "\033[2m", "bold": "\033[1m", "off": "\033[0m"}


def _c(s: str, key: str) -> str:
    return f"{_C.get(key, '')}{s}{_C['off']}" if sys.stdout.isatty() else s


def _summary(r: dict) -> None:
    v = r["verdict"]
    ev = r["effective_verdict"]
    banner = f" Gate F: {v} " + (f"(effective {ev} — report-only) " if ev != v else "")
    print(_c(_c(banner, "bold"), v))
    print(f"  universe: {r['universe_n']} reports"
          + (f"  ·  malformed/old-schema: {r['malformed']}" if r["malformed"] else ""))
    print(f"  calc status: {r['calc_status']}  ·  skip-rate: {r['skip_rate']:.0%}")

    if r["reasons"]:
        print(_c("  reasons:", "bold"))
        for reason in r["reasons"]:
            print(f"    • {reason}")

    if r["systematic_fields"]:
        print(_c(f"  SYSTEMATIC drift (gated, ≥{r['thresholds']['systematic_frac']:.0%} of universe):", "FAIL"))
        for f in r["systematic_fields"]:
            print(f"    {f}: {r['gated_field_stats'][f]['structural']}/{r['universe_n']} tickers")

    gfs = r["gated_field_stats"]
    if gfs:
        print(_c("  gated field drift (strict/standard blocking — can FAIL):", "bold"))
        for f, s in sorted(gfs.items(), key=lambda x: -x[1]["structural"]):
            offs = ", ".join(f"{o['ticker']}({o['drift_pct']*100:+.1f}%)" for o in s["offenders"][:6])
            print(f"    {f} [{s['tier']}]: {s['structural']} drift, worst {s['worst']*100:.1f}%  {_c(offs, 'dim')}")

    if r["blocking_reports"]:
        print(_c("  blocking_drift reports:", "FAIL"))
        for b in r["blocking_reports"]:
            print(f"    {b['ticker']}: {', '.join(b['fields'])}")

    if r["context_fields"]:
        cf = ", ".join(f"{f}({s['structural']})" for f, s in
                       sorted(r["context_fields"].items(), key=lambda x: -x[1]["structural"]))
        print(_c(f"  context only (definitional/non-blocking — drift by design, never gated): {cf}", "dim"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serving-dir", default=str(ROOT / "valuation_data/serving/latest"))
    ap.add_argument("--report-only", action="store_true",
                    help="compute + record the verdict but never exit non-zero (WARN-only rollout)")
    ap.add_argument("--out", default=None, help="artifact path (default audits/gate_f_<date>.json)")
    ap.add_argument("--quiet", action="store_true", help="suppress the human summary")
    args = ap.parse_args()

    result = aggregate_universe(args.serving_dir, report_only=args.report_only)

    # Artifact — diff-able across regens.
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    out = Path(args.out) if args.out else (ROOT / "audits" / f"gate_f_{stamp}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": datetime.now(timezone.utc).isoformat(),
               "serving_dir": args.serving_dir, **result}
    out.write_text(json.dumps(payload, indent=2, sort_keys=True))

    if not args.quiet:
        _summary(result)
        print(_c(f"  artifact: {out.relative_to(ROOT)}", "dim"))

    return 0 if result["effective_verdict"] in ("PASS", "WARN") else 1


if __name__ == "__main__":
    raise SystemExit(main())
