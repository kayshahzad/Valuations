"""
aletheia/tools/multiple_decomposition.py

Phase 2 — Multiple Decomposition
==================================
Implements the Liberti formula to mathematically justify EV/EBITDA multiples.

Core formula:
    EV/EBITDA = [ NOPATn × (1 - g/ROIC) / EBITDA ] / (WACC - g)

This decomposes any multiple into three fundamental drivers:
  1. Cash conversion: NOPAT × (1 - g/ROIC) / EBITDA
     → How efficiently EBITDA converts to free cash flow after reinvestment
  2. Risk (WACC): the denominator
     → Higher risk = lower multiple
  3. Value-added growth (g when ROIC > WACC)
     → Growth only creates value when ROIC exceeds cost of capital

Also computes:
  - P/Sales formula: P/Sales = [(1+g)/(r-g)] × (1-reinvestment) × profit margin
  - PEG ratio analysis
  - Comparable universe multiple comparison

Usage:
    from aletheia.tools.multiple_decomposition import MultipleDecomposition
    md = MultipleDecomposition()
    result = md.run("TICKER")
    print(result.summary())
"""

import warnings
from dataclasses import dataclass, field
from typing import Optional, List, Dict

import numpy as np

from aletheia.calculations import (
    _require_range,
    RANGE_BOUNDS,
    CalculationError,
    _guard_mode,
    resolve_tax_rate,
)

warnings.filterwarnings("ignore")

from typing import Tuple

def _compute_justified_ev_ebitda(
    nopat: float, ebitda: float, roic: float, wacc: float, g_terminal: float
) -> Tuple[float, float]:
    """
    Compute justified EV/EBITDA and cash conversion ratio using the Liberti formula.
    EV/EBITDA = [ NOPAT * (1 - g/ROIC) / EBITDA ] / (WACC - g)
    Returns: (justified_ev_ebitda, cash_conversion_ratio)
    """
    effective_roic = max(roic, 0.08)
    if ebitda > 0 and wacc > g_terminal:
        cash_conv = nopat * (1 - g_terminal / effective_roic) / ebitda
        justified_ev_ebitda = max(cash_conv / (wacc - g_terminal), 0.0)
        return justified_ev_ebitda, cash_conv
    return 0.0, 0.0


# ─────────────────────────────────────────────────────────────────────────────
# Sector peer reference multiples (Damodaran approximations, large-cap)
# Used when live comparable data is not available
# ─────────────────────────────────────────────────────────────────────────────

SECTOR_MEDIAN_MULTIPLES = {
    "Technology":          {"ev_ebitda": 22, "ev_ebit": 28, "p_sales": 5.0, "p_e": 32},
    "Software":            {"ev_ebitda": 30, "ev_ebit": 38, "p_sales": 8.0, "p_e": 45},
    "Semiconductors":      {"ev_ebitda": 20, "ev_ebit": 25, "p_sales": 6.0, "p_e": 30},
    "Healthcare":          {"ev_ebitda": 14, "ev_ebit": 18, "p_sales": 2.5, "p_e": 22},
    "Healthcare Plans":    {"ev_ebitda": 8,  "ev_ebit": 10, "p_sales": 0.4, "p_e": 14},
    "Consumer Cyclical":   {"ev_ebitda": 12, "ev_ebit": 15, "p_sales": 1.5, "p_e": 20},
    "Auto Manufacturers":  {"ev_ebitda": 8,  "ev_ebit": 10, "p_sales": 0.6, "p_e": 12},
    "Internet":            {"ev_ebitda": 20, "ev_ebit": 25, "p_sales": 5.0, "p_e": 28},
    "Financial":           {"ev_ebitda": 10, "ev_ebit": 12, "p_sales": 2.0, "p_e": 15},
    "Default":             {"ev_ebitda": 12, "ev_ebit": 15, "p_sales": 2.0, "p_e": 20},
}


@dataclass
class MultipleDriver:
    """One component of the multiple decomposition."""
    name: str
    value: float
    interpretation: str


@dataclass
class MultipleResult:
    """Full multiple decomposition result for one company."""
    ticker: str
    fiscal_year: int

    # Actual market multiples
    market_ev: float = 0.0
    current_price: float = 0.0
    market_ev_ebitda: float = 0.0       # Market EV / DB EBITDA
    market_ev_ebit: float = 0.0
    market_p_sales: float = 0.0         # Market cap / Revenue
    market_p_e: float = 0.0             # Price / Net Income
    market_peg: float = 0.0             # P/E / 5Y EPS CAGR

    # Fundamentals
    revenue: float = 0.0
    ebitda: float = 0.0
    ebit: float = 0.0
    nopat: float = 0.0
    roic: float = 0.0
    wacc: float = 0.0
    growth_rate: float = 0.0            # Historical 5Y CAGR (used as g proxy)
    terminal_growth: float = 0.025
    profit_margin: float = 0.0
    reinvestment_rate: float = 0.0

    # Liberti formula components
    cash_conversion_ratio: float = 0.0  # NOPAT*(1-g/ROIC)/EBITDA
    roic_wacc_spread: float = 0.0
    value_creation: str = ""            # "creating", "destroying", "neutral"

    # Justified multiples (from Liberti formula)
    justified_ev_ebitda: float = 0.0
    justified_ev_ebit: float = 0.0
    justified_p_sales: float = 0.0     # P/Sales with margin driver

    # Multiple premium/discount vs justified
    ev_ebitda_premium: float = 0.0     # Market - Justified (positive = premium)
    ev_ebitda_premium_pct: float = 0.0

    # Sector comparison
    sector: str = "Default"
    sector_median_ev_ebitda: float = 0.0
    vs_sector_premium: float = 0.0

    # Drivers
    drivers: List[MultipleDriver] = field(default_factory=list)

    # P/Sales decomposition (SFM formula)
    p_sales_margin_component: float = 0.0
    p_sales_growth_component: float = 0.0
    p_sales_reinvestment_component: float = 0.0

    # Signal
    signal: str = "neutral"
    signal_reasons: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    tax_rate_source: str = "statutory"  # cash | gaap | company_fy | statutory

    def summary(self) -> str:
        lines = [
            f"MultipleDecomposition: {self.ticker} FY{self.fiscal_year}",
            f"",
            f"  MARKET MULTIPLES",
            f"  EV/EBITDA  : {self.market_ev_ebitda:>6.1f}x  (justified: {self.justified_ev_ebitda:>5.1f}x)  premium: {self.ev_ebitda_premium:+.1f}x ({self.ev_ebitda_premium_pct:+.0%})",
            f"  EV/EBIT    : {self.market_ev_ebit:>6.1f}x  (justified: {self.justified_ev_ebit:>5.1f}x)",
            f"  P/Sales    : {self.market_p_sales:>6.1f}x  (justified: {self.justified_p_sales:>5.1f}x)",
            f"  P/E        : {self.market_p_e:>6.1f}x",
            f"  PEG        : {self.market_peg:>6.2f}x" if self.market_peg else "",
            f"  Sector med : {self.sector_median_ev_ebitda:>6.1f}x  vs sector: {self.vs_sector_premium:+.1f}x",
            f"",
            f"  LIBERTI FORMULA DECOMPOSITION",
            f"  EV/EBITDA = [NOPAT×(1-g/ROIC)/EBITDA] / (WACC-g)",
            f"  ROIC       : {self.roic:>6.1%}",
            f"  WACC       : {self.wacc:>6.1%}",
            f"  ROIC-WACC  : {self.roic_wacc_spread:>+6.1%}  [{self.value_creation}]",
            f"  Growth (g) : {self.growth_rate:>6.1%}",
            f"  Cash conv  : {self.cash_conversion_ratio:>6.3f}  (NOPAT×(1-g/ROIC)/EBITDA)",
            f"",
            f"  P/SALES DECOMPOSITION (SFM)",
            f"  P/Sales = [(1+g)/(r-g)] × (1-reinvest) × profit_margin",
            f"  Margin component    : {self.p_sales_margin_component:>6.3f}",
            f"  Growth component    : {self.p_sales_growth_component:>6.3f}",
            f"  Reinvestment factor : {self.p_sales_reinvestment_component:>6.3f}",
            f"  Justified P/Sales   : {self.justified_p_sales:>6.1f}x",
            f"",
            f"  SIGNAL: {self.signal.upper()}",
        ]
        for reason in self.signal_reasons:
            lines.append(f"    → {reason}")
        for driver in self.drivers:
            lines.append(f"  Driver [{driver.name}={driver.value:.3f}]: {driver.interpretation}")
        lines = [l for l in lines if l]   # Remove blank PEG line if absent
        if self.warnings:
            lines.append("")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "market_ev_ebitda": self.market_ev_ebitda,
            "market_ev_ebit": self.market_ev_ebit,
            "market_p_sales": self.market_p_sales,
            "market_p_e": self.market_p_e,
            "market_peg": self.market_peg,
            "justified_ev_ebitda": self.justified_ev_ebitda,
            "justified_ev_ebit": self.justified_ev_ebit,
            "justified_p_sales": self.justified_p_sales,
            "ev_ebitda_premium": self.ev_ebitda_premium,
            "ev_ebitda_premium_pct": self.ev_ebitda_premium_pct,
            "sector_median_ev_ebitda": self.sector_median_ev_ebitda,
            "vs_sector_premium": self.vs_sector_premium,
            "roic": self.roic,
            "wacc": self.wacc,
            "roic_wacc_spread": self.roic_wacc_spread,
            "value_creation": self.value_creation,
            "cash_conversion_ratio": self.cash_conversion_ratio,
            "growth_rate": self.growth_rate,
            "signal": self.signal,
        }


class MultipleDecomposition:
    """
    Decomposes current trading multiples using the Liberti formula.
    Reads from InvestmentDatabase and fetches live market data.
    """

    def __init__(
        self,
        db_path: str = "valuation_data/database/investment.duckdb",
        terminal_growth: float = 0.025,
        verbose: bool = True,
    ):
        self.db_path = db_path
        self.terminal_growth = terminal_growth
        self.verbose = verbose

    def run(
        self,
        calc_input: 'CalculationInput',
        fiscal_year: Optional[int] = None,
        wacc_override: Optional[float] = None,
        growth_override: Optional[float] = None,
    ) -> MultipleResult:
        """
        Decompose current multiples and compute justified values.

        Args:
            calc_input: CalculationInput containing df and metadata
            fiscal_year: defaults to latest
            wacc_override: use this WACC instead of computing
            growth_override: use this g instead of historical CAGR

        Returns:
            MultipleResult with full decomposition and signal
        """
        ticker = calc_input.classification.ticker if calc_input.classification else "UNKNOWN"
        result = MultipleResult(ticker=ticker, fiscal_year=fiscal_year or 0)

        # ── Load data ─────────────────────────────────────────────────────────
        df = calc_input.df

        if df.empty:
            result.warnings.append(f"No data for {ticker}")
            return result

        # Phase Q-5: split FY rows from TTM the same way DCFEngine does.
        # Multiple Decomposition shares the base row with DCF so analyst-
        # facing ROIC + EV/EBITDA stay consistent across tools.
        if "period" in df.columns:
            df_fy  = df[df["period"] == "FY"]
            df_ttm = df[df["period"] == "TTM"]
        else:
            df_fy  = df
            df_ttm = df.iloc[0:0]

        if df_fy.empty:
            result.warnings.append(f"No FY data for {ticker}")
            return result

        fy = fiscal_year or int(df_fy["fiscal_year"].max())
        latest_fy_row = df_fy[df_fy["fiscal_year"] == fy].iloc[0]
        if not df_ttm.empty:
            from aletheia.tools.dcf_engine import _merge_ttm_with_fy_fallback
            row = _merge_ttm_with_fy_fallback(df_ttm.iloc[-1], latest_fy_row)
        else:
            row = latest_fy_row
        result.fiscal_year = fy

        def get(col, fallback=0.0):
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                return float(val)
            return fallback

        revenue = get("clean_Revenue")
        ebitda = get("derived_EBITDA")
        ebit = get("clean_NormalizedEBIT")
        nopat = get("clean_NOPAT", ebit * 0.79)
        roic = get("derived_ROIC", 0.12)
        net_income = get("raw_NetIncome")
        net_debt = get("derived_NetDebt")
        # Pass None (not the local get-default of 0.0) for missing
        # rates so the resolver doesn't treat a missing tax field as a
        # legitimate 0% rate. Without this, MD silently used 0.0 while
        # DCFEngine (whose get defaults to None) fell to statutory,
        # breaking WACC consistency.
        tax_rate, tax_rate_source = resolve_tax_rate(
            ticker=ticker, fn="multiple_decomposition.run",
            df=df_fy, fy=fy,
            cash_tax_rate=get("clean_CashTaxRate", None),
            gaap_tax_rate=get("clean_GAAP_TaxRate", None),
        )
        result.tax_rate_source = tax_rate_source

        # Tier-3 input range check on tax_rate. Defense-in-depth: the
        # resolver enforces ``_is_usable_rate`` on every input but this
        # catches anything malformed that slips through (e.g. an
        # in-range company_fy mean computed from edge-case rows).
        tax_min, tax_max = RANGE_BOUNDS["tax_rate"]
        try:
            _require_range(
                tax_rate, min=tax_min, max=tax_max,
                field_name="tax_rate", ticker=ticker,
                fn="multiple_decomposition.run",
                note="rates outside [-1, 1] are upstream errors",
            )
        except CalculationError as exc:
            result.warnings.append(str(exc))
            if _guard_mode() == "hard":
                raise
            return result

        # Reinvestment rate = g / ROIC (proxy)
        # We derive g from historical CAGR
        hist = df[df["fiscal_year"] <= fy].sort_values("fiscal_year")
        rev_series = hist["clean_Revenue"].dropna()
        rev_now3 = float(rev_series.iloc[-1]) if len(rev_series) > 0 else 0.0
        cagr_candidates3 = []
        for lookback in [3, 5, 7, 10]:
            if len(rev_series) >= lookback:
                r0 = float(rev_series.iloc[-lookback])
                if r0 > 0 and rev_now3 > 0:
                    cagr_candidates3.append((rev_now3 / r0) ** (1/lookback) - 1)
        if len(cagr_candidates3) >= 3:
            s3 = sorted(cagr_candidates3)
            hist_cagr = float(np.median(s3[1:-1]))
        elif cagr_candidates3:
            hist_cagr = float(np.median(cagr_candidates3))
        else:
            hist_cagr = 0.08
        hist_cagr = float(np.clip(hist_cagr, 0.0, 0.60))
        g = growth_override or hist_cagr

        profit_margin = net_income / revenue if revenue > 0 and net_income else 0.0
        reinvestment_rate = g / max(roic, 0.08) if roic > 0 else 0.5

        result.revenue = revenue
        result.ebitda = ebitda
        result.ebit = ebit
        result.nopat = nopat
        result.roic = roic
        result.growth_rate = g
        result.terminal_growth = self.terminal_growth
        result.profit_margin = profit_margin
        result.reinvestment_rate = reinvestment_rate

        # Sector
        if calc_input.classification:
            result.sector = calc_input.classification.sector
        else:
            result.sector = "Default"

        sector_mults = SECTOR_MEDIAN_MULTIPLES.get(
            result.sector, SECTOR_MEDIAN_MULTIPLES["Default"]
        )
        result.sector_median_ev_ebitda = sector_mults["ev_ebitda"]

        # ── Live market data ──────────────────────────────────────────────────
        from aletheia.data.market_data import get_current_price, get_market_cap
        try:
            current_price = get_current_price(ticker)
            market_cap = get_market_cap(ticker)
        except Exception as e:
            result.warnings.append(f"market_data failed: {e}")
            current_price = 0.0
            market_cap = 0.0

        result.current_price = current_price
        market_ev = market_cap + net_debt
        result.market_ev = market_ev

        # ── Compute WACC ──────────────────────────────────────────────────────
        if wacc_override:
            wacc = wacc_override
        else:
            from aletheia.tools.dcf_engine import (
                _fetch_risk_free_rate, _compute_beta, compute_wacc
            )
            rf = _fetch_risk_free_rate()
            beta = _compute_beta(ticker)
            ltd = get("raw_LongTermDebt")
            wacc, _, _, _ = compute_wacc(
                ticker=ticker,
                total_equity=market_cap,
                total_debt=max(net_debt + get("raw_Cash", 0), ltd),
                interest_expense=ltd * 0.04,
                tax_rate=tax_rate,
                risk_free_rate=rf,
                beta=beta,
            )
        result.wacc = wacc

        # ── Market multiples ──────────────────────────────────────────────────
        result.market_ev_ebitda = market_ev / ebitda if ebitda > 0 else 0.0
        result.market_ev_ebit = market_ev / ebit if ebit > 0 else 0.0
        result.market_p_sales = market_cap / revenue if revenue > 0 else 0.0
        result.market_p_e = (
            current_price / (net_income / (market_cap / current_price))
            if net_income and current_price and market_cap > 0
            else 0.0
        )

        # PEG: P/E / EPS growth rate (5Y CAGR used as proxy)
        eps_cagr_series = hist["derived_ROIC"].dropna()   # Proxy for EPS quality
        if result.market_p_e > 0 and g > 0:
            result.market_peg = result.market_p_e / (g * 100)

        # ── Liberti formula — justified EV/EBITDA ────────────────────────────
        # EV/EBITDA = [ NOPAT × (1 - g/ROIC) / EBITDA ] / (WACC - g)
        w = wacc
        g_terminal = self.terminal_growth   # Use terminal growth for justified multiple

        result.roic_wacc_spread = roic - w
        result.value_creation = (
            "creating" if roic > w * 1.02
            else "destroying" if roic < w * 0.98
            else "neutral"
        )

        justified_ev_ebitda, cash_conv = _compute_justified_ev_ebitda(
            nopat=nopat, ebitda=ebitda, roic=roic, wacc=w, g_terminal=g_terminal
        )
        result.justified_ev_ebitda = justified_ev_ebitda
        result.cash_conversion_ratio = cash_conv


        # Justified EV/EBIT = EV/EBITDA × (EBITDA/EBIT)
        if ebit > 0 and ebitda > 0:
            result.justified_ev_ebit = result.justified_ev_ebitda * (ebitda / ebit)

        # ── P/Sales decomposition (SFM formula) ──────────────────────────────
        # P/Sales = [(1+g)/(r-g)] × (1 - reinvestment_rate) × profit_margin
        if w > g and profit_margin > 0:
            growth_component = (1 + g) / (w - g)
            reinvest_component = 1 - reinvestment_rate
            result.p_sales_growth_component = growth_component
            result.p_sales_reinvestment_component = reinvest_component
            result.p_sales_margin_component = profit_margin
            result.justified_p_sales = growth_component * reinvest_component * profit_margin
        else:
            result.justified_p_sales = 0.0

        # ── Premium/discount analysis ─────────────────────────────────────────
        if result.justified_ev_ebitda > 0:
            result.ev_ebitda_premium = result.market_ev_ebitda - result.justified_ev_ebitda
            result.ev_ebitda_premium_pct = result.ev_ebitda_premium / result.justified_ev_ebitda
        result.vs_sector_premium = result.market_ev_ebitda - result.sector_median_ev_ebitda

        # Output sanity on ev_ebitda_premium_pct. This is the field thesis
        # prose cites ("474.1% multiple premium" — NVDA). Within [-90%,
        # +1000%] is the plausibility band: a 90% discount means market
        # is pricing distress (legitimate); +1000% means model degradation
        # (justified multiple near zero or wrong unit). Outside this band
        # is almost always a NOPAT/WACC/growth combination producing
        # justified_ev_ebitda < 1x, which is itself the bug to surface.
        pp = result.ev_ebitda_premium_pct
        if pp != 0.0 and not (-0.90 <= pp <= 10.0):
            err = (
                f"ev_ebitda_premium_pct={pp*100:.1f}% outside plausibility "
                f"band [-90%, +1000%]. Likely justified_ev_ebitda is near-"
                f"zero ({result.justified_ev_ebitda:.2f}x) — check NOPAT, "
                "WACC, growth inputs."
            )
            result.warnings.append(err)
            if _guard_mode() == "hard":
                from aletheia.calculations import CalculationOutputError
                raise CalculationOutputError(
                    err, ticker=ticker, fn="multiple_decomposition.run",
                    field="ev_ebitda_premium_pct",
                    value=float(pp), expected="[-0.90, 10.0]",
                )
            if _guard_mode() in ("shadow", "soft"):
                import logging
                logging.getLogger(__name__).warning(
                    "multiple_decomposition output sanity: ticker=%s premium_pct=%.3f "
                    "justified=%.2f market=%.2f",
                    ticker, pp, result.justified_ev_ebitda, result.market_ev_ebitda,
                )

        # ── Key drivers ───────────────────────────────────────────────────────
        result.drivers = [
            MultipleDriver(
                name="ROIC-WACC",
                value=result.roic_wacc_spread,
                interpretation=(
                    f"ROIC ({roic:.1%}) {'exceeds' if roic > w else 'lags'} "
                    f"WACC ({w:.1%}) by {abs(result.roic_wacc_spread):.1%}. "
                    + ("Growth is value-creating — justifies premium." if roic > w
                       else "Growth destroys value — should compress multiple.")
                ),
            ),
            MultipleDriver(
                name="CashConversion",
                value=result.cash_conversion_ratio,
                interpretation=(
                    f"EBITDA converts to FCFF at {result.cash_conversion_ratio:.2f}x ratio. "
                    + ("Strong cash conversion supports the multiple."
                       if result.cash_conversion_ratio > 0.40
                       else "Weak cash conversion — high reinvestment drag.")
                ),
            ),
            MultipleDriver(
                name="ProfitMargin",
                value=profit_margin,
                interpretation=(
                    f"Net margin of {profit_margin:.1%}. "
                    f"P/Sales of {result.market_p_sales:.1f}x implies "
                    f"{'reasonable' if result.market_p_sales <= result.justified_p_sales * 1.2 else 'elevated'} "
                    f"multiple given margins (justified: {result.justified_p_sales:.1f}x)."
                ),
            ),
        ]

        # ── Signal ────────────────────────────────────────────────────────────
        reasons = []
        premium_pct = result.ev_ebitda_premium_pct

        if premium_pct < -0.20:
            result.signal = "undervalued"
            reasons.append(
                f"Trading at {abs(premium_pct):.0%} discount to justified multiple "
                f"({result.market_ev_ebitda:.1f}x vs {result.justified_ev_ebitda:.1f}x). "
                f"ROIC-WACC spread of {result.roic_wacc_spread:+.1%} supports higher multiple."
            )
        elif premium_pct < 0.10:
            result.signal = "fairly_valued"
            reasons.append(
                f"Trading close to justified multiple "
                f"({result.market_ev_ebitda:.1f}x vs {result.justified_ev_ebitda:.1f}x). "
                f"Upside dependent on ROIC or growth improvement."
            )
        elif premium_pct < 0.40:
            result.signal = "moderate_premium"
            reasons.append(
                f"Trading at {premium_pct:.0%} premium to justified multiple. "
                f"Premium requires either ROIC improvement above {roic:.1%} "
                f"or growth acceleration above {g:.1%}."
            )
        elif premium_pct < 1.0:
            result.signal = "high_premium"
            reasons.append(
                f"Trading at {premium_pct:.0%} premium to justified multiple. "
                f"Market is pricing in material improvement in ROIC or growth "
                f"that must be documented before position entry."
            )
        else:
            result.signal = "speculative_premium"
            reasons.append(
                f"Trading at {premium_pct:.0%} premium to justified multiple. "
                f"Valuation requires extraordinary assumptions — flag for mandatory "
                f"reverse-DCF analysis and TAM stress test."
            )

        # Additional signal: P/Sales without margin context
        if profit_margin < 0.05 and result.market_p_sales > 5:
            reasons.append(
                f"P/Sales of {result.market_p_sales:.1f}x with only {profit_margin:.1%} "
                f"net margin implies very significant margin improvement is required "
                f"to justify the revenue multiple."
            )
            result.warnings.append(
                "High P/Sales with low margins: P/Sales comparison across companies "
                "with different margin profiles is misleading (SFM principle). "
                f"At current margins, justified P/Sales = {result.justified_p_sales:.1f}x."
            )

        result.signal_reasons = reasons

        if self.verbose:
            print(result.summary())

        return result


