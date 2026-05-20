# Contrarian A/B — Web Search Value Test

Each ticker run twice with identical upstream state; only the contrarian DuckDuckGo web-search query differs. Comparison axes:

| Axis | Δ ≥ 10% triggers "keep search" |
|---|---|
| Bear-case length | n_chars vs w_chars |
| Bias-category    | match required for "drop search" |
| Sentiment score  | match required for "drop search" |
| Proper nouns only-with-search | named entities only the search produced |
| Recency markers  | dates / events that imply live data |

## Per-ticker

| Ticker | w_chars | n_chars | Δ% | bias= | sentΔ | sharedPN | wOnly | nOnly | wRec | nRec |
|---|---|---|---|---|---|---|---|---|---|---|
| AAPL | 1079 | 1172 | +8.6% | ✓ | +0 | 12 | 1 | 4 | 0 | 0 |
| BRK-B | 982 | 1099 | +11.9% | ✓ | +0 | 7 | 2 | 5 | 0 | 0 |
| CNC | 952 | 982 | +3.2% | ✗ | +0 | 12 | 4 | 2 | 0 | 0 |
| COST | 1209 | 1012 | -16.3% | ✓ | +0 | 8 | 6 | 1 | 0 | 0 |
| JPM | 1036 | 811 | -21.7% | ✗ | +0 | 7 | 6 | 4 | 0 | 0 |
| META | 1263 | 1203 | -4.8% | ✓ | +0 | 11 | 4 | 1 | 0 | 0 |
| MSFT | 1220 | 1048 | -14.1% | ✗ | +0 | 6 | 8 | 5 | 0 | 0 |
| NVDA | 1010 | 1120 | +10.9% | ✓ | -2 | 12 | 4 | 1 | 0 | 0 |
| TSLA | 1405 | 1334 | -5.1% | ✓ | +0 | 15 | 1 | 4 | 0 | 0 |

## Aggregate

- Bias-category match: **6/9**
- Sentiment-score equal: **8/9**
- Avg |Δ bear-case chars|: **10.7%**
- Max |Δ bear-case chars|: **21.7%**
- Avg unique proper-nouns surfaced ONLY by the search: **4.0**
- Avg recency-marker count (with): **0.0** vs (without): **0.0**

## Per-ticker proper-noun deltas (samples)

### AAPL
  - Only-with-search proper nouns: ['the reverse dcf']
  - Only-without-search proper nouns: ['dcf', 'fomo', 'this', 'ultimately']

### BRK-B
  - Only-with-search proper nouns: ['compounding', 'concurrently']
  - Only-without-search proper nouns: ['brk', 'furthermore', 'narrative fallacy', 'the', 'without']

### CNC
  - Only-with-search proper nouns: ['aca', 'first', 'growth extrapolation bias', 'second']
  - Only-without-search proper nouns: ['furthermore', 'medicaid']

### COST
  - Only-with-search proper nouns: ['cost', 'dcf', 'growth extrapolation bias', 'liberti multiple decomposition', 'this']
  - Only-without-search proper nouns: ['costco']

### JPM
  - Only-with-search proper nouns: ['basel iii', 'compounding', 'equity', 'nim', 'return']
  - Only-without-search proper nouns: ['furthermore', 'nims', 'normalizing', 'without']

### META
  - Only-with-search proper nouns: ['additionally', 'growth extrapolation bias', 'metaverse', 'roic']
  - Only-without-search proper nouns: ['this']

### MSFT
  - Only-with-search proper nouns: ['dcf', 'furthermore', 'intrinsic price', 'microsoft', 'share']
  - Only-without-search proper nouns: ['even', 'intrinsic per share', 'more', 'msft', 'the liberti formula']

### NVDA
  - Only-with-search proper nouns: ['asic', 'even', 'nvidia', 'this']
  - Only-without-search proper nouns: ['nvda']

### TSLA
  - Only-with-search proper nouns: ['liberti formula']
  - Only-without-search proper nouns: ['agent', 'dcf', 'liberti multiple decomposition', 'ultimately']
