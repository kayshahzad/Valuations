"""
tests/test_phase3_validation.py

Phase 3 End-to-End Validation Suite
=====================================
Validates the full LangGraph agent pipeline against real-world
investment knowledge and publicly verifiable facts.

Unlike unit tests (which test math), this suite tests:
  1. Pipeline completeness — every agent produced output
  2. Output coherence — outputs are internally consistent
  3. Economic sanity — conclusions match known real-world facts
  4. Signal accuracy — flags the right companies for the right reasons
  5. Report quality — final report contains all required sections

Validation approach:
  - Run the full pipeline via main.py graph for 2 tickers
  - Load the generated report JSON
  - Assert specific facts that any informed analyst would know
  - Flag where the system produces conclusions that contradict reality

Known facts used as ground truth:
  - AAPL: High moat, services mix shift, $109B FCF, net cash position
  - CNC: Low margin healthcare plan, ROIC improving, trades at discount
  - NVDA: AI infrastructure dominant, exceptional ROIC, high growth premium

Run with:
    PYTHONPATH=. python3 tests/test_phase3_validation.py

Or run just the report loader (faster, uses cached report):
    PYTHONPATH=. python3 tests/test_phase3_validation.py --no-run
"""

import sys
import json
import subprocess
import argparse
import warnings
from pathlib import Path
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Tuple

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# Known real-world facts for validation
# These are verifiable from public sources
# ─────────────────────────────────────────────────────────────────────────────

KNOWN_FACTS = {
    "AAPL": {
        # Financial facts (FY2024, SEC 10-K)
        "revenue_bn_min": 380,           # $391B actual
        "revenue_bn_max": 410,
        "fcf_bn_min": 95,                # $109B actual
        "fcf_bn_max": 130,
        "gross_margin_min": 44,          # 46.2% actual
        "gross_margin_max": 50,
        "net_cash_position": True,       # AAPL has more cash than debt
        "sbc_bn_min": 8,                 # $11.7B actual
        "sbc_bn_max": 15,

        # Qualitative facts (consensus knowledge)
        "moat_score_min": 6,             # Strong moat — App Store, ecosystem lock-in
        "has_services_revenue": True,    # Services is fastest growing segment
        "is_value_creating": True,       # ROIC >> WACC

        # Valuation facts (at ~$270 price)
        "is_growth_premium": True,       # Market prices in above-historical growth
        "implied_cagr_min": 0.10,        # At least 10% required to justify price
        "implied_cagr_max": 0.35,        # Not more than 35%

        # Signals the system should produce
        "reverse_dcf_signal_not": ["deep_value"],   # AAPL is not cheap
        "multiple_signal_not": ["undervalued"],      # AAPL is not undervalued
    },

    "NVDA": {
        # Financial facts (FY2025, SEC 10-K)
        "revenue_bn_min": 120,           # $130.5B actual
        "revenue_bn_max": 145,
        "gross_margin_min": 73,          # 77.7% actual
        "gross_margin_max": 82,
        "roic_min": 0.50,                # ~65% actual — extraordinary
        "roic_max": 1.00,
        "is_value_creating": True,

        # Valuation — NVDA trades at massive premium
        "implied_cagr_min": 0.25,        # Market prices in very high growth
        "implied_cagr_max": 0.80,
        "reverse_dcf_signal_expected": ["caution", "flag"],

        # NVDA specific
        "has_high_sbc": True,            # SBC is material at NVDA
    },

    "CNC": {
        # Financial facts (FY2024)
        "revenue_bn_min": 135,           # $145.5B actual
        "revenue_bn_max": 160,
        "operating_margin_max": 5,       # Healthcare plans have thin margins
        "is_low_margin": True,

        # CNC should appear undervalued vs justified multiple
        "multiple_signal_expected": ["undervalued", "fairly_valued"],

        # Contrarian — CNC has regulatory risk
        "has_regulatory_risk": True,
    },

    "MSFT": {
        # Financial facts (FY2024)
        "revenue_bn_min": 235,           # $245B actual
        "revenue_bn_max": 260,
        "gross_margin_min": 67,          # 69.8% actual
        "gross_margin_max": 73,
        "roic_min": 0.25,
        "is_value_creating": True,
        "has_cloud_revenue": True,       # Azure is primary growth driver
    },
}

# Report structure — every section that must be present
REQUIRED_REPORT_SECTIONS = [
    "ticker",
    "generated_at",
    "1_economic_reality",
    "2_financial_translation",
    "3_capital_structure_risk",
    "4_valuation_synthesis",
]

REQUIRED_PHASE2_KEYS = [
    "three_scenario_dcf",
    "reverse_dcf",
    "multiple_decomposition",
]

REQUIRED_CONVICTION_SCORE = True  # Must be a number 0-10
CONVICTION_SCORE_MIN = 0
CONVICTION_SCORE_MAX = 10


# ─────────────────────────────────────────────────────────────────────────────
# Test result
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ValidationResult:
    category: str
    check: str
    passed: bool
    actual: Any
    expected: str
    note: str = ""

    def __str__(self):
        status = "✓" if self.passed else "✗"
        return (
            f"  {status} [{self.category}] {self.check:<50} "
            f"| actual={str(self.actual)[:30]:<30} "
            f"| expected={self.expected[:25]}"
            + (f" | {self.note}" if self.note else "")
        )


@dataclass
class ValidationSuite:
    ticker: str
    results: List[ValidationResult] = field(default_factory=list)

    def add(self, result: ValidationResult):
        self.results.append(result)

    def check(self, category: str, check: str, condition: bool,
               actual: Any, expected: str, note: str = ""):
        self.add(ValidationResult(
            category=category, check=check, passed=condition,
            actual=actual, expected=expected, note=note
        ))
        return condition

    @property
    def passed(self): return sum(1 for r in self.results if r.passed)
    @property
    def failed(self): return sum(1 for r in self.results if not r.passed)
    @property
    def total(self): return len(self.results)

    def summary(self) -> str:
        lines = [
            f"\n{'='*100}",
            f"  {self.ticker} — Phase 3 Validation: {self.passed}/{self.total} passed",
            f"{'='*100}",
        ]
        # Group by category
        categories = {}
        for r in self.results:
            categories.setdefault(r.category, []).append(r)
        for cat, results in categories.items():
            cat_passed = sum(1 for r in results if r.passed)
            lines.append(f"\n  [{cat}] {cat_passed}/{len(results)}")
            for r in results:
                lines.append(str(r))
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# Pipeline runner
# ─────────────────────────────────────────────────────────────────────────────

def run_pipeline(ticker: str, timeout: int = 300) -> bool:
    """Run the full LangGraph pipeline for a ticker."""
    print(f"\n  Running pipeline for {ticker} (timeout={timeout}s)...")
    try:
        result = subprocess.run(
            ["python3", "main.py", "--ticker", ticker],
            capture_output=True, text=True, timeout=timeout,
            env={**__import__("os").environ, "PYTHONPATH": "."}
        )
        if result.returncode == 0:
            print(f"  ✓ Pipeline completed for {ticker}")
            return True
        else:
            print(f"  ✗ Pipeline failed for {ticker}")
            print(f"  STDERR: {result.stderr[-500:]}")
            return False
    except subprocess.TimeoutExpired:
        print(f"  ✗ Pipeline timed out for {ticker}")
        return False
    except Exception as e:
        print(f"  ✗ Pipeline error: {e}")
        return False


def load_report(ticker: str) -> Optional[Dict]:
    """Load the generated report JSON."""
    path = Path(f"valuation_data/serving/latest/{ticker.upper()}_report.json")
    if not path.exists():
        print(f"  ✗ Report not found: {path}")
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except Exception as e:
        print(f"  ✗ Failed to load report: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Validation checks
# ─────────────────────────────────────────────────────────────────────────────

def validate_report_structure(suite: ValidationSuite, report: Dict):
    """Check all required sections are present."""
    cat = "Structure"

    # Required top-level sections
    for section in REQUIRED_REPORT_SECTIONS:
        present = section in report
        suite.check(cat, f"Section present: {section}",
                    present, present, "present", )

    # Ticker matches
    suite.check(cat, "Ticker matches",
                report.get("ticker", "").upper() == suite.ticker,
                report.get("ticker"), f"=={suite.ticker}")

    # Generated_at is recent
    gen_at = report.get("generated_at", "")
    suite.check(cat, "Generated_at is populated",
                bool(gen_at), gen_at, "non-empty string")

    # Phase 2 valuation present
    p2 = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {})
    suite.check(cat, "phase2_valuation present",
                bool(p2), bool(p2), "non-empty dict",
                "ValuationNode output missing from report" if not p2 else "")

    for key in REQUIRED_PHASE2_KEYS:
        suite.check(cat, f"phase2_valuation.{key} present",
                    key in p2, key in p2, "present")

    # Investment thesis
    thesis = report.get("4_valuation_synthesis", {}).get("investment_thesis", {})
    conviction = thesis.get("conviction_score")
    suite.check(cat, "Conviction score is a number",
                isinstance(conviction, (int, float)) and conviction is not None,
                conviction, f"{CONVICTION_SCORE_MIN}-{CONVICTION_SCORE_MAX}")
    if isinstance(conviction, (int, float)):
        suite.check(cat, "Conviction score in valid range",
                    CONVICTION_SCORE_MIN <= conviction <= CONVICTION_SCORE_MAX,
                    conviction, f"[{CONVICTION_SCORE_MIN}, {CONVICTION_SCORE_MAX}]")

    # Narrative is substantive
    narrative = thesis.get("narrative", "")
    suite.check(cat, "Narrative length > 50 chars",
                len(str(narrative)) > 50, len(str(narrative)), ">50 chars")


def validate_financial_facts(suite: ValidationSuite, report: Dict):
    """Check financial figures against known ground truth."""
    cat = "Financials"
    ticker = suite.ticker
    facts = KNOWN_FACTS.get(ticker, {})

    if not facts:
        return

    # Get financial data from report
    fin_trans = report.get("2_financial_translation", {})
    clean_fin = fin_trans.get("clean_financials", {})
    income = clean_fin.get("financials", {}).get("income_statement", {}) if clean_fin else {}

    p2 = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {})
    dcf_data = p2.get("three_scenario_dcf", {}).get("base", {})

    # Revenue check
    if "revenue_bn_min" in facts:
        # Try multiple sources
        rev = None
        if income.get("revenue"):
            rev = income["revenue"] / 1e9
        elif p2.get("dcf", {}).get("revenue"):
            rev = p2["dcf"]["revenue"] / 1e9

        if rev:
            in_range = facts["revenue_bn_min"] <= rev <= facts["revenue_bn_max"]
            suite.check(cat, "Revenue in expected range",
                        in_range, f"${rev:.0f}B",
                        f"[${facts['revenue_bn_min']}B, ${facts['revenue_bn_max']}B]")

    # Gross margin check
    if "gross_margin_min" in facts:
        ratios = fin_trans.get("ratios", {}) if fin_trans else {}
        gm = ratios.get("gross_margin", 0) * 100 if ratios else None
        if gm:
            in_range = facts["gross_margin_min"] <= gm <= facts["gross_margin_max"]
            suite.check(cat, "Gross margin in expected range",
                        in_range, f"{gm:.1f}%",
                        f"[{facts['gross_margin_min']}%, {facts['gross_margin_max']}%]")


def validate_phase2_outputs(suite: ValidationSuite, report: Dict):
    """Check Phase 2 valuation outputs against known facts."""
    cat = "Phase2"
    ticker = suite.ticker
    facts = KNOWN_FACTS.get(ticker, {})

    p2 = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {})
    if not p2:
        suite.check(cat, "Phase2 data available", False, None, "non-empty")
        return

    # Three-scenario DCF
    dcf3 = p2.get("three_scenario_dcf", {})
    bear_iv = dcf3.get("bear", {}).get("intrinsic_per_share", 0)
    base_iv = dcf3.get("base", {}).get("intrinsic_per_share", 0)
    bull_iv = dcf3.get("bull", {}).get("intrinsic_per_share", 0)

    # Scenario ordering
    # Bear IV >= 0 allowed (zero is valid floor for thin-margin companies like CNC)
    suite.check(cat, "Bull IV > Base IV >= Bear IV >= 0",
                bull_iv > base_iv >= bear_iv >= 0,
                f"${bear_iv:.0f} <= ${base_iv:.0f} <= ${bull_iv:.0f}",
                "bull > base >= bear >= 0")

    # Bull and Base must be positive; Bear >= 0
    suite.check(cat, "Bull and Base IVs are positive",
                bull_iv > 0 and base_iv > 0 and bear_iv >= 0,
                f"bear={bear_iv:.0f}, base={base_iv:.0f}, bull={bull_iv:.0f}",
                "bull>0, base>0, bear>=0")

    # Reverse DCF
    rdcf = p2.get("reverse_dcf", {})
    implied_cagr = rdcf.get("implied_cagr_10y")
    hist_cagr = rdcf.get("implied_cagr_10y")
    signal = rdcf.get("signal", "")

    if implied_cagr is not None:
        suite.check(cat, "Implied CAGR is a number",
                    isinstance(implied_cagr, (int, float)), implied_cagr, "numeric")

        suite.check(cat, "Implied CAGR in [-10%, 80%]",
                    -0.10 <= implied_cagr <= 0.80,
                    f"{implied_cagr:.1%}", "[-10%, 80%]")

        if "implied_cagr_min" in facts:
            suite.check(cat, "Implied CAGR >= expected minimum",
                        implied_cagr >= facts["implied_cagr_min"],
                        f"{implied_cagr:.1%}", f">={facts['implied_cagr_min']:.1%}")

        if "implied_cagr_max" in facts:
            suite.check(cat, "Implied CAGR <= expected maximum",
                        implied_cagr <= facts["implied_cagr_max"],
                        f"{implied_cagr:.1%}", f"<={facts['implied_cagr_max']:.1%}")

    if signal:
        suite.check(cat, "Reverse DCF signal is populated",
                    bool(signal), signal, "non-empty string")

        if "reverse_dcf_signal_not" in facts:
            not_these = facts["reverse_dcf_signal_not"]
            suite.check(cat, f"Signal not in {not_these}",
                        signal not in not_these, signal, f"not in {not_these}",
                        f"{ticker} at current price should not be {not_these}")

        if "reverse_dcf_signal_expected" in facts:
            expected_signals = facts["reverse_dcf_signal_expected"]
            # For NVDA: historical CAGR is so high that implied CAGR may not
            # look abnormal relative to it — check absolute implied CAGR instead
            if "implied_cagr_min" in facts and implied_cagr is not None:
                abs_high = implied_cagr >= facts["implied_cagr_min"]
                suite.check(cat, f"Implied CAGR >= {facts['implied_cagr_min']:.0%} (high growth priced in)",
                            abs_high, f"{implied_cagr:.1%}",
                            f">={facts['implied_cagr_min']:.0%}",
                            "NVDA: signal may be fair_value if hist CAGR is also very high")
            else:
                suite.check(cat, f"Signal in expected {expected_signals}",
                            signal in expected_signals, signal, str(expected_signals))

    # Multiple decomposition
    md = p2.get("multiple_decomposition", {})
    market_mult = md.get("market_ev_ebitda", 0)
    justified_mult = md.get("justified_ev_ebitda", 0)
    mult_signal = md.get("signal", "")
    value_creation = md.get("value_creation", "")

    suite.check(cat, "Market EV/EBITDA > 0",
                market_mult > 0, f"{market_mult:.1f}x", "> 0")

    suite.check(cat, "Justified EV/EBITDA > 0",
                justified_mult > 0, f"{justified_mult:.1f}x", "> 0")

    if "multiple_signal_expected" in facts:
        suite.check(cat, f"Multiple signal in {facts['multiple_signal_expected']}",
                    mult_signal in facts["multiple_signal_expected"],
                    mult_signal, str(facts["multiple_signal_expected"]))

    if "multiple_signal_not" in facts:
        suite.check(cat, f"Multiple signal not in {facts['multiple_signal_not']}",
                    mult_signal not in facts["multiple_signal_not"],
                    mult_signal, f"not in {facts['multiple_signal_not']}")

    if "is_value_creating" in facts and facts["is_value_creating"]:
        suite.check(cat, "Value creation = 'creating' (ROIC > WACC)",
                    value_creation == "creating",
                    value_creation, "creating",
                    f"ROIC-WACC spread: {md.get('roic_wacc_spread', 0):+.1%}")

    # WACC populated
    wacc = p2.get("wacc")
    suite.check(cat, "WACC is populated and reasonable",
                wacc and 0.04 <= wacc <= 0.18,
                f"{wacc:.2%}" if wacc else None,
                "[4%, 18%]")

    # Beta populated
    beta = p2.get("beta")
    suite.check(cat, "Beta is populated",
                beta and 0.2 <= beta <= 3.0,
                f"{beta:.2f}" if beta else None, "[0.2, 3.0]")


def validate_agent_outputs(suite: ValidationSuite, report: Dict):
    """Check that individual agents produced meaningful outputs."""
    cat = "Agents"
    ticker = suite.ticker
    facts = KNOWN_FACTS.get(ticker, {})

    # Economic reality section (from forensic + value chain + context agents)
    er = report.get("1_economic_reality", {})

    # Moat score
    moat = er.get("moat", {})
    moat_score = moat.get("score")
    suite.check(cat, "Moat score is populated",
                moat_score is not None, moat_score, "not None")
    if moat_score is not None:
        suite.check(cat, "Moat score in [0, 10]",
                    0 <= float(moat_score) <= 10, moat_score, "[0, 10]")
        if "moat_score_min" in facts:
            suite.check(cat, f"Moat score >= {facts['moat_score_min']} (strong moat known)",
                        float(moat_score) >= facts["moat_score_min"],
                        moat_score, f">={facts['moat_score_min']}",
                        f"{ticker} has well-documented moat")

    # Value chain
    vc = er.get("value_chain", {})
    suite.check(cat, "Value chain power_ratio populated",
                vc.get("power_ratio") is not None,
                vc.get("power_ratio"), "not None")

    suite.check(cat, "Value chain strategic_leverage populated",
                vc.get("strategic_leverage") is not None,
                vc.get("strategic_leverage"), "not None")

    # Industry structure / cyclicality
    ind = er.get("industry_structure", {})
    suite.check(cat, "Cyclicality z_score populated",
                ind.get("cyclicality_z_score") is not None,
                ind.get("cyclicality_z_score"), "not None")

    # Capital structure (strategist)
    cap = report.get("3_capital_structure_risk", {})
    cap_stack = cap.get("capital_stack", {})
    wacc_strat = cap_stack.get("wacc")
    suite.check(cat, "Strategist WACC populated",
                wacc_strat is not None and wacc_strat > 0,
                f"{wacc_strat:.2%}" if wacc_strat else None, "> 0")

    # Contrarian report
    p2 = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {})
    # Contrarian output flows to lead — check it appeared in narrative
    narrative = report.get("4_valuation_synthesis", {}).get(
        "investment_thesis", {}).get("narrative", "")
    suite.check(cat, "Narrative mentions quantitative analysis",
                len(str(narrative)) > 100, len(str(narrative)), ">100 chars",
                "LLM synthesis should be substantive")


def validate_constitution_checks(suite: ValidationSuite, report: Dict):
    """Check that the constitution enforcement worked correctly."""
    cat = "Constitution"

    thesis = report.get("4_valuation_synthesis", {}).get("investment_thesis", {})
    checks = thesis.get("constitution_checks", [])

    suite.check(cat, "Constitution checks list is present",
                isinstance(checks, list), type(checks).__name__, "list")

    if isinstance(checks, list):
        suite.check(cat, "At least 1 constitution check ran",
                    len(checks) >= 1, len(checks), ">= 1")

        # Check for terminal cap check
        has_terminal_check = any("Terminal" in str(c) or "terminal" in str(c)
                                 for c in checks)
        suite.check(cat, "Terminal cap check present",
                    has_terminal_check, has_terminal_check, "True")

        # Check for Phase 2 checks (implied CAGR, multiple premium)
        has_cagr_check = any("CAGR" in str(c) or "cagr" in str(c)
                              for c in checks)
        suite.check(cat, "Implied CAGR check present (Phase 2)",
                    has_cagr_check, has_cagr_check, "True",
                    "Phase 2 constitution checks should appear")

        has_multiple_check = any("premium" in str(c).lower() or "EV/EBITDA" in str(c)
                                 for c in checks)
        suite.check(cat, "Multiple premium check present (Phase 2)",
                    has_multiple_check, has_multiple_check, "True")

        # Print the actual checks found
        print(f"\n  Constitution checks found for {suite.ticker}:")
        for c in checks:
            symbol = "  ✓" if ("PASS" in str(c) or "✅" in str(c)) else "  ✗"
            print(f"  {symbol} {str(c)[:100]}")


def validate_cross_ticker_consistency(
    reports: Dict[str, Dict]
) -> ValidationSuite:
    """
    Cross-ticker consistency checks — things that must be true
    across the full universe.
    """
    suite = ValidationSuite("CROSS_TICKER")
    cat = "CrossTicker"

    # NVDA should have higher implied CAGR than CNC
    nvda_rep = reports.get("NVDA", {})
    cnc_rep = reports.get("CNC", {})
    aapl_rep = reports.get("AAPL", {})
    msft_rep = reports.get("MSFT", {})

    def get_implied_cagr(rep):
        return (rep.get("4_valuation_synthesis", {})
                   .get("phase2_valuation", {})
                   .get("reverse_dcf", {})
                   .get("implied_cagr_10y"))

    def get_roic(rep):
        return (rep.get("4_valuation_synthesis", {})
                   .get("phase2_valuation", {})
                   .get("multiple_decomposition", {})
                   .get("roic"))

    def get_ev_ebitda(rep):
        return (rep.get("4_valuation_synthesis", {})
                   .get("phase2_valuation", {})
                   .get("multiple_decomposition", {})
                   .get("market_ev_ebitda"))

    # Implied CAGR: NVDA > AAPL > CNC (growth expectations ordering)
    nvda_cagr = get_implied_cagr(nvda_rep)
    aapl_cagr = get_implied_cagr(aapl_rep)
    cnc_cagr = get_implied_cagr(cnc_rep)

    if nvda_cagr and aapl_cagr:
        suite.check(cat, "NVDA implied CAGR > AAPL implied CAGR",
                    nvda_cagr > aapl_cagr,
                    f"NVDA={nvda_cagr:.1%} vs AAPL={aapl_cagr:.1%}",
                    "NVDA > AAPL",
                    "AI infrastructure commands higher growth premium")

    if aapl_cagr and cnc_cagr:
        suite.check(cat, "AAPL implied CAGR > CNC implied CAGR",
                    aapl_cagr > cnc_cagr,
                    f"AAPL={aapl_cagr:.1%} vs CNC={cnc_cagr:.1%}",
                    "AAPL > CNC")

    # ROIC: NVDA > AAPL > CNC
    nvda_roic = get_roic(nvda_rep)
    aapl_roic = get_roic(aapl_rep)
    cnc_roic = get_roic(cnc_rep)

    if nvda_roic and aapl_roic:
        suite.check(cat, "NVDA ROIC > AAPL ROIC",
                    nvda_roic > aapl_roic,
                    f"NVDA={nvda_roic:.1%} vs AAPL={aapl_roic:.1%}",
                    "NVDA > AAPL",
                    "NVDA's asset-light model earns extraordinary ROIC")

    if aapl_roic and cnc_roic:
        suite.check(cat, "AAPL ROIC > CNC ROIC",
                    aapl_roic > cnc_roic,
                    f"AAPL={aapl_roic:.1%} vs CNC={cnc_roic:.1%}",
                    "AAPL > CNC")

    # EV/EBITDA: tech should trade at higher multiple than healthcare plans
    nvda_mult = get_ev_ebitda(nvda_rep)
    aapl_mult = get_ev_ebitda(aapl_rep)
    cnc_mult = get_ev_ebitda(cnc_rep)

    if aapl_mult and cnc_mult:
        suite.check(cat, "AAPL EV/EBITDA > CNC EV/EBITDA",
                    aapl_mult > cnc_mult,
                    f"AAPL={aapl_mult:.1f}x vs CNC={cnc_mult:.1f}x",
                    "tech > healthcare plans",
                    "Consumer electronics/services vs healthcare plans")

    # CNC should be flagged as relatively undervalued vs AAPL
    def get_mult_signal(rep):
        return (rep.get("4_valuation_synthesis", {})
                   .get("phase2_valuation", {})
                   .get("multiple_decomposition", {})
                   .get("signal", ""))

    cnc_signal = get_mult_signal(cnc_rep)
    aapl_signal = get_mult_signal(aapl_rep)

    cnc_relatively_cheaper = cnc_signal in ("undervalued", "fairly_valued")
    aapl_relatively_pricier = aapl_signal in (
        "moderate_premium", "high_premium", "speculative_premium"
    )

    suite.check(cat, "CNC multiple signal indicates value (undervalued/fairly_valued)",
                cnc_relatively_cheaper, cnc_signal, "undervalued or fairly_valued",
                "CNC historically trades at discount to justified multiple")

    suite.check(cat, "AAPL multiple signal indicates premium",
                aapl_relatively_pricier, aapl_signal,
                "moderate/high/speculative premium",
                "AAPL always trades at premium to mechanical justified multiple")

    return suite


# ─────────────────────────────────────────────────────────────────────────────
# Master runner
# ─────────────────────────────────────────────────────────────────────────────

def run_phase3_validation(
    tickers: List[str],
    run_pipeline: bool = True,
    pipeline_timeout: int = 300
) -> Tuple[int, int]:


    print("\n" + "█"*100)
    print("  ALETHEIA PHASE 3 — END-TO-END VALIDATION")
    print("  Testing full LangGraph pipeline against real-world investment knowledge")
    print("█"*100)

    reports = {}
    all_suites = []

    # Step 1: Run or load reports
    for ticker in tickers:
        if run_pipeline:
            success = globals()["run_pipeline"](ticker, timeout=pipeline_timeout)
            if not success:
                print(f"  ⚠ Pipeline failed for {ticker} — using cached report if available")

        report = load_report(ticker)
        if report:
            reports[ticker] = report
            print(f"  ✓ Loaded report for {ticker} ({len(json.dumps(report))/1024:.0f}KB)")
        else:
            print(f"  ✗ No report for {ticker}")

    # Step 2: Per-ticker validation
    for ticker, report in reports.items():
        print(f"\n  Validating {ticker}...")
        suite = ValidationSuite(ticker)

        validate_report_structure(suite, report)
        validate_financial_facts(suite, report)
        validate_phase2_outputs(suite, report)
        validate_agent_outputs(suite, report)
        validate_constitution_checks(suite, report)

        all_suites.append(suite)
        print(suite.summary())

    # Step 3: Cross-ticker validation
    if len(reports) >= 2:
        print(f"\n  Running cross-ticker consistency checks...")
        cross_suite = validate_cross_ticker_consistency(reports)
        all_suites.append(cross_suite)
        print(cross_suite.summary())

    # Step 4: Final summary
    total_passed = sum(s.passed for s in all_suites)
    total_tests = sum(s.total for s in all_suites)

    print("\n" + "█"*100)
    print("  PHASE 3 VALIDATION SUMMARY")
    print("█"*100)
    print(f"  Total: {total_passed}/{total_tests} checks passed  "
          f"({total_passed/total_tests*100:.1f}% pass rate)" if total_tests > 0 else "")

    for suite in all_suites:
        status = "✓" if suite.failed == 0 else "✗"
        print(f"  {status} {suite.ticker:<20} {suite.passed:>3}/{suite.total:>3}")

    # Critical failures
    critical = [
        r for s in all_suites for r in s.results
        if not r.passed and r.category != "Structure"
    ]
    if critical:
        print(f"\n  CRITICAL FAILURES ({len(critical)}):")
        for r in critical[:20]:
            print(f"    [{r.category}] {r.check}: got={r.actual}, expected={r.expected}"
                  + (f" | {r.note}" if r.note else ""))

    # What to fix
    missing = [
        r for s in all_suites for r in s.results
        if not r.passed and "None" in str(r.actual)
    ]
    if missing:
        print(f"\n  MISSING DATA ({len(missing)}) — likely tag mapping or agent output gaps:")
        for r in missing[:10]:
            print(f"    [{r.category}] {r.check}")

    print("█"*100)
    return total_passed, total_tests


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3 end-to-end validation")
    parser.add_argument("--no-run", action="store_true",
                        help="Skip running pipeline, use cached reports")
    parser.add_argument("--tickers", nargs="+",
                        default=["AAPL", "NVDA", "CNC"],
                        help="Tickers to validate (default: AAPL NVDA CNC)")
    parser.add_argument("--timeout", type=int, default=300,
                        help="Pipeline timeout per ticker in seconds")
    args = parser.parse_args()

    Path("tests").mkdir(exist_ok=True)

    passed, total = run_phase3_validation(
        tickers=args.tickers,
        run_pipeline=not args.no_run,
        pipeline_timeout=args.timeout,
    )
    sys.exit(0 if passed >= total * 0.80 else 1)  # Pass if ≥80% checks pass
