# Moat Fingerprint Methodology

**Status:** Active. Locked at v1.0 (2026-05-02).
**Owner:** Calc layer (deterministic, no LLM).
**Consumers:** `aletheia/tools/moat_fingerprint.py` implements this spec; `aletheia/tools/conviction_scorer.py` reads `moat_fingerprint.score` for Pillar 1 (P1 — Moat Strength).

## Purpose

Replace the previous LLM-derived `moat_score` (1-10) with a deterministic, auditable fingerprint computed from multi-year financial data already present in DuckDB. The fingerprint produces a 1-5 score consumable by ConvictionScorer Pillar 1.

## Why a fingerprint, not a measure

A genuine economic moat is **inferred from financial fingerprints**, not measured directly. Moats produce observable patterns:
- Persistent ROIC above cost of capital (the most reliable single signal)
- Stable gross margins through demand cycles (pricing power)
- Low maintenance capex relative to revenue (asset-light dynamics)

A fingerprint algorithm that scores these patterns is a *signal aggregator*, not a *moat measurement*. We document this distinction explicitly because methodology drift in this space happens when the signal is mistaken for the measure.

## Data window

| Rule | Specification |
|---|---|
| Window source | DuckDB `company_records` history per ticker |
| Window length | Longest available, capped at **10 years** |
| Minimum requirement | **5 years** of clean records |
| Insufficient history | `score = None`, `is_null_due_to_history = True` (NOT defaulted to a value) |
| Year selection | Sorted by `fiscal_year` ascending; take last N years (most recent N) |

**Rationale for 5-year minimum:** below 5 years the statistics (ROIC persistence, GM coefficient of variation) are too noisy to distinguish moat patterns from cyclical luck. Defaulting a low-history ticker to a numeric score would conflate "we don't know" with "no moat."

## Components & weights

Total weight = 100%. Component scores are 1-5; weighted total is rounded to nearest integer (1-5).

### Component 1 — ROIC Persistence (50% weight)

Most reliable single moat signal per the framework's Liberti methodology — durable ROIC above WACC indicates real value creation.

| Score | Threshold |
|---|---|
| 5/5 | ROIC ≥ 8% in **every** year of window AND median ROIC ≥ 15% |
| 4/5 | ROIC ≥ 8% in ≥80% of years AND median ROIC ≥ 12% |
| 3/5 | ROIC ≥ 8% in ≥50% of years |
| 2/5 | ROIC ≥ 8% in ≥25% of years |
| 1/5 | otherwise |

**Field:** `derived_ROIC` (already in DB, populated by cleaning_engine `_compute_derived`).

### Component 2 — Gross Margin Stability (30% weight)

Stable GM through cycles indicates pricing power. Low coefficient of variation (CV = std/mean) is the signal.

| Score | Threshold (non-cyclical) |
|---|---|
| 5/5 | CV ≤ 0.05 |
| 4/5 | CV ≤ 0.10 |
| 3/5 | CV ≤ 0.20 |
| 2/5 | CV ≤ 0.35 |
| 1/5 | otherwise |

**Field:** `derived_GrossMargin_Pct` from each fiscal year in window.

**Sector normalization rule** (see Cyclical Handling below): cyclical tickers score this against within-sector peer median CV instead of the absolute thresholds above.

### Component 3 — Capex Intensity (20% weight)

Median capex/revenue across the window. Low intensity is a signal of asset-light moat dynamics, BUT see the explicit caveat below — this is a signal not a measure, hence the 20% (lowest) weight.

| Score | Threshold |
|---|---|
| 5/5 | median capex/rev ≤ 2% (asset-light) |
| 4/5 | ≤ 5% |
| 3/5 | ≤ 10% |
| 2/5 | ≤ 15% |
| 1/5 | otherwise |

**Field:** `derived_CapEx / clean_Revenue` per year, then median.

## Cyclical handling

Cyclical tickers (lifecycle == `cyclical_industrial` OR sector ∈ {Energy, Materials, Industrials}) get sector-relative scoring for **Component 2 (Gross Margin Stability)** because their GM CV is inherently elevated by demand cycles, not lack of moat.

**Algorithm:**
1. Identify the sector peer set within UNIVERSE (ticker.sector matches OR ticker.lifecycle == "cyclical_industrial")
2. If peer_set_size ≥ 3:
   - Compute peer median GM CV
   - Score relative to median: ≤ 0.7 × peer_median → 5/5; ≤ 0.85 × peer_median → 4/5; etc.
3. **If peer_set_size < 3 (FALLBACK):**
   - Fall back to absolute thresholds from Component 2 table above
   - Log a `[WARN] cyclical peer set too small (n=<size>), using absolute GM thresholds for {ticker}` to stderr
   - Set `score_metadata.fallback_used = "absolute_gm_thresholds"`

**Future enhancement (deferred):** when external sector benchmarks (BLS productivity indices, S&P sector ETF holdings) are integrated, the fallback can use those instead of absolute thresholds. For now the absolute fallback is the documented behavior.

## Known simplifications (to be addressed in future phases)

These are explicit, documented limitations of v1.0. The methodology is intentionally simple to be auditable; refinements get versioned methodology docs.

### Simplification 1 — Equal-year weighting

ROIC persistence treats every year in the window equally. A company with 8% ROIC in 8 of 10 years scores the same regardless of whether the 2 misses are 2009-2010 (post-GFC, recoverable) or 2023-2024 (recent, possibly structural).

**Why this is acceptable for v1.0:**
- The 5-year minimum window prevents single-year noise from dominating
- Equal weighting is intuitive and easy to explain
- The alternative (exponential time decay or recency weighting) introduces additional methodology choices (decay rate, lookback)

**Phase-2 refinement option:** weight recent years higher via exponential decay (e.g. `weight_year = 0.85^years_ago`) or a 5Y-only sub-score blended with the full-window score. Either approach produces a different fingerprint and would need its own methodology MD.

### Simplification 2 — Capex intensity is a moat *signal*, not a *measure*

Low capex/revenue is a signal of asset-light moat dynamics. It does NOT differentiate between:

- **AAPL** — low capex because of supply chain leverage and contract manufacturing (genuine asset-light moat from scale)
- **A QSR franchise** — low capex because the franchisor doesn't own restaurants (different kind of asset shifting)
- **A holding company** — low capex because operating subsidiaries hold the assets (artifact of structure, not moat)

For the current 25-ticker universe (no QSRs, no holding companies aside from BRK-B which is bypassed), this distinction doesn't materially affect any individual fingerprint. We weight capex intensity at 20% — the lowest of the three components — because of this ambiguity. Future universe additions in QSR / franchise space may require disambiguation logic.

### Simplification 3 — Sector peer set may be too small

The current universe has only 2-3 cyclical names (CAT, AMD, TXN). 2-3 data points is not a proper peer set for computing within-sector CV medians. We document the explicit fallback (absolute GM thresholds with logged warning) so this is auditable rather than silent.

**Phase-2 refinement option:** integrate external benchmarks — S&P sector ETF holdings, BLS productivity series — as the cyclical reference. This requires data sourcing decisions (vendor, refresh cadence, USD vs local) and would warrant its own methodology document.

## Output schema

```python
@dataclass(frozen=True)
class MoatFingerprint:
    score: Optional[int]                       # 1-5, or None if insufficient history
    roic_persistence_score: Optional[int]      # 1-5
    gm_stability_score: Optional[int]          # 1-5
    capex_intensity_score: Optional[int]       # 1-5
    window_years: int                          # actual years used (could be < cap)
    is_null_due_to_history: bool
    fallback_used: Optional[str]               # e.g. "absolute_gm_thresholds"
    rationale: str                             # human-readable explanation
```

Example output for AAPL (FY2009-FY2025 window, 17 years → capped at 10):
```
score = 5
roic_persistence_score = 5  (ROIC ≥ 8% in 10/10 years, median 35%)
gm_stability_score = 4      (CV = 0.07)
capex_intensity_score = 4   (median capex/rev = 4.1%)
window_years = 10
is_null_due_to_history = False
fallback_used = None
rationale = "10/10 years ROIC≥8% (median 35%); GM CV=0.07; capex intensity 4.1%."
```

## Determinism guarantee

This methodology produces byte-identical output for the same input data. There are no LLM calls, no random sampling, no time-dependent thresholds. The same `(ticker, fiscal_year)` will always produce the same fingerprint.

This is the load-bearing property that lets ConvictionScorer P1 stay deterministic across runs (the determinism gate check in B.5).

## Versioning

Changes to thresholds, weights, window, or fallback rules require:
1. New methodology MD version (this file, header bumped)
2. Updated component scores documented with a migration note
3. Architecture lock test re-run to verify no calc-layer drift
4. Determinism gate re-run on AAPL

The methodology document is the source of truth — code follows the document.
