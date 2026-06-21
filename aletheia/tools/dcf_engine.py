"""
aletheia/tools/dcf_engine.py

Phase 2 — DCF Engine
=====================
Implements the NorthWestern methodology DCF directly from CleanedRecord data.

Core formula (Cash Flow from Assets):
    CFA = Revenue - Operating Costs - Taxes + D&A - CapEx - ΔNWC
      = NOPAT + D&A - CapEx - ΔNWC
      = FCFF

Three scenarios: bull / base / bear
Each scenario has explicit, documented assumptions stored in ScenarioAssumptions.

WACC is computed from live market inputs:
    Ke = Rf + Beta × MRP   (CAPM)
    Kd = Interest Expense / Avg Debt
    WACC = (E/V)×Ke + (D/V)×Kd×(1-T)

Terminal value — both methods computed and cross-checked:
    Gordon Growth : TV = FCF_final × (1+g) / (WACC - g)
    Reinvestment  : TV = NOPAT_final × (1 - g/ROIC) / (WACC - g)  [NorthWestern]

Usage:
    from aletheia.tools.dcf_engine import DCFEngine
    engine = DCFEngine()
    result = engine.run("TICKER")
    print(result.summary())
"""

import datetime
import warnings
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple

import numpy as np
import pandas as pd

from aletheia.calculations import (
    _require_range,
    RANGE_BOUNDS,
    CalculationError,
    CalculationOutputError,
    _guard_mode,
    resolve_tax_rate,
)
from aletheia.calculations.rollforward import fcf_pathway_b as _rf_fcf_b



warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

from aletheia.calculations.macro import market_risk_premium as _mrp
# Damodaran implied ERP, sourced from valuation_data/macro/damodaran_implied_erp.csv
# (single source shared with every specialized engine + the backtest); 4.75%
# fallback. Resolved at import — servers restart on deploy and the ERP moves ~annually.
MARKET_RISK_PREMIUM = _mrp()
DEFAULT_WACC        = 0.09        # Fallback if WACC computation fails
DEFAULT_TERMINAL_G  = 0.025       # 2.5% long-run nominal GDP growth
MAX_TERMINAL_G      = 0.04        # Hard cap — requires megatrend justification
BETA_PERIOD         = "5y"        # 5-year weekly beta regression
BETA_INTERVAL       = "1wk"


# ─────────────────────────────────────────────────────────────────────────────
# Data structures
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class ScenarioAssumptions:
    """Explicit, documented assumptions for one DCF scenario."""
    name: str                          # "bull", "base", "bear"

    # Revenue
    revenue_growth_rates: List[float]
    revenue_cagr_y1_5: float           # Years 1-5 revenue CAGR (for metadata)
    revenue_cagr_y6_10: float          # Years 6-10 revenue CAGR (for metadata)

    # Margins
    ebit_margin_terminal: float        # Terminal EBIT margin
    ebit_margin_current: float         # Starting EBIT margin (from DB)

    # Reinvestment
    capex_pct_revenue: float           # CapEx as % of revenue
    da_pct_revenue: float              # D&A as % of revenue
    nwc_pct_revenue: float             # Net working capital as % of revenue

    # Cost of capital
    wacc: float                        # Scenario-specific WACC
    terminal_growth: float             # Gordon growth rate

    # Tax
    tax_rate: float                    # Cash tax rate

    # ROIC Methodology
    base_roic: float = 0.10            # Historical starting ROIC
    terminal_roic_override: Optional[float] = None  # analyst override of terminal ROIC

    # Justification (required for assumption discipline)
    justification: str = ""

    @property
    def terminal_roic(self) -> float:
        """
        Returns the terminal ROIC used to calculate the NorthWestern reinvestment-rate
        terminal value.

        Methodology Choice: This framework rejects the standard academic assumption
        that competitive advantages erode and ROIC fades to WACC over the projection
        period. Instead, it explicitly holds the company's historical `base_roic`
        constant into perpetuity (floored at 8%), assuming genuine moats persist.

        An explicit analyst ``terminal_roic_override`` (from the assumption-
        grounding bridge) wins over the historical anchor when set.
        """
        if self.terminal_roic_override is not None:
            return max(float(self.terminal_roic_override), 0.0)
        return max(self.base_roic, 0.08)


@dataclass
class YearProjection:
    """One year of projected financials."""
    year: int
    fiscal_year: int
    revenue: float
    ebit_margin: float
    ebit: float
    tax_expense: float
    nopat: float
    da: float
    capex: float
    delta_nwc: float
    fcff: float                        # Free Cash Flow to Firm (CFA formula)
    pv_fcff: float                     # Present value of FCFF
    cumulative_pv: float


@dataclass
class TerminalValue:
    gordon_tv: float                   # Gordon Growth terminal value
    reinvestment_tv: float             # NorthWestern reinvestment-rate TV
    tv_used: float                     # Which one we use (reinvestment preferred)
    pv_tv: float                       # Present value of terminal value
    implied_tv_ebitda_multiple: float  # TV / terminal EBITDA (sanity check)
    tv_pct_of_ev: float                # Terminal value as % of enterprise value


@dataclass
class ScenarioResult:
    """Full result for one scenario."""
    assumptions: ScenarioAssumptions
    projections: List[YearProjection] = field(default_factory=list)
    terminal: Optional[TerminalValue] = None

    enterprise_value: float = 0.0
    pv_explicit_period: float = 0.0
    implied_ev_ebitda: float = 0.0    # EV / latest EBITDA
    implied_ev_ebit: float = 0.0

    # NorthWestern multiple decomposition
    # EV/EBITDA = NOPATn*(1-g/ROIC)/EBITDA / (WACC-g)
    justified_ev_ebitda: float = 0.0  # What multiple is mathematically justified
    roic_wacc_spread: float = 0.0

    warnings: List[str] = field(default_factory=list)
    metadata: Dict[str, str] = field(default_factory=dict)


@dataclass
class DCFResult:
    """Complete DCF output for all three scenarios."""
    ticker: str
    fiscal_year: int
    run_date: str = field(default_factory=lambda: datetime.datetime.now(datetime.timezone.utc).isoformat())

    # Phase Q-5: which period drove the base-year inputs. 'TTM' when a
    # TTM row is available (default for current 10-Q-fresh valuation),
    # 'FY' when only annual data exists. The Deep Dive surfaces an
    # FY-base reconciliation row alongside so analysts can see how
    # much of the IPS movement comes from rolling forward the trailing
    # twelve months vs the underlying scenario assumptions.
    base_period: str = "FY"
    base_period_end_date: Optional[str] = None
    fy_fiscal_year: Optional[int] = None  # for FY-reconciliation context

    # Market inputs
    current_price: float = 0.0
    shares_diluted: float = 0.0
    market_cap: float = 0.0
    risk_free_rate: float = 0.0
    beta: float = 1.0
    wacc_base: float = 0.0
    wacc_total_debt: float = 0.0   # debt figure used in the WACC weights
    # β-reference diagnostic (Build 1 / spec §7). β here is the ^GSPC β that
    # drives WACC; these expose the marginal-investor (sector) β + R² so the
    # discount-rate section can flag a mis-referenced β. Diagnostic only.
    beta_r2: Optional[float] = None
    beta_sector: Optional[float] = None
    beta_sector_r2: Optional[float] = None
    beta_benchmark: Optional[str] = None

    # Base financials (from DB)
    revenue: float = 0.0
    ebitda: float = 0.0
    ebit: float = 0.0
    nopat: float = 0.0
    roic: float = 0.0
    fcf: float = 0.0
    net_debt: float = 0.0

    # Scenario results
    bull: Optional[ScenarioResult] = None
    base: Optional[ScenarioResult] = None
    bear: Optional[ScenarioResult] = None

    confidence: str = "high"
    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    tax_rate_source: str = "statutory"  # cash | gaap | company_fy | statutory | analyst_override

    @property
    def wacc(self) -> float:
        """
        Returns the baseline WACC (`wacc_base`) for public API consistency 
        with MultipleDecomposition and ReverseDCF. 
        Note: The DCF Engine projects three distinct scenarios simultaneously. 
        For scenario-specific WACCs (e.g. including the 150bps Bear stress), 
        refer to `result.bull.assumptions.wacc` or `result.bear.assumptions.wacc`.
        """
        return self.wacc_base

    def ev_to_equity(self, ev: float, net_debt: float,
                     pension_deficit: float = 0.0,
                     lease_debt: float = 0.0,
                     jva_value: float = 0.0) -> float:
        """Convert EV to equity value. Equity bridge is in equity_bridge.py."""
        equity = ev - net_debt - pension_deficit - lease_debt + jva_value
        return max(0.0, equity) # Non-recourse equity cannot be negative

    def intrinsic_per_share(self, ev: float, net_debt: float) -> Optional[float]:
        if self.shares_diluted and self.shares_diluted > 0:
            equity_val = self.ev_to_equity(ev, net_debt)
            return equity_val / self.shares_diluted
        return None

    def upside(self, intrinsic: float) -> Optional[float]:
        if intrinsic and self.current_price and self.current_price > 0:
            return (intrinsic - self.current_price) / self.current_price
        return None

    def summary(self) -> str:
        lines = [
            f"DCFResult: {self.ticker} FY{self.fiscal_year}",
            f"  Price        : ${self.current_price:,.2f}",
            f"  Market Cap   : ${self.market_cap/1e9:,.1f}B",
            f"  Risk-free    : {self.risk_free_rate:.2%}",
            f"  Beta         : {self.beta:.2f}",
            f"  WACC (base)  : {self.wacc_base:.2%}",
            f"  Revenue      : ${self.revenue/1e9:,.1f}B",
            f"  EBITDA       : ${self.ebitda/1e9:,.1f}B",
            f"  ROIC         : {self.roic:.1%}",
            f"  FCF          : ${self.fcf/1e9:,.1f}B",
            f"  Net Debt     : ${self.net_debt/1e9:,.1f}B",
        ]
        for scenario_name, scenario in [("BULL", self.bull),
                                         ("BASE", self.base),
                                         ("BEAR", self.bear)]:
            if scenario:
                ev = scenario.enterprise_value
                iv = self.intrinsic_per_share(ev, self.net_debt)
                upside = self.upside(iv) if iv else None
                if iv:
                    line = f"  {scenario_name:4s} EV={ev/1e9:,.0f}B  IV/share=${iv:,.2f}"
                else:
                    line = f"  {scenario_name:4s} EV={ev/1e9:,.0f}B  IV/share=N/A"
                lines.append(line)
                if upside is not None:
                    lines[-1] += f"  upside={upside:+.1%}"
                lines.append(
                    f"       WACC={scenario.assumptions.wacc:.2%}"
                    f"  g={scenario.assumptions.terminal_growth:.2%}"
                    f"  EV/EBITDA={scenario.implied_ev_ebitda:.1f}x"
                    f"  (justified={scenario.justified_ev_ebitda:.1f}x)"
                )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Flat dict for database storage."""
        d = {
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "run_date": self.run_date,
            "current_price": self.current_price,
            "shares_diluted": self.shares_diluted,
            "market_cap": self.market_cap,
            "risk_free_rate": self.risk_free_rate,
            "beta": self.beta,
            "wacc_base": self.wacc_base,
            "revenue": self.revenue,
            "ebitda": self.ebitda,
            "ebit": self.ebit,
            "nopat": self.nopat,
            "roic": self.roic,
            "fcf": self.fcf,
            "net_debt": self.net_debt,
        }
        for sname, scenario in [("bull", self.bull),
                                  ("base", self.base),
                                  ("bear", self.bear)]:
            if scenario:
                iv = self.intrinsic_per_share(
                    scenario.enterprise_value, self.net_debt
                )
                upside = self.upside(iv) if iv else None
                d[f"{sname}_ev"] = scenario.enterprise_value
                d[f"{sname}_intrinsic_per_share"] = iv
                d[f"{sname}_upside"] = upside
                d[f"{sname}_wacc"] = scenario.assumptions.wacc
                d[f"{sname}_terminal_g"] = scenario.assumptions.terminal_growth
                d[f"{sname}_ev_ebitda"] = scenario.implied_ev_ebitda
                d[f"{sname}_justified_ev_ebitda"] = scenario.justified_ev_ebitda
                d[f"{sname}_roic_wacc_spread"] = scenario.roic_wacc_spread
                tv = scenario.terminal
                if tv:
                    d[f"{sname}_tv_pct_of_ev"] = tv.tv_pct_of_ev
                    d[f"{sname}_implied_tv_ebitda"] = tv.implied_tv_ebitda_multiple
        return d


# ─────────────────────────────────────────────────────────────────────────────
# WACC computation
# ─────────────────────────────────────────────────────────────────────────────

from aletheia.data.market_data import get_risk_free_rate, get_beta

def _merge_ttm_with_fy_fallback(
    ttm_row: pd.Series, fy_row: pd.Series,
) -> pd.Series:
    """Phase Q-5: build the base row the DCF engine consumes when a TTM
    record is available.

    TTM values win for the fields TTM derivation actually computes (the
    raw_X / clean_X / derived_X flow + balance + ratio fields populated
    by `derive_ttm_from_fmp`). For everything else — cleaning-engine-
    only outputs like clean_NormalizedEBIT, domain_score_*, warnings_json
    — the latest FY row's value carries through. Fields that are NaN /
    None on TTM also fall back to FY so the engine sees a complete
    record, mirroring what an analyst means by "TTM rolled forward
    against the last 10-K's structural data".

    The returned Series carries TTM-flavored period_end_date,
    fiscal_year, and `period='TTM'` so callers downstream can still
    detect the base period.
    """
    # Treat NaN as missing on the TTM side so FY values fill in.
    ttm_filled = ttm_row.dropna()
    merged = fy_row.copy()
    for k, v in ttm_filled.items():
        merged[k] = v
    # Preserve TTM identity fields explicitly.
    merged["period"]          = "TTM"
    merged["period_end_date"] = ttm_row.get("period_end_date")
    merged["fiscal_year"]     = ttm_row.get("fiscal_year")
    return merged


def _compute_smoothed_capex_pct(
    df: pd.DataFrame,
    latest_revenue: float,
    latest_capex: float,
) -> float:
    """
    CapEx as % of revenue using a 5-year rolling average across the cleaned
    history. Reduces sensitivity to peak-cycle CapEx (Azure / AI infrastructure
    buildout, factory expansion cycles) being misinterpreted as steady-state.

    Falls back to single-year ratio if <3 years of history available, or if
    the smoothed value is unreasonable (negative, zero, or > 100%).
    """
    fallback = latest_capex / latest_revenue if latest_revenue > 0 else 0.04
    if df is None or df.empty:
        return fallback

    if "fiscal_year" in df.columns:
        ordered = df.sort_values("fiscal_year").tail(5)
    else:
        ordered = df.tail(5)

    if len(ordered) < 3:
        return fallback

    capex_col = "derived_CapEx" if "derived_CapEx" in ordered.columns else None
    rev_col   = "clean_Revenue" if "clean_Revenue" in ordered.columns else None
    if capex_col is None or rev_col is None:
        return fallback

    ratios: List[float] = []
    for _, row in ordered.iterrows():
        c = row.get(capex_col)
        r = row.get(rev_col)
        if c is None or r is None or pd.isna(c) or pd.isna(r) or r <= 0:
            continue
        ratio = abs(float(c)) / float(r)
        if 0.0 < ratio < 1.0:
            ratios.append(ratio)

    if len(ratios) < 3:
        return fallback
    smoothed = sum(ratios) / len(ratios)
    if not (0.0 < smoothed < 1.0):
        return fallback
    return smoothed


def _fetch_risk_free_rate() -> float:
    """Fetch current 10-year US Treasury yield from market_data."""
    return get_risk_free_rate()

# ─────────────────────────────────────────────────────────────────────────
# Sector-aware beta floors
#
# 5Y-weekly-vs-SPY regression produces implausibly low betas for defensive
# names (JNJ regressed to 0.24, MRK 0.26, KO 0.37 — all ~half of consensus
# Bloomberg/sector-average values). Likely cause: COVID anomalies or
# low-correlation periods dominating the regression window.
#
# Applying a sector floor stops a single bad regression from poisoning the
# entire DCF chain (low β → low WACC → inflated IPS for half the universe).
# Floors approximate Damodaran sector-average industry betas; conservative
# (use lowest end of plausible range) to minimize false-positive clamping.
# When the floor binds, the result is flagged via `beta_clamped=True` in
# the DCFResult so downstream consumers can surface the override.
# ─────────────────────────────────────────────────────────────────────────

_SECTOR_BETA_FLOORS: dict = {
    "Technology":             1.00,
    "Semiconductors":         1.20,
    "Auto Manufacturers":     1.20,
    "Healthcare":             0.70,
    "Healthcare Plans":       0.60,
    "Financials":             0.90,
    # Industrials raised 0.85 → 1.00 — heavy-machinery (CAT) and
    # Class I freight rail (NSC, UNP) are genuinely cyclical with
    # consensus betas ≥1.0; the prior 0.85 floor was below FMP's
    # 1.30-1.63 actuals for these names.
    "Industrials":            1.00,
    "Consumer Defensive":     0.50,
    "Consumer Discretionary": 0.85,
    "Utilities":              0.40,
}


def _sector_beta_floor(ticker: str) -> float:
    """Return the sector-aware β floor for `ticker`. Falls back to 0.6
    when the ticker isn't in the classification universe (a safer
    default than 0 for unknown names)."""
    try:
        from config.ticker_classification import get_extended_universe
        cls = get_extended_universe().get(ticker.upper())
        if cls is None:
            return 0.6
        return _SECTOR_BETA_FLOORS.get(cls.sector, 0.6)
    except Exception:
        return 0.6


def _sector_of(ticker: str) -> Optional[str]:
    """Resolve a ticker's sector from the classification universe (for the
    β-reference benchmark map). Returns None when unknown."""
    try:
        from config.ticker_classification import get_extended_universe
        cls = get_extended_universe().get(ticker.upper())
        return getattr(cls, "sector", None) if cls else None
    except Exception:
        return None


def _compute_beta(ticker: str, period: str = BETA_PERIOD,
                  interval: str = BETA_INTERVAL) -> float:
    """Compute 5-year weekly beta from market_data, then apply the
    sector-aware floor. Single point — every consumer of `_compute_beta`
    (calc_node, strategist) gets the floored value automatically.
    """
    raw = get_beta(ticker, period=period, interval=interval)
    floor = _sector_beta_floor(ticker)
    if raw is None:
        return floor
    return max(raw, floor)


def compute_wacc(
    ticker: str,
    total_equity: float,      # Market cap
    total_debt: float,        # Book value of debt (LTD + STD)
    interest_expense: float,  # Annual interest expense
    tax_rate: float,
    risk_free_rate: Optional[float] = None,
    beta: Optional[float] = None,
    mrp: float = MARKET_RISK_PREMIUM,
) -> Tuple[float, float, float, float]:
    """
    Compute WACC for ``ticker``.

    Orchestration only: resolves Rf from live market data, computes
    beta from the ticker's price history, then delegates the math to
    ``aletheia.calculations.formulas.cost_of_capital``. The central
    module owns the formula, bounds, and fallback policy — see Phase 4
    of the centralization refactor.

    Returns:
        (wacc, ke, kd, beta)
    """
    from aletheia.calculations.formulas import (
        cost_of_equity as _cost_of_equity,
        cost_of_debt as _cost_of_debt,
        wacc as _wacc,
    )

    rf = risk_free_rate or _fetch_risk_free_rate()
    b = beta or _compute_beta(ticker)

    ke = _cost_of_equity(
        risk_free_rate=rf, beta=b, market_risk_premium=mrp,
    )
    kd = _cost_of_debt(
        interest_expense=interest_expense,
        total_debt=total_debt,
        risk_free_rate=rf,
    )
    wacc_val = _wacc(
        cost_of_equity=ke,
        cost_of_debt=kd,
        total_equity=total_equity,
        total_debt=total_debt,
        tax_rate=tax_rate,
        risk_free_rate=rf,
    )

    return float(wacc_val), float(ke or 0.0), float(kd or 0.0), float(b)


def wacc_at_beta(result: "DCFResult", beta: float) -> float:
    """Recompute the headline WACC at an alternate β WITHOUT mutating the
    result (Build 1 / R1). β enters WACC only through the equity leg
    (ke = rf + β·mrp), so the marginal effect is exact and linear:

        ΔWACC = w_E · Δβ · mrp

    where w_E is the equity weight from the same capital structure the engine
    used. ``wacc_at_beta(result, result.beta) == result.wacc_base`` by
    construction. Build 4 uses this to price the justified multiple at both
    the ^GSPC β and the sector β (the mult_contrib β-band)."""
    mcap = result.market_cap or 0.0
    debt = result.wacc_total_debt or 0.0
    denom = mcap + debt
    w_e = (mcap / denom) if denom > 0 else 1.0
    return float(result.wacc_base + w_e * (beta - result.beta) * MARKET_RISK_PREMIUM)


# ─────────────────────────────────────────────────────────────────────────────
# Scenario builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_assumptions(
    scenario_name: str,
    revenue: float,
    ebit: float,
    roic: float,
    fcf: float,
    capex_pct: float,
    da_pct: float,
    nwc_pct: float,
    tax_rate: float,
    wacc_base: float,
    hist_revenue_cagr: float,
    profile: "ValuationProfile",
    lifecycle: str = "mature",
) -> ScenarioAssumptions:
    """
    Build scenario-specific assumptions from base metrics.

    Bull: above-trend growth, margin expansion, WACC compressed
    Base: consensus + historical discipline
    Bear: margin compression, WACC expansion, mean reversion
    """
    forecast_years = profile.forecast_years
    ebit_margin = ebit / revenue if revenue > 0 else 0.15

    applies_cyclical_haircut = lifecycle == "cyclical_industrial"
    cagr_y1_5_factor = 0.70 if applies_cyclical_haircut else 1.0
    cagr_y6_10_factor = 0.40 if applies_cyclical_haircut else 1.0
    terminal_g_delta = -0.005 if applies_cyclical_haircut else 0.0

    # Margin and decay logic based on profile
    margin_rev_base = 1.0 - profile.terminal_margin_decay
    margin_rev_bull = margin_rev_base + profile.bull_margin_compression
    margin_rev_bear = margin_rev_base + profile.bear_margin_compression
    
    decay_base = profile.decay_base
    decay_bull = profile.decay_bull
    decay_bear = profile.decay_bear

    # ── Terminal growth: relative spread first, uniform cap last ─────────
    #
    # Compute bull/base/bear terminal growth as relative deltas from the
    # profile's `terminal_growth` value, then apply caps uniformly to all
    # three. The previous code applied MAX_TERMINAL_G only to the bull
    # branch, which inverted bull < base for high-growth profiles
    # (NVDA/AMD/MSFT/etc.) — bull's pre-cap value of base+100bps got clipped
    # while base passed through, leaving bull with smaller terminal value
    # despite the lower WACC.
    #
    # Per locked policy: terminal growth is the perpetual rate after the
    # explicit forecast period. It must respect the long-run-GDP perpetuity
    # constraint (≤ 4%). When the cap binds, bull/base/bear may collapse to
    # a single terminal-growth value — that's correct economics, not a bug.
    # Scenario differentiation comes from explicit-period assumptions
    # (Y1-5 growth, margins, WACC) below, which still vary per scenario.
    base_terminal_g_raw = profile.terminal_growth + terminal_g_delta
    bull_terminal_g_raw = base_terminal_g_raw + 0.01
    bear_terminal_g_raw = max(base_terminal_g_raw - 0.01, 0.015)

    raw_g = {
        "bull": bull_terminal_g_raw,
        "base": base_terminal_g_raw,
        "bear": bear_terminal_g_raw,
    }[scenario_name]

    # Apply caps uniformly (global ceiling, then per-profile if tighter).
    terminal_g = min(raw_g, MAX_TERMINAL_G)
    if profile.terminal_growth_cap is not None:
        terminal_g = min(terminal_g, profile.terminal_growth_cap)

    # Audit log when cap binds — tells analysts that bull/base/bear
    # differentiation on terminal growth has collapsed (correct behavior
    # for high-growth profiles whose default exceeds the cap).
    if terminal_g < raw_g:
        cap_source = "MAX_TERMINAL_G"
        if (profile.terminal_growth_cap is not None
                and profile.terminal_growth_cap < MAX_TERMINAL_G):
            cap_source = f"profile.terminal_growth_cap ({profile.terminal_growth_cap*100:.1f}%)"
        # Use logging.info — visible in uvicorn output without polluting
        # the per-scenario data structures. Stdlib `warnings` module is
        # already imported above for filterwarnings(); we deliberately
        # avoid that path because it's about deprecation/runtime warnings.
        import logging as _logging
        _logging.getLogger(__name__).info(
            "[%s] terminal_g %.2f%% capped to %.2f%% by %s. "
            "Scenario differentiation comes from explicit-period assumptions.",
            scenario_name, raw_g * 100, terminal_g * 100, cap_source,
        )

    # ── Explicit-period assumptions per scenario ─────────────────────────
    #
    # Same pattern as terminal_g above: compute bull/base/bear via relative
    # spread from the base value, then apply uniform absolute bounds. The
    # previous code applied caps asymmetrically (e.g., bull y1_5 capped at
    # 45% but base uncapped, bull WACC floored at 6% but base unfloored)
    # which inverted bull/base/bear ordering whenever a cap or floor bound
    # the bull side. Correctness rule: bull bounds and base bounds must
    # match, otherwise scenario ordering can flip.

    # Y1-5 revenue growth
    base_y1_5_raw = hist_revenue_cagr * cagr_y1_5_factor
    bull_y1_5_raw = base_y1_5_raw * (1.0 + profile.bull_growth_haircut)
    bear_y1_5_raw = max(base_y1_5_raw * (1.0 + profile.bear_growth_haircut),
                        0.01)
    y1_5_raw = {"bull": bull_y1_5_raw, "base": base_y1_5_raw, "bear": bear_y1_5_raw}[scenario_name]
    y1_5 = min(y1_5_raw, 0.45)   # uniform cap, applies to all scenarios

    # Y6-10 revenue growth
    base_y6_10_raw = hist_revenue_cagr * decay_base * cagr_y6_10_factor
    bull_y6_10_raw = hist_revenue_cagr * decay_bull * cagr_y6_10_factor
    bear_y6_10_raw = max(hist_revenue_cagr * decay_bear * cagr_y6_10_factor,
                         0.005)
    y6_10_raw = {"bull": bull_y6_10_raw, "base": base_y6_10_raw, "bear": bear_y6_10_raw}[scenario_name]
    y6_10 = min(y6_10_raw, 0.25)   # uniform cap

    # Terminal EBIT margin
    base_margin_raw = ebit_margin * margin_rev_base
    bull_margin_raw = ebit_margin * margin_rev_bull
    bear_margin_raw = ebit_margin * margin_rev_bear
    margin_raw = {"bull": bull_margin_raw, "base": base_margin_raw, "bear": bear_margin_raw}[scenario_name]
    ebit_margin_term = min(margin_raw, 0.65)   # uniform cap (65% terminal margin ceiling)

    # CapEx % revenue
    capex_pct_rev = {"bull": capex_pct * 0.90,
                     "base": capex_pct,
                     "bear": capex_pct * 1.15}[scenario_name]

    # WACC: relative spread + uniform [0.06, 0.16] bounds.
    # The 6% floor and 16% ceiling exist to prevent implausible discount
    # rates from blowing up terminal value. Applied uniformly so bull WACC
    # < base WACC < bear WACC ordering is preserved across edge cases
    # (low-WACC tickers like KO previously had bull floored above base).
    base_wacc_raw = wacc_base
    bull_wacc_raw = base_wacc_raw + profile.bull_wacc_adjustment
    bear_wacc_raw = base_wacc_raw + profile.bear_wacc_adjustment
    wacc_raw = {"bull": bull_wacc_raw, "base": base_wacc_raw, "bear": bear_wacc_raw}[scenario_name]
    wacc = min(max(wacc_raw, 0.06), 0.16)

    # Phase 3 — direct calc-input overrides from ScenarioOverride. Routed
    # through ValuationProfile by scenario_eval_node._clone_profile_with_overrides.
    # When set, these win over lifecycle-derived computation.
    if profile.terminal_ebit_margin_override is not None:
        ebit_margin_term = profile.terminal_ebit_margin_override
    if profile.capex_pct_revenue_override is not None:
        capex_pct_rev = profile.capex_pct_revenue_override
    if profile.discount_rate_override is not None:
        wacc = profile.discount_rate_override
    # Phase 3.5 — Y6-10 CAGR override (closes the long-standing routing gap).
    # Applies to all three scenarios identically; analysts wanting different
    # Y6-10 values per scenario should construct three separate scenarios.
    if getattr(profile, "revenue_growth_y6_10_override", None) is not None:
        y6_10 = profile.revenue_growth_y6_10_override
    roic_override = getattr(profile, "terminal_roic_override", None)

    # Numerical guardrail: terminal value formula divides by (WACC − g);
    # when historical Rf was near zero (2020-2021), software sub-profile g=5%
    # combined with WACC ~6% produces a 1% spread that explodes the terminal
    # value. Enforce a minimum 200bps spread by lowering terminal_g when
    # needed. This is a robustness fix, not a methodology change — at
    # WACC < 7% with high-g lifecycles, the math is meaningless without it.
    MIN_TERMINAL_SPREAD = 0.02
    if (wacc - terminal_g) < MIN_TERMINAL_SPREAD:
        terminal_g = max(0.0, wacc - MIN_TERMINAL_SPREAD)

    # Compute array of explicit growth rates
    growth_rates = []
    for i in range(1, forecast_years + 1):
        if i <= 5:
            growth_rates.append(y1_5)
        elif i <= 10:
            growth_rates.append(y6_10)
        else:
            # Interpolate smoothly from y6_10 to terminal_g
            progress = (i - 10) / (forecast_years - 10)
            interp_g = y6_10 - (y6_10 - terminal_g) * progress
            growth_rates.append(interp_g)

    return ScenarioAssumptions(
        name=scenario_name,
        revenue_growth_rates=growth_rates,
        revenue_cagr_y1_5=y1_5,
        revenue_cagr_y6_10=y6_10,
        ebit_margin_current=ebit_margin,
        ebit_margin_terminal=ebit_margin_term,
        capex_pct_revenue=capex_pct_rev,
        da_pct_revenue=da_pct,
        nwc_pct_revenue=nwc_pct if scenario_name != "bear" else nwc_pct * 1.10,
        wacc=wacc,
        terminal_growth=terminal_g,
        tax_rate=tax_rate if scenario_name != "bear" else min(tax_rate + 0.03, 0.30),
        base_roic=roic,
        terminal_roic_override=roic_override,
        justification=f"{scenario_name.capitalize()}: {y1_5:.1%} Y1-5 CAGR, margin {ebit_margin_term/ebit_margin if ebit_margin > 0 else 1.0:.2f}x, g={terminal_g:.1%}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Projection engine
# ─────────────────────────────────────────────────────────────────────────────

def _project_scenario(
    assumptions: ScenarioAssumptions,
    base_revenue: float,
    base_roic: float,
    base_da: float,
    base_capex: float,
    base_nwc: float,
    latest_fy: int,
    forecast_years: int = 10,
) -> Tuple[List[YearProjection], TerminalValue, float]:
    """
    Project cash flows for one scenario using the NorthWestern CFA formula.

    CFA = NOPAT + D&A - CapEx - ΔNWC

    Returns:
        (projections, terminal_value, enterprise_value)
    """
    wacc = assumptions.wacc
    g = assumptions.terminal_growth
    tax = assumptions.tax_rate

    effective_roic = assumptions.terminal_roic
    terminal_nopat_margin = assumptions.ebit_margin_terminal * (1 - tax)

    # Terminal Reinvestment (as % of revenue) = (g / ROIC) * NOPAT Margin
    terminal_reinvest_margin = terminal_nopat_margin * (g / effective_roic)
    
    # Terminal CapEx = D&A + Terminal Reinvestment - Terminal ΔNWC
    # ΔNWC as % of revenue = g * nwc_pct_revenue
    terminal_capex_pct = terminal_reinvest_margin + assumptions.da_pct_revenue - (g * assumptions.nwc_pct_revenue)
    terminal_capex_pct = max(terminal_capex_pct, assumptions.da_pct_revenue * 0.5) # Floor to partial maintenance

    projections = []
    cumulative_pv = 0.0
    prev_nwc = base_nwc

    for yr in range(1, forecast_years + 1):
        # Revenue growth — pulled from pre-calculated array (handles 15yr interpolation)
        # Note: yr is 1-indexed, so we use yr - 1 for array access
        if len(assumptions.revenue_growth_rates) >= yr:
            cagr = assumptions.revenue_growth_rates[yr - 1]
        else:
            # Fallback in case of mismatch
            cagr = assumptions.terminal_growth

        if yr == 1:
            revenue = base_revenue * (1 + cagr)
        else:
            revenue = projections[-1].revenue * (1 + cagr)

        # EBIT margin — linear interpolation from current to terminal
        margin_progression = yr / forecast_years
        ebit_margin = (
            assumptions.ebit_margin_current * (1 - margin_progression) +
            assumptions.ebit_margin_terminal * margin_progression
        )

        ebit = revenue * ebit_margin
        tax_expense = ebit * tax
        nopat = ebit * (1 - tax)

        # Reinvestment (NorthWestern CFA formula components)
        da = revenue * assumptions.da_pct_revenue
        
        # Gradual convergence of CapEx from current intensity to terminal intensity
        progression = yr / forecast_years
        current_capex_pct = assumptions.capex_pct_revenue * (1 - progression) + terminal_capex_pct * progression
        capex = revenue * current_capex_pct
        
        nwc = revenue * assumptions.nwc_pct_revenue
        delta_nwc = nwc - prev_nwc
        prev_nwc = nwc

        # FCFF = NOPAT + D&A − CapEx − ΔNWC
        # Routes through aletheia.calculations.rollforward.fcf_pathway_b
        # — the same L3 primitive identity_checks uses for audit-side
        # reconciliation. Both audit and projection share the same
        # arithmetic, so the formula is named once and used twice.
        fcff = _rf_fcf_b(
            nopat=nopat, da=da, capex=capex, delta_nwc=delta_nwc,
        )

        # Discount factor
        discount = (1 + wacc) ** yr
        pv_fcff = fcff / discount
        cumulative_pv += pv_fcff

        projections.append(YearProjection(
            year=yr,
            fiscal_year=latest_fy + yr,
            revenue=revenue,
            ebit_margin=ebit_margin,
            ebit=ebit,
            tax_expense=tax_expense,
            nopat=nopat,
            da=da,
            capex=capex,
            delta_nwc=delta_nwc,
            fcff=fcff,
            pv_fcff=pv_fcff,
            cumulative_pv=cumulative_pv,
        ))

    # Terminal value — both methods
    final = projections[-1]
    final_nopat = final.nopat
    final_ebitda = final.ebit + final.da

    # Framework-explicit precondition for perpetuity: wacc - g must be
    # a meaningful positive spread. Below 1% spread the perpetuity
    # becomes near-singular and TV blows up. The legacy `if wacc > g`
    # guard at the strict singularity is below; this log surfaces the
    # near-singular range so receipts capture which scenarios fell back
    # to the FCF×20 form.
    if (wacc - g) <= 0.01:
        import logging
        logging.getLogger(__name__).info(
            "terminal_value_wacc_g_spread_below_safe scenario=%s wacc=%.4f g=%.4f spread=%.4f",
            assumptions.scenario_name, wacc, g, wacc - g,
        )

    # Method 1: Gordon Growth TV = FCF_final × (1+g) / (WACC - g)
    if wacc > g:
        gordon_tv = final.fcff * (1 + g) / (wacc - g)
    else:
        gordon_tv = final.fcff * 20   # Fallback if wacc ≤ g

    # Method 2: NorthWestern reinvestment-rate TV = NOPAT × (1 - g/ROIC) / (WACC - g)
    # Single source of truth for the ROIC floor: assumptions.terminal_roic
    effective_roic = assumptions.terminal_roic
    if wacc > g and effective_roic > g:
        reinvest_tv = final_nopat * (1 - g / effective_roic) / (wacc - g)
    else:
        reinvest_tv = gordon_tv

    # Use reinvestment-rate TV (preferred — NorthWestern) if it is within 50% of Gordon
    # or if Gordon TV goes negative (fallback logic).
    if (gordon_tv > 0 and abs(reinvest_tv - gordon_tv) / gordon_tv < 0.50) or (gordon_tv < 0 and reinvest_tv > 0):
        tv_used = reinvest_tv
    else:
        tv_used = gordon_tv   # Fall back to Gordon if large divergence

    # Present value of terminal value
    pv_tv = tv_used / (1 + wacc) ** forecast_years

    enterprise_value = cumulative_pv + pv_tv

    # Implied terminal multiple for sanity check
    if final_ebitda > 0:
        implied_tv_multiple = tv_used / final_ebitda
    else:
        implied_tv_multiple = 0.0

    tv_pct = pv_tv / enterprise_value if enterprise_value > 0 else 0.0

    # Floor enterprise value at zero — negative EV is economically undefined
    # Can occur in bear case for low-margin companies (CNC, utilities) where
    # high stress WACC overwhelms thin FCF generation
    if enterprise_value < 0:
        enterprise_value = 0.0
        pv_tv = max(pv_tv, 0.0)
        tv_pct = 0.0

    terminal = TerminalValue(
        gordon_tv=gordon_tv,
        reinvestment_tv=reinvest_tv,
        tv_used=tv_used,
        pv_tv=pv_tv,
        implied_tv_ebitda_multiple=implied_tv_multiple,
        tv_pct_of_ev=tv_pct,
    )

    return projections, terminal, enterprise_value


def _organic_cagr_ex_breaks(df_fy, lookback_years: int = 8):
    """Detect outlier YoY revenue jumps (transformative M&A / regime breaks)
    and return ``(organic_cagr, [break_fiscal_years])``.

    A single YoY above ``max(40%, 2.0× the trailing median YoY)`` is treated
    as a structural break — e.g. AVGO's VMware acquisition (FY2024 +44%) —
    because such a jump inflates EVERY lookback window, so the
    longest-vs-shortest M&A heuristic in ``run()`` can't catch a *recent*
    deal. The organic CAGR is the geometric mean of the remaining (non-break)
    YoY factors. Returns ``(None, [])`` when there's too little history or no
    break is detected, so the caller keeps its existing CAGR.

    Scoped to the last ``lookback_years`` fiscal years: the purpose is to clean
    the FORWARD growth reference, so only forecast-relevant recent history
    matters. Without this scope, a company's early-stage organic hypergrowth
    (e.g. CRM growing 50-80%/yr in FY2004-2007) is wrongly flagged as "M&A",
    and the geomean of the remaining (still-high early-growth) years is adopted
    as the forward CAGR — CRM was forecast at 26% vs ~14% actual recent growth.
    """
    if df_fy is None or "clean_Revenue" not in getattr(df_fy, "columns", []):
        return None, []
    d = df_fy.dropna(subset=["clean_Revenue"]).sort_values("fiscal_year")
    revs = [float(x) for x in d["clean_Revenue"].tolist()]
    years = [int(x) for x in d["fiscal_year"].tolist()]
    if len(revs) < 4:
        return None, []
    # Restrict to the recent, forecast-relevant window (keep one extra year so
    # the first YoY in the window has a base).
    if lookback_years and len(revs) > lookback_years + 1:
        revs = revs[-(lookback_years + 1):]
        years = years[-(lookback_years + 1):]
    yoy = [
        (years[i], revs[i] / revs[i - 1] - 1.0)
        for i in range(1, len(revs)) if revs[i - 1] > 0
    ]
    if len(yoy) < 3:
        return None, []
    med = float(np.median([g for _, g in yoy]))
    threshold = max(0.40, 2.0 * med) if med > 0 else 0.40
    break_years = [y for y, g in yoy if g > threshold]
    if not break_years:
        return None, []
    organic = [g for y, g in yoy if y not in break_years]
    if len(organic) < 2:
        return None, []
    prod = 1.0
    for g in organic:
        prod *= (1.0 + g)
    return prod ** (1.0 / len(organic)) - 1.0, break_years


# ─────────────────────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────────────────────

class DCFEngine:
    """
    Reads from InvestmentDatabase and runs three-scenario DCF.
    No manual inputs required — all assumptions derived from clean data.
    """
    
    # Note: This contract is dynamically consumed by test_structured_ingestion.py
    REQUIRED_CLEAN_FIELDS = [
        "Revenue", "OperatingIncome", "Depreciation_Total", "CapEx", "TotalAssets", "TotalEquity"
    ]

    def __init__(
        self,
        db_path: str = "valuation_data/database/investment.duckdb",
        forecast_years: int = 10,
        mrp: Optional[float] = None,
        verbose: bool = True,
        as_of_date: Optional[datetime.date] = None,
        risk_free_rate: Optional[float] = None,
        beta: Optional[float] = None,
    ):
        """
        WACC inputs:
          - If `as_of_date` is provided, historical-macro lookups fill any
            missing rf/mrp/beta. Used by the backtest harness.
          - If `as_of_date` is None (production path), missing rf/mrp/beta
            fall through to live market_data lookups (today's values).
          - Explicit kwargs always win over both lookups.
        """
        self.db_path = db_path
        self.forecast_years = forecast_years
        self.verbose = verbose
        self._as_of_date = as_of_date
        self._rf_override = risk_free_rate
        self._beta_override = beta
        # Resolve MRP at construction so callers see a stable value
        if mrp is not None:
            self.mrp = mrp
        elif as_of_date is not None:
            from aletheia.data.historical_macro import get_equity_risk_premium
            self.mrp = get_equity_risk_premium(as_of_date)
        else:
            self.mrp = MARKET_RISK_PREMIUM

    def run(self, calc_input: 'CalculationInput', fiscal_year: Optional[int] = None) -> DCFResult:
        """
        Run full three-scenario DCF for a ticker.

        Args:
            calc_input: CalculationInput containing dataframe, classification, valuation_profile, and known_issues
            fiscal_year: specific year to value (defaults to latest)

        Returns:
            DCFResult with bull/base/bear scenarios
        """
        max_historical_cagr = calc_input.valuation_profile.max_historical_cagr
        from aletheia.data.exceptions import MissingFieldError

        ticker = calc_input.classification.ticker
        meta = calc_input.classification
        lifecycle = meta.lifecycle
        profile = calc_input.valuation_profile
        
        if meta and meta.business_model in ["ddm_required", "embedded_value_required", "routing_required", "reit_required", "mlp_required", "residual_income_required"]:
            raise NotImplementedError(f"DCFEngine: ticker {ticker} requires specialized model ({meta.business_model}) — see KNOWN_ISSUES")

        # Honor `bypass` workarounds from KNOWN_ISSUES — declared data gaps that
        # cannot be patched at the resolver layer (e.g. V's missing diluted-share
        # XBRL facts). Treated as a typed skip, not a hard error.
        for issue in calc_input.known_issues:
            if issue.workaround == "bypass":
                raise NotImplementedError(
                    f"DCFEngine: ticker {ticker} bypassed via KNOWN_ISSUES "
                    f"({issue.issue_type}: {issue.field or 'general'})"
                )

        result = DCFResult(ticker=ticker, fiscal_year=fiscal_year or 0)

        if self.verbose:
            print(f"\n{'='*60}")
            print(f"  DCF Engine: {ticker}")
            print(f"{'='*60}")

        # ── Step 1: Extract DataFrame ───────────────────────────
        df = calc_input.df

        if df.empty:
            result.errors.append(f"No data in dataframe for {ticker}")
            return result

        # ── Phase Q-5: split FY rows from TTM ───────────────────────
        # Base row is the TTM if present (most current snapshot, anchors
        # IPS to the latest 10-Q); otherwise the latest FY. Historical
        # CAGR + trend math always operate on the FY-only frame so
        # quarterly seasonality doesn't leak into long-run growth rates.
        if "period" in df.columns:
            df_fy  = df[df["period"] == "FY"].copy()
            df_ttm = df[df["period"] == "TTM"].copy()
        else:
            df_fy  = df
            df_ttm = df.iloc[0:0]

        # Use requested year or latest year with reported Revenue
        # (avoids FY+1 stub rows where Revenue is NaN). FY-frame only.
        df_with_rev = df_fy.dropna(subset=["clean_Revenue"]) if "clean_Revenue" in df_fy.columns else df_fy
        if df_fy.empty and df_with_rev.empty:
            result.errors.append(f"No FY data in dataframe for {ticker}")
            return result

        if fiscal_year:
            year_df = df_fy[df_fy["fiscal_year"] == fiscal_year]
            if year_df.empty:
                fiscal_year = int(df_with_rev["fiscal_year"].max() if not df_with_rev.empty else df_fy["fiscal_year"].max())
                year_df = df_fy[df_fy["fiscal_year"] == fiscal_year]
        else:
            fiscal_year = int(df_with_rev["fiscal_year"].max() if not df_with_rev.empty else df_fy["fiscal_year"].max())
            year_df = df_fy[df_fy["fiscal_year"] == fiscal_year]

        latest_fy_row = year_df.iloc[0]

        # Pick TTM as base when available; FY otherwise. When TTM is
        # base, build a merged row: TTM values take precedence on flow +
        # ratio fields, but cleaning-engine-only fields (clean_NormalizedEBIT,
        # domain_score_*, etc.) that the TTM derivation doesn't compute
        # fall back to the latest FY row. This keeps the DCF engine's
        # field requirements satisfied without polluting `latest` with
        # stale values for fields TTM legitimately replaces.
        if not df_ttm.empty:
            ttm_row = df_ttm.iloc[-1]
            latest = _merge_ttm_with_fy_fallback(ttm_row, latest_fy_row)
            result.base_period = "TTM"
            result.base_period_end_date = (
                str(ttm_row.get("period_end_date") or "")[:10] or None
            )
            result.fy_fiscal_year = fiscal_year
            # `fiscal_year` on the result reflects the base row's stamped
            # year (TTM rows carry the latest contributing quarter's FY).
            result.fiscal_year = int(ttm_row.get("fiscal_year") or fiscal_year)
        else:
            latest = latest_fy_row
            result.base_period = "FY"
            result.base_period_end_date = (
                str(latest.get("period_end_date") or "")[:10] or None
            )
            result.fy_fiscal_year = fiscal_year
            result.fiscal_year = fiscal_year

        # ── Step 2: Extract base financials ──────────────────────────────────
        def get(col, fallback=None):
            val = latest.get(col)
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                return float(val)
            return fallback
            
        def get_with_provenance(field: str) -> Tuple[Optional[float], str]:
            raw_val = get(f"raw_{field}")
            if raw_val is not None:
                return raw_val, "raw"
            
            derived_val = get(f"derived_{field}")
            if derived_val is not None:
                return derived_val, "derived"
                
            return None, "missing"

        revenue = get("clean_Revenue")
        if revenue is None:
            raise MissingFieldError(f"Missing required field 'Revenue' for {ticker}")

        # Ex-impairment OpInc preference. Phase 5 (hybrid-XBRL specialty
        # tags) populates ``clean_OperatingIncome_ExUnusual`` from
        # discrete impairment / restructuring tags. When present we use
        # it as base-year EBIT so years with one-time non-recurring
        # charges (e.g., LDOS FY2023 Dynetics goodwill writedown) don't
        # depress the base case and inflate the implied recovery in
        # bull / base projections. Falls back to raw OpInc when the
        # ex-unusual variant is missing.
        ebit_exu = get("clean_OperatingIncome_ExUnusual")
        if ebit_exu is not None and ebit_exu > 0:
            ebit, ebit_prov = ebit_exu, "ex_unusual"
        else:
            ebit, ebit_prov = get_with_provenance("OperatingIncome")
        if ebit_prov == "missing":
            raise MissingFieldError(f"Missing required field 'OperatingIncome' for {ticker}")

        ebitda_exu = get("clean_EBITDA_ExUnusual")
        if ebitda_exu is not None and ebitda_exu > 0:
            ebitda, ebitda_prov = ebitda_exu, "ex_unusual"
        else:
            ebitda, ebitda_prov = get_with_provenance("EBITDA")
        if ebitda_prov == "missing": ebitda = ebit
        
        nopat = get("clean_NOPAT", ebit * 0.79)
        roic = get("derived_ROIC", 0.12)
        fcf = get("derived_FCF", 0.0)
        net_debt = get("derived_NetDebt", 0.0)
        invested_capital = get("derived_InvestedCapital", 0.0)
        tax_rate, tax_rate_source = resolve_tax_rate(
            ticker=ticker, fn="dcf_engine.run",
            df=df_fy, fy=fiscal_year,
            cash_tax_rate=get("clean_CashTaxRate"),
            gaap_tax_rate=get("clean_GAAP_TaxRate"),
        )
        # Phase 3 — analyst tax_rate override from ScenarioOverride
        if profile.tax_rate_override is not None:
            tax_rate = profile.tax_rate_override
            tax_rate_source = "analyst_override"
        result.tax_rate_source = tax_rate_source

        # Tier-3 input range check on tax_rate. Defense-in-depth: the
        # resolver enforces ``_is_usable_rate`` on every input, but
        # malformed rates that survive (e.g. an analyst override out of
        # band) get caught here before they enter compute_wacc and the
        # scenario projection.
        tax_min, tax_max = RANGE_BOUNDS["tax_rate"]
        try:
            _require_range(
                tax_rate, min=tax_min, max=tax_max,
                field_name="tax_rate", ticker=ticker, fn="dcf_engine.run",
                note="rates outside [-1, 1] are upstream errors",
            )
        except CalculationError as exc:
            result.errors.append(str(exc))
            if _guard_mode() == "hard":
                raise
            return result
        long_term_debt = get("raw_LongTermDebt", 0.0)
        total_equity_book = get("raw_TotalEquity", 0.0)

        da, da_prov = get_with_provenance("Depreciation_Total")
        capex, capex_prov = get_with_provenance("CapEx")

        if da_prov == "missing" or capex_prov == "missing":
            print(f"[ERROR] DCFEngine missing critical inputs for {ticker}")
            raise MissingFieldError(
                f"DCFEngine: missing D&A or CapEx for {ticker}. "
                f"da_prov={da_prov}, capex_prov={capex_prov}. "
            )

        nwc = revenue * 0.03   # Structural NWC estimate

        # CapEx intensity — smoothed via 5-year rolling average rather than
        # latest single year. Reduces sensitivity to peak-cycle CapEx (e.g.
        # MSFT FY2025 22.9% Azure data-center buildout) being misinterpreted
        # as steady-state. Falls back to single-year if <3y of history.
        # df_fy (not df) — capex smoothing must trace annual cycles,
        # not pick up a TTM row that would weight the latest period 2x.
        capex_pct = _compute_smoothed_capex_pct(df_fy, revenue, capex)
        da_pct = da / revenue if revenue > 0 else 0.03
        nwc_pct = 0.03

        # Historical revenue CAGR — Phase 4.5 Exact-Day Math
        # Uses exact period_end_date differences instead of integer fiscal_years.
        # Tracks data integrity (n_years_used vs n_years_attempted) to drop suspect records.
        
        # Phase Q-5: historical CAGR + trend math operates on FY-only
        # rows so quarterly seasonality doesn't leak into long-run growth.
        # The base-year inputs may come from a TTM row, but the trend
        # ladder must trace year-over-year for the forecast to be sane.
        if "period_end_date_missing" in df_fy.columns:
            valid_df = df_fy[~df_fy["period_end_date_missing"].astype(bool)]
        else:
            valid_df = df_fy
        excluded = len(df_fy) - len(valid_df)
        if excluded > 0 and self.verbose:
            print(f"  [WARN] Excluded {excluded} records with missing period_end_date from CAGR history.")

        hist_revenues_df = valid_df[valid_df["fiscal_year"] <= fiscal_year].sort_values("fiscal_year").dropna(subset=["clean_Revenue"])
        
        cagr_candidates = []
        if not hist_revenues_df.empty:
            row_now = hist_revenues_df.iloc[-1]
            rev_now = float(row_now["clean_Revenue"])
            date_now_str = row_now.get("period_end_date")
            
            if pd.notna(date_now_str) and rev_now > 0:
                try:
                    date_now = pd.to_datetime(date_now_str)
                    
                    for lookback in [3, 5, 7, 10]:
                        n_years_attempted = lookback
                        n_years_used = 0
                        rev_past = None
                        date_past = None
                        
                        # Need lookback+1 records to span an N-year window
                        # (endpoints + intermediate years).
                        if len(hist_revenues_df) > lookback:
                            target_row = hist_revenues_df.iloc[-lookback - 1]
                            window = hist_revenues_df.iloc[-lookback - 1:]
                            # Density: how many records in the window have valid dates,
                            # measured as a fraction of expected records.
                            n_valid = int(window["period_end_date"].notna().sum())
                            n_years_used = n_valid - 1   # exclude endpoint duplicate
                            
                            rev_past_val = target_row["clean_Revenue"]
                            date_past_str = target_row.get("period_end_date")
                            
                            if pd.notna(date_past_str) and float(rev_past_val) > 0:
                                rev_past = float(rev_past_val)
                                date_past = pd.to_datetime(date_past_str)
                                
                                days_between = (date_now - date_past).days
                                if days_between > 0:
                                    cagr = (rev_now / rev_past) ** (365.25 / days_between) - 1
                                    cagr_candidates.append((cagr, n_years_used, n_years_attempted, lookback))
                except Exception as e:
                    if self.verbose:
                        print(f"  [WARN] Failed exact-date CAGR parsing: {e}")

        forecast_years_applied = profile.forecast_years

        # Select the longest lookback that meets the 50% data integrity
        # threshold, BUT detect M&A structural breaks. When a longer
        # lookback's CAGR is >1.5× a shorter lookback's CAGR, the longer
        # window likely spans a transformative acquisition (LDOS / IS&GS
        # merger 2016 doubled the company; long-window CAGR captures the
        # mechanical doubling rather than organic growth). In that case
        # we prefer the shorter window — the post-break organic rate.
        valid_cagrs = [c for c in cagr_candidates if c[1] >= c[2] * 0.5]

        if valid_cagrs:
            # Sort by lookback period (longest first).
            valid_cagrs.sort(key=lambda x: x[3], reverse=True)
            selected = valid_cagrs[0]

            # M&A break heuristic: compare longest-window CAGR to the
            # 3-5Y CAGR. If 3-5Y is materially lower (<= 60% of long),
            # the long window probably includes a step-change acquisition.
            # Prefer the shortest valid window in that case so the base
            # year reflects organic post-merger growth.
            short_candidates = [c for c in valid_cagrs if 3 <= c[3] <= 5]
            if short_candidates and selected[3] >= 7:
                # Use the longest in the 3-5Y bucket as the "organic"
                # benchmark.
                short_candidates.sort(key=lambda x: x[3], reverse=True)
                short_ref = short_candidates[0]
                long_cagr = selected[0]
                short_cagr = short_ref[0]
                if (long_cagr > 0 and short_cagr > 0
                        and short_cagr <= long_cagr * 0.6):
                    if self.verbose:
                        print(f"  [M&A break detected] {selected[3]}Y CAGR "
                              f"{long_cagr:.1%} vs {short_ref[3]}Y CAGR "
                              f"{short_cagr:.1%}; using shorter window "
                              "(organic post-break growth).")
                    selected = short_ref

            hist_cagr = selected[0]
            if self.verbose:
                print(f"  Selected {selected[3]}Y Exact-Date CAGR: {hist_cagr:.1%} (Data density: {selected[1]}/{selected[2]})")
        else:
            print(f"  [WARN] Missing or sparse SEC history for {ticker}. Using lifecycle default ({lifecycle}).")
            hist_cagr = profile.growth_rate

        # M&A / regime-break adjustment. A single outlier YoY revenue jump
        # (e.g. AVGO's VMware FY2024 +44%) inflates EVERY lookback window, so
        # the longest-vs-shortest heuristic above can't catch a *recent*
        # transformative deal. Recompute an organic CAGR that chain-links the
        # non-break YoY factors and prefer it when a break is detected. The
        # analyst can still refine via the Y1-5 override (applied below).
        organic_cagr, break_years = _organic_cagr_ex_breaks(df_fy)
        if organic_cagr is not None and break_years:
            if self.verbose:
                print(f"  [M&A/regime break] FY{break_years} excluded; organic "
                      f"CAGR {organic_cagr:.1%} (raw {hist_cagr:.1%})")
            result.warnings.append(
                f"Historical CAGR adjusted for transformative event(s) in "
                f"FY{break_years}: organic {organic_cagr:.1%} vs raw "
                f"{hist_cagr:.1%}. Verify the forward Y1-5 assumption."
            )
            hist_cagr = organic_cagr

        # Scenario-override path: when an agent-proposed scenario provides
        # an explicit Y1-5 CAGR, it must replace the historical reference
        # entirely. Without this, the override only ever takes effect on
        # tickers with sparse SEC data (~never), producing identical-to-
        # base IPS for every scenario and inverting bull/bear semantics
        # in the Scenarios tab UI. Phase 3.6 fix.
        if getattr(profile, "revenue_growth_y1_5_override", None) is not None:
            hist_cagr = float(profile.revenue_growth_y1_5_override)
            if self.verbose:
                print(f"  [SCENARIO OVERRIDE] Y1-5 CAGR set to {hist_cagr:.1%} "
                      f"(replaces SEC-derived hist_cagr).")

        hist_cagr = float(np.clip(hist_cagr, 0.01, max_historical_cagr))

        if self.verbose:
            cagr_str = ", ".join(
                f"{c[0]:.1%}" for c in cagr_candidates
            )
            print(f"  CAGR candidates ({len(cagr_candidates)} periods): [{cagr_str}]")
            print(f"  Robust CAGR (trimmed median): {hist_cagr:.1%}")

        result.revenue = revenue
        result.ebitda = ebitda
        result.ebit = ebit
        result.nopat = nopat
        result.roic = roic
        result.fcf = fcf
        result.net_debt = net_debt

        if self.verbose:
            print(f"  Revenue: ${revenue/1e9:.1f}B")
            print(f"  EBIT:    ${ebit/1e9:.1f}B  ({ebit/revenue:.1%} margin)")
            print(f"  ROIC:    {roic:.1%}")
            print(f"  FCF:     ${fcf/1e9:.1f}B")
            print(f"  5Y CAGR: {hist_cagr:.1%}")

        # ── Step 3: Fetch live market data ────────────────────────────────────
        # Diluted share count is required (Section 5 framework). We do NOT
        # silently fall back to current shares_outstanding — that mixes share
        # count bases and inflates per-share IV for SBC-heavy names.
        shares_diluted = get("clean_SharesDiluted") or get("raw_SharesDiluted")
        if not shares_diluted or shares_diluted <= 0:
            raise MissingFieldError(
                f"Missing required field 'SharesDiluted' for {ticker}. "
                f"DCFEngine requires diluted basis; outstanding-share fallback is disabled."
            )

        from aletheia.data.market_data import get_current_price, get_market_cap
        try:
            current_price = get_current_price(ticker)
            market_cap = get_market_cap(ticker)
        except Exception as e:
            result.errors.append(f"market_data fetch failed: {e}")
            current_price = 0.0
            market_cap = 0.0

        result.current_price = current_price
        result.market_cap = market_cap
        result.shares_diluted = shares_diluted

        if self.verbose:
            print(f"  Price:   ${current_price:,.2f}")
            print(f"  Mkt Cap: ${market_cap/1e9:.1f}B")

        # ── Step 4: Compute WACC ──────────────────────────────────────────────
        # Historical-macro path: use injected rf/beta if provided, else look
        # them up from historical_macro by self._as_of_date, else fall back
        # to live market_data (production current-date behavior).
        if self._rf_override is not None:
            rf = self._rf_override
        elif self._as_of_date is not None:
            from aletheia.data.historical_macro import get_risk_free_rate as _hist_rf
            rf = _hist_rf(self._as_of_date)
        else:
            rf = _fetch_risk_free_rate()

        if self._beta_override is not None:
            beta = self._beta_override
        elif self._as_of_date is not None:
            from aletheia.data.historical_macro import compute_historical_beta
            hist_b = compute_historical_beta(ticker, self._as_of_date)
            beta = hist_b if hist_b is not None else _compute_beta(ticker)
        else:
            beta = _compute_beta(ticker)

        # Interest expense proxy — kd from net debt and a spread
        interest_expense = long_term_debt * 0.04   # Rough proxy

        wacc_total_debt = max(net_debt + get("raw_Cash", 0.0), long_term_debt)
        wacc_base, ke, kd, beta = compute_wacc(
            ticker=ticker,
            total_equity=market_cap,
            total_debt=wacc_total_debt,
            interest_expense=interest_expense,
            tax_rate=tax_rate,
            risk_free_rate=rf,
            beta=beta,
            mrp=self.mrp,
        )
        # Expose the EXACT debt figure used in the WACC weights so the §7
        # discount-detail and the cost-of-capital build display the same
        # capital structure the engine actually used (not net-debt vs LTD+STD
        # re-derivations that disagree, e.g. 86/14 vs 91.8/8.2).
        result.wacc_total_debt = float(wacc_total_debt)
        
        result.risk_free_rate = rf
        result.beta = beta
        # β-reference diagnostic (Build 1): β + R² vs ^GSPC and the sector
        # benchmark. Does not touch the WACC the engine just computed.
        try:
            from aletheia.data.market_data import get_beta_diagnostics
            _sector = _sector_of(ticker)
            _bd = get_beta_diagnostics(ticker, sector=_sector)
            result.beta_r2 = _bd.get("r2_gspc")
            result.beta_sector = _bd.get("beta_sector")
            result.beta_sector_r2 = _bd.get("r2_sector")
            result.beta_benchmark = _bd.get("sector_benchmark")
        except Exception:
            pass
        # Honor the analyst WACC override at the headline level too. The
        # per-scenario WACC is overridden inside _build_assumptions, but the
        # top-level wacc_base (reported as `wacc` in the /dcf payload and the
        # Deep Dive hero) was still the CAPM-derived value — so an overridden
        # valuation showed the model WACC there while the IV reflected the
        # override. Pin wacc_base to the override so they're consistent.
        if profile.discount_rate_override is not None:
            wacc_base = profile.discount_rate_override
        result.wacc_base = wacc_base

        # Tier-3 input range check on resolved WACC. compute_wacc clips
        # to [4%, 18%] internally but the framework primitive provides a
        # second-layer guard in case the clip is bypassed or future
        # refactors widen the band.
        wacc_min, wacc_max = RANGE_BOUNDS["wacc"]
        try:
            _require_range(
                wacc_base, min=wacc_min, max=wacc_max,
                field_name="wacc_base", ticker=ticker, fn="dcf_engine.run",
                note="WACC outside [2%, 30%] suggests compute_wacc bug",
            )
        except CalculationError as exc:
            result.errors.append(str(exc))
            if _guard_mode() == "hard":
                raise
            return result

        if self.verbose:
            print(f"  Rf:      {rf:.2%}")
            print(f"  Beta:    {beta:.2f}")
            print(f"  Ke:      {ke:.2%}")
            print(f"  Kd:      {kd:.2%}")
            print(f"  WACC:    {wacc_base:.2%}")

        # ── Step 4.5: Moat-aware terminal-growth enforcement ──────────────────
        # The constitution rule (lead.py) flags terminal_g > 3% with moat < 9
        # as a discipline violation, but historically just warned without
        # enforcement. Apply the cap HERE — before scenarios are built —
        # so the base/bull/bear all respect the moat-implied terminal
        # ceiling. Reads latest moat_strength assessment from DuckDB;
        # falls back to a conservative 2.5% when no assessment yet exists
        # (protects first-ingest runs from inflated terminal multiples).
        try:
            from aletheia.data.database import InvestmentDatabase
            with InvestmentDatabase(verbose=False) as _moat_db:
                _moat_rec = _moat_db.get_latest_assessment(
                    ticker.upper(), "moat_strength",
                )
            _moat_score = _moat_rec.get("score") if _moat_rec else None
        except Exception:
            _moat_score = None

        if _moat_score is not None:
            _moat_score = float(_moat_score)
            if _moat_score >= 6.0:
                _moat_cap = 0.040
            elif _moat_score >= 5.0:
                _moat_cap = 0.030
            elif _moat_score >= 4.0:
                _moat_cap = 0.025
            else:
                _moat_cap = 0.020
        else:
            # First-ingest case: no moat in DB yet. Use a defensive 2.5%
            # so the initial DCF doesn't run with an inflated terminal
            # cap; subsequent re-runs after qualitative ingest will use
            # the assessed score.
            _moat_cap = 0.025

        # Apply as additional terminal_growth_cap (kept alongside any
        # lifecycle-set cap — whichever is tighter wins). ValuationProfile
        # is frozen, so use dataclasses.replace to swap in the new cap.
        import dataclasses as _dc
        _existing_cap = profile.terminal_growth_cap
        _new_cap = (_moat_cap if _existing_cap is None
                    else min(_existing_cap, _moat_cap))
        profile = _dc.replace(profile, terminal_growth_cap=_new_cap)

        if self.verbose:
            _src = f"moat {_moat_score:.1f}" if _moat_score is not None else "no-moat default"
            print(f"  Terminal-g cap: {profile.terminal_growth_cap*100:.2f}% ({_src})")

        # ── Step 5: Build scenarios and project ───────────────────────────────
        for scenario_name in ["bull", "base", "bear"]:
            assumptions = _build_assumptions(
                scenario_name=scenario_name,
                revenue=revenue,
                ebit=ebit,
                roic=roic,
                fcf=fcf,
                capex_pct=capex_pct,
                da_pct=da_pct,
                nwc_pct=nwc_pct,
                tax_rate=tax_rate,
                wacc_base=wacc_base,
                hist_revenue_cagr=hist_cagr,
                profile=profile,
                lifecycle=lifecycle,
            )

            projections, terminal, ev = _project_scenario(
                assumptions=assumptions,
                base_revenue=revenue,
                base_roic=roic,
                base_da=da,
                base_capex=capex,
                base_nwc=nwc,
                latest_fy=fiscal_year,
                forecast_years=forecast_years_applied,
            )

            # Multiple decomposition (NorthWestern formula)
            # EV/EBITDA = NOPATn*(1-g/ROIC)/EBITDA / (WACC-g)
            final_nopat = projections[-1].nopat
            final_ebitda = projections[-1].ebit + projections[-1].da
            g = assumptions.terminal_growth
            w = assumptions.wacc
            effective_roic = assumptions.terminal_roic

            if final_ebitda > 0 and w > g:
                justified_ev_ebitda = (
                    final_nopat * (1 - g / effective_roic) / final_ebitda
                ) / (w - g)
            else:
                justified_ev_ebitda = 0.0

            implied_ev_ebitda = ev / ebitda if ebitda > 0 else 0.0
            implied_ev_ebit = ev / ebit if ebit > 0 else 0.0
            roic_wacc_spread = roic - w

            metadata = {
                "lifecycle_category": lifecycle,
                "growth_default": profile.growth_rate,
                "hist_cagr": hist_cagr,
                "forecast_years": forecast_years_applied,
                "terminal_growth_cap": profile.terminal_growth_cap
            }
            
            ticker_issues = calc_input.known_issues
            if ticker_issues:
                metadata["data_quality_warnings"] = " | ".join([issue.description for issue in ticker_issues])
            if lifecycle == "cyclical_industrial":
                metadata["cyclical_haircut_methodology"] = "projection_growth_penalty (peak-cycle distortion expected)"
                
            scenario_result = ScenarioResult(
                assumptions=assumptions,
                projections=projections,
                terminal=terminal,
                enterprise_value=ev,
                pv_explicit_period=sum(p.pv_fcff for p in projections),
                implied_ev_ebitda=implied_ev_ebitda,
                implied_ev_ebit=implied_ev_ebit,
                justified_ev_ebitda=justified_ev_ebitda,
                roic_wacc_spread=roic_wacc_spread,
                metadata=metadata
            )

            # Sanity checks
            if terminal.tv_pct_of_ev > 0.85:
                scenario_result.warnings.append(
                    f"Terminal value is {terminal.tv_pct_of_ev:.0%} of EV. "
                    f"Check terminal growth assumption ({g:.1%}) and WACC ({w:.1%})."
                )
            if terminal.implied_tv_ebitda_multiple > implied_ev_ebitda * 1.2:
                scenario_result.warnings.append(
                    f"Implied TV multiple ({terminal.implied_tv_ebitda_multiple:.1f}x) "
                    f"exceeds current EV/EBITDA ({implied_ev_ebitda:.1f}x). "
                    f"Terminal assumptions may be too optimistic."
                )

            # Framework output sanity on TV multiple. Plausible band [3, 50]
            # — outside that, TV math has degraded (NOPAT too high, WACC too
            # low, growth too high). Distinct from the soft-warning above
            # (compares to current EV/EBITDA); this checks absolute plaus-
            # ibility regardless of current trading multiple. Honors guard
            # mode: off swallows + warns, hard raises CalculationOutputError.
            tv_mult = terminal.implied_tv_ebitda_multiple
            if tv_mult != 0.0 and not (3.0 <= tv_mult <= 50.0):
                err = CalculationOutputError(
                    f"implied_tv_ebitda_multiple={tv_mult:.1f}x outside "
                    "plausibility band [3, 50]. TV math has likely degraded "
                    "— check WACC-vs-g spread, NOPAT magnitude, growth.",
                    ticker=ticker, fn=f"dcf_engine.run({scenario_name})",
                    field="implied_tv_ebitda_multiple",
                    value=float(tv_mult), expected="[3, 50]",
                )
                if _guard_mode() == "hard":
                    raise err
                if _guard_mode() in ("shadow", "soft"):
                    import logging
                    logging.getLogger(__name__).warning(
                        "terminal_value output sanity flag: %s", err.to_receipt()
                    )
                scenario_result.warnings.append(str(err))

            setattr(result, scenario_name, scenario_result)

            if self.verbose:
                iv = result.intrinsic_per_share(ev, net_debt)
                upside = result.upside(iv) if iv else None
                print(f"  {scenario_name.upper():4s}: EV=${ev/1e9:.0f}B"
                      f"  IV/share=${iv:,.0f}" if iv else
                      f"  {scenario_name.upper():4s}: EV=${ev/1e9:.0f}B  IV=N/A",
                      end="")
                if upside is not None:
                    print(f"  ({upside:+.1%} vs ${current_price:.0f})", end="")
                print()

        # ── Step 6: Apply Bear Case Structural Floor ────────────────────────────
        # Ensure bear scenario doesn't produce penny-stock values if company
        # has massive net cash reserves. Floor EV at 20% of net cash.
        if result.bear and net_debt < 0:
            net_cash = abs(net_debt)
            floor_ev = net_cash * 0.20
            if result.bear.enterprise_value < floor_ev:
                result.bear.enterprise_value = floor_ev
                result.bear.warnings.append(
                    f"Bear EV floored at 20% of Net Cash (${floor_ev/1e9:.1f}B)"
                )

        # ── Step 7: Apply Earnings Quality Metadata ────────────────────────────
        issues = calc_input.known_issues
        quality_issues = [i for i in issues if i.issue_type == "earnings_quality"]
        if quality_issues:
            result.confidence = "low"
            result.warnings.extend(i.description for i in quality_issues)

        # ── Step 8: Output sanity validation on per-scenario IPS ────────────
        # The MDT-class structural defense applied to IPS: even if every
        # individual input passed validation, a combination producing
        # nonsense (intrinsic_per_share = 0 / NaN / negative / orders-of-
        # magnitude off vs current_price) is itself a signal worth raising.
        # Honors guard mode — off swallows, hard raises.
        price = float(result.current_price or 0.0)
        for scn_name in ("bull", "base", "bear"):
            scn = getattr(result, scn_name, None)
            if scn is None:
                continue
            ips = result.intrinsic_per_share(scn.enterprise_value, result.net_debt)
            if ips is None:
                continue
            ips_f = float(ips)
            # Two checks: (1) finite + non-negative for bull/base (bear can be
            # negative on distressed scenarios but only down to 0), (2) not
            # > 100× current price (silent inflation pattern).
            implausible = False
            reason = ""
            if not (ips_f == ips_f):  # NaN
                implausible, reason = True, "NaN per-share value"
            elif ips_f < 0 and scn_name in ("bull", "base"):
                implausible, reason = True, f"negative IPS in {scn_name} ({ips_f:.2f})"
            elif price > 0 and ips_f > price * 100:
                implausible, reason = True, (
                    f"IPS {ips_f:.2f} is >100x current price {price:.2f} — "
                    "model degraded or unit error"
                )
            elif price > 0 and 0 < ips_f < price * 0.01:
                implausible, reason = True, (
                    f"IPS {ips_f:.2f} is <1% of current price {price:.2f} — "
                    "model degraded or scenario over-pessimistic"
                )
            if implausible:
                err = CalculationOutputError(
                    f"{scn_name} scenario IPS implausible: {reason}",
                    ticker=ticker, fn="dcf_engine.run",
                    field=f"{scn_name}.intrinsic_per_share",
                    value=ips_f, expected=f"plausible vs current_price={price:.2f}",
                )
                if _guard_mode() == "hard":
                    raise err
                if _guard_mode() in ("shadow", "soft"):
                    import logging
                    logging.getLogger(__name__).warning(
                        "dcf_engine output sanity flag: %s", err.to_receipt()
                    )
                result.errors.append(str(err))

        return result


# CLI entrypoint removed to avoid architectural violations (no config imports allowed here).
# Use scratch/run_valuation_phase.py instead.
