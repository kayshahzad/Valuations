"""Streamlit view — run Stage 3 in isolation against FMP-sourced data.

Lets the analyst validate the calc layer without depending on our SEC
ingest / cleaning pipeline. Click the run button for the selected
ticker, and we:
  1. Pull FMP income / balance / cash flow (uses on-disk cache).
  2. Adapt to ``ValidatedCleanedRecord`` shape.
  3. Run Stage 3 → returns a ``CalculationBundle``.
  4. Render coverage, engine outputs, and the L1 + L3 live trace.

Bundles are also written to ``audits/fmp_validation_<ticker>_<ts>.json``
for diff against the DB-backed bundle.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import streamlit as st

from aletheia.data import fmp_client
from aletheia.ui.pipeline_explorer_view import (
    _bundle_get, _drift_pct, _fmt_metric, _fmt_pct, _fmt_usd,
    _render_identity_audit_panel,
)
from aletheia.validation.stage3_isolated import run_stage3_isolated


SESSION_KEY = "_iso_stage3_result"


def render_stage3_validation(ticker: str) -> None:
    """Entry point. Renders the validation tab for one ticker.

    Provider selection lives in the global sidebar (P4) — this view
    just reads ``st.session_state["provider"]`` through the registry,
    honouring per-ticker pins from ``config/provider_pins.py``.
    """
    from aletheia.providers import resolve_provider_name
    selected = st.session_state.get("provider", "fmp")
    effective, pin_reason = resolve_provider_name(selected, ticker=ticker)

    st.markdown("## 🧪 Stage 3 validation")
    chip = (
        f"📌 Pinned to **{effective.upper()}** for {ticker} · {pin_reason}"
        if pin_reason
        else f"📊 Active source: **{effective.upper()}**"
    )
    st.caption(
        f"Run Stage 3 for **{ticker}** against the configured data "
        f"provider. {chip}. Change the global source in the sidebar "
        "selector — the choice flows through every panel that "
        "consumes a bundle. Clear results below by re-running."
    )

    cols = st.columns([2, 1, 3])
    run_clicked = cols[0].button(
        f"🧪 Run Stage 3 for {ticker}",
        use_container_width=True,
        key="iso_run_btn",
    )
    force_refresh = cols[1].toggle(
        "Force refetch", value=False, key="iso_force_refresh",
        help=(
            "FMP: hit the API and overwrite the on-disk cache. "
            "XBRL: no-op (Stage 1 owns SEC fetching)."
        ),
    )

    # Cached result is invalid when the user switches provider in the
    # sidebar between renders. Compare stamps.
    cached = st.session_state.get(SESSION_KEY)
    if cached and cached.get("provider") != effective:
        st.session_state.pop(SESSION_KEY, None)

    if run_clicked:
        with st.spinner(
            f"Running Stage 3 via {effective.upper()} provider..."
        ):
            result = run_stage3_isolated(
                ticker, write_audit=True,
                force_refresh_fmp=force_refresh,
                provider=effective,
            )
        st.session_state[SESSION_KEY] = result

    result = st.session_state.get(SESSION_KEY)
    if not result:
        st.info(
            "Click the run button above to execute Stage 3 against "
            "FMP data."
        )
        return

    if result.get("ticker") != ticker:
        st.warning(
            f"The cached result is for **{result.get('ticker')}**; "
            f"click run to refresh for {ticker}."
        )
        return

    _render_run_summary(result)

    if result.get("error"):
        st.error(f"Stage 3 raised an error: {result['error']}")
        return

    bundle = result.get("bundle")
    if not bundle:
        st.warning("Stage 3 returned no bundle.")
        return

    _render_engine_status(bundle)
    _render_dcf_panel(bundle)
    _render_fmp_parity_panel(
        bundle, ticker, force_refresh=force_refresh,
    )
    _render_identity_section(bundle)
    _render_coverage_panel(result.get("coverage") or {})
    _render_cross_source_diff_panel(ticker)


def _render_run_summary(result: Dict[str, Any]) -> None:
    cols = st.columns(5)
    prov = (result.get("provider") or "?").upper()
    cols[0].metric("📊 Source", prov)
    cols[1].metric("Years adapted", result.get("records_built", 0))
    cov = result.get("coverage") or {}
    rng = cov.get("fy_range") or (None, None)
    if rng[0] is not None:
        cols[2].metric("FY range", f"{rng[0]}–{rng[1]}")
    cols[3].metric("Elapsed", f"{result.get('elapsed_s', 0.0):.1f}s")
    audit = result.get("audit_path")
    if audit:
        cols[4].metric("Bundle saved", "✓")
        st.caption(f"Audit: `{audit}`")


def _render_engine_status(bundle: Dict[str, Any]) -> None:
    """One-line status per engine — which produced output, which came
    back empty. The empty engines tell you which depend on
    cleaning-engine-derived fields the FMP adapter doesn't supply."""
    st.markdown("### Engine status (FMP-only inputs)")
    engines = [
        ("DCF", "dcf"),
        ("Reverse DCF", "reverse_dcf"),
        ("Multiple Decomposition", "multiple_decomposition"),
        ("Screening", "screening"),
        ("Cyclicality", "cyclicality"),
        ("Moat", "moat_fingerprint"),
        ("Reality checks", "reality_checks"),
        ("Identity audit", "accounting_identities"),
    ]
    rows = []
    for name, key in engines:
        v = bundle.get(key)
        if v is None:
            status = "✗ no output"
            keys_n = 0
        elif isinstance(v, dict):
            keys_n = len(v)
            status = (
                f"✓ {keys_n} keys" if keys_n else "⚠ empty (engine ran, no fields)"
            )
        else:
            status = f"⚠ {type(v).__name__}"
            keys_n = 0
        rows.append({
            "Engine": name, "Status": status, "Output keys": keys_n,
        })
    st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_dcf_panel(bundle: Dict[str, Any]) -> None:
    dcf = bundle.get("dcf") or {}
    rdcf = bundle.get("reverse_dcf") or {}
    if not (dcf or rdcf):
        return
    st.markdown("### Valuation summary")
    cols = st.columns(4)
    cols[0].metric(
        "WACC",
        _fmt_pct(dcf.get("wacc") or dcf.get("wacc_base"), decimals=2),
    )
    # DCFResult.to_dict() flattens scenarios into top-level keys
    # (base_intrinsic_per_share, base_ev, base_upside, …). There is no
    # nested ``dcf.base`` dict.
    cols[1].metric(
        "IV per share (base)",
        _fmt_usd(dcf.get("base_intrinsic_per_share")),
    )
    cols[2].metric(
        "RDCF implied 10Y CAGR",
        _fmt_pct(
            rdcf.get("implied_cagr_10y")
            or rdcf.get("implied_revenue_cagr_10y"),
        ),
    )
    # Reverse-DCF margin: the engine exposes the EBIT margin it solved
    # for in ``ebit_margin``; the explicit ``implied_terminal_margin``
    # key isn't on ReverseDCFResult.to_dict().
    cols[3].metric(
        "Reverse-DCF EBIT margin",
        _fmt_pct(rdcf.get("ebit_margin")),
    )


def _render_identity_section(bundle: Dict[str, Any]) -> None:
    identities = bundle.get("accounting_identities") or {}
    if not identities.get("results"):
        return
    st.markdown("### L1 + L3 identity audit (on FMP data)")
    st.caption(
        "Identity checks run against the FMP-fed records. Surfaces "
        "whether FMP's data is internally consistent — failures here "
        "are FMP data issues, not necessarily calc-layer bugs."
    )
    _render_identity_audit_panel(identities)


def _load_fmp_ttm_metrics(
    ticker: str, force_refresh: bool = False,
) -> Dict[str, Any]:
    """Merged FMP TTM ratios + key-metrics for one ticker.

    Uses ``fmp_client`` (cache-first, honors ``force_refresh``). Returns
    a flat dict keyed by FMP's native field names. Empty dict when both
    endpoints fail.
    """
    out: Dict[str, Any] = {}
    ratios = fmp_client.fetch_ratios_ttm(ticker, force_refresh=force_refresh)
    if isinstance(ratios, dict):
        out.update(ratios)
    keymet = fmp_client.fetch_key_metrics_ttm(ticker, force_refresh=force_refresh)
    if isinstance(keymet, dict):
        out.update(keymet)
    return out


# Side-by-side parity spec. Each entry compares ONE metric:
#   our_fn(bundle) → our value (None when our calc can't be produced)
#   fmp_field      → key into _load_fmp_ttm_metrics output
#   kind           → for formatting only (pct | ratio | usd)
# Categories group the rendered table.
def _safe_div(num: Any, den: Any) -> Optional[float]:
    try:
        a, b = float(num), float(den)
    except (TypeError, ValueError):
        return None
    if not b:
        return None
    return a / b


def _our_fcf(b: Dict[str, Any]) -> Optional[float]:
    return _bundle_get(b, "dcf/fcf")


def _our_market_cap(b: Dict[str, Any]) -> Optional[float]:
    return _bundle_get(b, "dcf/market_cap")


def _our_ev(b: Dict[str, Any]) -> Optional[float]:
    """Enterprise value = market_cap + net_debt, derived from the DCF
    bundle. MD's market_ev_ebitda returns 0.0 on FMP-fed inputs (needs
    sector-median lookup), so we compute EV ourselves."""
    mc = _our_market_cap(b)
    nd = _bundle_get(b, "dcf/net_debt")
    if mc is None:
        return None
    return mc + (nd or 0.0)


def _pct_from_pct_units(v: Optional[float]) -> Optional[float]:
    """Screening engines store margins/ROE/ROA in percent units
    (46.91 = 46.91%). FMP stores them as decimal fractions
    (0.4691). Convert ours → decimal so drift compares correctly."""
    if v is None:
        return None
    return v / 100.0


def _parity_spec() -> List[Dict[str, Any]]:
    """The full FMP-parity catalog. Adds to the existing 16 mappings in
    pipeline_explorer_view, covering profitability / liquidity / leverage
    / efficiency / per-share / cash flow categories that weren't in the
    Stage 3 calc table."""
    return [
        # ── Valuation multiples ────────────────────────────────────────
        {"cat": "Valuation", "label": "P/E",
         "our": lambda b: _bundle_get(b, "screening/p_per_e_ratio"),
         "fmp": "priceToEarningsRatioTTM", "kind": "ratio"},
        {"cat": "Valuation", "label": "P/B",
         "our": lambda b: _bundle_get(b, "screening/p_per_b_ratio"),
         "fmp": "priceToBookRatioTTM", "kind": "ratio"},
        {"cat": "Valuation", "label": "P/S",
         "our": lambda b: _bundle_get(b, "multiple_decomposition/market_p_sales"),
         "fmp": "priceToSalesRatioTTM", "kind": "ratio"},
        {"cat": "Valuation", "label": "PEG",
         "our": lambda b: _bundle_get(b, "screening/peg_ratio"),
         "fmp": "priceEarningsToGrowthRatioTTM", "kind": "ratio"},
        # EV/EBITDA: MD's value is 0 on FMP-fed inputs (needs sector
        # comp lookups). Derive directly from DCF outputs.
        {"cat": "Valuation", "label": "EV/EBITDA",
         "our": lambda b: _safe_div(_our_ev(b), _bundle_get(b, "dcf/ebitda")),
         "fmp": "evToEBITDATTM", "kind": "ratio"},
        {"cat": "Valuation", "label": "EV/Sales",
         "our": lambda b: _safe_div(_our_ev(b), _bundle_get(b, "dcf/revenue")),
         "fmp": "evToSalesTTM", "kind": "ratio"},
        {"cat": "Valuation", "label": "EV/FCF",
         "our": lambda b: _bundle_get(b, "screening/ev_per_fcf"),
         "fmp": "evToFreeCashFlowTTM", "kind": "ratio"},
        {"cat": "Valuation", "label": "Earnings yield",
         "our": lambda b: _safe_div(1.0, _bundle_get(b, "screening/p_per_e_ratio")),
         "fmp": "earningsYieldTTM", "kind": "pct"},

        # ── Profitability ──────────────────────────────────────────────
        # Screening engine stores margins in % units (46.91 = 46.91%);
        # FMP stores them as decimal fractions (0.4691). Normalise ours.
        {"cat": "Profitability", "label": "Gross margin",
         "our": lambda b: _pct_from_pct_units(_bundle_get(b, "screening/gross_margin_pct")),
         "fmp": "grossProfitMarginTTM", "kind": "pct"},
        {"cat": "Profitability", "label": "Operating margin",
         "our": lambda b: _safe_div(_bundle_get(b, "dcf/ebit"), _bundle_get(b, "dcf/revenue")),
         "fmp": "operatingProfitMarginTTM", "kind": "pct"},
        {"cat": "Profitability", "label": "EBIT margin",
         "our": lambda b: _safe_div(_bundle_get(b, "dcf/ebit"), _bundle_get(b, "dcf/revenue")),
         "fmp": "ebitMarginTTM", "kind": "pct"},
        {"cat": "Profitability", "label": "EBITDA margin",
         "our": lambda b: _safe_div(_bundle_get(b, "dcf/ebitda"), _bundle_get(b, "dcf/revenue")),
         "fmp": "ebitdaMarginTTM", "kind": "pct"},
        {"cat": "Profitability", "label": "Net margin",
         "our": lambda b: _safe_div(
             _bundle_get(b, "dcf/nopat"),
             _bundle_get(b, "dcf/revenue"),
         ),  # nopat = NI proxy; for true net margin we need NI in bundle
         "fmp": "netProfitMarginTTM", "kind": "pct"},
        # FMP doesn't publish FCF/Revenue directly (its closest field is
        # freeCashFlowOperatingCashFlowRatioTTM = FCF/OCF). Surface ours
        # without an FMP comparator so the analyst still sees the value.
        {"cat": "Profitability", "label": "FCF margin",
         "our": lambda b: _pct_from_pct_units(_bundle_get(b, "screening/fcf_margin_pct")),
         "fmp": None, "kind": "pct"},
        {"cat": "Profitability", "label": "ROIC",
         "our": lambda b: _bundle_get(b, "dcf/roic"),
         "fmp": "returnOnInvestedCapitalTTM", "kind": "pct"},
        {"cat": "Profitability", "label": "ROE",
         "our": lambda b: _pct_from_pct_units(_bundle_get(b, "screening/roe")),
         "fmp": "returnOnEquityTTM", "kind": "pct"},
        {"cat": "Profitability", "label": "Effective tax rate",
         "our": lambda b: _bundle_get(b, "dcf/tax_rate"),
         "fmp": "effectiveTaxRateTTM", "kind": "pct"},

        # ── Leverage ───────────────────────────────────────────────────
        # Screening uses kebab-case here: "debt-to-equity".
        {"cat": "Leverage", "label": "Debt / Equity",
         "our": lambda b: _bundle_get(b, "screening/debt-to-equity"),
         "fmp": "debtToEquityRatioTTM", "kind": "ratio"},
        # Debt / Assets — derive from DCF outputs as a clean ratio.
        {"cat": "Leverage", "label": "Debt / Assets",
         "our": lambda b: _safe_div(
             _bundle_get(b, "dcf/net_debt") + (_bundle_get(b, "dcf/market_cap") or 0)*0,  # placeholder until total_debt surfaces
             None,  # TotalAssets not exposed on dcf bundle directly
         ),
         "fmp": "debtToAssetsRatioTTM", "kind": "pct"},
        {"cat": "Leverage", "label": "Net debt / EBITDA",
         "our": lambda b: _safe_div(_bundle_get(b, "dcf/net_debt"), _bundle_get(b, "dcf/ebitda")),
         "fmp": "netDebtToEBITDATTM", "kind": "ratio"},
        {"cat": "Leverage", "label": "Interest coverage",
         "our": lambda b: _bundle_get(b, "screening/interest_coverage"),
         "fmp": "interestCoverageRatioTTM", "kind": "ratio"},

        # ── Liquidity ──────────────────────────────────────────────────
        {"cat": "Liquidity", "label": "Current ratio",
         "our": lambda b: _bundle_get(b, "screening/current_ratio"),
         "fmp": "currentRatioTTM", "kind": "ratio"},
        {"cat": "Liquidity", "label": "Quick ratio",
         "our": lambda b: _bundle_get(b, "screening/quick_ratio"),
         "fmp": "quickRatioTTM", "kind": "ratio"},

        # ── Efficiency ─────────────────────────────────────────────────
        # Most efficiency ratios aren't surfaced by ScreeningEngine; we
        # compute the easy ones from DCF/CF inputs.
        {"cat": "Efficiency", "label": "Asset turnover",
         "our": lambda b: None,  # Total assets not in dcf bundle; FMP-only for now
         "fmp": "assetTurnoverTTM", "kind": "ratio"},
        {"cat": "Efficiency", "label": "EPS growth rate",
         "our": lambda b: _pct_from_pct_units(_bundle_get(b, "screening/eps_growth_rate")),
         "fmp": None, "kind": "pct"},
        {"cat": "Efficiency", "label": "Revenue CAGR",
         "our": lambda b: _pct_from_pct_units(_bundle_get(b, "screening/revenue_cagr_(robust)")),
         "fmp": None, "kind": "pct"},

        # ── Cash flow ──────────────────────────────────────────────────
        {"cat": "Cash flow", "label": "FCF yield",
         "our": lambda b: _safe_div(_our_fcf(b), _our_market_cap(b)),
         "fmp": "freeCashFlowYieldTTM", "kind": "pct"},
        {"cat": "Cash flow", "label": "Margin of safety",
         "our": lambda b: _pct_from_pct_units(_bundle_get(b, "screening/margin_of_safety")),
         "fmp": None, "kind": "pct"},

        # ── Per share ──────────────────────────────────────────────────
        {"cat": "Per share", "label": "Revenue / share",
         "our": lambda b: _safe_div(_bundle_get(b, "dcf/revenue"), _bundle_get(b, "dcf/shares_diluted")),
         "fmp": "revenuePerShareTTM", "kind": "ratio"},
        {"cat": "Per share", "label": "FCF / share",
         "our": lambda b: _safe_div(_our_fcf(b), _bundle_get(b, "dcf/shares_diluted")),
         "fmp": "freeCashFlowPerShareTTM", "kind": "ratio"},
        {"cat": "Per share", "label": "Current price",
         "our": lambda b: _bundle_get(b, "dcf/current_price"),
         "fmp": None, "kind": "ratio"},
    ]


def _drift_status(drift: Optional[float]) -> str:
    """Color-coded status for a drift value (signed fraction)."""
    if drift is None:
        return "—"
    d = abs(drift)
    if d < 0.01:
        return "✓ <1%"
    if d < 0.05:
        return "◧ <5%"
    if d < 0.20:
        return "⚠ <20%"
    return "✗ ≥20%"


def _render_fmp_parity_panel(
    bundle: Dict[str, Any], ticker: str, *, force_refresh: bool = False,
) -> None:
    """Side-by-side: every comparable calc vs FMP's TTM equivalent.

    Both sides operate on the same FMP-sourced data — so drift here
    reflects ONLY differences in how we compute the metric vs how FMP
    computes it. A green ✓ row means our formula matches FMP's. A red
    ✗ row means a formula divergence worth investigating.
    """
    fmp = _load_fmp_ttm_metrics(ticker, force_refresh=force_refresh)
    if not fmp:
        st.warning(
            f"No FMP TTM ratios/key-metrics cache for {ticker}. The "
            "parity panel needs `ratios_ttm` and `key_metrics_ttm` "
            "payloads on disk or fetched live."
        )
        return

    spec = _parity_spec()

    # Build category-grouped rows.
    by_cat: Dict[str, List[Dict[str, Any]]] = {}
    n_total = 0
    n_resolved = 0
    n_ge_5pct = 0
    n_ge_20pct = 0
    for s in spec:
        try:
            ours = s["our"](bundle)
        except Exception:  # noqa: BLE001
            ours = None
        fmp_val = fmp.get(s["fmp"])
        # Some FMP "pct" fields are decimal fractions already (0.31),
        # others come as percent (31). All the *Margin*TTM / *RatioTTM
        # ratio fields are decimal fractions in FMP. Our values follow
        # the same convention. _fmt_metric(kind=pct) handles both.
        drift = _drift_pct(ours, fmp_val)
        if ours is not None and fmp_val is not None:
            n_resolved += 1
            if drift is not None:
                if abs(drift) >= 0.05: n_ge_5pct += 1
                if abs(drift) >= 0.20: n_ge_20pct += 1
        n_total += 1
        by_cat.setdefault(s["cat"], []).append({
            "Metric": s["label"],
            "Ours": _fmt_metric(ours, s["kind"]),
            "FMP": _fmt_metric(fmp_val, s["kind"]),
            "Drift": f"{drift * 100:+.1f}%" if drift is not None else "—",
            "Status": _drift_status(drift),
        })

    # Top summary strip.
    st.markdown("### 🔎 FMP parity — side-by-side calc validation")
    cols = st.columns(4)
    cols[0].metric("Metrics compared", n_total)
    cols[1].metric(
        "Resolved (both sides)", f"{n_resolved}/{n_total}",
    )
    cols[2].metric(
        "Drift ≥ 5%", n_ge_5pct,
        delta=None if n_ge_5pct == 0 else f"-{n_ge_5pct}",
        delta_color="inverse",
    )
    cols[3].metric(
        "Drift ≥ 20%", n_ge_20pct,
        delta=None if n_ge_20pct == 0 else f"-{n_ge_20pct}",
        delta_color="inverse",
    )
    st.caption(
        "Both sides operate on the SAME FMP-sourced data. Drift = "
        "formula divergence (not a data-quality issue). ✓ <1% — full "
        "agreement; ◧ <5% — rounding-tier; ⚠ <20% — formula nuance "
        "(weighted vs simple averages, gross-debt vs net-debt, etc.); "
        "✗ ≥20% — meaningful formula divergence worth investigating."
    )

    for cat in (
        "Valuation", "Profitability", "Leverage", "Liquidity",
        "Efficiency", "Cash flow", "Per share",
    ):
        rows = by_cat.get(cat) or []
        if not rows:
            continue
        with st.expander(
            f"{cat}  ·  {len(rows)} metric(s)", expanded=(cat == "Valuation"),
        ):
            st.dataframe(rows, use_container_width=True, hide_index=True)


def _render_coverage_panel(coverage: Dict[str, Any]) -> None:
    if not coverage:
        return
    with st.expander(
        "Field coverage (which canonical fields the adapter populated)",
        expanded=False,
    ):
        st.caption(
            "Fraction of years the FMP adapter resolved each canonical "
            "field. Gaps here explain why downstream engines may have "
            "no output."
        )
        n_years = coverage.get("n_years", 0)
        field_cov = coverage.get("field_coverage") or {}
        rows: List[Dict[str, Any]] = []
        for k, n in sorted(field_cov.items()):
            rows.append({
                "Canonical field": k,
                "Years populated": f"{n} / {n_years}",
                "Coverage": (
                    "✓" if n == n_years else
                    "◧" if n >= n_years * 0.7 else "◌"
                ),
            })
        st.dataframe(rows, use_container_width=True, hide_index=True)


_DIFF_SESSION_KEY = "_cross_source_diff_result"


def _render_cross_source_diff_panel(ticker: str) -> None:
    """P6 — Cross-source diff: run all 3 providers + show drift table.

    On-demand (button-gated) because running every provider takes 3-5s
    total. Diff result is cached per session per ticker so the analyst
    can scroll without re-running.
    """
    st.markdown("### 🔍 Cross-source diff (FMP ↔ XBRL ↔ Hybrid)")
    st.caption(
        "Run Stage 3 against all three providers and diff key metrics. "
        "✓ <1% — full agreement · ◧ <5% — rounding · ⚠ <20% — formula "
        "nuance · ✗ ≥20% — investigate (or expected Cat-D divergence)."
    )

    cols = st.columns([2, 5])
    run_clicked = cols[0].button(
        f"🔍 Run cross-source diff for {ticker}",
        use_container_width=True,
        key="diff_run_btn",
    )

    if run_clicked:
        from aletheia.validation.cross_source_diff import run_cross_source_diff
        with st.spinner(
            "Running Stage 3 via FMP, XBRL, and Hybrid providers..."
        ):
            result = run_cross_source_diff(ticker)
        st.session_state[_DIFF_SESSION_KEY] = result

    cached = st.session_state.get(_DIFF_SESSION_KEY)
    if not cached:
        st.info("Click to run all three providers and compare bundle metrics.")
        return
    if cached.get("ticker") != ticker:
        st.warning(
            f"Cached diff is for **{cached.get('ticker')}**; "
            f"re-run for {ticker}."
        )
        return

    summary = cached.get("summary") or {}
    sumcols = st.columns(5)
    sumcols[0].metric("✓ Agree (<1%)",   summary.get("ok", 0))
    sumcols[1].metric("◧ Rounding (<5%)", summary.get("minor", 0))
    sumcols[2].metric("⚠ Nuance (<20%)",  summary.get("notable", 0))
    sumcols[3].metric("✗ Material (≥20%)", summary.get("material", 0))
    sumcols[4].metric("◌ Unresolved",     summary.get("unresolved", 0))

    rows = cached.get("metrics") or []
    if not rows:
        return

    table_rows: List[Dict[str, Any]] = []
    for r in rows:
        cat_chip = " 📐 Cat-D" if r.get("cat_d") else ""
        table_rows.append({
            "Metric":  r["metric"] + cat_chip,
            "FMP":     r.get("fmp", "—"),
            "XBRL":    r.get("xbrl", "—"),
            "Hybrid":  r.get("hybrid", "—"),
            "Drift (FMP↔XBRL)": r.get("drift", "—"),
            "Tier":    r.get("tier", "—"),
        })
    st.dataframe(table_rows, use_container_width=True, hide_index=True)

    # Surface unexpected material drift prominently — Cat-D rows are
    # already documented in the Layer 2 registry; anything else is
    # a real finding for the analyst.
    unexpected_material = [
        r for r in rows
        if r["tier"].startswith("✗") and not r.get("cat_d")
    ]
    if unexpected_material:
        st.error(
            f"⚠️ {len(unexpected_material)} metric(s) show ≥20% drift "
            "between providers AND are NOT documented Cat-D divergences "
            "— worth investigating: "
            + ", ".join(r["metric"] for r in unexpected_material)
        )
    else:
        st.caption(
            "📐 Cat-D rows (ROIC, ROE, Net Debt) are documented "
            "methodology divergences — see Layer 2 registry. All other "
            "metrics agree within tolerance."
        )
