"""
aletheia/ui/validation_badge.py

Per-ticker, per-field validation-status lookup for the Streamlit UI.

When the analyst sees a number in the dashboard or screening tab, an asterisk
on the label means: "this value (or the underlying raw input) has been
externally cross-checked against SEC XBRL and/or FMP, and matches within
tolerance." A bare label means we display the value but it hasn't passed an
external check at this layer (could be missing-on-validator-side, schema-
specific, or a documented definitional drift).

The status is computed once per ticker per session and cached in
`st.session_state.validation_status[ticker]`. Re-running the validator from
the Quality Report's "Re-run validation" button will refresh it.

Each entry is one of:
  - "validated"  → SEC byte-perfect (or FMP within tolerance for ratios)
  - "near"        → FMP within 1–5% (definitional difference, both correct)
  - "drift"       → >5% drift (documented or unresolved)
  - "missing"     → field absent on either side
  - "unknown"     → not validated yet
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

try:
    import streamlit as st  # type: ignore
    _HAS_STREAMLIT = True
except Exception:  # pragma: no cover
    st = None  # type: ignore
    _HAS_STREAMLIT = False


# Map between display labels (what UI shows) and validator field labels.
# Aliases let one display label resolve to either SEC or FMP, whichever
# has the strongest signal.
_ALIASES: Dict[str, list] = {
    # Bottom-line raw fields — SEC byte-perfect when validated
    "Revenue":                ["Revenue"],
    "Net Income":             ["Net Income"],
    "Total Assets":           ["Total Assets"],
    "Total Liabilities":      ["Total Liabilities"],
    "Total Equity":           ["Total Equity"],
    "Cash":                   ["Cash"],
    "Long-Term Debt":         ["Long-Term Debt"],
    "Operating CF":           ["Operating CF"],
    # Statement lines — FMP only
    "EBITDA":                 ["EBITDA"],
    "EBIT":                   ["Operating Income"],
    "Operating Income":       ["Operating Income"],
    "FCF":                    ["Free Cash Flow"],
    "Free Cash Flow":         ["Free Cash Flow"],
    "CapEx":                  ["CapEx"],
    "Diluted EPS":            ["Diluted EPS"],
    "Diluted Shares":         ["Diluted Shares"],
    "Buybacks":               ["Buybacks"],
    "Dividends Paid":         ["Dividends Paid"],
    "Accounts Receivable":    ["Accounts Receivable"],
    "Inventory":              ["Inventory"],
    "PPE Net":                ["PPE Net"],
    "Goodwill":               ["Goodwill"],
    "Short-Term Debt":        ["Short-Term Debt"],
    "Short-Term Investments": ["Short-Term Investments"],
    "Accounts Payable":       ["Accounts Payable"],
    "Current Assets":         ["Current Assets"],
    "Current Liabilities":    ["Current Liabilities"],
    "COGS":                   ["COGS"],
    "Gross Profit":           ["Revenue", "COGS"],  # composite
    # Derived / screening ratios — FMP screening section
    "Gross Margin":           ["Gross Margin %"],
    "Gross Margin %":         ["Gross Margin %"],
    "EBIT Margin":            ["EBIT Margin %"],
    "EBIT Margin %":          ["EBIT Margin %"],
    "EBITDA Margin":          ["EBITDA Margin %"],
    "EBITDA Margin %":        ["EBITDA Margin %"],
    "ROIC":                   ["ROIC"],
    "ROE":                    ["ROE"],
    "Invested Capital":       ["Invested Capital"],
    "P/E":                    ["P/E Ratio"],
    "P/E Ratio":              ["P/E Ratio"],
    "P/B":                    ["P/B Ratio"],
    "P/B Ratio":              ["P/B Ratio"],
    "EV/EBITDA":              ["EV/EBITDA"],
    "EV/FCF":                 ["EV/FCF"],
    "Debt-to-Equity":         ["Debt-to-Equity"],
    "Interest Coverage":      ["Interest Coverage"],
    "Current Ratio":          ["Current Ratio"],
    "Net Debt / EBITDA":      ["Net Debt / EBITDA"],
    "Dividend Yield":         ["Dividend Yield %"],
    "Dividend Yield %":       ["Dividend Yield %"],
    "EV":                     ["EV ($B)"],
    "EV (Billions)":          ["EV ($B)"],
    "Market Cap":             ["Market Cap ($B)"],
    "Market Cap (Billions)":  ["Market Cap ($B)"],
}


def _flag_to_status(flag: str) -> str:
    if flag == "✓":
        return "validated"
    if flag == "≈":
        return "near"
    if flag == "✗":
        return "drift"
    if flag in ("ours_missing", "fmp_missing", "sec_missing", "—", "n/a (schema)"):
        return "missing"
    return "unknown"


def get_validation_status(ticker: str, force_refresh: bool = False) -> Dict[str, str]:
    """
    Return {validator_label: status} for the given ticker. Pulled from
    session_state.validation_status cache; populated lazily by running
    SEC + FMP validators if not already cached.

    Cheap to call repeatedly — first call per ticker takes a few seconds
    (re-runs validators), subsequent calls are dict lookups.
    """
    if not ticker:
        return {}

    if not _HAS_STREAMLIT:
        # Out-of-Streamlit context: build fresh, no caching
        return _build_status_map(ticker)

    cache = st.session_state.setdefault("validation_status", {})
    if force_refresh or ticker not in cache:
        cache[ticker] = _build_status_map(ticker)
    return cache[ticker]


def _build_status_map(ticker: str) -> Dict[str, str]:
    """Run both validators and produce a flat label→status dict."""
    status: Dict[str, str] = {}
    fy: Optional[int] = None

    # SEC first
    try:
        from scripts.validate_sec import validate_ticker as sec_validate
        sec = sec_validate(ticker)
        if not sec.get("error"):
            fy = sec.get("fiscal_year")
            for r in sec.get("rows", []):
                label = r.get("label")
                if label:
                    status[label] = _flag_to_status(r.get("flag", ""))
    except Exception:
        pass

    # FMP (overrides SEC where stronger — i.e., upgrade missing→validated when FMP confirms)
    try:
        from scripts.validate_fmp import validate_ticker as fmp_validate
        fmp = fmp_validate(ticker, fy=fy)
        if not fmp.get("error"):
            for sect in ("income", "balance", "cashflow", "derived", "screening"):
                for r in (fmp.get(sect) or []):
                    label = r.get("label")
                    if not label:
                        continue
                    new_status = _flag_to_status(r.get("flag", ""))
                    existing = status.get(label)
                    # Strongest wins: validated > near > drift > missing > unknown
                    rank = {"validated": 4, "near": 3, "drift": 2, "missing": 1, "unknown": 0}
                    if rank.get(new_status, 0) > rank.get(existing or "", 0):
                        status[label] = new_status
    except Exception:
        pass

    return status


def lookup_status(ticker: str, display_label: str) -> str:
    """Return the highest-confidence status for a UI display label."""
    statuses = get_validation_status(ticker)
    aliases = _ALIASES.get(display_label, [display_label])
    rank = {"validated": 4, "near": 3, "drift": 2, "missing": 1, "unknown": 0}
    best = "unknown"
    for alias in aliases:
        s = statuses.get(alias, "unknown")
        if rank.get(s, 0) > rank.get(best, 0):
            best = s
    return best


# ── Display helpers ──────────────────────────────────────────────────────

# Asterisk-style markers. Plain ASCII so they render in any st.metric label.
_MARKER = {
    "validated": " *",
    "near":      " ~",
    "drift":     " ⚠",
    "missing":   "",
    "unknown":   "",
}


def badge_label(label: str, ticker: Optional[str]) -> str:
    """
    Return `label + asterisk` if the value is externally validated for
    `ticker`. Use as a drop-in replacement for the label in `st.metric`,
    table headers, etc.
    """
    if not ticker:
        return label
    status = lookup_status(ticker, label)
    return f"{label}{_MARKER.get(status, '')}"


def status_marker(ticker: str, label: str) -> str:
    """Just the marker, no label — useful when concatenating in HTML/markdown."""
    return _MARKER.get(lookup_status(ticker, label), "")


LEGEND_MD = (
    "**Validation badges:** "
    "`*` = externally validated against SEC XBRL or FMP within 1% · "
    "`~` = within 5% (documented definitional difference) · "
    "`⚠` = >5% drift to investigate. "
    "Bare labels mean the value is shown but not yet cross-checked at that layer "
    "(typically schema-specific or pending revalidation)."
)


# ────────────────────────────────────────────────────────────────────────────
# Convention-change flag — Phase 1 of formula centralization
#
# Surfaces a small marker beside affected metric labels so analysts know
# the number's methodology was canonicalized on a known date. Date-gated
# retirement (set at definition time) means the flag disappears
# automatically once the convention has had time to settle — no code
# change required.
#
# Spec: docs/methodology_changes/2026-05-roic-ui-flag-spec.md
# ────────────────────────────────────────────────────────────────────────────

import datetime as _dt

_CONVENTION_FLAG_RETIRES = "2026-12-31"
_CONVENTION_FLAG = "📐 2026-05"
CONVENTION_TOOLTIP = (
    "Convention canonicalized 2026-05. ROIC now excludes excess cash "
    "from invested capital. See docs/methodology_changes/"
    "2026-05-roic-invested-capital.md. Flag retires 2026-12-31."
)
_CONVENTION_AFFECTED_METRICS = frozenset({
    "ROIC",
    "Invested Capital",
    "InvestedCapital",
})


def convention_flag(metric_label: str) -> str:
    """Return the convention-canonicalized flag suffix (with leading
    space), or empty string when the metric isn't affected or the
    flag has retired.

    Use as: ``f"ROIC{convention_flag('ROIC')}"`` → ``"ROIC 📐 2026-05"``
    until 2026-12-31, then bare ``"ROIC"``.
    """
    if metric_label not in _CONVENTION_AFFECTED_METRICS:
        return ""
    if _dt.date.today() > _dt.date.fromisoformat(_CONVENTION_FLAG_RETIRES):
        return ""
    return f" {_CONVENTION_FLAG}"


# ────────────────────────────────────────────────────────────────────────────
# Full legend
#
# The same emoji glyphs (🟢/🟡/🔴/⚪) appear in several places with related
# but distinct meanings. Documenting all of them in one place — used by the
# sidebar's "ℹ︎ Visual legend" expander.
# ────────────────────────────────────────────────────────────────────────────

LEGEND_VALIDATION = (
    "**Validation status** — Financials · Deep Dive · Quality Report\n\n"
    "- 🟢 validated against SEC XBRL or FMP within 1%\n"
    "- 🟡 within 1–5% (documented definitional difference)\n"
    "- 🔴 >5% drift — investigate\n"
    "- ⚪ field absent on the validator side (FMP doesn't expose it)\n"
    "- · not yet validated this session"
)

LEGEND_ASTERISK = (
    "**Asterisk markers (legacy)** — Dashboard · Screening tab\n\n"
    "Same meaning as the dots above, just rendered inline with metric labels.\n\n"
    "- `*` validated within 1%\n"
    "- `~` within 5% (definitional)\n"
    "- `⚠` >5% drift\n"
    "- (none) not yet validated"
)

LEGEND_AGENT_LIFECYCLE = (
    "**Agent lifecycle** — Universe tab `Status` column\n\n"
    "Tracks whether a ticker has had the LangGraph LLM workflow run.\n\n"
    "- 🟢 **ready** — agents have produced a `_report.json` "
    "(conviction, pillar score, narrative all populated)\n"
    "- 🟡 **pending** — DB rows exist (calc-layer ready) but agents haven't "
    "run yet; conviction / moat / pillars show as `—`\n"
    "- ⚪ **not ingested** — classified but no DB rows (rare; SEC fetch failed)"
)

LEGEND_DCF_METHOD = (
    "**DCF method** — Deep Dive pill below the ticker name\n\n"
    "Indicates which valuation framework actually applies; standard FCFF "
    "DCF doesn't fit every schema.\n\n"
    "- 🟢 **FCFF DCF** — standard Free Cash Flow to Firm DCF (default path)\n"
    "- 🟡 **Dividend Discount** — float-based business (insurance, managed care)\n"
    "- 🟡 **Embedded Value** — life insurance specifically\n"
    "- 🔴 **Schema-specific** — bank, utility, or conglomerate (FCFF DCF "
    "doesn't apply; needs efficiency ratio + NIM + ROTCE for banks, "
    "rate-base + allowed-ROE for utilities, segment-level for conglomerates)"
)

LEGEND_OTHER = (
    "**Other indicators**\n\n"
    "- ✅ / ⚠ / ❌ — Constitution checks (framework rule compliance, Deep Dive bottom)\n"
    "- 🔴 banner at top of Deep Dive — at cyclical peak (z-score > 2σ)\n"
    "- 🟡 banner — elevated cycle position (1σ < z ≤ 2σ)"
)


def render_full_legend() -> None:
    """Render every visual-language legend block in the Streamlit sidebar.
    Each section is a sub-expander so the sidebar stays compact."""
    try:
        import streamlit as _st
    except Exception:
        return

    _st.markdown(LEGEND_VALIDATION)
    _st.markdown("---")
    _st.markdown(LEGEND_ASTERISK)
    _st.markdown("---")
    _st.markdown(LEGEND_AGENT_LIFECYCLE)
    _st.markdown("---")
    _st.markdown(LEGEND_DCF_METHOD)
    _st.markdown("---")
    _st.markdown(LEGEND_OTHER)
