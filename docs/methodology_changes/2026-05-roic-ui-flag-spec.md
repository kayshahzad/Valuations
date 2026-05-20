# UI flag spec — ROIC convention canonicalization

Visual treatment for the `📐 Convention canonicalized 2026-05` flag that surfaces in the dashboard for two quarters following the Phase 1 merge. Companion document to [2026-05-roic-invested-capital.md](2026-05-roic-invested-capital.md).

## Visual

**Glyph:** 📐 (already used for Cat-D methodology divergences — the registry-tagged equivalent. Reusing it keeps the visual vocabulary tight.)

**Inline label:** `📐 2026-05` — short enough to fit beside a metric value in a tight column.

**Tooltip (hover):**
> Convention canonicalized 2026-05. ROIC now excludes excess cash from invested capital. See `docs/methodology_changes/2026-05-roic-invested-capital.md` for details. Flag retires 2026-12-31.

## Where it appears

| Surface | File | Render rule |
|---|---|---|
| Financials tab — Returns & Capital table, ROIC row | [aletheia/ui/financials_view.py:701](../../aletheia/ui/financials_view.py#L701) | Append flag to the ROIC metric label in the table's first column |
| Financials tab — Multi-year history, ROIC column | [aletheia/ui/financials_view.py:409-410](../../aletheia/ui/financials_view.py#L409-L410) | Append flag to the column header (one-line caption above the progress bar) |
| Dashboard — top-line ROIC cell | [aletheia/ui/dashboard.py:343-344](../../aletheia/ui/dashboard.py#L343-L344) | Append flag to the metric label, same row as the validation dot |
| Deep Dive — ROIC − WACC spread metric | [aletheia/ui/deep_dive_view.py:288](../../aletheia/ui/deep_dive_view.py#L288) | Append flag to the `st.metric` label |
| Reports tab — Executive HTML / Detailed Markdown exports | [aletheia/tools/thesis_builder.py](../../aletheia/tools/thesis_builder.py) report template | Add the flag inline after the ROIC value in the relevant table cell |
| FMP Compare view — ROIC rows | [aletheia/ui/fmp_compare_view.py:390](../../aletheia/ui/fmp_compare_view.py#L390), [532](../../aletheia/ui/fmp_compare_view.py#L532) | **Skip** — this view is explicitly the methodology comparison surface; the flag would be redundant noise next to the Cat-D tag already present |

## Where it does NOT appear

- Stage 3 validation view — already shows the convention divergence machinery directly.
- Pipeline Status matrix — surfaces stage success, not metric values.
- Universe table — too tight a row to fit a per-metric annotation.
- LLM agent outputs — the underlying number is what changes; the agents speak about it without needing UI annotation. Documented in the methodology memo instead.

## Implementation pattern

A single helper in [aletheia/ui/validation_badge.py](../../aletheia/ui/validation_badge.py) (the existing badge module):

```python
# Date-gated. After this date returns "" so the flag retires
# automatically without a code change.
_CONVENTION_FLAG_RETIRES = "2026-12-31"
_CONVENTION_FLAG = "📐 2026-05"
_CONVENTION_TOOLTIP = (
    "Convention canonicalized 2026-05. ROIC now excludes excess cash "
    "from invested capital. See docs/methodology_changes/"
    "2026-05-roic-invested-capital.md. Flag retires 2026-12-31."
)
_CONVENTION_AFFECTED_METRICS = {"ROIC", "Invested Capital"}


def convention_flag(metric_label: str) -> str:
    """Return the convention-canonicalized flag suffix, or '' when
    the metric isn't affected or the flag has retired."""
    if metric_label not in _CONVENTION_AFFECTED_METRICS:
        return ""
    import datetime as _dt
    if _dt.date.today() > _dt.date.fromisoformat(_CONVENTION_FLAG_RETIRES):
        return ""
    return f" {_CONVENTION_FLAG}"
```

Each render site appends `convention_flag(label)` to its metric label. Single-source: change the retirement date or the affected-metrics set in one place. The retirement is *automatic* — no code change required when 2026-12-31 passes.

## Retirement timeline

| Date | State |
|---|---|
| 2026-05 (Phase 1 merge) | Flag appears |
| 2026-Q3 | Flag still visible — analyst feedback / questions accumulate here |
| 2026-12-31 | Flag retires (date-gated, no code change) |
| 2027-01-01 onwards | Number is just "ROIC" — convention has had two full quarters of explicit annotation |

## Phase 1 implementation checklist

- [ ] Add `convention_flag()` helper to `validation_badge.py`
- [ ] Wire into each of the 5 render sites listed above
- [ ] Visual QA pass: confirm the flag fits in tight columns without truncating
- [ ] Confirm tooltip renders on hover in Streamlit (some columns truncate help text)
- [ ] Verify the retirement date-gate works (manually set system date forward in a test)
