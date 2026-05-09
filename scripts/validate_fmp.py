"""
scripts/validate_fmp.py

FMP-vs-cleaned validation harness — CLI + Markdown report.

The comparison machinery (field maps, drift calc, validate_ticker)
moved to `aletheia/data/fmp_validation_core.py` so it can be reused
by Gate orchestrators (A/B/D), UI consumers, and ad-hoc scripts.

This script is now a thin shim that:
  - re-exports `validate_ticker` (and the field tuples) for legacy
    UI imports (`from scripts.validate_fmp import validate_ticker`)
    in quality_report.py / validation_badge.py / add_ticker_pipeline.py
  - provides the human-readable terminal renderer (`render_ticker_report`)
  - writes the Markdown report (`write_markdown_report`) consumed by
    docs/FMP_VALIDATION_REPORT.md

Usage:
    PYTHONPATH=. python3 scripts/validate_fmp.py [TICKER] [TICKER...]

If no tickers given, runs on the default 40-ticker universe baseline.
Output: docs/FMP_VALIDATION_REPORT.md plus per-ticker tables to stdout.
"""

from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional

# Re-export the library surface so `from scripts.validate_fmp import validate_ticker`
# keeps working byte-perfect for the 3 UI consumers + any external callers.
from aletheia.data.fmp_validation_core import (   # noqa: F401
    INCOME_FIELDS,
    BALANCE_FIELDS,
    CASHFLOW_FIELDS,
    DERIVED_FIELDS,
    SCREENING_FIELDS,
    TOL_OK,
    TOL_NEAR,
    validate_ticker,
)

# Explicit re-export — silences pyright's "unused import" on these.
__all__ = [
    "INCOME_FIELDS", "BALANCE_FIELDS", "CASHFLOW_FIELDS",
    "DERIVED_FIELDS", "SCREENING_FIELDS",
    "TOL_OK", "TOL_NEAR",
    "validate_ticker",
    "render_ticker_report", "write_markdown_report", "main",
    "DEFAULT_TICKERS",
]


# ────────────────────────────────────────────────────────────────────────
# CLI / report-only formatters (NOT used by gate orchestrators —
# orchestrators emit structured JSON for the _validation block instead)
# ────────────────────────────────────────────────────────────────────────

def _fmt_val(v: Optional[float]) -> str:
    if v is None: return "—"
    if abs(v) >= 1e9: return f"${v/1e9:>10,.2f}B"
    if abs(v) >= 1e6: return f"${v/1e6:>10,.0f}M"
    if abs(v) >= 1: return f"{v:>11,.2f}"
    return f"{v:>11,.4f}"


def _fmt_drift(d: Optional[float]) -> str:
    if d is None: return "—"
    if d == float("inf"): return "∞"
    return f"{d*100:>+6.2f}%"


def render_ticker_report(result: Dict[str, Any]) -> List[str]:
    lines = []
    ticker = result.get("ticker", "?")
    lines.append(f"\n{'=' * 92}")
    lines.append(f"  {ticker}  FY{result.get('fiscal_year', '?')}")
    lines.append("=" * 92)
    if result.get("error"):
        lines.append(f"  ERROR: {result['error']}")
        return lines

    for section_name, section_data in [
        ("Income Statement", result.get("income", [])),
        ("Balance Sheet",    result.get("balance", [])),
        ("Cash Flow",        result.get("cashflow", [])),
        ("Derived metrics + ratios", result.get("derived", [])),
        ("Screening ratios", result.get("screening", [])),
    ]:
        lines.append(f"\n  {section_name}")
        lines.append(f"  {'metric':<24}{'FMP':>16}{'Ours':>16}{'drift':>10}  flag")
        lines.append("  " + "-" * 75)
        for r in section_data:
            lines.append(
                f"  {r['label']:<24}{_fmt_val(r['fmp']):>16}{_fmt_val(r['ours']):>16}"
                f"{_fmt_drift(r['drift']):>10}  {r['flag']}"
            )

    all_rows = (result.get("income", []) + result.get("balance", []) +
                result.get("cashflow", []) + result.get("derived", []) +
                result.get("screening", []))
    n_ok = sum(1 for r in all_rows if r["flag"] == "✓")
    n_near = sum(1 for r in all_rows if r["flag"] == "≈")
    n_bad = sum(1 for r in all_rows if r["flag"] == "✗")
    n_missing = sum(1 for r in all_rows if r["flag"] in ("ours_missing", "fmp_missing", "—", "n/a (schema)"))
    total = len(all_rows)
    lines.append(f"\n  Summary: ✓ {n_ok}  ≈ {n_near}  ✗ {n_bad}  missing {n_missing}  ({total} fields total)")
    return lines


# ────────────────────────────────────────────────────────────────────────
# Markdown report (preserved verbatim from previous incarnation)
# ────────────────────────────────────────────────────────────────────────

_FINDINGS_SECTION = """
## Findings — TL;DR

The harness compares **45 fields per ticker** (9 income + 14 balance + 5
cash-flow + 6 derived ratios + 11 screening ratios) across all 40 universe
tickers. The screening ratios layer cross-checks the screening-engine
output (P/E, P/B, EV/EBITDA, EV/FCF, Debt-to-Equity, Current Ratio,
Interest Coverage, Net Debt / EBITDA, Dividend Yield, EV, Market Cap)
end-to-end, validating the same numbers the dashboard's screening tab
displays. Two classes of result remain:

1. **Validated** — most tickers. FMP returns USD statements and matches
   our cleaned data on the conventional fields. Bank/insurer/utility/
   conglomerate tickers (JPM, BRK-B, UNH, CNC, NEE) show many ✗ and
   missing flags by design — their schemas don't map to the standard
   income statement / balance sheet layout. ROIC and Invested Capital
   are explicitly suppressed for those filers via
   `business_model != fcff_compatible`.
2. **Currency-mismatched** — ASML files 20-F under EUR; TSM under TWD.
   FMP returns the home-currency statements; comparison is not meaningful.
   Harness skips with a clear flag.

### Documented normalization-difference patterns

Every ✗ flag observed in validated tickers fits one of these patterns. None
are data errors in the cleaned records — values reconcile exactly when you
add back the granular fields we keep separate but FMP aggregates:

| Drift pattern                        | Cause                                                      | Reconciles |
|--------------------------------------|------------------------------------------------------------|---|
| Accounts Receivable (AAPL/UNH/V)     | FMP `netReceivables` aggregates trade AR + other/vendor receivables | ✓ exact |
| Short-Term Debt (AAPL/WMT)           | FMP `shortTermDebt` aggregates commercial paper + current portion of LTD | ✓ exact |
| PPE Net (MSFT/COST/GOOGL/AMZN/NVDA/WMT) | FMP `propertyPlantEquipmentNet` includes Operating Lease ROU assets | ✓ exact |
| EBITDA (most tickers, -5 to -16%)    | FMP `ebitda` adds back stock-based compensation; ours = OpInc + D&A. See `clean_EBITDA_ExcludingSBC` for the FMP-pattern parallel field. | ✓ to within SBC |
| ROIC (most tickers, +30 to +60%)     | FMP's `returnOnInvestedCapital` divides by *operating-side* invested capital (NWC + Net PP&E); ours divides by *financing-side* (Equity + Debt − Cash). Both standard definitions. | definitional |
| Invested Capital (large drifts)      | Same root cause: FMP `investedCapital` = NWC + Net PP&E; ours = Equity + Total Debt − Cash. | definitional |
| Margin %, ROE                        | Byte-perfect or within SBC across every ticker — these are robust to definitional choices. | ✓ |
| Screening multiples (P/E, P/B, EV/EBITDA, EV/FCF, EV, Market Cap, Dividend Yield) | FMP uses period-end price (FY close); our screening uses current market price. ~+10% drift is the price move since fiscal-year-end. | price-timing |
| Net Debt / EBITDA                    | We subtract long-term marketable securities from gross debt (AAPL has $77B securities portfolio); FMP doesn't. Cash-rich tech tickers can flip from net-cash (us) to net-debt (FMP). | definitional |
| Debt-to-Equity (some tickers)        | FMP sometimes folds operating-lease debt into total debt. Ours uses financial debt only. | definitional |
| ROIC / Invested Capital for JPM, BRK-B, NEE, UNH, CNC | Suppressed entirely (`n/a (schema)`) — invested-capital ratios don't apply to bank, insurer, conglomerate, or regulated-utility balance sheets. | schema |

### FMP rate limits

The current FMP plan provides 300 calls/minute and full coverage of the
universe. A full 40-ticker run is ~200 calls (5 endpoints × 40 tickers)
and completes in well under a minute. The harness retains the
quota-exhaustion + stale-cache fallback paths.
"""


def write_markdown_report(results: List[Dict[str, Any]], out_path: Path) -> None:
    lines = []
    lines.append("# FMP Validation Report\n")
    lines.append(f"**Generated:** {date.today().isoformat()}  ")
    lines.append(f"**Tickers:** {', '.join(r['ticker'] for r in results)}  ")
    lines.append(f"**Tolerance:** ✓ <1% drift, ≈ 1-5%, ✗ >5%  ")
    lines.append("**Source A:** FMP `income-statement`, `balance-sheet-statement`, `cash-flow-statement` (annual)  ")
    lines.append("**Source B:** Aletheia cleaned records via `make_calc_input`  \n")

    lines.append(_FINDINGS_SECTION)

    # Aggregate summary
    lines.append("## Summary by ticker\n")
    lines.append("| Ticker | FY | ✓ | ≈ | ✗ | missing | total |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['ticker']} | — | — | — | — | — | _error: {r['error']}_ |")
            continue
        rows = (r.get("income", []) + r.get("balance", []) +
                r.get("cashflow", []) + r.get("derived", []) +
                r.get("screening", []))
        n_ok = sum(1 for x in rows if x["flag"] == "✓")
        n_near = sum(1 for x in rows if x["flag"] == "≈")
        n_bad = sum(1 for x in rows if x["flag"] == "✗")
        n_missing = sum(1 for x in rows if x["flag"] in ("ours_missing", "fmp_missing", "—", "n/a (schema)"))
        lines.append(f"| {r['ticker']} | {r['fiscal_year']} | {n_ok} | {n_near} | {n_bad} | {n_missing} | {len(rows)} |")

    # Per-ticker detail
    for r in results:
        ticker = r["ticker"]
        lines.append(f"\n---\n## {ticker} FY{r.get('fiscal_year', '?')}\n")
        if r.get("error"):
            lines.append(f"_Error: {r['error']}_\n")
            continue
        for section_name, section_data in [
            ("Income Statement", r.get("income", [])),
            ("Balance Sheet",    r.get("balance", [])),
            ("Cash Flow",        r.get("cashflow", [])),
            ("Derived metrics + ratios", r.get("derived", [])),
            ("Screening ratios", r.get("screening", [])),
        ]:
            lines.append(f"\n### {section_name}\n")
            lines.append("| Metric | FMP | Ours | Drift | Flag |")
            lines.append("|---|---:|---:|---:|:---:|")
            for x in section_data:
                lines.append(
                    f"| {x['label']} | {_fmt_val(x['fmp']).strip()} | "
                    f"{_fmt_val(x['ours']).strip()} | {_fmt_drift(x['drift']).strip()} | {x['flag']} |"
                )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))


# ────────────────────────────────────────────────────────────────────────
# Entry point
# ────────────────────────────────────────────────────────────────────────

DEFAULT_TICKERS = [
    "AAPL", "MSFT", "LLY", "COST", "ASML", "SMCI", "GOOGL",
    "ABT", "AMD", "AMZN", "BRK-B", "CAT", "CNC", "JPM", "META",
    "NEE", "NVDA", "ORCL", "QCOM", "TSLA", "TSM", "TXN", "UNH", "V", "WMT",
    "KO", "PEP", "PG", "JNJ", "MRK", "MDT", "HD", "LOW",
    "UNP", "NSC", "ITW", "EMR", "MCO", "AXP", "ACN",
]


def main(tickers: Optional[List[str]] = None) -> None:
    from aletheia.data import fmp_client
    if not fmp_client.has_api_key():
        print("\n  ERROR: FMP_API_KEY not configured.")
        print("  Sign up free at https://site.financialmodelingprep.com/developer/docs/dashboard")
        print("  Then add to .env: FMP_API_KEY=your_key_here\n")
        sys.exit(1)

    tickers = tickers or DEFAULT_TICKERS
    print(f"\n  Validating {len(tickers)} tickers against FMP: {', '.join(tickers)}\n")
    results = []
    for t in tickers:
        print(f"  [{t}] fetching + comparing...")
        r = validate_ticker(t)
        results.append(r)
        for line in render_ticker_report(r):
            print(line)

    out = Path("docs") / "FMP_VALIDATION_REPORT.md"
    write_markdown_report(results, out)
    print(f"\n  Report written to {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args:
        main([t.upper() for t in args])
    else:
        main()
