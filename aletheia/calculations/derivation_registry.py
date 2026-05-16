"""Layer 2 — Derivation Registry: documented methodology for every
Stage 3 derived value.

The financial-statement encoding model has three layers (see
docs/layer2_derivation_registry_predictions_2026-05-14.md):

  L1: Structural identities — laws of accounting, hard assertions
  L2: Derivational relationships — methodology-bearing formulas (THIS MODULE)
  L3: Period-over-period flows — roll-forward functions

L2 is the layer where methodology choices live: which CapEx? which tax
rate? which FCF definition? Two analysts looking at the same filing can
legitimately compute different FCFs depending on methodology. The
registry catalogues each derived value with its inputs, formula,
methodology citation, alternates, and known divergence from FMP.

Usage:
    from aletheia.calculations.derivation_registry import (
        DERIVATIONS, lookup, lookup_by_label,
    )
    entry = lookup("FCF")              # canonical-name lookup
    entry = lookup_by_label("EV/EBITDA")  # display-label lookup

Each entry surfaces in the Stage 3 Pipeline Explorer panel as a
"How was this derived?" expander. When an analyst sees "FCF +108% drift
vs FMP", the registry tells them: "we use Liberti-method FCF (post-SBC
add-back); FMP uses freeCashFlowToFirmTTM (different formula).
Category D — methodology divergence, not a bug."
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class DerivationEntry:
    """One derived-value catalog entry.

    Fields:
        name:            Canonical name (matches bundle key).
        label:           Human-readable label (matches UI display label).
        category:        DCF | RDCF | MultDec | Screen | Cleaning | Moat | Cyclic
        formula:         Plain-language expression of the arithmetic.
        inputs:          List of input field names.
        methodology:     Citation or in-house policy reference for the
                         specific methodology choice.
        alternates:      Other valid methodologies with brief contrast.
                         Empty list when this is the unambiguous canonical.
        fmp_equivalent:  Mapped FMP field name + brief note on
                         divergence. None when FMP has no equivalent.
        category_d:      True when this entry's value is expected to
                         diverge from FMP due to methodology choice (not
                         a bug). Surfaced as a warning chip in the UI.
    """
    name: str
    label: str
    category: str
    formula: str
    inputs: List[str]
    methodology: str
    alternates: List[str] = field(default_factory=list)
    fmp_equivalent: Optional[str] = None
    category_d: bool = False


# ─────────────────────────────────────────────────────────────────────
# Registry — 30+ entries covering Stage 3 derivations
# ─────────────────────────────────────────────────────────────────────

DERIVATIONS: List[DerivationEntry] = [
    # ── DCF core derivations ───────────────────────────────────────
    DerivationEntry(
        name="wacc_base",
        label="WACC",
        category="DCF",
        formula="(E/V × Ke) + (D/V × Kd × (1 − tax_rate))",
        inputs=["market_cap", "TotalDebt", "beta", "risk_free_rate",
                "equity_risk_premium", "tax_rate"],
        methodology=(
            "Standard CAPM-based WACC. Ke = Rf + β × ERP. Kd from "
            "interest expense / avg total debt. Floor at 6%, cap at 18% "
            "to bound the discount rate; per-scenario overrides allowed."
        ),
        alternates=[
            "Damodaran sector WACC (industry-median bottom-up beta)",
            "Implied WACC from current market price (used in Reverse DCF)",
        ],
        fmp_equivalent=None,
    ),
    DerivationEntry(
        name="nopat",
        label="NOPAT",
        category="DCF",
        formula="NormalizedEBIT × (1 − tax_rate)",
        inputs=["NormalizedEBIT", "tax_rate"],
        methodology=(
            "EBIT-based NOPAT using A11-resolved tax rate (cash → gaap "
            "→ company_fy → statutory fallback). NormalizedEBIT adds "
            "back non-recurring items per cleaning engine domain 1."
        ),
        alternates=[
            "Raw EBIT × statutory rate (simpler, ignores deferred tax)",
            "Damodaran NOPAT (uses effective tax rate, not cash)",
        ],
    ),
    DerivationEntry(
        name="roic",
        label="ROIC",
        category="DCF",
        formula="NOPAT / InvestedCapital",
        inputs=["nopat", "TotalEquity", "TotalDebt", "Cash"],
        methodology=(
            "**Platform**: IC = TotalEquity + TotalDebt − Cash (book "
            "invested capital, Damodaran convention, cash netted). "
            "Rationale: matches the engines' WACC weighting (cash is "
            "non-operating; including it would double-count the equity "
            "buffer). End-of-period balance sheet values. "
            "**Worked example (AAPL TTM Q2 FY26)**: NOPAT $122.32B; "
            "IC = $106.5B + $84.7B − $36.3B = $154.9B → "
            "ROIC = 78.98%."
        ),
        alternates=[
            "FMP returnOnInvestedCapitalTTM — back-calculated implies IC "
            "≈ $247B for AAPL (NOPAT $122.32B / 49.57% ROIC). ~$92B "
            "above our $154.9B; FMP's formula is opaque but likely "
            "uses operating-IC (NWC + Net PP&E + Goodwill + leases) "
            "without netting cash. Their published investedCapitalTTM "
            "field ($80.9B) DOES NOT match their own ROIC denominator — "
            "FMP-internal inconsistency.",
            "McKinsey IC: NWC + Net PP&E + Goodwill + capitalized "
            "operating leases. Excludes excess cash AND financial "
            "investments. Used when ROIC is meant to measure operating "
            "performance only.",
            "Damodaran IC (book) without cash netting: TotalEquity + "
            "TotalDebt. Used in academic comparisons; sensitive to "
            "cash-rich firms.",
        ],
        fmp_equivalent=(
            "returnOnInvestedCapitalTTM — drift +50-60% on AAPL "
            "(78.98% ours vs 49.57% FMP). Documented Category-D "
            "methodology divergence."
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="fcf",
        label="FCF",
        category="DCF",
        formula="OperatingCF − abs(CapEx)",
        inputs=["OperatingCF", "CapEx_Total"],
        methodology=(
            "Damodaran FCFF definition: OCF minus signed-positive CapEx. "
            "Used by DCFEngine + ReverseDCF for projection. Excludes "
            "lease-related cash flows (we treat ASC 842 leases as "
            "non-debt for FCF purposes)."
        ),
        alternates=[
            "Liberti FCF (post-SBC add-back) — used by cleaning engine "
            "FCF canonical field. Adds SBC back as a non-cash item, "
            "$10-20B more for SBC-heavy filers (META, AAPL, GOOGL).",
            "FMP freeCashFlowToFirmTTM — computed against a different "
            "OCF base; typically 50-100% LOWER than our FCF for "
            "SBC-heavy filers",
        ],
        fmp_equivalent=(
            "freeCashFlowToFirmTTM — drift of +50% to +100% on tech "
            "filers is methodology divergence (different OCF base + "
            "different SBC treatment), not a bug"
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="base_intrinsic_per_share",
        label="IV per share (base)",
        category="DCF",
        formula=(
            "(Σ FCF_projected_t / (1+wacc)^t + TerminalValue / (1+wacc)^N "
            "− NetDebt) / SharesDiluted"
        ),
        # NB: fcf_projections is an in-flight intermediate (10y FCF
        # series the DCFEngine projects forward). Not exposed on the
        # bundle in V3; analyst sees individual fcf, wacc, terminal_value
        # values that produced it. V4 would emit the per-year series.
        inputs=["fcf", "wacc", "terminal_value", "net_debt",
                "shares_diluted"],
        methodology=(
            "DCFEngine base scenario. Forecast horizon = 10y "
            "(LIFECYCLE_PROFILES tuned per lifecycle). Terminal value "
            "uses Gordon growth with terminal_growth_cap by lifecycle."
        ),
        alternates=[
            "Two-stage DCF (5y high-growth + Gordon terminal)",
            "Exit-multiple terminal value (NTM EBITDA × sector multiple)",
        ],
        fmp_equivalent=(
            "FMP's /discounted-cash-flow endpoint — uses a simpler "
            "model, not cached locally"
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="net_debt",
        label="Net debt",
        category="DCF",
        formula="TotalDebt − CashAndEquivalents − ShortTermInvestments",
        inputs=["LongTermDebt", "ShortTermDebt", "Cash", "ShortTermInvestments"],
        methodology=(
            "**Platform**: broad-cash basket — gross debt minus "
            "(cash + cash equivalents + short-term investments). "
            "Rationale: liquid short-term investments (Treasury bills, "
            "money-market positions) are economically substitutable for "
            "cash and could service debt on demand. Damodaran convention. "
            "Lease liabilities EXCLUDED from the debt side (matches our "
            "TotalDebt input which equals LongTermDebt + ShortTermDebt; "
            "operating leases live in a separate liability bucket). "
            "**Worked example (AAPL TTM Q2 FY26)**: TotalDebt $84.7B "
            "− Cash $36.3B − STInv $32.2B = NetDebt $16.2B."
        ),
        alternates=[
            "FMP netDebtToEBITDATTM uses **narrow cash** "
            "(cashAndCashEquivalents only, NOT short-term investments). "
            "Back-calc (AAPL): implied NetDebt = 0.302 × EBITDA $160.3B "
            "= $48.4B. Matches TotalDebt $84.7B − Cash $36.3B = $48.4B "
            "(NO STI subtraction). Empirically validated; drift on AAPL "
            "is ~$32B = exactly the STI line. Drift scales with the "
            "size of a firm's marketable-security portfolio.",
            "Strict accounting net debt: TotalDebt − Cash only. Used "
            "by ratings agencies (Moody's, S&P) — they treat STI as "
            "less liquid than cash for stress scenarios.",
            "Enterprise-value net debt (lease-inclusive): "
            "TotalDebt + capitalised OperatingLease liability − Cash. "
            "Used in EV/EBITDA where leases should burden the multiple.",
        ],
        fmp_equivalent=(
            "netDebtToEBITDATTM — drift -65 to -70% on AAPL (0.10 ours "
            "vs 0.30 FMP) because FMP excludes ShortTermInvestments "
            "from the cash netting. Documented Category-D methodology "
            "divergence; magnitude depends on STI portfolio size."
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="roe",
        label="ROE",
        category="Screen",
        formula="NetIncome / TotalEquity",
        inputs=["NetIncome", "TotalEquity"],
        methodology=(
            "**Platform**: end-of-period TotalStockholdersEquity in the "
            "denominator. TTM NetIncome in the numerator (rolled-forward "
            "via quarterly sum). Rationale: end-of-period is the most "
            "recent observable equity base; for buyback-heavy filers "
            "(AAPL, MSFT) this is the conservative choice — large "
            "buybacks shrink equity faster than NI accrues, so EOP "
            "equity is smaller, producing a higher reported ROE. "
            "**Worked example (AAPL TTM Q2 FY26)**: NI_TTM $122.575B / "
            "EOP equity $106.491B = 115.10%."
        ),
        alternates=[
            "FMP returnOnEquityTTM uses **average of last 4 "
            "quarter-end equities** in the denominator. Back-calc (AAPL): "
            "FMP ROE 146.69% → implied equity = NI_TTM $122.575B / 1.4669 "
            "= $83.561B. Average of (Q3'25 $73.7B, Q4'25 $88.2B "
            "— wait we don't have that snapshot. Listed quarters: "
            "Q3'25 $65.8B, Q4'25 $73.7B, Q1'26 $88.2B, Q2'26 $106.5B) "
            "= ($65.8 + $73.7 + $88.2 + $106.5) / 4 = $83.55B. "
            "Drift vs implied: 0.0% (exact match — methodology "
            "confirmed). Drift on AAPL: -21.5% (115.10% ours vs "
            "146.69% FMP) because AAPL's equity grew $40B over 4 "
            "quarters; averaging produces a smaller denominator.",
            "Beginning-of-period equity (Q3'25): $65.8B → 186%. Used "
            "in DuPont decomposition when matching to opening-balance "
            "metrics.",
            "Tangible equity (excludes goodwill + intangibles): "
            "appropriate for asset-heavy filers but not AAPL.",
        ],
        fmp_equivalent=(
            "returnOnEquityTTM (in key_metrics_ttm) — drift -15 to -25% "
            "on growing-equity filers; drift +15 to +25% on "
            "shrinking-equity filers. Documented Category-D methodology "
            "divergence (equity period convention)."
        ),
        category_d=True,
    ),

    # ── Reverse DCF ────────────────────────────────────────────────
    DerivationEntry(
        name="implied_cagr_10y",
        label="Implied 10Y CAGR",
        category="RDCF",
        formula=(
            "Revenue-CAGR that solves: ΣDCF + TerminalValue = current_EV"
        ),
        inputs=["current_ev", "ebit_margin", "wacc", "tax_rate"],
        methodology=(
            "Numerical root-find. Locks margin + WACC + terminal-g at "
            "lifecycle defaults; solves for the 10y revenue CAGR that "
            "the current market price implies."
        ),
        alternates=[
            "Implied EBIT-margin (alternative: fix CAGR, solve for "
            "margin) — used by some sell-side analysts",
        ],
        fmp_equivalent=None,
    ),
    DerivationEntry(
        name="current_ev_ebitda",
        label="Current EV / EBITDA",
        category="RDCF",
        formula="EnterpriseValue / EBITDA",
        inputs=["enterprise_value", "ebitda"],
        methodology=(
            "EV = market cap + net debt (Damodaran definition). EBITDA "
            "from NormalizedEBIT + Depreciation_Total."
        ),
        alternates=[
            "FMP evToEBITDATTM (~5% lower — they net cash differently)",
        ],
        fmp_equivalent=(
            "evToEBITDATTM — minor divergence (~5%) from EV "
            "cash-netting + minority-interest treatment"
        ),
        category_d=True,
    ),

    # ── Multiple Decomposition ─────────────────────────────────────
    DerivationEntry(
        name="market_p_e",
        label="Market P/E",
        category="MultDec",
        formula="market_cap / NetIncome (latest FY)",
        inputs=["market_cap", "NetIncome"],
        methodology=(
            "FY-anchored P/E. Numerator = diluted shares × latest "
            "price. Denominator = latest-FY NetIncome (NOT TTM)."
        ),
        alternates=[
            "TTM P/E (FMP convention): latest 4 quarters' NI",
            "Forward P/E: next-FY consensus NI",
        ],
        fmp_equivalent=(
            "priceToEarningsRatioTTM — typically 15-20% LOWER because "
            "TTM NI > prior-FY NI in growth years"
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="market_p_sales",
        label="Market P/Sales",
        category="MultDec",
        formula="market_cap / Revenue (latest FY)",
        inputs=["market_cap", "Revenue"],
        methodology=(
            "FY-anchored P/Sales. Numerator = diluted shares × latest "
            "price. Denominator = latest-FY Revenue (NOT TTM)."
        ),
        alternates=[
            "TTM P/Sales (FMP priceToSalesRatioTTM)",
        ],
        fmp_equivalent=(
            "priceToSalesRatioTTM — typically 5-10% drift from "
            "FY-vs-TTM revenue base"
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="market_ev_ebitda",
        label="Market EV/EBITDA",
        category="MultDec",
        formula="EnterpriseValue / EBITDA (Multiple Decomposition)",
        inputs=["enterprise_value", "ebitda"],
        methodology=(
            "Same numerator/denominator as the RDCF 'Current EV/EBITDA' "
            "but computed inside MultipleDecomposition for comparison "
            "against the justified multiple. Minor (~5%) drift vs "
            "FMP from EV cash-netting + minority-interest treatment."
        ),
        alternates=[
            "FMP evToEBITDATTM (typically ~5% lower)",
        ],
        fmp_equivalent=(
            "evToEBITDATTM — minor divergence (~5%) from EV "
            "cash-netting + minority-interest treatment"
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="market_peg",
        label="Market PEG",
        category="MultDec",
        formula="P/E ÷ EPS_growth_rate",
        inputs=["market_p_e", "eps_growth_rate"],
        methodology=(
            "EPS_growth = robust 5y CAGR (winsorized at 1st/99th "
            "percentile). PEG > 2 considered expensive."
        ),
        alternates=[
            "Forward PEG (next-12m consensus growth)",
        ],
    ),
    DerivationEntry(
        name="justified_ev_ebitda",
        label="Justified EV/EBITDA",
        category="MultDec",
        formula="(1 − g/ROIC) / (WACC − g) × (1 − tax_rate) × EBIT/EBITDA",
        inputs=["growth_rate", "roic", "wacc", "tax_rate", "ebit_margin"],
        methodology=(
            "Damodaran's justified-multiple formula. Decomposes the "
            "current market multiple into growth + quality + cost-of-"
            "capital components."
        ),
        alternates=[],
    ),
    DerivationEntry(
        name="ev_ebitda_premium_pct",
        label="EV/EBITDA premium %",
        category="MultDec",
        formula="(market_ev_ebitda − justified_ev_ebitda) / justified_ev_ebitda",
        inputs=["market_ev_ebitda", "justified_ev_ebitda"],
        methodology=(
            "Positive = market trades above quality/growth/WACC-justified "
            "multiple; negative = discount."
        ),
        alternates=[],
    ),

    # ── Screening ──────────────────────────────────────────────────
    DerivationEntry(
        name="screening_p_e",
        label="P/E",
        category="Screen",
        formula="current_price / EPS_diluted (latest FY)",
        inputs=["current_price", "DilutedEPS"],
        methodology=(
            "FY-anchored P/E using per-share EPS rather than market_cap / NI. "
            "May diverge slightly from Multiple Decomposition's P/E due to "
            "rounding in EPS reporting."
        ),
        alternates=[
            "TTM P/E (FMP convention)",
            "Normalized-EPS P/E (Schiller-style 10y avg)",
        ],
        fmp_equivalent=(
            "priceToEarningsRatioTTM — drift typical 15-20% from "
            "FY-vs-TTM source"
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="screening_p_b",
        label="P/B",
        category="Screen",
        formula="market_cap / TotalEquity",
        inputs=["market_cap", "TotalEquity"],
        methodology=(
            "**Platform**: end-of-period TotalStockholdersEquity from "
            "the latest available balance sheet (TTM = latest quarterly "
            "BS). Excludes minority interest. Rationale: P/B uses "
            "point-in-time market cap (intraday price × shares), so the "
            "denominator should also be point-in-time, not averaged. "
            "**Worked example (AAPL TTM Q2 FY26)**: market_cap "
            "$4,421B / EOP equity $106.491B = 41.51. "
            "**Equity-averaging question**: ROE-style averaging "
            "(avg of 4 quarter-ends) would yield $83.6B → P/B 52.9 — "
            "but FMP does NOT do this for P/B. FMP's priceToBookRatioTTM "
            "of 41.19 implies their denominator = $106.3B = EOP equity. "
            "Both platforms use EOP for P/B; the residual <2% drift is "
            "market-cap timestamp jitter, NOT an equity convention "
            "divergence. The equity-period-convention divergence surfaces "
            "in ROE only (see `roe` entry)."
        ),
        alternates=[
            "Tangible P/B: market_cap / (TotalEquity − Goodwill − "
            "Intangibles). Used for asset-heavy filers; for software/"
            "hardware with low goodwill (AAPL) it's near-identical.",
            "FMP priceToBookRatioTTM — matches our convention "
            "(end-of-period equity) within <2% on AAPL post-TTM. NOT "
            "a methodology divergence in practice.",
        ],
        fmp_equivalent=(
            "priceToBookRatioTTM — drift <2% on AAPL post-TTM-alignment. "
            "Pre-TTM (FY-anchored) the drift was +48% because our "
            "denominator was buyback-shrunken FY2025 equity ($72B) vs "
            "FMP's most-recent quarterly equity ($106B). Now both use "
            "EOP equity from the latest quarter."
        ),
        category_d=False,
    ),
    DerivationEntry(
        name="screening_peg",
        label="PEG",
        category="Screen",
        formula="P/E ÷ eps_growth_rate",
        inputs=["screening_p_e", "eps_growth_rate"],
        methodology=(
            "Screening engine PEG. EPS growth uses 5y CAGR floor of "
            "1% to avoid spurious negative PEG on flat-growth filers."
        ),
        alternates=[
            "Forward PEG (next-12m consensus growth)",
        ],
    ),
    DerivationEntry(
        name="debt_to_equity",
        label="Debt / Equity",
        category="Screen",
        formula="TotalDebt / TotalEquity",
        inputs=["TotalDebt", "TotalEquity"],
        methodology=(
            "**Platform**: gross TotalDebt (ShortTermDebt + LongTermDebt) "
            "from the latest balance sheet, divided by end-of-period "
            "TotalStockholdersEquity. Rationale: matches the capital-"
            "structure view used in WACC weighting. Operating-lease "
            "liabilities are typically included by FMP within "
            "longTermDebt for filers reporting under ASC 842 (current "
            "US-GAAP) — empirically validated against FMP for AAPL "
            "TTM Q2 FY26 (post-ASC 842). For pre-ASC 842 era or filers "
            "where the lease line is broken out, the included-vs-"
            "excluded debt definition can drift 5-15%. "
            "**Worked example (AAPL TTM Q2 FY26)**: TotalDebt $84.7B "
            "/ EOP equity $106.491B = 0.7956. FMP debtToEquityRatioTTM "
            "= 0.7955 → drift 0.0% (exact match — debt scope confirmed "
            "identical post-ASC 842)."
        ),
        alternates=[
            "Long-term-only debt: LongTermDebt / TotalEquity. "
            "Excludes current-portion-LTD and ST-debt. Used in "
            "leverage-stability analyses (ignores working-capital "
            "facility usage).",
            "Lease-explicit: (TotalDebt + capitalised OperatingLease) "
            "/ TotalEquity. Used for filers with significant ROU "
            "assets where lease economics dwarf financial debt.",
            "Net Debt/Equity: (TotalDebt − Cash) / TotalEquity. "
            "Caters to cash-rich firms (AAPL: would show ~0.45 vs "
            "gross 0.80).",
        ],
        fmp_equivalent=(
            "debtToEquityRatioTTM — drift 0.0% on AAPL post-TTM. Both "
            "platforms include operating leases in TotalDebt (ASC 842). "
            "NOT a methodology divergence post-TTM-alignment; was flagged "
            "as divergent in the FY-anchored analysis due to "
            "buyback-shrunken FY2025 equity in our denominator."
        ),
        category_d=False,
    ),
    DerivationEntry(
        name="screening_ev_ebit",
        label="EV/EBIT (normalized)",
        category="Screen",
        formula="EnterpriseValue / NormalizedEBIT",
        inputs=["enterprise_value", "NormalizedEBIT"],
        methodology=(
            "EBIT normalized for non-recurring items (impairments, "
            "restructuring, litigation). Drift vs raw EV/EBIT can be "
            "material in years with large one-offs."
        ),
        alternates=[
            "Raw EV/EBIT (no normalization)",
        ],
    ),
    DerivationEntry(
        name="screening_ev_ebitda",
        label="EV/EBITDA (clean)",
        category="Screen",
        formula="(market_cap + net_debt) / EBITDA_clean",
        inputs=["market_cap", "net_debt", "ebitda"],
        methodology=(
            "Same numerator as MultDec EV/EBITDA. Denominator uses "
            "EBITDA_clean = OperatingIncome + Depreciation_Total "
            "(no non-recurring add-backs)."
        ),
        alternates=[
            "EV/EBITDA-adjusted (with non-recurring add-backs)",
        ],
        fmp_equivalent=(
            "evToEBITDATTM — minor divergence (~5%) from EV "
            "cash-netting + TTM-vs-FY EBITDA denominator"
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="screening_ev_fcf",
        label="EV/FCF",
        category="Screen",
        formula="EnterpriseValue / FCF",
        inputs=["enterprise_value", "fcf"],
        methodology=(
            "Uses cleaning's FCF canonical (Liberti method, post-SBC "
            "add-back)."
        ),
        alternates=[
            "EV/FCFF (FMP convention) — uses different FCF formula",
        ],
        fmp_equivalent=(
            "evToFreeCashFlowTTM — typically close (~1-5% drift) when "
            "FCF definitions align by accident"
        ),
    ),
    DerivationEntry(
        name="margin_of_safety",
        label="Margin of safety",
        category="Screen",
        formula="(IV_base − current_price) / current_price × 100",
        inputs=["base_intrinsic_per_share", "current_price"],
        methodology=(
            "DCF-based margin of safety. Positive = current price is "
            "BELOW intrinsic (undervalued); negative = OVERvalued. Stored "
            "in raw-percent units (218.14 means +218.14%)."
        ),
        alternates=[
            "Multiple-based MoS (justified − market premium)",
            "Reverse-DCF MoS (implied vs reasonable growth)",
        ],
    ),

    # ── Cleaning engine canonical fields ───────────────────────────
    DerivationEntry(
        name="NormalizedEBIT",
        label="NormalizedEBIT",
        category="Cleaning",
        formula="OperatingIncome + non_recurring_adjustments",
        inputs=["OperatingIncome", "NonRecurring_TotalAdjustment"],
        methodology=(
            "Cleaning engine domain 1. Adds back impairments, "
            "restructuring, litigation, write-downs that don't reflect "
            "ongoing operating earnings power."
        ),
        alternates=[
            "Raw OperatingIncome (no adjustments)",
            "Adjusted EBIT per management presentation",
        ],
        fmp_equivalent=(
            "FMP operatingIncome (raw, no normalization) — material "
            "drift in years with one-off impairments"
        ),
        category_d=True,
    ),
    DerivationEntry(
        name="EBITDA",
        label="EBITDA",
        category="Cleaning",
        formula="NormalizedEBIT + Depreciation_Total",
        inputs=["NormalizedEBIT", "Depreciation_Total"],
        methodology=(
            "EBITDA built from NormalizedEBIT + total D&A (tangible + "
            "intangible amortization)."
        ),
        alternates=[
            "FMP EBITDA (raw OperatingIncome + D&A)",
            "EBITDA-excluding-SBC (Liberti adjustment)",
        ],
    ),
    DerivationEntry(
        name="Depreciation_Total",
        label="D&A (Total)",
        category="Cleaning",
        formula="Depreciation_Tangible + IntangibleAmortization",
        inputs=["Depreciation_Tangible", "IntangibleAmortization"],
        methodology=(
            "Aggregate D&A from cash-flow statement + IS-disclosed "
            "intangible amortization."
        ),
        alternates=[],
    ),
    DerivationEntry(
        name="CapEx_Total",
        label="CapEx (Total)",
        category="Cleaning",
        formula="GrossCapEx + capitalized_software + intangibles_acquired",
        inputs=["CapEx", "capitalized_software", "intangibles_purchased"],
        methodology=(
            "Total capex includes capitalized software (intangible IT) "
            "and intangibles purchased through normal operations. "
            "Excludes acquisitions (those flow through Goodwill)."
        ),
        alternates=[
            "PaymentsToAcquirePropertyPlantAndEquipment (narrow, FMP)",
            "GrossCapEx + R&D (treats R&D as growth CapEx)",
        ],
    ),
    DerivationEntry(
        name="DeltaNWC",
        label="ΔNWC",
        category="Cleaning",
        formula="ΔAR + ΔInventory − ΔAP",
        inputs=["AccountsReceivable", "Inventory", "AccountsPayable"],
        methodology=(
            "Trade working-capital change. Sign convention: positive = "
            "WC grew (cash sink)."
        ),
        alternates=[
            "Structural NWC (excludes acquisition-acquired WC)",
            "Includes deferred revenue + accrued liabilities",
        ],
    ),

    # ── Cyclicality + Moat ─────────────────────────────────────────
    DerivationEntry(
        name="z_score",
        label="Cyclical Z-score",
        category="Cyclic",
        formula="(current_metric − historical_avg) / historical_stddev",
        # Cyclicality engine z-scores REVENUE not EBIT margin. The
        # historical series lives in cyclicality.details.revenues
        # (not resolvable by bare input name; surfaced for analyst via
        # the details payload).
        inputs=["Revenue"],
        methodology=(
            "Cyclicality engine computes z-score of current period's "
            "revenue (or industrial-cyclical proxy) vs the trailing "
            "distribution. |z| > 1.0 → peak/trough; haircut applied "
            "to DCF in peak years. Historical series is in "
            "cyclicality.details.revenues; engine is not yet "
            "instrumented to expose per-period EBIT-margin series "
            "for true margin-based cyclicality."
        ),
        alternates=[],
        fmp_equivalent=None,
    ),
    DerivationEntry(
        name="moat_score",
        label="Moat score",
        category="Moat",
        formula="weighted_sum(roic_persistence + gm_stability + capex_intensity)",
        # Moat engine combines 3 sub-scores already exposed in
        # moat_fingerprint output. The underlying time-series (roic
        # history, gross-margin history) are engine-internal — V4
        # would emit them for full trace fidelity.
        inputs=["roic_persistence_score", "gm_stability_score",
                "capex_intensity_score"],
        methodology=(
            "Moat fingerprint composite. 0-10 score combining three "
            "sub-scores: ROIC persistence (consistency of high "
            "returns), gross-margin stability (low volatility), and "
            "capex intensity (low reinvestment as %FCF). Higher = "
            "wider durable moat (Buffett-style)."
        ),
        alternates=[
            "Morningstar moat rating (narrow/wide/none)",
        ],
        fmp_equivalent=None,
    ),

    # ── Accounting identities (Layer 1 cross-references) ──────────
    DerivationEntry(
        name="balance_sheet_equation",
        label="A = L + E (identity check)",
        category="L1_identity",
        formula="TotalAssets − (TotalLiabilities + TotalEquity)",
        inputs=["TotalAssets", "TotalLiabilities", "TotalEquity"],
        methodology=(
            "Layer 1 structural identity. Should equal 0 within 0.5% "
            "tolerance. Material violations indicate cleaning gap or "
            "NCI exclusion (see balance_sheet_residual_complexity flag)."
        ),
        alternates=[],
    ),
    DerivationEntry(
        name="cash_rollforward",
        label="Cash roll-forward (identity check)",
        category="L1_identity",
        formula="Cash_end − (Cash_beg + OCF + ICF + FCF + FX)",
        inputs=["Cash_beg", "Cash_end", "OperatingCF", "InvestingCF",
                "FinancingCF", "FX_effect"],
        methodology=(
            "Layer 1 structural identity. Uses BROAD cash "
            "(CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents) "
            "per ASU 2016-18 for FY2018+ filers; narrow cash fallback "
            "for older filings."
        ),
        alternates=[],
    ),
    DerivationEntry(
        name="re_rollforward",
        label="RE roll-forward (equity-bridge)",
        category="L3_rollforward",
        formula=(
            "RE_end − (RE_beg + NI − Div − Buybacks − TaxWithhold "
            "+ SBC − ΔAPIC)"
        ),
        inputs=["RetainedEarnings_beg", "RetainedEarnings_end",
                "NetIncome", "DividendsPaid", "Buybacks",
                "TaxWithholding_RSU", "SBC", "APIC_beg", "APIC_end"],
        methodology=(
            "Phase-1.β equity-bridge model. Buybacks + TaxWithhold draw "
            "down APIC first; residual hits RE. SBC credits APIC. "
            "Validated on META/AAPL share-retirement filers."
        ),
        alternates=[
            "Basic formula: RE_beg + NI − Div (fails on buyback filers)",
            "Treasury-method extended (rare in our universe)",
        ],
    ),
    DerivationEntry(
        name="fcf_pathway_b",
        label="FCF Pathway B (NOPAT-based)",
        category="L3_rollforward",
        formula="NOPAT + DA + SBC − abs(CapEx) − ΔNWC",
        inputs=["nopat", "Depreciation_Total", "SBC", "CapEx_Total", "DeltaNWC"],
        methodology=(
            "Extended Pathway B with SBC add-back (Phase 1 fix). "
            "Reconciliation target for Pathway A (OCF − CapEx) — "
            "drift > 10% surfaces as fcf_pathway_residual_complexity "
            "flag (deferred-tax + other non-cash items not yet modelled)."
        ),
        alternates=[
            "Basic Pathway B (no SBC): NOPAT + DA − CapEx − ΔNWC",
            "Full Pathway B (with deferred-tax + other non-cash) — not yet built",
        ],
    ),
]


# ─────────────────────────────────────────────────────────────────────
# Lookup helpers
# ─────────────────────────────────────────────────────────────────────

_BY_NAME: Dict[str, DerivationEntry] = {e.name: e for e in DERIVATIONS}
_BY_LABEL: Dict[str, DerivationEntry] = {e.label: e for e in DERIVATIONS}


def lookup(name: str) -> Optional[DerivationEntry]:
    """Lookup by canonical name (matches bundle key)."""
    return _BY_NAME.get(name)


def lookup_by_label(label: str) -> Optional[DerivationEntry]:
    """Lookup by display label (matches UI display column)."""
    return _BY_LABEL.get(label)


def entries_by_category(category: str) -> List[DerivationEntry]:
    """All entries in one category (DCF | RDCF | MultDec | Screen |
    Cleaning | Moat | Cyclic | L1_identity | L3_rollforward)."""
    return [e for e in DERIVATIONS if e.category == category]


def category_d_entries() -> List[DerivationEntry]:
    """All entries flagged as expected-to-diverge-from-FMP (Category D
    methodology divergence). UI shows a warning chip on these rows."""
    return [e for e in DERIVATIONS if e.category_d]


# ─────────────────────────────────────────────────────────────────────
# V1 — Runtime trace reconstruction
# ─────────────────────────────────────────────────────────────────────

# Bundle sub-dicts searched (in order) when resolving an input name.
# V1 reads from the already-computed Stage 3 bundle — no engine
# instrumentation. Inputs that don't resolve here come from upstream
# cleaning records (Stage 2 / DB), tagged "<from upstream>" in the trace.
_BUNDLE_SEARCH_PATHS = (
    "dcf",
    "reverse_dcf",
    "multiple_decomposition",
    "screening",
    "moat_fingerprint",
    "cyclicality",
)

# Per-input name → bundle-key aliases. Catches camelCase ↔ snake_case
# and known short forms (wacc_base vs wacc, nopat vs NOPAT).
_INPUT_ALIASES: Dict[str, List[str]] = {
    "wacc":               ["wacc_base", "wacc"],
    "tax_rate":           ["tax_rate", "tax_rate_base"],
    "NormalizedEBIT":     ["ebit", "NormalizedEBIT"],
    "OperatingIncome":    ["ebit", "OperatingIncome"],
    "EBITDA":             ["ebitda", "EBITDA"],
    "OperatingCF":        ["operating_cf", "OperatingCF"],
    "CapEx_Total":        ["capex_total", "CapEx_Total", "CapEx"],
    "Depreciation_Total": ["depreciation_total", "Depreciation_Total", "da"],
    "SBC":                ["sbc", "SBC"],
    "NetIncome":          ["net_income", "NetIncome"],
    "Revenue":            ["revenue", "Revenue"],
    "TotalEquity":        ["total_equity", "TotalEquity"],
    "LongTermDebt":       ["long_term_debt", "LongTermDebt"],
    "TotalAssets":        ["total_assets", "TotalAssets"],
    "TotalLiabilities":   ["total_liabilities", "TotalLiabilities"],
    "Cash":               ["cash", "Cash"],
    "ShortTermInvestments": ["short_term_investments", "ShortTermInvestments"],
    "shares_diluted":     ["shares_diluted", "SharesDiluted"],
    "market_cap":         ["market_cap", "marketCap"],
    "enterprise_value":   ["base_ev", "current_ev", "enterprise_value"],
    "ebit_margin":        ["ebit_margin", "EBIT_Margin"],
    "fcf":                ["fcf", "FCF"],
    "ebitda":             ["ebitda", "EBITDA"],
    "nopat":              ["nopat", "NOPAT"],
    "net_debt":           ["net_debt", "NetDebt"],
    "ebit":               ["ebit", "EBIT", "NormalizedEBIT"],
    "tax_rate":           ["tax_rate", "tax_rate_base", "GAAP_TaxRate", "CashTaxRate"],
    "screening_p_e":      ["p_per_e_ratio"],
    "market_p_e":         ["market_p_e"],
    "market_ev_ebitda":   ["market_ev_ebitda"],
    "justified_ev_ebitda":["justified_ev_ebitda"],
    "growth_rate":        ["growth_rate"],
    "roic":               ["roic"],
    "current_price":      ["current_price"],
    "DilutedEPS":         ["DilutedEPS", "diluted_eps"],
    "eps_growth_rate":    ["eps_growth_rate"],
    "base_intrinsic_per_share": ["base_intrinsic_per_share"],
    "beta":               ["beta"],
    "risk_free_rate":     ["risk_free_rate"],
    "equity_risk_premium":["equity_risk_premium"],
}


def _resolve_input(
    input_name: str, bundle: Dict[str, Any],
) -> Optional[Any]:
    """Look up an input value by name across the Stage 3 bundle.

    Resolution order (first non-None wins):
      1. Engine sub-bundles (dcf, reverse_dcf, multiple_decomposition,
         screening, moat_fingerprint, cyclicality) — V1
      2. ``upstream_inputs`` — V2 flat raw+clean+derived echo from
         the anchor FY record (OperatingCF, CapEx_Total, NetIncome,
         TotalEquity, etc.)
      3. ``prior_year_inputs`` (only for ``_beg``-suffixed inputs) —
         V3a flat raw+clean+derived from the FY-1 record. A bare
         ``Cash_beg`` input resolves by stripping the suffix and
         reading ``Cash`` from the prior-year dict.
      4. Top-level bundle keys

    Tries known aliases for each tier; then exact name. Returns None
    when the input doesn't resolve (caller marks "<from upstream>" —
    typically engine-synthetic intermediates V3b doesn't yet capture).
    """
    candidates = [input_name] + _INPUT_ALIASES.get(input_name, [])

    # V3a — pair-rollforward semantic suffix handling:
    #   "Cash_beg" → look up bare "Cash" in prior_year_inputs (FY-1)
    #   "Cash_end" → look up bare "Cash" in upstream_inputs (anchor FY)
    if input_name.endswith("_beg"):
        base = input_name[: -len("_beg")]
        prior = bundle.get("prior_year_inputs") or {}
        if isinstance(prior, dict):
            for cand in [base] + _INPUT_ALIASES.get(base, []):
                if cand in prior and prior[cand] is not None:
                    return prior[cand]
    elif input_name.endswith("_end"):
        base = input_name[: -len("_end")]
        upstream = bundle.get("upstream_inputs") or {}
        if isinstance(upstream, dict):
            for cand in [base] + _INPUT_ALIASES.get(base, []):
                if cand in upstream and upstream[cand] is not None:
                    return upstream[cand]

    # 1. Engine sub-bundles
    for sub_key in _BUNDLE_SEARCH_PATHS:
        sub = bundle.get(sub_key) or {}
        if not isinstance(sub, dict):
            continue
        for cand in candidates:
            if cand in sub and sub[cand] is not None:
                return sub[cand]
    # 2. V2 — upstream_inputs (anchor FY raw+clean+derived + V3 enrichment)
    upstream = bundle.get("upstream_inputs") or {}
    if isinstance(upstream, dict):
        for cand in candidates:
            if cand in upstream and upstream[cand] is not None:
                return upstream[cand]
    # 3. V3 — config_inputs (ERP, statutory rates, date-aware constants)
    config = bundle.get("config_inputs") or {}
    if isinstance(config, dict):
        for cand in candidates:
            if cand in config and config[cand] is not None:
                return config[cand]
    # 4. Top-level bundle keys (some calc results live there directly)
    for cand in candidates:
        if cand in bundle and bundle[cand] is not None:
            return bundle[cand]
    return None


def trace_value(name: str, bundle: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Build a runtime trace for one derived value.

    Combines the registry entry (formula + methodology) with the
    actual runtime values from the Stage 3 bundle. The result is what
    you'd see on a "show your work" page next to the value:

      {
        "name": "fcf",
        "label": "FCF",
        "result": 46_110_000_000,         # actual value from bundle
        "formula": "OperatingCF − abs(CapEx)",
        "input_values": [
          ("OperatingCF", 91_330_000_000),    # resolved from dcf bundle
          ("CapEx_Total", "<from upstream>"), # not in bundle, came from cleaning
        ],
        "methodology": "...",
        "category_d": True,
      }

    Returns None when the registry has no entry for ``name``.
    """
    entry = lookup(name)
    if entry is None:
        return None
    # Result value — try direct name first, then alias map
    result = _resolve_input(name, bundle)
    # Resolve each declared input
    input_values: List[Any] = []
    for inp in entry.inputs:
        v = _resolve_input(inp, bundle)
        input_values.append((inp, v if v is not None else "<from upstream>"))
    return {
        "name": entry.name,
        "label": entry.label,
        "category": entry.category,
        "result": result,
        "formula": entry.formula,
        "input_values": input_values,
        "methodology": entry.methodology,
        "alternates": entry.alternates,
        "fmp_equivalent": entry.fmp_equivalent,
        "category_d": entry.category_d,
    }


__all__ = [
    "DerivationEntry",
    "DERIVATIONS",
    "lookup",
    "lookup_by_label",
    "entries_by_category",
    "category_d_entries",
    "trace_value",
]
