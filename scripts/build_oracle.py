#!/usr/bin/env python3
"""Phase-0 regression oracle (fix-plan task 0.1.1).

Snapshots, per universe ticker, the cleaned financial fields AND the
deterministically-recomputed valuation outputs (WACC / NOPAT / scenario IPS /
justified multiple), from cache only — DuckDB `company_records_latest` via
`make_calc_input` → `DCFEngine.run` (or `ValuationRouter` for specialized
engines). No live FMP, no LLM (spike 0.1.0 confirmed the path is pure), so the
snapshot is re-runnable after a code change and any delta is attributable to
the change.

Dual override state (per the adopted recommendation):
  * `pure`      — apply_overrides=False, the authoritative Phase-1 regression
                  signal (isolates code changes from analyst what-ifs).
  * `overrides` — apply_overrides=True, what the UI/goldens actually show.

Usage:
    python scripts/build_oracle.py            # -> scratch/oracle_baseline.json
    python scripts/build_oracle.py -o PATH
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Silence the pipeline's info/debug chatter so the run is clean.
logging.disable(logging.WARNING)

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Cleaned fields captured from the calc-input df's latest period row. These are
# exactly the columns the engine and the serving JSON read.
_CLEAN_FIELDS = [
    "clean_Revenue", "clean_NOPAT", "clean_EBITDA", "clean_FCF", "clean_FCFF",
    "derived_OperatingIncome", "derived_EBITDA", "derived_Depreciation_Total",
    "derived_CapEx", "derived_FCF", "derived_FCFF", "derived_ROIC", "derived_ROE",
    "derived_NetDebt", "derived_InvestedCapital", "derived_GrossMargin_Pct",
    "derived_EBIT_Margin_Pct", "derived_EBITDA_Margin_Pct", "derived_FCF_Margin_Pct",
    "raw_Revenue", "raw_TotalAssets", "raw_TotalEquity", "raw_CapEx",
]


def _f(x):
    """Coerce to float or None (NaN → None)."""
    try:
        if x is None:
            return None
        v = float(x)
        return v if v == v else None  # NaN check
    except (TypeError, ValueError):
        return None


def _sorted(df):
    sort_key = "period_end_date" if "period_end_date" in df.columns else "fiscal_year"
    return df.sort_values(sort_key)


def _row_fields(row, df) -> dict:
    out = {f: _f(row.get(f)) for f in _CLEAN_FIELDS if f in df.columns}
    out["_period"] = str(row.get("period")) if "period" in df.columns else None
    out["_period_end_date"] = str(row.get("period_end_date")) if "period_end_date" in df.columns else None
    out["_fiscal_year"] = _f(row.get("fiscal_year"))
    return out


def _capture_cleaned(df) -> dict:
    """Capture TWO rows: the freshest period (TTM flows) and the latest FY row
    (where the clean_ normalization fields — NOPAT/EBITDA — live; TTM rows skip
    them by design). They coincide when no TTM row is present."""
    d = _sorted(df)
    latest = d.iloc[-1]
    if "period" in df.columns:
        fy = d[d["period"] == "FY"]
        fy_row = fy.iloc[-1] if not fy.empty else latest
    else:
        fy_row = latest
    return {"latest": _row_fields(latest, df), "latest_fy": _row_fields(fy_row, df)}


def _capture_valuation(calc) -> dict:
    """Recompute the deterministic valuation block. Mirrors _compute_dcf_live /
    _compute_specialized_live field extraction so the oracle matches the app."""
    from aletheia.tools.dcf_engine import DCFEngine
    from aletheia.tools.multiple_decomposition import MultipleDecomposition

    val: dict = {}
    try:
        result = DCFEngine(verbose=False).run(calc)
        val["engine"] = "fcff"
        val["wacc_base"] = _f(getattr(result, "wacc_base", None))
        val["beta"] = _f(getattr(result, "beta", None))
        val["risk_free_rate"] = _f(getattr(result, "risk_free_rate", None))
        val["current_price"] = _f(getattr(result, "current_price", None))
        val["market_cap"] = _f(getattr(result, "market_cap", None))
        val["shares_diluted"] = _f(getattr(result, "shares_diluted", None))
        val["net_debt"] = _f(getattr(result, "net_debt", None))
        nd = getattr(result, "net_debt", None)
        for name in ("bear", "base", "bull"):
            sc = getattr(result, name, None)
            if sc is None:
                val[name] = None
                continue
            ev = _f(getattr(sc, "enterprise_value", None))
            ips = result.intrinsic_per_share(ev, nd) if ev is not None else None
            ips = _f(ips)
            mos = _f(result.upside(ips)) if ips is not None else None
            val[name] = {"ev": ev, "ips": ips, "mos": mos}
        ba = getattr(getattr(result, "base", None), "assumptions", None)
        if ba is not None:
            val["base_assumptions"] = {
                "wacc": _f(getattr(ba, "wacc", None)),
                "ebit_margin_terminal": _f(getattr(ba, "ebit_margin_terminal", None)),
                "terminal_growth": _f(getattr(ba, "terminal_growth", None)),
            }
    except NotImplementedError:
        # Specialized engine (NEE rate-base, DDM banks, BRK-B embedded value…).
        val["engine"] = "specialized"
        try:
            from aletheia.tools.valuation_router import ValuationRouter
            vr = ValuationRouter().execute(calc)
            val["engine"] = getattr(vr, "engine", "specialized")
            val["ips_base"] = _f(getattr(vr, "intrinsic_per_share", None))
            val["current_price"] = _f(getattr(vr, "current_price", None))
            val["mos"] = _f(getattr(vr, "margin_of_safety", None))
        except Exception as e:  # empty-state (CNC), KNOWN_ISSUES bypass (V)
            val["error"] = f"{type(e).__name__}: {e}"
    except Exception as e:
        val["engine"] = "error"
        val["error"] = f"{type(e).__name__}: {e}"

    try:
        md = MultipleDecomposition(verbose=False).run(calc)
        val["multiple"] = {
            "justified_ev_ebitda": _f(getattr(md, "justified_ev_ebitda", None)),
            "market_ev_ebitda": _f(getattr(md, "market_ev_ebitda", None)),
            "roic": _f(getattr(md, "roic", None)),
            "wacc": _f(getattr(md, "wacc", None)),
            "roic_wacc_spread": _f(getattr(md, "roic_wacc_spread", None)),
        }
    except Exception:
        val["multiple"] = None
    return val


def _snapshot_ticker(ticker: str, apply_overrides: bool) -> dict:
    from aletheia.utils.calc_input_builder import make_calc_input
    calc = make_calc_input(ticker, apply_overrides=apply_overrides)
    if calc.df is None or calc.df.empty:
        return {"error": "empty_df"}
    return {"cleaned": _capture_cleaned(calc.df), "valuation": _capture_valuation(calc)}


def _git_sha() -> str:
    try:
        import subprocess
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, text=True).strip()
    except Exception:
        return "unknown"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=str(ROOT / "scratch" / "oracle_baseline.json"))
    args = ap.parse_args()

    from config.ticker_classification import get_extended_universe
    tickers = sorted(get_extended_universe().keys())

    out = {
        "_meta": {
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "git_sha": _git_sha(),
            "n_tickers": len(tickers),
            "override_states": ["pure", "overrides"],
            "note": "pure=apply_overrides False (authoritative); overrides=True (UI parity)",
        },
        "tickers": {},
    }
    n_ok = n_err = 0
    for t in tickers:
        rec = {}
        for state, ov in (("pure", False), ("overrides", True)):
            try:
                rec[state] = _snapshot_ticker(t, ov)
            except Exception as e:
                rec[state] = {"error": f"{type(e).__name__}: {e}"}
        # Count a ticker as OK if pure valuation produced any engine result.
        eng = (rec.get("pure", {}).get("valuation", {}) or {}).get("engine")
        if eng in (None, "error") or "error" in rec.get("pure", {}):
            n_err += 1
        else:
            n_ok += 1
        out["tickers"][t] = rec
        print(f"  {t:6s} pure={eng}", file=sys.stderr)

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w") as fh:
        json.dump(out, fh, indent=2, sort_keys=True)
    print(f"\nWrote {args.out}  ({len(tickers)} tickers · ok={n_ok} err/specialized-empty={n_err})",
          file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
