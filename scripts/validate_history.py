"""
scripts/validate_history.py

Multi-year FMP validation. For each ticker × last-N fiscal years (default 5),
compares cleaned `raw_<field>` and `derived_<metric>` against FMP's annual
statements + ratios + key-metrics. Produces a stability matrix:

    rows    = tickers
    columns = fiscal years
    cells   = ✓ / total per FY

This catches drift that single-FY validation misses:
  - Tag-mapping changes the issuer makes between filings (LLY moving from
    `IncomeFromOperations` to a derived path is invisible if you only check
    the latest year).
  - Cleaning-engine fixes that improved one FY but regressed earlier years.
  - Restatement events where prior-year numbers move.

Usage:
    PYTHONPATH=. python3 scripts/validate_history.py [TICKER ...] [--years N]

Output: docs/FMP_HISTORY_REPORT.md plus per-ticker tables to stdout.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# Reuse the existing single-FY validator
from scripts.validate_fmp import validate_ticker, DEFAULT_TICKERS


# Sections we count toward stability (skip screening — runs once on latest FY).
_HISTORICAL_SECTIONS = ("income", "balance", "cashflow", "derived")


def validate_history(ticker: str, n_years: int = 5) -> Dict[int, Dict[str, Any]]:
    """Validate the last `n_years` fiscal years for a ticker. Returns
    {fiscal_year: validation_payload}, ordered most-recent-first."""
    from aletheia.utils.calc_input_builder import make_calc_input
    try:
        calc = make_calc_input(ticker)
    except Exception as e:
        return {0: {"ticker": ticker, "error": f"calc input failed: {e}"}}

    df = calc.df
    if df.empty:
        return {0: {"ticker": ticker, "error": "no cleaned data in DB"}}

    fys = sorted(df["fiscal_year"].unique().tolist(), reverse=True)[:n_years]
    out: Dict[int, Dict[str, Any]] = {}
    for fy in fys:
        try:
            r = validate_ticker(ticker, fy=int(fy))
        except Exception as e:
            r = {"ticker": ticker, "fiscal_year": int(fy),
                 "error": f"{type(e).__name__}: {e}"}
        out[int(fy)] = r
    return out


def cell_counts(payload: Dict[str, Any]) -> Dict[str, int]:
    """Count flags across the historical sections of a per-FY payload."""
    if payload.get("error"):
        return {"ok": 0, "near": 0, "bad": 0, "missing": 0, "total": 0,
                "error": payload["error"]}
    rows: List[Dict[str, Any]] = []
    for sect in _HISTORICAL_SECTIONS:
        rows.extend(payload.get(sect) or [])
    counts = {
        "ok":      sum(1 for r in rows if r["flag"] == "✓"),
        "near":    sum(1 for r in rows if r["flag"] == "≈"),
        "bad":     sum(1 for r in rows if r["flag"] == "✗"),
        "missing": sum(1 for r in rows if r["flag"] in
                       ("ours_missing", "fmp_missing", "—", "n/a (schema)")),
        "total":   len(rows),
    }
    return counts


def ticker_stability(history: Dict[int, Dict[str, Any]]) -> Dict[str, Any]:
    """Compute per-ticker stability metrics across all years."""
    fy_counts = {fy: cell_counts(p) for fy, p in history.items()}
    successful = [c for c in fy_counts.values() if "error" not in c and c["total"] > 0]
    if not successful:
        return {"years_validated": 0, "pass_rate": None,
                "stable_pass": True, "fy_counts": fy_counts}
    total_cells = sum(c["total"] for c in successful)
    ok_cells = sum(c["ok"] + c["near"] for c in successful)
    pass_rate = ok_cells / total_cells if total_cells else 0.0
    pass_rates_per_fy = [
        (c["ok"] + c["near"]) / c["total"] for c in successful if c["total"]
    ]
    drift_yoy = (max(pass_rates_per_fy) - min(pass_rates_per_fy)) if pass_rates_per_fy else 0
    return {
        "years_validated": len(successful),
        "pass_rate":       pass_rate,
        "min_yoy":         min(pass_rates_per_fy) if pass_rates_per_fy else None,
        "max_yoy":         max(pass_rates_per_fy) if pass_rates_per_fy else None,
        "yoy_spread":      drift_yoy,
        "fy_counts":       fy_counts,
    }


def render_ticker_history(ticker: str, history: Dict[int, Dict[str, Any]]) -> List[str]:
    lines = [f"\n{'=' * 72}", f"  {ticker}  history", "=" * 72]
    s = ticker_stability(history)
    lines.append(f"  Years validated: {s['years_validated']}  ·  "
                 f"Pass rate: {s['pass_rate']*100:.1f}% (across {s['years_validated']} FYs)"
                 if s["pass_rate"] is not None else "  No years validated")
    if s["pass_rate"] is not None:
        lines.append(f"  YoY spread: {s['min_yoy']*100:.1f}% (worst FY) → "
                     f"{s['max_yoy']*100:.1f}% (best FY)")
    lines.append(f"  {'FY':<8}{'✓':>5}{'≈':>5}{'✗':>5}{'miss':>6}{'tot':>5}{'pass%':>8}")
    for fy in sorted(history.keys(), reverse=True):
        c = s["fy_counts"][fy]
        if "error" in c:
            lines.append(f"  FY{fy:<6} {c['error'][:50]}")
            continue
        pr = (c["ok"] + c["near"]) / c["total"] * 100 if c["total"] else 0
        lines.append(f"  FY{fy:<6}{c['ok']:>5}{c['near']:>5}{c['bad']:>5}"
                     f"{c['missing']:>6}{c['total']:>5}{pr:>7.1f}%")
    return lines


def write_markdown_report(
    histories: Dict[str, Dict[int, Dict[str, Any]]],
    out_path: Path,
) -> None:
    lines = ["# FMP Historical Validation Report\n"]
    lines.append(f"**Generated:** {date.today().isoformat()}  ")
    lines.append(f"**Scope:** Last 5 fiscal years per ticker, "
                 "FMP statements + derived ratios (screening section is latest-FY only and not included here)  ")
    lines.append("**Cells per ticker per year:** 34 (9 income + 14 balance + 5 cash-flow + 6 derived)  \n")

    lines.append(_FINDINGS)

    # ── Stability rollup ──────────────────────────────────────────────────
    lines.append("\n## Per-ticker stability rollup\n")
    lines.append("| Ticker | Years | Avg pass rate | Worst FY | Best FY | YoY spread |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    rollup_rows = []
    for ticker, history in histories.items():
        s = ticker_stability(history)
        if s["pass_rate"] is None:
            lines.append(f"| {ticker} | 0 | _no validation_ |  |  |  |")
            continue
        lines.append(
            f"| {ticker} | {s['years_validated']} | "
            f"{s['pass_rate']*100:.1f}% | "
            f"{s['min_yoy']*100:.1f}% | "
            f"{s['max_yoy']*100:.1f}% | "
            f"{s['yoy_spread']*100:.1f}pp |"
        )
        rollup_rows.append((ticker, s["pass_rate"], s["yoy_spread"]))

    # ── Per-ticker per-year matrix ────────────────────────────────────────
    lines.append("\n## Per-ticker per-year detail (✓ / total per FY)\n")
    # Collect all FYs
    all_fys = sorted({fy for h in histories.values() for fy in h.keys()
                       if fy != 0}, reverse=True)
    header_cells = " | ".join(f"FY{fy}" for fy in all_fys)
    lines.append(f"| Ticker | {header_cells} |")
    lines.append("|" + "|".join(["---"] * (len(all_fys) + 1)) + "|")
    for ticker, history in histories.items():
        cells = []
        for fy in all_fys:
            payload = history.get(fy)
            if not payload:
                cells.append("—")
                continue
            c = cell_counts(payload)
            if "error" in c:
                cells.append("err")
                continue
            if c["total"] == 0:
                cells.append("—")
                continue
            pr = (c["ok"] + c["near"]) / c["total"] * 100
            cells.append(f"{c['ok']}/{c['total']} ({pr:.0f}%)")
        lines.append(f"| {ticker} | " + " | ".join(cells) + " |")

    # ── Identify regression candidates ────────────────────────────────────
    regressions = sorted(rollup_rows, key=lambda r: -r[2])[:8]
    lines.append("\n## Tickers with biggest year-over-year drift in pass rate\n")
    lines.append("Large spread suggests either a tag-mapping change between filings "
                 "or a cleaning-engine fix that improved one year but not others — "
                 "worth a closer look.\n")
    lines.append("| Ticker | Avg pass rate | YoY spread |")
    lines.append("|---|---:|---:|")
    for ticker, pr, spread in regressions:
        lines.append(f"| {ticker} | {pr*100:.1f}% | {spread*100:.1f}pp |")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))


_FINDINGS = """
## What this catches that single-FY doesn't

The single-FY validator (`scripts/validate_fmp.py`) tells you whether the
latest cleaned record matches FMP. The historical validator tells you
whether the cleaning pipeline produces *consistent* output across years.
Use it to catch:

- **Tag-mapping regressions.** A filer changes which us-gaap tag they use
  between FY filings. The single-FY check passes for the new year but the
  prior years show drift you didn't see at clean-time.
- **Restatement leakage.** When an issuer restates prior-year numbers, FMP
  picks up the restatement quickly but our cached cleaned record is from
  the original filing. Drift increases for restated FYs.
- **Cleaning fix backports.** A fix shipped for the latest FY may not
  apply cleanly to the way the same field was filed 3-4 years ago — older
  years can drift more than newer ones.

A ticker with a flat ~85% pass rate across 5 years is more trustworthy
than one that's 95% on the latest FY but 60% three years back. The "YoY
spread" column in the rollup highlights tickers where this is happening.

## What's not covered

This validator skips the screening section (`P/E`, `EV/EBITDA`, etc.)
because the screening engine runs only on the latest FY in the cleaned
DataFrame. Historical screening would require running the screening
pipeline per-FY, which the engine wasn't designed for. The drift patterns
on screening multiples are price-timing-driven anyway and don't reveal
filing-level issues.
"""


# ────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────

def main(tickers: Optional[List[str]] = None, n_years: int = 5) -> None:
    tickers = tickers or DEFAULT_TICKERS
    print(f"\n  Validating {len(tickers)} tickers × last {n_years} FYs against FMP\n")

    histories: Dict[str, Dict[int, Dict[str, Any]]] = {}
    for t in tickers:
        print(f"  [{t}] running history…")
        h = validate_history(t, n_years=n_years)
        histories[t] = h
        for ln in render_ticker_history(t, h):
            print(ln)

    out = Path("docs") / "FMP_HISTORY_REPORT.md"
    write_markdown_report(histories, out)
    print(f"\n  Report written to {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    n_years = 5
    if "--years" in args:
        i = args.index("--years")
        try:
            n_years = int(args[i + 1])
            args = args[:i] + args[i + 2:]
        except (IndexError, ValueError):
            print("Usage: --years N (must be a positive integer)", file=sys.stderr)
            sys.exit(1)
    if args:
        main([t.upper() for t in args], n_years=n_years)
    else:
        main(n_years=n_years)
