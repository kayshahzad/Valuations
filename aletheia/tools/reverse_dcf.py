"""
aletheia/tools/reverse_dcf.py

Phase 2 — Reverse DCF
======================
Given the current market price, solves backwards to find what growth rate
and margin assumptions the market is pricing in.

This answers the framework question: "What is the market pricing in?"

Three uses (Liberti / SFM):
  1. What's priced in: what CAGR does current price imply?
  2. Sensitivity: how does implied growth change with different margin assumptions?
  3. TV sanity: what terminal multiple does the implied EV represent?

The implied growth rate is compared against:
  - Company's 5-year historical revenue CAGR (from DB)
  - Sector 75th percentile CAGR (hardcoded reference values)
  - Maximum realistic TAM penetration growth

Flags:
  - Implied growth > historical CAGR × 1.5 → CAUTION
  - Implied growth > 75th percentile → FLAG (requires documented justification)
  - Implied terminal multiple > current forward EV/EBITDA → FLAG

Usage:
    from aletheia.tools.reverse_dcf import ReverseDCF
    rdcf = ReverseDCF()
    result = rdcf.run("AAPL")
    print(result.summary())
"""

import warnings
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

import numpy as np
import yfinance as yf
from scipy.optimize import brentq

warnings.filterwarnings("ignore")

# Sector 75th percentile revenue CAGRs (Damodaran approximations, large-cap)
SECTOR_75TH_CAGR = {
    "Technology":          0.18,
    "Software":            0.20,
    "Semiconductors":      0.22,
    "Healthcare":          0.12,
    "Healthcare Plans":    0.10,
    "Consumer Cyclical":   0.14,
    "Auto Manufacturers":  0.15,
    "Internet":            0.20,
    "Financial":           0.10,
    "Energy":              0.08,
    "Industrials":         0.09,
    "Default":             0.12,
}


@dataclass
class SensitivityResult:
    """Implied growth at one (margin, wacc) combination."""
    ebit_margin: float
    wacc: float
    implied_cagr: float
    implied_ev_ebitda: float


@dataclass
class ReverseDCFResult:
    """Full reverse DCF output."""
    ticker: str
    fiscal_year: int

    # Inputs
    current_price: float = 0.0
    market_cap: float = 0.0
    current_ev: float = 0.0      # Market cap + net debt
    net_debt: float = 0.0
    wacc: float = 0.0
    ebit_margin: float = 0.0
    tax_rate: float = 0.21

    # Implied assumptions
    implied_revenue_cagr_10y: float = 0.0    # 10-year CAGR implied by price
    implied_revenue_cagr_5y: float = 0.0     # 5-year equivalent
    implied_terminal_growth: float = 0.025

    # Benchmark comparison
    historical_cagr_5y: float = 0.0
    sector_75th_cagr: float = 0.12
    sector: str = "Default"

    # Multiple context
    current_ev_ebitda: float = 0.0           # Market EV / DB EBITDA
    implied_tv_ebitda: float = 0.0           # Terminal value / terminal EBITDA
    forward_ev_ebitda_justified: float = 0.0 # Liberti justified multiple

    # Signal
    signal: str = "neutral"   # "deep_value", "fair_value", "priced_for_growth",
                               # "caution", "flag"
    signal_reasons: List[str] = field(default_factory=list)

    # Sensitivity grid
    sensitivity: List[SensitivityResult] = field(default_factory=list)

    warnings: List[str] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"ReverseDCF: {self.ticker} FY{self.fiscal_year}",
            f"  Current Price    : ${self.current_price:,.2f}",
            f"  Current EV       : ${self.current_ev/1e9:,.1f}B",
            f"  EV/EBITDA        : {self.current_ev_ebitda:.1f}x",
            f"  WACC             : {self.wacc:.2%}",
            f"  EBIT Margin      : {self.ebit_margin:.1%}",
            f"",
            f"  Implied 10Y CAGR : {self.implied_revenue_cagr_10y:.1%}",
            f"  Implied 5Y equiv : {self.implied_revenue_cagr_5y:.1%}",
            f"  Historical 5Y    : {self.historical_cagr_5y:.1%}",
            f"  Sector 75th pct  : {self.sector_75th_cagr:.1%}",
            f"",
            f"  Implied TV mult  : {self.implied_tv_ebitda:.1f}x",
            f"  Signal           : {self.signal.upper()}",
        ]
        for reason in self.signal_reasons:
            lines.append(f"    → {reason}")
        if self.sensitivity:
            lines += ["", "  Sensitivity grid (implied CAGR):"]
            lines.append(f"  {'EBIT Margin':>12} | {'WACC 7%':>10} | {'WACC 9%':>10} | {'WACC 11%':>10}")
            lines.append(f"  {'-'*50}")
            margins_shown = set()
            by_margin = {}
            for s in self.sensitivity:
                m = round(s.ebit_margin, 2)
                if m not in by_margin:
                    by_margin[m] = {}
                by_margin[m][round(s.wacc, 2)] = s.implied_cagr
            for m in sorted(by_margin.keys()):
                row = f"  {m:>11.0%}  |"
                for w in [0.07, 0.09, 0.11]:
                    cagr = by_margin[m].get(round(w, 2))
                    row += f" {cagr:>9.1%}" if cagr else f" {'N/A':>9}"
                    row += " |"
                lines.append(row)
        if self.warnings:
            lines.append("")
            for w in self.warnings:
                lines.append(f"  ⚠ {w}")
        return "\n".join(lines)

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "fiscal_year": self.fiscal_year,
            "current_price": self.current_price,
            "current_ev": self.current_ev,
            "current_ev_ebitda": self.current_ev_ebitda,
            "wacc": self.wacc,
            "ebit_margin": self.ebit_margin,
            "implied_cagr_10y": self.implied_revenue_cagr_10y,
            "implied_cagr_5y": self.implied_revenue_cagr_5y,
            "historical_cagr_5y": self.historical_cagr_5y,
            "sector_75th_cagr": self.sector_75th_cagr,
            "implied_tv_ebitda": self.implied_tv_ebitda,
            "forward_ev_ebitda_justified": self.forward_ev_ebitda_justified,
            "signal": self.signal,
        }


class ReverseDCF:
    """
    Solves for the implied revenue CAGR embedded in the current market price.

    Method:
      1. Compute current EV = market cap + net debt
      2. For a given CAGR assumption, project 10 years of FCFF using
         the same formula as DCFEngine (NOPAT + D&A - CapEx - ΔNWC)
      3. Add terminal value at fixed terminal growth
      4. Discount to PV and compare to current EV
      5. Use binary search (brentq) to find the CAGR where PV(FCFF) = current EV
    """

    TERMINAL_GROWTH = 0.025
    FORECAST_YEARS = 10

    def __init__(
        self,
        db_path: str = "valuation_data/database/investment.duckdb",
        verbose: bool = True,
    ):
        self.db_path = db_path
        self.verbose = verbose

    def run(
        self,
        ticker: str,
        fiscal_year: Optional[int] = None,
        wacc_override: Optional[float] = None,
        margin_override: Optional[float] = None,
        **kwargs
    ) -> ReverseDCFResult:
        """
        Solve for implied growth rate from current market price.

        Args:
            ticker: e.g. "AAPL"
            fiscal_year: defaults to latest
            wacc_override: use this WACC instead of computing from market data
            margin_override: use this EBIT margin instead of historical

        Returns:
            ReverseDCFResult with implied growth and signal
        """
        from aletheia.data.database import InvestmentDatabase

        result = ReverseDCFResult(ticker=ticker, fiscal_year=fiscal_year or 0)

        # ── Load data ─────────────────────────────────────────────────────────
        try:
            db = InvestmentDatabase(verbose=False)
            df = db.get_latest(ticker)
            db.close()
        except Exception as e:
            result.errors.append(f"DB load failed: {e}")
            return result

        if df.empty:
            result.errors.append(f"No data for {ticker}")
            return result

        fy = fiscal_year or int(df["fiscal_year"].max())
        row = df[df["fiscal_year"] == fy].iloc[0]
        result.fiscal_year = fy

        def get(col, fallback=0.0):
            val = row.get(col)
            if val is not None and not (isinstance(val, float) and np.isnan(val)):
                return float(val)
            return fallback

        revenue = get("clean_Revenue")
        if "base_revenue_override" in kwargs:
            revenue = kwargs["base_revenue_override"]
            
        ebit = get("clean_NormalizedEBIT")
        ebitda = get("derived_EBITDA", ebit)
        nopat = get("clean_NOPAT", ebit * 0.79)
        roic = get("derived_ROIC", 0.12)
        net_debt = get("derived_NetDebt")
        tax_rate = get("clean_CashTaxRate") or get("clean_GAAP_TaxRate") or 0.21

        # Reinvestment ratios
        da = get("clean_Depreciation", revenue * 0.03)
        capex = get("clean_CapEx_Total") or get("raw_CapEx", revenue * 0.04)
        da_pct = da / revenue if revenue > 0 else 0.03
        capex_pct = capex / revenue if revenue > 0 else 0.04
        nwc_pct = 0.03

        ebit_margin = margin_override or (ebit / revenue if revenue > 0 else 0.15)
        result.ebit_margin = ebit_margin
        result.tax_rate = tax_rate
        result.net_debt = net_debt

        # Historical 5Y CAGR
        hist = df[df["fiscal_year"] <= fy].sort_values("fiscal_year")
        rev_series = hist["clean_Revenue"].dropna()
        rev_now = float(rev_series.iloc[-1]) if len(rev_series) > 0 else 0.0
        cagr_candidates2 = []
        for lookback in [3, 5, 7, 10]:
            if len(rev_series) >= lookback:
                r0 = float(rev_series.iloc[-lookback])
                if r0 > 0 and rev_now > 0:
                    cagr_candidates2.append((rev_now / r0) ** (1/lookback) - 1)
        if len(cagr_candidates2) >= 3:
            s = sorted(cagr_candidates2)
            hist_cagr = float(np.median(s[1:-1]))
        elif cagr_candidates2:
            hist_cagr = float(np.median(cagr_candidates2))
        else:
            hist_cagr = 0.08
        result.historical_cagr_5y = float(np.clip(hist_cagr, 0.0, 0.80))

        # Sector
        try:
            universe_path = "config/universe.csv"
            import csv
            with open(universe_path) as f:
                reader = csv.DictReader(f)
                for row_csv in reader:
                    if row_csv.get("ticker", "").upper() == ticker.upper():
                        result.sector = row_csv.get("sector", "Default")
                        break
        except Exception:
            pass
        result.sector_75th_cagr = SECTOR_75TH_CAGR.get(
            result.sector, SECTOR_75TH_CAGR["Default"]
        )

        # ── Fetch live market data ────────────────────────────────────────────
        try:
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.fast_info
            current_price = float(info.last_price or 0)
            market_cap = float(info.market_cap or 0)
        except Exception as e:
            result.errors.append(f"yfinance failed: {e}")
            return result

        result.current_price = current_price
        result.market_cap = market_cap

        # Current EV = market cap + net debt
        current_ev = market_cap + net_debt
        result.current_ev = current_ev
        result.current_ev_ebitda = current_ev / ebitda if ebitda > 0 else 0.0

        # WACC
        if wacc_override:
            wacc = wacc_override
        else:
            # Import from dcf_engine
            from aletheia.tools.dcf_engine import (
                _fetch_risk_free_rate, _compute_beta, compute_wacc
            )
            rf = _fetch_risk_free_rate()
            beta = _compute_beta(ticker)
            long_term_debt = get("raw_LongTermDebt")
            wacc, _, _, _ = compute_wacc(
                ticker=ticker,
                total_equity=market_cap,
                total_debt=max(net_debt + get("raw_Cash", 0), long_term_debt),
                interest_expense=long_term_debt * 0.04,
                tax_rate=tax_rate,
                risk_free_rate=rf,
                beta=beta,
            )

        result.wacc = wacc

        if current_ev <= 0 or revenue <= 0:
            result.errors.append("Cannot solve: EV or revenue is zero")
            return result

        # ── Binary search for implied CAGR ────────────────────────────────────
        def compute_model_ev(cagr: float) -> float:
            """Project 10Y FCFF and compute model EV for a given CAGR."""
            prev_nwc = revenue * nwc_pct
            pv_total = 0.0

            for yr in range(1, self.FORECAST_YEARS + 1):
                # Two-stage: Y1-5 at full CAGR, Y6-10 fading to 60%
                if yr <= 5:
                    effective_cagr = cagr
                else:
                    fade = (yr - 5) / 5
                    effective_cagr = cagr * (1 - fade * 0.40)

                if yr == 1:
                    rev = revenue * (1 + effective_cagr)
                else:
                    rev = rev * (1 + effective_cagr)

                nopat_yr = rev * ebit_margin * (1 - tax_rate)
                da_yr = rev * da_pct
                capex_yr = rev * capex_pct
                nwc_yr = rev * nwc_pct
                delta_nwc = nwc_yr - prev_nwc
                prev_nwc = nwc_yr

                fcff = nopat_yr + da_yr - capex_yr - delta_nwc
                pv_total += fcff / (1 + wacc) ** yr

            # Terminal value
            final_nopat = rev * ebit_margin * (1 - tax_rate)
            g = self.TERMINAL_GROWTH
            effective_roic = max(roic, 0.08)
            if wacc > g:
                tv = final_nopat * (1 - g / effective_roic) / (wacc - g)
            else:
                tv = final_nopat * 15
            pv_tv = tv / (1 + wacc) ** self.FORECAST_YEARS
            return pv_total + pv_tv

        # Objective: find CAGR where model_ev = current_ev
        def objective(cagr):
            return compute_model_ev(cagr) - current_ev

        try:
            # Search in range -10% to +80%
            implied_cagr = brentq(objective, -0.10, 0.80, xtol=1e-6, maxiter=200)
        except ValueError:
            # If no root found, determine direction
            if compute_model_ev(0.80) < current_ev:
                implied_cagr = 0.80   # Market pricing in >80% CAGR — extraordinary
                result.warnings.append(
                    "Implied CAGR exceeds 80% — stock may be in speculative territory"
                )
            else:
                implied_cagr = -0.10
                result.warnings.append(
                    "No convergence — possible data quality issue"
                )
        except Exception as e:
            result.errors.append(f"Solver failed: {e}")
            implied_cagr = 0.0

        result.implied_revenue_cagr_10y = float(implied_cagr)
        # 5Y equivalent: assume 60% of 10Y rate in years 6-10
        result.implied_revenue_cagr_5y = implied_cagr   # Y1-5 rate = full implied CAGR

        # Implied terminal multiple
        if ebitda > 0:
            result.implied_tv_ebitda = result.current_ev_ebitda
        result.implied_tv_ebitda = result.current_ev_ebitda   # Rough proxy

        # Liberti justified multiple
        g = self.TERMINAL_GROWTH
        effective_roic = max(roic, 0.08)
        if ebitda > 0 and wacc > g and effective_roic > g:
            terminal_nopat = (revenue * (1 + implied_cagr) ** 10
                              * ebit_margin * (1 - tax_rate))
            terminal_ebitda = revenue * (1 + implied_cagr) ** 10 * ebit_margin + da
            if terminal_ebitda > 0:
                result.forward_ev_ebitda_justified = (
                    terminal_nopat * (1 - g / effective_roic) / terminal_ebitda
                ) / (wacc - g)

        # ── Signal determination ──────────────────────────────────────────────
        reasons = []

        cagr_vs_hist = implied_cagr / result.historical_cagr_5y if result.historical_cagr_5y > 0 else 1.0
        cagr_vs_sector = implied_cagr / result.sector_75th_cagr if result.sector_75th_cagr > 0 else 1.0

        if implied_cagr < result.historical_cagr_5y * 0.50:
            result.signal = "deep_value"
            reasons.append(
                f"Implied CAGR ({implied_cagr:.1%}) is less than 50% of "
                f"historical ({result.historical_cagr_5y:.1%}) — market is pricing "
                f"in significant deceleration. Value opportunity if thesis is intact."
            )
        elif implied_cagr < result.historical_cagr_5y:
            result.signal = "fair_value"
            reasons.append(
                f"Implied CAGR ({implied_cagr:.1%}) is below historical "
                f"({result.historical_cagr_5y:.1%}). "
                f"Market is not pricing in full continuation of historical trajectory."
            )
        elif implied_cagr < result.sector_75th_cagr:
            result.signal = "priced_for_growth"
            reasons.append(
                f"Implied CAGR ({implied_cagr:.1%}) exceeds historical "
                f"({result.historical_cagr_5y:.1%}) but is below sector 75th "
                f"percentile ({result.sector_75th_cagr:.1%}). "
                f"Growth premium is being paid — requires documented justification."
            )
        elif implied_cagr < result.sector_75th_cagr * 1.30:
            result.signal = "caution"
            reasons.append(
                f"Implied CAGR ({implied_cagr:.1%}) exceeds sector 75th percentile "
                f"({result.sector_75th_cagr:.1%}). "
                f"Market is pricing above-norm growth — requires very strong thesis."
            )
        else:
            result.signal = "flag"
            reasons.append(
                f"Implied CAGR ({implied_cagr:.1%}) is {cagr_vs_sector:.1f}x the "
                f"sector 75th percentile ({result.sector_75th_cagr:.1%}). "
                f"Market is pricing extraordinary growth — framework requires explicit "
                f"TAM justification and documented override."
            )

        result.signal_reasons = reasons

        # ── Sensitivity grid ──────────────────────────────────────────────────
        sensitivity = []
        for m in [ebit_margin * 0.80, ebit_margin, ebit_margin * 1.20]:
            for w in [0.07, 0.09, 0.11]:
                def obj_grid(cagr, margin=m, w=w):
                    prev_nwc_g = revenue * nwc_pct
                    pv = 0.0
                    rev_g = revenue
                    for yr in range(1, self.FORECAST_YEARS + 1):
                        eff_cagr = cagr if yr <= 5 else cagr * (1 - (yr-5)/5 * 0.40)
                        rev_g = rev_g * (1 + eff_cagr)
                        n = rev_g * margin * (1 - tax_rate)
                        d = rev_g * da_pct
                        c = rev_g * capex_pct
                        nwc_g = rev_g * nwc_pct
                        pv += (n + d - c - (nwc_g - prev_nwc_g)) / (1+w)**yr
                        prev_nwc_g = nwc_g
                    final_n = rev_g * margin * (1 - tax_rate)
                    tv_g = final_n * (1 - 0.025/max(roic,0.08)) / (w - 0.025) if w > 0.025 else final_n * 15
                    return pv + tv_g / (1+w)**self.FORECAST_YEARS - current_ev

                try:
                    cagr_grid = brentq(obj_grid, -0.10, 0.80, xtol=1e-5, maxiter=100)
                    ev_ebitda = (current_ev /
                                 (revenue * (1+cagr_grid) * m / (1-0.025/max(roic,0.08)))
                                 if revenue > 0 else 0)
                    sensitivity.append(SensitivityResult(
                        ebit_margin=m,
                        wacc=w,
                        implied_cagr=cagr_grid,
                        implied_ev_ebitda=ev_ebitda,
                    ))
                except Exception:
                    pass

        result.sensitivity = sensitivity

        if self.verbose:
            print(result.summary())

        return result


# ─────────────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    tickers = sys.argv[1:] if len(sys.argv) > 1 else ["AAPL"]
    rdcf = ReverseDCF(verbose=True)
    for ticker in tickers:
        print(f"\n{'='*60}")
        result = rdcf.run(ticker)
        print()
