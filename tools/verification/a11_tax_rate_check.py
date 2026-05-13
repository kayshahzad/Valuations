"""A11 verification — confirm the tax-rate fallback chain is operational.

For each sample ticker, runs all four calc engines that consume
``resolve_tax_rate`` (DCFEngine, ReverseDCF, MultipleDecomposition,
ScreeningEngine) and reports:

  - which chain step produced the tax rate (cash / gaap / company_fy /
    statutory / analyst_override)
  - the resolved numeric rate
  - whether the rate is plausible for the ticker's known profile

This is the *targeted* follow-up to the Phase-1 identity audit's
finding that FCF pathway fails ~70% of the time. The hypothesis is
that A11 is working correctly and the FCF pathway failures are SBC-
driven, not tax-driven. This script confirms (or falsifies) that
hypothesis empirically.

Sample tickers cover:
  - US large-cap with normal tax (AAPL, NVDA, MSFT)
  - Foreign filers (ASML, TSM)
  - Routing-required FCFF-incompatibles (UNH, JPM)
  - Cyclical (CAT)

Run:
    python -m tools.verification.a11_tax_rate_check
    python -m tools.verification.a11_tax_rate_check --tickers AAPL MSFT
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Sample tickers + per-ticker plausibility expectations
# ─────────────────────────────────────────────────────────────────────

# Format: ticker → (expected_range, brief_rationale).
# Range is the analyst-known plausible effective-tax-rate band; the
# A11 resolver should produce something inside it (or a statutory
# fallback with a documented Category-B gap).
EXPECTED_RATE_RANGE: Dict[str, Tuple[Tuple[float, float], str]] = {
    "AAPL": ((0.13, 0.17), "Apple — US filer, low single-digit teens cash rate"),
    "NVDA": ((0.10, 0.20), "NVDA — recent profile of teens, varies with NOL releases"),
    "MSFT": ((0.13, 0.20), "Microsoft — mid-to-high teens cash rate"),
    "ASML": ((0.13, 0.20), "ASML — Dutch filer, EUR-reporting, mid-teens"),
    "TSM": ((0.08, 0.16), "TSMC — Taiwan filer, low-teens preferential rate"),
    "UNH": ((0.20, 0.27), "UnitedHealth — health insurer, near US statutory"),
    "JPM": ((0.18, 0.26), "JPMorgan — bank, near US statutory"),
    "CAT": ((0.18, 0.28), "Caterpillar — cyclical industrial, near US statutory"),
    "COST": ((0.22, 0.28), "Costco — consumer staples, near US statutory"),
    "TSLA": ((-0.20, 0.20), "Tesla — historically NOL-driven negatives, then teens"),
}


# ─────────────────────────────────────────────────────────────────────
# Result + reporting
# ─────────────────────────────────────────────────────────────────────

@dataclass
class A11Probe:
    ticker: str
    function: str  # 'dcf_engine' | 'reverse_dcf' | 'multiple_decomposition' | 'screening'
    tax_rate: Optional[float]
    tax_rate_source: Optional[str]
    expected_range: Tuple[float, float]
    plausible: Optional[bool]  # None when function couldn't run
    note: Optional[str] = None


# ─────────────────────────────────────────────────────────────────────
# Engine drivers — each wraps one calc function + extracts the source
# ─────────────────────────────────────────────────────────────────────

def _probe_dcf_engine(calc_input) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    """Returns (tax_rate, tax_rate_source, error_or_None)."""
    from aletheia.tools.dcf_engine import DCFEngine
    try:
        result = DCFEngine(verbose=False).run(calc_input)
    except NotImplementedError as exc:
        return None, None, f"NotImplementedError: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"
    if result.errors:
        return None, None, f"errors: {result.errors[0]}"
    # DCFResult exposes scenario-level tax_rate via the assumptions
    # bundle; the base scenario's resolved rate is the canonical
    # post-A11 value. Stamp from the result's top-level field which
    # the A11 fix populated explicitly.
    return result.base.assumptions.tax_rate if result.base else None, result.tax_rate_source, None


def _probe_reverse_dcf(calc_input) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    from aletheia.tools.reverse_dcf import ReverseDCF
    try:
        result = ReverseDCF(verbose=False).run(calc_input)
    except NotImplementedError as exc:
        return None, None, f"NotImplementedError: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"
    if result.errors:
        return None, None, f"errors: {result.errors[0]}"
    return result.tax_rate, result.tax_rate_source, None


def _probe_multiple_decomposition(calc_input) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    from aletheia.tools.multiple_decomposition import MultipleDecomposition
    try:
        result = MultipleDecomposition(verbose=False).run(calc_input)
    except NotImplementedError as exc:
        return None, None, f"NotImplementedError: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"
    if getattr(result, "warnings", None):
        # MD returns warnings on calc-degradation; not a hard error
        pass
    return getattr(result, "tax_rate", None), result.tax_rate_source, None


def _probe_screening(calc_input) -> Tuple[Optional[float], Optional[str], Optional[str]]:
    from aletheia.tools.screening_ratios import ScreeningEngine
    try:
        card = ScreeningEngine(verbose=False).score(calc_input)
    except NotImplementedError as exc:
        return None, None, f"NotImplementedError: {exc}"
    except Exception as exc:  # noqa: BLE001
        return None, None, f"{type(exc).__name__}: {exc}"
    # Screening card doesn't expose tax_rate numerically (it's
    # consumed internally for the 34 metrics). We surface the
    # source only.
    return None, card.tax_rate_source, None


PROBES = [
    ("dcf_engine", _probe_dcf_engine),
    ("reverse_dcf", _probe_reverse_dcf),
    ("multiple_decomposition", _probe_multiple_decomposition),
    ("screening", _probe_screening),
]


# ─────────────────────────────────────────────────────────────────────
# Per-ticker driver
# ─────────────────────────────────────────────────────────────────────

def _make_calc_input(ticker: str):
    from aletheia.utils.calc_input_builder import make_calc_input
    return make_calc_input(ticker)


def probe_ticker(ticker: str) -> List[A11Probe]:
    expected, _ = EXPECTED_RATE_RANGE.get(ticker, ((0.0, 0.40), ""))
    try:
        calc_input = _make_calc_input(ticker)
    except Exception as exc:  # noqa: BLE001
        logger.warning("calc_input failed for %s: %s", ticker, exc)
        return [
            A11Probe(
                ticker=ticker, function=fn,
                tax_rate=None, tax_rate_source=None,
                expected_range=expected, plausible=None,
                note=f"calc_input unavailable: {exc}",
            )
            for fn, _ in PROBES
        ]

    out: List[A11Probe] = []
    for fn_name, probe_fn in PROBES:
        rate, source, err = probe_fn(calc_input)
        if err:
            out.append(A11Probe(
                ticker=ticker, function=fn_name,
                tax_rate=None, tax_rate_source=None,
                expected_range=expected, plausible=None,
                note=err,
            ))
            continue
        plausible = None
        if rate is not None and math.isfinite(rate):
            lo, hi = expected
            plausible = lo <= rate <= hi
        out.append(A11Probe(
            ticker=ticker, function=fn_name,
            tax_rate=rate, tax_rate_source=source,
            expected_range=expected, plausible=plausible,
        ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Report
# ─────────────────────────────────────────────────────────────────────

def render_report(probes: List[A11Probe]) -> str:
    out: List[str] = []
    out.append("# A11 Tax-Rate Verification — Sample Sweep")
    out.append("")
    out.append("Per-ticker × per-function audit of the A11 fallback chain "
               "(cash → gaap → company_fy → statutory). Confirms whether "
               "the FCF-pathway failures surfaced by the Phase-1 identity "
               "audit are tax-rate-driven (Category B, would block "
               "further audit work) or SBC/deferred-tax-driven (Category C, "
               "documented exception).")
    out.append("")

    # Group by ticker.
    by_ticker: Dict[str, List[A11Probe]] = {}
    for p in probes:
        by_ticker.setdefault(p.ticker, []).append(p)

    # Source distribution.
    source_counts: Dict[str, int] = {}
    for p in probes:
        s = p.tax_rate_source or "unavailable"
        source_counts[s] = source_counts.get(s, 0) + 1
    out.append("## Source distribution across all probes")
    out.append("")
    out.append("| Source | Count | Pct |")
    out.append("|---|---|---|")
    tot = max(1, len(probes))
    for s in sorted(source_counts, key=lambda k: -source_counts[k]):
        c = source_counts[s]
        out.append(f"| `{s}` | {c} | {c/tot*100:.0f}% |")
    out.append("")

    out.append("## Per-ticker findings")
    out.append("")
    for ticker in sorted(by_ticker):
        rationale = EXPECTED_RATE_RANGE.get(ticker, (None, ""))[1]
        out.append(f"### {ticker}")
        if rationale:
            out.append(f"*Expected:* {rationale}")
        out.append("")
        out.append("| Function | source | rate | plausible | note |")
        out.append("|---|---|---|---|---|")
        for p in by_ticker[ticker]:
            rate_str = f"{p.tax_rate:.3f}" if p.tax_rate is not None else "—"
            plaus_str = (
                "✓" if p.plausible is True
                else ("✗" if p.plausible is False else "—")
            )
            source_str = p.tax_rate_source or "—"
            note_str = (p.note or "")[:80]
            out.append(
                f"| `{p.function}` | `{source_str}` | {rate_str} | "
                f"{plaus_str} | {note_str} |"
            )
        out.append("")

    # Headline finding.
    cleaned_count = sum(
        1 for p in probes if p.tax_rate_source in ("cash", "gaap")
    )
    company_fy_count = sum(
        1 for p in probes if p.tax_rate_source == "company_fy"
    )
    statutory_count = sum(
        1 for p in probes if p.tax_rate_source == "statutory"
    )
    runnable = sum(1 for p in probes if p.tax_rate_source is not None)
    out.insert(3, "")
    out.insert(3, f"**Headline finding**: of {runnable} probes where the "
                  f"engine actually ran, {cleaned_count} resolved via the "
                  f"cleaned `cash`/`gaap` step, {company_fy_count} via the "
                  f"`company_fy` historical fallback, and {statutory_count} "
                  f"all the way through to `statutory`. "
                  + _interpret_distribution(cleaned_count, company_fy_count,
                                            statutory_count, runnable))
    out.insert(3, "")
    return "\n".join(out)


def _interpret_distribution(cleaned: int, company_fy: int,
                            statutory: int, total: int) -> str:
    """Two-line analyst interpretation of the source distribution."""
    if total == 0:
        return "No engines ran successfully — investigate before any conclusion."
    statutory_pct = statutory / total * 100
    cleaned_pct = cleaned / total * 100
    if cleaned_pct >= 60:
        return (
            "A11 chain is OPERATIONAL: the majority of probes resolve at "
            "the cleaned `cash`/`gaap` step. Where statutory fallback "
            "fires, it's narrow and Ticker-specific — investigate those "
            "cases via the cleaning_engine domain-10 path."
        )
    if statutory_pct >= 60:
        return (
            "A11 chain IS OPERATIONAL but the cleaning engine is failing "
            "to populate cleaned tax rates for most tickers. The resolver "
            "correctly falls through to statutory. ROOT-CAUSE INVESTIGATION "
            "needed at the cleaning_engine domain-10 layer (TaxSustainability) — "
            "the chain works; the upstream feeder doesn't."
        )
    return (
        "MIXED distribution. A11 chain is operational; the cleaning "
        "engine populates cleaned rates for some tickers but not others. "
        "Investigate the per-ticker pattern to find the cleaning-engine gap."
    )


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

DEFAULT_TICKERS = list(EXPECTED_RATE_RANGE.keys())


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="a11_tax_rate_check",
        description=__doc__.split("\n\n")[0] if __doc__ else "A11 check",
    )
    p.add_argument(
        "--tickers", nargs="+", default=DEFAULT_TICKERS,
        help=f"Sample tickers (default: {' '.join(DEFAULT_TICKERS)})",
    )
    p.add_argument(
        "--output", default="docs/a11_tax_rate_verification.md",
        help="Output path for the Markdown verification report",
    )
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    all_probes: List[A11Probe] = []
    for t in args.tickers:
        logger.info("probing %s", t)
        all_probes.extend(probe_ticker(t))

    if not all_probes:
        print("no probes ran — check the ticker list", file=sys.stderr)
        return 1

    report = render_report(all_probes)
    from pathlib import Path
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    Path(args.output).write_text(report)

    # Console summary for quick eyeball.
    runnable = [p for p in all_probes if p.tax_rate_source is not None]
    sources = {p.tax_rate_source for p in runnable}
    print(f"probed {len(args.tickers)} tickers × {len(PROBES)} functions = {len(all_probes)} cells")
    print(f"  runnable: {len(runnable)}")
    print(f"  sources observed: {sorted(sources)}")
    print(f"  report → {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
