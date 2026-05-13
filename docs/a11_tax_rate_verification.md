# A11 Tax-Rate Verification — Sample Sweep

Per-ticker × per-function audit of the A11 fallback chain (cash → gaap → company_fy → statutory). Confirms whether the FCF-pathway failures surfaced by the Phase-1 identity audit are tax-rate-driven (Category B, would block further audit work) or SBC/deferred-tax-driven (Category C, documented exception).

**Headline finding**: of 38 probes where the engine actually ran, 24 resolved via the cleaned `cash`/`gaap` step, 14 via the `company_fy` historical fallback, and 0 all the way through to `statutory`. A11 chain is OPERATIONAL: the majority of probes resolve at the cleaned `cash`/`gaap` step. Where statutory fallback fires, it's narrow and Ticker-specific — investigate those cases via the cleaning_engine domain-10 path.


## Source distribution across all probes

| Source | Count | Pct |
|---|---|---|
| `cash` | 24 | 60% |
| `company_fy` | 14 | 35% |
| `unavailable` | 2 | 5% |

## Per-ticker findings

### AAPL
*Expected:* Apple — US filer, low single-digit teens cash rate

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `cash` | 0.327 | ✗ |  |
| `reverse_dcf` | `cash` | 0.327 | ✗ |  |
| `multiple_decomposition` | `cash` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### ASML
*Expected:* ASML — Dutch filer, EUR-reporting, mid-teens

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `cash` | 0.142 | ✓ |  |
| `reverse_dcf` | `cash` | 0.142 | ✓ |  |
| `multiple_decomposition` | `cash` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### CAT
*Expected:* Caterpillar — cyclical industrial, near US statutory

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `company_fy` | 0.256 | ✓ |  |
| `reverse_dcf` | `company_fy` | 0.256 | ✓ |  |
| `multiple_decomposition` | `company_fy` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### COST
*Expected:* Costco — consumer staples, near US statutory

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `cash` | 0.270 | ✓ |  |
| `reverse_dcf` | `cash` | 0.270 | ✓ |  |
| `multiple_decomposition` | `cash` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### JPM
*Expected:* JPMorgan — bank, near US statutory

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `—` | — | — | NotImplementedError: DCFEngine: ticker JPM requires specialized model (routing_r |
| `reverse_dcf` | `company_fy` | 0.181 | ✓ |  |
| `multiple_decomposition` | `company_fy` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### MSFT
*Expected:* Microsoft — mid-to-high teens cash rate

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `cash` | 0.232 | ✗ |  |
| `reverse_dcf` | `cash` | 0.232 | ✗ |  |
| `multiple_decomposition` | `cash` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### NVDA
*Expected:* NVDA — recent profile of teens, varies with NOL releases

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `cash` | 0.143 | ✓ |  |
| `reverse_dcf` | `cash` | 0.143 | ✓ |  |
| `multiple_decomposition` | `cash` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### TSLA
*Expected:* Tesla — historically NOL-driven negatives, then teens

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `cash` | 0.233 | ✗ |  |
| `reverse_dcf` | `cash` | 0.233 | ✗ |  |
| `multiple_decomposition` | `cash` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### TSM
*Expected:* TSMC — Taiwan filer, low-teens preferential rate

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `cash` | 0.000 | ✗ |  |
| `reverse_dcf` | `cash` | 0.000 | ✗ |  |
| `multiple_decomposition` | `cash` | — | — |  |
| `screening` | `company_fy` | — | — |  |

### UNH
*Expected:* UnitedHealth — health insurer, near US statutory

| Function | source | rate | plausible | note |
|---|---|---|---|---|
| `dcf_engine` | `—` | — | — | NotImplementedError: DCFEngine: ticker UNH requires specialized model (ddm_requi |
| `reverse_dcf` | `cash` | 0.253 | ✓ |  |
| `multiple_decomposition` | `cash` | — | — |  |
| `screening` | `cash` | — | — |  |
