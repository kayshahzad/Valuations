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
from typing import Dict, List, Optional


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
        inputs=["nopat", "TotalEquity", "LongTermDebt"],
        methodology=(
            "IC = TotalEquity + LongTermDebt (operating-capital definition). "
            "Excludes excess cash, intangibles-from-acquisitions, "
            "non-operating investments."
        ),
        alternates=[
            "McKinsey IC (includes operating leases capitalized, "
            "excludes goodwill)",
            "Damodaran IC (book equity + book debt + operating-lease "
            "liability capitalized)",
            "FMP IC (their returnOnInvestedCapitalTTM uses a broader "
            "denominator including some intangibles)",
        ],
        fmp_equivalent=(
            "returnOnInvestedCapitalTTM — typically 30-50% LOWER than "
            "our ROIC due to broader IC denominator"
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
        inputs=["fcf_projections", "wacc", "terminal_value", "net_debt",
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
            "Operating-debt definition: gross debt minus liquid assets. "
            "Includes short-term investments per Damodaran since they "
            "could service debt. Lease liabilities EXCLUDED."
        ),
        alternates=[
            "Strict net debt: TotalDebt − Cash (no STI)",
            "Lease-inclusive: + OperatingLeaseLiability (some analysts)",
        ],
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
            "Book value = TotalEquity (book) including all equity "
            "components (RE, APIC, AOCI). Excludes minority interest."
        ),
        alternates=[
            "Tangible book P/B (subtract goodwill + intangibles)",
            "FMP P/B (priceToBookRatioTTM) — may include MI",
        ],
        fmp_equivalent=(
            "priceToBookRatioTTM — 10-15% drift from book-value "
            "definition (MI inclusion, intangibles)"
        ),
        category_d=True,
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
        inputs=["EBIT_margin", "historical_EBIT_margin_series"],
        methodology=(
            "Z-score of current period's EBIT margin vs the 10y "
            "trailing distribution. |z| > 1.0 → peak/trough; haircut "
            "applied to DCF in peak years."
        ),
        alternates=[],
        fmp_equivalent=None,
    ),
    DerivationEntry(
        name="moat_score",
        label="Moat score",
        category="Moat",
        formula="weighted_sum(ROIC_persistence + margin_stability + market_share)",
        inputs=["roic_10y_avg", "ebit_margin_volatility", "revenue_growth_consistency"],
        methodology=(
            "Moat fingerprint composite. 0-10 score where 10 = wide "
            "durable moat (Buffett-style)."
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


__all__ = [
    "DerivationEntry",
    "DERIVATIONS",
    "lookup",
    "lookup_by_label",
    "entries_by_category",
    "category_d_entries",
]
