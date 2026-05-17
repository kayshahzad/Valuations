"""``aletheia pipeline ...`` CLI — orchestrator entry point.

Sub-commands:
  - ``aletheia pipeline run <TICKER>`` — chain Stage 1 → 2 → 3
    (optionally → 4) for one ticker.
  - ``aletheia pipeline run --all`` — universe sweep.
  - ``aletheia pipeline status`` — universe-level (ticker, stage)
    matrix from the pipeline_status registry.
  - ``aletheia pipeline status <TICKER>`` — per-ticker breakdown.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from typing import List, Optional

from aletheia.contracts.pipeline import StageStatus
from aletheia.pipeline.orchestrator import Orchestrator
from aletheia.pipeline.status_store import PipelineStatusStore


def detect_pipeline_version() -> str:
    """Resolve the current code SHA. Falls back to ``"unversioned"``."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        return sha or "unversioned"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unversioned"


# ─────────────────────────────────────────────────────────────────────
# Sub-command: run
# ─────────────────────────────────────────────────────────────────────

def _run_one(args, pipeline_version: str) -> int:
    bust_cache = _parse_bust_cache(args.bust_cache)

    with Orchestrator() as orch:
        result = orch.run(
            args.ticker.upper(),
            pipeline_version=pipeline_version,
            auto_agents=args.auto_agents,
            bust_cache=bust_cache,
            force_refresh=args.force_refresh,
            include_market_snapshot=not args.no_market_snapshot,
            provider=args.provider,
        )

    print(json.dumps({
        "ticker": result.ticker,
        "pipeline_version": result.pipeline_version,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "auto_agents": result.auto_agents,
        "stages": {
            s: {
                "status": o.status.value,
                "fingerprint": o.fingerprint,
                "duration_seconds": round(o.duration_seconds, 3),
                "error_message": o.error_message,
            }
            for s, o in result.stages.items()
        },
        "all_ok": result.all_ok,
    }, indent=2, default=str))
    return 0 if result.all_ok else 1


def _run_universe(args, pipeline_version: str) -> int:
    from config.ticker_classification import UNIVERSE
    bust_cache = _parse_bust_cache(args.bust_cache)
    failures = 0

    with Orchestrator() as orch:
        for ticker in sorted(UNIVERSE):
            result = orch.run(
                ticker,
                pipeline_version=pipeline_version,
                auto_agents=args.auto_agents,
                bust_cache=bust_cache,
                force_refresh=args.force_refresh,
                include_market_snapshot=not args.no_market_snapshot,
                provider=args.provider,
            )
            summary = " ".join(
                f"{s.split('_')[0]}={o.status.value[:2]}"
                for s, o in result.stages.items()
            )
            print(f"{ticker:<8} {summary}", file=sys.stderr)
            if not result.all_ok:
                failures += 1

    print(f"\nuniverse: {len(UNIVERSE) - failures}/{len(UNIVERSE)} all-ok",
          file=sys.stderr)
    return 0 if failures == 0 else 1


def _parse_bust_cache(value: Optional[str]) -> Optional[List[str]]:
    """``--bust-cache stage1,stage2`` → ['stage1_ingest', 'stage2_validate'].

    Accepts both short forms ('stage1') and full names. Comma-separated,
    no spaces. Per the CLI convention locked in
    docs/pipeline_contracts.md.
    """
    if not value:
        return None
    short_to_full = {
        "stage1": "stage1_ingest",
        "stage2": "stage2_validate",
        "stage3": "stage3_calculate",
        "stage4": "stage4_agents",
    }
    out: List[str] = []
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        out.append(short_to_full.get(part, part))
    return out


# ─────────────────────────────────────────────────────────────────────
# Sub-command: status
# ─────────────────────────────────────────────────────────────────────

def _status(args) -> int:
    with PipelineStatusStore() as store:
        if args.ticker:
            rows = store.get_for_ticker(args.ticker.upper())
            if not rows:
                print(f"no status entries for {args.ticker.upper()}",
                      file=sys.stderr)
                return 1
            for r in rows:
                print(_fmt_status_row(r))
            return 0

        rows = store.matrix()
        if not rows:
            print("no status entries; run `aletheia pipeline run` first",
                  file=sys.stderr)
            return 1
        # Pivot by ticker for terminal readability.
        by_ticker: dict[str, dict[str, StageStatus]] = {}
        for r in rows:
            by_ticker.setdefault(r.ticker, {})[r.stage] = r.status
        print(f"{'ticker':<8} stage1   stage2   stage3   stage4")
        for t in sorted(by_ticker):
            cells = []
            for stage in ("stage1_ingest", "stage2_validate",
                          "stage3_calculate", "stage4_agents"):
                s = by_ticker[t].get(stage)
                cells.append(f"{s.value[:8]:<8}" if s else "—".ljust(8))
            print(f"{t:<8} " + " ".join(cells))
    return 0


def _fmt_status_row(r) -> str:
    fp = (r.fingerprint or "")[:16] + "…" if r.fingerprint else "—"
    err = f"  error: {r.error_message}" if r.error_message else ""
    last = r.last_run_at.isoformat() if r.last_run_at else "—"
    return (
        f"{r.stage:<18} status={r.status.value:<14} "
        f"fp={fp:<18} last_run={last}{err}"
    )


# ─────────────────────────────────────────────────────────────────────
# Parser
# ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="aletheia pipeline",
        description=(
            "Orchestrate the four-stage pipeline. See "
            "docs/pipeline_contracts.md for the architecture."
        ),
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # run
    run_p = sub.add_parser("run", help="Run the pipeline for a ticker.")
    run_p.add_argument("ticker", nargs="?", default=None,
                        help="Ticker (omit when --all is set).")
    run_p.add_argument("--all", action="store_true",
                       dest="all_universe",
                       help="Sweep every ticker in the UNIVERSE.")
    run_p.add_argument("--auto-agents", action="store_true",
                       help=("Also run Stage 4 (LLM agents). "
                             "Off by default — incurs LLM dollars."))
    run_p.add_argument("--bust-cache", default=None,
                       help=("Comma-separated stage ids to force "
                             "re-run, e.g. stage1,stage2. Cascade-"
                             "invalidates downstream stages."))
    run_p.add_argument("--force-refresh", action="store_true",
                       help="Bust every stage's cache (full re-run).")
    run_p.add_argument("--no-market-snapshot", action="store_true",
                       help="Skip Stage 1's live market data fetch.")
    run_p.add_argument("--pipeline-version", default=None,
                       help="Override stamped pipeline version "
                            "(defaults to git rev-parse HEAD).")
    run_p.add_argument("--provider", default=None,
                       choices=["fmp", "xbrl", "hybrid"],
                       help="Data-source provider. "
                            "Default reads ALETHEIA_PROVIDER env var, "
                            "then config/data_source.py "
                            "(currently 'fmp'). Routes Stage 1 source "
                            "allow-list + Stage 2 record construction.")

    # status
    status_p = sub.add_parser("status", help="Show pipeline status.")
    status_p.add_argument("ticker", nargs="?", default=None,
                          help=("When supplied, show per-ticker "
                                "breakdown; otherwise universe matrix."))

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)

    if args.command == "run":
        pipeline_version = args.pipeline_version or detect_pipeline_version()
        if args.all_universe:
            return _run_universe(args, pipeline_version)
        if not args.ticker:
            print("`run` requires a ticker (or --all)", file=sys.stderr)
            return 2
        return _run_one(args, pipeline_version)

    if args.command == "status":
        return _status(args)

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
