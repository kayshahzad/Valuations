"""Identity-audit CLI — thin wrapper around the calc-layer module.

The actual checker functions, tolerance config, and Phase-3 Category-C
exception flagging live in ``aletheia.calculations.identity_checks``
(promoted in Phase 4 to invert the cross-layer import). This file is a
standalone CLI that loads results via the calc-layer drivers and emits
CSV / JSON / Markdown report files.

Run:
    python -m tools.verification.identity_checks
    python -m tools.verification.identity_checks --tickers NVDA AAPL
    python -m tools.verification.identity_checks --output-dir audits/

Outputs:
    audits/identity_audit_<DATE>.csv      — flat tabular, one row per check
    audits/identity_audit_<DATE>.json     — structured (metadata + results)
    docs/identity_audit_findings_<DATE>.md — human-readable findings
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import subprocess
import sys
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

# Re-export from the calc layer so existing callers (tests, audit
# scripts) that import from the tools-side path keep working without
# code changes. Identity-check logic + tolerance config live in the
# calc layer; this module owns ONLY the CLI + report emitters.
from aletheia.calculations.identity_checks import (  # noqa: F401
    ABS_MAGNITUDE_FLOOR_USD,
    HYPERSCALER_TICKERS,
    IdentityCheckResult,
    RecordLoader,
    TOLERANCE_THRESHOLDS,
    check_balance_sheet_equation,
    check_cash_rollforward,
    check_debt_rollforward,
    check_fcf_pathway_reconciliation,
    check_ppe_rollforward,
    check_retained_earnings_rollforward,
    check_working_capital_reconciliation,
    run_all_checks_for_ticker,
    run_universe_audit,
)

__all__ = [
    # Backwards-compat re-exports of the calc-layer primitives.
    "ABS_MAGNITUDE_FLOOR_USD",
    "HYPERSCALER_TICKERS",
    "IdentityCheckResult",
    "RecordLoader",
    "TOLERANCE_THRESHOLDS",
    "check_balance_sheet_equation",
    "check_cash_rollforward",
    "check_debt_rollforward",
    "check_fcf_pathway_reconciliation",
    "check_ppe_rollforward",
    "check_retained_earnings_rollforward",
    "check_working_capital_reconciliation",
    "run_all_checks_for_ticker",
    "run_universe_audit",
    # CLI emitters + entry points
    "build_parser",
    "main",
]

logger = logging.getLogger(__name__)

OUTPUT_DIR_DEFAULT = Path("audits")
DOCS_DIR = Path("docs")


# ─────────────────────────────────────────────────────────────────────
# Output emitters
# ─────────────────────────────────────────────────────────────────────

def _emit_csv(results: List[IdentityCheckResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "ticker", "fiscal_year", "period", "identity",
            "passed", "discrepancy_abs", "discrepancy_pct",
            "tolerance_pct", "notes", "components_json",
        ])
        for r in results:
            w.writerow([
                r.ticker, r.fiscal_year, r.period, r.identity_name,
                r.passed,
                f"{r.discrepancy_abs:.2f}" if r.discrepancy_abs is not None else "",
                f"{r.discrepancy_pct:.4f}" if r.discrepancy_pct is not None else "",
                r.tolerance_pct,
                r.notes or "",
                json.dumps(r.components, default=str),
            ])


def _emit_json(results: List[IdentityCheckResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "universe_size": len({r.ticker for r in results}),
        "total_checks": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "tolerance_thresholds": TOLERANCE_THRESHOLDS,
        "abs_magnitude_floor_usd": ABS_MAGNITUDE_FLOOR_USD,
    }

    # Per-identity and per-ticker exception summaries.
    by_identity: Dict[str, Dict[str, int]] = {}
    by_ticker: Dict[str, Dict[str, int]] = {}
    for r in results:
        i_bucket = by_identity.setdefault(r.identity_name, {"failed": 0, "total": 0, "skipped": 0})
        t_bucket = by_ticker.setdefault(r.ticker, {"failed": 0, "total": 0, "skipped": 0})
        i_bucket["total"] += 1
        t_bucket["total"] += 1
        if r.notes and r.notes.startswith("skipped:"):
            i_bucket["skipped"] += 1
            t_bucket["skipped"] += 1
        elif not r.passed:
            i_bucket["failed"] += 1
            t_bucket["failed"] += 1

    payload = {
        "metadata": metadata,
        "exceptions_by_identity": by_identity,
        "exceptions_by_ticker": by_ticker,
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(payload, indent=2, default=str))


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unversioned"


def _emit_markdown(results: List[IdentityCheckResult], path: Path) -> None:
    """Human-readable findings report. Sections per the prompt."""
    path.parent.mkdir(parents=True, exist_ok=True)

    total = len(results)
    failed = [r for r in results if (not r.passed) and not (r.notes or "").startswith("skipped:")]
    skipped = [r for r in results if (r.notes or "").startswith("skipped:")]
    passed = total - len(failed) - len(skipped)

    identities = sorted({r.identity_name for r in results})
    rows_per_identity = []
    for ident in identities:
        sub = [r for r in results if r.identity_name == ident]
        s_total = len(sub)
        s_fail = sum(
            1 for r in sub
            if (not r.passed) and not (r.notes or "").startswith("skipped:")
        )
        s_skip = sum(1 for r in sub if (r.notes or "").startswith("skipped:"))
        s_pass = s_total - s_fail - s_skip
        rate = (s_pass / max(1, s_total - s_skip)) * 100.0 if s_total > s_skip else 0.0
        rows_per_identity.append((ident, s_total, s_pass, s_fail, s_skip, rate))

    by_ticker: Dict[str, List[IdentityCheckResult]] = {}
    for r in results:
        by_ticker.setdefault(r.ticker, []).append(r)

    out: List[str] = []
    out.append(f"# Identity Audit Findings — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    out.append("")
    out.append("Audit of the seven foundational accounting identities across the "
               "production universe. Checker logic lives in "
               "[aletheia/calculations/identity_checks.py](../aletheia/calculations/identity_checks.py); "
               "this report was emitted by the standalone CLI at "
               "[tools/verification/identity_checks.py](../tools/verification/identity_checks.py).")
    out.append("")

    out.append("## Executive summary")
    out.append("")
    out.append(f"- Total checks: **{total}**")
    out.append(f"- Passed: **{passed}** ({passed/total*100:.1f}%)")
    out.append(f"- Failed: **{len(failed)}** ({len(failed)/total*100:.1f}%)")
    out.append(f"- Skipped (no data): **{len(skipped)}** ({len(skipped)/total*100:.1f}%)")
    out.append(f"- Universe size: **{len(by_ticker)}** tickers")
    out.append(f"- Git SHA: `{_git_sha()[:12]}`")
    out.append("")
    out.append("| Identity | Total | Pass | Fail | Skip | Pass-rate (ex-skip) |")
    out.append("|---|---|---|---|---|---|")
    for ident, t, p, f, s, rate in rows_per_identity:
        out.append(f"| `{ident}` | {t} | {p} | {f} | {s} | {rate:.1f}% |")
    out.append("")

    out.append("## Findings by identity")
    out.append("")
    for ident, t, p, f, s, rate in rows_per_identity:
        out.append(f"### `{ident}` — {f} failure(s), {s} skipped")
        out.append("")
        ident_fails = [
            r for r in results
            if r.identity_name == ident
            and not r.passed
            and not (r.notes or "").startswith("skipped:")
        ]
        ident_fails.sort(
            key=lambda r: abs(r.discrepancy_abs or 0.0), reverse=True,
        )
        if not ident_fails:
            out.append("_No failures._")
            out.append("")
            continue
        out.append("Top 10 by absolute discrepancy:")
        out.append("")
        out.append("| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |")
        out.append("|---|---|---|---|---|---|")
        for r in ident_fails[:10]:
            cat = _suggest_category(r)
            disc_m = (r.discrepancy_abs or 0.0) / 1e6
            pct = r.discrepancy_pct or 0.0
            out.append(
                f"| {r.ticker} | {r.fiscal_year} | {r.period} | "
                f"{disc_m:+.1f} | {pct:+.2f}% | {cat} |"
            )
        out.append("")

    out.append("## Findings by ticker")
    out.append("")
    out.append("Tickers with at least one failure, ordered by failure count.")
    out.append("")
    ticker_fail_counts = [
        (
            t,
            sum(
                1 for r in rs
                if not r.passed and not (r.notes or "").startswith("skipped:")
            ),
            len(rs),
        )
        for t, rs in by_ticker.items()
    ]
    ticker_fail_counts.sort(key=lambda x: x[1], reverse=True)
    out.append("| Ticker | Failures | Total checks | Failing identities |")
    out.append("|---|---|---|---|")
    for t, fcount, tcount in ticker_fail_counts:
        if fcount == 0:
            continue
        idents = sorted({
            r.identity_name for r in by_ticker[t]
            if not r.passed and not (r.notes or "").startswith("skipped:")
        })
        out.append(f"| {t} | {fcount} | {tcount} | {', '.join(idents)} |")
    out.append("")

    path.write_text("\n".join(out))


def _suggest_category(r: IdentityCheckResult) -> str:
    """Heuristic category suggestion. Phase-3 exception_category on the
    result is authoritative; this is a legacy hint for older audit
    reports."""
    if r.exception_category:
        return r.exception_category
    ident = r.identity_name
    ticker = r.ticker
    fy = r.fiscal_year
    if ident == "balance_sheet_equation" and ticker == "NEE":
        return "C (utility taxonomy — see A19/A15)"
    if ident == "ppe_rollforward" and ticker in {"ABT", "ADBE", "JPM", "NVDA"}:
        return "C (active acquirer — M&A year?)"
    if ident == "debt_rollforward" and fy == 2019:
        return "C (ASC 842 transition)"
    if ident in ("working_capital_AR", "working_capital_inventory", "working_capital_AP"):
        return "C (likely M&A / WC reclassification)"
    if ident == "fcf_pathway_reconciliation":
        return "B/C (SBC + deferred-tax non-cash items)"
    if ident == "cash_rollforward" and ticker in {"ASML", "TSM"}:
        return "B (FX effect not captured by cleaner)"
    return "?"


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="identity_checks",
        description="Run the seven-identity audit across the universe.",
    )
    p.add_argument(
        "--tickers", nargs="+", default=None,
        help="Specific tickers to audit (default: full UNIVERSE)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT,
        help="Directory for CSV + JSON outputs",
    )
    p.add_argument(
        "--docs-dir", type=Path, default=DOCS_DIR,
        help="Directory for the Markdown findings report",
    )
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    results = run_universe_audit(args.tickers)
    if not results:
        print("No results — empty universe or DB unavailable.", file=sys.stderr)
        return 1

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_path = args.output_dir / f"identity_audit_{date_str}.csv"
    json_path = args.output_dir / f"identity_audit_{date_str}.json"
    md_path = args.docs_dir / f"identity_audit_findings_{date_str}.md"

    _emit_csv(results, csv_path)
    _emit_json(results, json_path)
    _emit_markdown(results, md_path)

    print(f"audited {len({r.ticker for r in results})} tickers, "
          f"{len(results)} total checks")
    print(f"  CSV  → {csv_path}")
    print(f"  JSON → {json_path}")
    print(f"  MD   → {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
