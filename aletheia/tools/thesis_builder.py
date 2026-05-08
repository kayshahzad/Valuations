"""
aletheia/tools/thesis_builder.py

Interactive Investment Thesis Builder — Section 16.2
======================================================
Guides the analyst through the seven required thesis components,
pre-populates from report JSON, stores in DuckDB thesis_memory,
and exports a PDF brief.

Usage:
    # Create new thesis
    python3 -m aletheia.tools.thesis_builder --ticker MSFT

    # Review existing thesis
    python3 -m aletheia.tools.thesis_builder --ticker MSFT --review

    # Update thesis (creates new version)
    python3 -m aletheia.tools.thesis_builder --ticker MSFT --update

    # Export PDF without re-entering thesis
    python3 -m aletheia.tools.thesis_builder --ticker MSFT --export-pdf

    # List all active theses
    python3 -m aletheia.tools.thesis_builder --list
"""

from __future__ import annotations

import json
import math
import argparse
import sys
import textwrap
from datetime import datetime
from pathlib import Path
from typing import Optional
import duckdb

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

REPORT_DIR   = Path("valuation_data/serving/latest")
DB_PATH      = Path("valuation_data/investment.duckdb")
THESIS_DIR   = Path("valuation_data/theses")
THESIS_DIR.mkdir(parents=True, exist_ok=True)

# Terminal colors
class C:
    GOLD   = "\033[93m"
    BLUE   = "\033[94m"
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    GREY   = "\033[90m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"
    CYAN   = "\033[96m"

def _safe(v) -> Optional[float]:
    if v is None: return None
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except: return None

# ─────────────────────────────────────────────────────────────────────────────
# Database
# ─────────────────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS thesis_memory (
    id               INTEGER,
    ticker           VARCHAR,
    version          INTEGER,
    created_at       TIMESTAMP,
    updated_at       TIMESTAMP,

    -- Seven required components (analyst-written)
    one_sentence     TEXT,
    assumption_1     TEXT,
    assumption_2     TEXT,
    assumption_3     TEXT,
    confirmation_12m TEXT,
    falsification    TEXT,
    moat_powers      TEXT,
    unit_economics   TEXT,

    -- Auto-populated from report JSON
    base_iv          DOUBLE,
    base_mos         DOUBLE,
    bear_iv          DOUBLE,
    bull_iv          DOUBLE,
    conviction_score INTEGER,
    position_tier    VARCHAR,
    p1_moat          INTEGER,
    p2_health        INTEGER,
    p3_tailwind      INTEGER,
    p4_mos           INTEGER,
    p5_leadership    INTEGER,
    lifecycle_stage  VARCHAR,
    wacc             DOUBLE,
    hist_cagr        DOUBLE,
    impl_cagr        DOUBLE,
    roic             DOUBLE,
    revenue_bn       DOUBLE,
    ebitda_bn        DOUBLE,
    fcf_margin       DOUBLE,
    roic_wacc_spread DOUBLE,
    reflexivity_cap  BOOLEAN,
    market_ev_ebitda    DOUBLE,
    justified_ev_ebitda DOUBLE,

    -- Position sizing
    entry_price      DOUBLE,
    initial_size_pct DOUBLE,
    max_size_pct     DOUBLE,

    -- Status
    status           VARCHAR DEFAULT 'active',
    exit_price       DOUBLE,
    exit_date        TIMESTAMP,
    exit_reason      TEXT,

    PRIMARY KEY (ticker, version)
)
"""

class ThesisDB:
    def __init__(self):
        self.conn = duckdb.connect(str(DB_PATH))
        self.conn.execute(SCHEMA)

    def next_id(self) -> int:
        r = self.conn.execute("SELECT COALESCE(MAX(id), 0) + 1 FROM thesis_memory").fetchone()
        return r[0]

    def next_version(self, ticker: str) -> int:
        r = self.conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM thesis_memory WHERE ticker = ?",
            [ticker.upper()]
        ).fetchone()
        return r[0]

    def save(self, thesis: dict):
        cols = ", ".join(thesis.keys())
        placeholders = ", ".join(["?" for _ in thesis])
        self.conn.execute(
            f"INSERT INTO thesis_memory ({cols}) VALUES ({placeholders})",
            list(thesis.values())
        )
        self.conn.commit()

    def get_latest(self, ticker: str) -> Optional[dict]:
        r = self.conn.execute(
            "SELECT * FROM thesis_memory WHERE ticker = ? ORDER BY version DESC LIMIT 1",
            [ticker.upper()]
        ).fetchone()
        if not r:
            return None
        cols = [d[0] for d in self.conn.description]
        return dict(zip(cols, r))

    def get_all_active(self) -> list[dict]:
        rows = self.conn.execute(
            """SELECT ticker, version, one_sentence, base_iv, base_mos,
                      conviction_score, position_tier, status, updated_at
               FROM thesis_memory
               WHERE (ticker, version) IN (
                   SELECT ticker, MAX(version) FROM thesis_memory GROUP BY ticker
               )
               AND status = 'active'
               ORDER BY conviction_score DESC NULLS LAST"""
        ).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def get_history(self, ticker: str) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM thesis_memory WHERE ticker = ? ORDER BY version",
            [ticker.upper()]
        ).fetchall()
        cols = [d[0] for d in self.conn.description]
        return [dict(zip(cols, r)) for r in rows]

    def close(self):
        self.conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# Report data extractor
# ─────────────────────────────────────────────────────────────────────────────

class ReportSnapshot:
    """Extracts all pre-populatable fields from a pipeline report JSON."""

    def __init__(self, ticker: str):
        self.ticker = ticker.upper()
        path = REPORT_DIR / f"{self.ticker}_report.json"
        if not path.exists():
            raise FileNotFoundError(f"No report for {ticker}. Run pipeline first.")
        report = json.loads(path.read_text())

        p2   = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {}) or {}
        ft   = report.get("2_financial_translation", {}) or {}
        cf   = ft.get("clean_financials", {}) or {}
        rat  = ft.get("ratios", {}) or {}
        it   = report.get("4_valuation_synthesis", {}).get("investment_thesis", {}) or {}
        ps   = it.get("pillar_scores", {}) or {}
        md   = p2.get("multiple_decomposition", {}) or {}
        dcf3 = p2.get("three_scenario_dcf", {}) or {}
        rdcf = p2.get("reverse_dcf", {}) or {}
        er   = report.get("1_economic_reality", {}) or {}

        self.base_iv  = _safe(dcf3.get("base", {}).get("intrinsic_per_share"))
        self.base_mos = _safe(dcf3.get("base", {}).get("margin_of_safety"))
        self.bear_iv  = _safe(dcf3.get("bear", {}).get("intrinsic_per_share"))
        self.bull_iv  = _safe(dcf3.get("bull", {}).get("intrinsic_per_share"))

        if self.base_iv and self.base_mos is not None and (1 + self.base_mos) != 0:
            self.market_price = self.base_iv / (1 + self.base_mos)
        else:
            self.market_price = None

        self.conviction_score = it.get("conviction_score")
        self.position_tier    = ps.get("position_tier", "")
        self.p1 = ps.get("p1_moat")
        self.p2 = ps.get("p2_health")
        self.p3 = ps.get("p3_tailwind")
        self.p4 = ps.get("p4_mos")
        self.p5 = ps.get("p5_leadership")
        self.total            = ps.get("capped_total")
        self.lifecycle_stage  = ps.get("lifecycle_stage", "")
        self.reflexivity_cap  = bool(ps.get("reflexivity_cap"))
        self.reflexivity_flags= ps.get("reflexivity_flags", [])

        self.wacc      = _safe(p2.get("wacc"))
        self.hist_cagr = _safe(rdcf.get("historical_cagr"))
        self.impl_cagr = _safe(rdcf.get("implied_cagr_10y"))
        self.roic      = _safe(rat.get("roic") or md.get("roic"))
        self.roic_wacc_spread = _safe(md.get("roic_wacc_spread"))
        self.value_creation   = md.get("value_creation", "")
        self.market_ev_ebitda = _safe(md.get("market_ev_ebitda"))
        self.justified_ev_ebitda = _safe(md.get("justified_ev_ebitda"))

        self.revenue_bn = _safe(cf.get("revenue_bn"))
        self.ebitda_bn  = _safe(cf.get("ebitda_bn"))
        self.fcf_margin = _safe(rat.get("fcf_margin_pct"))

        self.narrative  = it.get("narrative", "")
        self.constitution = it.get("constitution_checks", [])
        self.p2_reasons = ps.get("p2_reasons", [])
        self.p3_reasons = ps.get("p3_reasons", [])
        self.moat_score = er.get("moat", {}).get("score") if er.get("moat") else None

        # Position sizing (from framework Section 16.3)
        tier = self.position_tier
        if tier == "high_conviction":
            self.initial_size = 0.05   # 5% initial
            self.max_size     = 0.15   # 15% max
        elif tier in ("conviction", "starter"):
            self.initial_size = 0.03
            self.max_size     = 0.08
        elif tier == "monitor":
            self.initial_size = 0.01
            self.max_size     = 0.04
        else:
            self.initial_size = 0.005
            self.max_size     = 0.02

        if self.reflexivity_cap:
            self.max_size *= 0.75   # reflexivity cap


# ─────────────────────────────────────────────────────────────────────────────
# Interactive terminal UI
# ─────────────────────────────────────────────────────────────────────────────

def _print_banner():
    print(f"\n{C.BOLD}{C.GOLD}{'='*64}{C.RESET}")
    print(f"{C.BOLD}{C.GOLD}  ALETHEIA — INVESTMENT THESIS BUILDER  •  Section 16.2{C.RESET}")
    print(f"{C.BOLD}{C.GOLD}{'='*64}{C.RESET}\n")

def _divider(title=""):
    if title:
        print(f"\n{C.BLUE}{C.BOLD}── {title} {'─'*(56-len(title))}{C.RESET}")
    else:
        print(f"{C.GREY}{'─'*64}{C.RESET}")

def _print_snapshot(snap: ReportSnapshot):
    """Print the pre-populated data from the pipeline report."""
    _divider(f"PIPELINE DATA  —  {snap.ticker}")

    tier_color = C.GREEN if snap.position_tier in ("conviction","high_conviction") else \
                 C.GOLD  if snap.position_tier == "monitor" else C.GREY

    print(f"  Lifecycle:    {C.CYAN}{snap.lifecycle_stage.replace('_',' ').title()}{C.RESET}")
    print(f"  Score:        {C.BOLD}{snap.total}/25{C.RESET}  "
          f"({tier_color}{snap.position_tier.upper()}{C.RESET})")
    print(f"  Conv score:   {snap.conviction_score:+d} / 10")

    # Pillar bar
    def pbar(score):
        if score is None: return "?"
        filled = "●" * score + "○" * (5 - score)
        col = C.GREEN if score >= 4 else C.GOLD if score >= 3 else C.RED
        return f"{col}{filled}{C.RESET}"

    print(f"  Pillars:      P1={pbar(snap.p1)} P2={pbar(snap.p2)} "
          f"P3={pbar(snap.p3)} P4={pbar(snap.p4)} P5={pbar(snap.p5)}")
    print()

    # Valuation
    mos_col = C.GREEN if (snap.base_mos or 0) > 0 else C.RED
    print(f"  Base IV:      {C.BOLD}${snap.base_iv:.2f}{C.RESET}  "
          f"({mos_col}MoS {snap.base_mos:+.1%}{C.RESET})")
    if snap.bear_iv:
        print(f"  Range:        Bear ${snap.bear_iv:.2f}  —  Bull ${snap.bull_iv:.2f}")
    if snap.market_price:
        print(f"  Market price: ${snap.market_price:.2f}")
    print()

    # Key metrics
    print(f"  ROIC:         {(snap.roic or 0)*100:.1f}%  "
          f"WACC: {(snap.wacc or 0)*100:.1f}%  "
          f"Spread: {(snap.roic_wacc_spread or 0)*100:+.1f}pp")
    print(f"  Revenue:      ${snap.revenue_bn:.1f}B  "
          f"EBITDA: ${snap.ebitda_bn:.1f}B  "
          f"FCF margin: {snap.fcf_margin:.1f}%")
    print(f"  Hist CAGR:    {(snap.hist_cagr or 0):.1%}  "
          f"Implied CAGR: {(snap.impl_cagr or 0):.1%}  "
          f"Ratio: {(snap.impl_cagr or 0)/(snap.hist_cagr or 1):.1f}x")
    print()

    # Constitution
    print("  Constitution:")
    for check in snap.constitution:
        icon = "  ✅" if "PASS" in check else "  ❌" if "FAIL" in check else "  ⚠️"
        col  = C.GREEN if "PASS" in check else C.RED if "FAIL" in check else C.GOLD
        print(f"  {col}{check[:80]}{C.RESET}")
    print()

    # Reflexivity
    if snap.reflexivity_cap:
        print(f"  {C.GOLD}★ Reflexivity flag active — max position capped at "
              f"{snap.max_size:.0%}{C.RESET}")
        for flag in snap.reflexivity_flags:
            print(f"    {C.DIM}{flag[:72]}{C.RESET}")
        print()

    # Position sizing
    print(f"  Suggested entry:  {snap.initial_size:.0%} of portfolio initially")
    print(f"  Maximum position: {snap.max_size:.0%} of portfolio at full conviction")


def _prompt(question: str, hint: str = "", required: bool = True,
            default: str = "") -> str:
    """Interactive prompt with formatting."""
    print(f"\n{C.BOLD}{question}{C.RESET}")
    if hint:
        for line in textwrap.wrap(hint, 70):
            print(f"  {C.DIM}{line}{C.RESET}")
    if default:
        print(f"  {C.GREY}[Enter to keep: {default[:60]}]{C.RESET}")

    while True:
        try:
            answer = input(f"\n  {C.CYAN}>{C.RESET} ").strip()
        except (EOFError, KeyboardInterrupt):
            print(f"\n{C.GREY}Aborted.{C.RESET}")
            sys.exit(0)

        if not answer and default:
            return default
        if not answer and required:
            print(f"  {C.RED}Required — please enter a response.{C.RESET}")
            continue
        if not answer and not required:
            return ""
        return answer


def _prompt_float(question: str, hint: str = "", default: float = None) -> float:
    """Prompt for a float value."""
    default_str = str(default) if default is not None else ""
    while True:
        raw = _prompt(question, hint, required=(default is None),
                      default=default_str)
        try:
            return float(raw.replace("%", "").replace("$", "").strip())
        except ValueError:
            print(f"  {C.RED}Please enter a number.{C.RESET}")


def _confirm(question: str) -> bool:
    try:
        ans = input(f"\n  {C.BOLD}{question}{C.RESET} {C.GREY}[y/N]{C.RESET} ").strip().lower()
        return ans in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Thesis creation flow
# ─────────────────────────────────────────────────────────────────────────────

def build_thesis(ticker: str, existing: dict = None) -> dict:
    """
    Interactive seven-component thesis builder.
    If existing is provided, use it as defaults for an update.
    """
    snap = ReportSnapshot(ticker)

    _print_banner()
    print(f"  Building investment thesis for {C.BOLD}{C.GOLD}{ticker.upper()}{C.RESET}")
    print(f"  {C.DIM}Framework Section 16.2 — Seven required components{C.RESET}")
    print(f"\n  {C.DIM}All seven components are required before a position can be sized.{C.RESET}")
    print(f"  {C.DIM}Type your answer and press Enter. Press Ctrl+C to abort.{C.RESET}")

    _print_snapshot(snap)

    ex = existing or {}

    # ── Component 1 — One-sentence thesis ────────────────────────────────────
    _divider("COMPONENT 1 — THE ONE-SENTENCE THESIS")
    print(f"\n  {C.DIM}Must answer three questions simultaneously:{C.RESET}")
    print(f"  {C.DIM}  WHY THIS COMPANY — what structural advantage creates durable returns?{C.RESET}")
    print(f"  {C.DIM}  WHY NOW          — what has changed or been mispriced recently?{C.RESET}")
    print(f"  {C.DIM}  WHY AT THIS PRICE — what is the specific valuation argument?{C.RESET}")
    print(f"\n  {C.RED}❌ Bad:{C.RESET} \"{ticker} is a great company with strong fundamentals.\"")
    print(f"  {C.RED}   (No price anchor, no timing, not testable){C.RESET}")
    if snap.base_mos and snap.hist_cagr and snap.impl_cagr:
        ratio = snap.impl_cagr / snap.hist_cagr
        print(f"\n  {C.GREY}Context: {ticker} offers {snap.base_mos:+.1%} MoS. Market prices "
              f"{snap.impl_cagr:.1%} growth vs {snap.hist_cagr:.1%} historical ({ratio:.1f}x).{C.RESET}")

    one_sentence = _prompt(
        "Write the one-sentence thesis:",
        "Example: '[Company] offers a [X]% discount to intrinsic value in a market pricing "
        "in [Y]% growth against [Z]% historical, driven by [specific mispricing].'",
        default=ex.get("one_sentence", "")
    )

    # ── Component 2 — Three key assumptions ──────────────────────────────────
    _divider("COMPONENT 2 — THREE KEY ASSUMPTIONS")
    print(f"\n  {C.DIM}Each assumption must be:{C.RESET}")
    print(f"  {C.DIM}  • Observable in quarterly data{C.RESET}")
    print(f"  {C.DIM}  • Specific enough to be refuted (not just \"demand remains strong\"){C.RESET}")
    print(f"  {C.DIM}  • Binary — if any ONE is refuted, the thesis is broken{C.RESET}")

    # Show P2/P3 reasons as context
    if snap.p2_reasons:
        print(f"\n  {C.GREY}P2 Health signals: {snap.p2_reasons[0]}{C.RESET}")
    if snap.p3_reasons:
        print(f"  {C.GREY}P3 Tailwind signals: {snap.p3_reasons[0]}{C.RESET}")

    assumption_1 = _prompt(
        "Assumption 1 (financial/operational metric):",
        "Example: 'ROIC stays above 25% as Xilinx intangibles amortize through FY2026.'",
        default=ex.get("assumption_1", "")
    )
    assumption_2 = _prompt(
        "Assumption 2 (competitive/moat metric):",
        "Example: 'Azure revenue growth stays above 25% YoY for the next 4 quarters.'",
        default=ex.get("assumption_2", "")
    )
    assumption_3 = _prompt(
        "Assumption 3 (market/macro metric):",
        "Example: 'GLP-1 net pricing stays above $800/month through 2026 despite CMS pressure.'",
        default=ex.get("assumption_3", "")
    )

    # ── Component 3 — 12-month confirmation signal ────────────────────────────
    _divider("COMPONENT 3 — 12-MONTH CONFIRMATION SIGNAL")
    print(f"\n  {C.DIM}One specific observable event that confirms the thesis is working.{C.RESET}")
    print(f"  {C.DIM}Sets the clock — if this does not happen, re-evaluate even if nothing{C.RESET}")
    print(f"  {C.DIM}bad has happened yet.{C.RESET}")

    confirmation_12m = _prompt(
        "What must happen within 12 months to confirm the thesis?",
        "Example: 'Q3 earnings show [metric] above [threshold], confirming [assumption].'",
        default=ex.get("confirmation_12m", "")
    )

    # ── Component 4 — Falsification trigger ──────────────────────────────────
    _divider("COMPONENT 4 — PRE-COMMITTED FALSIFICATION TRIGGER")
    print(f"\n  {C.BOLD}{C.RED}This is the most important component.{C.RESET}")
    print(f"\n  {C.DIM}The specific event that causes an IMMEDIATE EXIT with no deliberation.{C.RESET}")
    print(f"  {C.DIM}Must be written NOW — before you own the position — when you are not{C.RESET}")
    print(f"  {C.DIM}emotionally invested. Two constitution FAILs automatically trigger review.{C.RESET}")
    print(f"\n  {C.RED}❌ Bad:{C.RESET} \"Exit if the thesis no longer holds.\"")
    print(f"  {C.RED}   (Circular — not actionable under emotional pressure){C.RESET}")
    print(f"\n  {C.GREY}Constitution checks currently: {C.RESET}")
    for check in snap.constitution:
        status = "✅" if "PASS" in check else "❌" if "FAIL" in check else "⚠️"
        print(f"  {C.GREY}  {check[:75]}{C.RESET}")

    falsification = _prompt(
        "Write the pre-committed exit trigger:",
        "Format: 'Exit immediately if: (a) [specific metric] drops below [threshold], "
        "OR (b) [event], OR (c) [event].'",
        default=ex.get("falsification", "")
    )

    # ── Component 5 — Scenario spread (auto-populated) ───────────────────────
    _divider("COMPONENT 5 — INTRINSIC VALUE SCENARIO SPREAD")
    print(f"\n  {C.DIM}Auto-populated from pipeline DCF engine:{C.RESET}")
    if snap.bear_iv and snap.base_iv and snap.bull_iv:
        bear_delta = (snap.bear_iv / snap.market_price - 1) if snap.market_price else 0
        bull_delta = (snap.bull_iv / snap.market_price - 1) if snap.market_price else 0
        print(f"\n  Bear  ${snap.bear_iv:>8.2f}  ({bear_delta:+.1%} from market)")
        print(f"  Base  ${snap.base_iv:>8.2f}  ({snap.base_mos:+.1%} from market)  ← PRIMARY")
        print(f"  Bull  ${snap.bull_iv:>8.2f}  ({bull_delta:+.1%} from market)")
        spread = snap.bull_iv - snap.bear_iv
        spread_pct = spread / snap.base_iv if snap.base_iv else 0
        print(f"\n  Scenario spread: ${spread:.2f} ({spread_pct:.0%} of base IV)")
        print(f"  {C.DIM}Wide spread = high uncertainty. Narrow spread = predictable earnings.{C.RESET}")
    else:
        print(f"  {C.GREY}DCF scenario data not available — run pipeline first.{C.RESET}")

    # ── Component 6 — Moat assessment ────────────────────────────────────────
    _divider("COMPONENT 6 — MOAT ASSESSMENT (7 Powers)")
    print(f"\n  {C.DIM}Which Hamilton Helmer powers are CONFIRMED (benefit + barrier both present)?{C.RESET}")
    print(f"  {C.DIM}Confirmed powers justify premium multiples. Aspirational do not.{C.RESET}")
    if snap.moat_score:
        print(f"\n  Pipeline moat score: {snap.moat_score}/10")

    moat_powers = _prompt(
        "Document confirmed vs aspirational moat powers:",
        "Format: 'Confirmed: [power, reason]. Aspirational: [power, reason].'",
        default=ex.get("moat_powers", "")
    )

    # ── Component 7 — Lifecycle + unit economics ──────────────────────────────
    _divider("COMPONENT 7 — LIFECYCLE STAGE & UNIT ECONOMICS")
    print(f"\n  {C.DIM}Lifecycle classification: {C.BOLD}{snap.lifecycle_stage.replace('_',' ').title()}{C.RESET}")
    print(f"  {C.DIM}For pharma/SaaS/platform names: document TAM and key unit economics.{C.RESET}")
    print(f"  {C.DIM}For mature names: document capital return quality and dividend safety.{C.RESET}")
    print(f"  {C.DIM}TAM justification is REQUIRED if implied CAGR > 1.3x historical.{C.RESET}")
    if snap.hist_cagr and snap.impl_cagr:
        ratio = snap.impl_cagr / snap.hist_cagr
        if ratio > 1.3:
            print(f"\n  {C.RED}⚠ TAM justification required: implied CAGR is {ratio:.1f}x historical.{C.RESET}")

    unit_economics = _prompt(
        "Document TAM sizing and lifecycle economics:",
        "Example: 'Obesity TAM $100B by 2030, current penetration <8%, "
        "patent protection through 2036. Revenue CAGR of 17% implies 15% market share by 2034.'",
        default=ex.get("unit_economics", "")
    )

    # ── Entry price ───────────────────────────────────────────────────────────
    _divider("POSITION SIZING")
    print(f"\n  {C.DIM}Framework Section 16.3 — Position Entry Protocol{C.RESET}")
    print(f"  Suggested initial size: {snap.initial_size:.0%} of portfolio")
    print(f"  Maximum position:       {snap.max_size:.0%} of portfolio")
    if snap.reflexivity_cap:
        print(f"  {C.GOLD}★ Reflexivity cap applied — maximum reduced to 75% of base size{C.RESET}")

    entry_price = _prompt_float(
        "Current entry price (market price):",
        f"Pipeline derived: ${snap.market_price:.2f}" if snap.market_price else "",
        default=snap.market_price
    )

    # ── Review ────────────────────────────────────────────────────────────────
    _divider("REVIEW YOUR THESIS")
    print(f"\n  {C.BOLD}One-sentence:{C.RESET}")
    for line in textwrap.wrap(one_sentence, 66):
        print(f"    {line}")
    print(f"\n  {C.BOLD}Assumptions:{C.RESET}")
    print(f"    A1: {assumption_1[:70]}")
    print(f"    A2: {assumption_2[:70]}")
    print(f"    A3: {assumption_3[:70]}")
    print(f"\n  {C.BOLD}Confirmation (12m):{C.RESET}")
    for line in textwrap.wrap(confirmation_12m, 66):
        print(f"    {line}")
    print(f"\n  {C.BOLD}Falsification trigger:{C.RESET}")
    for line in textwrap.wrap(falsification, 66):
        print(f"    {line}")
    print(f"\n  {C.BOLD}Moat powers:{C.RESET}")
    for line in textwrap.wrap(moat_powers, 66):
        print(f"    {line}")
    print(f"\n  {C.BOLD}Unit economics:{C.RESET}")
    for line in textwrap.wrap(unit_economics, 66):
        print(f"    {line}")
    print(f"\n  {C.BOLD}Entry: ${entry_price:.2f}  "
          f"Initial: {snap.initial_size:.0%}  Max: {snap.max_size:.0%}{C.RESET}")

    if not _confirm("Save this thesis?"):
        print(f"\n{C.GREY}Thesis discarded.{C.RESET}")
        sys.exit(0)

    now = datetime.utcnow()
    db  = ThesisDB()
    ver = db.next_version(ticker)
    idd = db.next_id()

    thesis = {
        "id":               idd,
        "ticker":           ticker.upper(),
        "version":          ver,
        "created_at":       now,
        "updated_at":       now,
        "one_sentence":     one_sentence,
        "assumption_1":     assumption_1,
        "assumption_2":     assumption_2,
        "assumption_3":     assumption_3,
        "confirmation_12m": confirmation_12m,
        "falsification":    falsification,
        "moat_powers":      moat_powers,
        "unit_economics":   unit_economics,
        "base_iv":          snap.base_iv,
        "base_mos":         snap.base_mos,
        "bear_iv":          snap.bear_iv,
        "bull_iv":          snap.bull_iv,
        "conviction_score": snap.conviction_score,
        "position_tier":    snap.position_tier,
        "p1_moat":          snap.p1,
        "p2_health":        snap.p2,
        "p3_tailwind":      snap.p3,
        "p4_mos":           snap.p4,
        "p5_leadership":    snap.p5,
        "lifecycle_stage":  snap.lifecycle_stage,
        "wacc":             snap.wacc,
        "hist_cagr":        snap.hist_cagr,
        "impl_cagr":        snap.impl_cagr,
        "roic":             snap.roic,
        "revenue_bn":       snap.revenue_bn,
        "ebitda_bn":        snap.ebitda_bn,
        "fcf_margin":       snap.fcf_margin,
        "roic_wacc_spread":     snap.roic_wacc_spread,
        "reflexivity_cap":      snap.reflexivity_cap,
        "market_ev_ebitda":     snap.market_ev_ebitda,
        "justified_ev_ebitda":  snap.justified_ev_ebitda,
        "entry_price":      entry_price,
        "initial_size_pct": snap.initial_size,
        "max_size_pct":     snap.max_size,
        "status":           "active",
        "exit_price":       None,
        "exit_date":        None,
        "exit_reason":      None,
    }

    db.save(thesis)
    db.close()

    print(f"\n  {C.GREEN}✓ Thesis saved — {ticker.upper()} v{ver}{C.RESET}")
    return thesis


def save_thesis_from_api(ticker: str, form_data: dict) -> dict:
    """API-friendly thesis builder that skips terminal prompts."""
    REQUIRED = [
        "one_sentence", "assumption_1", "assumption_2", "assumption_3",
        "confirmation_12m", "falsification", "moat_powers", "unit_economics"
    ]
    missing = [k for k in REQUIRED if not (form_data.get(k) or "").strip()]
    if missing:
        raise ValueError(f"All 8 components required. Missing: {', '.join(missing)}")
    
    snap = ReportSnapshot(ticker)
    
    now = datetime.utcnow().isoformat() + "Z"
    db  = ThesisDB()
    ver = db.next_version(ticker)
    idd = db.next_id()
    
    thesis = {
        "id":               idd,
        "ticker":           ticker.upper(),
        "version":          ver,
        "created_at":       now,
        "updated_at":       now,
        "one_sentence":     form_data.get("one_sentence", "").strip(),
        "assumption_1":     form_data.get("assumption_1", "").strip(),
        "assumption_2":     form_data.get("assumption_2", "").strip(),
        "assumption_3":     form_data.get("assumption_3", "").strip(),
        "confirmation_12m": form_data.get("confirmation_12m", "").strip(),
        "falsification":    form_data.get("falsification", "").strip(),
        "moat_powers":      form_data.get("moat_powers", "").strip(),
        "unit_economics":   form_data.get("unit_economics", "").strip(),
        "base_iv":          snap.base_iv,
        "base_mos":         snap.base_mos,
        "bear_iv":          snap.bear_iv,
        "bull_iv":          snap.bull_iv,
        "conviction_score": snap.conviction_score,
        "position_tier":    snap.position_tier,
        "p1_moat":          snap.p1,
        "p2_health":        snap.p2,
        "p3_tailwind":      snap.p3,
        "p4_mos":           snap.p4,
        "p5_leadership":    snap.p5,
        "lifecycle_stage":  snap.lifecycle_stage,
        "wacc":             snap.wacc,
        "hist_cagr":        snap.hist_cagr,
        "impl_cagr":        snap.impl_cagr,
        "roic":             snap.roic,
        "revenue_bn":       snap.revenue_bn,
        "ebitda_bn":        snap.ebitda_bn,
        "fcf_margin":       snap.fcf_margin,
        "roic_wacc_spread": snap.roic_wacc_spread,
        "reflexivity_cap":  snap.reflexivity_cap,
        "market_ev_ebitda": snap.market_ev_ebitda,
        "justified_ev_ebitda": snap.justified_ev_ebitda,
        "entry_price":      snap.market_price,
        "initial_size_pct": snap.initial_size,
        "max_size_pct":     snap.max_size,
        "status":           "active",
        "exit_price":       None,
        "exit_date":        None,
        "exit_reason":      None,
    }

    db.save(thesis)
    db.close()
    return thesis


# ─────────────────────────────────────────────────────────────────────────────
# PDF Export
# ─────────────────────────────────────────────────────────────────────────────


def _val_color(val, ST):
    """Return style based on value sign/content."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib import colors
    s = str(val)
    if s.startswith("+") or (s.endswith("%") and not s.startswith("-") and not s.startswith("0")):
        return ParagraphStyle("vg", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#1a6b3c"), leading=11)
    if s.startswith("-") and not s.startswith("-0"):
        return ParagraphStyle("vr", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#8b1c1c"), leading=11)
    if "⚠" in s or "FAIL" in s:
        return ParagraphStyle("va", fontName="Helvetica-Bold", fontSize=8, textColor=colors.HexColor("#92650a"), leading=11)
    return ParagraphStyle("vn", fontName="Helvetica", fontSize=8, textColor=colors.HexColor("#1a1a2e"), leading=11)

def export_pdf(thesis: dict, report: dict = None) -> Path:
    """Export thesis as a comprehensive PDF brief."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib import colors
    from reportlab.lib.units import mm
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.lib.enums import TA_CENTER
    from reportlab.platypus import (SimpleDocTemplate, Paragraph, Spacer,
                                     Table, TableStyle, HRFlowable)

    ticker   = thesis["ticker"]
    out_path = THESIS_DIR / f"{ticker}_Thesis_v{thesis['version']}.pdf"

    if report is None:
        rp = REPORT_DIR / f"{ticker}_report.json"
        report = json.loads(rp.read_text()) if rp.exists() else {}

    # Extract all sections
    p2   = report.get("4_valuation_synthesis",{}).get("phase2_valuation",{}) or {}
    ft   = report.get("2_financial_translation",{}) or {}
    cf   = ft.get("clean_financials",{}) or {}
    rat  = ft.get("ratios",{}) or {}
    qs   = ft.get("quality_screens",{}) or {}
    cfl  = ft.get("cleaning_flags",{}) or {}
    ds   = qs.get("domain_scores",{}) or {}
    cap  = report.get("3_capital_structure_risk",{}) or {}
    cs   = cap.get("capital_stack",{}) or {}
    rf   = cap.get("risk_factors",{}) or {}
    liq  = rf.get("liquidity",{}) or {}
    lev  = rf.get("leverage",{}) or {}
    dn   = rf.get("downside",{}) or {}
    er   = report.get("1_economic_reality",{}) or {}
    moat = er.get("moat",{}) or {}
    vc   = er.get("value_chain",{}) or {}
    ind  = er.get("industry_structure",{}) or {}
    it   = report.get("4_valuation_synthesis",{}).get("investment_thesis",{}) or {}
    ps   = it.get("pillar_scores",{}) or {}
    dcf3 = p2.get("three_scenario_dcf",{}) or {}
    rdcf = p2.get("reverse_dcf",{}) or {}
    md   = p2.get("multiple_decomposition",{}) or {}

    INK   = colors.HexColor("#1a1a2e")
    GOLD  = colors.HexColor("#c9a84c")
    BLUE  = colors.HexColor("#2E5FA3")
    LBLUE = colors.HexColor("#D9E2F3")
    VLIGHT= colors.HexColor("#EEF3FB")
    GREEN = colors.HexColor("#1a6b3c")
    LGREEN= colors.HexColor("#d4edda")
    RED   = colors.HexColor("#8b1c1c")
    LRED  = colors.HexColor("#f8d7da")
    AMBER = colors.HexColor("#92650a")
    LAMBER= colors.HexColor("#fff3cd")
    GREY  = colors.HexColor("#6c757d")
    WHITE = colors.white
    DARK  = colors.HexColor("#1F3864")

    # Fixed widths in points — slightly narrower than page to account for cell padding
    PW = 468.0   # safe usable width (A4=595, margins=51*2=102, padding buffer=25)
    C1 = PW*0.27; C2 = PW*0.23; GAP = PW*0.02; C3 = PW*0.27; C4 = PW*0.21

    doc = SimpleDocTemplate(str(out_path), pagesize=A4,
                            leftMargin=51, rightMargin=51,
                            topMargin=51, bottomMargin=68)

    def sty(nm, **kw): return ParagraphStyle(nm, **kw)
    BOLD9  = sty("b9",  fontName="Helvetica-Bold", fontSize=9,  textColor=INK, leading=12)
    NORM8  = sty("n8",  fontName="Helvetica",      fontSize=8,  textColor=INK, leading=11)
    NORM7  = sty("n7",  fontName="Helvetica",      fontSize=7.5,textColor=GREY,leading=10)
    GOLDB  = sty("gb",  fontName="Helvetica-Bold", fontSize=28, textColor=GOLD,leading=34)
    H1     = sty("h1",  fontName="Helvetica-Bold", fontSize=11, textColor=GOLD,leading=14,spaceBefore=10,spaceAfter=2)
    H2     = sty("h2",  fontName="Helvetica-Bold", fontSize=9,  textColor=BLUE,leading=12,spaceBefore=5, spaceAfter=2)
    THESIS_S=sty("th",  fontName="Helvetica-BoldOblique",fontSize=9.5,textColor=INK,leading=14,spaceAfter=4)
    BODY   = sty("bd",  fontName="Helvetica",      fontSize=9,  textColor=INK, leading=13,spaceAfter=3)
    FOOTER = sty("ft",  fontName="Helvetica",      fontSize=7,  textColor=GREY,alignment=TA_CENTER)
    WHD    = sty("wh",  fontName="Helvetica-Bold", fontSize=7.5,textColor=WHITE,alignment=TA_CENTER)

    def P(t, st=None): return Paragraph(str(t), st or BODY)
    def SP(n=5): return Spacer(1,n)
    def HR(): return HRFlowable(width="100%",thickness=0.4,color=colors.HexColor("#dee2e6"),spaceAfter=3)
    def GR(): return HRFlowable(width="100%",thickness=1.5,color=GOLD,spaceAfter=5)

    def val_sty(v):
        s = str(v)
        if s.startswith("+") or ("%" in s and not s.startswith("-") and not s.startswith("0.")):
            return sty("vg", fontName="Helvetica-Bold", fontSize=8, textColor=GREEN, leading=11)
        if s.startswith("-") and not s.startswith("-0"):
            return sty("vr", fontName="Helvetica-Bold", fontSize=8, textColor=RED, leading=11)
        if any(x in s for x in ["⚠","FAIL","YES — ★"]):
            return sty("va", fontName="Helvetica-Bold", fontSize=8, textColor=AMBER, leading=11)
        return NORM8

    def kv_table(left_pairs, right_pairs):
        """Four-column key-value table: label | value | label | value."""
        LK = PW*0.27; LV = PW*0.23; RK = PW*0.27; RV = PW*0.23
        n = max(len(left_pairs), len(right_pairs))
        data = []
        for i in range(n):
            lk = str(left_pairs[i][0])  if i < len(left_pairs)  else ""
            lv = str(left_pairs[i][1])  if i < len(left_pairs)  else ""
            rk = str(right_pairs[i][0]) if i < len(right_pairs) else ""
            rv = str(right_pairs[i][1]) if i < len(right_pairs) else ""
            data.append([P(lk,NORM7), P(lv,val_sty(lv)), P(rk,NORM7), P(rv,val_sty(rv))])
        t = Table(data, colWidths=[LK, LV, RK, RV])
        t.setStyle(TableStyle([
            ("ROWBACKGROUNDS",(0,0),(-1,-1),[WHITE,VLIGHT]),
            ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#dee2e6")),
            ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
            ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
            ("LINEAFTER",(1,0),(1,-1),0.5,colors.HexColor("#aaaaaa")),
        ]))
        return t

    def full_table(headers, rows, cws=None):
        if cws is None: cws = [PW/len(headers)]*len(headers)
        data = [[P(h,WHD) for h in headers]]
        for row in rows:
            data.append([P(str(c),NORM8) for c in row])
        t = Table(data, colWidths=cws)
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),DARK),
            ("ROWBACKGROUNDS",(0,1),(-1,-1),[WHITE,VLIGHT]),
            ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#dee2e6")),
            ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
            ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
            ("VALIGN",(0,0),(-1,-1),"MIDDLE"),
        ]))
        return t

    def colored_box(text, bg=LBLUE, bar_color=BLUE):
        t = Table([[P(str(text),BODY)]], colWidths=[PW])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),bg),
            ("LEFTPADDING",(0,0),(-1,-1),10),("RIGHTPADDING",(0,0),(-1,-1),10),
            ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
            ("LINEBEFORE",(0,0),(0,-1),3,bar_color),
        ]))
        return t

    def assumption_box(num, text):
        num_sty = sty(f"an{num}", fontName="Helvetica-Bold", fontSize=9, textColor=BLUE)
        t = Table([[P(f"A{num}",num_sty), P(str(text),BODY)]], colWidths=[22,PW-22])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),LBLUE),
            ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
            ("TOPPADDING",(0,0),(-1,-1),5),("BOTTOMPADDING",(0,0),(-1,-1),5),
            ("VALIGN",(0,0),(-1,-1),"TOP"),("LINEAFTER",(0,0),(0,-1),2,BLUE),
        ]))
        return t

    def pillar_row(num, name, score, reasons):
        bar = "●"*(score or 0)+"○"*(5-(score or 0))
        sc  = score or 0
        pc  = GREEN if sc>=4 else (AMBER if sc>=3 else RED)
        ps_ = sty(f"pr{num}", fontName="Helvetica-Bold", fontSize=8, textColor=pc)
        rtext = "<br/>".join([f"→ {r}" for r in (reasons or [])]) or "—"
        rs_ = sty(f"rp{num}", fontName="Helvetica", fontSize=7.5, textColor=INK, leading=11)
        t = Table([[P(name,H2), P(f"P{num}  {bar}  {sc}/5",ps_), P(rtext,rs_)]],
                  colWidths=[PW*0.22, PW*0.20, PW*0.58])
        t.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,-1),VLIGHT),
            ("LEFTPADDING",(0,0),(-1,-1),5),("RIGHTPADDING",(0,0),(-1,-1),5),
            ("TOPPADDING",(0,0),(-1,-1),4),("BOTTOMPADDING",(0,0),(-1,-1),4),
            ("VALIGN",(0,0),(-1,-1),"TOP"),
            ("LINEBELOW",(0,0),(-1,-1),0.3,colors.HexColor("#dee2e6")),
        ]))
        return t

    # ── Helper aliases ────────────────────────────────────────────────────────
    def g(d, *keys, default=0):
        """Safe nested get."""
        v = d
        for k in keys:
            if not isinstance(v, dict): return default
            v = v.get(k, default)
        return v if v is not None else default

    def fbn(v): return f"${float(v):.1f}B"   if v else "—"
    def fpct(v): return f"{float(v):.1f}%"   if v else "—"
    def fpp(v):  return f"{float(v):+.1f}pp" if v else "—"
    def fx(v):   return f"{float(v):.1f}x"   if v else "—"
    def fd(v):   return f"{float(v):.2f}"     if v else "—"
    def fpct2(v): return f"{float(v)*100:.1f}%" if v else "—"

    # Values from report (prefer report, fall back to thesis)
    base_iv  = _safe(g(dcf3,"base","intrinsic_per_share")) or thesis.get("base_iv") or 0
    base_mos = _safe(g(dcf3,"base","margin_of_safety"))    or thesis.get("base_mos") or 0
    bear_iv  = _safe(g(dcf3,"bear","intrinsic_per_share")) or thesis.get("bear_iv") or 0
    bull_iv  = _safe(g(dcf3,"bull","intrinsic_per_share")) or thesis.get("bull_iv") or 0
    hist_c   = (_safe(rdcf.get("historical_cagr"))  or thesis.get("hist_cagr") or 0)*100
    impl_c   = (_safe(rdcf.get("implied_cagr_10y")) or thesis.get("impl_cagr") or 0)*100
    ratio    = impl_c/hist_c if hist_c else 0
    mev      = _safe(md.get("market_ev_ebitda"))     or thesis.get("market_ev_ebitda") or 0
    jev      = _safe(md.get("justified_ev_ebitda"))  or thesis.get("justified_ev_ebitda") or 0
    prem     = _safe(md.get("premium_pct"))          or 0
    roic_v   = (_safe(rat.get("roic"))               or thesis.get("roic") or 0)*100
    wacc_v   = (_safe(p2.get("wacc"))                or thesis.get("wacc") or 0)*100
    spread_v = (_safe(md.get("roic_wacc_spread"))    or thesis.get("roic_wacc_spread") or 0)*100
    entry    = thesis.get("entry_price") or (base_iv/(1+base_mos) if (1+base_mos)!=0 and base_iv else 0)

    tier  = (thesis.get("position_tier") or "").replace("_"," ").upper()
    stage = (thesis.get("lifecycle_stage") or "").replace("_"," ").title()
    cv    = thesis.get("conviction_score") or 0
    ver   = thesis.get("version", 1)
    ts    = str(thesis.get("updated_at",""))[:10]
    total = ps.get("capped_total") or (cv + 15)

    story = []

    # COVER
    story += [SP(20), P(f"◈  {thesis['ticker']}",GOLDB), P("Investment Thesis",GOLDB), SP(6), GR()]
    cover_sty = sty("cs", fontName="Helvetica", fontSize=9, textColor=GREY, leading=13)
    rfx_txt   = "  ★ Reflexivity Cap Active" if thesis.get("reflexivity_cap") else ""
    story.append(P(f"<b>{tier}</b>  •  Score: <b>{cv:+d}/10</b>  •  Stage: <b>{stage}</b>  •  v{ver}  •  {ts}{rfx_txt}", cover_sty))
    story += [SP(20)]

    # 1. THESIS
    story += [P("Investment Thesis",H1), GR(), P(thesis.get("one_sentence",""),THESIS_S), SP(10)]

    # 2. VALUATION
    story += [P("Valuation",H1), GR()]
    story.append(kv_table(
        [("Entry Price", f"${entry:.2f}"),
         ("Base IV", f"${base_iv:.2f}"),
         ("Margin of Safety", f"{base_mos:+.1%}"),
         ("Bear IV", f"${bear_iv:.2f}"),
         ("Bull IV", f"${bull_iv:.2f}"),
         ("Scenario Spread", f"${bull_iv-bear_iv:.0f}"),],
        [("Market EV/EBITDA", f"{mev:.1f}x"),
         ("Justified EV/EBITDA", f"{jev:.1f}x"),
         ("Premium to Justified", f"{prem:+.0%}"),
         ("Hist CAGR", f"{hist_c:.1f}%"),
         ("Implied CAGR", f"{impl_c:.1f}%"),
         ("Impl / Hist Ratio", f"{ratio:.2f}x"),]
    ))
    story += [SP(5)]

    # RDCF signal + constitution
    story.append(P("Reverse DCF & Constitution",H2))
    const_rows = [[rdcf.get("signal","").replace("_"," ").title() or "—", md.get("value_creation","").replace("_"," ").title() or "—"]]
    story.append(full_table(["RDCF Signal","Value Creation"], const_rows, cws=[PW*0.5, PW*0.5]))
    story += [SP(3)]
    for check in it.get("constitution_checks", []):
        bg = LGREEN if "PASS" in check else (LRED if "FAIL" in check else LAMBER)
        bc = GREEN  if "PASS" in check else (RED  if "FAIL" in check else AMBER)
        story.append(colored_box(check, bg=bg, bar_color=bc))
        story += [SP(2)]
    story += [SP(5)]

    # 3. FUNDAMENTAL HEALTH
    story += [P("Fundamental Health",H1), GR()]
    story.append(kv_table(
        [("Revenue", fbn(cf.get("revenue_bn") or thesis.get("revenue_bn"))),
         ("EBITDA",  fbn(cf.get("ebitda_bn")  or thesis.get("ebitda_bn"))),
         ("FCF",     fbn(cf.get("fcf_bn"))),
         ("NOPAT",   fbn(cf.get("nopat_bn"))),
         ("Invested Capital", fbn(cf.get("invested_capital_bn"))),
         ("Net Debt", fbn(cf.get("net_debt_bn"))),
         ("SBC",     fbn(cf.get("sbc_bn"))),],
        [("Gross Margin",  fpct(rat.get("gross_margin_pct"))),
         ("EBIT Margin",   fpct(rat.get("ebit_margin_pct"))),
         ("EBITDA Margin", fpct(rat.get("ebitda_margin_pct"))),
         ("FCF Margin",    fpct(rat.get("fcf_margin_pct")  or thesis.get("fcf_margin"))),
         ("ROIC",          f"{roic_v:.1f}%"),
         ("WACC",          f"{wacc_v:.1f}%"),
         ("ROIC-WACC Spread", f"{spread_v:+.1f}pp"),]
    ))
    story += [SP(4)]
    story.append(kv_table(
        [("ROE",           fpct2(rat.get("roe"))),
         ("Share Dilution",f"{rat.get('share_dilution_pct',0) or 0:.2f}%/yr"),
         ("Data Quality",  f"{cf.get('data_quality',0) or 0:.2f}"),
         ("Fiscal Year",   str(int(cf.get("fiscal_year",0) or 0))),],
        [("Beta",          f"{_safe(cs.get('beta') or p2.get('beta')) or 0:.3f}"),
         ("Risk-free Rate",f"{(_safe(p2.get('risk_free_rate')) or 0)*100:.2f}%"),
         ("Lifecycle",     stage),
         ("Cyclicality Z", f"{ind.get('cyclicality_z_score',0) or 0:.2f}"),]
    ))
    story += [SP(5)]

    # 4. CAPITAL STRUCTURE
    story += [P("Capital Structure & Risk",H1), GR()]
    story.append(kv_table(
        [("Current Debt",   fbn((cs.get("debt_current") or 0)/1e9)),
         ("LT Debt",        fbn((cs.get("debt_long") or 0)/1e9)),
         ("Market Cap",     fbn((cs.get("equity") or 0)/1e9)),
         ("Cash",           fbn((liq.get("cash") or 0)/1e9)),
         ("Maturities 2Y",  fbn((liq.get("maturities_next_2y") or 0)/1e9)),
         ("Liquidity Ratio",f"{liq.get('liquidity_ratio',0) or 0:.2f}x"),
         ("Refinancing Risk",f"{liq.get('refinancing_risk_score',0) or 0}/10"),
         ("Liquidity Alert","⚠ YES" if liq.get("liquidity_alert") else "No"),],
        [("Cost of Equity", f"{(cs.get('cost_of_equity') or 0)*100:.2f}%"),
         ("Op. Leverage",   f"{lev.get('operating_leverage_score',0) or 0:.1f}/10"),
         ("Fin. Leverage",  f"{lev.get('financial_leverage_score',0) or 0:.4f}"),
         ("Double Lev.",    "⚠ YES" if lev.get("double_leverage_flag") else "No"),
         ("Tangible Book",  fbn((dn.get("tangible_book_value") or 0)/1e9)),
         ("EPV",            fbn((dn.get("earnings_power_value") or 0)/1e9)),
         ("Crash FCF",      fbn((dn.get("crash_fcf") or 0)/1e9)),
         ("Floor $/share",  f"${dn.get('floor_price_per_share',0) or 0:.2f}"),]
    ))
    story += [SP(4)]

    # WACC Schedule
    wacc_sched = rf.get("wacc_schedule") or {}
    if wacc_sched:
        story.append(P("WACC Schedule (Years 1-5)", H2))
        ws_rows = [[f"Yr {k}", f"{v*100:.2f}%"] for k,v in sorted(wacc_sched.items())]
        # Show as horizontal table
        ws_headers = [f"Year {k}" for k in sorted(wacc_sched.keys())]
        ws_vals    = [f"{v*100:.2f}%" for k,v in sorted(wacc_sched.items())]
        wt = Table([ws_headers, ws_vals],
                   colWidths=[PW/len(ws_headers)]*len(ws_headers))
        wt.setStyle(TableStyle([
            ("BACKGROUND",(0,0),(-1,0),DARK),
            ("TEXTCOLOR",(0,0),(-1,0),WHITE),
            ("FONTNAME",(0,0),(-1,-1),"Helvetica"),
            ("FONTSIZE",(0,0),(-1,-1),8),
            ("ALIGNMENT",(0,0),(-1,-1),"CENTER"),
            ("GRID",(0,0),(-1,-1),0.3,colors.HexColor("#dee2e6")),
            ("TOPPADDING",(0,0),(-1,-1),3),("BOTTOMPADDING",(0,0),(-1,-1),3),
        ]))
        story.append(wt)
    story += [SP(5)]

    # Cleaning flags — off-balance-sheet items
    pen = cfl.get("pension_deficit_bn") or 0
    lse = cfl.get("lease_debt_bn") or 0
    jva = cfl.get("jva_income_bn") or 0
    if any([pen, lse, jva]):
        story.append(P("Off-Balance-Sheet Adjustments", H2))
        story.append(kv_table(
            [("Pension Deficit", f"${pen:.2f}B" if pen else "None"),
             ("Lease Debt",      f"${lse:.2f}B" if lse else "None"),],
            [("JVA Income Stripped", f"${jva:.2f}B" if jva else "None"),
             ("",""),]
        ))
    story += [SP(5)]

    # 5. MOAT & DOMAIN SCORES
    story += [P("Moat, Value Chain & Data Quality",H1), GR()]
    story.append(kv_table(
        [("Moat Score",       f"{moat.get('score',0) or 0:.1f}/10"),
         ("Switching Costs",  f"{moat.get('switching_costs',0) or 0:.1f}/10"),
         ("Network Effects",  f"{moat.get('network_effects',0) or 0:.1f}/10"),],
        [("Power Ratio",      f"{vc.get('power_ratio',0) or 0:.2f}"),
         ("Upstream Leak",    "⚠ YES" if vc.get("upstream_leak") else "No"),
         ("Strategic Lev.",   f"{vc.get('strategic_leverage',0) or 0:.1f}/10"),]
    ))
    story += [SP(3)]
    # Domain scores
    dlabels = [("D1_NonRecurring","D1 Non-Recurring"),("D2_JVA","D2 JVA"),
               ("D3_EBITNorm","D3 EBIT Norm"),("D4_AccountingPolicy","D4 Acctg Policy"),
               ("D5_Lease","D5 Lease"),("D6_Pension","D6 Pension"),
               ("D7_SBC","D7 SBC"),("D8_Revenue","D8 Revenue"),
               ("D9_WorkingCapital","D9 WC"),("D10_Tax","D10 Tax")]
    left_d, right_d = [], []
    for i,(key,lbl) in enumerate(dlabels):
        sc = ds.get(key)
        flag = ("✅ " if sc==1.0 else ("⚠ " if sc and sc>=0.8 else "❌ ")) + (f"{sc:.2f}" if sc is not None else "N/A")
        pair = (lbl, flag)
        (left_d if i<5 else right_d).append(pair)
    left_d.append(("Warnings", str(cfl.get("warning_count",0))))
    right_d.append(("Errors", str(cfl.get("error_count",0))))
    story.append(kv_table(left_d, right_d))
    story += [SP(8)]

    # 6. FIVE PILLARS
    story += [P("5-Pillar Conviction Scores",H1), GR()]
    tier_c  = GREEN if "CONVICTION" in tier else (AMBER if "MONITOR" in tier else GREY)
    sum_sty = sty("ss", fontName="Helvetica-Bold", fontSize=10, textColor=tier_c)
    sm_data = [[P(f"Total: {total}/25",sum_sty), P(f"Tier: {tier}",sum_sty),
                P(f"Conv: {cv:+d}/10",sum_sty),
                P("★ Reflexivity Cap" if thesis.get("reflexivity_cap") else "", NORM7)]]
    sm_t = Table(sm_data, colWidths=[PW*0.25]*4)
    sm_t.setStyle(TableStyle([("BACKGROUND",(0,0),(-1,-1),LBLUE),
                               ("LEFTPADDING",(0,0),(-1,-1),6),
                               ("TOPPADDING",(0,0),(-1,-1),6),("BOTTOMPADDING",(0,0),(-1,-1),6),
                               ("VALIGN",(0,0),(-1,-1),"MIDDLE")]))
    story.append(sm_t)
    story += [SP(4)]

    for num, name, sk, rk in [(1,"Moat Strength","p1_moat","p1_reasons"),
                               (2,"Fundamental Health","p2_health","p2_reasons"),
                               (3,"Secular Tailwind","p3_tailwind","p3_reasons"),
                               (4,"Margin of Safety","p4_mos","p4_reasons"),
                               (5,"Leadership Quality","p5_leadership","p5_reasons")]:
        story.append(pillar_row(num, name, ps.get(sk) or thesis.get(sk), ps.get(rk) or []))

    # Reflexivity flags
    for flag in (ps.get("reflexivity_flags") or []):
        story.append(colored_box(f"★ {flag}", bg=LAMBER, bar_color=AMBER))

    # Multiple-justified cap
    if ps.get("cap_applied"):
        story.append(colored_box(
            f"⚠ Multiple-Justified Cap Applied: {ps.get('cap_reason','')}",
            bg=LAMBER, bar_color=AMBER))
    story += [SP(8)]

    # RDCF reason strings
    rdcf_reasons = rdcf.get("reasons") or []
    if rdcf_reasons:
        story += [P("Reverse DCF — Analyst Reasons",H1), GR()]
        for reason in rdcf_reasons:
            story.append(colored_box(str(reason), bg=LBLUE, bar_color=BLUE))
            story += [SP(2)]
        story += [SP(6)]

    # Forensic quality screens
    beneish = qs.get("beneish_m_score")
    sloan   = qs.get("sloan_accrual")
    if beneish is not None or sloan is not None:
        story += [P("Forensic Quality Screens",H1), GR()]
        forensic_rows = []
        if beneish is not None:
            flag = "❌ MANIPULATION RISK" if beneish > -1.78 else "✅ Low risk"
            forensic_rows.append(("Beneish M-Score", f"{beneish:.3f}  {flag}"))
        if sloan is not None:
            flag = "❌ LOW QUALITY" if sloan > 0.10 else ("⚠ Elevated" if sloan > 0.05 else "✅ High quality")
            forensic_rows.append(("Sloan Accrual Ratio", f"{sloan:.3f}  {flag}"))
        story.append(kv_table(forensic_rows, []))
        story += [SP(6)]

    # Pipeline narrative
    narrative = it.get("narrative") or ""
    if narrative:
        story += [P("Pipeline Analysis Narrative",H1), GR()]
        story.append(colored_box(narrative, bg=VLIGHT, bar_color=BLUE))
        story += [SP(8)]

    # 7. THREE ASSUMPTIONS
    story += [P("Three Key Assumptions",H1), GR(),
              P("Each must be observable quarterly. If any ONE is refuted the thesis is broken.",NORM7), SP(4)]
    for i, k in enumerate(["assumption_1","assumption_2","assumption_3"],1):
        story.append(assumption_box(i, thesis.get(k,"")))
        story += [SP(3)]
    story += [SP(8)]

    # 8. CONFIRMATION SIGNAL
    story += [P("12-Month Confirmation Signal",H1), GR()]
    story.append(colored_box(thesis.get("confirmation_12m",""), bg=LGREEN, bar_color=GREEN))
    story += [SP(8)]

    # 9. FALSIFICATION TRIGGER
    story += [P("Pre-Committed Exit Trigger",H1), GR(),
              P("Written before the position. Non-negotiable when this fires.",NORM7), SP(3)]
    story.append(colored_box(thesis.get("falsification",""), bg=LRED, bar_color=RED))
    story += [SP(8)]

    # 10. MOAT + UNIT ECONOMICS
    story += [P("Moat Assessment  —  7 Powers Framework",H1), GR(),
              P(thesis.get("moat_powers",""),BODY), SP(8)]
    story += [P("Lifecycle Stage & Unit Economics / TAM",H1), GR(),
              P(f"<b>Stage:</b> {stage}  |  <b>WACC:</b> {wacc_v:.2f}%  |  <b>Beta:</b> {_safe(cs.get('beta') or p2.get('beta')) or 0:.3f}",BODY),
              SP(3), P(thesis.get("unit_economics",""),BODY), SP(8)]

    # 11. POSITION SIZING
    story += [P("Position Sizing  —  Section 16.3",H1), GR()]
    story.append(kv_table(
        [("Entry Price",   f"${thesis.get('entry_price',0) or 0:.2f}"),
         ("Initial Size",  f"{thesis.get('initial_size_pct',0) or 0:.0%} of portfolio"),
         ("Maximum Size",  f"{thesis.get('max_size_pct',0) or 0:.0%} of portfolio"),],
        [("Reflexivity Cap","★ YES — 75% of base" if thesis.get("reflexivity_cap") else "No"),
         ("Scale Trigger", "-10% to -15% if thesis intact"),
         ("Full Position", "MoS>15% + all assumptions confirmed"),]
    ))
    story += [SP(10), HR(),
              P(f"Aletheia Conviction Engine  •  {ticker} Thesis v{ver}  •  {ts}  •  CONFIDENTIAL  •  Section 16.2", FOOTER)]

    doc.build(story)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# List view
# ─────────────────────────────────────────────────────────────────────────────

def list_theses():
    db = ThesisDB()
    rows = db.get_all_active()
    db.close()

    if not rows:
        print(f"\n{C.GREY}No active theses found.{C.RESET}")
        print(f"  Run: python3 -m aletheia.tools.thesis_builder --ticker MSFT")
        return

    _print_banner()
    print(f"  {C.BOLD}ACTIVE INVESTMENT THESES{C.RESET}\n")
    print(f"  {'Ticker':>6}  {'Score':>5}  {'Tier':>12}  {'MoS':>7}  "
          f"{'IV':>7}  {'v':>2}  {'Updated':>10}")
    print(f"  {'─'*65}")
    for r in rows:
        mos   = (r.get("base_mos") or 0)
        iv    = (r.get("base_iv")  or 0)
        mos_col = C.GREEN if mos > 0 else C.RED
        date  = str(r.get("updated_at",""))[:10]
        print(f"  {r['ticker']:>6}  {str(r.get('conviction_score','')):>5}  "
              f"{(r.get('position_tier') or ''):>12}  "
              f"{mos_col}{mos:>+7.1%}{C.RESET}  "
              f"${iv:>6.2f}  v{r.get('version',1)}  {date}")
    print(f"\n  {len(rows)} active thesis/theses in thesis_memory.")


# ─────────────────────────────────────────────────────────────────────────────
# Review view
# ─────────────────────────────────────────────────────────────────────────────

def review_thesis(ticker: str):
    db = ThesisDB()
    thesis = db.get_latest(ticker.upper())
    db.close()

    if not thesis:
        print(f"\n{C.RED}No thesis found for {ticker}.{C.RESET}")
        print(f"  Run: python3 -m aletheia.tools.thesis_builder --ticker {ticker}")
        return

    _print_banner()
    print(f"  {C.BOLD}{C.GOLD}{ticker.upper()} — Thesis v{thesis['version']}{C.RESET}")
    print(f"  Status: {thesis.get('status','')}")
    print(f"  Updated: {str(thesis.get('updated_at',''))[:10]}")
    _divider()

    sections = [
        ("ONE-SENTENCE THESIS",    "one_sentence"),
        ("ASSUMPTION 1",           "assumption_1"),
        ("ASSUMPTION 2",           "assumption_2"),
        ("ASSUMPTION 3",           "assumption_3"),
        ("12-MONTH CONFIRMATION",  "confirmation_12m"),
        ("FALSIFICATION TRIGGER",  "falsification"),
        ("MOAT ASSESSMENT",        "moat_powers"),
        ("UNIT ECONOMICS",         "unit_economics"),
    ]

    for title, key in sections:
        print(f"\n  {C.BLUE}{C.BOLD}{title}{C.RESET}")
        text = thesis.get(key, "(not set)")
        for line in textwrap.wrap(text, 68):
            print(f"    {line}")

    _divider()
    base_iv  = thesis.get("base_iv")  or 0
    base_mos = thesis.get("base_mos") or 0
    entry    = thesis.get("entry_price") or 0
    mos_col  = C.GREEN if base_mos > 0 else C.RED
    print(f"\n  Entry: ${entry:.2f}  Base IV: ${base_iv:.2f}  "
          f"MoS: {mos_col}{base_mos:+.1%}{C.RESET}")
    print(f"  Initial: {thesis.get('initial_size_pct',0):.0%}  "
          f"Max: {thesis.get('max_size_pct',0):.0%}")
    if thesis.get("reflexivity_cap"):
        print(f"  {C.GOLD}★ Reflexivity cap active{C.RESET}")


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Aletheia Investment Thesis Builder — Section 16.2",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 -m aletheia.tools.thesis_builder --ticker MSFT
  python3 -m aletheia.tools.thesis_builder --ticker LLY --review
  python3 -m aletheia.tools.thesis_builder --ticker BRK-B --update
  python3 -m aletheia.tools.thesis_builder --ticker MSFT --export-pdf
  python3 -m aletheia.tools.thesis_builder --list
        """
    )
    parser.add_argument("--ticker",     help="Ticker to build/review/update thesis for")
    parser.add_argument("--review",     action="store_true", help="Review existing thesis")
    parser.add_argument("--update",     action="store_true", help="Update thesis (new version)")
    parser.add_argument("--export-pdf", action="store_true", help="Export thesis to PDF")
    parser.add_argument("--list",       action="store_true", help="List all active theses")
    args = parser.parse_args()

    # ── Retirement banner ────────────────────────────────────────────────
    # Free-text Thesis Builder retired in the dashboard-wiring change.
    # Read operations (--list, --review, --export-pdf) remain functional
    # for historical access. Write operations (default build, --update)
    # are deprecated and will be hard-disabled in 1-2 weeks.
    is_write_op = (not args.list) and (not args.review) and (not args.export_pdf)
    if is_write_op:
        print(
            f"\n{C.RED}{C.BOLD}╔══════════════════════════════════════════════════════════════════╗{C.RESET}\n"
            f"{C.RED}{C.BOLD}║  DEPRECATED — Free-text Thesis Builder is retiring               ║{C.RESET}\n"
            f"{C.RED}{C.BOLD}╚══════════════════════════════════════════════════════════════════╝{C.RESET}\n"
            f"  {C.GOLD}Structured analyst judgment now lives in the Qualitative\n"
            f"  Dashboard (per-dimension assessments). The integrated thesis is\n"
            f"  produced by the thesis_synthesizer agent and surfaced via\n"
            f"  GET /ticker/<T>/summary -> 4_valuation_synthesis.thesis_synthesis.{C.RESET}\n"
            f"\n  This CLI's write commands (default build, --update) will be\n"
            f"  hard-disabled within 1-2 weeks. Read commands (--list, --review,\n"
            f"  --export-pdf) remain available for historical access.\n"
        )

    if args.list:
        list_theses()
        return

    if not args.ticker:
        parser.print_help()
        return

    ticker = args.ticker.upper()

    if args.review:
        review_thesis(ticker)
        return

    if args.export_pdf:
        db = ThesisDB()
        thesis = db.get_latest(ticker)
        db.close()
        if not thesis:
            print(f"{C.RED}No thesis found for {ticker}. Build one first.{C.RESET}")
            sys.exit(1)
        path = export_pdf(thesis)
        print(f"\n{C.GREEN}✓ PDF exported: {path}{C.RESET}")
        return

    if args.update:
        db = ThesisDB()
        existing = db.get_latest(ticker)
        db.close()
        if not existing:
            print(f"{C.GOLD}No existing thesis — creating new.{C.RESET}")
            existing = None
        thesis = build_thesis(ticker, existing=existing)
    else:
        # Check if thesis already exists
        db = ThesisDB()
        existing = db.get_latest(ticker)
        db.close()
        if existing:
            print(f"\n{C.GOLD}Thesis already exists for {ticker} (v{existing['version']}).{C.RESET}")
            print(f"  Use --update to create a new version, or --review to view it.")
            if not _confirm("Create a new version anyway?"):
                sys.exit(0)
        thesis = build_thesis(ticker)

    # Auto-export PDF after saving
    if _confirm("\nExport thesis as PDF?"):
        path = export_pdf(thesis)
        print(f"\n{C.GREEN}✓ PDF: {path}{C.RESET}")

    print(f"\n{C.BOLD}{C.GOLD}Thesis complete. Next steps:{C.RESET}")
    print(f"  1. Review entry protocol:  --ticker {ticker} --review")
    print(f"  2. Run quarterly check:    aletheia.tools.exit_trigger_monitor --ticker {ticker}")
    print(f"  3. Add to watchlist for quarterly re-run of pipeline + exit check")


if __name__ == "__main__":
    main()
