"""
aletheia/data/quantitative_screens.py

Quantitative Earnings Quality Screens
======================================
Implements three automated screens that run on every CleanedRecord:

  1. Beneish M-Score  — accounting manipulation probability
  2. Sloan Accrual Ratio — earnings quality / cash conversion
  3. Earnings Power Value (EPV) — value of current earnings at zero growth

These screens run automatically after cleaning_engine.py completes.
Results are stored on the CleanedRecord and written to the database.

Usage:
    from aletheia.data.quantitative_screens import QuantitativeScreens
    from aletheia.data.cleaning_engine import CleaningEngine

    engine = CleaningEngine()
    record = engine.clean("AAPL", 2023)

    screens = QuantitativeScreens()
    result = screens.run_all(record, prior_record=prior)
    print(result)
"""

from dataclasses import dataclass, field
from typing import Optional, Dict
from aletheia.data.cleaning_engine import CleanedRecord, _safe_div


# ─────────────────────────────────────────────────────────────────────────────
# Result containers
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BeneishResult:
    """
    Beneish M-Score result.
    Score > -1.78 → elevated manipulation probability → mandatory review.
    """
    m_score: Optional[float] = None
    components: Dict[str, Optional[float]] = field(default_factory=dict)
    is_flagged: bool = False
    flag_reason: str = ""
    data_completeness: float = 0.0   # 0–1, fraction of 8 components available


@dataclass
class SloanResult:
    """
    Sloan Accrual Ratio result.
    High positive accruals → earnings outrunning cash → expect mean reversion.
    Negative accruals → cash outrunning earnings → quality signal.
    """
    accrual_ratio: Optional[float] = None
    net_income: Optional[float] = None
    operating_cf: Optional[float] = None
    investing_cf: Optional[float] = None
    avg_total_assets: Optional[float] = None
    signal: str = "unknown"      # "high_quality", "neutral", "caution", "flag"
    is_flagged: bool = False


@dataclass
class EPVResult:
    """
    Earnings Power Value (Greenwald).
    EPV = Normalized EBIT × (1 - t) / WACC
    Separates value of current earnings from value of growth.
    """
    epv: Optional[float] = None
    epv_per_share: Optional[float] = None
    normalized_ebit: Optional[float] = None
    tax_rate: Optional[float] = None
    wacc: Optional[float] = None
    shares_outstanding: Optional[float] = None
    epv_to_price_ratio: Optional[float] = None   # > 1 = buying current earnings at discount
    signal: str = "unknown"


@dataclass
class ScreenResult:
    """Container for all three screen results."""
    ticker: str
    fiscal_year: int
    beneish: BeneishResult = field(default_factory=BeneishResult)
    sloan: SloanResult = field(default_factory=SloanResult)
    epv: EPVResult = field(default_factory=EPVResult)
    any_flagged: bool = False

    def __str__(self) -> str:
        lines = [
            f"ScreenResult: {self.ticker} FY{self.fiscal_year}",
            f"  Beneish M-Score : {self.beneish.m_score:.3f} "
            f"{'⚠ FLAGGED' if self.beneish.is_flagged else '✓ OK'}"
            if self.beneish.m_score else "  Beneish M-Score : insufficient data",
            f"  Sloan Accrual   : {self.sloan.accrual_ratio:.3f} [{self.sloan.signal}]"
            if self.sloan.accrual_ratio else "  Sloan Accrual   : insufficient data",
            f"  EPV/Share       : {self.epv.epv_per_share:,.2f} "
            f"(ratio to price: {self.epv.epv_to_price_ratio:.2f}x)"
            if self.epv.epv_per_share else "  EPV             : insufficient data",
        ]
        return "\n".join(lines)

    def to_flat_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            # Beneish
            "beneish_m_score": self.beneish.m_score,
            "beneish_flagged": self.beneish.is_flagged,
            "beneish_completeness": self.beneish.data_completeness,
            **{f"beneish_{k}": v for k, v in self.beneish.components.items()},
            # Sloan
            "sloan_accrual_ratio": self.sloan.accrual_ratio,
            "sloan_signal": self.sloan.signal,
            "sloan_flagged": self.sloan.is_flagged,
            # EPV
            "epv": self.epv.epv,
            "epv_per_share": self.epv.epv_per_share,
            "epv_to_price_ratio": self.epv.epv_to_price_ratio,
            "epv_signal": self.epv.signal,
            # Summary
            "any_flagged": self.any_flagged,
        }


# ─────────────────────────────────────────────────────────────────────────────
# Main class
# ─────────────────────────────────────────────────────────────────────────────

class QuantitativeScreens:
    """
    Runs all three quantitative screens against a CleanedRecord.
    Requires prior-year CleanedRecord for Beneish (YoY deltas) and Sloan.
    """

    # Beneish threshold: > -1.78 = elevated manipulation probability
    BENEISH_THRESHOLD = -1.78

    # Sloan thresholds
    SLOAN_FLAG_THRESHOLD = 0.05    # > +5% = flag
    SLOAN_CAUTION_THRESHOLD = 0.02  # > +2% = caution
    SLOAN_QUALITY_THRESHOLD = -0.02  # < -2% = high quality signal

    # Default WACC for EPV if not provided
    DEFAULT_WACC = 0.09

    def __init__(self, verbose: bool = True):
        self.verbose = verbose

    def run_all(
        self,
        record: CleanedRecord,
        prior_record: Optional[CleanedRecord] = None,
        current_price: Optional[float] = None,
        wacc_override: Optional[float] = None,
    ) -> ScreenResult:
        """
        Run all three screens. Returns a ScreenResult.

        Args:
            record: Current year CleanedRecord (from cleaning_engine.py)
            prior_record: Prior year CleanedRecord (required for Beneish and Sloan)
            current_price: Current market price per share (for EPV ratio)
            wacc_override: Use this WACC instead of default 9% for EPV
        """
        result = ScreenResult(ticker=record.ticker, fiscal_year=record.fiscal_year)

        result.beneish = self._beneish_m_score(record, prior_record)
        result.sloan = self._sloan_accrual_ratio(record, prior_record)
        result.epv = self._earnings_power_value(record, current_price, wacc_override)

        result.any_flagged = (
            result.beneish.is_flagged
            or result.sloan.is_flagged
        )

        # Write screen results back onto the CleanedRecord for downstream use
        record.derived["Beneish_MScore"] = result.beneish.m_score
        record.derived["Sloan_AccrualRatio"] = result.sloan.accrual_ratio
        record.derived["EPV"] = result.epv.epv
        record.derived["EPV_PerShare"] = result.epv.epv_per_share

        if result.beneish.is_flagged:
            record.warn(
                f"Beneish M-Score {result.beneish.m_score:.3f} > -1.78 threshold. "
                f"Mandatory fundamental review before deployment."
            )
        if result.sloan.is_flagged:
            record.warn(
                f"Sloan Accrual Ratio {result.sloan.accrual_ratio:.3f} > {self.SLOAN_FLAG_THRESHOLD}. "
                f"Earnings outrunning cash — expect mean reversion."
            )

        if self.verbose:
            print(result)

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Screen 1 — Beneish M-Score
    # ─────────────────────────────────────────────────────────────────────────

    def _beneish_m_score(
        self,
        record: CleanedRecord,
        prior: Optional[CleanedRecord],
    ) -> BeneishResult:
        """
        Beneish M-Score: 8 financial ratio model.
        Score > -1.78 = elevated manipulation probability.

        The 8 components:
          DSRI  — Days Sales Receivable Index
          GMI   — Gross Margin Index
          AQI   — Asset Quality Index
          SGI   — Sales Growth Index
          DEPI  — Depreciation Index
          SGAI  — SG&A Index
          LVGI  — Leverage Index
          TATA  — Total Accruals to Total Assets

        Formula:
          M = -4.84 + 0.92*DSRI + 0.528*GMI + 0.404*AQI + 0.892*SGI
              + 0.115*DEPI - 0.172*SGAI + 4.679*TATA - 0.327*LVGI
        """
        result = BeneishResult()

        if prior is None:
            result.flag_reason = "Prior year record required for Beneish — skipped"
            result.data_completeness = 0.0
            return result

        # ── Helpers ──────────────────────────────────────────────────────────
        def get(rec: CleanedRecord, *keys) -> Optional[float]:
            for k in keys:
                v = rec.raw.get(k) or rec.clean.get(k)
                if v is not None:
                    return float(v)
            return None

        # Current year values
        revenue = get(record, "Revenue")
        ar = get(record, "AccountsReceivable", "AccountsReceivableNetCurrent")
        cogs = get(record, "COGS", "CostOfRevenue", "CostOfGoodsAndServicesSold")
        gross_profit = get(record, "GrossProfit") or (
            (revenue - cogs) if revenue and cogs else None
        )
        total_assets = get(record, "TotalAssets", "Assets")
        pp_and_e = get(record, "PPE", "PropertyPlantAndEquipmentNet")
        depreciation = get(record, "Depreciation", "DepreciationAndAmortization")
        sga = get(record, "SG&A", "SellingGeneralAndAdministrativeExpense")
        long_term_debt = get(record, "LongTermDebt", "LongTermDebtNoncurrent")
        total_equity = get(record, "TotalEquity")
        current_assets = get(record, "CurrentAssets", "AssetsCurrent")
        current_liab = get(record, "LiabilitiesCurrent")
        net_income = get(record, "NetIncome", "NetIncomeLoss")
        cash_ops = get(record, "OperatingCF", "NetCashProvidedByUsedInOperatingActivities")

        # Prior year values
        p_revenue = get(prior, "Revenue")
        p_ar = get(prior, "AccountsReceivable", "AccountsReceivableNetCurrent")
        p_cogs = get(prior, "COGS", "CostOfRevenue", "CostOfGoodsAndServicesSold")
        p_gross_profit = get(prior, "GrossProfit") or (
            (p_revenue - p_cogs) if p_revenue and p_cogs else None
        )
        p_total_assets = get(prior, "TotalAssets", "Assets")
        p_pp_and_e = get(prior, "PPE", "PropertyPlantAndEquipmentNet")
        p_depreciation = get(prior, "Depreciation", "DepreciationAndAmortization")
        p_sga = get(prior, "SG&A", "SellingGeneralAndAdministrativeExpense")
        p_long_term_debt = get(prior, "LongTermDebt", "LongTermDebtNoncurrent")
        p_total_equity = get(prior, "TotalEquity")
        p_current_assets = get(prior, "CurrentAssets", "AssetsCurrent")
        p_current_liab = get(prior, "LiabilitiesCurrent")

        components = {}
        available = 0

        # ── DSRI: Days Sales Receivable Index ─────────────────────────────────
        # = (AR_t / Revenue_t) / (AR_{t-1} / Revenue_{t-1})
        # Rising DSRI = revenue recognized faster than cash collected
        if ar and revenue and p_ar and p_revenue and p_revenue != 0 and revenue != 0:
            dsri = (ar / revenue) / (p_ar / p_revenue)
            components["DSRI"] = dsri
            available += 1

        # ── GMI: Gross Margin Index ───────────────────────────────────────────
        # = (GP_{t-1}/Rev_{t-1}) / (GP_t/Rev_t)
        # > 1 = deteriorating gross margins → incentive to manipulate
        if gross_profit and revenue and p_gross_profit and p_revenue and revenue != 0 and p_revenue != 0:
            gmi = (p_gross_profit / p_revenue) / (gross_profit / revenue)
            components["GMI"] = gmi
            available += 1

        # ── AQI: Asset Quality Index ──────────────────────────────────────────
        # = [1 - (CA_t + PPE_t) / TA_t] / [1 - (CA_{t-1} + PPE_{t-1}) / TA_{t-1}]
        # > 1 = more assets shifted to intangibles/deferred costs
        if (current_assets and pp_and_e and total_assets and
                p_current_assets and p_pp_and_e and p_total_assets and
                total_assets != 0 and p_total_assets != 0):
            aqi_t = 1 - (current_assets + pp_and_e) / total_assets
            aqi_p = 1 - (p_current_assets + p_pp_and_e) / p_total_assets
            if aqi_p != 0:
                aqi = aqi_t / aqi_p
                components["AQI"] = aqi
                available += 1

        # ── SGI: Sales Growth Index ───────────────────────────────────────────
        # = Revenue_t / Revenue_{t-1}
        # High growth companies have more incentive to manipulate
        if revenue and p_revenue and p_revenue != 0:
            sgi = revenue / p_revenue
            components["SGI"] = sgi
            available += 1

        # ── DEPI: Depreciation Index ──────────────────────────────────────────
        # = [Dep_{t-1}/(Dep_{t-1}+PPE_{t-1})] / [Dep_t/(Dep_t+PPE_t)]
        # > 1 = slowing depreciation rate → inflating assets
        if (depreciation and pp_and_e and p_depreciation and p_pp_and_e and
                (depreciation + pp_and_e) != 0 and (p_depreciation + p_pp_and_e) != 0):
            depi = (
                (p_depreciation / (p_depreciation + p_pp_and_e)) /
                (depreciation / (depreciation + pp_and_e))
            )
            components["DEPI"] = depi
            available += 1

        # ── SGAI: SG&A Index ─────────────────────────────────────────────────
        # = (SGA_t/Rev_t) / (SGA_{t-1}/Rev_{t-1})
        # > 1 = disproportionate SG&A growth → operational issues
        if sga and revenue and p_sga and p_revenue and revenue != 0 and p_revenue != 0:
            sgai = (sga / revenue) / (p_sga / p_revenue)
            components["SGAI"] = sgai
            available += 1

        # ── LVGI: Leverage Index ──────────────────────────────────────────────
        # = [(LTD_t + CL_t) / TA_t] / [(LTD_{t-1} + CL_{t-1}) / TA_{t-1}]
        # > 1 = increasing leverage → incentive to manipulate
        if (long_term_debt and current_liab and total_assets and
                p_long_term_debt and p_current_liab and p_total_assets and
                total_assets != 0 and p_total_assets != 0):
            lvgi = (
                ((long_term_debt + current_liab) / total_assets) /
                ((p_long_term_debt + p_current_liab) / p_total_assets)
            )
            components["LVGI"] = lvgi
            available += 1

        # ── TATA: Total Accruals to Total Assets ──────────────────────────────
        # = (Net Income - Operating CF) / Total Assets
        # High positive = accrual-based earnings > cash earnings
        if net_income is not None and cash_ops is not None and total_assets and total_assets != 0:
            tata = (net_income - cash_ops) / total_assets
            components["TATA"] = tata
            available += 1

        result.components = components
        result.data_completeness = available / 8

        # ── Compute M-Score if we have enough components ─────────────────────
        if available >= 5:
            dsri = components.get("DSRI", 0)
            gmi = components.get("GMI", 0)
            aqi = components.get("AQI", 0)
            sgi = components.get("SGI", 0)
            depi = components.get("DEPI", 0)
            sgai = components.get("SGAI", 0)
            lvgi = components.get("LVGI", 0)
            tata = components.get("TATA", 0)

            m_score = (
                -4.84
                + 0.920 * dsri
                + 0.528 * gmi
                + 0.404 * aqi
                + 0.892 * sgi
                + 0.115 * depi
                - 0.172 * sgai
                + 4.679 * tata
                - 0.327 * lvgi
            )

            result.m_score = round(m_score, 4)
            result.is_flagged = m_score > self.BENEISH_THRESHOLD

            if result.is_flagged:
                result.flag_reason = (
                    f"M-Score {m_score:.3f} exceeds threshold {self.BENEISH_THRESHOLD}. "
                    f"Elevated manipulation probability. "
                    f"Key drivers: DSRI={dsri:.2f}, GMI={gmi:.2f}, TATA={tata:.4f}. "
                    f"Trigger: mandatory fundamental review and auditor communication analysis."
                )
        else:
            result.flag_reason = (
                f"Insufficient data ({available}/8 components). "
                f"M-Score not computed."
            )

        if self.verbose:
            score_str = f"{result.m_score:.3f}" if result.m_score else "N/A"
            flag_str = "⚠ FLAGGED" if result.is_flagged else "✓ OK"
            print(f"  Beneish M-Score: {score_str} {flag_str} "
                  f"({available}/8 components, completeness={result.data_completeness:.0%})")

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Screen 2 — Sloan Accrual Ratio
    # ─────────────────────────────────────────────────────────────────────────

    def _sloan_accrual_ratio(
        self,
        record: CleanedRecord,
        prior: Optional[CleanedRecord],
    ) -> SloanResult:
        """
        Sloan (1996) Accrual Ratio.
        Formula: (Net Income - Operating CF - Investing CF) / Avg Total Assets

        Interpretation:
          > +5%  → FLAG: earnings significantly outrunning cash. Mean reversion expected.
          +2–5%  → CAUTION
          -2–+2% → NEUTRAL
          < -2%  → HIGH QUALITY: cash outrunning earnings (positive signal)

        Sloan (1996) showed companies in highest accrual decile underperform
        lowest decile by 10%+ annually — one of the most robust academic anomalies.
        """
        result = SloanResult()

        net_income = record.raw.get("NetIncome") or record.raw.get("NetIncomeLoss")
        cash_ops = record.raw.get("OperatingCF") or record.raw.get("NetCashProvidedByUsedInOperatingActivities")
        cash_inv = record.raw.get("InvestingCF") or record.raw.get("NetCashProvidedByUsedInInvestingActivities")
        total_assets = record.raw.get("TotalAssets")
        prior_assets = prior.raw.get("TotalAssets") if prior else None

        result.net_income = net_income
        result.operating_cf = cash_ops
        result.investing_cf = cash_inv

        # Need at least net income, operating CF, and total assets
        if net_income is None or cash_ops is None or total_assets is None:
            result.signal = "insufficient_data"
            if self.verbose:
                print("  Sloan Accrual: insufficient data")
            return result

        # Average total assets
        if prior_assets:
            avg_assets = (total_assets + prior_assets) / 2
        else:
            avg_assets = total_assets
            
        result.avg_total_assets = avg_assets

        # Investing CF defaults to 0 if not available
        cash_inv_val = cash_inv if cash_inv is not None else 0.0

        # Accrual Ratio = (NI - OCF - ICF) / Avg Assets
        accruals = net_income - cash_ops - cash_inv_val
        accrual_ratio = accruals / avg_assets if avg_assets != 0 else None

        if accrual_ratio is None:
            result.signal = "insufficient_data"
            return result

        result.accrual_ratio = round(accrual_ratio, 4)

        # Signal classification
        if accrual_ratio > self.SLOAN_FLAG_THRESHOLD:
            result.signal = "flag"
            result.is_flagged = True
        elif accrual_ratio > self.SLOAN_CAUTION_THRESHOLD:
            result.signal = "caution"
        elif accrual_ratio < self.SLOAN_QUALITY_THRESHOLD:
            result.signal = "high_quality"
        else:
            result.signal = "neutral"

        if self.verbose:
            flag_str = "⚠" if result.is_flagged else "✓"
            print(f"  Sloan Accrual: {accrual_ratio:.4f} [{result.signal}] {flag_str}")

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # Screen 3 — Earnings Power Value (Greenwald)
    # ─────────────────────────────────────────────────────────────────────────

    def _earnings_power_value(
        self,
        record: CleanedRecord,
        current_price: Optional[float] = None,
        wacc_override: Optional[float] = None,
    ) -> EPVResult:
        """
        Earnings Power Value (Bruce Greenwald).
        EPV = Normalized EBIT × (1 - t) / WACC

        This separates the value of current earnings (assuming zero growth)
        from the value of growth. If EPV > market price, you are paying
        nothing for growth — current operations alone justify the valuation.

        EPV/Price ratio interpretation:
          > 1.0 = current operations worth more than market price → strong MoS
          0.5–1.0 = paying modest growth premium
          < 0.5 = paying a large premium for growth assumptions
        """
        result = EPVResult()

        normalized_ebit = (
            record.clean.get("NormalizedEBIT")
            or record.raw.get("OperatingIncome")
            or record.raw.get("EBIT")
        )
        cash_tax_rate = record.clean.get("CashTaxRate") or 0.21
        shares = (
            record.raw.get("WeightedAverageNumberOfDilutedSharesOutstanding")
            or record.raw.get("CommonStockSharesOutstanding")
        )

        result.normalized_ebit = normalized_ebit
        result.tax_rate = cash_tax_rate
        result.shares_outstanding = shares

        wacc = wacc_override or self.DEFAULT_WACC
        result.wacc = wacc

        if normalized_ebit is None:
            result.signal = "insufficient_data"
            if self.verbose:
                print("  EPV: insufficient data (no normalized EBIT)")
            return result

        # EPV = NOPAT / WACC
        nopat = normalized_ebit * (1 - cash_tax_rate)
        epv = nopat / wacc

        result.epv = round(epv, 0)

        # EPV per share
        if shares and shares > 0:
            epv_per_share = epv / shares
            result.epv_per_share = round(epv_per_share, 2)

            # EPV to price ratio
            if current_price and current_price > 0:
                ratio = epv_per_share / current_price
                result.epv_to_price_ratio = round(ratio, 3)

                if ratio > 1.2:
                    result.signal = "deep_value"
                elif ratio > 0.8:
                    result.signal = "fair_value"
                elif ratio > 0.5:
                    result.signal = "growth_premium"
                else:
                    result.signal = "high_growth_premium"
            else:
                result.signal = "no_price"

        if self.verbose:
            epv_str = f"{epv:,.0f}"
            per_share_str = f"${result.epv_per_share:,.2f}/share" if result.epv_per_share else ""
            ratio_str = f"(ratio={result.epv_to_price_ratio:.2f}x)" if result.epv_to_price_ratio else ""
            print(f"  EPV: {epv_str} {per_share_str} {ratio_str} [{result.signal}]")

        return result
