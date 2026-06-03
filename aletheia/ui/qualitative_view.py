"""Streamlit qualitative-analysis tab — readability-tuned (Step 4 redesign).

Renders the 19-dimension framework as a category-grouped surface for the
selected ticker. Designed to be easy on the eyes:

  - Larger body type (14-16px regular sans-serif, not monospace)
  - Higher contrast text (no rgba ghost-text in dimension titles)
  - Native Streamlit components (st.metric, st.badge) where available so
    light/dark theme tracks correctly
  - One score, one short status line per card — no stacked badges
  - Composite math + sub-questions live in a single expander, not on the
    card surface

Submission UI (HITL input dialog) is wired via `_request_assess`.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

import httpx
import streamlit as st


# Score color tokens — used sparingly for the score badge only
_GREEN = "#10b981"
_AMBER = "#f59e0b"
_RED   = "#ef4444"

# Source-category labels (Streamlit st.badge handles colors via theme)
_SOURCE_LABEL = {
    "deterministic":  "Computed",
    "hitl":           "Analyst",
    "llm_augmented":  "LLM",
    "pending_data":   "Data pending",
}

_SOURCE_BADGE_COLOR = {
    "deterministic":  "blue",
    "hitl":           "violet",
    "llm_augmented":  "orange",
    "pending_data":   "gray",
}

_STATUS_LABEL = {
    "assessed":     "Assessed",
    "stale":        "Refresh",
    "not_assessed": "Not assessed",
    "pending_data": "Awaiting data",
}

_STATUS_COLOR = {
    "assessed":     "green",
    "stale":        "orange",
    "not_assessed": "gray",
    "pending_data": "gray",
}


def _score_color(score: Optional[float]) -> str:
    if score is None:
        return "rgba(120,120,128,0.6)"
    if score >= 6:  return _GREEN
    if score >= 4:  return _AMBER
    return _RED


def _format_score(score: Optional[float]) -> str:
    if score is None:
        return "—"
    if isinstance(score, int) or float(score).is_integer():
        return str(int(score))
    return f"{score:.1f}"


def _format_timestamp(iso_str: Optional[str]) -> str:
    if not iso_str:
        return ""
    return iso_str[:10]


def _drift_summary(d: Dict[str, Any]) -> Optional[tuple]:
    """Compare ``llm_proposal_latest`` against the analyst's current
    sub_scores. Returns ``(n_changed, biggest_delta, direction)`` when
    drift is material (≥1 sub-score changed by ≥1 point), else None.

    Triggers only on analyst-owned rows (analyst_adjusted /
    analyst_overridden). Drift on un-reviewed LLM proposals isn't
    interesting — the latest proposal already IS the canonical score.
    """
    if d.get("provenance") not in ("analyst_adjusted", "analyst_overridden"):
        return None
    latest = d.get("llm_proposal_latest") or {}
    llm_sub = latest.get("sub_scores") or {}
    analyst_sub = d.get("sub_scores") or {}
    if not llm_sub or not analyst_sub:
        return None
    diffs = []
    for k, llm_v in llm_sub.items():
        a_v = analyst_sub.get(k)
        if a_v is None:
            continue
        try:
            delta = float(llm_v) - float(a_v)
        except (TypeError, ValueError):
            continue
        if abs(delta) >= 1:
            diffs.append((k, delta))
    if not diffs:
        return None
    diffs.sort(key=lambda kv: abs(kv[1]), reverse=True)
    biggest = diffs[0]
    direction = "higher" if biggest[1] > 0 else "lower"
    return (len(diffs), biggest[0], direction, biggest[1])


def _provenance_pill(d: Dict[str, Any]) -> Optional[tuple]:
    """Return ``(label, color)`` for the source-provenance pill.

    Surfaces who produced this assessment + when, alongside the
    status badge. Three patterns reflecting Phase A-D wiring:

      - DETERMINISTIC → ``📊 Computer (formula_v1)`` blue
      - HITL → ``👤 Analyst`` violet
      - LLM_AUGMENTED → ``🤖 Gemini`` orange (provider from
        source_payload — defaults to "LLM" when older rows
        predate Phase B's provenance trail)

    Returns ``None`` for ``pending_data`` and ``not_assessed`` —
    those states are conveyed by the status badge alone.
    """
    src = d.get("source_category")
    status = d.get("status")
    if status not in ("assessed", "stale"):
        return None

    payload = d.get("source_payload") or {}

    if src == "deterministic":
        version = payload.get("formula", "v1")
        # Surface the formula version (e.g. "industry_concentration_v1")
        # — analysts can read the methodology pin without expanding the
        # detail panel.
        return f"📊 {version}", "blue"

    if src == "hitl":
        # LLM-proposer pipeline (Phase 1 + 2) flips this to richer
        # badges that distinguish proposed / confirmed / adjusted /
        # overridden. Legacy "👤 Analyst" stays as the fallback for
        # rows persisted before the provenance schema landed.
        prov = d.get("provenance")
        if prov == "llm_proposed":
            confidence = d.get("confidence") or ""
            label = "🤖 LLM proposed"
            if confidence:
                label += f" · {confidence}"
            return label, "orange"
        if prov == "analyst_confirmed":
            return "✓ Analyst confirmed", "green"
        if prov == "analyst_adjusted":
            return "✎ Analyst adjusted", "blue"
        if prov == "analyst_overridden":
            return "👤 Analyst override", "violet"
        return "👤 Analyst", "violet"

    if src == "llm_augmented":
        provider = (payload.get("llm_provider") or "LLM").capitalize()
        return f"🤖 {provider}", "orange"

    return None


# ─────────────────────────────────────────────────────────────────────────────
# Top strip — category composites
# ─────────────────────────────────────────────────────────────────────────────

def _render_category_strip(categories: List[Dict[str, Any]]) -> None:
    """Five-card row at the top: composite score per category with
    coverage counter. Uses st.metric so theme + sizing track Streamlit
    defaults."""
    cols = st.columns(len(categories))
    for col, cat in zip(cols, categories):
        composite = cat.get("composite_score")
        n_assessed = cat.get("n_assessed", 0)
        n_total = cat.get("n_total", 0)

        comp_text = _format_score(composite) if composite is not None else "—"
        coverage  = f"{n_assessed} / {n_total}" if n_total else "data pending"

        with col:
            with st.container(border=True):
                st.metric(
                    label=cat["title"],
                    value=comp_text,
                    delta=coverage,
                    delta_color="off",
                    border=False,
                )
                if cat.get("contributing"):
                    with st.expander("Composite math", expanded=False):
                        for c in cat["contributing"]:
                            score = _format_score(c["score"])
                            weight = c.get("renormalized_weight", c["weight"])
                            contribution = c.get("contribution", 0)
                            st.markdown(
                                f"**{c['dimension_id'].replace('_', ' ').title()}** — "
                                f"score {score} × weight {weight:.2f} "
                                f"= {contribution:.2f}"
                            )


# ─────────────────────────────────────────────────────────────────────────────
# Dimension cards
# ─────────────────────────────────────────────────────────────────────────────

def _render_score_block(score: Optional[float]) -> None:
    """Right-aligned score display — large + colored. No badges, no HTML
    pill — just a number and the 1-7 hint."""
    color = _score_color(score)
    text  = _format_score(score)
    st.markdown(
        f'<div style="text-align:right; line-height:1;">'
        f'  <div style="font-size:36px; font-weight:600; color:{color};">{text}</div>'
        f'  <div style="font-size:12px; opacity:0.6; margin-top:2px;">of 7</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _render_dimension_card(d: Dict[str, Any], on_assess: Optional[Callable] = None) -> None:
    """One bordered card per dimension. Layout:
        Left column (wide):  title (large) + description (1-2 lines) +
                             status line + Assess button (if HITL)
        Right column (narrow): score in big colored type + "of 7"
        Inside expander:     formula / questions / source detail
    """
    status = d["status"]
    src = d["source_category"]

    with st.container(border=True):
        left, right = st.columns([5, 1])

        with left:
            # Title + source badge
            title = d["title"]
            source_label = _SOURCE_LABEL.get(src, src.upper())
            source_color = _SOURCE_BADGE_COLOR.get(src, "gray")
            st.markdown(f"### {title}")
            st.badge(source_label, color=source_color)

            # Description (full text, no truncation — readable size)
            desc = d.get("description", "")
            if desc:
                st.markdown(
                    f'<div style="font-size:14px; line-height:1.5; '
                    f'opacity:0.85; margin-top:8px;">{desc}</div>',
                    unsafe_allow_html=True,
                )

            # Status line — single sentence with status badge + metadata
            st.markdown(" ")  # spacing
            _render_status_line(d)

            # Drift banner — surfaces when the latest LLM proposal
            # diverges materially from the analyst's recorded scores.
            # Only renders on analyst-owned rows; un-reviewed LLM
            # proposals already use the latest as canonical.
            drift = _drift_summary(d)
            if drift:
                n_changed, biggest_q, direction, delta = drift
                st.warning(
                    f"⚡ **LLM drift detected** — re-run after a new filing "
                    f"now rates {biggest_q} **{abs(delta):.0f} points "
                    f"{direction}** than you did "
                    f"({n_changed} sub-score{'s' if n_changed != 1 else ''} "
                    f"differ by ≥1). Review the LLM proposal to decide if "
                    f"the new disclosure changes the analysis."
                )

            # Assess button — HITL only. Label + emphasis follow the
            # provenance state so the analyst's next action is obvious:
            # unreviewed proposals get a primary "Review" CTA; confirmed
            # assessments get a quiet "Re-assess" link.
            if on_assess is not None and src == "hitl":
                prov = d.get("provenance")
                review_state = d.get("review_state")
                if prov == "llm_proposed" and review_state == "unreviewed":
                    label = "🔍 Review LLM proposal"
                    btn_type = "primary"
                elif status in ("assessed", "stale"):
                    label = "Re-assess"
                    btn_type = "secondary"
                else:
                    label = "Assess this dimension"
                    btn_type = "primary"
                if st.button(
                    label,
                    key=f"qual_assess_btn__{d['dimension_id']}",
                    type=btn_type,
                ):
                    on_assess(d["dimension_id"])

        with right:
            _render_score_block(d.get("score"))

        # Detail — under both columns
        with st.expander("View detail", expanded=False):
            _render_dimension_detail(d)


def _render_status_line(d: Dict[str, Any]) -> None:
    """Single-line status with badge + provenance pill + metadata."""
    status = d["status"]
    src = d["source_category"]
    status_color = _STATUS_COLOR.get(status, "gray")
    status_label = _STATUS_LABEL.get(status, status)

    # Status badge first; provenance pill alongside (assessed/stale only)
    badge_col, pill_col = st.columns([1, 3])
    with badge_col:
        st.badge(status_label, color=status_color)
    pill = _provenance_pill(d)
    if pill:
        label, color = pill
        with pill_col:
            st.badge(label, color=color)

    # Status-specific metadata
    if status == "pending_data":
        sp = d.get("source_payload") or {}
        reason = sp.get("reason") if sp else None
        msg = "Data infrastructure not yet wired"
        if reason:
            msg += f" — {reason.replace('_', ' ')}"
        st.caption(msg)
        return

    if status == "not_assessed":
        if src == "llm_augmented":
            # Phases B + C shipped the extraction bundles — empty
            # state means Stage 4 hasn't been run for this ticker yet,
            # not that the pipeline is deferred.
            st.caption(
                "Run **Stage 4 Agents** from the sidebar to extract this "
                "dimension from the 10-K / DEF 14A."
            )
        elif src == "hitl":
            st.caption("Click Assess to fill in the structured prompts")
        elif src == "deterministic":
            # Most deterministic dims auto-fill on Stage 3; "not_assessed"
            # means the ticker hasn't been ingested yet.
            st.caption(
                "Will populate automatically once the ticker has been "
                "ingested (Stages 1-3)."
            )
        return

    # assessed or stale
    parts = []
    last = _format_timestamp(d.get("last_updated"))
    if last:
        parts.append(f"Updated {last}")
    by = d.get("assessed_by")
    if by and by != "system":
        parts.append(f"by {by}")
    if status == "stale":
        parts.append(f"⚠ exceeds {d.get('staleness_days')}-day threshold")
    if d.get("code_git_sha"):
        parts.append(f"build {d['code_git_sha'][:7]}")
    if parts:
        st.caption(" · ".join(parts))


def _render_llm_payload(dim_id: str, sp: Dict[str, Any]) -> None:
    """Render the structured extraction payload for an LLM_AUGMENTED
    dim. Each of the 5 Phase B/C dims has a known payload shape — we
    pick the renderer by ``dim_id``. Unknown dims fall back to a
    generic key:value dump."""

    # ── Phase B (10-K bundle) ──────────────────────────────────────
    if dim_id == "competitor_identification":
        intensity = sp.get("competitive_intensity")
        if intensity:
            st.markdown(
                f"- **Competitive intensity** — {intensity.capitalize()}"
            )
        comps = sp.get("named_competitors") or []
        if comps:
            st.markdown("- **Named competitors**")
            for c in comps:
                st.markdown(f"    - {c}")
        else:
            st.markdown(
                "- _No named competitors surfaced — usually a 10-K disclosure "
                "gap; flag if filer claimed material competition._"
            )
        return

    if dim_id == "regulatory_exposure":
        exposures = sp.get("material_exposures") or []
        if exposures:
            st.markdown("- **Material exposures**")
            for x in exposures:
                regulator = x.get("regulator", "—")
                area = x.get("area", "—")
                sev = (x.get("severity") or "").capitalize()
                st.markdown(
                    f"    - **{regulator}** — {area}  _(severity: {sev})_"
                )
        else:
            st.markdown(
                "- _No material exposures captured — rare for large filers; "
                "may indicate truncation past the relevant Item 1A section._"
            )
        return

    if dim_id == "customer_concentration":
        disclosed = sp.get("concentration_disclosed")
        if disclosed is False:
            st.markdown(
                "- **Concentration disclosure** — filer affirmed no single "
                "customer exceeds 10% of revenue"
            )
        elif disclosed is True:
            st.markdown("- **Concentration disclosed** — yes")
            named = sp.get("named_customers") or []
            for c in named:
                name = c.get("name", "—")
                share = c.get("revenue_share_pct")
                if share is not None:
                    st.markdown(f"    - **{name}** — {share:.1f}% of revenue")
                else:
                    st.markdown(f"    - **{name}** _(share not quantified)_")
        return

    # ── Phase C (DEF 14A bundle) ───────────────────────────────────
    if dim_id == "management_tenure_continuity":
        ceo = sp.get("ceo_name")
        ceo_tenure = sp.get("ceo_years_tenure")
        median = sp.get("median_director_tenure_years")
        if ceo:
            ceo_line = f"- **CEO** — {ceo}"
            if ceo_tenure is not None:
                ceo_line += f" ({ceo_tenure} years)"
            st.markdown(ceo_line)
        if median is not None:
            st.markdown(
                f"- **Median director tenure** — {median} years"
            )
        turnover = sp.get("recent_turnover_events") or []
        if turnover:
            st.markdown("- **Recent turnover (last 2 years)**")
            for t in turnover:
                st.markdown(f"    - {t}")
        notable = sp.get("notable_directors") or []
        if notable:
            st.markdown("- **Notable directors**")
            for nd in notable:
                line = f"    - {nd.get('name', '—')} _({nd.get('role', '—')})_"
                if nd.get("years_tenure") is not None:
                    line += f" — {nd['years_tenure']} years"
                st.markdown(line)
        return

    if dim_id == "management_alignment":
        ceo_pct = sp.get("ceo_ownership_pct")
        ins_pct = sp.get("insider_ownership_pct")
        if ceo_pct is not None:
            st.markdown(f"- **CEO ownership** — {ceo_pct:.2f}%")
        if ins_pct is not None:
            st.markdown(
                f"- **Insider ownership (D&O group)** — {ins_pct:.2f}%"
            )
        comp = sp.get("comp_structure") or []
        if comp:
            st.markdown("- **CEO comp structure**")
            for c in comp:
                component = c.get("component", "—").replace("_", " ").title()
                weight = c.get("weight_pct")
                if weight is not None:
                    st.markdown(f"    - {component} — {weight:.0f}%")
                else:
                    st.markdown(
                        f"    - {component} _(weight not quantified)_"
                    )
        metrics = sp.get("performance_metrics") or []
        if metrics:
            st.markdown("- **Performance metrics tied to vesting**")
            for m in metrics:
                st.markdown(f"    - {m}")
        return

    # Generic fallback for any future LLM_AUGMENTED dim
    skip_keys = {"llm_provider", "llm_model", "reason", "error"}
    for k, v in sp.items():
        if k in skip_keys or v is None or v == [] or v == "":
            continue
        st.markdown(f"- **{k.replace('_', ' ').capitalize()}** — {v}")


def _catalog_questions(dimension_id: str) -> List[Dict[str, Any]]:
    """Pull the static catalog questions for a HITL dimension. The list
    endpoint intentionally omits questions (heavy payload); this lets the
    inline expander render structured prompts without a per-card detail
    fetch. Returns [] when the dimension has no questions in the catalog
    (deterministic / llm_augmented / pending_data dims)."""
    from config.qualitative_dimensions import DIMENSIONS
    entry = DIMENSIONS.get(dimension_id)
    if entry is None:
        return []
    return [
        {
            "id":            q.id,
            "text":          q.text,
            "weight":        q.weight,
            "score_anchors": q.score_anchors,
        }
        for q in (entry.questions or ())
    ]


def _render_dimension_detail(d: Dict[str, Any]) -> None:
    """Expander content — bigger, more readable than the previous version."""
    src = d["source_category"]

    if src == "deterministic":
        sp = d.get("source_payload") or {}
        if sp:
            formula = sp.get("formula", "—")
            st.markdown(f"**Formula** — `{formula}`")
            # Surface the inputs the formula used as a clean key:value list
            skip_keys = {"formula", "v1_limitation", "note", "reason"}
            for k, v in sp.items():
                if k in skip_keys:
                    continue
                key_human = k.replace("_", " ").capitalize()
                st.markdown(f"- **{key_human}** — {v}")
            for caveat_key in ("v1_limitation", "note"):
                if sp.get(caveat_key):
                    st.info(sp[caveat_key])

    elif src == "hitl":
        # The list endpoint omits `questions` (heavy payload, enforced by
        # test_list_omits_questions). Catalog is static config — load it
        # locally so the inline expander can render structured prompts +
        # composite math without a per-dimension API roundtrip.
        questions = d.get("questions") or _catalog_questions(d["dimension_id"])
        sub_scores = d.get("sub_scores") or {}

        if questions:
            st.markdown("**Structured prompts**")
            for q in questions:
                sub = sub_scores.get(q["id"])
                sub_text = _format_score(sub) if sub is not None else "—"
                # Larger, readable line per question
                st.markdown(
                    f"**{sub_text}**  ·  weight {q['weight']:.2f}  ·  {q['text']}"
                )

        # Composite math
        if d.get("score") is not None and sub_scores and questions:
            terms = []
            for q in questions:
                s = sub_scores.get(q["id"])
                if s is not None:
                    terms.append(f"{q['weight']:.2f} × {_format_score(s)}")
            if terms:
                st.divider()
                st.markdown(
                    f"**Composite** = {' + '.join(terms)} = "
                    f"**{_format_score(d['score'])}**"
                )

        # Narrative
        if d.get("narrative"):
            st.divider()
            st.markdown("**Narrative**")
            st.markdown(f"> {d['narrative']}")

        # LLM proposal trail — surfaces evidence quotes + the original
        # LLM-proposed snapshot. Shown whenever an llm_proposal payload
        # exists (i.e. the dim has been touched by the proposer at any
        # point, regardless of current provenance).
        proposal = d.get("llm_proposal") or {}
        if proposal:
            st.divider()
            prov = d.get("provenance") or "—"
            conf = proposal.get("confidence") or d.get("confidence") or "—"
            st.markdown(
                f"**LLM proposal**  ·  provenance: `{prov}`  ·  "
                f"confidence: `{conf}`"
            )
            llm_narr = proposal.get("narrative")
            if llm_narr:
                st.markdown(f"_LLM narrative:_  {llm_narr}")
            quotes = proposal.get("evidence_quotes") or []
            if quotes:
                st.markdown("_Evidence quotes:_")
                for q in quotes:
                    qid = q.get("question_id", "—")
                    txt = q.get("quote", "")
                    src_label = q.get("source", "")
                    st.markdown(
                        f"- **{qid}** ({src_label}): _\"{txt}\"_"
                    )

    elif src == "llm_augmented":
        sp = d.get("source_payload") or {}
        provider = sp.get("llm_provider", "—")
        model = sp.get("llm_model", "—")

        if not sp or d.get("score") is None:
            # Empty-state — no extraction has run yet, or it failed
            failure_reason = sp.get("reason") if sp else None
            st.markdown(
                "**Awaiting extraction.** This dimension is populated by "
                "an LLM agent reading the 10-K (Item 1 + Item 1A) or "
                "DEF 14A proxy statement. Run **Stage 4 Agents** from the "
                "sidebar to extract it."
            )
            if failure_reason:
                st.warning(f"Last attempt failed: {failure_reason}")
            return

        # Provenance trail
        st.markdown(
            f"**Provenance** — extracted via `{provider}` (`{model}`)"
        )

        if d.get("narrative"):
            st.markdown("**Narrative**")
            st.markdown(f"> {d['narrative']}")

        st.divider()
        st.markdown("**Structured extraction**")
        _render_llm_payload(d["dimension_id"], sp)

    elif src == "pending_data":
        sp = d.get("source_payload") or {}
        reason = sp.get("reason") if sp else None
        st.markdown(
            "**Data infrastructure pending.** This dimension requires data "
            "that is not yet in the cleaning pipeline. The slot is reserved "
            "in the framework so the gap is visible; the dimension activates "
            "when the underlying ingestion ships."
        )
        if reason:
            st.markdown(f"**Specific gap** — {reason.replace('_', ' ')}")


# ─────────────────────────────────────────────────────────────────────────────
# Top-level render
# ─────────────────────────────────────────────────────────────────────────────

def render_qualitative_view(
    ticker: str,
    qual_data: Optional[Dict[str, Any]],
    categories_data: Optional[Dict[str, Any]],
    on_recompute,
    api_base: str,
    on_submit_success,
) -> None:
    """Entry point invoked from streamlit_app.py."""
    if not ticker:
        st.info("Select a ticker from the sidebar.")
        return

    if not qual_data:
        st.warning("Could not load qualitative data for this ticker.")
        return

    # ── Assessment dialog ───────────────────────────────────────────────
    @st.dialog("Assess dimension", width="large")
    def _open_assessment_dialog():
        from aletheia.ui.qualitative_input import render_assessment_dialog
        dim_id = st.session_state.get("qual_pending_dim")
        if not dim_id:
            st.warning("Dialog opened without a target dimension.")
            return
        try:
            r = httpx.get(
                f"{api_base}/ticker/{ticker}/qualitative/{dim_id}",
                timeout=10,
            )
            r.raise_for_status()
            detail = r.json()
        except Exception as e:
            st.error(f"Could not load dimension detail: {e}")
            return
        st.subheader(detail.get("title", dim_id))
        render_assessment_dialog(
            ticker=ticker,
            dimension=detail,
            api_base=api_base,
            on_success=on_submit_success,
        )

    def _request_assess(dim_id: str) -> None:
        st.session_state["qual_pending_dim"] = dim_id
        _open_assessment_dialog()

    # ── Title row + recompute button ────────────────────────────────────
    title_col, action_col = st.columns([3, 1])
    with title_col:
        st.title(f"{ticker} — Qualitative Analysis")
        st.caption(
            "19 analytical dimensions · scores 1–7 · "
            "category composites renormalize over assessed members"
        )
    with action_col:
        st.write("")  # alignment with title
        if st.button(
            "↻ Recompute deterministic",
            key="qual_recompute",
            use_container_width=True,
        ):
            on_recompute()

    st.divider()

    # ── Category composite strip ────────────────────────────────────────
    if categories_data and categories_data.get("categories"):
        _render_category_strip(categories_data["categories"])
        st.divider()

    # ── Per-category sections ───────────────────────────────────────────
    dimensions = qual_data.get("dimensions", [])
    cats_in_order = [c for c in (categories_data or {}).get("categories", [])]

    for cat_summary in cats_in_order:
        cat_id = cat_summary["category_id"]
        members = [d for d in dimensions if d["category"] == cat_id]
        if not members:
            continue

        composite = cat_summary.get("composite_score")
        n_a = cat_summary.get("n_assessed", 0)
        n_t = cat_summary.get("n_total", 0)

        # Section header — larger, with composite inline
        st.header(cat_summary["title"])
        if composite is not None:
            st.caption(
                f"Composite **{_format_score(composite)}** · "
                f"{n_a} of {n_t} dimensions assessed"
            )
        elif n_t > 0:
            st.caption(f"{n_a} of {n_t} dimensions assessed")
        else:
            st.caption("Data infrastructure pending — no assessable dimensions yet")

        st.write("")  # vertical breathing room

        for d in members:
            _render_dimension_card(d, on_assess=_request_assess)
            st.write("")  # space between cards

        st.write("")  # space between categories
