# Qualitative Synthesis — Quality Gate Comparison

Side-by-side analytical-quality comparison: 3-agent baseline (saved reports) vs new consolidated `qualitative_synthesis`.

**Verdict scoring per field**: `✓` = new output has equal-or-greater named-entity / percentage citation density; `≈` = minor reduction (within 2 fewer citations); `✗` = material reduction (3+ fewer).

**Per-ticker verdict**: PASS (zero fails, ≤2 concerns), REVIEW (1 fail or 3+ concerns), FAIL (2+ fails).

---

## Summary

| Ticker | Verdict | Moat (base→new) | Scenarios (base→new) | Field passes | Concerns | Fails |
|---|---|---|---|---|---|---|
| AAPL | **PASS** | 9.0 → 9.5 (+0.5) | 2 → 2 | 5 | 0 | 0 |
| BRK-B | **PASS** | 9.0 → 9.0 (+0.0) | 0 → 0 | 3 | 2 | 0 |
| CNC | **PASS** | 6.5 → 6.0 (-0.5) | 0 → 2 | 3 | 2 | 0 |
| COST | **PASS** | 8.5 → 9.0 (+0.5) | 0 → 1 | 5 | 0 | 0 |
| JPM | **PASS** | 8.0 → 9.0 (+1.0) | 0 → 2 | 5 | 0 | 0 |
| META | **REVIEW** | 8.5 → 9.0 (+0.5) | 0 → 2 | 3 | 1 | 1 |
| MSFT | **PASS** | 9.5 → 9.0 (-0.5) | 0 → 2 | 5 | 0 | 0 |
| NVDA | **PASS** | 9.5 → 9.5 (+0.0) | 5 → 2 | 5 | 0 | 0 |
| TSLA | **PASS** | 7.5 → 7.0 (-0.5) | 0 → 2 | 5 | 0 | 0 |

## AAPL — PASS

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 4 | 8 | +4 | 1 | 2 | +1 | ✓ |
| business_description | 2 | 8 | +6 | 0 | 0 | +0 | ✓ |
| bottleneck_analysis | 5 | 13 | +8 | 1 | 0 | -1 | ✓ |
| pricing_power_assessment | 5 | 10 | +5 | 2 | 2 | +0 | ✓ |
| context_summary | 3 | 4 | +1 | 1 | 2 | +1 | ✓ |

## BRK-B — PASS

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 4 | 7 | +3 | 0 | 0 | +0 | ✓ |
| business_description | 3 | 2 | -1 | 0 | 0 | +0 | ≈ |
| bottleneck_analysis | 7 | 7 | +0 | 0 | 0 | +0 | ✓ |
| pricing_power_assessment | 5 | 7 | +2 | 0 | 0 | +0 | ✓ |
| context_summary | 6 | 5 | -1 | 0 | 1 | +1 | ≈ |

## CNC — PASS

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 11 | 14 | +3 | 0 | 0 | +0 | ✓ |
| business_description | 5 | 8 | +3 | 0 | 0 | +0 | ✓ |
| bottleneck_analysis | 7 | 9 | +2 | 2 | 0 | -2 | ✓ |
| pricing_power_assessment | 6 | 4 | -2 | 0 | 0 | +0 | ≈ |
| context_summary | 6 | 5 | -1 | 2 | 1 | -1 | ≈ |

## COST — PASS

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 7 | 8 | +1 | 1 | 4 | +3 | ✓ |
| business_description | 3 | 3 | +0 | 0 | 0 | +0 | ✓ |
| bottleneck_analysis | 7 | 13 | +6 | 0 | 1 | +1 | ✓ |
| pricing_power_assessment | 1 | 4 | +3 | 3 | 3 | +0 | ✓ |
| context_summary | 3 | 5 | +2 | 1 | 3 | +2 | ✓ |

## JPM — PASS

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 6 | 14 | +8 | 0 | 2 | +2 | ✓ |
| business_description | 5 | 8 | +3 | 0 | 0 | +0 | ✓ |
| bottleneck_analysis | 4 | 8 | +4 | 0 | 0 | +0 | ✓ |
| pricing_power_assessment | 1 | 5 | +4 | 0 | 2 | +2 | ✓ |
| context_summary | 3 | 3 | +0 | 0 | 1 | +1 | ✓ |

## META — REVIEW

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 10 | 12 | +2 | 0 | 2 | +2 | ✓ |
| business_description | 8 | 10 | +2 | 0 | 0 | +0 | ✓ |
| bottleneck_analysis | 14 | 13 | -1 | 0 | 1 | +1 | ≈ |
| pricing_power_assessment | 10 | 6 | -4 | 0 | 1 | +1 | ✗ |
| context_summary | 3 | 5 | +2 | 1 | 1 | +0 | ✓ |

## MSFT — PASS

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 9 | 17 | +8 | 0 | 1 | +1 | ✓ |
| business_description | 1 | 5 | +4 | 0 | 0 | +0 | ✓ |
| bottleneck_analysis | 10 | 14 | +4 | 0 | 0 | +0 | ✓ |
| pricing_power_assessment | 9 | 9 | +0 | 0 | 1 | +1 | ✓ |
| context_summary | 3 | 4 | +1 | 1 | 2 | +1 | ✓ |

## NVDA — PASS

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 12 | 12 | +0 | 1 | 2 | +1 | ✓ |
| business_description | 5 | 8 | +3 | 0 | 0 | +0 | ✓ |
| bottleneck_analysis | 7 | 16 | +9 | 0 | 0 | +0 | ✓ |
| pricing_power_assessment | 3 | 8 | +5 | 1 | 2 | +1 | ✓ |
| context_summary | 3 | 3 | +0 | 0 | 1 | +1 | ✓ |

## TSLA — PASS

| Field | Base proper-nouns | New proper-nouns | Δ | Base % | New % | Δ | Verdict |
|---|---|---|---|---|---|---|---|
| moat_evidence | 5 | 12 | +7 | 0 | 3 | +3 | ✓ |
| business_description | 4 | 7 | +3 | 0 | 0 | +0 | ✓ |
| bottleneck_analysis | 8 | 10 | +2 | 0 | 0 | +0 | ✓ |
| pricing_power_assessment | 5 | 6 | +1 | 0 | 2 | +2 | ✓ |
| context_summary | 4 | 4 | +0 | 1 | 2 | +1 | ✓ |
