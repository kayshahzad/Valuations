# Calculation-Layer Safety — Engineer Reference

How to write calculation functions that integrate cleanly with the
validation framework, and how to consume their outputs as a caller.

## Audience

Every engineer who calls or writes a calculation function. If you are
adding a new metric, refactoring an existing one, or wiring a new
downstream consumer to calc outputs — read this first.

## The framework in one paragraph

The validation framework (`aletheia/calculations/`) provides primitives
that calc functions use to catch input degradation and output
implausibility at function boundaries. Every primitive honors the
`ALETHEIA_GUARD_MODE` env var — `off` is a no-op for legacy
compatibility, `shadow` logs structured warnings, `soft` adds UI
surfacing, `hard` raises errors that callers must handle. The MDT
incident (36.5% implied CAGR from silently-coerced NaN inputs)
motivated this design — without the framework, the same class of
silent corruption can recur on any deterministic function.

## Writing a calc function — the checklist

Place these in this order at function entry:

### 1. Finiteness on every required input

```python
from aletheia.calculations import _require_finite

def my_calc(*, ticker, revenue, ebit, capex, tax_rate, **_):
    fn = "my_calc"
    _require_finite(revenue, "revenue", ticker=ticker, fn=fn)
    _require_finite(ebit,    "ebit",    ticker=ticker, fn=fn)
    _require_finite(capex,   "capex",   ticker=ticker, fn=fn)
    _require_finite(tax_rate, "tax_rate", ticker=ticker, fn=fn)
```

### 2. Tier-1 sign checks (hard fail)

```python
from aletheia.calculations import _require_strict_nonneg

# Revenue is Tier 1 — must be non-negative
_require_strict_nonneg(revenue, "revenue", ticker=ticker, fn=fn)
```

**Coding-bug guard**: calling `_require_strict_nonneg` on a Tier-2 field
raises `ValueError` unconditionally — that's a caller bug, not a data
bug. For Tier-2 fields use the soft flag + range check instead:

```python
from aletheia.calculations import _flag_unusual, _require_range, RANGE_BOUNDS

# CapEx is Tier 2 — can legitimately be negative
if capex < 0:
    _flag_unusual(capex, "capex", ticker=ticker, fn=fn,
                  note="Negative CapEx may indicate net divestitures (legitimate) "
                       "or sign-convention error (bug). Verify against cash flow.")
```

### 3. Range checks on ratios (the most reliable bug-catcher)

```python
cmin, cmax = RANGE_BOUNDS["capex_to_revenue"]
_require_range(
    capex / revenue, min=cmin, max=cmax,
    field_name="capex_to_revenue", ticker=ticker, fn=fn,
    note="negative beyond bound → sign error; above upper → unit error",
)
```

Range checks catch sign errors, unit errors, and wrong-tag mappings
simultaneously — more powerful than sign rules alone.

### 4. Arithmetic identity checks (most powerful)

```python
from aletheia.calculations import _require_consistent, IDENTITY_TOLERANCES

# FCF = OperatingCF - CapEx (definitional)
_require_consistent(
    actual=fcf, expected=(operating_cf - capex),
    tolerance_pct=IDENTITY_TOLERANCES["fcf_equals_opcf_minus_capex"],
    identity_name="fcf_equals_opcf_minus_capex",
    ticker=ticker, fn=fn,
)
```

Identities encode what MUST be true — `FCF = OpCF - CapEx` doesn't
depend on what's "typical." They are the framework's strongest
verification mechanism.

### 5. Output sanity validation before returning

```python
from aletheia.calculations import CalculationOutputError

implied_cagr = my_solver(...)

cagr_min, cagr_max = RANGE_BOUNDS["implied_cagr"]
if not (cagr_min <= implied_cagr <= cagr_max):
    raise CalculationOutputError(
        f"implied_cagr={implied_cagr:.4f} outside [{cagr_min}, {cagr_max}]",
        ticker=ticker, fn=fn, field="implied_cagr_output",
        value=implied_cagr, expected=f"[{cagr_min}, {cagr_max}]",
    )
```

`CalculationOutputError` is distinct from `CalculationInputError` so
callers can distinguish "input was bad" from "model produced
nonsense from individually-valid inputs." The MDT bug was an output-
sanity failure: inputs were silently coerced, solver compensated.

### 6. Eliminate silent fallbacks

When ingest is reliable, these patterns are no longer needed:

| Anti-pattern | Replace with |
|---|---|
| `tax_rate = clean_tax or 0.21` | Explicit `tax_rate_fallback` param OR `_require_finite` + raise |
| `ebit = ebit if pd.notna(ebit) else 0` | `_require_finite` + raise |
| `capex = abs(capex)` | `_require_range(capex/revenue, ...)`; fix sign at ingest |
| `depreciation = max(0, depreciation)` | `_require_strict_nonneg` |

Search for `or 0`, `or 0.21`, `fillna(0)`, `abs(`, `max(0,`, `np.nan_to_num` in calc functions — each occurrence is a candidate for replacement.

## Calling a calc function — the checklist

### As a pipeline consumer (orchestration layer)

Calc functions raise `CalculationError` (or its subclasses). Catch at the
orchestration boundary — never inside a calc function:

```python
from aletheia.calculations import CalculationError

try:
    result = my_calc(ticker=ticker, revenue=...)
except CalculationError as e:
    # Field is now unavailable for this ticker
    mark_field_unavailable(ticker, e.field, reason=str(e))
    # Continue with other fields / other tickers
    continue
```

**Never substitute a fallback value** when calc raises. The right
behavior: mark the field as unavailable, continue processing other
fields/tickers, surface the receipt to the analyst.

### As a UI rendering layer

Check the `unavailable` status on a field before rendering:

```python
if field_status == "unavailable":
    render_unavailable_affordance(field, reason=receipt.message)
else:
    render_value(field, value)
```

Never render zeros, dashes, or computed-from-fallback values where
validation failed. The UI's job is to surface the gap, not paper over it.

### As an LLM agent prompt

Filter `unavailable` fields out of LLM context entirely. The LLM should
not write prose about a field that failed validation:

```python
context = {k: v for k, v in calc_outputs.items()
           if get_status(k) != "unavailable"}
```

If a field critical to the thesis is unavailable, the synthesizer should
refuse to produce that section of the thesis rather than write around
the gap. The thesis_synthesizer prompt already enforces this via the
`cited_signals` validator.

## Mode resolution & kill switch

The `ALETHEIA_GUARD_MODE` env var is the global kill switch:

| Mode | Behavior |
|---|---|
| `off` | All primitives are no-ops; legacy fallback behavior preserved |
| `shadow` | Primitives log structured warnings to `audits/guard_violations_*.jsonl`; never raise |
| `soft` | Same as shadow + UI Data Quality panel reads persisted violations |
| `hard` | Primitives raise `CalculationError`; caller's try/except decides next step |

Defaults to `shadow` if unset. Set `ALETHEIA_GUARD_MODE=off` in any
emergency to disable enforcement everywhere — takes effect on the
next call, no restart needed.

### Per-function override

A function can opt into stricter enforcement than the global mode via
a function-level resolver. Example from `reverse_dcf.py`:

```python
def _reverse_dcf_mode() -> str:
    """reverse_dcf is in hard-mode by default. Kill switch via env=off."""
    env = os.environ.get("ALETHEIA_GUARD_MODE", "").strip().lower()
    return "off" if env == "off" else "hard"

# In each guard call:
_require_range(..., mode_override=_reverse_dcf_mode())
```

This pattern lets us flip enforcement on individual functions without
flipping the global. Use when:
- The function has been thoroughly tested
- Its inputs are well-understood
- The blast radius of false-positives is acceptable

Current per-function hard-mode functions:
- `reverse_dcf.run` (Phase 6 Step 3 — MDT incident motivated this)

## Override registry

The framework's `OVERRIDES` registry (`aletheia/calculations/_overrides.py`)
holds per-ticker exception entries for legitimate edge cases. Each entry
documents WHY the exception exists with `reason`, `created_date`, and
`review_by_date`.

When adding a new override:
1. Document the reason substantively (>30 chars)
2. Pick a short `review_by_date` for transient cases (e.g., V shares
   ingest bug expires 2026-08-12 to force a real fix)
3. Pick a longer `review_by_date` for genuinely-permanent edge cases
   (e.g., LOW negative-equity buyback pattern)

**Registry stays small.** The framework warns at startup if total
entries exceed 20. If you find yourself adding many overrides, your
validation rules are too strict — recalibrate the rule, don't accumulate
exceptions.

## Error hierarchy

```
CalculationError                  # base; always re-raisable
├── CalculationInputError         # upstream data violated a guard
├── CalculationOutputError        # model produced an implausible result
└── CalculationConsistencyError   # arithmetic identity violated
```

Distinguish in receipts:
- `CalculationInputError` → blame upstream; fix at cleaning/ingestion
- `CalculationOutputError` → blame model assumptions or input combination
- `CalculationConsistencyError` → blame an XBRL tag mapping or definition

## Audit log

Every `calc_guard_violation` / `calc_guard_soft_flag` log record gets
captured to `audits/guard_violations_{YYYY-MM-DD}.jsonl` with a
structured envelope. Per-day rotation; one JSON object per line; usable
with `jq` or DuckDB.

Example query — find every CAT violation in the last 7 days:

```bash
jq 'select(.ticker == "CAT")' audits/guard_violations_*.jsonl
```

## Where to learn more

- [docs/sign_conventions.md](sign_conventions.md) — tier definitions, RANGE_BOUNDS, identity tolerances
- [docs/calculation_inventory.md](calculation_inventory.md) — every calc function inventoried
- [docs/calculation_anomaly_catalog.md](calculation_anomaly_catalog.md) — historical anomalies the framework was designed to catch (A1-A19)
- [docs/phase6_triage.md](phase6_triage.md) — empirical findings from rolling out the framework
- [aletheia/calculations/](../aletheia/calculations/) — the framework source
- [tests/calculations/](../tests/calculations/) — 114 tests covering primitives, conventions, schema contract, and per-function wiring
