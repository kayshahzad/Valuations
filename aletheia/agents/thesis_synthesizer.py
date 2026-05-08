"""aletheia/agents/thesis_synthesizer.py

Thesis Synthesis Agent — integration layer (NOT analysis layer).

The agent's job is to STRUCTURE existing upstream signals into an
explicit investment thesis. It does NOT produce parallel analysis:
  - Bias detection is contrarian's job (cite `contrarian.bias_detected`)
  - Cyclicality measurement is calc_node's job (cite `phase2.cyclicality.z_score`)
  - Reality plausibility is reality_checks's job (when wired)
  - Multiple decomposition is calc_node's job (cite `phase2.multiple_decomposition`)

When thesis content references these dimensions, it must CITE the
specific upstream field rather than re-derive the analysis. This is
operationalized through three structural layers:

  1. **Schema-level**: Every claim field has `cited_signals: List[str]`
     that must be non-empty. Pydantic rejects unsourced claims at parse
     time.

  2. **Vocabulary regex** (post-generation): the synthesized prose must
     contain at least 2 of these canonical-signal markers:
       contrarian | reality.check | cyclicality | z.score | implied CAGR
       | multiple.decomposition | scenario_eval | validation | reverse.dcf
       | margin.of.safety | conviction
     Failure triggers a retry with stronger guidance; second failure
     surfaces "thesis_synthesizer produced unsourced output" as a
     quality flag.

  3. **AST architecture-lock** (separate test file
     `tests/architecture/test_thesis_synthesizer_no_analysis.py`):
     scans this file's imports and rejects any data-fetching primitive
     (DuckDuckGoSearchRun, search_sentiment, InvestmentDatabase,
     yfinance, requests). Locks the agent into integration mode at
     module-load time.

Persistence: writes to `agent_runs.investment_thesis` (existing JSON
column from the JSON-as-truth migration). The previous content of
that column was lead.py's narrative + conviction; thesis_synthesizer
supersedes it as the structured-thesis payload.
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import HumanMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_google_genai import ChatGoogleGenerativeAI
from pydantic import BaseModel, Field, ValidationError, model_validator

from config import MODEL_NAME, TEMPERATURE
from aletheia.utils.tracing import tracer

# ─────────────────────────────────────────────────────────────────────────────
# Catalog import — used to build the vocabulary regex programmatically
# (D4) and to compute the dashboard catalog hash (D5). Catalog is a pure-data
# module with no I/O — safe at module load. Defensive try/except so a catalog
# import failure doesn't render the synthesizer uncallable; a fallback regex
# keeps citation-discipline checks functional even when the catalog is
# unavailable for some reason.
# ─────────────────────────────────────────────────────────────────────────────
try:
    from config.qualitative_dimensions import DIMENSIONS as _DIMENSIONS
    from config.qualitative_dimensions import CATEGORIES as _CATEGORIES
    _CATALOG_LOADED = True
except Exception as _catalog_exc:   # pragma: no cover — defensive
    print(f"[thesis_synthesizer] catalog import failed, falling back: "
          f"{_catalog_exc}", file=sys.stderr)
    _DIMENSIONS = {}
    _CATEGORIES = ()
    _CATALOG_LOADED = False


# ─────────────────────────────────────────────────────────────────────────────
# Schema — structured investment thesis
# ─────────────────────────────────────────────────────────────────────────────

class DecisionCondition(BaseModel):
    """One trigger → action pair. Forces specificity over hand-waving.

    `observable` is the machine-checkable expression (e.g.
    "phase2.implied_cagr > 0.25"). Future tooling can evaluate these
    against the live calc layer to surface "thesis trigger fired" alerts."""
    model_config = {"frozen": True}

    trigger: str = Field(
        description="What change in the world / market / data would trigger action. "
                    "E.g. 'Implied CAGR exceeds 25%', 'NIM compression below 2.5%'.")
    observable: str = Field(
        description="The machine-checkable expression. E.g. 'phase2.implied_cagr > 0.25'.")
    action: Literal["exit", "trim", "hold", "add"]
    priority: Literal["red", "amber", "green"] = Field(
        description="Red = act immediately on trigger. "
                    "Amber = re-evaluate within a quarter. "
                    "Green = informational, watch only.")


class CitedClaim(BaseModel):
    """A thesis claim with mandatory upstream-signal citations.

    Empty `cited_signals` is rejected at parse time — this is the
    structural guarantee that thesis content stays grounded in upstream
    signals rather than re-deriving analysis. Citations use field paths
    (e.g. `contrarian.bias_detected`, `phase2.multiple_decomposition.signal`)
    so they're machine-checkable, not just descriptive."""
    model_config = {"frozen": True}

    claim: str = Field(
        description="The thesis claim itself, 2-4 sentences, "
                    "specific and reference-rich.")
    cited_signals: List[str] = Field(
        description="List of upstream-signal field paths the claim cites. "
                    "E.g. ['contrarian.bias_detected', 'phase2.implied_cagr', "
                    "'qualitative.forensic.moat.score']. MUST be non-empty.",
        min_length=1,
    )


class ThesisSynthesis(BaseModel):
    """The structured investment thesis output.

    Replaces what lead.py's LLM call previously produced (free-form
    growth_decay_assessment + contrarian_rebuttal). Decision-grade by
    construction: no field accepts vague prose; every claim has typed
    citations; every conditional has a machine-readable trigger."""
    model_config = {"frozen": True}

    thesis_statement: str = Field(
        description="One sentence stating the core investment thesis. "
                    "E.g. 'AAPL is a quality compounder fairly valued at "
                    "current prices, with services-driven re-rating as the "
                    "primary upside path and supply-chain disruption the main "
                    "downside risk.'")

    bull_case:  CitedClaim
    bear_case:  CitedClaim
    base_case:  CitedClaim

    decision_conditions: List[DecisionCondition] = Field(
        description="3-5 specific trigger → action conditions. Each is a "
                    "named DecisionCondition with observable expression "
                    "and priority.",
        min_length=3,
        max_length=6,
    )

    thesis_confidence: Literal[
        "high", "medium", "low", "insufficient_signal"
    ] = Field(
        description="High = signals converge across contrarian + scenarios + "
                    "qualitative. Medium = mostly converge with one notable "
                    "divergence. Low = signals diverge materially. "
                    "insufficient_signal = upstream blocks (qualitative empty, "
                    "scenarios unevaluable, etc).")

    time_horizon: Literal["3_months", "1_year", "3_years", "5_years_plus"]

    position_sizing_implications: str = Field(
        description="Reference the conviction-scorer tier (e.g. 'full / starter "
                    "/ watchlist / avoid') and explain any directional adjustment "
                    "vs that base sizing. Cite `conviction.position_tier` or "
                    "`conviction.position_size_pct` explicitly.")

    required_analyst_judgment: List[str] = Field(
        description="Gaps where the framework defers to the analyst. "
                    "E.g. 'Strategic value of services bundling not in calc layer; "
                    "needs analyst weighting'.",
        max_length=5,
    )

    update_conditions: List[str] = Field(
        description="What would invalidate this thesis. "
                    "E.g. 'New 10-K shows services revenue concentration in a "
                    "single segment', 'Reverse-DCF signal flips from caution to flag'.",
        min_length=1,
        max_length=5,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Vocabulary check — post-generation enforcement of citation discipline
# ─────────────────────────────────────────────────────────────────────────────

# A thesis grounded in upstream signals will mention at least 2 of these
# canonical markers. Failure indicates the LLM produced parallel-analysis
# prose instead of citing upstream conclusions.
#
# The pattern is built programmatically from the dimension catalog so that
# adding a new dashboard dimension automatically expands acceptable
# vocabulary on next module load (D4). Composite markers
# (`{cat_id}_composite`) are synthesized from `CATEGORIES`. A fallback
# pattern matching the legacy hardcoded list is used if the catalog
# couldn't be imported.

_LEGACY_CANONICAL_MARKERS = (
    "contrarian", "reality.check", "cyclicality", "z.score",
    r"z[-\s]?score", r"implied\s+CAGR", "multiple.decomposition",
    "scenario_eval", "validation", "reverse.dcf", "margin.of.safety",
    "conviction", "phase2", "qualitative", r"upstream\s+signal",
)


def _build_vocabulary_pattern() -> re.Pattern:
    """Build the canonical-marker regex from the live catalog.

    Returns a case-insensitive pattern matching: legacy upstream markers,
    each dim_id (escaped), each `{cat_id}_composite` (escaped). Falls
    back to the legacy-only pattern when the catalog isn't loaded.
    """
    parts: List[str] = list(_LEGACY_CANONICAL_MARKERS)
    if _CATALOG_LOADED:
        parts.extend(re.escape(dim_id) for dim_id in _DIMENSIONS.keys())
        parts.extend(re.escape(f"{cat_id}_composite")
                     for cat_id, _ in _CATEGORIES)
    return re.compile(r"\b(" + "|".join(parts) + r")\b", re.IGNORECASE)


_VOCABULARY_PATTERN = _build_vocabulary_pattern()
_VOCABULARY_MIN = 2


# ── Catalog hash (D5) ──────────────────────────────────────────────────
# Aggregate hash over per-dim catalog hashes. Stamped onto thesis metadata
# so historical theses remain interpretable against the catalog version
# they were generated under.

def _aggregate_catalog_hash() -> str:
    if not _CATALOG_LOADED:
        return ""
    payload = {dim_id: dim.catalog_hash() for dim_id, dim in _DIMENSIONS.items()}
    encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()[:16]


def _per_dim_catalog_hashes() -> Dict[str, str]:
    if not _CATALOG_LOADED:
        return {}
    return {dim_id: dim.catalog_hash() for dim_id, dim in _DIMENSIONS.items()}


# ── Module-load git SHA (D5) ───────────────────────────────────────────
# Mirrors the pattern in `aletheia/agents/lead.py` and
# `aletheia/qualitative/runner.py`. Captured once at import.

def _capture_git_sha() -> Optional[str]:
    try:
        import subprocess
        from pathlib import Path
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
            cwd=Path(__file__).resolve().parents[2],
        )
        sha = out.stdout.strip()
        return sha if sha and out.returncode == 0 else None
    except Exception:
        return None


_GIT_SHA = _capture_git_sha()


# ─────────────────────────────────────────────────────────────────────────────
# Per-call schema factory (D1) — citation-path enforcement parameterized
# by the ticker's actual citable set
# ─────────────────────────────────────────────────────────────────────────────

# Static citable paths shared by every ticker (upstream-agent and calc-node
# outputs). The ticker-specific dashboard paths get added at call time.
_STATIC_CITABLE_PREFIXES = (
    "phase2.",
    "contrarian.",
    "scenario_eval.",
    "scenarios.",
    "conviction.",
    "qualitative.forensic.",
    "qualitative.value_chain.",
    "qualitative.strategic_context.",
    "reality_check.",
    "validation.",
    "reverse_dcf.",
    "multiple_decomposition.",
    "cyclicality.",
)


def _is_statically_citable(path: str) -> bool:
    """True if `path` matches one of the always-allowed upstream prefixes
    (calc_node fields, contrarian, scenarios, qualitative_synthesis-agent
    outputs). Dashboard-citable paths are checked separately against the
    per-call citable set."""
    return any(path.startswith(prefix) for prefix in _STATIC_CITABLE_PREFIXES)


def make_thesis_synthesis_class(
    citable_dashboard_paths: frozenset,
    stale_dashboard_paths: frozenset,
) -> type:
    """Build a per-call ThesisSynthesis subclass whose validator enforces
    (a) every cited_signals entry resolves to a citable path, and
    (b) any stale dashboard citation is mirrored into `staleness_flags`.

    `citable_dashboard_paths` is the set of dashboard paths the ticker
    can cite — `qualitative.{dim_id}` for assessed/stale dims and
    `qualitative.{cat_id}_composite` for non-null composites. Unassessed
    dimensions are NOT in this set and citing them raises ValidationError.
    """
    citable = frozenset(citable_dashboard_paths)
    stale = frozenset(stale_dashboard_paths)

    class _ThesisSynthesisCitationAware(ThesisSynthesis):
        # Pydantic frozen-model — we can't mutate after init, so the
        # staleness map gets written into a private attribute the agent
        # reads after validation.
        model_config = {"frozen": False}   # allow staleness_flags annotation

        @model_validator(mode="after")
        def _enforce_citation_paths(self):
            stale_hits: set = set()
            invalid: List[str] = []
            for case_name in ("bull_case", "bear_case", "base_case"):
                case = getattr(self, case_name)
                for path in case.cited_signals:
                    if path in citable or _is_statically_citable(path):
                        if path in stale:
                            stale_hits.add(path)
                        continue
                    invalid.append(f"{case_name}.cited_signals: {path!r}")
            if invalid:
                raise ValueError(
                    "cited_signals references non-citable paths "
                    "(unassessed dashboard dimensions, or unknown upstream "
                    "field paths). Either remove the citation or move the "
                    "concern to required_analyst_judgment.\n  "
                    + "\n  ".join(invalid)
                )
            # Stash on the instance for the agent to read post-validation
            object.__setattr__(self, "_stale_citations", sorted(stale_hits))
            return self

    return _ThesisSynthesisCitationAware


def _vocabulary_score(thesis: ThesisSynthesis) -> int:
    """Count distinct canonical markers across all narrative fields."""
    text = " ".join([
        thesis.thesis_statement,
        thesis.bull_case.claim,
        thesis.bear_case.claim,
        thesis.base_case.claim,
        thesis.position_sizing_implications,
        " ".join(thesis.required_analyst_judgment),
        " ".join(thesis.update_conditions),
    ])
    matches = set(m.lower() for m in _VOCABULARY_PATTERN.findall(text))
    return len(matches)


# ─────────────────────────────────────────────────────────────────────────────
# Prompt
# ─────────────────────────────────────────────────────────────────────────────

_PROMPT_BODY = """
You are the Thesis Synthesizer for {ticker}.

YOUR ROLE — INTEGRATION, NOT ANALYSIS
═════════════════════════════════════════════════════════════════════
You consume STRUCTURED upstream signals from other agents and integrate
them into an explicit investment thesis. You do NOT produce parallel
analysis. Specifically:

  ✗ DO NOT detect bias — that's the contrarian's job. Cite
    `contrarian.bias_detected` instead.
  ✗ DO NOT measure cyclicality — that's calc_node's job. Cite
    `phase2.cyclicality.z_score` instead.
  ✗ DO NOT compute multiple premium — calc_node already did. Cite
    `phase2.multiple_decomposition.premium_pct` instead.
  ✗ DO NOT propose scenarios — qualitative_synthesis + scenario_eval
    already did. Cite scenarios by name.

Every thesis claim must reference upstream signals via the
`cited_signals` field. Empty `cited_signals` = rejection.

═════════════════════════════════════════════════════════════════════
UPSTREAM SIGNALS AVAILABLE TO YOU
═════════════════════════════════════════════════════════════════════

PHASE 2 VALUATION (deterministic, from calc_node):
{phase2_summary}

QUALITATIVE SYNTHESIS (forensic + value_chain + strategic context):
{qualitative_summary}

CONTRARIAN OUTPUT (bias detection + bear case + sentiment):
{contrarian_summary}

AGENT-PROPOSED SCENARIOS (with DCF-evaluated IPS):
{scenarios_summary}

CONVICTION (deterministic pillar scoring):
{conviction_summary}

DASHBOARD — STRUCTURED ANALYST JUDGMENT:
{dashboard_summary}

═════════════════════════════════════════════════════════════════════
YOUR TASKS
═════════════════════════════════════════════════════════════════════

1. THESIS STATEMENT (one sentence): the core investment view.

2. BULL CASE: 2-4 sentences. What needs to be true for upside? Cite
   specific scenarios by name (e.g. "Per `Services-led re-acceleration`
   scenario, IPS=$X"). Cite `phase2.three_scenario_dcf.bull` if the
   bull case relies on the engine's bull scenario. Do NOT invent new
   bull arguments — synthesize from the existing scenarios + qualitative
   evidence.

3. BEAR CASE: 2-4 sentences. Cite `contrarian.bear_case_summary` and
   the worst-IPS scenario. Cite `phase2.implied_cagr` if the bear case
   relies on the reverse-DCF math. Do NOT do bias detection — that's
   the contrarian's `bias_detected` field which you cite as part of
   your bear case framing.

4. BASE CASE: 2-4 sentences. Where the math lands. Cite
   `phase2.three_scenario_dcf.base.intrinsic_per_share` and
   `phase2.three_scenario_dcf.base.margin_of_safety` for the central
   estimate. Reference cyclicality via `phase2.cyclicality.z_score` if
   relevant.

5. DECISION CONDITIONS: 3-5 typed triggers. Each has:
     - trigger (descriptive English)
     - observable (machine-readable expression — use the actual field
       paths, e.g. "phase2.implied_cagr > 0.25",
       "phase2.three_scenario_dcf.base.margin_of_safety < -0.20")
     - action: exit / trim / hold / add
     - priority: red / amber / green

6. THESIS CONFIDENCE:
     - high  = signals converge (contrarian + qualitative + multiple
               decomposition all point the same way)
     - medium = mostly converge with one notable divergence
     - low   = signals diverge materially
     - insufficient_signal = upstream gaps prevent confident integration

7. POSITION SIZING IMPLICATIONS: cite the conviction-scorer's
   `position_tier` and `position_size_pct` explicitly. Adjust
   directionally if your thesis confidence and the conviction score
   diverge — and explain why.

8. REQUIRED ANALYST JUDGMENT: list 0-5 specific gaps where the framework
   defers to the analyst. Things the math cannot answer (e.g. "Strategic
   value of services bundling under consumer-electronics-as-platform
   thesis is qualitative; not in calc layer").

9. UPDATE CONDITIONS: list 1-5 specific things that would invalidate
   the thesis (e.g. "Reverse-DCF signal flips from caution to flag",
   "Q3 earnings show services revenue decel below 8% YoY",
   "New 10-K disclosure of customer concentration > 20%").

═════════════════════════════════════════════════════════════════════
CITATION REQUIREMENT
═════════════════════════════════════════════════════════════════════
Each `cited_signals` list contains FIELD PATHS, not concept names.
Examples of valid entries:
  "contrarian.bias_detected"
  "contrarian.sentiment_score"
  "phase2.implied_cagr"
  "phase2.multiple_decomposition.premium_pct"
  "phase2.three_scenario_dcf.bear.intrinsic_per_share"
  "phase2.cyclicality.z_score"
  "qualitative.forensic.moat.score"               (LLM-extracted)
  "qualitative.value_chain.upstream_value_leak"   (LLM-extracted)
  "qualitative.strategic_context.intangible_decay_severity"  (LLM-extracted)
  "qualitative.moat_strength"                     (analyst-assessed dim)
  "qualitative.roiic_trend"                       (deterministic dim)
  "qualitative.quality_composite"                 (renormalized category score)
  "scenarios.<name>.intrinsic_per_share_base"
  "conviction.position_tier"
  "conviction.conviction_score"

CITATION NAMESPACES — TWO DISTINCT SOURCES, BOTH VALID
═════════════════════════════════════════════════════════════════════
There are TWO `qualitative.*` citation namespaces. Pick the right one:

  (A) `qualitative.{{agent}}.{{field}}` — LLM-extracted from the 10-K by
      the qualitative_synthesis agent. ALWAYS citable for any ticker.
      Examples: qualitative.forensic.moat_score,
                qualitative.forensic.has_pricing_power,
                qualitative.forensic.concentration_risk,
                qualitative.value_chain.upstream_value_leak,
                qualitative.value_chain.strategic_position,
                qualitative.strategic_context.terminal_haircut,
                qualitative.strategic_context.intangible_decay_severity.

  (B) `qualitative.{{dim_id}}` — analyst-structured judgment from the
      Qualitative Dashboard. ONLY citable when the dim is ASSESSED for
      THIS ticker (the dashboard summary above lists which ones).
      Same applies to `qualitative.{{cat_id}}_composite`.

If you want to cite "moat strength" and `qualitative.moat_strength` is
NOT_ASSESSED for this ticker, cite `qualitative.forensic.moat_score`
INSTEAD. NEVER cite a NOT_ASSESSED dim path — the validator rejects.

DASHBOARD CITATION RULES:
  - ASSESSED dim → cite `qualitative.{{dim_id}}` freely.
  - NON-NULL composite → cite `qualitative.{{cat}}_composite` freely.
  - NOT_ASSESSED dim or NULL composite → use the LLM-extracted alternative
    (namespace A above), or raise as `required_analyst_judgment`.
  - STALE dim citations are accepted but trigger a staleness flag.
    Acknowledge staleness in the case prose if you cite one.

Return ThesisSynthesis JSON.
"""

_PROMPT = ChatPromptTemplate.from_template(_PROMPT_BODY)


# ─────────────────────────────────────────────────────────────────────────────
# Upstream-signal projectors — extract LLM-prompt-friendly summaries
# ─────────────────────────────────────────────────────────────────────────────

def _summarize_phase2(state: Dict[str, Any]) -> str:
    p2 = state.get("phase2_valuation") or {}
    if not p2:
        return "(phase2 unavailable)"
    bear = (p2.get("three_scenario_dcf", {}) or {}).get("bear", {}) or {}
    base = (p2.get("three_scenario_dcf", {}) or {}).get("base", {}) or {}
    bull = (p2.get("three_scenario_dcf", {}) or {}).get("bull", {}) or {}
    md = p2.get("multiple_decomposition") or {}
    cyc = state.get("cyclicality") or {}
    lines = [
        f"  WACC:                 {p2.get('wacc'):.2%}" if p2.get("wacc") else "  WACC:                 N/A",
        f"  Implied CAGR (10y):   {p2.get('implied_cagr'):.1%}" if p2.get('implied_cagr') else "  Implied CAGR:         N/A",
        f"  Historical CAGR:      {p2.get('historical_cagr'):.1%}" if p2.get('historical_cagr') else "  Historical CAGR:      N/A",
        f"  Reverse DCF signal:   {p2.get('reverse_dcf_signal', 'N/A')}",
        f"  Bear IPS / MoS:       ${bear.get('intrinsic_per_share', 'N/A')} / {bear.get('margin_of_safety', 'N/A')}",
        f"  Base IPS / MoS:       ${base.get('intrinsic_per_share', 'N/A')} / {base.get('margin_of_safety', 'N/A')}",
        f"  Bull IPS / MoS:       ${bull.get('intrinsic_per_share', 'N/A')} / {bull.get('margin_of_safety', 'N/A')}",
        f"  Multiple premium pct: {md.get('premium_pct')}",
        f"  Multiple signal:      {md.get('signal', 'N/A')}",
        f"  Value creation:       {md.get('value_creation', 'N/A')}",
        f"  Cyclicality z-score:  {cyc.get('z_score'):+.2f} (peak={cyc.get('is_peak')})" if cyc.get("z_score") is not None else "  Cyclicality:          N/A",
    ]
    return "\n".join(lines)


def _summarize_qualitative(state: Dict[str, Any]) -> str:
    forensic = state.get("forensic_report") or {}
    vc       = state.get("value_chain_report") or {}
    sc       = state.get("strategic_context_report") or {}

    parts = []
    if forensic:
        parts.append(
            f"  Forensic: moat_score={forensic.get('moat_score')}/10; "
            f"has_pricing_power={forensic.get('has_pricing_power')}; "
            f"concentration_risk={forensic.get('concentration_risk')}"
        )
    if vc:
        parts.append(
            f"  Value chain: upstream_value_leak={vc.get('upstream_value_leak')}; "
            f"strategic_position={vc.get('strategic_position', 'N/A')}; "
            f"substitution_pressure={vc.get('substitution_pressure', 'N/A')}"
        )
    if sc:
        parts.append(
            f"  Strategic context: cyclicality_classification="
            f"{sc.get('cyclicality_classification', 'N/A')}; "
            f"growth_quality={sc.get('growth_quality', 'N/A')}; "
            f"intangible_decay_severity={sc.get('intangible_decay_severity', 'N/A')}; "
            f"terminal_haircut={sc.get('terminal_haircut')}"
        )
    return "\n".join(parts) if parts else "(qualitative unavailable)"


def _summarize_contrarian(state: Dict[str, Any]) -> str:
    cr = state.get("contrarian_report") or {}
    sa = cr.get("structured_analysis") or {}
    if not sa:
        return "(contrarian unavailable)"
    bear = sa.get("bear_case_summary", "") or ""
    return (
        f"  bias_detected:    {sa.get('bias_detected', 'N/A')}\n"
        f"  sentiment_score:  {sa.get('sentiment_score', 'N/A')}\n"
        f"  bear_case_summary (excerpt):\n    {bear[:600]}"
    )


def _summarize_scenarios(state: Dict[str, Any]) -> str:
    sc_results = state.get("scenario_results") or []
    if not sc_results:
        return "(no agent-proposed scenarios)"
    lines = []
    for s in sc_results:
        ips = s.get("intrinsic_per_share_base")
        ups = s.get("upside_pct_base")
        ips_str = f"${ips:,.2f}" if ips is not None else "N/A"
        ups_str = f"{ups:+.1f}%" if ups is not None else "N/A"
        lines.append(
            f"  - '{s.get('name', '?')}' ({s.get('scenario_type', '?')}, "
            f"by {s.get('proposed_by', '?')}): IPS={ips_str} upside={ups_str}. "
            f"Rationale: {s.get('rationale', '')[:200]}"
        )
    return "\n".join(lines)


def _summarize_conviction(state: Dict[str, Any]) -> str:
    c = state.get("conviction") or {}
    if not c:
        return "(conviction unavailable)"
    return (
        f"  position_tier:    {c.get('position_tier', 'N/A')}\n"
        f"  position_size_pct: {c.get('position_size_pct', 'N/A')}\n"
        f"  conviction_score: {c.get('conviction_score', 'N/A')}\n"
        f"  capped_total:     {c.get('capped_total', 'N/A')}/25"
    )


def _summarize_qualitative_dashboard(state: Dict[str, Any]) -> str:
    """Render the dashboard projection (from `qualitative_dashboard` state
    key) into prompt-friendly text.

    Surfaces:
      - Coverage stats (n_assessed / n_assessable, bucket)
      - Per-category composite + n_assessed/n_total + staleness flag
      - Per-dim line for ASSESSED dims (citable as qualitative.{dim_id})
      - STALE dim list with staleness reason
      - NOT_ASSESSED dim list (cannot cite — mention as gap)
      - PENDING_DATA dim list (data infra missing — explicit platform
        limitation, not analyst gap)
    """
    qd = state.get("qualitative_dashboard") or {}
    if not qd or not qd.get("available", False):
        return (
            "(dashboard unavailable — proceed citing only calc_node + agent "
            "outputs; explicitly note in `required_analyst_judgment` that "
            "structured analyst judgment was not captured.)"
        )

    cov = qd.get("coverage") or {}
    cats = qd.get("categories") or {}
    dims = qd.get("dimensions") or {}

    n_a = cov.get("n_assessed", 0)
    n_total = cov.get("n_assessable", 0)
    state_label = cov.get("coverage_state", "zero")
    n_stale = cov.get("n_stale", 0)
    n_pending = cov.get("n_pending", 0)
    n_na = cov.get("n_not_assessed", 0)

    lines: List[str] = [
        f"  Coverage: {n_a}/{n_total} dims assessed "
        f"({state_label}). {n_stale} stale. {n_na} not_assessed. "
        f"{n_pending} pending_data (infra not wired)."
    ]

    # Composites — one line per category
    for cat_id, cat in cats.items():
        comp = cat.get("composite_score")
        n_cat_a = cat.get("n_assessed", 0)
        n_cat_t = cat.get("n_total", 0)
        comp_str = f"{comp:.2f}" if comp is not None else "N/A"
        stale_marker = ""
        if cat.get("status") == "stale":
            stale_paths = cat.get("stale_contributors") or []
            stale_marker = f" [stale via {','.join(stale_paths)}]"
        cat_path = f"qualitative.{cat_id}_composite"
        citable_marker = "" if comp is None else f"  (cite as {cat_path})"
        lines.append(
            f"    {cat.get('title', cat_id):<20s} composite={comp_str:<6s} "
            f"({n_cat_a}/{n_cat_t} dims){stale_marker}{citable_marker}"
        )

    # ASSESSED dims (citable)
    assessed_lines = []
    stale_lines = []
    not_assessed_ids = []
    pending_ids = []
    for dim_id, d in dims.items():
        status = d.get("status")
        score = d.get("score")
        score_str = f"{score:.2f}" if score is not None else "N/A"
        narr = (d.get("narrative") or "")[:120].replace("\n", " ")
        narr_clip = f" — {narr}…" if narr else ""
        if status == "assessed":
            assessed_lines.append(
                f"    qualitative.{dim_id} = {score_str} "
                f"[{d.get('source_category')}, fresh]{narr_clip}"
            )
        elif status == "stale":
            assessed_at = d.get("assessed_at", "")
            stale_lines.append(
                f"    qualitative.{dim_id} = {score_str} "
                f"[{d.get('source_category')}, stale; assessed_at={assessed_at}]"
                f"{narr_clip}"
            )
        elif status == "not_assessed":
            not_assessed_ids.append(dim_id)
        elif status == "pending_data":
            pending_ids.append(dim_id)

    if assessed_lines:
        lines.append("  ASSESSED dimensions (citable as qualitative.{dim_id}):")
        lines.extend(assessed_lines)
    if stale_lines:
        lines.append("  STALE dimensions (citable but flag staleness):")
        lines.extend(stale_lines)
    if not_assessed_ids:
        lines.append(
            "  NOT_ASSESSED dimensions — DO NOT cite as `qualitative.{dim_id}`. "
            "If material, EITHER cite the LLM-extracted equivalent listed below "
            "OR raise as required_analyst_judgment."
        )
        # Explicit substitution map: when the analyst hasn't assessed a
        # dimension yet, the qualitative_synthesis agent often emits a
        # related signal that's always-citable. Steer the LLM there
        # instead of letting it cite the unassessed dashboard path.
        for dim_id in not_assessed_ids:
            alt = _NOT_ASSESSED_ALTERNATIVE_PATH.get(dim_id, "")
            if alt:
                lines.append(
                    f"    qualitative.{dim_id} → cite `{alt}` (LLM-extracted)"
                )
            else:
                lines.append(
                    f"    qualitative.{dim_id} → no LLM-extracted equivalent; "
                    f"mention as gap"
                )
    if pending_ids:
        lines.append(
            f"  PENDING_DATA dimensions (platform-level gap, not analyst gap): "
            f"{', '.join(pending_ids)}"
        )

    return "\n".join(lines)


# Map from dashboard dim_id → always-citable LLM-extracted path under the
# qualitative_synthesis agent's namespace. Used when a dim is NOT_ASSESSED
# but the LLM still wants to express a related claim — better to cite the
# LLM-derived signal than to either invent a path or skip the claim.
_NOT_ASSESSED_ALTERNATIVE_PATH = {
    "moat_strength":         "qualitative.forensic.moat_score",
    "pricing_power":         "qualitative.forensic.has_pricing_power",
    "switching_costs":       "qualitative.forensic.moat_score",
    "brand_strength":        "",
    "capital_allocation_track_record": "",
    "reinvestment_opportunity":        "",
    "market_position":       "qualitative.value_chain.strategic_position",
    "competitor_identification":       "",
    "competitive_trajectory":          "",
    "regulatory_exposure":   "",
    "technology_disruption_risk":      "",
    "customer_concentration": "qualitative.forensic.concentration_risk",
    # PENDING_DATA dims — never citable; no alternative needed but listed
    # for completeness so a future change doesn't regress.
    "industry_concentration":          "",
    "management_tenure_continuity":    "",
    "management_alignment":            "",
}


# ─────────────────────────────────────────────────────────────────────────────
# Confidence-floor clamp (D2) — deterministic post-LLM enforcement
# ─────────────────────────────────────────────────────────────────────────────

_CONFIDENCE_RANK = {
    "insufficient_signal": 0,
    "low":                 1,
    "medium":              2,
    "high":                3,
}
_RANK_TO_CONFIDENCE = {v: k for k, v in _CONFIDENCE_RANK.items()}


def _coverage_confidence_ceiling(coverage_state: str) -> str:
    """Locked D2 floors expressed as ceilings (i.e. the highest the LLM
    is allowed to claim given coverage). Floor-only — clamps DOWN, never up."""
    return {
        "high":   "high",                 # unclamped
        "medium": "medium",
        "low":    "low",
        "zero":   "insufficient_signal",
    }.get(coverage_state, "insufficient_signal")


def _clamp_confidence(
    llm_confidence: str,
    coverage_state: str,
) -> tuple:
    """Returns (clamped_confidence, was_clamped, reason).

    Compares the LLM-emitted confidence rank against the ceiling derived
    from coverage_state. If the LLM picked higher, clamp to the ceiling.
    Never raises confidence — the LLM is allowed to be more pessimistic.
    """
    ceiling = _coverage_confidence_ceiling(coverage_state)
    llm_rank = _CONFIDENCE_RANK.get(llm_confidence, 0)
    ceil_rank = _CONFIDENCE_RANK.get(ceiling, 0)
    if llm_rank > ceil_rank:
        return (
            ceiling,
            True,
            f"confidence_clamped_to_{ceiling}_from_{llm_confidence}_due_to_coverage_{coverage_state}",
        )
    return (llm_confidence, False, "")


# ─────────────────────────────────────────────────────────────────────────────
# Agent
# ─────────────────────────────────────────────────────────────────────────────

def thesis_synthesizer_agent(state: Dict[str, Any]) -> Dict[str, Any]:
    """Single LLM call producing structured ThesisSynthesis output.

    Two-pass with vocabulary-check retry: if first-pass output has
    fewer than _VOCABULARY_MIN canonical-signal markers, retry once
    with a stronger 'cite upstream signals' directive. After two
    attempts, write the best-effort output and surface a quality flag.
    """
    print("---THESIS SYNTHESIZER AGENT---")
    ticker = state["ticker"]

    # Mock fallback when no LLM available
    if not os.environ.get("GOOGLE_API_KEY"):
        return _emit_mock(ticker)

    # Read dashboard projection (populated by dashboard_fetch_node) — used
    # for: prompt context, per-call citation-validator, confidence-floor
    # clamp, and _metadata stamp on the output.
    qd = state.get("qualitative_dashboard") or {}
    citable_dim_paths = frozenset(qd.get("citable_dim_paths") or [])
    citable_composite_paths = frozenset(qd.get("citable_composite_paths") or [])
    citable_dashboard_paths = citable_dim_paths | citable_composite_paths
    stale_paths = frozenset(qd.get("stale_paths") or [])
    coverage_state = ((qd.get("coverage") or {}).get("coverage_state")) or "zero"

    invoke_args = {
        "ticker":              ticker,
        "phase2_summary":      _summarize_phase2(state),
        "qualitative_summary": _summarize_qualitative(state),
        "contrarian_summary":  _summarize_contrarian(state),
        "scenarios_summary":   _summarize_scenarios(state),
        "conviction_summary":  _summarize_conviction(state),
        "dashboard_summary":   _summarize_qualitative_dashboard(state),
    }

    # Build the per-call schema — citation enforcement is parameterized by
    # the dashboard's actual citable set for THIS ticker. The LLM cannot
    # cite an unassessed dim; the validator rejects.
    PerCallSchema = make_thesis_synthesis_class(
        citable_dashboard_paths=citable_dashboard_paths,
        stale_dashboard_paths=stale_paths,
    )

    llm = ChatGoogleGenerativeAI(model=MODEL_NAME, temperature=TEMPERATURE)
    structured_llm = llm.with_structured_output(PerCallSchema)
    chain = _PROMPT | structured_llm

    thesis: Optional[ThesisSynthesis] = None
    last_error: Optional[Exception] = None
    quality_flags: List[str] = []

    for attempt in (1, 2):
        try:
            candidate: ThesisSynthesis = chain.invoke(invoke_args)
        except (ValidationError, Exception) as e:
            last_error = e
            err_str = str(e)
            print(f"  ⚠ Attempt {attempt}: schema validation failed: {err_str[:300]}")
            if attempt == 2:
                break

            # Build path-specific substitution guidance from the error.
            # When the validator names a NOT_ASSESSED dim, point to the
            # LLM-extracted alternative explicitly so the LLM doesn't
            # repeat the same citation.
            substitution_hints: List[str] = []
            for dim_id, alt in _NOT_ASSESSED_ALTERNATIVE_PATH.items():
                token = f"qualitative.{dim_id}"
                if token in err_str:
                    if alt:
                        substitution_hints.append(
                            f"  - REPLACE `{token}` with `{alt}` "
                            f"(LLM-extracted, always citable)"
                        )
                    else:
                        substitution_hints.append(
                            f"  - REMOVE `{token}` citation; raise as "
                            f"required_analyst_judgment instead"
                        )
            substitution_block = (
                "PATH SUBSTITUTIONS REQUIRED:\n" + "\n".join(substitution_hints)
                if substitution_hints else
                ""
            )

            chain = (
                ChatPromptTemplate.from_template(
                    "PRIOR ATTEMPT FAILED VALIDATION:\n{error_msg}\n\n"
                    + substitution_block + "\n\n"
                    + "Common issues:\n"
                    + "  - cited_signals MUST be a non-empty list of field paths\n"
                    + "  - NOT_ASSESSED dashboard dims (qualitative.{dim_id}) cannot\n"
                    + "    be cited — use the LLM-extracted equivalent under\n"
                    + "    qualitative.forensic.* / qualitative.value_chain.* /\n"
                    + "    qualitative.strategic_context.* instead.\n"
                    + "Re-do.\n\n"
                    + "═══════════════════════════════════════════════════\n"
                    + _PROMPT_BODY
                ) | structured_llm
            )
            invoke_args = dict(invoke_args)
            invoke_args["error_msg"] = err_str[:1000]
            continue

        # Vocabulary-discipline check
        score = _vocabulary_score(candidate)
        if score >= _VOCABULARY_MIN:
            thesis = candidate
            break

        print(f"  ⚠ Attempt {attempt}: only {score} canonical-signal markers "
              f"(need {_VOCABULARY_MIN}); retrying")
        if attempt == 2:
            # Accept the candidate but flag it
            thesis = candidate
            quality_flags.append(
                f"vocabulary_below_floor: only {score} canonical markers "
                f"(threshold {_VOCABULARY_MIN}); thesis may be producing "
                f"parallel analysis instead of citing upstream signals"
            )
            break
        # Retry with vocabulary directive
        chain = (
            ChatPromptTemplate.from_template(
                "PRIOR ATTEMPT WAS INSUFFICIENTLY GROUNDED IN UPSTREAM SIGNALS.\n"
                f"It contained only {score} of the required {_VOCABULARY_MIN} "
                "canonical markers (contrarian, cyclicality, z-score, implied "
                "CAGR, multiple.decomposition, reverse.dcf, conviction, etc.). "
                "Re-do, explicitly REFERENCING upstream signal field paths "
                "in the narrative prose, not just in cited_signals.\n\n"
                "═══════════════════════════════════════════════════\n"
                + _PROMPT_BODY
            ) | structured_llm
        )

    if thesis is None:
        print(f"  ✗ Both attempts failed; falling back to mock")
        return _emit_mock(ticker, error=str(last_error))

    out = thesis.model_dump()

    # ── Confidence-floor clamp (D2) — coverage-aware, downward-only ─────
    clamped, was_clamped, clamp_reason = _clamp_confidence(
        thesis.thesis_confidence, coverage_state
    )
    if was_clamped:
        out["thesis_confidence"] = clamped
        quality_flags.append(clamp_reason)
    if coverage_state == "zero":
        # Always flag a zero-coverage thesis explicitly
        quality_flags.append("coverage_zero_calc_only_thesis")

    # ── Metadata stamp (D5) — catalog hash + coverage receipts ──────────
    # `dashboard_state_fingerprint` is the staleness key: comparing it
    # against the live dashboard fingerprint at any later time tells us
    # whether the thesis incorporates the latest analyst judgment, or
    # whether a refresh is needed (Phase 7).
    cov = qd.get("coverage") or {}
    out["_metadata"] = {
        "code_git_sha":            _GIT_SHA,
        "dashboard_catalog_hash":  _aggregate_catalog_hash(),
        "per_dim_catalog_hashes":  _per_dim_catalog_hashes(),
        "dashboard_state_fingerprint": qd.get("state_fingerprint", ""),
        "generated_at":            datetime.datetime.now(
            datetime.timezone.utc
        ).isoformat(),
        "n_assessed":              cov.get("n_assessed", 0),
        "n_assessable":            cov.get("n_assessable", 0),
        "coverage_state":          coverage_state,
        "stale_paths":             sorted(stale_paths),
        "stale_citations": list(getattr(thesis, "_stale_citations", []) or []),
        "cited_paths_resolved": sorted({
            p
            for case in (thesis.bull_case, thesis.bear_case, thesis.base_case)
            for p in case.cited_signals
        }),
        "dashboard_available":     bool(qd.get("available", False)),
    }

    if quality_flags:
        out["_quality_flags"] = quality_flags

    output = {
        "thesis_synthesis": out,
        "messages": [HumanMessage(
            content=(
                f"ThesisSynthesizer: {ticker} — "
                f"confidence={out['thesis_confidence']} (raw={thesis.thesis_confidence}), "
                f"coverage={coverage_state} ({cov.get('n_assessed', 0)}/{cov.get('n_assessable', 0)}), "
                f"horizon={thesis.time_horizon}, "
                f"{len(thesis.decision_conditions)} decision conditions"
            )
        )],
    }
    tracer.log_step("ThesisSynthesizer", state, output)
    return output


def _emit_mock(ticker: str, error: Optional[str] = None) -> Dict[str, Any]:
    """No-API-key / failure fallback.

    The error string (when present) is surfaced both in the agent message
    AND inside `thesis_synthesis._mock_error` so callers (e.g. the refresh
    endpoint) can include it in their HTTP response. Without this, "fell
    back to mock" is hard to debug — the LLM/validation error is buried
    in stdout and lost.
    """
    msg = f"ThesisSynthesizer (mock): {ticker}"
    if error:
        msg += f" — error: {error[:200]}"
    return {
        "thesis_synthesis": {
            "thesis_statement": "Mock thesis (no API key or call failure)",
            "bull_case": {"claim": "Mock", "cited_signals": ["mock"]},
            "bear_case": {"claim": "Mock", "cited_signals": ["mock"]},
            "base_case": {"claim": "Mock", "cited_signals": ["mock"]},
            "decision_conditions": [
                {"trigger": "mock", "observable": "mock", "action": "hold", "priority": "green"}
                for _ in range(3)
            ],
            "thesis_confidence": "insufficient_signal",
            "time_horizon": "1_year",
            "position_sizing_implications": "mock",
            "required_analyst_judgment": [],
            "update_conditions": ["mock"],
            "_quality_flags": ["mock_fallback"],
            "_mock_error":     (error or "")[:1500],
        },
        "messages": [HumanMessage(content=msg)],
    }


__all__ = [
    "thesis_synthesizer_agent",
    "ThesisSynthesis",
    "DecisionCondition",
    "CitedClaim",
]
