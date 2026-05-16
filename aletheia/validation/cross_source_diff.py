"""Shadow-mode cross-source diff report.

Runs Stage 3 against every registered provider on the same ticker and
diffs key bundle metrics, classifying disagreements as:

  ✓ ok       — within 1% (rounding tier)
  ◧ minor    — within 5% (timestamp / TTM-alignment residual)
  ⚠ notable  — within 20% (formula-nuance territory; sometimes Cat-D)
  ✗ material — ≥20% drift (worth investigating)

Three Category-D divergences are documented + accepted by design:
``ROIC``, ``ROE``, ``Net Debt / EBITDA`` — see Layer 2 registry. The
report flags these explicitly so the analyst sees "expected divergence"
vs "investigate this".

Output shape is a plain dict so the same payload powers the UI panel
and CLI inspection. Each metric row carries a per-provider value plus
the FMP↔XBRL drift (Hybrid is shown for completeness; it matches FMP
on flow metrics by construction).
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from aletheia.validation.stage3_isolated import run_stage3_isolated


# Per-metric comparison spec. Each entry:
#   label    — analyst-facing name (matches Layer 2 registry where
#              applicable so methodology lookup is clickable)
#   path     — slash-separated path into the CalculationBundle dict
#   kind     — "pct" (decimal → %), "usd" (large-number → $X.XXB),
#              "ratio" (raw 2-decimal), "int" (count)
#   cat_d    — True when divergence here is a documented methodology
#              choice, not a bug. ROIC / ROE / NetDebt-EBITDA are
#              empirically validated (see registry).
_METRICS_SPEC: List[Dict[str, Any]] = [
    # Top-line flows — should agree closely across all providers
    {"label": "Revenue (latest)",  "path": "dcf/revenue",      "kind": "usd", "cat_d": False},
    {"label": "EBIT",              "path": "dcf/ebit",         "kind": "usd", "cat_d": False},
    {"label": "EBITDA",            "path": "dcf/ebitda",       "kind": "usd", "cat_d": False},
    {"label": "NOPAT",             "path": "dcf/nopat",        "kind": "usd", "cat_d": False},
    {"label": "FCF",               "path": "dcf/fcf",          "kind": "usd", "cat_d": False},
    {"label": "Market cap",        "path": "dcf/market_cap",   "kind": "usd", "cat_d": False},
    {"label": "Net debt",          "path": "dcf/net_debt",     "kind": "usd", "cat_d": True},
    # DCF outputs
    {"label": "WACC",                  "path": "dcf/wacc_base",                "kind": "pct",   "cat_d": False},
    {"label": "Beta",                  "path": "dcf/beta",                     "kind": "ratio", "cat_d": False},
    {"label": "Risk-free rate",        "path": "dcf/risk_free_rate",           "kind": "pct",   "cat_d": False},
    {"label": "ROIC",                  "path": "dcf/roic",                     "kind": "pct",   "cat_d": True},
    {"label": "Current price",         "path": "dcf/current_price",            "kind": "ratio", "cat_d": False},
    {"label": "Shares diluted",        "path": "dcf/shares_diluted",           "kind": "usd",   "cat_d": False},
    {"label": "IV per share (base)",   "path": "dcf/base_intrinsic_per_share", "kind": "ratio", "cat_d": False},
    {"label": "IV upside (base)",      "path": "dcf/base_upside",              "kind": "pct",   "cat_d": False},
    # RDCF
    {"label": "RDCF implied 10Y CAGR", "path": "reverse_dcf/implied_cagr_10y", "kind": "pct",   "cat_d": False},
    {"label": "RDCF EBIT margin",      "path": "reverse_dcf/ebit_margin",      "kind": "pct",   "cat_d": False},
]


def _bundle_get(bundle: Dict[str, Any], path: str) -> Any:
    cur: Any = bundle
    for part in path.split("/"):
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _drift_pct(a: Any, b: Any) -> Optional[float]:
    """Relative drift (a − b) / |b|. Returns None when either side is
    non-numeric or denominator is too small to be meaningful."""
    try:
        x = float(a); y = float(b)
    except (TypeError, ValueError):
        return None
    if abs(y) < 1e-9:
        return None
    return (x - y) / abs(y)


def _drift_tier(drift: Optional[float]) -> str:
    """Classify a drift fraction into a tier label."""
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


def _fmt(v: Any, kind: str) -> str:
    if v is None:
        return "—"
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return str(v)
    if not (fv == fv):  # NaN
        return "—"
    if kind == "pct":
        return f"{fv * 100:.2f}%"
    if kind == "usd":
        if abs(fv) >= 1e9:
            return f"${fv / 1e9:.2f}B"
        if abs(fv) >= 1e6:
            return f"${fv / 1e6:.2f}M"
        return f"${fv:,.0f}"
    if kind == "ratio":
        return f"{fv:.2f}"
    if kind == "int":
        return f"{int(fv):,}"
    return f"{fv:g}"


def run_cross_source_diff(
    ticker: str,
    *,
    providers: Optional[List[str]] = None,
    run_fn: Optional[Callable] = None,
) -> Dict[str, Any]:
    """Run Stage 3 via every provider and produce a comparison payload.

    Args:
        ticker: Symbol to compare.
        providers: Override the default provider set (``["fmp", "xbrl",
            "hybrid"]``). Useful for tests that want to skip slow paths.
        run_fn: Test-injection hook — accepts ``(ticker, provider=...)``
            and returns the same shape as ``run_stage3_isolated``. When
            None, defaults to the real runner.

    Returns:
        Dict with:
          - ``ticker``                 (input)
          - ``providers``              (list of provider names actually run)
          - ``per_provider``           ({provider_name: result_dict})
          - ``metrics``                (list of comparison rows — UI-ready)
          - ``summary``                (counts of ok / minor / notable / material)
          - ``error``                  (str when a critical provider failed)
    """
    providers = providers or ["fmp", "xbrl", "hybrid"]
    runner = run_fn or run_stage3_isolated

    # Run each provider sequentially. Disable audit writes since the
    # diff is the artifact we care about — bundles per provider get
    # written by the regular Stage 3 validation tab when needed.
    per_provider: Dict[str, Dict[str, Any]] = {}
    for p in providers:
        per_provider[p] = runner(ticker, provider=p, write_audit=False)

    # Build comparison rows. FMP↔XBRL drift is the canonical diff
    # (the other-source compare); Hybrid is shown for completeness.
    rows: List[Dict[str, Any]] = []
    counts = {"ok": 0, "minor": 0, "notable": 0, "material": 0, "unresolved": 0}
    for spec in _METRICS_SPEC:
        values = {
            p: _bundle_get(per_provider[p].get("bundle") or {}, spec["path"])
            for p in providers
        }
        fmp_v = values.get("fmp")
        xbrl_v = values.get("xbrl")
        drift = _drift_pct(fmp_v, xbrl_v)
        tier = _drift_tier(drift)
        # Bucket the row for the summary strip
        if drift is None:
            counts["unresolved"] += 1
        elif abs(drift) < 0.01:
            counts["ok"] += 1
        elif abs(drift) < 0.05:
            counts["minor"] += 1
        elif abs(drift) < 0.20:
            counts["notable"] += 1
        else:
            counts["material"] += 1

        row = {
            "metric": spec["label"],
            "kind": spec["kind"],
            "cat_d": spec["cat_d"],
            "drift_pct": drift,
            "tier": tier,
        }
        for p in providers:
            row[p] = _fmt(values[p], spec["kind"])
        # Drift only renders when both sides resolved
        row["drift"] = (
            f"{drift * 100:+.1f}%" if drift is not None else "—"
        )
        rows.append(row)

    return {
        "ticker": ticker,
        "providers": providers,
        "per_provider": per_provider,
        "metrics": rows,
        "summary": counts,
        "error": None,
    }


__all__ = ["run_cross_source_diff"]
