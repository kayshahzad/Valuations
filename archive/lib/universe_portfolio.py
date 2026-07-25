"""
aletheia/tools/universe_portfolio.py

Universe Portfolio Table
========================
Aggregates all per-ticker pipeline outputs into a single ranked view.
This is the primary deliverable of the system — what a portfolio manager
uses every morning to identify where analytical attention is warranted.

Pulls from three sources:
  1. InvestmentDatabase — cleaned fundamentals (Phase 1)
  2. Serving reports JSON — Phase 2 valuation outputs + agent outputs
  3. ScreeningEngine — all 34 framework metrics

Output: ranked universe table + detailed per-ticker summary

Usage:
    from aletheia.tools.universe_portfolio import UniversePortfolio
    up = UniversePortfolio()
    up.print_universe()

    # Or run directly:
    PYTHONPATH=. python3 aletheia/tools/universe_portfolio.py
"""

import json
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Dict, List

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")

REPORT_DIR = Path("valuation_data/serving/latest")


# ─────────────────────────────────────────────────────────────────────────────
# Per-ticker summary row
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TickerRow:
    ticker: str

    # Market
    price: float = 0.0
    market_cap_bn: float = 0.0

    # Conviction (from lead agent)
    conviction: Optional[int] = None       # -10 to +10
    moat_score: Optional[float] = None     # 0-10

    # Phase 2 valuation
    base_iv: Optional[float] = None        # intrinsic value per share (base)
    margin_of_safety: Optional[float] = None
    bear_iv: Optional[float] = None
    bull_iv: Optional[float] = None

    # Reverse DCF
    implied_cagr: Optional[float] = None
    historical_cagr: Optional[float] = None
    rdcf_signal: str = ""

    # Multiple decomposition
    ev_ebitda: Optional[float] = None
    justified_ev_ebitda: Optional[float] = None
    multiple_premium: Optional[float] = None
    multiple_signal: str = ""
    roic: Optional[float] = None
    wacc: Optional[float] = None
    value_creation: str = ""

    # Fundamentals (Phase 1)
    revenue_bn: Optional[float] = None
    ebitda_bn: Optional[float] = None
    fcf_bn: Optional[float] = None
    fcf_margin: Optional[float] = None
    gross_margin: Optional[float] = None

    # Quality screens
    beneish_score: Optional[float] = None
    beneish_flagged: bool = False
    sloan_ratio: Optional[float] = None
    sloan_signal: str = ""
    quality_score: Optional[float] = None

    # Errors
    errors: List[str] = field(default_factory=list)


# ─────────────────────────────────────────────────────────────────────────────
# Loader
# ─────────────────────────────────────────────────────────────────────────────

def _safe(val, fallback=None):
    if val is None:
        return fallback
    try:
        f = float(val)
        return fallback if np.isnan(f) else f
    except (TypeError, ValueError):
        return fallback


def load_ticker_row(ticker: str) -> TickerRow:
    row = TickerRow(ticker=ticker)

    # ── Live market data ──────────────────────────────────────────────────────
    from aletheia.data.market_data import get_current_price, get_market_cap
    try:
        row.price = get_current_price(ticker)
        row.market_cap_bn = get_market_cap(ticker) / 1e9
    except Exception as e:
        row.errors.append(f"market_data: {e}")

    # ── Serving report (Phase 2 + agent outputs) ──────────────────────────────
    report_path = REPORT_DIR / f"{ticker}_report.json"
    if report_path.exists():
        try:
            report = json.loads(report_path.read_text())

            # Conviction and moat
            thesis = report.get("4_valuation_synthesis", {}).get("investment_thesis", {})
            row.conviction = thesis.get("conviction_score")
            er = report.get("1_economic_reality", {})
            row.moat_score = _safe(er.get("moat", {}).get("score"))

            # Phase 2 valuation
            p2 = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {})
            dcf3 = p2.get("three_scenario_dcf", {})
            row.base_iv = _safe(dcf3.get("base", {}).get("intrinsic_per_share"))
            row.bear_iv = _safe(dcf3.get("bear", {}).get("intrinsic_per_share"))
            row.bull_iv = _safe(dcf3.get("bull", {}).get("intrinsic_per_share"))
            row.margin_of_safety = _safe(dcf3.get("base", {}).get("margin_of_safety"))

            # Reverse DCF
            rdcf = p2.get("reverse_dcf", {})
            row.implied_cagr = _safe(rdcf.get("implied_cagr_10y"))
            row.historical_cagr = _safe(rdcf.get("historical_cagr"))
            row.rdcf_signal = rdcf.get("signal", "")

            # Multiple decomposition
            md = p2.get("multiple_decomposition", {})
            row.ev_ebitda = _safe(md.get("market_ev_ebitda"))
            row.justified_ev_ebitda = _safe(md.get("justified_ev_ebitda"))
            row.multiple_premium = _safe(md.get("premium_pct"))
            row.multiple_signal = md.get("signal", "")
            row.roic = _safe(md.get("roic"))
            row.wacc = _safe(md.get("wacc"))
            row.value_creation = md.get("value_creation", "")

        except Exception as e:
            row.errors.append(f"report: {e}")
    else:
        row.errors.append("No report — run main.py first")

    # ── Database (Phase 1 fundamentals + screens) ─────────────────────────────
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker)

        if not df.empty:
            latest = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]
            row.revenue_bn = _safe(latest.get("clean_Revenue"), 0) / 1e9
            row.ebitda_bn = _safe(latest.get("derived_EBITDA"), 0) / 1e9
            row.fcf_bn = _safe(latest.get("derived_FCF"), 0) / 1e9
            row.fcf_margin = _safe(latest.get("derived_FCF_Margin_Pct"))
            row.gross_margin = _safe(latest.get("derived_GrossMargin_Pct"))
            row.quality_score = _safe(latest.get("overall_quality_score"))

        # Latest screen results
        screens = db.query(
            f"SELECT * FROM screen_results WHERE ticker='{ticker}' "
            f"ORDER BY screened_at DESC LIMIT 1"
        )
        if not screens.empty:
            s = screens.iloc[0]
            row.beneish_score = _safe(s.get("beneish_m_score"))
            row.beneish_flagged = bool(s.get("beneish_flagged", False))
            row.sloan_ratio = _safe(s.get("sloan_accrual_ratio"))
            row.sloan_signal = str(s.get("sloan_signal", ""))

        db.close()
    except Exception as e:
        row.errors.append(f"database: {e}")

    return row


# ─────────────────────────────────────────────────────────────────────────────
# Portfolio display
# ─────────────────────────────────────────────────────────────────────────────

class UniversePortfolio:

    def __init__(self, snapshot: 'UniverseSnapshot'):
        self.snapshot = snapshot
        self.tickers = snapshot.tickers

    def load_all(self) -> List[TickerRow]:
        rows = []
        for ticker in self.tickers:
            print(f"  Loading {ticker}...")
            rows.append(load_ticker_row(ticker))
        return rows

    def rank(self, rows: List[TickerRow]) -> List[TickerRow]:
        """
        Rank tickers by:
          1. Conviction score (higher = better opportunity)
          2. Margin of safety (larger = better entry)
          3. Multiple signal (undervalued first)
        """
        signal_rank = {
            "undervalued": 0,
            "fairly_valued": 1,
            "moderate_premium": 2,
            "high_premium": 3,
            "speculative_premium": 4,
            "": 5,
        }

        def sort_key(r: TickerRow):
            conv = r.conviction if r.conviction is not None else -99
            mos = r.margin_of_safety if r.margin_of_safety is not None else -99
            sig = signal_rank.get(r.multiple_signal, 5)
            return (-conv, -mos, sig)

        return sorted(rows, key=sort_key)

    def print_universe(self):
        """Print the complete ranked universe table."""
        print("\n" + "█"*100)
        print("  ALETHEIA — UNIVERSE PORTFOLIO TABLE")
        print("  Ranked by conviction → margin of safety → multiple signal")
        print("█"*100)

        rows = self.load_all()
        ranked = self.rank(rows)

        # ── Table 1: Valuation Summary ─────────────────────────────────────────
        print("\n" + "─"*100)
        print("  VALUATION SUMMARY")
        print("─"*100)
        hdr = (f"{'Ticker':>6} │ {'Conv':>5} │ {'Price':>7} │ "
               f"{'Bear IV':>8} │ {'Base IV':>8} │ {'Bull IV':>8} │ "
               f"{'MoS':>7} │ {'Signal':>22} │ {'Moat':>5}")
        print(f"  {hdr}")
        print("  " + "─"*96)

        for r in ranked:
            conv_str = f"{r.conviction:+d}" if r.conviction is not None else "N/A"
            price_str = f"${r.price:,.0f}" if r.price else "N/A"
            bear_str = f"${r.bear_iv:,.0f}" if r.bear_iv else "—"
            base_str = f"${r.base_iv:,.0f}" if r.base_iv else "—"
            bull_str = f"${r.bull_iv:,.0f}" if r.bull_iv else "—"
            mos_str = f"{r.margin_of_safety:+.1%}" if r.margin_of_safety is not None else "—"
            sig_str = r.multiple_signal or "—"
            moat_str = f"{r.moat_score:.1f}" if r.moat_score else "—"

            # Color-code signal
            sig_icon = {"undervalued": "★", "fairly_valued": "◆",
                        "moderate_premium": "▲", "high_premium": "⚠",
                        "speculative_premium": "✗"}.get(r.multiple_signal, " ")

            print(f"  {r.ticker:>6} │ {conv_str:>5} │ {price_str:>7} │ "
                  f"{bear_str:>8} │ {base_str:>8} │ {bull_str:>8} │ "
                  f"{mos_str:>7} │ {sig_icon}{sig_str:<21} │ {moat_str:>5}")

        # ── Table 2: Growth and Implied CAGR ──────────────────────────────────
        print("\n" + "─"*100)
        print("  REVERSE DCF — WHAT IS THE MARKET PRICING IN?")
        print("─"*100)
        hdr2 = (f"{'Ticker':>6} │ {'Price':>7} │ "
                f"{'Implied CAGR':>13} │ {'Hist CAGR':>10} │ "
                f"{'Ratio':>7} │ {'Signal':>18} │ "
                f"{'EV/EBITDA':>10} │ {'Justified':>10} │ {'Premium':>9}")
        print(f"  {hdr2}")
        print("  " + "─"*96)

        for r in ranked:
            imp_str = f"{r.implied_cagr:.1%}" if r.implied_cagr is not None else "—"
            hist_str = f"{r.historical_cagr:.1%}" if r.historical_cagr else "—"
            ratio_str = (f"{r.implied_cagr/r.historical_cagr:.1f}x"
                         if r.implied_cagr and r.historical_cagr and r.historical_cagr > 0
                         else "—")
            sig_str = r.rdcf_signal or "—"
            ev_str = f"{r.ev_ebitda:.1f}x" if r.ev_ebitda else "—"
            just_str = f"{r.justified_ev_ebitda:.1f}x" if r.justified_ev_ebitda else "—"
            prem_str = f"{r.multiple_premium:+.0%}" if r.multiple_premium is not None else "—"

            print(f"  {r.ticker:>6} │ ${r.price:>6,.0f} │ "
                  f"{imp_str:>13} │ {hist_str:>10} │ "
                  f"{ratio_str:>7} │ {sig_str:>18} │ "
                  f"{ev_str:>10} │ {just_str:>10} │ {prem_str:>9}")

        # ── Table 3: Fundamentals ──────────────────────────────────────────────
        print("\n" + "─"*100)
        print("  FUNDAMENTALS — PHASE 1 CLEANED DATA")
        print("─"*100)
        hdr3 = (f"{'Ticker':>6} │ {'Revenue':>9} │ {'EBITDA':>8} │ "
                f"{'FCF':>8} │ {'FCF Marg':>9} │ {'GM%':>6} │ "
                f"{'ROIC':>7} │ {'WACC':>6} │ {'Spread':>7} │ {'Quality':>8}")
        print(f"  {hdr3}")
        print("  " + "─"*96)

        for r in ranked:
            rev_str = f"${r.revenue_bn:.0f}B" if r.revenue_bn else "—"
            ebitda_str = f"${r.ebitda_bn:.0f}B" if r.ebitda_bn else "—"
            fcf_str = f"${r.fcf_bn:.0f}B" if r.fcf_bn else "—"
            fcfm_str = f"{r.fcf_margin:.1f}%" if r.fcf_margin else "—"
            gm_str = f"{r.gross_margin:.1f}%" if r.gross_margin else "—"
            roic_str = f"{r.roic:.1%}" if r.roic else "—"
            wacc_str = f"{r.wacc:.1%}" if r.wacc else "—"
            spread_str = (f"{(r.roic - r.wacc):+.1%}"
                          if r.roic and r.wacc else "—")
            qual_str = f"{r.quality_score:.2f}" if r.quality_score else "—"

            vc_icon = {"creating": "★", "neutral": "◆", "destroying": "✗"}.get(
                r.value_creation, " "
            )

            print(f"  {r.ticker:>6} │ {rev_str:>9} │ {ebitda_str:>8} │ "
                  f"{fcf_str:>8} │ {fcfm_str:>9} │ {gm_str:>6} │ "
                  f"{roic_str:>7} │ {wacc_str:>6} │ {vc_icon}{spread_str:>6} │ {qual_str:>8}")

        # ── Table 4: Quality Screens ───────────────────────────────────────────
        print("\n" + "─"*100)
        print("  QUALITY SCREENS — BENEISH M-SCORE + SLOAN ACCRUAL RATIO")
        print("─"*100)
        hdr4 = (f"{'Ticker':>6} │ {'Beneish':>9} │ {'Flag':>6} │ "
                f"{'Sloan':>8} │ {'Signal':>14} │ "
                f"{'MktCap':>8} │ {'Errors':>6}")
        print(f"  {hdr4}")
        print("  " + "─"*70)

        for r in ranked:
            ben_str = f"{r.beneish_score:.3f}" if r.beneish_score is not None else "—"
            ben_flag = "⚠ YES" if r.beneish_flagged else "✓  no"
            sloan_str = f"{r.sloan_ratio:.3f}" if r.sloan_ratio is not None else "—"
            sloan_sig = r.sloan_signal or "—"
            mc_str = f"${r.market_cap_bn:.0f}B" if r.market_cap_bn else "—"
            err_str = f"{len(r.errors)} err" if r.errors else "✓ ok"

            print(f"  {r.ticker:>6} │ {ben_str:>9} │ {ben_flag:>6} │ "
                  f"{sloan_str:>8} │ {sloan_sig:>14} │ "
                  f"{mc_str:>8} │ {err_str:>6}")

        # ── Summary statistics ─────────────────────────────────────────────────
        print("\n" + "─"*100)
        print("  UNIVERSE SUMMARY STATISTICS")
        print("─"*100)

        valid_convictions = [r.conviction for r in rows if r.conviction is not None]
        valid_mos = [r.margin_of_safety for r in rows if r.margin_of_safety is not None]
        valid_roic = [r.roic for r in rows if r.roic is not None]

        if valid_convictions:
            print(f"  Conviction: avg={np.mean(valid_convictions):.1f}  "
                  f"min={min(valid_convictions)}  max={max(valid_convictions)}")
        if valid_mos:
            print(f"  Margin of Safety: avg={np.mean(valid_mos):+.1%}  "
                  f"min={min(valid_mos):+.1%}  max={max(valid_mos):+.1%}")
        if valid_roic:
            print(f"  ROIC: avg={np.mean(valid_roic):.1%}  "
                  f"min={min(valid_roic):.1%}  max={max(valid_roic):.1%}")

        beneish_flagged = [r.ticker for r in rows if r.beneish_flagged]
        if beneish_flagged:
            print(f"  ⚠ Beneish flagged: {', '.join(beneish_flagged)}")
        else:
            print(f"  ✓ No Beneish flags in universe")

        undervalued = [r.ticker for r in rows
                       if r.multiple_signal in ("undervalued", "fairly_valued")]
        if undervalued:
            print(f"  ★ Relatively attractive multiples: {', '.join(undervalued)}")

        # Errors summary
        errors = [(r.ticker, r.errors) for r in rows if r.errors]
        if errors:
            print(f"\n  ⚠ Data gaps (run pipeline to fix):")
            for t, errs in errors:
                print(f"    {t}: {'; '.join(errs[:2])}")

        print("█"*100)
        print()

    def to_csv(self, filepath: str = "valuation_data/serving/universe_table.csv"):
        """Export universe table to CSV."""
        rows = self.load_all()
        data = []
        for r in rows:
            data.append({
                "ticker": r.ticker,
                "price": r.price,
                "market_cap_bn": r.market_cap_bn,
                "conviction": r.conviction,
                "moat_score": r.moat_score,
                "base_iv": r.base_iv,
                "bear_iv": r.bear_iv,
                "bull_iv": r.bull_iv,
                "margin_of_safety": r.margin_of_safety,
                "implied_cagr": r.implied_cagr,
                "historical_cagr": r.historical_cagr,
                "rdcf_signal": r.rdcf_signal,
                "ev_ebitda": r.ev_ebitda,
                "justified_ev_ebitda": r.justified_ev_ebitda,
                "multiple_premium": r.multiple_premium,
                "multiple_signal": r.multiple_signal,
                "roic": r.roic,
                "wacc": r.wacc,
                "value_creation": r.value_creation,
                "revenue_bn": r.revenue_bn,
                "ebitda_bn": r.ebitda_bn,
                "fcf_bn": r.fcf_bn,
                "fcf_margin": r.fcf_margin,
                "gross_margin": r.gross_margin,
                "beneish_score": r.beneish_score,
                "beneish_flagged": r.beneish_flagged,
                "sloan_ratio": r.sloan_ratio,
                "sloan_signal": r.sloan_signal,
                "quality_score": r.quality_score,
            })
        df = pd.DataFrame(data)
        df.to_csv(filepath, index=False)
        print(f"✓ Universe table saved to {filepath}")
        return df


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

# CLI entrypoint removed to avoid architectural violations.
