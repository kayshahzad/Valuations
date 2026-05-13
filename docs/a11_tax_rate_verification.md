# A11 Tax-Rate Verification — Sample Sweep

Per-ticker × per-function audit of the A11 fallback chain (cash → gaap → company_fy → statutory). Confirms whether the FCF-pathway failures surfaced by the Phase-1 identity audit are tax-rate-driven (Category B, would block further audit work) or SBC/deferred-tax-driven (Category C, documented exception).

**Headline finding**: of 38 probes where the engine actually ran, 0 resolved via the cleaned `cash`/`gaap` step, 0 via the `company_fy` historical fallback, and 38 all the way through to `statutory`. A11 chain IS OPERATIONAL but the cleaning engine is failing to populate cleaned tax rates for most tickers. The resolver correctly falls through to statutory. ROOT-CAUSE INVESTIGATION needed at the cleaning_engine domain-10 layer (TaxSustainability) — the chain works; the upstream feeder doesn't.


## Source distribution across all probes

| Source | Count | Pct |
|---|---|---|
| `statutory` | 38 | 95% |
| `unavailable` | 2 | 5% |

## Per-ticker findings

### AAPL
*Expected:* Apple — US filer, low single-digit teens cash rate

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `statutory` | 0.210 | ✗ |  |
| `reverse_dcf` | `statutory` | 0.210 | ✗ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### ASML
*Expected:* ASML — Dutch filer, EUR-reporting, mid-teens

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `statutory` | 0.210 | ✗ |  |
| `reverse_dcf` | `statutory` | 0.210 | ✗ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### CAT
*Expected:* Caterpillar — cyclical industrial, near US statutory

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `statutory` | 0.210 | ✓ |  |
| `reverse_dcf` | `statutory` | 0.210 | ✓ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### COST
*Expected:* Costco — consumer staples, near US statutory

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `statutory` | 0.210 | ✗ |  |
| `reverse_dcf` | `statutory` | 0.210 | ✗ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### JPM
*Expected:* JPMorgan — bank, near US statutory

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `—` | — | — | NotImplementedError: DCFEngine: ticker JPM requires specialized model (routing_r |
| `reverse_dcf` | `statutory` | 0.210 | ✓ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### MSFT
*Expected:* Microsoft — mid-to-high teens cash rate

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `statutory` | 0.210 | ✗ |  |
| `reverse_dcf` | `statutory` | 0.210 | ✗ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### NVDA
*Expected:* NVDA — recent profile of teens, varies with NOL releases

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `statutory` | 0.210 | ✗ |  |
| `reverse_dcf` | `statutory` | 0.210 | ✗ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### TSLA
*Expected:* Tesla — historically NOL-driven negatives, then teens

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `statutory` | 0.210 | ✗ |  |
| `reverse_dcf` | `statutory` | 0.210 | ✗ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### TSM
*Expected:* TSMC — Taiwan filer, low-teens preferential rate

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `statutory` | 0.210 | ✗ |  |
| `reverse_dcf` | `statutory` | 0.210 | ✗ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |

### UNH
*Expected:* UnitedHealth — health insurer, near US statutory

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `—` | — | — | NotImplementedError: DCFEngine: ticker UNH requires specialized model (ddm_requi |
| `reverse_dcf` | `statutory` | 0.210 | ✓ |  |
| `multiple_decomposition` | `statutory` | — | — |  |
| `screening` | `statutory` | — | — |  |
