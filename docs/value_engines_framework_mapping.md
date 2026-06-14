# Aletheia ↔ Liberti Four-Engine Framework — Mapping & Roadmap

*How the platform's signals map to the four sources of equity value (Operating / Financial / Governance / Multiples), what we just shipped to operationalize it, and where to push next.*

Date: 2026-06-13 · Reference: Liberti FIN-447 "Foundational Frameworks to Unlock Value" (slides 36–54)

---

## 0. Executive summary

The four-engine framework is not a new feature request — it is the **organizing lens** for signals Aletheia already computes. Until this week those signals were scattered across memo Section 4 with no single verdict. The **Value Source Decomposition (VSD) layer** (shipped this session) rolls them into a 100%-stacked attribution — Operating / Financial / Multiple share + a Governance modifier — and, critically, makes that attribution **bind the conviction tier**.

The headline: the one discipline the framework says we lacked — *"multiple expansion not backed by operating cash flow is a transfer, not creation"* (slide 36 banner) — **is now enforced.** A thesis whose return leans on re-rating is capped at PASS regardless of headline margin-of-safety. That is the single most important behavioral change for a Buffett-style long-term holder, because only the operating engine compounds.

---

## 1. Engine-by-engine mapping

| Engine (Liberti) | Balance-sheet side | Aletheia signals (where computed) | Now rolled into | Durability |
|---|---|---|---|---|
| **Operating** | LHS, above EBIT (slide 37) | organic-vs-M&A CAGR (`build_growth_decomposition`), incremental margin (`operating_leverage`), share gain/loss (`share_gain_pp`), segment economics, TAM band — all in [business_analysis.py](aletheia/tools/business_analysis.py) | `operating_share` = `organic_cagr × clamp(incremental/current margin)` | **High — compounds** |
| **Financial** | RHS, EBIT→NI (slides 39, 43) | WACC build (`build_wacc_analysis`), net-debt/EBITDA, SBC, dividend + buyback yield, **debt-vs-FCF buyback funding** (new `build_buyback_funding`) | `financial_share` = dividend + net-buyback yield (AfterSBC) | Medium — bounded, often a transfer across time |
| **Governance** | RHS / structural (slides 38, 40) | Leadership pillar (P5), SBC discipline, value-destructive-M&A detection | `gov_modifier ∈ {−1,0,+1}` (debt-funding primary; M&A∧ROIC<WACC secondary) | Optionality — modifier, not a bucket |
| **Multiples** | Market (slides 39, 53–54) | reverse-DCF "what's priced in", cyclicality z-score, premium-to-justified (`MultipleDecomposition`), **own 5Y-avg multiple** (new `historical_multiples`) | `multiple_share` = `\|min-signed re-rating\|` vs justified (β-band) + historical | **Low — ephemeral, mean-reverting** |

Supporting fix shipped: a **β-reference diagnostic** (`get_beta_diagnostics`, `wacc_at_beta`) — R² + sector-β alongside the ^GSPC β — because the discount rate feeding both the Financial and Multiple engines is meaningless if β is measured against the wrong index (the Nokia/JSE lesson, slides 17–19). Headline WACC is unchanged; the layer *flags* a mis-referenced β.

---

## 2. The discipline now enforced: creation vs. transfer

Slide 36: *"Value creation (if any!) should have an impact on cash-flow generation."* The VSD conviction gate (spec §4) operationalizes this:

| Operating share | Multiple share | → Conviction ceiling |
|---|---|---|
| ≥ 60% | ≤ 25% | **CONVICTION eligible** |
| 40–60% | 25–40% | **MONITOR max** |
| < 40% | > 40% | **PASS** — return depends on re-rating |
| — | — | gov −1 → **downgrade one tier** |

Plus a **value-transfer override**: debt-funded buybacks (slide IV) or a narrative-driven multiple (EQIX's AI premium) cap the tier even when the MoS looks fat. Caps are *additive and most-restrictive* (`final = min(all ceilings)`) — the gate only ever lowers a tier, never raises it, so the existing calibrated universe doesn't re-tier.

**Calibration poles (live):**
- **ADBE** → Operating ~65% / Financial ~30% / Multiple ~4%, gov +1 → *"CONVICTION eligible — durable operating-led return."* ~96% of the return is operating-and-financial creation; only ~4% rides re-rating, and that from an undervalued base.
- **EQIX** → Operating ~48% / Financial ~8% / Multiple ~44% (P/AFFO 27.6× vs a growth-normalized 21.9×) → *"Return depends on re-rating — PASS / watch only."* The +1.6% MoS no longer reads "fair," because a third of the expected return rides a narrative multiple our own contrarian layer flagged as FOMO. This is exactly the REIT financial-engineering artifact the user named — surfaced honestly instead of mistaken for operating decay.

---

## 3. Where the framework exposes remaining gaps

The mapping is clarifying precisely because it shows what's still thin:

1. **Governance is the weakest engine** (the user's ABT "neutral — SBC unavailable" observation). The `gov_modifier` currently fires off buyback funding + a coarse M&A∧ROIC<WACC test. It does *not* yet read board quality, activist presence, insider alignment, or capital-allocation track record. The CEO-transition-tracking backlog item lives here.
2. **Operating share is consolidated-entity only.** For conglomerates / post-M&A names (ABT post-EXAS), blending segments into one terminal margin understates operating durability. The deferred **TSOP (value-by-parts)** engine would value segments separately and feed clean per-segment inputs into the decomposition.
3. **Multiple engine for REITs uses a growth-normalized perpetuity reference**, not an own-history P/AFFO series (we don't carry one). It's defensible but coarse.
4. **FCF-based metrics remain misleading for capex-heavy REITs** — the layer routes REITs to AFFO, but other surfaces (FCF CAGR, FCF yield) still compute GAAP FCF that's structurally negative for EQIX.

---

## 4. Suggestions — how this helps us analyze equities better

**Tier 1 — already live; lean on it:**
- **Lead every memo with the durability verdict, not the MoS.** The one-sentence read ("X% of return is durable operating creation; Y% rides re-rating") is the buy-side framing. It's now in memo §6 and the deep-dive panel.
- **Use the conviction cap as the tie-breaker** when scorecard and prose disagree — the tier is now a *principled function of return durability*, which kills the scorecard-vs-prose reconciliation bugs.

**Tier 2 — build next (highest leverage):**
- **Governance engine, properly.** Wire a capital-allocation track-record feed (incremental ROIC on retained earnings, buyback timing vs. own valuation, M&A value creation) so `gov_modifier` stops defaulting to 0. This is the engine with the most upside because it's currently the most hollow.
- **TSOP / value-by-parts** for conglomerates and post-M&A names, feeding per-segment operating shares into the decomposition.

**Tier 3 — sharpen:**
- **Make the β-reference flag actionable** — when sector-β R² materially beats ^GSPC, surface a "discount rate may understate multiple fragility" warning on the IV, not just in §7.
- **Cross-engine consistency check** — assert that a CONVICTION-tier name has operating_share ≥ 60% in the decomposition; flag any tier that contradicts its own attribution as a coherence bug.

---

## 5. Bottom line

The framework's value to us is *diagnostic discipline*: it forces every thesis to declare which engine is doing the work, and it refuses to let a re-rating bet masquerade as compounding. We now compute that declaration and let it bind conviction. The next dollar of analytical return is in the **Governance engine** (today the hollowest) and **TSOP** (so the Operating engine reads cleanly for multi-segment names) — both of which the four-engine lens makes obvious.
