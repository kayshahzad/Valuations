"""
scripts/validate_sec.py

Validate Aletheia cleaned records against SEC XBRL companyfacts (the
authoritative source — same data as SEC's quarterly Financial Statement
Data Sets, just shaped per-filer). Free, covers every SEC filer including
the FMP-restricted tickers.

For each ticker x fiscal_year, the validator pulls the canonical us-gaap
(or IFRS) value for a small set of bottom-line fields and compares to the
cleaned record's `raw_<field>`. Drift bands match the FMP harness:
  ✓ <1%   ≈ 1-5%   ✗ >5%

Usage:
    PYTHONPATH=. python3 scripts/validate_sec.py [TICKER] [TICKER...]
    PYTHONPATH=. python3 scripts/validate_sec.py            # full universe

Output: docs/SEC_VALIDATION_REPORT.md and per-ticker tables to stdout.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from aletheia.data.sec_xbrl_validator import CANONICAL_TAGS, lookup_xbrl


# Aletheia's raw_<field> name → canonical SEC field name (CANONICAL_TAGS key)
FIELDS_TO_VALIDATE: List[Tuple[str, str, str]] = [
    # (display_label, sec_canonical_field, our_raw_key)
    ("Revenue",          "Revenue",          "Revenue"),
    ("Net Income",       "NetIncome",        "NetIncome"),
    ("Total Assets",     "TotalAssets",      "TotalAssets"),
    ("Total Liabilities","TotalLiabilities", "TotalLiabilities"),
    ("Total Equity",     "TotalEquity",      "TotalEquity"),
    ("Cash",             "Cash",             "Cash"),
    ("Long-Term Debt",   "LongTermDebt",     "LongTermDebt"),
    ("Operating CF",     "OperatingCF",      "OperatingCF"),
]

TOL_OK = 0.01
TOL_NEAR = 0.05


def _drift(ours: Optional[float], theirs: Optional[float]) -> Tuple[str, Optional[float]]:
    if ours is None and theirs is None:
        return "—", None
    if ours is None:
        return "ours_missing", None
    if theirs is None:
        return "sec_missing", None
    if abs(theirs) < 1e-6:
        return ("✓", 0.0) if abs(ours) < 1e-6 else ("✗", float("inf"))
    d = (ours - theirs) / abs(theirs)
    flag = "✓" if abs(d) < TOL_OK else ("≈" if abs(d) < TOL_NEAR else "✗")
    return flag, d


def validate_ticker(ticker: str, fy: Optional[int] = None) -> Dict[str, Any]:
    from aletheia.utils.calc_input_builder import make_calc_input

    calc = make_calc_input(ticker)
    df = calc.df
    if df.empty:
        return {"ticker": ticker, "error": "no cleaned data in DB"}
    if fy is None:
        fy = int(df["fiscal_year"].max())
    matched = df[df["fiscal_year"] == fy]
    if matched.empty:
        return {"ticker": ticker, "error": f"no FY{fy} record"}
    row = matched.iloc[0]
    raw_json = json.loads(row.get("raw_json") or "{}")

    # Some filers (HD, LOW, COST) have non-calendar fiscal years that end
    # in January/February of the following calendar year — HD's "FY2025"
    # ends 2026-02-01. The SEC XBRL fact list uses `end` as the period-end
    # date; matching on the fiscal_year label alone would miss those facts.
    # Use the period-end date's calendar year instead.
    period_end = row.get("period_end_date")
    match_year = fy
    try:
        if period_end is not None:
            # period_end_date can be a string or datetime depending on driver
            year_str = str(period_end)[:4]
            if year_str.isdigit():
                match_year = int(year_str)
    except Exception:
        pass

    rows = []
    for label, sec_field, our_key in FIELDS_TO_VALIDATE:
        sec_fact = lookup_xbrl(ticker, sec_field, match_year)
        sec_val = sec_fact.value if sec_fact else None
        our_val = raw_json.get(our_key)
        try:
            our_val = float(our_val) if our_val is not None else None
        except (TypeError, ValueError):
            our_val = None
        flag, d = _drift(our_val, sec_val)
        rows.append({
            "label":   label,
            "sec":     sec_val,
            "sec_tag": sec_fact.tag if sec_fact else None,
            "ours":    our_val,
            "drift":   d,
            "flag":    flag,
        })

    return {"ticker": ticker, "fiscal_year": fy, "rows": rows}


def _fmt_val(v: Optional[float]) -> str:
    if v is None: return "—"
    if abs(v) >= 1e9: return f"${v/1e9:>10,.2f}B"
    if abs(v) >= 1e6: return f"${v/1e6:>10,.0f}M"
    return f"{v:>11,.2f}"


def _fmt_drift(d: Optional[float]) -> str:
    if d is None: return "—"
    if d == float("inf"): return "∞"
    return f"{d*100:>+6.2f}%"


def render(result: Dict[str, Any]) -> List[str]:
    lines = []
    t = result.get("ticker", "?")
    lines.append(f"\n{'=' * 92}")
    lines.append(f"  {t}  FY{result.get('fiscal_year', '?')}")
    lines.append("=" * 92)
    if result.get("error"):
        lines.append(f"  ERROR: {result['error']}")
        return lines
    lines.append(f"  {'metric':<22}{'SEC value':>16}{'Our raw':>16}{'drift':>10}  {'flag':<6}{'tag'}")
    lines.append("  " + "-" * 88)
    for r in result.get("rows", []):
        lines.append(
            f"  {r['label']:<22}{_fmt_val(r['sec']):>16}{_fmt_val(r['ours']):>16}"
            f"{_fmt_drift(r['drift']):>10}  {r['flag']:<6}{r['sec_tag'] or '-'}"
        )
    rows = result.get("rows", [])
    n_ok = sum(1 for r in rows if r["flag"] == "✓")
    n_near = sum(1 for r in rows if r["flag"] == "≈")
    n_bad = sum(1 for r in rows if r["flag"] == "✗")
    n_missing = sum(1 for r in rows if r["flag"] in ("ours_missing", "sec_missing", "—"))
    lines.append(f"\n  Summary: ✓ {n_ok}  ≈ {n_near}  ✗ {n_bad}  missing {n_missing}  ({len(rows)} fields)")
    return lines


_FINDINGS = """
## What this validates

The validator pulls the canonical us-gaap (or IFRS-full) tagged value
straight from each filer's SEC XBRL companyfacts and compares it byte-for-
byte to our cleaned `raw_<field>`. Because both sides ultimately come from
the same 10-K filing, byte-perfect agreement is expected; any drift means
either:

1. Our tag resolver picked a tag the issuer no longer files, leaving us with
   a stale or null value (look at fields with `ours_missing`).
2. The issuer files under a non-canonical tag name we haven't mapped (for
   example `RegulatedAndUnregulatedOperatingRevenue` for utilities).
3. The issuer files multiple variants of the same concept (e.g., Revenue
   tagged twice with slightly different scopes — common in 20-F filings) and
   we picked a different variant than the filer's primary.

This is structurally different from FMP validation: FMP applies its own
normalization (aggregating receivables, bundling lease ROU into PPE,
adding SBC back into EBITDA), so drift there is almost always a documented
normalization difference. SEC drift, in contrast, is almost always either
a tag-mapping fix or a known multi-variant disclosure.

## Coverage

This source closes the FMP-restricted gap: every SEC filer in our universe
has a companyfacts file, including the 11 tickers FMP's free tier blocks
(LLY, ASML, SMCI, ABT, BRK-B, CAT, CNC, NEE, ORCL, QCOM, TXN). It is also
the first authoritative validation for the bank (JPM) and 20-F filers
(ASML, TSM) where FMP either reclassifies (banks) or returns local-currency
statements (TSM in TWD).
"""


def write_markdown(results: List[Dict[str, Any]], out: Path) -> None:
    lines = ["# SEC XBRL Validation Report\n"]
    lines.append(f"**Generated:** {date.today().isoformat()}  ")
    lines.append(f"**Source:** `valuation_data/raw/sec/companyfacts/CIK*.json` (per-filer SEC bulk XBRL)  ")
    lines.append("**Tolerance:** ✓ <1%, ≈ 1-5%, ✗ >5%  ")
    lines.append("")
    lines.append(_FINDINGS)
    lines.append("\n## Summary by ticker\n")
    lines.append("| Ticker | FY | ✓ | ≈ | ✗ | missing | total |")
    lines.append("|---|---|---:|---:|---:|---:|---:|")
    for r in results:
        if r.get("error"):
            lines.append(f"| {r['ticker']} | — | — | — | — | — | _error: {r['error']}_ |")
            continue
        rows = r.get("rows", [])
        n_ok = sum(1 for x in rows if x["flag"] == "✓")
        n_near = sum(1 for x in rows if x["flag"] == "≈")
        n_bad = sum(1 for x in rows if x["flag"] == "✗")
        n_miss = sum(1 for x in rows if x["flag"] in ("ours_missing", "sec_missing", "—"))
        lines.append(f"| {r['ticker']} | {r['fiscal_year']} | {n_ok} | {n_near} | {n_bad} | {n_miss} | {len(rows)} |")

    for r in results:
        lines.append(f"\n---\n## {r['ticker']} FY{r.get('fiscal_year', '?')}\n")
        if r.get("error"):
            lines.append(f"_Error: {r['error']}_\n")
            continue
        lines.append("| Metric | SEC value | Our raw | Drift | Flag | SEC tag |")
        lines.append("|---|---:|---:|---:|:---:|---|")
        for x in r.get("rows", []):
            lines.append(
                f"| {x['label']} | {_fmt_val(x['sec']).strip()} | "
                f"{_fmt_val(x['ours']).strip()} | {_fmt_drift(x['drift']).strip()} | "
                f"{x['flag']} | `{x['sec_tag'] or '-'}` |"
            )

    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines))


DEFAULT_TICKERS = [
    "AAPL", "MSFT", "LLY", "COST", "ASML", "SMCI", "GOOGL",
    "ABT", "AMD", "AMZN", "BRK-B", "CAT", "CNC", "JPM", "META",
    "NEE", "NVDA", "ORCL", "QCOM", "TSLA", "TSM", "TXN", "UNH", "V", "WMT",
    # 2026-05 expansion
    "KO", "PEP", "PG", "JNJ", "MRK", "MDT", "HD", "LOW",
    "UNP", "NSC", "ITW", "EMR", "MCO", "AXP", "ACN",
]


def main(tickers: Optional[List[str]] = None) -> None:
    tickers = tickers or DEFAULT_TICKERS
    print(f"\n  Validating {len(tickers)} tickers against SEC XBRL: {', '.join(tickers)}\n")
    results = []
    for t in tickers:
        print(f"  [{t}] looking up SEC tags...")
        r = validate_ticker(t)
        results.append(r)
        for ln in render(r):
            print(ln)
    out = Path("docs") / "SEC_VALIDATION_REPORT.md"
    write_markdown(results, out)
    print(f"\n  Report written to {out}")


if __name__ == "__main__":
    args = sys.argv[1:]
    main([t.upper() for t in args] if args else None)
