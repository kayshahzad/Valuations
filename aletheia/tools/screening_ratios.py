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

from aletheia.calculations import (
    _require_range,
    RANGE_BOUNDS,
    CalculationError,
    _guard_mode,
    resolve_tax_rate,
)

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
    tax_rate_source: str = "statutory"  # cash | gaap | company_fy | statutory

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
        # NOTE: this block had a long-standing bug — `get("clean_SharesDiluted")`
        # called an undefined name, raising NameError that was silently caught
        # by the broad `except Exception` below and wiped price/mktcap/shares
        # to zero. That made every price-dependent ratio (P/E, P/B, PEG,
        # EV/EBITDA-implied) return N/A across the entire universe. Fixed by
        # reading shares from the cleaned-record row + handling each fetch
        # independently so a single failure doesn't poison the others.
        from aletheia.data.market_data import get_current_price, get_market_cap, get_shares_outstanding
        try:
            price = float(get_current_price(ticker) or 0.0)
        except Exception:
            price = 0.0
        try:
            mktcap = float(get_market_cap(ticker) or 0.0)
        except Exception:
            mktcap = 0.0
        # Prefer cleaned-record diluted shares (already split-adjusted via the
        # cleaning engine); fall back to live yfinance only if the FY value
        # is missing or zero.
        shares = _safe(row.get("clean_SharesDiluted")) or _safe(row.get("raw_SharesDiluted"))
        if not shares or shares <= 0:
            try:
                shares = float(get_shares_outstanding(ticker) or 0.0)
            except Exception:
                shares = 0.0
        # If yfinance gave us mktcap but we have shares too, sanity-check;
        # otherwise infer mktcap from price × shares for downstream EV math.
        if mktcap == 0 and price > 0 and shares > 0:
            mktcap = price * shares

        card = ScreeningCard(
            ticker=ticker,
            fiscal_year=fy,
            current_price=price,
            market_cap=mktcap,
        )

        # ── Inject DCF Engine outputs ─────────────────────────────────────────
        # Pulls WACC + terminal-growth (used by P/E peg-band, EV/EBITDA-justified
        # math, etc.) PLUS base-scenario margin-of-safety + implied-TV-multiple,
        # which were previously hardcoded N/A in the Margin-of-Safety [Graham]
        # and Implied-DCF-Multiple [Liberti] screening rows. Fail-soft on any
        # DCF computation issue — defaults preserve the previous behavior.
        from aletheia.tools.dcf_engine import DCFEngine
        wacc = 0.09
        terminal_growth = 0.025
        base_mos = None
        implied_tv_ebitda = None
        try:
            dcf_result = DCFEngine(verbose=False).run(calc_input)
            wacc = dcf_result.wacc
            if dcf_result.base:
                terminal_growth = dcf_result.base.assumptions.terminal_growth
                # Margin of safety = (IV - current_price) / current_price.
                # `dcf_result.upside(iv)` returns this fraction directly.
                base_iv = dcf_result.intrinsic_per_share(
                    dcf_result.base.enterprise_value, dcf_result.net_debt,
                )
                if base_iv is not None:
                    base_mos = dcf_result.upside(base_iv)
                tv = dcf_result.base.terminal
                if tv is not None:
                    implied_tv_ebitda = tv.implied_tv_ebitda_multiple
        except Exception as e:
            if self.verbose:
                print(f"[WARN] DCF dynamic inputs failed for {ticker}: {e}")

        return self._compute_metrics(
            card=card,
            all_years_df=all_years_df,
            fy=fy,
            row=row,
            price=price,
            mktcap=mktcap,
            shares=shares,
            wacc=wacc,
            terminal_growth=terminal_growth,
            base_mos=base_mos,
            implied_tv_ebitda=implied_tv_ebitda,
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
        terminal_growth: float,
        base_mos: Optional[float] = None,
        implied_tv_ebitda: Optional[float] = None,
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
        # FY-only slice for the company_fy lookup. TTM rows would
        # duplicate fiscal_year entries and shift the dedup-keep-last
        # behaviour, producing a different rate than the FY-only
        # DCFEngine path — that broke WACC consistency on first pass.
        if "period" in all_years_df.columns:
            tax_lookup_df = all_years_df[all_years_df["period"] == "FY"]
        else:
            tax_lookup_df = all_years_df
        tax_rate, tax_rate_source = resolve_tax_rate(
            ticker=card.ticker, fn="screening_ratios._compute_metrics",
            df=tax_lookup_df, fy=fy,
            cash_tax_rate=_safe(row.get("clean_CashTaxRate")),
            gaap_tax_rate=_safe(row.get("clean_GAAP_TaxRate")),
        )
        card.tax_rate_source = tax_rate_source

        # Tier-3 input range check on tax_rate. Defense-in-depth: the
        # resolver enforces ``_is_usable_rate`` on every input but a
        # malformed rate surviving (e.g. in-range company_fy mean from
        # edge-case rows) gets caught here before propagating into the
        # 34-metric scorecard.
        tax_min, tax_max = RANGE_BOUNDS["tax_rate"]
        try:
            _require_range(
                tax_rate, min=tax_min, max=tax_max,
                field_name="tax_rate", ticker=card.ticker,
                fn="screening_ratios._compute_metrics",
                note="rates outside [-1, 1] are upstream errors",
            )
        except CalculationError as exc:
            # ScreeningCard is fail-soft (returns N/A metrics on bad
            # inputs); record the error and continue with the legacy
            # fallback so the analyst still sees a populated scorecard.
            if _guard_mode() == "hard":
                raise
            import logging
            logging.getLogger(__name__).warning(
                "screening_ratios tax_rate flagged: %s", exc
            )

        # From raw_json / clean_json blobs
        current_assets = _get_json(row, "CurrentAssets") or _get_json(row, "AssetsCurrent")
        current_liab   = _get_json(row, "CurrentLiabilities") or _get_json(row, "LiabilitiesCurrent")
        std            = _get_json(row, "ShortTermDebt") or _get_json(row, "LiabilitiesCurrent") and 0
        interest_exp   = _get_json(row, "InterestExpense") or _get_json(row, "InterestAndDebtExpense")
        # Interest-expense fallback: AAPL/MSFT/LLY stopped filing InterestExpense
        # as a standalone XBRL fact in FY2024+ (bundled into "Other income/
        # expense, net"). When missing, estimate as LongTermDebt × proxy rate
        # of 4.5% — same proxy DCFEngine uses for its WACC kd computation.
        # Loses precision but better than N/A on Interest Coverage for
        # AAPL/MSFT/LLY where Schwab DOES show $154M / $169M / $160M etc.
        if interest_exp is None or interest_exp == 0:
            ltd = _get_json(row, "LongTermDebt")
            if ltd and ltd > 0:
                interest_exp = ltd * 0.045
        # Dividends: cleaning engine writes `DividendsPaid` (not the older
        # `Dividends` or `PaymentsOfDividendsCommonStock` aliases). All 4
        # validated tickers use DividendsPaid in raw_json.
        dividends      = (_get_json(row, "DividendsPaid")
                          or _get_json(row, "Dividends")
                          or _get_json(row, "PaymentsOfDividendsCommonStock"))
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

        # Computed ratios. Revenue CAGR uses the framework-canonical
        # FY-only normalized CAGR (shared with ReverseDCF) so "historical
        # revenue CAGR" is ONE number across the screening table, the deep
        # dive, and the report — the prior _robust_cagr here pulled in the
        # TTM row and disagreed (AAPL 6.6% vs the canonical 4.8%).
        from aletheia.tools.reverse_dcf import normalized_revenue_cagr
        rev_cagr    = normalized_revenue_cagr(all_years_df, fy)
        fcf_cagr    = _robust_cagr(fcf_series)
        eps_series  = ni_series / shares if shares > 0 else ni_series * 0
        eps_cagr    = _robust_cagr(eps_series)

        # Valuation multiples + ratios — delegated to the central
        # formula module (Phase 4 centralization). The central
        # functions encode the same None-on-non-positive-denom policy
        # this block enforced inline; behavior is identical.
        from aletheia.calculations.formulas import (
            price_to_earnings as _pe,
            price_to_book as _pb,
            ev_to_ebitda as _ev_ebitda,
            ev_to_ebit as _ev_ebit,
            ev_to_fcf as _ev_fcf,
            net_debt_to_ebitda as _nd_ebitda,
            debt_to_equity as _de,
            interest_coverage as _interest_cov,
            current_ratio as _current_ratio_fn,
            dividend_yield as _div_yield,
        )
        eps_val = (net_income / shares) if (net_income and shares > 0) else None
        book_per_share = (total_equity / shares) if (total_equity and shares > 0) else None
        pe          = _pe(price=price if price > 0 else None, eps=eps_val)
        peg         = pe / (eps_cagr * 100) if (pe and eps_cagr and eps_cagr > 0) else None
        pb          = _pb(market_cap=price if price > 0 else None,
                          book_equity=book_per_share)
        ev_ebitda   = _ev_ebitda(enterprise_value=ev if ev else None, ebitda=ebitda)
        ev_ebit     = _ev_ebit(enterprise_value=ev if ev else None, ebit=ebit)
        ev_fcf      = _ev_fcf(enterprise_value=ev if ev else None, fcf=fcf)
        mos         = (nopat / tax_rate / 0.09 - ev) / ev if (nopat and ev > 0) else None  # rough EPV MoS

        de_ratio    = _de(total_debt=total_debt, total_equity=total_equity)
        interest_cov = _interest_cov(ebit=ebit, interest_expense=interest_exp)
        current_ratio = _current_ratio_fn(
            current_assets=current_assets, current_liabilities=current_liab,
        )
        nd_ebitda   = _nd_ebitda(net_debt=net_debt, ebitda=ebitda)
        std_pct_debt = std / total_debt if (std and total_debt and total_debt > 0) else None

        div_yield   = _div_yield(dividends_paid=dividends, market_cap=mktcap)

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

        # Margin of safety — base scenario IV vs current price.
        # Graham band: PASS when MoS ≥ 35% (price ≤ 65% of IV);
        #              FLAG when 15% ≤ MoS < 35%;
        #              FAIL when MoS < 15% (within 15% of IV — no margin).
        # Display as percent; signals use the underlying decimal.
        mos_value = base_mos * 100 if base_mos is not None else None
        if base_mos is None:
            mos_signal = NA
        elif base_mos >= 0.35:
            mos_signal = PASS
        elif base_mos >= 0.15:
            mos_signal = FLAG
        else:
            mos_signal = FAIL
        add("Margin of Safety", cat, "Graham", mos_value,
            "Buy at <65-85% of DCF intrinsic value (MoS ≥ 35%)",
            mos_signal,
            note=("Base IV vs current price. Negative = overvalued. "
                  "Source: dcf_engine.py base scenario.")
                  if base_mos is not None
                  else "→ See equity_bridge.py base scenario margin_of_safety")

        # Implied terminal-year EBITDA multiple — sanity check on DCF
        # assumptions. Per Liberti: terminal multiple should compress as
        # the business matures; a TV that requires ≥20× EBITDA in
        # perpetuity is signaling growth assumptions that don't decay.
        # Bands: PASS <12×, FLAG 12-20×, FAIL >20×.
        if implied_tv_ebitda is None:
            tv_signal = NA
        elif implied_tv_ebitda < 12:
            tv_signal = PASS
        elif implied_tv_ebitda < 20:
            tv_signal = FLAG
        else:
            tv_signal = FAIL
        add("Terminal EV/EBITDA (implied)", cat, "Liberti", implied_tv_ebitda,
            "TV multiple should decline as business matures (<12× ideal)",
            tv_signal,
            note=("Implied EBITDA multiple at the TERMINAL year (TV / terminal "
                  "EBITDA) — NOT the justified EV/EBITDA in multiple_decomposition. "
                  "Source: dcf_engine.py base.terminal.")
                  if implied_tv_ebitda is not None
                  else "→ See dcf_engine.py terminal.implied_tv_ebitda_multiple")

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

        add("ROIC", cat, "Liberti/Framework",
            roic * 100 if roic else None,
            "ROIC>WACC = value creation; gap drives multiple premium",
            PASS if roic and roic > 0.12 else FLAG if roic and roic > 0.08 else FAIL if roic else NA,
            note=f"ROIC−WACC spread ≈ {(roic - wacc)*100:.1f}% (this row shows ROIC, not the spread)" if roic else "")

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
