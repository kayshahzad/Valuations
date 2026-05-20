"""HITL assessment dialog — Step 5 of week 1.

Renders a Streamlit modal dialog (`@st.dialog`) for one (ticker, HITL
dimension) pair:

  - Optional "Restore previous draft" banner if session_state has a
    saved partial response under the current catalog hash
  - One row per sub-question: 1-7 slider + score-anchor cheat-sheet
  - Live composite score with weighted-math display
  - Optional 50-word narrative textarea (with character counter)
  - Submit button (disabled until all sub-questions answered)

Posts to `POST /ticker/{T}/qualitative/{dim}` on submit. On success:
  - Clears the session_state draft for this dimension
  - Invalidates the qualitative fetch caches
  - Reruns the script so the parent dimension card reflects the new
    score immediately

The composite is server-computed; the client-side display is purely
for UX feedback. If the server's computed score disagrees with the
preview shown here, the server's value wins.

Invariants:
  - All sub-questions must be answered before submit is enabled
  - Narrative ≤ 500 chars (validated client-side, also enforced server-side)
  - Catalog questions/weights are fetched fresh on each dialog open via
    GET /ticker/{T}/qualitative/{dim}, so a catalog redeploy mid-session
    doesn't render stale prompts
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

import httpx
import streamlit as st

from aletheia.ui.qualitative_drafts import (
    load_draft, save_draft, clear_draft, has_any_draft_for, time_ago,
)


# Theme tokens — match qualitative_view.py
_GREEN = "#10b981"
_AMBER = "#f59e0b"
_RED   = "#ef4444"
_MUTED = "rgba(120,120,128,0.85)"
_BAR_BG = "rgba(120,120,128,0.20)"
_PANEL_BG_AMBER = "rgba(245,158,11,0.08)"
_PANEL_BG_GREEN = "rgba(16,185,129,0.06)"


def _score_color(score: float) -> str:
    if score >= 6:  return _GREEN
    if score >= 4:  return _AMBER
    return _RED


def _compose(sub_scores: Dict[str, int], questions: List[Dict[str, Any]]) -> Optional[float]:
    """Client-side composite for live preview. Mirrors the server formula
    in `_compute_composite_score`. Returns None when not all questions
    are answered."""
    if not all(q["id"] in sub_scores for q in questions):
        return None
    return round(sum(q["weight"] * float(sub_scores[q["id"]]) for q in questions), 2)


def _render_anchor_help(anchors: Dict[int, str]) -> None:
    """Show 1-4-7 anchors as hint text under the slider so the analyst
    has the calibration cheat-sheet visible while answering."""
    if not anchors:
        return
    items = []
    for level in (1, 4, 7):
        text = anchors.get(level) or anchors.get(str(level))   # JSON keys may be str
        if text:
            color = _RED if level == 1 else _AMBER if level == 4 else _GREEN
            items.append(
                f'<span style="color:{color};font-weight:600">{level}</span>: '
                f'<span style="color:{_MUTED}">{text}</span>'
            )
    if items:
        st.markdown(
            f'<div style="font-family:DM Mono,monospace;font-size:10.5px;'
            f'line-height:1.5;margin-top:-4px;margin-bottom:10px;">'
            + " &nbsp;·&nbsp; ".join(items) +
            "</div>",
            unsafe_allow_html=True,
        )


def _render_restore_banner(
    ticker: str,
    dimension_id: str,
    catalog_hash: str,
) -> Optional[Dict[str, Any]]:
    """If a draft exists for the current catalog_hash, show a banner.
    If a draft exists for a *different* catalog_hash, surface a
    "stale draft" notice and offer to discard.

    Returns the draft dict to apply, or None if no restore action.
    """
    current = load_draft(ticker, dimension_id, catalog_hash)
    if current:
        st.markdown(
            f'<div style="background:{_PANEL_BG_GREEN};border-left:3px solid {_GREEN};'
            f'padding:8px 12px;margin-bottom:12px;font-size:12px;color:{_MUTED};">'
            f'<span style="color:{_GREEN};font-weight:600">●</span> '
            f'Restored from draft (saved {time_ago(current["saved_at"])})'
            f'</div>',
            unsafe_allow_html=True,
        )
        return current

    # Look for a stale-hash draft (catalog changed since draft was saved)
    stale = has_any_draft_for(ticker, dimension_id)
    if stale and stale["stored_catalog_hash"] != catalog_hash:
        st.markdown(
            f'<div style="background:{_PANEL_BG_AMBER};border-left:3px solid {_AMBER};'
            f'padding:8px 12px;margin-bottom:12px;font-size:12px;color:{_MUTED};">'
            f'<span style="color:{_AMBER};font-weight:600">⚠</span> '
            f"A draft exists from a previous catalog version "
            f"(saved {time_ago(stale['draft']['saved_at'])}). "
            f"Question wording or weights have changed since — "
            f"please re-answer."
            f'</div>',
            unsafe_allow_html=True,
        )
    return None


def _render_composite_preview(
    sub_scores: Dict[str, int],
    questions: List[Dict[str, Any]],
) -> None:
    """Live composite score with the weighted-math line."""
    composite = _compose(sub_scores, questions)
    if composite is None:
        n_answered = sum(1 for q in questions if q["id"] in sub_scores)
        st.markdown(
            f'<div style="font-family:DM Mono,monospace;font-size:11px;'
            f'color:{_MUTED};">'
            f'Answer all {len(questions)} questions to see the composite. '
            f'({n_answered}/{len(questions)} answered)'
            f'</div>',
            unsafe_allow_html=True,
        )
        return

    color = _score_color(composite)
    terms = " + ".join(
        f'{q["weight"]:.2f}×{sub_scores[q["id"]]}'
        for q in questions
    )
    st.markdown(
        f'<div style="border-top:1px solid {_BAR_BG};padding-top:10px;margin-top:8px;">'
        f'<div style="font-family:DM Mono,monospace;font-size:11px;color:{_MUTED};">'
        f'Composite: {terms}</div>'
        f'<div style="font-size:32px;font-weight:600;color:{color};line-height:1.1;'
        f'margin-top:4px;">= {composite}</div>'
        f'</div>',
        unsafe_allow_html=True,
    )


def _post_assessment(
    api_base: str,
    ticker: str,
    dimension_id: str,
    sub_scores: Dict[str, int],
    narrative: Optional[str],
) -> Optional[Dict[str, Any]]:
    """POST to /ticker/{T}/qualitative/{dim}. Returns the response dict
    on success, None on failure (with an st.error banner)."""
    payload = {
        "sub_scores": {k: float(v) for k, v in sub_scores.items()},
        "narrative":  (narrative or None),
    }
    try:
        r = httpx.post(
            f"{api_base}/ticker/{ticker}/qualitative/{dimension_id}",
            json=payload,
            timeout=15,
        )
        if r.status_code != 200:
            try:
                detail = r.json().get("detail", r.text)
            except Exception:
                detail = r.text
            st.error(f"Submit failed ({r.status_code}): {detail}")
            return None
        return r.json()
    except Exception as e:
        st.error(f"Submit request failed: {e}")
        return None


def render_assessment_dialog(
    ticker: str,
    dimension: Dict[str, Any],
    api_base: str,
    on_success,
) -> None:
    """Body of the Streamlit dialog. The decorator (`@st.dialog`) is
    applied at the call site (in qualitative_view.py) so the dialog
    title can interpolate the dimension's title.

    Args:
        ticker: Selected ticker.
        dimension: One element from /ticker/{T}/qualitative/{dim} —
                   must include `questions`, `catalog_hash`, etc.
        api_base: API base URL.
        on_success: Callable invoked after successful submission. Used
                    to clear fetch caches and trigger a rerun in the
                    parent page.
    """
    catalog_hash = dimension.get("catalog_hash") or ""
    dim_id = dimension["dimension_id"]
    questions = dimension.get("questions") or []

    # ── Restore previous draft if applicable ────────────────────────────
    draft = _render_restore_banner(ticker, dim_id, catalog_hash)

    # Initialize session_state form values from draft (or empty)
    sub_scores: Dict[str, int] = {}
    initial_narrative = ""
    if draft:
        for q_id, score in (draft.get("sub_scores") or {}).items():
            sub_scores[q_id] = int(score)
        initial_narrative = draft.get("narrative") or ""

    # ── Description ─────────────────────────────────────────────────────
    if dimension.get("description"):
        st.caption(dimension["description"])

    # ── Per-question sliders ────────────────────────────────────────────
    st.markdown(" ")  # breathing room
    for q in questions:
        # Slider key is unique per (dim, question) so reopening the dialog
        # picks up prior session_state values
        slider_key = f"qual_input__{ticker}__{dim_id}__{q['id']}"

        st.markdown(
            f'<div style="font-size:13px;font-weight:500;line-height:1.4;'
            f'margin-bottom:2px;">{q["text"]}</div>'
            f'<div style="font-family:DM Mono,monospace;font-size:10px;'
            f'color:{_MUTED};margin-bottom:2px;">'
            f'weight = {q["weight"]:.2f}</div>',
            unsafe_allow_html=True,
        )

        # Pre-populate slider value from draft (defaults to 4 = midpoint)
        default_value = sub_scores.get(q["id"], 4)
        # Only set the session_state default if the slider hasn't been
        # interacted with yet — Streamlit owns it after first render
        if slider_key not in st.session_state:
            st.session_state[slider_key] = default_value

        score = st.slider(
            label=q["text"],
            min_value=1, max_value=7,
            key=slider_key,
            label_visibility="collapsed",
        )
        sub_scores[q["id"]] = int(score)

        _render_anchor_help(q.get("score_anchors") or {})

    # ── Narrative ───────────────────────────────────────────────────────
    narrative_key = f"qual_narrative__{ticker}__{dim_id}"
    if narrative_key not in st.session_state:
        st.session_state[narrative_key] = initial_narrative

    narrative = st.text_area(
        "Optional narrative (≤ 500 chars)",
        key=narrative_key,
        max_chars=500,
        height=80,
        placeholder="Brief rationale, evidence, or notes — optional.",
    )

    # ── Save draft (incrementally) ──────────────────────────────────────
    # Streamlit reruns the dialog body on every interaction. Saving the
    # current state on every render means the draft is always up-to-date
    # in session_state — closing the dialog and reopening within the same
    # session restores the latest values.
    save_draft(ticker, dim_id, catalog_hash, sub_scores, narrative)

    # ── Composite preview ───────────────────────────────────────────────
    _render_composite_preview(sub_scores, questions)

    # ── Submit / cancel ─────────────────────────────────────────────────
    st.markdown(" ")
    btn_cols = st.columns([1, 1, 4])
    with btn_cols[0]:
        all_answered = all(q["id"] in sub_scores for q in questions)
        submit = st.button(
            "Submit assessment",
            disabled=not all_answered,
            type="primary",
            use_container_width=True,
            key=f"qual_submit__{ticker}__{dim_id}",
        )
    with btn_cols[1]:
        cancel = st.button(
            "Cancel",
            use_container_width=True,
            key=f"qual_cancel__{ticker}__{dim_id}",
        )
    with btn_cols[2]:
        st.caption(
            "Draft auto-saved every change · lost on browser tab close "
            "(week-1 limitation)."
        )

    if cancel:
        st.rerun()

    if submit:
        result = _post_assessment(
            api_base, ticker, dim_id, sub_scores, narrative,
        )
        if result is not None:
            clear_draft(ticker, dim_id, catalog_hash)
            # Also drop the per-slider session_state so the dialog opens
            # fresh on next assess (rather than re-using the just-submitted
            # values as defaults)
            for q in questions:
                st.session_state.pop(
                    f"qual_input__{ticker}__{dim_id}__{q['id']}", None,
                )
            st.session_state.pop(narrative_key, None)
            st.success(
                f"Submitted: composite score **{result['score']}**"
            )
            on_success()


__all__ = ["render_assessment_dialog"]
