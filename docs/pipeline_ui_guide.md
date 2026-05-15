# Pipeline UI Guide

A user-facing guide for the two pipeline views: **Stage Explorer**
(per-ticker depth) and **Status Matrix** (universe breadth). Reads
as a tour of what each panel shows and how to interpret the visual
semantics.

---

## Two views, two purposes

### ⚙ Pipeline Explorer — per-ticker depth

The Stage Explorer drills into one ticker at a time. Four panels
correspond to the four pipeline stages:

| Stage | What it shows |
|---|---|
| **Stage 1 — Ingest** | Raw payloads fetched (SEC companyfacts JSON, FMP statements). Per-source bundle fingerprints; payload sizes. |
| **Stage 2 — Validate** | Cleaned record counts, quality scores, schema violations, OVERRIDES applied. The 3-column XBRL ↔ Cleaned ↔ FMP diagnostic surfaces drift per field per FY. |
| **Stage 3 — Calculate** | DCF, RDCF, MultDec, Screening, Moat, Cyclicality outputs. The "All Stage 3 calcs vs FMP" panel lists every derived value with drift %. Identity-audit panel shows pass / expected-exception / failed counts. Methodology expander documents derivation chains per Layer 2. |
| **Stage 4 — Agents** | LLM-derived qualitative analysis. Requires confirmation (incurs API cost). |

### ⚙ Pipeline Status — universe breadth

The Status Matrix shows all 40 universe tickers in one row each, with:
- 4 stage badges per ticker (🟢 success / 🟡 running / 🔴 failed / 🟠 stale / ⚪ pending)
- Identity-audit summary (when Stage 3 bundle is in session)
- Last-run relative timestamp
- Navigate button (`→ TICKER`) that opens Stage Explorer for that ticker

Use this view to answer "which tickers need attention?" without
drilling into each individually.

---

## Visual semantics — chips and badges

The UI uses a small vocabulary of chips. Understanding what each
means is the fastest way to interpret a screen.

### Stage status chips

| Chip | Meaning |
|---|---|
| 🟢 | Stage ran successfully; bundle present |
| 🟡 | Stage currently running |
| 🔴 | Stage failed — investigate error |
| 🟠 | Stage stale — upstream changed, needs re-run |
| ⚪ | Stage pending — never run |
| ⬜ | Stage status unknown (no row in pipeline_status table) |

### Stage 2 — schema-contract chips (Layer 1)

| Chip | Meaning |
|---|---|
| 🚫 **Tier-C** | Critical violation. Stage 3 will REFUSE to run. Add an OVERRIDES waiver to proceed. |
| ⚠️ Tier-W | Identity drift (EBITDA, net_debt, FCF). Surfaces in Stage 3 audit; doesn't block. |

**Tier-C blockers** are truly invalid states — A ≠ L + E, or missing
a Tier-1 required field (Revenue, TotalAssets, SharesDiluted).
Computing on them produces meaningless math, so Stage 3 refuses.

### Stage 2 — 3-column FMP comparison chips

The XBRL ↔ FMP comparison table shows three values per cell:
**Raw XBRL · Cleaned · FMP**. Drift tier:

| Tier | Drift |
|---|---|
| 🟢 ok | ≤ 0.5% |
| 🟡 minor | 0.5-2% |
| 🟠 notable | 2-5% |
| 🔴 material | > 5% |
| ⚫ incomplete | one side missing |

A cleaning-effect chip indicates whether cleaning materially changed
the raw value:
- `~` adjusted — cleaning modified the value
- `⤴︎` cleaned-only — cleaning derived it (no raw input)
- blank passthrough — raw flowed through unchanged

### Stage 3 — calculation chips (Layer 2)

| Chip | Meaning |
|---|---|
| 📐 | Category-D — expected methodology divergence from FMP (not a bug). Click the methodology expander for details. |

Category-D rows have ≥5% drift vs FMP because we and FMP use
different methodologies for the same concept (e.g., FCF, ROIC, P/E
definitions diverge). Each has a documented `fmp_equivalent` note
explaining the divergence.

### Stage 3 — identity-audit chips (Layer 1+3)

| Chip | Meaning |
|---|---|
| ✓ Passed | Identity holds within tolerance |
| ⚠️ Expected exception | Failure with documented Category-C category (hyperscaler CIP, ASC 842 transition, M&A WC distortion, etc.) |
| ❌ Failed | Unflagged failure — investigate. **Target state: this column is empty.** |

Universe-wide today: 0 unflagged failures across 40 tickers. Every
remaining non-passing check carries a documented exception category.

---

## Common workflows

### "Why is FCF so different from FMP?"

1. Land on **Stage Explorer** for the ticker
2. Run Stage 3 (or refresh the cached bundle)
3. Scroll to **"All Stage 3 calculations vs FMP"**
4. Find the FCF row — note the 📐 chip and drift %
5. Open the **"📖 How each value is derived"** expander
6. Read the FCF entry — `fmp_equivalent` note explains the methodology divergence

The 📐 chip tells you upfront that this is Category-D (expected
methodology divergence, not a bug). The expander tells you which
formulas each side uses and why they differ.

### "Why won't Stage 3 run for ticker X?"

1. Stage Explorer → Stage 2 panel
2. Look at the 5-metric strip — if **🚫 Tier-C** is non-zero, that's why
3. Expander shows the specific blocking violation (field + message)
4. Two options:
   - **Add an OVERRIDES waiver** if it's a documented edge case
     (foreign filer, historical year with known accounting issue)
   - **Investigate the cleaning gap** if the violation is unexpected
5. To add a waiver: edit `aletheia/calculations/_overrides.py`, add an
   entry under the ticker with `fields=["accounting_equation_a_eq_l_plus_e"]`
   (or the specific blocking field)

The waiver MUST carry a `reason`, `created_date`, and
`review_by_date` — the registry refuses to load entries without these.

### "Which universe tickers need attention?"

1. **Pipeline Status** view (sidebar)
2. Scan the 4-metric strip at the top:
   - **🟡 Running stages** — wait or interrupt
   - **🔴 Failed stages** — needs investigation
   - **🟠 Stale stages** — needs re-run
3. Filter by sector or lifecycle to narrow scope
4. Click `→ TICKER` to jump to that ticker's Stage Explorer

### "Are the seven accounting identities passing for this ticker?"

1. Stage Explorer → Stage 3 panel
2. **Identity audit** strip shows: total / passed / expected exceptions / failed / skipped
3. If `failed > 0`, an "❌ Unflagged failures" expander appears (expanded)
   - These are genuine diagnostic gaps to investigate
4. If `failed == 0`, all non-passing checks are expected exceptions
   - Click "⚠️ Expected exceptions" expander to see them grouped by category
   - Each category has documented structural reason (see Phase 3 doc)

---

## How the 3-layer accounting model maps to the UI

The underlying conceptual model (Stage 3 results encode three layers
of accounting relationships):

| Layer | What | Where in UI |
|---|---|---|
| **L1 Structural identities** | Laws of accounting (A=L+E, cash roll-forward). Hard assertions. | Stage 2 "🚫 Tier-C" metric. Stage 3 identity-audit panel. |
| **L2 Derivational relationships** | Methodology-bearing formulas (FCF, ROIC, NOPAT). Documented methodology choices. | Stage 3 "All calcs vs FMP" table 📐 chip + "📖 How derived" expander. |
| **L3 Period-over-period flows** | Roll-forwards (PP&E, RE, debt) linking BS items through IS+CF activity. | Stage 3 identity-audit panel (the 6 roll-forward checks). |

When an analyst sees a Stage 3 panel with green identity audit + 📐
chips on a few drift rows + no Tier-C banner, they know:
- Layer 1: balance sheet balances, required fields present
- Layer 2: drifts vs FMP are documented methodology choices
- Layer 3: roll-forwards reconcile to within tolerance

That's the trust signal the whole pipeline architecture is designed
to deliver in one panel.

---

## Adding an OVERRIDES waiver

When Stage 3 blocks on a Tier-C violation, the path forward is to
either (a) fix the underlying data quality issue or (b) waive it as
a documented edge case.

Waiver template:

```python
# aletheia/calculations/_overrides.py
"NEW_TICKER": {
    "descriptive_override_key": {
        "reason": "Specific rationale — what's wrong with the data, "
                  "why is the violation legitimate, what's the path to a real fix.",
        "created_date":   "YYYY-MM-DD",
        "review_by_date": "YYYY-MM-DD",   # required; long for stable cases, short for follow-ups
        "fields":         ["accounting_equation_a_eq_l_plus_e"],  # specific field to waive
    },
}
```

**Review-date hygiene**: short review dates (3-6 months) when the
waiver is a stopgap for a real fix. Long review dates (12 months) for
genuinely stable edge cases (foreign filers, historical-era taxonomy
quirks). The registry warns at startup when entries pass their review
date — that's the signal to either renew the rationale or remove the
override.

---

## Pipeline state and persistence

| State | Lives in |
|---|---|
| Raw payloads | `valuation_data/raw/sec/companyfacts/` + `valuation_data/macro/fmp/` |
| Cleaned records | DuckDB `company_records` table |
| Stage outputs | DuckDB `pipeline_status` table (status badges) |
| Session bundles | Streamlit `st.session_state` (clears on app restart) |
| Identity audit results | Inside Stage 3 bundles → `accounting_identities` field |
| Override registry | `aletheia/calculations/_overrides.py` (source-controlled) |

The Status Matrix reads from `pipeline_status`. Stage Explorer reads
from session bundles (so analyst nav doesn't trigger re-runs). The
identity audit travels inside the Stage 3 bundle.
