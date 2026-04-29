"""
aletheia/tools/equity_bridge.py

Phase 2 — Equity Bridge
========================
Converts Enterprise Value (from DCFEngine) to per-share equity value
using the full 8-item Liberti equity bridge.

Bridge items (in order):
  Enterprise Value
  − Long-term debt
  − Short-term / current portion of debt
  − Minority interests (NCI)
  − Pension deficit (from Phase 1 cleaning, Domain 6)
  − Operating lease liabilities (from Phase 1 cleaning, Domain 5)
  − Non-recurring liabilities
  + Cash and equivalents (with three haircuts)
  + Short/long-term marketable securities
  + JVA / associate investments (separately valued)
  + Other non-core assets
  = Equity Value
  ÷ Diluted shares outstanding
  = Intrinsic Value Per Share

Cash haircuts (Liberti):
  1. Working capital requirement: ~3% of revenue (needed to run operations)
  2. Restricted cash: contractually unavailable
  3. Overseas repatriation tax: tax cost on repatriating foreign-held cash

Usage:
    from aletheia.tools.equity_bridge import EquityBridge
    bridge = EquityBridge()
    result = bridge.build("AAPL", enterprise_value=3_000_000_000_000)
    print(result.summary())
"""

import warnings
from dataclasses import dataclass, field
from typing import Optional, List, Dict

import numpy as np
import yfinance as yf

warnings.filterwarnings("ignore")

# Default overseas cash repatriation tax rate
# (TCJA reduced from 35% to ~15.5% on cash, 8% on illiquid assets)
DEFAULT_REPATRIATION_TAX = 0.155

# Working capital requirement as % of revenue
WORKING_CAPITAL_PCT = 0.03

# OECD Pillar Two minimum effective tax rate
PILLAR_TWO_FLOOR = 0.15


# ─────────────────────────────────────────────────────────────────────────────
# Bridge item dataclass
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class BridgeItem:
    name: str
    value: float          # Positive = added to equity, Negative = deducted
    note: str = ""
    confidence: float = 1.0   # 0-1, how reliable is this estimate


@dataclass
class CashAnalysis:
    """Detailed cash haircut analysis (Liberti standard)."""
    gross_cash: float = 0.0
    working_capital_haircut: float = 0.0    # ~3% of revenue
    restricted_cash_haircut: float = 0.0    # Contractually unavailable
    overseas_tax_haircut: float = 0.0       # Repatriation tax cost
    net_accessible_cash: float = 0.0
    marketable_securities: float = 0.0
    total_cash_value: float = 0.0           # Net accessible + securities


@dataclass
class EquityBridgeResult:
    """Complete equity bridge from EV to per-share intrinsic value."""
    ticker: str
    enterprise_value: float
    scenario_name: str = "base"

    # Bridge items in order
    items: List[BridgeItem] = field(default_factory=list)

    # Cash analysis
    cash_analysis: Optional[CashAnalysis] = None

    # Outputs
    equity_value: float = 0.0
    shares_diluted: float = 0.0
    intrinsic_per_share: float = 0.0
    current_price: float = 0.0
    margin_of_safety: float = 0.0    # (intrinsic - price) / price
    upside_pct: float = 0.0

    # Flags
    warnings: List[str] = field(default_factory=list)
    data_quality: float = 1.0        # Average confidence of bridge items

    def summary(self) -> str:
        lines = [
            f"EquityBridge: {self.ticker} [{self.scenario_name}]",
            f"  Enterprise Value : ${self.enterprise_value/1e9:>10,.1f}B",
        ]
        for item in self.items:
            sign = "+" if item.value >= 0 else "-"
            lines.append(
                f"  {sign} {item.name:<35}: ${abs(item.value)/1e9:>8,.1f}B"
                + (f"  [{item.note}]" if item.note else "")
            )
        lines += [
            f"  {'─'*55}",
            f"  Equity Value     : ${self.equity_value/1e9:>10,.1f}B",
            f"  Shares Diluted   : {self.shares_diluted/1e6:>10,.0f}M",
            f"  Intrinsic/Share  : ${self.intrinsic_per_share:>10,.2f}",
            f"  Current Price    : ${self.current_price:>10,.2f}",
            f"  Margin of Safety : {self.margin_of_safety:>10.1%}",
            f"  Upside           : {self.upside_pct:>10.1%}",
        ]
        if self.warnings:
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        d = {
            "ticker": self.ticker,
            "scenario": self.scenario_name,
            "enterprise_value": self.enterprise_value,
            "equity_value": self.equity_value,
            "shares_diluted": self.shares_diluted,
            "intrinsic_per_share": self.intrinsic_per_share,
            "current_price": self.current_price,
            "margin_of_safety": self.margin_of_safety,
            "upside_pct": self.upside_pct,
            "data_quality": self.data_quality,
        }
        for item in self.items:
            key = item.name.lower().replace(" ", "_").replace("/", "_")
            d[f"bridge_{key}"] = item.value
        if self.cash_analysis:
            ca = self.cash_analysis
            d["cash_gross"] = ca.gross_cash
            d["cash_wc_haircut"] = ca.working_capital_haircut
            d["cash_restricted_haircut"] = ca.restricted_cash_haircut
            d["cash_overseas_tax_haircut"] = ca.overseas_tax_haircut
            d["cash_net_accessible"] = ca.net_accessible_cash
            d["cash_marketable_securities"] = ca.marketable_securities
            d["cash_total_value"] = ca.total_cash_value
        return d


# ─────────────────────────────────────────────────────────────────────────────
# Main class
# ─────────────────────────────────────────────────────────────────────────────

class EquityBridge:
    """
    Builds the full Liberti equity bridge from EV to per-share intrinsic value.
    Reads all bridge inputs from the InvestmentDatabase.
    """

    def __init__(
        self,
        db_path: str = "valuation_data/database/investment.duckdb",
        repatriation_tax: float = DEFAULT_REPATRIATION_TAX,
        wc_pct: float = WORKING_CAPITAL_PCT,
        verbose: bool = True,
    ):
        self.db_path = db_path
        self.repatriation_tax = repatriation_tax
        self.wc_pct = wc_pct
        self.verbose = verbose

    def build(
        self,
        ticker: str,
        enterprise_value: float,
        fiscal_year: Optional[int] = None,
        scenario_name: str = "base",
        overseas_cash_fraction: float = 0.60,   # Fraction of cash held overseas
        restricted_cash: float = 0.0,            # Override if known
    ) -> EquityBridgeResult:
        """
        Build equity bridge for a given EV.

        Args:
            ticker: e.g. "AAPL"
            enterprise_value: from DCFEngine (bull/base/bear)
            fiscal_year: defaults to latest in DB
            scenario_name: label for this bridge ("bull", "base", "bear")
            overseas_cash_fraction: fraction of total cash held by foreign subs
            restricted_cash: override for known restricted cash amount

        Returns:
            EquityBridgeResult with full bridge and per-share intrinsic value
        """
        from aletheia.data.database import InvestmentDatabase

        result = EquityBridgeResult(
            ticker=ticker,
            enterprise_value=enterprise_value,
            scenario_name=scenario_name,
        )

        # ── Load DB data ──────────────────────────────────────────────────────
        try:
            db = InvestmentDatabase(verbose=False)
            df = db.get_latest(ticker)
            db.close()
        except Exception as e:
            result.warnings.append(f"DB load failed: {e}")
            return result

        if df.empty:
            result.warnings.append(f"No data for {ticker}")
            return result

        fy = fiscal_year or int(df["fiscal_year"].max())
        row = df[df["fiscal_year"] == fy].iloc[0]

        def get(col, fallback=0.0):
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                return float(val)
            return fallback

        # ── Extract bridge inputs ─────────────────────────────────────────────
        revenue = get("clean_Revenue")
        long_term_debt = get("raw_LongTermDebt")
        short_term_debt = get("raw_ShortTermDebt",
                              get("clean_ShortTermDebt", 0.0))
        minority_interest = get("raw_MinorityInterest", 0.0)

        # Phase 1 cleaning already computed these
        pension_deficit = get("clean_PensionDeficit_ForEquityBridge", 0.0)
        lease_debt = get("clean_LeaseDebt_ForEquityBridge", 0.0)
        jva_income = get("clean_JVA_Income_Isolated", 0.0)

        gross_cash = get("raw_Cash")
        short_term_investments = get("raw_ShortTermInvestments", 0.0)
        long_term_investments = get("raw_LongTermInvestments", 0.0)

        # ── Cash haircut analysis (Liberti) ───────────────────────────────────
        # Haircut 1: Working capital requirement
        wc_requirement = revenue * self.wc_pct

        # Haircut 2: Restricted cash (use override or estimate)
        restricted = restricted_cash if restricted_cash > 0 else 0.0

        # Haircut 3: Overseas cash repatriation tax
        overseas_cash = gross_cash * overseas_cash_fraction
        repatriation_cost = overseas_cash * self.repatriation_tax

        # Net accessible cash
        net_cash = max(0.0, gross_cash - wc_requirement - restricted - repatriation_cost)

        # Marketable securities (liquid)
        liquid_securities = short_term_investments   # Short-term only — LT may be illiquid

        total_cash_value = net_cash + liquid_securities

        cash_analysis = CashAnalysis(
            gross_cash=gross_cash,
            working_capital_haircut=wc_requirement,
            restricted_cash_haircut=restricted,
            overseas_tax_haircut=repatriation_cost,
            net_accessible_cash=net_cash,
            marketable_securities=liquid_securities,
            total_cash_value=total_cash_value,
        )
        result.cash_analysis = cash_analysis

        # ── JVA valuation ─────────────────────────────────────────────────────
        # If JVA income was isolated in Phase 1, value it at a sector PE multiple.
        # Use 20x as default PE for JVA stakes (conservative).
        jva_value = jva_income * 20 if jva_income > 0 else 0.0

        # ── Build bridge items in order ───────────────────────────────────────
        items = []

        # Deductions
        if long_term_debt > 0:
            items.append(BridgeItem(
                name="Long-term debt",
                value=-long_term_debt,
                note="Book value — face amount",
                confidence=0.95,
            ))

        if short_term_debt > 0:
            items.append(BridgeItem(
                name="Short-term debt",
                value=-short_term_debt,
                note="Current portion + commercial paper",
                confidence=0.90,
            ))

        if minority_interest > 0:
            items.append(BridgeItem(
                name="Minority interests (NCI)",
                value=-minority_interest,
                note="Book value — large NCIs may need separate valuation",
                confidence=0.75,
            ))

        if pension_deficit > 0:
            items.append(BridgeItem(
                name="Pension deficit",
                value=-pension_deficit,
                note="From D6 cleaning — unfunded obligation",
                confidence=0.85,
            ))

        if lease_debt > 0:
            items.append(BridgeItem(
                name="Operating lease liabilities",
                value=-lease_debt,
                note="From D5 cleaning — ASC 842 right-of-use obligations",
                confidence=0.90,
            ))

        # Additions
        if net_cash > 0:
            items.append(BridgeItem(
                name="Cash (net of haircuts)",
                value=net_cash,
                note=(f"Gross ${gross_cash/1e9:.1f}B − WC ${wc_requirement/1e9:.1f}B"
                      f" − restricted ${restricted/1e9:.1f}B"
                      f" − repatriation tax ${repatriation_cost/1e9:.1f}B"),
                confidence=0.85,
            ))

        if liquid_securities > 0:
            items.append(BridgeItem(
                name="Short-term marketable securities",
                value=liquid_securities,
                note="Liquid, accessible to shareholders",
                confidence=0.90,
            ))

        if jva_value > 0:
            items.append(BridgeItem(
                name="JVA / associate investments",
                value=jva_value,
                note=f"JVA income ${jva_income/1e9:.1f}B × 20x PE multiple",
                confidence=0.60,
            ))

        result.items = items

        # ── Compute equity value ──────────────────────────────────────────────
        bridge_adjustments = sum(item.value for item in items)
        equity_value = enterprise_value + bridge_adjustments
        result.equity_value = max(equity_value, 0.0)   # Floor at zero

        # Average confidence
        if items:
            result.data_quality = np.mean([i.confidence for i in items])

        # ── Per-share intrinsic value ─────────────────────────────────────────
        try:
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.fast_info
            current_price = float(info.last_price or 0)
            market_cap = float(info.market_cap or 0)
            shares = market_cap / current_price if current_price > 0 else 0.0
        except Exception:
            current_price = 0.0
            shares = 0.0

        result.current_price = current_price
        result.shares_diluted = shares

        if shares > 0:
            intrinsic = equity_value / shares
            result.intrinsic_per_share = intrinsic

            if current_price > 0:
                result.margin_of_safety = (intrinsic - current_price) / current_price
                result.upside_pct = result.margin_of_safety

        # ── Sanity checks and warnings ────────────────────────────────────────
        if repatriation_cost > gross_cash * 0.30:
            result.warnings.append(
                f"Overseas repatriation tax haircut is "
                f"{repatriation_cost/gross_cash:.0%} of gross cash "
                f"(${repatriation_cost/1e9:.1f}B). "
                f"Verify overseas cash fraction assumption ({overseas_cash_fraction:.0%})."
            )

        if equity_value < 0:
            result.warnings.append(
                "Equity value is negative — EV insufficient to cover all debt claims. "
                "Verify debt figures and EV assumptions."
            )
            result.equity_value = 0.0
            result.intrinsic_per_share = 0.0

        if minority_interest > enterprise_value * 0.20:
            result.warnings.append(
                f"Minority interest (${minority_interest/1e9:.1f}B) is material "
                f"({minority_interest/enterprise_value:.0%} of EV). "
                "Book value may understate fair value — consider separate valuation."
            )

        if self.verbose:
            print(result.summary())

        return result

    def build_for_dcf(
        self,
        ticker: str,
        dcf_result,   # DCFResult from dcf_engine.py
        fiscal_year: Optional[int] = None,
        overseas_cash_fraction: float = 0.60,
    ) -> Dict[str, EquityBridgeResult]:
        """
        Convenience method: build equity bridges for all three DCF scenarios.

        Args:
            ticker: stock ticker
            dcf_result: DCFResult object from DCFEngine.run()
            fiscal_year: year to use for bridge inputs

        Returns:
            Dict of {"bull": result, "base": result, "bear": result}
        """
        bridges = {}
        for scenario_name in ["bull", "base", "bear"]:
            scenario = getattr(dcf_result, scenario_name, None)
            if scenario is None:
                continue
            ev = scenario.enterprise_value
            bridges[scenario_name] = self.build(
                ticker=ticker,
                enterprise_value=ev,
                fiscal_year=fiscal_year,
                scenario_name=scenario_name,
                overseas_cash_fraction=overseas_cash_fraction,
            )
        return bridges


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from aletheia.tools.dcf_engine import DCFEngine

    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL"]

    for ticker in tickers:
        print(f"\n{'='*60}")
        print(f"  Equity Bridge: {ticker}")
        print(f"{'='*60}")

        engine = DCFEngine(verbose=False)
        dcf = engine.run(ticker)

        bridge = EquityBridge(verbose=True)
        bridges = bridge.build_for_dcf(ticker, dcf)

        print(f"\n{'─'*60}")
        print("SCENARIO COMPARISON")
        print(f"{'─'*60}")
        for name, b in bridges.items():
            print(f"  {name.upper():4s}: IV=${b.intrinsic_per_share:,.0f}"
                  f"  MoS={b.margin_of_safety:+.1%}"
                  f"  EqVal=${b.equity_value/1e9:.0f}B")
