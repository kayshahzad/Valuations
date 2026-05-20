## Summary

<!-- 1-3 bullet points on what this PR does and why -->

## Test plan

<!-- bullet checklist of how this PR was verified -->

---

### Catalog changes

If this PR modifies `config/qualitative_dimensions.py` (qualitative-dimension catalog) or
`config/valuation_defaults.py` (lifecycle profiles, terminal-growth caps), tick the
applicable boxes:

- [ ] **Catalog change is purely structural** (renaming, code reorganization, type-only) — no analyst review needed
- [ ] **Catalog change alters analytical meaning** (weights, score buckets, question wording, sub-question additions/removals, new dimensions) — analyst sign-off required
  - [ ] Analyst reviewer:
  - [ ] Rationale documented in this PR description
  - [ ] `code_version` field bumped on the affected dimension(s) so prior assessments correctly flag stale via `code_git_sha` mismatch

The catalog is the framework's analytical schema. Engineering review verifies the
code is correct; only analyst review verifies the *claim* embedded in the weights
or buckets is correct. Skipping analyst sign-off on a meaning-altering change
silently changes the framework under existing assessments.
