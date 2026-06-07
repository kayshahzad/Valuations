"""DCF-assumption validation — the gate before recompute and Stage 4.

Validates the *effective* DCF assumptions (model-derived merged with any
analyst overrides) so an incoherent set never reaches the terminal-value
math or — more expensively — the Stage 4 LLM agents that build a thesis on
top of them.

The thresholds here deliberately MIRROR the DCFEngine's own constants
(``MAX_TERMINAL_G``, the 200bps minimum terminal spread, the
``ScenarioOverride`` field bounds). "Valid" therefore means "the engine
will produce a coherent, non-divergent valuation" — not a second,
independent opinion that could disagree with the math the engine runs.

Four layers:
  1. Per-field bounds (mirror ScenarioOverride validators).
  2. Cross-field economic invariants (Gordon spread, terminal-growth cap,
     margin decay, growth fade). These protect the TV math.
  3. Plausibility vs. the model baseline (warn-only): how far an override
     strays from the value it replaced.
  4. Provenance/stale handled by the API layer (needs DB state).

Severity → gate behavior:
  error → block recompute (422) AND Stage 4 refuses to run.
  warn  → allowed; surfaced in the Stage 4 confirmation panel.
  ok    → clean.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

# Mirror the engine's hard cap so validation and math never disagree.
try:
    from aletheia.tools.dcf_engine import MAX_TERMINAL_G
except Exception:  # pragma: no cover - defensive: keep validator importable
    MAX_TERMINAL_G = 0.04

# The engine enforces a 200bps minimum WACC−g spread (MIN_TERMINAL_SPREAD,
# a local in dcf_engine) and treats ≤1% as explosive. Mirrored here.
MIN_TERMINAL_SPREAD = 0.02
EXPLOSIVE_SPREAD = 0.01

# Per-field bounds, keyed by the *assumptions-bundle* field name. These
# match the ScenarioOverride validator bands one-to-one.
_BOUNDS: Dict[str, tuple] = {
    "revenue_cagr_y1_5":    (0.0, 0.45),
    "revenue_cagr_y6_10":   (0.0, 0.25),
    "ebit_margin_terminal": (0.0, 0.65),
    "capex_pct_revenue":    (0.0, 0.50),
    "tax_rate":             (0.0, 0.40),
    "wacc":                 (0.04, 0.16),
    "terminal_growth":      (-0.02, 0.06),
    "terminal_roic":        (0.05, 0.60),
}

# Fields whose absence is NOT an error (engine derives them when unset).
_OPTIONAL_BOUNDS = {"terminal_roic"}

# Assumptions-bundle key → the ScenarioOverride field that overrides it.
ASSUMPTION_TO_OVERRIDE: Dict[str, str] = {
    "revenue_cagr_y1_5":    "revenue_growth_y1_5",
    "revenue_cagr_y6_10":   "revenue_growth_y6_10",
    "ebit_margin_terminal": "terminal_ebit_margin",
    "capex_pct_revenue":    "capex_pct_revenue",
    "tax_rate":             "tax_rate",
    "wacc":                 "discount_rate",
    "terminal_growth":      "terminal_growth",
    "terminal_roic":        "terminal_roic",
}

_LABELS = {
    "revenue_cagr_y1_5": "CAGR Y1-5",
    "revenue_cagr_y6_10": "CAGR Y6-10",
    "ebit_margin_terminal": "EBIT margin term.",
    "capex_pct_revenue": "CapEx % revenue",
    "tax_rate": "Tax rate",
    "wacc": "WACC",
    "terminal_growth": "Terminal growth",
    "terminal_roic": "Terminal ROIC",
}


@dataclass
class FieldVerdict:
    name: str
    value: Optional[float]
    overridden: bool
    verdict: str  # "ok" | "warn" | "error"
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": _LABELS.get(self.name, self.name),
            "value": self.value,
            "overridden": self.overridden,
            "verdict": self.verdict,
            "message": self.message,
        }


@dataclass
class ValidationResult:
    status: str  # "ok" | "warn" | "error"
    fields: List[FieldVerdict] = field(default_factory=list)
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    overridden: List[str] = field(default_factory=list)

    @property
    def is_error(self) -> bool:
        return self.status == "error"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "fields": [f.to_dict() for f in self.fields],
            "errors": self.errors,
            "warnings": self.warnings,
            "overridden": self.overridden,
        }


def _pct(v: Optional[float]) -> str:
    return f"{v*100:.1f}%" if v is not None else "—"


def validate_dcf_assumptions(
    assumptions: Dict[str, Any],
    overridden_fields: Optional[Set[str]] = None,
    *,
    terminal_growth_cap: Optional[float] = None,
    baseline: Optional[Dict[str, Any]] = None,
) -> ValidationResult:
    """Validate the effective DCF assumptions.

    Args:
        assumptions: the effective assumptions bundle (model-derived merged
            with overrides) — keys per ``ui/financials.py`` (revenue_cagr_y1_5,
            wacc, terminal_growth, ...).
        overridden_fields: set of *override* field names (ScenarioOverride
            keys) currently applied, used to badge fields and scope Layer 3.
        terminal_growth_cap: the ticker's lifecycle terminal-growth cap
            (profile.terminal_growth_cap), if known.
        baseline: model-derived assumptions BEFORE overrides, for Layer-3
            plausibility comparison. Optional; Layer 3 is skipped if absent.

    Returns:
        ValidationResult with per-field verdicts and a rolled-up status.
    """
    overridden_overrides = overridden_fields or set()
    # Translate override field names → assumptions-bundle keys for badging.
    overridden_bundle_keys = {
        a for a, o in ASSUMPTION_TO_OVERRIDE.items()
        if o in overridden_overrides
    }

    errors: List[str] = []
    warnings: List[str] = []
    field_verdicts: List[FieldVerdict] = []

    def _verdict_for(name: str) -> tuple:
        """Return (verdict, message) for a single field's bounds check."""
        v = assumptions.get(name)
        if v is None:
            # terminal_roic is optional — the engine derives it (hold-base-ROIC)
            # when no override is set, so its absence is not an error.
            if name in _OPTIONAL_BOUNDS:
                return "ok", ""
            return "error", f"{_LABELS.get(name, name)} is missing"
        lo, hi = _BOUNDS[name]
        if not (lo <= v <= hi):
            # Optional fields' bands constrain ANALYST OVERRIDES only. The
            # engine's model-derived value can legitimately sit outside the
            # override band (e.g. NVDA/AAPL hold base ROIC ~75% > the 60%
            # override cap) — that is not a validation error.
            if name in _OPTIONAL_BOUNDS and name not in overridden_bundle_keys:
                return "ok", ""
            return "error", (
                f"{_LABELS.get(name, name)} {_pct(v)} outside allowed "
                f"[{_pct(lo)}, {_pct(hi)}]"
            )
        return "ok", ""

    # ── Layer 1 — per-field bounds ──────────────────────────────────────
    for name in _BOUNDS:
        verdict, msg = _verdict_for(name)
        if verdict == "error":
            errors.append(msg)
        field_verdicts.append(FieldVerdict(
            name=name, value=assumptions.get(name),
            overridden=name in overridden_bundle_keys,
            verdict=verdict, message=msg,
        ))

    fv_by_name = {f.name: f for f in field_verdicts}

    def _escalate(name: str, verdict: str, msg: str) -> None:
        """Raise a field's verdict (ok→warn→error, never downgrade) and
        record the message in the right bucket."""
        order = {"ok": 0, "warn": 1, "error": 2}
        fv = fv_by_name.get(name)
        if fv and order[verdict] > order[fv.verdict]:
            fv.verdict = verdict
            fv.message = msg
        (errors if verdict == "error" else warnings).append(msg)

    g = assumptions.get("terminal_growth")
    wacc = assumptions.get("wacc")
    margin_now = assumptions.get("ebit_margin_current")
    margin_term = assumptions.get("ebit_margin_terminal")
    y1_5 = assumptions.get("revenue_cagr_y1_5")
    y6_10 = assumptions.get("revenue_cagr_y6_10")
    troic = assumptions.get("terminal_roic")

    # ── Layer 2 — cross-field economic invariants ──────────────────────
    if g is not None and wacc is not None:
        spread = wacc - g
        if spread <= 0:
            _escalate("terminal_growth", "error",
                      f"Terminal growth {_pct(g)} ≥ WACC {_pct(wacc)} — "
                      "Gordon terminal value diverges (negative/infinite).")
        elif spread <= EXPLOSIVE_SPREAD:
            _escalate("terminal_growth", "error",
                      f"WACC−g spread {_pct(spread)} ≤ {_pct(EXPLOSIVE_SPREAD)} "
                      "— terminal value explodes; not a usable valuation.")
        elif spread < MIN_TERMINAL_SPREAD:
            _escalate("terminal_growth", "warn",
                      f"WACC−g spread {_pct(spread)} below the engine's "
                      f"{_pct(MIN_TERMINAL_SPREAD)} floor — TV dominates EV; "
                      "the engine will clamp g upward.")

    if g is not None:
        if g > MAX_TERMINAL_G:
            _escalate("terminal_growth", "error",
                      f"Terminal growth {_pct(g)} exceeds the hard cap "
                      f"{_pct(MAX_TERMINAL_G)} (perpetual growth can't durably "
                      "outpace nominal GDP).")
        elif (terminal_growth_cap is not None
              and g > terminal_growth_cap
              and terminal_growth_cap < MAX_TERMINAL_G):
            _escalate("terminal_growth", "warn",
                      f"Terminal growth {_pct(g)} exceeds this ticker's "
                      f"lifecycle cap {_pct(terminal_growth_cap)} — requires "
                      "megatrend justification.")

    if margin_now is not None and margin_term is not None and margin_term > margin_now + 1e-9:
        _escalate("ebit_margin_terminal", "warn",
                  f"Terminal EBIT margin {_pct(margin_term)} exceeds current "
                  f"{_pct(margin_now)} — margins are assumed to expand in "
                  "perpetuity, not decay. Confirm this is intended.")

    if y1_5 is not None and y6_10 is not None and y6_10 > y1_5 + 1e-9:
        _escalate("revenue_cagr_y6_10", "warn",
                  f"Y6-10 CAGR {_pct(y6_10)} exceeds Y1-5 {_pct(y1_5)} — "
                  "growth accelerates rather than fades. Unusual.")

    # Terminal ROIC feeds the Liberti reinvestment rate (g / ROIC). It must
    # exceed terminal growth, else reinvestment ≥ 100% of NOPAT (nonsensical);
    # and ROIC ≤ WACC means terminal reinvestment destroys value.
    if troic is not None and g is not None and troic <= g:
        _escalate("terminal_roic", "error",
                  f"Terminal ROIC {_pct(troic)} ≤ terminal growth {_pct(g)} — "
                  "implied reinvestment rate ≥ 100% of NOPAT; TV is degenerate.")
    elif troic is not None and wacc is not None and troic < wacc:
        _escalate("terminal_roic", "warn",
                  f"Terminal ROIC {_pct(troic)} below WACC {_pct(wacc)} — "
                  "terminal reinvestment destroys value. Confirm a deliberate "
                  "fade-to/below-WACC thesis.")

    # ── Layer 3 — plausibility vs. model baseline (warn-only) ───────────
    if baseline:
        base_y1_5 = baseline.get("revenue_cagr_y1_5")
        if ("revenue_cagr_y1_5" in overridden_bundle_keys
                and y1_5 is not None and base_y1_5 is not None
                and base_y1_5 > 0 and y1_5 > 2.0 * base_y1_5):
            _escalate("revenue_cagr_y1_5", "warn",
                      f"Y1-5 CAGR {_pct(y1_5)} is over 2× the model-derived "
                      f"{_pct(base_y1_5)}.")
        base_wacc = baseline.get("wacc")
        if ("wacc" in overridden_bundle_keys
                and wacc is not None and base_wacc is not None
                and abs(wacc - base_wacc) > 0.03):
            _escalate("wacc", "warn",
                      f"WACC {_pct(wacc)} deviates >300bps from the CAPM-derived "
                      f"{_pct(base_wacc)}.")
        base_tax = baseline.get("tax_rate")
        if ("tax_rate" in overridden_bundle_keys
                and assumptions.get("tax_rate") is not None and base_tax is not None
                and abs(assumptions["tax_rate"] - base_tax) > 0.10):
            _escalate("tax_rate", "warn",
                      f"Tax rate {_pct(assumptions['tax_rate'])} deviates >10pp "
                      f"from the model-derived {_pct(base_tax)}.")

    status = "error" if errors else ("warn" if warnings else "ok")
    return ValidationResult(
        status=status,
        fields=field_verdicts,
        errors=errors,
        warnings=warnings,
        overridden=sorted(overridden_bundle_keys),
    )


__all__ = [
    "validate_dcf_assumptions",
    "ValidationResult",
    "FieldVerdict",
    "ASSUMPTION_TO_OVERRIDE",
    "MAX_TERMINAL_G",
    "MIN_TERMINAL_SPREAD",
]
