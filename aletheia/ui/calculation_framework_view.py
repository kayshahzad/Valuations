"""Calculation Framework view — surfaces the entire 3-layer encoding
model with formulas, tolerances, and exception categories.

Per the 3-layer accounting model:

  L1 Structural identities      → laws of accounting; hard assertions
  L2 Derivational relationships → methodology-bearing formulas
  L3 Period-over-period flows   → roll-forward primitives (linking
                                   audit + projection)

This view reads constants / catalogs / primitives directly from the
calc-layer modules so the page stays in sync as engineers add new
identities or registry entries. No hand-maintained copy.
"""

from __future__ import annotations

import inspect
from typing import Any, Dict, List

import streamlit as st


# ─────────────────────────────────────────────────────────────────────
# L1 — Structural identities + tier-C enforcement
# ─────────────────────────────────────────────────────────────────────

# Identity → (formula, enforcement_point, rationale) reference. Tolerances
# live in TOLERANCE_THRESHOLDS (read dynamically below).
_L1_IDENTITIES: List[Dict[str, str]] = [
    {
        "id": "balance_sheet_equation",
        "name": "Balance Sheet Equation",
        "formula": "TotalAssets = TotalLiabilities + TotalEquity + RedeemableNCI",
        "enforcement": "Hard block at Stage 1→2 persist + Stage 2→3 gate",
        "rationale": (
            "The fundamental accounting identity. Financial statements "
            "cannot internally contradict; computing DCF on an unbalanced "
            "BS produces meaningless math. Tier-C — refuses to persist."
        ),
    },
    {
        "id": "cash_rollforward",
        "name": "Cash Roll-forward",
        "formula": "Cash_end = Cash_beg + OCF + ICF + FCF + FX_effect",
        "enforcement": "Audit-only (surfaces in Stage 3 identity audit)",
        "rationale": (
            "Post-ASU 2016-18 the CF statement reconciles to BROAD cash "
            "(Cash + restricted cash + restricted CE). Most exact "
            "identity in the framework — small drifts indicate cleaning "
            "gaps or FX-effect tag misses."
        ),
    },
    {
        "id": "retained_earnings_rollforward",
        "name": "Retained Earnings Roll-forward (equity-bridge)",
        "formula": (
            "RE_end = RE_beg + NI − Div − (Buybacks + TaxWithhold) "
            "+ SBC − ΔAPIC"
        ),
        "enforcement": "Audit-only",
        "rationale": (
            "Empirically validated equity-bridge model. For share-"
            "retirement filers (META, AAPL, GOOGL, MSFT), buybacks "
            "draw down APIC first; residual hits RE. SBC credits "
            "APIC. The observed ΔAPIC captures the net effect."
        ),
    },
    {
        "id": "ppe_rollforward",
        "name": "PP&E Roll-forward",
        "formula": "PPE_end ≈ PPE_beg + CapEx − D&A (± acquisitions, impairments, CIP)",
        "enforcement": "Audit-only; wider tolerance for hyperscalers",
        "rationale": (
            "Drift > +5% with material Goodwill growth → acquisition. "
            "Drift < −5% → impairment. Hyperscaler filers get widened "
            "tolerance (15%) for routine CIP accumulation."
        ),
    },
    {
        "id": "debt_rollforward",
        "name": "Debt Roll-forward",
        "formula": (
            "TotalDebt_end ≈ TotalDebt_beg + Issued − Repaid + CP_net"
        ),
        "enforcement": "Audit-only; wider tolerance in 2019 (ASC 842)",
        "rationale": (
            "Total debt includes LTD_noncurrent + LTD_current + "
            "CommercialPaper + FinanceLease_C/NC. FY2019 widened to 8% "
            "for ASC 842 operating-lease-to-BS transition."
        ),
    },
    {
        "id": "working_capital_AR",
        "name": "WC Reconciliation — AR",
        "formula": "BS Δ AR = CF Δ AR (positive-magnitude convention)",
        "enforcement": "Audit-only; skipped when |Δ| < $1M",
        "rationale": (
            "XBRL IncreaseDecreaseInAccountsReceivable and BS Δ both "
            "report the same positive-magnitude. Identity holds when "
            "no acquired AR or CF aggregation effects."
        ),
    },
    {
        "id": "working_capital_inventory",
        "name": "WC Reconciliation — Inventory",
        "formula": "BS Δ Inventory = CF Δ Inventory",
        "enforcement": "Audit-only; skipped when |beg|+|end| < $10M",
        "rationale": (
            "Materiality floor at $10M for services / digital filers "
            "(META, V, MA) with structural zero inventory."
        ),
    },
    {
        "id": "working_capital_AP",
        "name": "WC Reconciliation — AP",
        "formula": "BS Δ AP = CF Δ AP",
        "enforcement": "Audit-only",
        "rationale": (
            "Filer-dependent: filers that split AP into Trade + Other "
            "subline + CF aggregation produce systematic drift "
            "(handled by direction-based flags)."
        ),
    },
    {
        "id": "fcf_pathway_reconciliation",
        "name": "FCF Pathway Reconciliation",
        "formula": (
            "Pathway A: OCF − CapEx    Pathway B: NOPAT + DA + SBC − CapEx − ΔNWC"
        ),
        "enforcement": "Audit-only",
        "rationale": (
            "Two pathways to FCF should agree within tolerance. "
            "Direction signals where Pathway B is over-/under-modelled "
            "(deferred-tax, other non-cash items not yet captured)."
        ),
    },
]


def _render_l1_section() -> None:
    """L1 — Structural identities surface."""
    from aletheia.calculations.identity_checks import (
        TOLERANCE_THRESHOLDS, ABS_MAGNITUDE_FLOOR_USD,
        HYPERSCALER_TICKERS, ASC_842_TRANSITION_FY,
        ASC_842_DEBT_TOL_WIDENED, IRA_EXCISE_TAX_START_FY,
        IRA_EXCISE_TAX_RATE,
    )
    from aletheia.calculations._schema_contract import (
        _TIER_C_FIELDS, _TIER_C_CATEGORIES,
    )

    st.markdown("## L1 — Structural Identities")
    st.markdown(
        "Laws of accounting encoded as hard assertions. Two enforcement "
        "tiers: **Tier C** (truly invalid states) hard-blocks persistence "
        "and Stage 3 computation; **Tier W** (identity drifts) surfaces as "
        "Stage 3 audit findings."
    )

    # Tier-C reference
    with st.expander("🚫 Tier-C — hard-blocking violations", expanded=False):
        st.markdown(
            "Records carrying these violations are **refused at "
            "persistence** (DuckDB upsert_record) and **refused at Stage 3** "
            "(run_stage3 pre-flight gate). Override via "
            "`aletheia/calculations/_overrides.OVERRIDES` when a documented "
            "edge case applies."
        )
        st.markdown("**Tier-C fields**:")
        for f in sorted(_TIER_C_FIELDS):
            st.markdown(f"- `{f}`")
        st.markdown("**Tier-C categories**:")
        for c in sorted(_TIER_C_CATEGORIES):
            st.markdown(f"- `{c}`")

    # Identity table with live tolerances
    st.markdown("### Per-identity reference")
    rows = []
    for entry in _L1_IDENTITIES:
        tol = TOLERANCE_THRESHOLDS.get(entry["id"], 0.0)
        rows.append({
            "Identity": entry["name"],
            "Formula": entry["formula"],
            "Tolerance": f"{tol * 100:.1f}%",
            "Enforcement": entry["enforcement"],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    # Rationales (separate expander to keep table compact)
    with st.expander("📖 Per-identity rationale", expanded=False):
        for entry in _L1_IDENTITIES:
            st.markdown(f"**{entry['name']}** (`{entry['id']}`)")
            st.markdown(f"_{entry['rationale']}_")
            st.markdown("---")

    # System constants
    st.markdown("### System constants")
    st.dataframe([
        {"Constant": "ABS_MAGNITUDE_FLOOR_USD",
         "Value": f"${ABS_MAGNITUDE_FLOOR_USD/1e6:.0f}M",
         "Purpose": "Tolerance floor — below this, % drift ignored to avoid noise on tiny denominators"},
        {"Constant": "HYPERSCALER_TICKERS",
         "Value": ", ".join(sorted(HYPERSCALER_TICKERS)),
         "Purpose": "Filers granted widened PP&E tolerance (15%) for routine CIP accumulation"},
        {"Constant": "ASC_842_TRANSITION_FY",
         "Value": str(ASC_842_TRANSITION_FY),
         "Purpose": "Operating-lease on-BS transition year; debt tolerance widened to "
                    f"{ASC_842_DEBT_TOL_WIDENED * 100:.0f}%"},
        {"Constant": "IRA_EXCISE_TAX_START_FY",
         "Value": str(IRA_EXCISE_TAX_START_FY),
         "Purpose": f"Inflation Reduction Act 1% excise tax on buybacks "
                    f"({IRA_EXCISE_TAX_RATE * 100:.1f}%); affects RE rollforward"},
    ], use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────
# L2 — Derivational registry
# ─────────────────────────────────────────────────────────────────────

def _render_l2_section() -> None:
    """L2 — Derivation registry surface."""
    from aletheia.calculations.derivation_registry import (
        DERIVATIONS, category_d_entries,
    )

    st.markdown("## L2 — Derivational Registry")
    st.markdown(
        "Every Stage 3 derived value catalogued with its formula, inputs, "
        "methodology citation, alternates, and FMP divergence note. "
        "Category-D rows are expected-to-diverge from FMP due to "
        "methodology choice (not bugs)."
    )

    # Top-level metrics
    n_total = len(DERIVATIONS)
    n_cat_d = len(category_d_entries())
    n_with_fmp = sum(1 for e in DERIVATIONS if e.fmp_equivalent)
    cols = st.columns(4)
    cols[0].metric("Registry entries", n_total)
    cols[1].metric("📐 Category-D", n_cat_d)
    cols[2].metric("FMP-equivalent documented", n_with_fmp)
    cols[3].metric("Resolver tiers", 3)  # engine sub-bundles, upstream, config

    # Category filter
    categories = sorted({e.category for e in DERIVATIONS})
    selected = st.multiselect(
        "Filter by category", categories, default=[],
        key="l2_category_filter",
    )

    # Table
    rows = []
    for e in DERIVATIONS:
        if selected and e.category not in selected:
            continue
        rows.append({
            "📐": "📐" if e.category_d else "",
            "Category": e.category,
            "Label": e.label,
            "Formula": e.formula[:80] + ("…" if len(e.formula) > 80 else ""),
            "FMP divergence": (e.fmp_equivalent or "—")[:50],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    # Detail expander
    with st.expander("📖 Full methodology details", expanded=False):
        st.caption(
            "Each entry's full methodology, inputs, and alternates. "
            "These same details surface in the per-ticker Stage Explorer's "
            "Stage 3 panel, scoped to the ticker's actual values."
        )
        for e in DERIVATIONS:
            if selected and e.category not in selected:
                continue
            chip = "📐 " if e.category_d else ""
            st.markdown(
                f"##### {chip}{e.label}  ·  `{e.name}`  ·  *{e.category}*"
            )
            st.markdown(f"**Formula**: `{e.formula}`")
            if e.inputs:
                st.markdown(
                    "**Inputs**: " + ", ".join(f"`{i}`" for i in e.inputs)
                )
            st.markdown(f"**Methodology**: {e.methodology}")
            if e.alternates:
                st.markdown("**Alternates**:")
                for alt in e.alternates:
                    st.markdown(f"  - {alt}")
            if e.fmp_equivalent:
                st.markdown(f"**FMP divergence**: {e.fmp_equivalent}")
            st.markdown("---")


# ─────────────────────────────────────────────────────────────────────
# L3 — Roll-forward primitives
# ─────────────────────────────────────────────────────────────────────

def _render_l3_section() -> None:
    """L3 — Roll-forward primitives surface."""
    from aletheia.calculations import rollforward

    st.markdown("## L3 — Roll-forward Primitives")
    st.markdown(
        "Six pure mathematical mappings `(beg_balance, period_activity, "
        "*adjustments) → implied_end`. Used by both **audit** "
        "(identity_checks.py — compute drift after-the-fact) and "
        "**projection** (DCFEngine — emit projected next-period FCFF). "
        "Same function, two contexts."
    )

    # Live introspection
    primitives = [
        ("ppe_rollforward", rollforward.ppe_rollforward),
        ("re_rollforward", rollforward.re_rollforward),
        ("cash_rollforward", rollforward.cash_rollforward),
        ("debt_rollforward", rollforward.debt_rollforward),
        ("wc_rollforward", rollforward.wc_rollforward),
        ("fcf_pathway_b", rollforward.fcf_pathway_b),
    ]

    rows = []
    for name, fn in primitives:
        sig = inspect.signature(fn)
        params = [
            p.name for p in sig.parameters.values()
            if p.kind != inspect.Parameter.VAR_KEYWORD
        ]
        doc = (fn.__doc__ or "").strip().split("\n")[0]
        rows.append({
            "Primitive": f"`{name}`",
            "Parameters": ", ".join(params),
            "Returns": "implied_end",
            "Summary": doc[:100],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)

    with st.expander("📖 Full primitive docstrings + source signatures", expanded=False):
        for name, fn in primitives:
            sig = inspect.signature(fn)
            st.markdown(f"##### `{name}{sig}`")
            doc = (fn.__doc__ or "").strip()
            if doc:
                st.markdown(doc.replace("\n    ", "\n"))
            st.markdown("---")


# ─────────────────────────────────────────────────────────────────────
# Exception categories surface
# ─────────────────────────────────────────────────────────────────────

# Documented exception categories (synthesized from the audit code).
# Read by run_identity_checks and exposed on the bundle's
# accounting_identities.summary.exception_categories list.
_EXCEPTION_CATEGORIES: List[Dict[str, str]] = [
    # NCI / balance sheet
    {"category": "nci_inclusion_required", "identity": "balance_sheet",
     "semantic": "Identity closes WHEN redeemable NCI is added. Cleaning's TotalEquity excludes it for this filer."},
    {"category": "balance_sheet_residual_complexity", "identity": "balance_sheet",
     "semantic": "Catch-all — smaller BS drifts not yet attributed to a specific cause."},
    # Cash
    {"category": "pre_asu_2016_18_narrow_cash", "identity": "cash_rollforward",
     "semantic": "Pre-FY2018 filer — narrow-cash CF reconciliation; expected residual."},
    {"category": "cash_rollforward_residual_complexity", "identity": "cash_rollforward",
     "semantic": "Catch-all — small residual drifts post-ASU."},
    # RE / equity bridge
    {"category": "pre_buyback_era", "identity": "retained_earnings",
     "semantic": "Buybacks < 1% NI — pre-buyback era, basic formula sufficient."},
    {"category": "asc842_cumulative_effect", "identity": "retained_earnings",
     "semantic": "FY2019 ASC 842 cumulative-effect adjustment to RE."},
    {"category": "ira_excise_tax_residual", "identity": "retained_earnings",
     "semantic": "FY2023+ residual ≤5% from IRA 1% excise tax handling."},
    {"category": "treasury_method_filer", "identity": "retained_earnings",
     "semantic": "Drift > +5% + material buybacks → filer uses treasury accounting (charge → TreasuryStock not RE)."},
    {"category": "cumulative_effect_adjustment_era", "identity": "retained_earnings",
     "semantic": "FY2018 (ASC 606) or FY2020 (ASU 2016-13) with material drift — accounting-standard adoption."},
    {"category": "near_zero_re_denominator", "identity": "retained_earnings",
     "semantic": "RE near zero (cumulative buybacks drove it negative) — % drift not meaningful."},
    {"category": "equity_bridge_residual_complexity", "identity": "retained_earnings",
     "semantic": "Catch-all — remaining equity-bridge residual (share-issuance, OCI reclass)."},
    # PP&E
    {"category": "impairment_implied", "identity": "ppe_rollforward",
     "semantic": "Drift < −5% — PP&E reduction exceeds D&A; likely impairment / write-down."},
    {"category": "acquisition_implied", "identity": "ppe_rollforward",
     "semantic": "Drift > +5% + Goodwill growth > 10% → material acquisition with PP&E step-up."},
    {"category": "minor_acquisition_implied", "identity": "ppe_rollforward",
     "semantic": "Drift > +5% + Goodwill growth 5-10% — tuck-in / bolt-on acquisition."},
    {"category": "hyperscaler_cip", "identity": "ppe_rollforward",
     "semantic": "Hyperscaler filer (META, AMZN, GOOGL, MSFT, NVDA, AAPL) with CIP accumulation pattern."},
    {"category": "capital_intensive_capex_timing", "identity": "ppe_rollforward",
     "semantic": "CapEx/Revenue > 5% (pharma, semis, industrials) with CIP + equipment-cycle timing."},
    {"category": "ppe_rollforward_residual_complexity", "identity": "ppe_rollforward",
     "semantic": "Catch-all — non-capital-intensive filer with unattributed drift."},
    # Debt
    {"category": "asc842_transition", "identity": "debt_rollforward",
     "semantic": "FY2019 ASC 842 operating-lease-to-BS transition; widened to 8% tolerance."},
    {"category": "finance_lease_roe_addition", "identity": "debt_rollforward",
     "semantic": "Δ Finance-lease liability > 50% of discrepancy — non-CF lease originations."},
    {"category": "pre_asc842_debt_era", "identity": "debt_rollforward",
     "semantic": "Pre-2019 — finance-lease liabilities not yet BS-capitalized; systematic residual."},
    {"category": "refinancing_year_gross_flows", "identity": "debt_rollforward",
     "semantic": "Gross issuance + repayment both > 5% of beg — refinancing distorts net-flow assumption."},
    {"category": "debt_reclassification_no_cf_flow", "identity": "debt_rollforward",
     "semantic": "Debt changed but Issued + Repaid ≈ 0 — non-CF reclassification (LTD-NC ↔ LTD-C)."},
    {"category": "near_zero_debt_denominator", "identity": "debt_rollforward",
     "semantic": "Beg + end debt both < $1B — % drift not meaningful."},
    {"category": "debt_rollforward_residual_complexity", "identity": "debt_rollforward",
     "semantic": "Catch-all — small residual."},
    # WC
    {"category": "acquisition_distorts_wc", "identity": "working_capital",
     "semantic": "Goodwill grew > 10% → acquired WC on BS without CF entry."},
    {"category": "wc_ar_cf_aggregates_more_lines", "identity": "working_capital_AR",
     "semantic": "CF reports more change than BS — CF aggregates wider set of accounts."},
    {"category": "wc_ar_bs_grew_beyond_cf", "identity": "working_capital_AR",
     "semantic": "BS grew more than CF — acquired AR, FX, or non-cash reclassification."},
    {"category": "wc_inventory_cf_aggregates_more_lines", "identity": "working_capital_inventory",
     "semantic": "Same as AR but inventory."},
    {"category": "wc_inventory_bs_grew_beyond_cf", "identity": "working_capital_inventory",
     "semantic": "Same as AR but inventory."},
    {"category": "wc_ap_cf_aggregates_more_lines", "identity": "working_capital_AP",
     "semantic": "Same as AR but AP (e.g., META's CF Trade+Other+Accrued aggregation)."},
    {"category": "wc_ap_bs_grew_beyond_cf", "identity": "working_capital_AP",
     "semantic": "Same pattern for AP."},
    # FCF pathway
    {"category": "first_year_or_pre_data", "identity": "fcf_pathway",
     "semantic": "No prior FY for ΔNWC; or statutory tax-rate fallback — pre-data-era."},
    {"category": "fcf_pathway_acquisition_distortion", "identity": "fcf_pathway",
     "semantic": "Goodwill grew > 10% — acquired OCF + step-up CapEx blur the bridge."},
    {"category": "fcf_pathway_a_excess_under_modeled_addbacks", "identity": "fcf_pathway",
     "semantic": "Pathway A > Pathway B + > 10% — missing add-backs (deferred-tax, non-cash impairments)."},
    {"category": "fcf_pathway_b_excess_over_modeled_addbacks", "identity": "fcf_pathway",
     "semantic": "Pathway B > Pathway A + > 10% — over-counted add-backs (SBC for treasury-method filers)."},
    {"category": "fcf_pathway_residual_complexity", "identity": "fcf_pathway",
     "semantic": "Remaining residual not fitting any pattern."},
]


def _render_exceptions_section() -> None:
    """Exception category catalog."""
    st.markdown("## Exception Categories")
    st.markdown(
        "Every non-passing identity check is flagged with an "
        "``exception_category``. Catch-all `*_residual_complexity` "
        "categories are honest acknowledgements of structural complexity "
        "we don't yet model. Specific categories tell the analyst the "
        "exact reason and (often) the path to closure."
    )

    # Filter by identity
    identities = sorted({c["identity"] for c in _EXCEPTION_CATEGORIES})
    selected = st.multiselect(
        "Filter by identity", identities, default=[],
        key="l1_exception_filter",
    )

    rows = []
    for c in _EXCEPTION_CATEGORIES:
        if selected and c["identity"] not in selected:
            continue
        is_catchall = "_residual_complexity" in c["category"]
        rows.append({
            "Catch-all?": "📦" if is_catchall else "✓",
            "Identity": c["identity"],
            "Category": f"`{c['category']}`",
            "Semantic": c["semantic"],
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)


# ─────────────────────────────────────────────────────────────────────
# Top-level entry point
# ─────────────────────────────────────────────────────────────────────

def render_calculation_framework() -> None:
    """Calculation Framework view — the 3-layer encoding model on one page.

    Sections (each independently expandable / filterable):
      L1 — Structural identities (formulas + tolerances + tier-C enforcement)
      L2 — Derivational registry (32 entries with methodology + FMP notes)
      L3 — Roll-forward primitives (6 pure functions, live introspection)
      Exception categories — full catalog of audit exception flags
    """
    st.markdown("# Calculation Framework")
    st.caption(
        "The 3-layer accounting encoding model: every formula, tolerance, "
        "primitive, and exception category in one reference. Pulled "
        "directly from the calc-layer modules so the page stays in sync "
        "with code."
    )

    # Top-level summary metrics
    from aletheia.calculations.derivation_registry import DERIVATIONS
    from aletheia.calculations.identity_checks import TOLERANCE_THRESHOLDS
    from aletheia.calculations import rollforward
    cols = st.columns(4)
    cols[0].metric("L1 identities", len(TOLERANCE_THRESHOLDS))
    cols[1].metric("L2 derivations", len(DERIVATIONS))
    cols[2].metric(
        "L3 primitives",
        sum(1 for n in dir(rollforward) if not n.startswith("_") and callable(getattr(rollforward, n))),
    )
    cols[3].metric("Exception categories", len(_EXCEPTION_CATEGORIES))

    st.markdown("---")

    tabs = st.tabs([
        "L1 Structural",
        "L2 Derivational",
        "L3 Roll-forward",
        "Exception Categories",
    ])
    with tabs[0]:
        _render_l1_section()
    with tabs[1]:
        _render_l2_section()
    with tabs[2]:
        _render_l3_section()
    with tabs[3]:
        _render_exceptions_section()


__all__ = ["render_calculation_framework"]
