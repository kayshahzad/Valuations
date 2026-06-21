"""Bank valuation as a convergent set — the financial-sector analog of the
four-method FCFF convergence (`valuation_methods.py`).

A bank's equity has no unlevered free-cash-flow kernel (deposits are raw
material, not debt; "FCFF" is meaningless), so the FCFF four-method engine
refuses it. Its canonical convergent set is instead:

  Residual income : BVPS₀ + Σ PV[(ROE−Ke)·BVPSₜ₋₁] + PV[terminal RI]
  Justified P/B   : (ROE − g)/(Ke − g)  →  × BVPS₀   (steady-state multiple)
  Gordon DDM      : DPS₁/(Ke − g) = payout·ROE·BVPS₀/(Ke − g)

THE GOLDEN IDENTITY (the bank analog of the FCFF $9,868 convergence): under
constant ROE with consistent growth g = ROE·(1−payout) < Ke, all three are
ALGEBRAICALLY IDENTICAL — clean-surplus accounting makes residual income a
re-arrangement of the dividend stream, and justified P/B is their closed form:

    BVPS₀·(ROE − g)/(Ke − g)  ≡  payout·ROE·BVPS₀/(Ke − g)   (∵ ROE − g = payout·ROE)

So convergence is the correctness test, exactly as for FCFF. The legs diverge in
practice for two structural reasons this module surfaces rather than hides:

  1. Low payout depresses the two-stage *config* DDM far below value. JPM's DDM
     headline (~$118) sees only the ~29% paid out; it ignores the 6pp ROE-spread
     compounding on the ~71% retained. Residual income captures it (~$367).
  2. Near-term g = ROE·retention can EXCEED Ke (JPM: 16.3%·0.71 = 11.5% > 10%),
     so a single-stage justified P/B / Gordon is undefined (negative denominator).
     The two-stage residual income is the only well-posed form — it sums a finite
     super-growth explicit phase, then fades to a terminal g < Ke.

Phase 1 discipline: every input (BVPS, ROE, retention, Ke) is DERIVED from the
cleaned frame + the engine's own CAPM Ke, not hand-curated — so the bank
convergent set refreshes on a plain rebuild. Payout falls back through a logged
chain (clean → derived → config) because `clean_PayoutRatio` is often NaN for
bank filers. This is an ADDITIVE diagnostic: the routed DdmEngine remains the
headline IV; this reconciles against it and flags the low-payout understatement.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

# Sectors whose equity is valued off book + ROE rather than FCFF.
_FINANCIAL_MODELS = ("ddm_required", "embedded_value_required")

# Terminal growth must sit a safe margin below Ke or the Gordon denominator
# explodes. Long-run nominal GDP-ish default, matching the DDM config terminals.
_DEFAULT_TERMINAL_GROWTH = 0.04
_MIN_KE_MINUS_G = 0.015          # floor on (Ke − g_terminal)
_DEFAULT_EXPLICIT_YEARS = 10


# ─────────────────────────── core formulas ────────────────────────────────
# The residual-income / justified-P/B / Gordon math now lives in
# aletheia.calculations.formulas.residual_income (shared with ResidualIncomeEngine).
# Re-exported here so existing call sites and tests that import them from this
# module keep working.
from aletheia.calculations.formulas import (          # noqa: E402
    gordon_ddm_value,
    justified_pb,
    residual_income_value,
)


@dataclass(frozen=True)
class BankConvergence:
    iv_residual_income: float
    iv_justified_pb: float
    iv_gordon_ddm: float
    max_spread_pct: float
    converged: bool


def value_bank_steady_state(*, bvps0: float, roe: float, payout: float,
                            ke: float, explicit_years: int = 60,
                            tol: float = 0.005) -> BankConvergence:
    """The golden bank convergence — constant ROE, g = ROE·(1−payout) < Ke.

    Proves residual income ≡ justified-P/B·BVPS ≡ Gordon DDM. This is the bank
    analog of the FCFF four-method $9,868 fixture: a steady-state firm valued
    three ways must return one number, or the machinery is wrong."""
    retention = 1.0 - payout
    g = roe * retention
    if ke - g <= 0:
        raise ValueError(
            f"steady-state convergence requires g < Ke (got g={g:.4f}, Ke={ke:.4f}); "
            "use the two-stage residual_income_value for super-growth banks")

    ri = residual_income_value(
        bvps0=bvps0, roe=roe, ke=ke, retention=retention,
        explicit_years=explicit_years, terminal_roe=roe, terminal_growth=g)["iv"]
    jpb = justified_pb(roe=roe, ke=ke, growth=g) * bvps0
    gordon = gordon_ddm_value(bvps0=bvps0, roe=roe, ke=ke, payout=payout)

    vals = [ri, jpb, gordon]
    lo = min(vals)
    spread = (max(vals) - lo) / abs(lo) if lo else float("inf")
    return BankConvergence(ri, jpb, gordon, spread, spread <= tol)


# ─────────────────────── deterministic orchestrator ───────────────────────

def _latest_non_nan(series) -> Optional[float]:
    for x in reversed(list(series)):
        try:
            if x is not None and not math.isnan(float(x)):
                return float(x)
        except (TypeError, ValueError):
            continue
    return None


def _normalized_roe(df, n: int = 3) -> tuple[Optional[float], Optional[float]]:
    """(normalized ROE = mean of last ``n`` non-NaN, latest ROE). Smooths the
    single-year noise a bank's ROE carries (loan-loss swings, one-off marks)."""
    if "derived_ROE" not in df.columns:
        return None, None
    vals = [float(x) for x in df.sort_values("fiscal_year")["derived_ROE"]
            if x is not None and not (isinstance(x, float) and math.isnan(x))]
    if not vals:
        return None, None
    latest = vals[-1]
    window = vals[-n:]
    return (sum(window) / len(window)), latest


def _resolve_payout(df, calc, ticker) -> tuple[Optional[float], str]:
    """Payout fallback chain (clean_PayoutRatio is frequently NaN for banks):
      1. clean_PayoutRatio        (latest non-NaN)
      2. DPS / EPS                (clean_DividendsPerShare / clean_EPS_Diluted)
      3. config payout_ratio_pct  (specialized_valuation_inputs.json)
      4. config DPS / derived EPS (NetIncome / shares)
    Returns (payout, source-label)."""
    if "clean_PayoutRatio" in df.columns:
        v = _latest_non_nan(df["clean_PayoutRatio"])
        if v is not None and 0.0 < v < 1.5:
            return v, "clean_PayoutRatio"
    dps = _latest_non_nan(df["clean_DividendsPerShare"]) if "clean_DividendsPerShare" in df.columns else None
    eps = _latest_non_nan(df["clean_EPS_Diluted"]) if "clean_EPS_Diluted" in df.columns else None
    if dps and eps and eps > 0:
        return max(0.0, min(1.5, dps / eps)), "derived (DPS/EPS)"
    # config fallbacks
    try:
        from aletheia.calculations.specialized_inputs import load_specialized_inputs
        cfg = load_specialized_inputs(ticker)
        params = getattr(cfg, "params", None) or {}
        pr = params.get("payout_ratio_pct")
        if pr is not None:
            return float(pr) / 100.0, "config payout_ratio_pct"
        cdps = params.get("current_dps_annualized")
        if cdps:
            ni = _latest_non_nan(df["raw_NetIncome"]) if "raw_NetIncome" in df.columns else None
            sh = _latest_non_nan(df["raw_SharesDiluted"]) if "raw_SharesDiluted" in df.columns else None
            if ni and sh and ni > 0:
                return max(0.0, min(1.5, float(cdps) / (ni / sh))), "config DPS / derived EPS"
    except Exception:
        pass
    return None, "unavailable"


def _resolve_ke(valuation_result, p2) -> tuple[Optional[float], str]:
    """Reuse the DDM engine's own Ke so the convergent set shares the headline's
    discount rate (CAPM or the bank-specific override). Fall back to p2['wacc']."""
    snap = getattr(valuation_result, "inputs_snapshot", None) or {}
    ke = snap.get("cost_of_equity")
    if ke is not None:
        src = "override" if snap.get("ke_override_used") else "CAPM (Rf + β·MRP)"
        return float(ke), f"DDM engine Ke ({src})"
    if p2 and p2.get("wacc") is not None:
        return float(p2["wacc"]), "phase2 cost_of_equity"
    return None, "unavailable"


def build_bank_valuation_methods(calc, valuation_result=None, p2=None) -> dict:
    """Deterministic bank convergent set (Phase 0/1), ADDITIVE to the headline
    DDM. Gated on financial-sector business models; non-banks get available=False
    and nothing renders. Mirrors `build_valuation_methods` (FCFF) in shape."""
    out = {"available": False}
    cls = getattr(calc, "classification", None)
    model = getattr(cls, "business_model", None)
    if model not in _FINANCIAL_MODELS:
        out["notes"] = "not a financial-sector engine (FCFF uses the four-method set)"
        return out

    df = getattr(calc, "df", None)
    if df is None or df.empty:
        out["notes"] = "no frame"
        return out
    ticker = getattr(cls, "ticker", "") or ""

    try:
        df = df.sort_values("fiscal_year")
        equity = _latest_non_nan(df["raw_TotalEquity"]) if "raw_TotalEquity" in df.columns else None
        shares = (_latest_non_nan(df["raw_SharesDiluted"]) if "raw_SharesDiluted" in df.columns else None) \
            or (_latest_non_nan(df["clean_SharesDiluted"]) if "clean_SharesDiluted" in df.columns else None)
        if not equity or not shares:
            out["notes"] = "missing total equity or shares — cannot derive BVPS"
            return out
        bvps0 = equity / shares
        if bvps0 <= 0:
            out["notes"] = f"non-positive book value per share ({bvps0:.2f})"
            return out

        roe_norm, roe_latest = _normalized_roe(df)
        if roe_norm is None:
            out["notes"] = "no derived_ROE in frame"
            return out

        payout, payout_src = _resolve_payout(df, calc, ticker)
        if payout is None:
            out["notes"] = "could not resolve payout ratio (no clean/derived/config source)"
            return out
        retention = max(0.0, min(1.0, 1.0 - payout))

        ke, ke_src = _resolve_ke(valuation_result, p2)
        if ke is None:
            out["notes"] = "no cost of equity available (engine Ke / phase2 wacc both absent)"
            return out

        # Deterministic terminal assumptions (overridable via config later).
        explicit_years = _DEFAULT_EXPLICIT_YEARS
        terminal_growth = _DEFAULT_TERMINAL_GROWTH
        try:
            from aletheia.calculations.specialized_inputs import load_specialized_inputs
            cfg = load_specialized_inputs(ticker)
            cp = getattr(cfg, "params", None) or {}
            if cp.get("explicit_years"):
                explicit_years = int(cp["explicit_years"])
            if cp.get("terminal_growth_pct") is not None:
                terminal_growth = float(cp["terminal_growth_pct"]) / 100.0
        except Exception:
            pass
        # Terminal growth must clear Ke by a safe margin.
        terminal_growth = max(0.0, min(terminal_growth, ke - _MIN_KE_MINUS_G))
        terminal_roe = roe_norm                       # persistence (documented)

        # ── the three legs ────────────────────────────────────────────────
        ri = residual_income_value(
            bvps0=bvps0, roe=roe_norm, ke=ke, retention=retention,
            explicit_years=explicit_years, terminal_roe=terminal_roe,
            terminal_growth=terminal_growth)
        near_term_g = ri["near_term_growth"]

        jpb_mult = justified_pb(roe=terminal_roe, ke=ke, growth=terminal_growth)
        iv_jpb_steady = (jpb_mult * bvps0) if jpb_mult is not None else None

        single_stage_ok = (ke - near_term_g) > 0
        gordon = gordon_ddm_value(bvps0=bvps0, roe=roe_norm, ke=ke,
                                  payout=payout) if single_stage_ok else None

        # ── reconciliation vs the routed headline DDM ─────────────────────
        ddm_iv = None
        if valuation_result is not None and getattr(valuation_result, "engine", "") == "ddm":
            ddm_iv = (float(valuation_result.intrinsic_per_share)
                      if valuation_result.intrinsic_per_share is not None else None)
        elif p2 and (p2.get("dcf") or {}).get("base_intrinsic_per_share") is not None and p2.get("engine") == "ddm":
            ddm_iv = float(p2["dcf"]["base_intrinsic_per_share"])

        ddm_vs_ri = ((ddm_iv / ri["iv"] - 1.0) if (ddm_iv and ri["iv"]) else None)
        understatement = (ddm_vs_ri is not None and ddm_vs_ri < -0.15)

        # Fair-value band: the steady-state floor (justified P/B on today's book)
        # to the fuller two-stage residual income. DDM is the cash-distribution
        # view and sits below for low payers — reported, not blended in.
        band = sorted(v for v in (iv_jpb_steady, ri["iv"]) if v is not None)
        fair_band = [band[0], band[-1]] if band else None

        notes = [
            "deterministic bank convergent set (residual income / justified P/B / "
            "Gordon DDM) — additive diagnostic, not the headline IV.",
        ]
        if not single_stage_ok:
            notes.append(
                f"near-term g = ROE·retention = {near_term_g:.1%} ≥ Ke {ke:.1%}: "
                "single-stage justified P/B / Gordon are undefined; the two-stage "
                "residual income is the only well-posed form (super-growth bank).")
        if understatement:
            notes.append(
                f"headline DDM (${ddm_iv:,.0f}) sits {abs(ddm_vs_ri):.0%} below "
                f"residual income (${ri['iv']:,.0f}): the config two-stage sees only "
                f"the {payout:.0%} payout, missing the {(roe_norm-ke):.1%} ROE-spread "
                "compounding on retained book. Residual income is the fuller read.")

        out.update({
            "available": True,
            "basis": "deterministic (ROE/BVPS/payout/Ke derived from financials)",
            "inputs": {
                "bvps0": bvps0,
                "roe_normalized": roe_norm,
                "roe_latest": roe_latest,
                "payout": payout,
                "payout_source": payout_src,
                "retention": retention,
                "ke": ke,
                "ke_source": ke_src,
                "explicit_years": explicit_years,
                "near_term_growth": near_term_g,
                "terminal_roe": terminal_roe,
                "terminal_growth": terminal_growth,
            },
            "methods": {
                "residual_income": {
                    "iv": ri["iv"],
                    "implied_pb": ri["implied_pb"],
                    "decomposition": ri["decomposition"],
                },
                "justified_pb": {
                    "multiple": jpb_mult,
                    "iv_steady_state": iv_jpb_steady,
                    "valid": jpb_mult is not None,
                    "note": "terminal (ROE−g)/(Ke−g) on today's book — steady-state "
                            "floor; ignores the near-term super-growth the RI captures",
                },
                "gordon_ddm": {
                    "iv": gordon,
                    "valid": gordon is not None,
                    "note": ("single-stage Gordon off normalized ROE/payout"
                             if single_stage_ok else
                             "undefined: near-term g ≥ Ke (use two-stage RI)"),
                },
            },
            "headline_ddm": {
                "iv": ddm_iv,
                "source": "DdmEngine (config two-stage)",
            },
            "reconciliation": {
                "ddm_vs_residual_income_pct": ddm_vs_ri,
                "low_payout_understatement": understatement,
                "fair_value_band": fair_band,
            },
            "convergence": {
                "steady_state_identity": "constant ROE with g<Ke → RI ≡ "
                                         "justified-P/B·BVPS ≡ Gordon (golden test)",
                "near_term_excess_growth": not single_stage_ok,
            },
            "notes": notes,
        })
    except Exception as e:                              # never break the report
        out = {"available": False,
               "notes": f"bank-valuation-methods failed: {type(e).__name__}: {e}"}
    return out


__all__ = [
    "justified_pb",
    "gordon_ddm_value",
    "residual_income_value",
    "value_bank_steady_state",
    "build_bank_valuation_methods",
    "BankConvergence",
]
