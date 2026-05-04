"""
aletheia/tools/screening_ratios.py

Unified Screening Framework — Graham + Lynch + Malkiel + Liberti
=================================================================
Computes all 34 metrics from Section 4.1 of the framework document.
Reads from InvestmentDatabase + live market data (yfinance).

Every metric traces to a named authority (Graham / Lynch / Malkiel / Liberti)
and is compared against the framework's defined threshold.

Output: ScreeningCard — one per ticker, all 34 metrics with
        pass/flag/fail signals and the authority behind each threshold.

Usage:
    from aletheia.tools.screening_ratios import ScreeningEngine
    engine = ScreeningEngine()
    card = engine.score("TICKER")
    print(card.summary())

    # Full universe
    universe = engine.score_universe(["TICKER1", "TICKER2"])
    print(engine.universe_table(universe))
"""

import json
import warnings
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Tuple
from pathlib import Path

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")


# ─────────────────────────────────────────────────────────────────────────────
# Signal constants
# ─────────────────────────────────────────────────────────────────────────────
PASS  = "✓"
FLAG  = "⚠"
FAIL  = "✗"
NA    = "—"


# ─────────────────────────────────────────────────────────────────────────────
# One metric result
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class MetricResult:
    name: str
    category: str
    authority: str          # Graham / Lynch / Malkiel / Liberti / Framework
    value: Optional[float]
    threshold: str          # Human-readable threshold description
    signal: str             # PASS / FLAG / FAIL / NA
    note: str = ""

    def display_value(self) -> str:
        if self.value is None:
            return "N/A"
        # Auto-format based on magnitude
        if abs(self.value) >= 1e9:
            return f"${self.value/1e9:.1f}B"
        if abs(self.value) >= 1e6:
            return f"${self.value/1e6:.0f}M"
        if abs(self.value) < 10 and abs(self.value) != 0:
            return f"{self.value:.2f}x"
        if abs(self.value) <= 1.0 and self.name.endswith("%"):
            return f"{self.value:.1%}"
        return f"{self.value:.1f}"


# ─────────────────────────────────────────────────────────────────────────────
# Screening card — all 34 metrics for one ticker
# ─────────────────────────────────────────────────────────────────────────────
@dataclass
class ScreeningCard:
    ticker: str
    fiscal_year: int
    current_price: float = 0.0
    market_cap: float = 0.0
    metrics: List[MetricResult] = field(default_factory=list)

    @property
    def passes(self) -> int:
        return sum(1 for m in self.metrics if m.signal == PASS)

    @property
    def flags(self) -> int:
        return sum(1 for m in self.metrics if m.signal == FLAG)

    @property
    def fails(self) -> int:
        return sum(1 for m in self.metrics if m.signal == FAIL)

    @property
    def available(self) -> int:
        return sum(1 for m in self.metrics if m.signal != NA)

    def get(self, name: str) -> Optional[MetricResult]:
        for m in self.metrics:
            if m.name == name:
                return m
        return None

    def summary(self) -> str:
        lines = [
            f"\n{'='*80}",
            f"  SCREENING SCORECARD: {self.ticker}  FY{self.fiscal_year}",
            f"  Price: ${self.current_price:,.2f}  |  Market Cap: ${self.market_cap/1e9:.1f}B",
            f"  Result: {self.passes}✓ pass  {self.flags}⚠ flag  {self.fails}✗ fail  "
            f"({self.available} of {len(self.metrics)} metrics available)",
            f"{'='*80}",
        ]
        current_cat = ""
        for m in self.metrics:
            if m.category != current_cat:
                current_cat = m.category
                lines.append(f"\n  [{current_cat}]")
            val_str = m.display_value()
            lines.append(
                f"  {m.signal}  {m.name:<35} {val_str:>10}  "
                f"│ {m.threshold:<35} [{m.authority}]"
                + (f"  ← {m.note}" if m.note else "")
            )
        return "\n".join(lines)

    def to_dict(self) -> dict:
        d = {
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "current_price": self.current_price,
            "market_cap_bn": self.market_cap / 1e9 if self.market_cap else None,
            "passes": self.passes,
            "flags": self.flags,
            "fails": self.fails,
            "available": self.available,
        }
        for m in self.metrics:
            key = m.name.lower().replace("/", "_per_").replace(" ", "_").replace("%", "pct")
            d[key] = m.value
            d[f"{key}_signal"] = m.signal
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe(val, fallback=None):
    """Return None if val is NaN or None, else float."""
    if val is None:
        return fallback
    try:
        f = float(val)
        return fallback if np.isnan(f) else f
    except (TypeError, ValueError):
        return fallback


def _get_json(row, key, fallback=None):
    """Extract a value from raw_json or clean_json blobs."""
    for blob_col in ["raw_json", "clean_json"]:
        blob = row.get(blob_col)
        if blob:
            try:
                d = json.loads(blob) if isinstance(blob, str) else blob
                val = d.get(key)
                if val is not None:
                    return float(val)
            except Exception:
                pass
    return fallback


def _cagr(series: pd.Series, years: int) -> Optional[float]:
    """Compute CAGR from a pandas Series over `years` lookback."""
    clean = series.dropna()
    if len(clean) < years + 1:
        return None
    v0 = float(clean.iloc[-(years + 1)])
    v1 = float(clean.iloc[-1])
    if v0 <= 0 or v1 <= 0:
        return None
    return (v1 / v0) ** (1 / years) - 1


def _robust_cagr(series: pd.Series) -> Optional[float]:
    """Multi-period trimmed median CAGR (same method as dcf_engine.py)."""
    candidates = []
    for y in [3, 5, 7, 10]:
        c = _cagr(series, y)
        if c is not None:
            candidates.append(c)
    if len(candidates) >= 3:
        s = sorted(candidates)
        return float(np.median(s[1:-1]))
    elif candidates:
        return float(np.median(candidates))
    return None


def _signal_threshold(value, good_below=None, good_above=None,
                       flag_below=None, flag_above=None) -> str:
    """
    Returns PASS/FLAG/FAIL based on threshold comparisons.
    good_below: value < this → PASS
    good_above: value > this → PASS
    flag_below: value < this → FLAG (between fail and pass)
    flag_above: value > this → FLAG
    """
    if value is None:
        return NA
    if good_below is not None:
        if value <= good_below:
            return PASS
        if flag_above is not None and value > flag_above:
            return FAIL
        return FLAG
    if good_above is not None:
        if value >= good_above:
            return PASS
        if flag_below is not None and value < flag_below:
            return FAIL
        return FLAG
    return NA


# ─────────────────────────────────────────────────────────────────────────────
# Main engine
# ─────────────────────────────────────────────────────────────────────────────

class ScreeningEngine:
    """
    Computes all 34 metrics from the unified screening framework.
    Reads from InvestmentDatabase (DuckDB) + yfinance for live market data.
    """

    def __init__(
        self,
        db_path: str = "valuation_data/database/investment.duckdb",
        verbose: bool = False,
    ):
        self.db_path = db_path
        self.verbose = verbose

    # ─────────────────────────────────────────────────────────────────────────
    def score(self, calc_input: 'CalculationInput', fiscal_year: int = None) -> ScreeningCard:
        """Compute full screening scorecard for one ticker."""
        ticker = calc_input.classification.ticker if calc_input.classification else "UNKNOWN"
        df = calc_input.df

        if df.empty:
            card = ScreeningCard(ticker=ticker, fiscal_year=0)
            card.metrics.append(MetricResult(
                "DATA", "Error", "—", None, "—", NA,
                note=f"No data in database for {ticker}"
            ))
            return card

        fy = fiscal_year or int(df["fiscal_year"].max())
        all_years_df = df.sort_values("fiscal_year")
        row = df[df["fiscal_year"] == fy].iloc[0]

        # ── Live market data ──────────────────────────────────────────────────
        from aletheia.data.market_data import get_current_price, get_market_cap, get_shares_outstanding
        try:
            price = get_current_price(ticker)
            mktcap = get_market_cap(ticker)
            shares = get("clean_SharesDiluted")
            if not shares or shares <= 0:
                shares = get_shares_outstanding(ticker)
        except Exception:
            price = mktcap = shares = 0.0

        card = ScreeningCard(
            ticker=ticker,
            fiscal_year=fy,
            current_price=price,
            market_cap=mktcap,
        )

        # ── Inject DCF Engine outputs ─────────────────────────────────────────
        from aletheia.tools.dcf_engine import DCFEngine
        try:
            dcf_result = DCFEngine(verbose=False).run(calc_input)
            wacc = dcf_result.wacc
            terminal_growth = dcf_result.base.assumptions.terminal_growth
        except Exception as e:
            if self.verbose:
                print(f"[WARN] DCF dynamic inputs failed for {ticker}: {e}")
            wacc = 0.09
            terminal_growth = 0.025

        return self._compute_metrics(
            card=card,
            all_years_df=all_years_df,
            fy=fy,
            row=row,
            price=price,
            mktcap=mktcap,
            shares=shares,
            wacc=wacc,
            terminal_growth=terminal_growth
        )

    def _compute_metrics(
        self,
        card: ScreeningCard,
        all_years_df: pd.DataFrame,
        fy: int,
        row: pd.Series,
        price: float,
        mktcap: float,
        shares: float,
        wacc: float,
        terminal_growth: float
    ) -> ScreeningCard:

        # ── Extract base values ───────────────────────────────────────────────
        revenue      = _safe(row.get("clean_Revenue"))
        ebit         = _safe(row.get("clean_NormalizedEBIT"))
        ebitda       = _safe(row.get("derived_EBITDA"))
        nopat        = _safe(row.get("clean_NOPAT"))
        fcf          = _safe(row.get("derived_FCF"))
        roic         = _safe(row.get("derived_ROIC"))
        roe          = _safe(row.get("derived_ROE"))
        net_debt     = _safe(row.get("derived_NetDebt"))
        inv_capital  = _safe(row.get("derived_InvestedCapital"))
        gross_margin = _safe(row.get("derived_GrossMargin_Pct"))  # in %
        ebit_margin  = _safe(row.get("derived_EBIT_Margin_Pct"))
        fcf_margin   = _safe(row.get("derived_FCF_Margin_Pct"))
        net_income   = _safe(row.get("raw_NetIncome"))
        total_assets = _safe(row.get("raw_TotalAssets"))
        total_equity = _safe(row.get("raw_TotalEquity"))
        ltd          = _safe(row.get("raw_LongTermDebt"))
        cash         = _safe(row.get("raw_Cash"))
        sbc          = _safe(row.get("clean_SBC"))
        sbc_pct_fcf  = _safe(row.get("clean_SBC_PctFCF"))
        tax_rate     = _safe(row.get("clean_CashTaxRate")) or 0.21

        # From raw_json / clean_json blobs
        current_assets = _get_json(row, "CurrentAssets") or _get_json(row, "AssetsCurrent")
        current_liab   = _get_json(row, "CurrentLiabilities") or _get_json(row, "LiabilitiesCurrent")
        std            = _get_json(row, "ShortTermDebt") or _get_json(row, "LiabilitiesCurrent") and 0
        interest_exp   = _get_json(row, "InterestExpense") or _get_json(row, "InterestAndDebtExpense")
        dividends      = _get_json(row, "Dividends") or _get_json(row, "PaymentsOfDividendsCommonStock")
        maint_capex    = _get_json(row, "MaintenanceCapEx")
        growth_capex   = _get_json(row, "GrowthCapEx")

        # Total debt
        total_debt = (ltd or 0) + (std or 0)
        if total_debt == 0 and ltd:
            total_debt = ltd

        # EV from market
        ev = mktcap + (net_debt or 0)

        # Time-series for CAGR calculations
        rev_series    = all_years_df["clean_Revenue"].where(
            all_years_df["fiscal_year"] <= fy
        )
        fcf_series    = all_years_df["derived_FCF"].where(
            all_years_df["fiscal_year"] <= fy
        )
        ni_series     = all_years_df["raw_NetIncome"].where(
            all_years_df["fiscal_year"] <= fy
        )
        ebit_m_series = all_years_df["derived_EBIT_Margin_Pct"].where(
            all_years_df["fiscal_year"] <= fy
        )

        # Computed ratios
        rev_cagr    = _robust_cagr(rev_series)
        fcf_cagr    = _robust_cagr(fcf_series)
        eps_series  = ni_series / shares if shares > 0 else ni_series * 0
        eps_cagr    = _robust_cagr(eps_series)

        pe          = price / (net_income / shares) if (net_income and shares > 0 and price > 0) else None
        peg         = pe / (eps_cagr * 100) if (pe and eps_cagr and eps_cagr > 0) else None
        pb          = price / (total_equity / shares) if (total_equity and shares > 0 and price > 0) else None
        ev_ebitda   = ev / ebitda if (ev and ebitda and ebitda > 0) else None
        ev_ebit     = ev / ebit if (ev and ebit and ebit > 0) else None
        ev_fcf      = ev / fcf if (ev and fcf and fcf > 0) else None
        mos         = (nopat / tax_rate / 0.09 - ev) / ev if (nopat and ev > 0) else None  # rough EPV MoS

        de_ratio    = total_debt / total_equity if (total_equity and total_equity > 0) else None
        interest_cov = ebit / abs(interest_exp) if (ebit and interest_exp and interest_exp != 0) else None
        current_ratio = current_assets / current_liab if (current_assets and current_liab and current_liab > 0) else None
        nd_ebitda   = net_debt / ebitda if (net_debt is not None and ebitda and ebitda > 0) else None
        std_pct_debt = std / total_debt if (std and total_debt and total_debt > 0) else None

        div_yield   = dividends / mktcap if (dividends and mktcap > 0) else None

        # EPS leverage: EPS CAGR - Revenue CAGR
        eps_leverage = (eps_cagr - rev_cagr) if (eps_cagr is not None and rev_cagr is not None) else None

        # EPS stability — count negative NI years
        ni_clean = ni_series.dropna()
        negative_ni_years = int((ni_clean < 0).sum()) if len(ni_clean) > 0 else 0
        eps_stable = negative_ni_years == 0 and len(ni_clean) >= 5

        # Operating margin trend — delta over last 4 years
        ebit_m_clean = ebit_m_series.dropna()
        om_trend = None
        if len(ebit_m_clean) >= 4:
            om_trend = float(ebit_m_clean.iloc[-1] - ebit_m_clean.iloc[-4])

        # EBITDA cash conversion ratio (Liberti)
        effective_roic = max(roic, 0.08) if roic else 0.08
        cash_conv = nopat * (1 - terminal_growth / effective_roic) / ebitda if (nopat and ebitda and ebitda > 0) else None

        # ── BUILD METRICS — exact same order as Table 36 ──────────────────────

        def add(name, cat, authority, value, threshold, signal, note=""):
            card.metrics.append(MetricResult(
                name=name, category=cat, authority=authority,
                value=value, threshold=threshold, signal=signal, note=note
            ))

        # ── VALUATION ─────────────────────────────────────────────────────────
        cat = "Valuation"
        add("P/E Ratio", cat, "Graham/Lynch/Malkiel", pe,
            "≤15 (Graham); sector-relative (Lynch/Malkiel)",
            _signal_threshold(pe, good_below=15, flag_above=30) if pe else NA)

        add("PEG Ratio", cat, "Lynch/Malkiel", peg,
            "<1 undervalued; <0.5 strong buy",
            PASS if peg and peg < 1 else FLAG if peg and peg < 2 else FAIL if peg else NA)

        add("P/B Ratio", cat, "Graham/Malkiel", pb,
            "≤1.5 (Graham); lower than peers",
            _signal_threshold(pb, good_below=1.5, flag_above=3.0) if pb else NA)

        add("EV/EBITDA (clean)", cat, "Liberti", ev_ebitda,
            "Justify via ROIC-WACC; sector comps",
            PASS if ev_ebitda and ev_ebitda < 20 else FLAG if ev_ebitda and ev_ebitda < 35 else FAIL if ev_ebitda else NA,
            note="Compare to justified multiple in multiple_decomposition")

        add("EV/EBIT (normalized)", cat, "Liberti", ev_ebit,
            "Peer comparison; capex-intensity adjusted",
            PASS if ev_ebit and ev_ebit < 25 else FLAG if ev_ebit and ev_ebit < 40 else FAIL if ev_ebit else NA)

        add("EV/FCF", cat, "Liberti/Lynch", ev_fcf,
            "<25x entry for capital-light compounders",
            _signal_threshold(ev_fcf, good_below=25, flag_above=50) if ev_fcf else NA)

        # Margin of safety — use base case from DCF if available
        add("Margin of Safety", cat, "Graham", None,
            "Buy at <65-85% of DCF intrinsic value",
            NA, note="→ See equity_bridge.py base scenario margin_of_safety")

        add("Implied DCF Multiple", cat, "Liberti", None,
            "TV multiple should decline as business matures",
            NA, note="→ See dcf_engine.py terminal.implied_tv_ebitda_multiple")

        # ── GROWTH ────────────────────────────────────────────────────────────
        cat = "Growth"
        add("Revenue CAGR (robust)", cat, "Lynch/Malkiel",
            rev_cagr * 100 if rev_cagr else None,
            ">10-20% by sector (Lynch/Malkiel)",
            PASS if rev_cagr and rev_cagr > 0.10 else FLAG if rev_cagr and rev_cagr > 0.05 else FAIL if rev_cagr else NA)

        add("EPS Growth Rate", cat, "Graham/Lynch/Malkiel",
            eps_cagr * 100 if eps_cagr else None,
            "≥33% over 10Y (Graham); 10-20% annually (Lynch)",
            PASS if eps_cagr and eps_cagr > 0.10 else FLAG if eps_cagr and eps_cagr > 0.05 else FAIL if eps_cagr else NA)

        add("EPS Stability (10yr+)", cat, "Graham",
            float(negative_ni_years) if ni_clean is not None else None,
            "Zero negative EPS years (Graham safety screen)",
            PASS if eps_stable else FLAG if negative_ni_years <= 2 else FAIL,
            note=f"{negative_ni_years} negative NI years found in history")

        add("EPS Leverage Signal", cat, "Framework",
            eps_leverage * 100 if eps_leverage else None,
            "Positive = profit leverage; Negative = profitless enthusiasm",
            PASS if eps_leverage and eps_leverage > 0 else FAIL if eps_leverage and eps_leverage < -0.05 else FLAG if eps_leverage else NA)

        add("FCF Growth (robust)", cat, "Lynch/Malkiel",
            fcf_cagr * 100 if fcf_cagr else None,
            "Consistently positive; growing >revenue preferred",
            PASS if fcf_cagr and fcf_cagr > 0 and (rev_cagr is None or fcf_cagr >= rev_cagr)
            else FLAG if fcf_cagr and fcf_cagr > 0
            else FAIL if fcf_cagr else NA)

        # ── PROFITABILITY ─────────────────────────────────────────────────────
        cat = "Profitability"
        add("ROE", cat, "Lynch",
            roe * 100 if roe else None,
            "≥15% sustained across full cycle",
            PASS if roe and roe > 0.15 else FLAG if roe and roe > 0.08 else FAIL if roe else NA)

        add("ROIC vs WACC", cat, "Liberti/Framework",
            roic * 100 if roic else None,
            "ROIC>WACC = value creation; gap drives multiple premium",
            PASS if roic and roic > 0.12 else FLAG if roic and roic > 0.08 else FAIL if roic else NA,
            note=f"ROIC-WACC spread ≈ {(roic - wacc)*100:.1f}%" if roic else "")

        add("Gross Margin %", cat, "Framework",
            gross_margin,
            "Software >60%; Healthcare >40%; Infra >35%",
            PASS if gross_margin and gross_margin > 40 else FLAG if gross_margin and gross_margin > 20 else FAIL if gross_margin else NA)

        add("Operating Margin Trend", cat, "Lynch",
            om_trend,
            "Positive slope over 6-8+ consecutive quarters",
            PASS if om_trend and om_trend > 0 else FLAG if om_trend and om_trend > -2 else FAIL if om_trend else NA,
            note=f"4Y delta: {om_trend:+.1f}pp" if om_trend else "")

        add("EBITDA Cash Conversion", cat, "Liberti",
            cash_conv,
            "Higher = better (captures reinvestment drag on FCF)",
            PASS if cash_conv and cash_conv > 0.4 else FLAG if cash_conv and cash_conv > 0.2 else FAIL if cash_conv else NA)

        # ── BALANCE SHEET ─────────────────────────────────────────────────────
        cat = "Balance Sheet"
        add("Debt-to-Equity", cat, "Graham/Lynch/Malkiel",
            de_ratio,
            "<1 preferred; Software 0-40%; Infra 20-90%",
            PASS if de_ratio is not None and de_ratio < 1 else FLAG if de_ratio and de_ratio < 2 else FAIL if de_ratio else NA)

        add("Interest Coverage", cat, "Graham",
            interest_cov,
            "≥5 (Graham); ≥4-7x minimum (Framework)",
            PASS if interest_cov and interest_cov > 5 else FLAG if interest_cov and interest_cov > 3 else FAIL if interest_cov else NA,
            note="Missing if InterestExpense not in XBRL" if not interest_exp else "")

        add("Current Ratio", cat, "Graham",
            current_ratio,
            "≥2 (Graham); ≥1.5 for capital-light businesses",
            PASS if current_ratio and current_ratio >= 2 else FLAG if current_ratio and current_ratio >= 1 else FAIL if current_ratio else NA)

        add("Debt Maturity Risk", cat, "Framework",
            std_pct_debt * 100 if std_pct_debt else None,
            "<40% of debt maturing in 12 months",
            PASS if std_pct_debt is not None and std_pct_debt < 0.40 else FLAG if std_pct_debt and std_pct_debt < 0.60 else FAIL if std_pct_debt else NA)

        add("Net Debt / EBITDA", cat, "Liberti",
            nd_ebitda,
            "<2x preferred; <3x acceptable; >4x = flag",
            PASS if nd_ebitda is not None and nd_ebitda < 2 else FLAG if nd_ebitda and nd_ebitda < 3 else FAIL if nd_ebitda else NA,
            note="Negative = net cash position" if nd_ebitda and nd_ebitda < 0 else "")

        # ── CASH & CAPITAL ────────────────────────────────────────────────────
        cat = "Cash & Capital"
        add("FCF Margin %", cat, "Lynch/Malkiel",
            fcf_margin,
            "≥15%; growing over time",
            PASS if fcf_margin and fcf_margin > 15 else FLAG if fcf_margin and fcf_margin > 5 else FAIL if fcf_margin else NA)

        add("SBC as % of FCF", cat, "Framework",
            sbc_pct_fcf,
            "<5% acceptable; >10% = quiet dilution",
            PASS if sbc_pct_fcf is not None and sbc_pct_fcf < 5 else FLAG if sbc_pct_fcf and sbc_pct_fcf < 10 else FAIL if sbc_pct_fcf else NA)

        add("Capex Discipline", cat, "Framework",
            growth_capex / revenue * 100 if (growth_capex and revenue) else None,
            "Growth capex should track revenue growth trajectory",
            PASS if growth_capex and maint_capex and growth_capex > maint_capex * 0.5 else FLAG,
            note=f"Maintenance: ${maint_capex/1e9:.1f}B  Growth: ${growth_capex/1e9:.1f}B"
            if (maint_capex and growth_capex) else "")

        add("Buyback Quality", cat, "Framework", None,
            "Buybacks at <intrinsic value = accretive",
            NA, note="→ Requires historical IV in thesis memory (Phase 4)")

        # ── SHAREHOLDER RETURNS ───────────────────────────────────────────────
        cat = "Shareholder Returns"
        add("Dividend Yield %", cat, "Graham/Malkiel/Lynch",
            div_yield * 100 if div_yield else None,
            "≥2% sustainable; very high yield = distress signal",
            PASS if div_yield and 0.02 <= div_yield <= 0.08
            else FLAG if div_yield and div_yield > 0.08
            else FLAG if div_yield and div_yield < 0.02 and div_yield > 0
            else NA,
            note="N/A if no dividend paid" if not dividends else "")

        add("Dividend Record (20yr+)", cat, "Graham", None,
            "≥20 years uninterrupted (Graham safety screen)",
            NA, note="→ Requires dividend history tag mapping (PaymentsOfDividends)")

        # ── SIZE & CONTEXT ────────────────────────────────────────────────────
        cat = "Size & Context"
        add("EV (Billions)", cat, "Liberti",
            ev / 1e9 if ev else None,
            "Context: liquidity, index inclusion, institutional ownership",
            PASS)  # informational only

        add("EBITDA (Billions)", cat, "Liberti",
            ebitda / 1e9 if ebitda else None,
            "Context: size for EV/EBITDA calibration",
            PASS)

        add("Market Cap (Billions)", cat, "Liberti",
            mktcap / 1e9 if mktcap else None,
            "Context: risk premium, liquidity, volatility profile",
            PASS)

        add("Market Classification", cat, "Lynch",
            None,
            "Growth / Defensive / Cyclical / Turnaround",
            NA, note="→ Lifecycle classifier (Section 14.1) not yet built")

        return card

# CLI entrypoint removed to avoid architectural violations.
