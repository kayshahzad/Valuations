"""Qualitative analysis framework.

Versioned 1-7 assessments across 18 analytical dimensions covering quality,
capital allocation, competitive position, risk, and management. Three
source categories:

- DETERMINISTIC: computed from `company_records_latest` via formulas pinned
  in the catalog. Recomputed on demand; auto-flagged stale when inputs or
  formula version change.
- HITL: structured prompts answered by an analyst; composite score
  weighted-averaged from sub-question responses.
- LLM_AUGMENTED: extracted from filings via LLM (deferred — week 4-6).

Plus a fourth empty-state for dimensions whose data infrastructure is not
yet wired (`PENDING_DATA`) — distinct from "not yet assessed."

The catalog (`config.qualitative_dimensions.DIMENSIONS`) is code-as-truth:
weights, sub-questions, bucket cutoffs, formula citations all live in
typed Python and are reviewed via PR with mandatory analyst sign-off on
meaning-altering changes.

Assessment storage in DuckDB (`qualitative_assessments` table) follows
the agent_runs versioning pattern: each submission appends a new row,
the latest version per (ticker, dimension) is exposed via the
`qualitative_assessments_latest` view.
"""
