"""
aletheia/ui/quality_report.py

Comprehensive Quality Report screen for the Streamlit dashboard.

For any ticker (typically the most-recently-added one), this screen renders:

  1. Header strip — overall quality score, FY range, ingest timestamp,
     warning + error counts.
  2. Pipeline run log — if the ticker was added through the UI orchestrator,
     the per-step status messages (ingest / SEC validate / FMP validate)
     captured in `session_state.last_add_ticker_full`.
  3. Cleaning-domain scorecard — D1–D10 scores out of 1.0 with iconography
     and per-domain explanations.
  4. SEC XBRL validation — full per-field table for the latest FY.
  5. FMP cross-source validation — full per-field tables (income / balance
     / cash flow / derived ratios).
  6. Cleaning flags — every transformation the cleaning engine applied,
     grouped by domain, with the issuer-level reason text.
  7. Warnings + errors — structured lists from `warnings_json` /
     `errors_json` recorded at clean-time.

Designed to be the first stop after adding a new ticker — answers the
question "is this ticker's data trustworthy?" in one view.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

import pandas as pd
import streamlit as st


_DOMAIN_LABELS: Dict[str, str] = {
    "D1_NonRecurring":    "D1 Non-Recurring Stripping",
    "D2_JVA":             "D2 JVA Separation",
    "D3_EBITNorm":        "D3 EBIT Normalization",
    "D4_AccountingPolicy":"D4 Accounting Policy",
    "D5_Lease":           "D5 Lease Normalization",
    "D6_Pension":         "D6 Pension Cleaning",
    "D7_SBC":             "D7 SBC Adjustment",
    "D8_Revenue":         "D8 Revenue Recognition",
    "D9_WorkingCapital":  "D9 Working Capital",
    "D10_Tax":            "D10 Tax Sustainability",
}


def _quality_icon(score: Optional[float]) -> str:
    if score is None:
        return "—"
    if score >= 0.9:
        return "✅"
    if score >= 0.7:
        return "⚠️"
    return "❌"


def _load_record(ticker: str) -> Optional[Dict[str, Any]]:
    """Pull the latest cleaned record + scores + cleaning flags from DuckDB."""
    try:
        import duckdb
        con = duckdb.connect("valuation_data/database/investment.duckdb", read_only=True)
    except Exception:
        return None
    try:
        # Latest row from the view
        cols = [r[0] for r in con.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='company_records'"
        ).fetchall()]
        row = con.execute(
            "SELECT * FROM company_records_latest WHERE ticker=? "
            "ORDER BY fiscal_year DESC LIMIT 1", [ticker.upper()],
        ).fetchone()
        if not row:
            return None
        view_cols = [d[0] for d in con.description]
        record = dict(zip(view_cols, row))
        # Fiscal-year coverage
        years = [r[0] for r in con.execute(
            "SELECT fiscal_year FROM company_records_latest WHERE ticker=? "
            "ORDER BY fiscal_year", [ticker.upper()],
        ).fetchall()]
        # Cleaning flags for the latest FY
        latest_fy = int(record.get("fiscal_year") or 0)
        flags = con.execute(
            "SELECT domain, domain_name, metric, raw_value, adjusted_value, "
            "action, reason, confidence FROM cleaning_flags "
            "WHERE ticker=? AND fiscal_year=? ORDER BY domain",
            [ticker.upper(), latest_fy],
        ).fetchall()
        flag_cols = [d[0] for d in con.description]
        flag_rows = [dict(zip(flag_cols, f)) for f in flags]
    finally:
        con.close()

    # Domain scores
    domain_scores = {
        k: record.get(f"domain_score_{k}")
        for k in _DOMAIN_LABELS
    }

    return {
        "record":         record,
        "fiscal_years":   years,
        "latest_fy":      latest_fy,
        "domain_scores":  domain_scores,
        "cleaning_flags": flag_rows,
        "warnings":       _safe_json_list(record.get("warnings_json")),
        "errors":         _safe_json_list(record.get("errors_json")),
    }


def _safe_json_list(blob: Optional[str]) -> List[Any]:
    if not blob:
        return []
    try:
        v = json.loads(blob)
        return v if isinstance(v, list) else []
    except Exception:
        return []


def _validation_table(payload: Optional[Dict[str, Any]], source: str) -> Optional[pd.DataFrame]:
    """Flatten an SEC or FMP validation payload to a DataFrame."""
    if not payload or payload.get("error"):
        return None
    if source == "sec":
        rows = [{"section": "raw", **r} for r in (payload.get("rows") or [])]
    else:
        rows = []
        for sect in ("income", "balance", "cashflow", "derived"):
            for r in (payload.get(sect) or []):
                rows.append({"section": sect, **r})
    if not rows:
        return None
    return pd.DataFrame([
        {
            "section": r.get("section"),
            "metric":  r.get("label"),
            "value":   r.get("ours"),
            "ref":     r.get("sec") if source == "sec" else r.get("fmp"),
            "drift":   (f"{r['drift']*100:+.2f}%"
                        if isinstance(r.get("drift"), (int, float)) and r["drift"] != float("inf")
                        else "—"),
            "flag":    r.get("flag"),
        }
        for r in rows
    ])


def _validation_counts(payload: Optional[Dict[str, Any]], source: str) -> Dict[str, int]:
    if not payload or payload.get("error"):
        return {"ok": 0, "near": 0, "bad": 0, "missing": 0, "total": 0}
    if source == "sec":
        rows = payload.get("rows") or []
    else:
        rows = (payload.get("income", []) + payload.get("balance", [])
                + payload.get("cashflow", []) + payload.get("derived", []))
    return {
        "ok":      sum(1 for r in rows if r["flag"] == "✓"),
        "near":    sum(1 for r in rows if r["flag"] == "≈"),
        "bad":     sum(1 for r in rows if r["flag"] == "✗"),
        "missing": sum(1 for r in rows if r["flag"] in
                       ("ours_missing", "fmp_missing", "sec_missing", "—", "n/a (schema)")),
        "total":   len(rows),
    }


def _run_fresh_validation(ticker: str, fy: int) -> Dict[str, Any]:
    """Re-run SEC + FMP validators on demand. Returns both payloads."""
    try:
        from scripts.validate_sec import validate_ticker as sec_validate
        sec_result = sec_validate(ticker, fy=fy)
    except Exception as e:
        sec_result = {"error": f"{type(e).__name__}: {e}"}
    try:
        from scripts.validate_fmp import validate_ticker as fmp_validate
        fmp_result = fmp_validate(ticker, fy=fy)
    except Exception as e:
        fmp_result = {"error": f"{type(e).__name__}: {e}"}
    return {"sec": sec_result, "fmp": fmp_result}


def render_quality_report(ticker: str) -> None:
    """Render the full Quality Report screen for `ticker`."""
    if not ticker:
        st.info("Select a ticker from the sidebar, or add a new one to see its Quality Report.")
        return

    data = _load_record(ticker)
    if not data:
        st.error(
            f"No cleaned data for **{ticker}** in the database. "
            f"Use the **➕ Add Ticker** sidebar to ingest it first."
        )
        return

    record       = data["record"]
    latest_fy    = data["latest_fy"]
    years        = data["fiscal_years"]
    domain_scores = data["domain_scores"]
    flags        = data["cleaning_flags"]
    warnings     = data["warnings"]
    errors       = data["errors"]

    # ── Header strip ──────────────────────────────────────────────────────
    st.markdown(f"## Quality Report — {ticker.upper()} (FY{latest_fy})")
    fy_range = f"FY{years[0]}–FY{years[-1]}" if years else "—"
    qscore = record.get("overall_quality_score")
    qicon  = _quality_icon(qscore)

    h1, h2, h3, h4, h5 = st.columns(5)
    h1.metric("Years on file", len(years), help=fy_range)
    h2.metric("Quality score", f"{qicon} {qscore:.2f}" if qscore is not None else "—")
    h3.metric("Warnings", record.get("warning_count") or 0)
    h4.metric("Errors", record.get("error_count") or 0)
    h5.metric("Cleaned at", (record.get("cleaned_at") or "—")[:10])

    # ── Pipeline run log (if the ticker was just added) ───────────────────
    last_add = st.session_state.get("last_add_ticker_full")
    if last_add and last_add.get("ticker", "").upper() == ticker.upper():
        st.markdown("### Pipeline run")
        st.caption(
            f"Captured {last_add.get('elapsed_s', 0):.1f}s ago — "
            f"{'success' if last_add.get('success') else 'failed'}."
        )
        for ln in last_add.get("step_log", []):
            st.markdown(ln)
        st.markdown("---")

    # ── Re-validate button ────────────────────────────────────────────────
    rv1, rv2 = st.columns([1, 4])
    with rv1:
        if st.button("Re-run validation", key=f"qr_revalidate_{ticker}",
                     use_container_width=True):
            with st.spinner("Re-running SEC + FMP validators…"):
                fresh = _run_fresh_validation(ticker, latest_fy)
            st.session_state[f"qr_validation_{ticker}"] = fresh

    cached_validation = st.session_state.get(f"qr_validation_{ticker}")
    if not cached_validation and last_add and last_add.get("ticker", "").upper() == ticker.upper():
        cached_validation = {
            "sec": last_add.get("sec_validation"),
            "fmp": last_add.get("fmp_validation"),
        }

    # ── Validation summary cards ──────────────────────────────────────────
    st.markdown("### Validation summary")
    sec_payload = (cached_validation or {}).get("sec")
    fmp_payload = (cached_validation or {}).get("fmp")
    sec_n = _validation_counts(sec_payload, "sec")
    fmp_n = _validation_counts(fmp_payload, "fmp")

    sv1, sv2 = st.columns(2)
    with sv1:
        st.markdown("**SEC XBRL** — byte-perfect cross-check vs the issuer's filing")
        if sec_payload and sec_payload.get("error"):
            st.warning(sec_payload["error"])
        else:
            st.metric(
                "SEC fields validated",
                f"{sec_n['ok']}/{sec_n['total']}",
                f"{sec_n['near']} near · {sec_n['bad']} fail · {sec_n['missing']} missing",
            )
    with sv2:
        st.markdown("**FMP** — methodology cross-check vs FinancialModelingPrep")
        if fmp_payload and fmp_payload.get("error"):
            st.warning(fmp_payload["error"])
        else:
            st.metric(
                "FMP fields validated",
                f"{fmp_n['ok']}/{fmp_n['total']}",
                f"{fmp_n['near']} near · {fmp_n['bad']} fail · {fmp_n['missing']} missing/skipped",
            )

    if not cached_validation:
        st.info(
            "No validation results loaded yet. Click **Re-run validation** "
            "above, or add this ticker via the sidebar to capture them."
        )

    # ── SEC validation table ──────────────────────────────────────────────
    if sec_payload and not sec_payload.get("error"):
        with st.expander(f"SEC XBRL validation — {sec_n['ok']}/{sec_n['total']} ✓", expanded=False):
            sec_df = _validation_table(sec_payload, "sec")
            if sec_df is not None:
                st.dataframe(sec_df, hide_index=True, use_container_width=True)

    # ── FMP validation table ──────────────────────────────────────────────
    if fmp_payload and not fmp_payload.get("error"):
        with st.expander(f"FMP validation — {fmp_n['ok']}/{fmp_n['total']} ✓", expanded=False):
            fmp_df = _validation_table(fmp_payload, "fmp")
            if fmp_df is not None:
                st.dataframe(fmp_df, hide_index=True, use_container_width=True)

    # ── Cleaning-domain scorecard (D1–D10) ────────────────────────────────
    st.markdown("### Cleaning-domain scorecard")
    rows = []
    for key, label in _DOMAIN_LABELS.items():
        score = domain_scores.get(key)
        rows.append({
            "domain": label,
            "score":  f"{score:.2f}" if score is not None else "—",
            "status": _quality_icon(score),
        })
    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)

    # ── Cleaning flags (per-transformation audit trail) ──────────────────
    if flags:
        st.markdown(f"### Cleaning transformations applied ({len(flags)} flags)")
        flag_df = pd.DataFrame([
            {
                "domain":     f.get("domain_name"),
                "metric":     f.get("metric"),
                "action":     f.get("action"),
                "raw":        f.get("raw_value"),
                "adjusted":   f.get("adjusted_value"),
                "confidence": f.get("confidence"),
                "reason":     (f.get("reason") or "")[:200],
            }
            for f in flags
        ])
        st.dataframe(flag_df, hide_index=True, use_container_width=True)

    # ── Warnings + errors ─────────────────────────────────────────────────
    has_warnings_or_errors = bool(warnings) or bool(errors)
    if has_warnings_or_errors:
        st.markdown("### Issues raised at clean-time")
        if errors:
            with st.expander(f"Errors ({len(errors)})", expanded=True):
                for e in errors:
                    if isinstance(e, dict):
                        st.error(f"**{e.get('field', '?')}**: {e.get('message', '')}")
                    else:
                        st.error(str(e))
        if warnings:
            with st.expander(f"Warnings ({len(warnings)})", expanded=False):
                for w in warnings:
                    if isinstance(w, dict):
                        st.warning(f"**{w.get('field', '?')}**: {w.get('message', '')}")
                    else:
                        st.warning(str(w))
    else:
        st.success("No warnings or errors raised at clean-time.")

    # ── Coverage gaps ─────────────────────────────────────────────────────
    raw = _safe_json_dict(record.get("raw_json"))
    expected = [
        "Revenue", "COGS", "OperatingIncome", "NetIncome", "TotalAssets",
        "TotalLiabilities", "TotalEquity", "Cash", "LongTermDebt",
        "OperatingCF", "CapEx", "PPE", "AccountsReceivable", "Inventory",
        "AccountsPayable", "ShareBasedCompensation", "DilutedEPS",
        "SharesDiluted",
    ]
    missing = [k for k in expected if raw.get(k) is None]
    if missing:
        with st.expander(f"Coverage gaps in raw data ({len(missing)} of {len(expected)} fields missing)",
                         expanded=False):
            st.caption(
                "These canonical fields were not resolved by the tag mapper. "
                "Often legitimate (issuer doesn't disclose), occasionally a "
                "tag-mapping fix opportunity."
            )
            for k in missing:
                st.markdown(f"- `{k}`")


def _safe_json_dict(blob: Optional[str]) -> Dict[str, Any]:
    if not blob:
        return {}
    try:
        v = json.loads(blob)
        return v if isinstance(v, dict) else {}
    except Exception:
        return {}
