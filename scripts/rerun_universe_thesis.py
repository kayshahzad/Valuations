"""One-shot universe re-runner for the qualitative+thesis stack.

Used to establish the post-Phase-1.7 + post-citation-fix baseline so
the eventual Phase 2 grounding work has a clean comparison set.
Iterates the tickers that already have modern-pipeline traces in
`audits/` (the 40-ticker set we measured for prose-anchoring) and
re-invokes the LangGraph workflow per ticker, writing fresh traces
back to `audits/`. The Excel exporter is bypassed — it's slow and
unrelated to the qualitative-rebaseline question.

Streams a one-line progress record per ticker to a log file so the
caller can `tail -f` it. Exceptions are caught per-ticker so one bad
run doesn't kill the rest.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import load_dotenv
load_dotenv()

from aletheia.workflow.graph import create_workflow
from aletheia.utils.tracing import tracer


LOG_PATH = Path("scratch/universe_rerun_progress.log")


def _modern_traced_tickers() -> list[str]:
    """Return tickers with at least one modern-pipeline trace in audits/.
    Modern = trace contains both QualitativeSynthesis and ThesisSynthesizer
    agent steps."""
    latest: dict[str, tuple[str, int]] = {}
    for fname in os.listdir("audits"):
        m = re.match(r"trace_([A-Z\.\-]+)_(\d+)\.json", fname)
        if not m:
            continue
        ticker, ts = m.group(1), int(m.group(2))
        cur = latest.get(ticker)
        if cur is None or cur[1] < ts:
            latest[ticker] = (fname, ts)

    out = []
    for ticker, (fname, _) in sorted(latest.items()):
        try:
            with open(f"audits/{fname}") as f:
                trace = json.load(f)
            agents = {e.get("agent") for e in trace if isinstance(e, dict)}
            if "QualitativeSynthesis" in agents and "ThesisSynthesizer" in agents:
                out.append(ticker)
        except Exception:
            continue
    return out


def _log(msg: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    line = f"[{time.strftime('%H:%M:%S')}]  {msg}"
    print(line, flush=True)
    with open(LOG_PATH, "a") as f:
        f.write(line + "\n")


def _run_one(ticker: str) -> dict:
    """Single full-pipeline run, returns a result dict."""
    ts = int(time.time())
    trace_id = f"trace_{ticker}_{ts}"
    tracer.start_trace(trace_id)

    t0 = time.time()
    app = create_workflow()
    state = {
        "ticker": ticker, "messages": [],
        "financial_data": {}, "valuation_report": {},
        "strategist_report": {}, "contrarian_report": {},
        "final_report": "",
    }
    final = app.invoke(state)
    elapsed = time.time() - t0

    tracer.save_traces(f"audits/{trace_id}.json")

    # Did thesis_synthesizer fall back to mock?
    ts_out = (final or {}).get("thesis_synthesis") or {}
    is_mock = "mock" in (ts_out.get("thesis_statement") or "").lower()

    # Pull out the new fields' values for the per-ticker log line
    qs_state = final or {}
    fr = qs_state.get("forensic_report") or {}
    vc = qs_state.get("value_chain_report") or {}
    sc = qs_state.get("strategic_context_report") or {}

    return {
        "ticker": ticker,
        "elapsed": elapsed,
        "trace_id": trace_id,
        "thesis_mock": is_mock,
        "moat_score": fr.get("moat_score"),
        "strategic_position": vc.get("strategic_position"),
        "substitution_pressure": vc.get("substitution_pressure"),
        "growth_quality": sc.get("growth_quality"),
        "concentration_risk": fr.get("concentration_risk"),
        # Citation observability — does the bear case now cite the new anchors?
        "bear_cites_subst": "qualitative.value_chain.substitution_pressure"
            in (((ts_out.get("bear_case") or {}).get("cited_signals")) or []),
        "bear_cites_conc": "qualitative.forensic.concentration_risk"
            in (((ts_out.get("bear_case") or {}).get("cited_signals")) or []),
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--skip", default="",
                   help="Comma-separated ticker list to skip (e.g. NSC,SMCI)")
    p.add_argument("--only", default="",
                   help="Comma-separated ticker list to run exclusively")
    p.add_argument("--append-log", action="store_true",
                   help="Append to progress log instead of truncating")
    args = p.parse_args()

    tickers = _modern_traced_tickers()
    skip = {t.strip().upper() for t in args.skip.split(",") if t.strip()}
    only = {t.strip().upper() for t in args.only.split(",") if t.strip()}
    if only:
        tickers = [t for t in tickers if t in only]
    if skip:
        tickers = [t for t in tickers if t not in skip]

    if not args.append_log:
        LOG_PATH.write_text("")
    _log(f"START — {len(tickers)} tickers"
         f"{' (skipping: ' + ','.join(sorted(skip)) + ')' if skip else ''}"
         f"{' (only: ' + ','.join(sorted(only)) + ')' if only else ''}")

    rows: list[dict] = []
    n_ok = n_mock = n_err = 0

    for i, ticker in enumerate(tickers, 1):
        _log(f"[{i:02d}/{len(tickers)}] {ticker} — starting")
        try:
            r = _run_one(ticker)
            rows.append(r)
            if r["thesis_mock"]:
                n_mock += 1
                _log(f"[{i:02d}/{len(tickers)}] {ticker} — MOCK FALLBACK in {r['elapsed']:.0f}s")
            else:
                n_ok += 1
                _log(
                    f"[{i:02d}/{len(tickers)}] {ticker} — OK in {r['elapsed']:.0f}s  "
                    f"moat={r['moat_score']} pos={r['strategic_position']} "
                    f"subst={r['substitution_pressure']} growth={r['growth_quality']} "
                    f"conc={r['concentration_risk']}  "
                    f"bear-cites-subst={r['bear_cites_subst']} "
                    f"bear-cites-conc={r['bear_cites_conc']}"
                )
        except Exception as exc:
            n_err += 1
            _log(f"[{i:02d}/{len(tickers)}] {ticker} — ERROR  {type(exc).__name__}: {exc}")
            _log(traceback.format_exc())

    # Final tally + write JSON summary
    _log("")
    _log(f"DONE — ok={n_ok}  mock={n_mock}  err={n_err}  total={len(tickers)}")

    summary_path = Path("scratch/universe_rerun_summary.json")
    summary_path.write_text(json.dumps({
        "started_at": int(time.time()) - sum(r["elapsed"] for r in rows if "elapsed" in r),
        "n_ok": n_ok, "n_mock": n_mock, "n_err": n_err,
        "rows": rows,
    }, indent=2))
    _log(f"Summary: {summary_path}")

    return 0 if (n_err == 0 and n_mock == 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())
