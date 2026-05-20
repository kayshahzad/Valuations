# Agent Layer — Empirical Investigation Report

**Scope:** verify what the agent layer is actually doing before refactoring decisions. Findings only — no recommendations beyond evidence-grounded confidence flags.

**Method:** code reading across [aletheia/agents/](aletheia/agents/), DAG analysis at [aletheia/workflow/graph.py](aletheia/workflow/graph.py), consumer mapping via grep across the codebase, sampling 9 representative tickers from `valuation_data/serving/latest/*_report.json`, and selective live-engine comparison against saved values.

**Date:** 2026-05-07.

---

## 1. Agent Functional Summary

### Active in production graph (9 nodes)

| Agent | File | LoC | Calls LLM? | Calls web search? | Failure mode |
|---|---|---|---|---|---|
| `librarian` | `librarian.py` | 46 | No | No (uses `edgartools` SEC API) | Catches all exceptions; returns "SEC Fetch Failed" string in `raw_10k_text` |
| `calc_node` | `calc_node.py` | 366 | No | No | Per-step try/except; appends to `errors[]`; never raises |
| `forensic` | `forensic.py` | 410 | 1× LLM | 1× search (`search_sentiment`, "competitor margins") | Returns empty dict + error message; lead checks for KEY presence, not content |
| `value_chain` | `value_chain.py` | 325 | 1× LLM | 1× search ("supplier margins") | Same — empty-but-present return on failure |
| `context` | `context.py` | 351 | 1× LLM | 1× search ("patent intangibles") | Same |
| `scenario_eval_node` | `scenario_eval_node.py` | 268 | No | No | Per-scenario try/except; produces `error` field per scenario; never crashes pipeline |
| `strategist` | `strategist.py` | 112 | No | No (yfinance for market_cap) | Single try around DB read; `market_cap` fallback to `raw_TotalEquity`; no error handling on `compute_wacc` |
| `contrarian_v2` | `contrarian_v2.py` | 242 | 1× LLM | 1× DuckDuckGo search ("bear case risks") | Try/except around chain.invoke; returns `error` field on failure |
| `lead` | `lead.py` | 487 | 1× LLM (synthesis) | No | **RAISES** ValueError if `forensic_report` or `value_chain_report` keys are missing from state ([lead.py:114-115](aletheia/agents/lead.py#L114-L115)) |

### Dead code (in repo, NOT in production graph)

| Agent | File | LoC | Status |
|---|---|---|---|
| `fundamentalist` | `fundamentalist.py` | 118 | **Removed from graph** ([graph.py:21](aletheia/workflow/graph.py#L21) docstring confirms). Only called by [run_valuation.py](run_valuation.py) (legacy CLI) and `scripts/archive/`. Output `valuation_report` is empty in current production runs (verified: AAPL 2026-05-02 report has `dcf_model.intrinsic_value = None`). |
| `valuation_node` | `valuation_node.py` | 32 | **Deprecated shim** ([valuation_node.py:1](aletheia/agents/valuation_node.py#L1) docstring). Just calls `calc_node`. Same as fundamentalist — only run_valuation.py imports it. |
| `intake` | `intake.py` | 280 | **No-op in production**. The `intake_agent` function returns `{"serving_base": {}, ...}` ([intake.py:280](aletheia/agents/intake.py#L280)). The 270 lines of `EconomicRealityTranslator` / `FinancialEngine` / `FREDLoader` are class definitions never called from the agent. Not in production graph. |
| `contrarian` (v1) | `contrarian.py` | 89 | Superseded by `contrarian_v2`. Not in production graph. |

### LLM call topology per ticker run

5 sequential LLM calls per pipeline (forensic → value_chain → context → contrarian_v2 → lead). Plus 4 web searches (3 in narrative agents + 1 in contrarian).

### Token cost estimation

Prompt sizes (template + injected DB context, excluding 10-K text):

| Agent | Prompt template | + DB context | + 10-K truncation | Approx total input |
|---|---|---|---|---|
| forensic | 3.3K chars | ~2K | 60K (`raw_10k[:60000]`) | ~16K tokens |
| value_chain | 3.0K chars | ~2K | 50K (`raw_10k[:50000]`) | ~14K tokens |
| context | 3.2K chars | ~2K | 50K (`raw_10k[:50000]`) | ~14K tokens |
| contrarian_v2 | 1.6K chars | ~3K (web + scenarios + quant_challenge) | none | ~1.2K tokens |
| lead | 0.8K chars | ~3K (compliance + p2_context + agent narratives) | none | ~1K tokens |

**Per-run estimate:** ~46K input tokens × 5 calls + structured-output overhead. Output sizes are bounded by Pydantic schemas (a few KB each). Using gemini-3.1-pro pricing as a rough order: ~$0.50-1.50 per ticker pass for inputs.

### Latency

No instrumentation captures per-agent latency in the production code. From the 25 saved reports' timestamps and run histories, full pipeline runs complete in O(minutes) — primarily LLM round-trip + 10-K fetch from EDGAR.

---

## 2. Consumer Dependency Map

Compiled via cross-codebase grep on every state field name. Detail below; orphans and export-only flagged.

### State field → consumer matrix (production graph only)

| State field (producer) | Read by agents | Read by API | Read by export pipeline | Read by UI |
|---|---|---|---|---|
| `ticker` (intake stub) | All 8 downstream agents | api_main.py: every endpoint | report_generator | streamlit_app |
| `raw_10k_text` (librarian) | forensic.py:263, value_chain.py:193, context.py:211 | — | — | — |
| `phase2_valuation` (calc_node) | contrarian_v2.py:57, lead.py:124 + 449 lines | api_main.py:879-906 (Section-3 reconstruction) | report_generator.py:46/85/96-145 (extensive) | streamlit_app, deep_dive_view |
| `cyclicality` (calc_node) | context.py:192 (LLM input only) | — | — | — |
| `operating_leverage` (calc_node) | **No consumers** | — | — | — |
| `moat_fingerprint` (calc_node) | **No consumers** | — | — | — |
| `conviction` (calc_node) | lead.py:387 | — | — (passes through final_report) | dashboard |
| `forensic_report` (forensic) | lead.py:119, capital_structure.py:123, conviction_scorer.py:429 | — | report_generator (via final_report) | deep_dive_view (via final_report) |
| `value_chain_report` (value_chain) | lead.py:120, conviction_scorer.py:430 | — | report_generator.py:93 | deep_dive_view |
| `strategic_context_report` (context) | lead.py:121, conviction_scorer.py:431 | — | (via final_report) | (via final_report) |
| `scenario_results` (scenario_eval_node) | contrarian_v2.py:187, lead.py:473 | — | (via final_report's `agent_scenarios`) | deep_dive_view |
| `strategist_report` (strategist) | lead.py:118, conviction_scorer.py:433, fundamentalist.py:59 (dead path) | — | (via final_report) | (via final_report) |
| `valuation_report` (fundamentalist) | lead.py:122 only — but fundamentalist isn't run in production, so this is always `{}` | — | — | — |
| `contrarian_report` (contrarian_v2) | lead.py:123 only | — | (via final_report) | (via final_report) |
| `final_report` (lead) | main.py:54 (CLI), api_main.py JSON-fallback path | report_generator.py (entire export chain) | streamlit_app:535-1380 | — |

### Orphan outputs (produced but not consumed)

1. **`operating_leverage`** — calc_node writes a typed dict with `score`, `gross_margin_pct`, `ebit_margin_pct`. **No reader.** ConvictionScorer reads `op_leverage.get("score")` and `op_leverage.get("ebit_margin_pct")` from inside calc_node before they leave the function. After that, the state key is dead.
2. **`moat_fingerprint`** — calc_node writes the typed `MoatFingerprintResult.to_dict()` to state. **No reader.** ConvictionScorer reads `mf.score` from the local variable inside calc_node, not from state. The state key persists for audit but is never read downstream.
3. **`valuation_report`** — fundamentalist is no longer in the graph, so this key is always `{}`. lead.py still tries to read `dcf_result.equity_value` and `calculated_upside` from it, producing `null` fields in the final report's `dcf_model` block.
4. **`intake.serving_base`** — intake_agent returns this; nothing reads it.

### Export-only (flow into render output but not into analytical state)

- `forensic_report.{moat_attributes.*, business_description, revenue_segments, key_customers, competitive_landscape, regulatory_risk}` — rendered into `1_economic_reality` section of HTML/Markdown; no further analytical use beyond conviction's read of `moat_score`/`operating_leverage_score`.
- `value_chain_report.{bottleneck_analysis, top_substitutes, pricing_power_assessment, analysis_summary}` — rendered into the value-chain card; conviction reads only `strategic_leverage_score`.
- `strategic_context_report.{deferred_revenue_trend, intangible_risk_assessment, summary}` — purely displayed.
- `contrarian_report.{structured_analysis, quant_challenge, raw_results}` — rendered into the contrarian card; no analytical reads.

### Dual-purpose (analytical input + render)

Only `phase2_valuation.*` is genuinely multi-consumer with deep analytical reach: ConvictionScorer pillar 3, lead's compliance checks, contrarian's quant challenge, report generator's 3-scenario DCF tables, API's section-3 capital-stack reconstruction, dashboard metrics. Every other agent output is "read by lead's display assembly + render pipeline."

---

## 3. Redundancy Verification

### Fundamentalist DCF ↔ calc_node phase2_valuation

**Empirical finding: not currently divergent because fundamentalist isn't running.**

Verified by:
1. Reading [graph.py:32-71](aletheia/workflow/graph.py#L32-L71) — `fundamentalist_agent` is not in the production workflow. Only `librarian → calc_node → forensic → value_chain → context → scenario_eval → strategist → contrarian → lead`.
2. Inspecting saved reports across generation dates:

| Ticker | gen_at | phase2 base IV | dcf_model.intrinsic_value (from valuation_report) |
|---|---|---|---|
| AAPL | 2026-05-02 | _null_ (engine bypassed) | **null** ← post-fundamentalist-removal |
| ABT | 2026-04-28 | $52.93 | $67.6B (pre-removal artifact) |
| AMD | 2026-04-28 | $30.63 | $6.75B |
| AMZN | 2026-04-28 | $170.00 | $3.05B |
| ASML | 2026-04-29 | $330.84 | $98.9B |

The `dcf_model.intrinsic_value` field stores **enterprise value** (in $billions), not per-share intrinsic value. AAPL's recent re-run (post-removal) correctly leaves this null. Older reports preserve the legacy field with a confused number.

**Type/scale conflation:** even when fundamentalist ran, its `dcf_model.intrinsic_value` was an EV in absolute dollars, not per-share intrinsic. The downstream comparison to `current_price` produced upside = -98% for ABT, AMZN, ASML — meaningless because of the unit mismatch. This was a latent rendering bug before the agent was removed.

**Migration scope to formalize the removal:** delete the file + remove the lead.py reads (`val.get("dcf_result")`, `val.get("calculated_upside")` at lines 322 and 411). 4 lines of reads in lead.py + 1 dead file. Estimated **0.5 day**.

### Strategist WACC ↔ calc_node WACC

**Empirical finding: divergent by exactly +1.50pp on 8 of 25 tickers (32%) at the time of report capture.**

Across 25 saved reports:

| Pattern | Tickers | Δ |
|---|---|---|
| Match | 17 of 25 (68%) | 0.00pp |
| **+1.50pp gap** | 8 of 25 (32%): AMD, ASML, CNC, LLY, QCOM, SMCI, TSM, UNH | exactly +1.50pp |

Both code paths call the same `compute_wacc()` function with similar inputs but produce divergent outputs for a specific subset. The +1.50pp pattern is exact-equal across all 8 tickers, suggesting a fixed adjustment (not a beta-driven multiplier) is applied in one path but not the other.

Further code archaeology required to pinpoint the source. Candidates examined and ruled out:
- `bear_wacc_adjustment: 0.015` ([interfaces.py:22](aletheia/contracts/interfaces.py#L22)) — applies only to bear scenario, not `wacc_base`
- `compute_wacc()` itself produces identical results for identical inputs
- Internal `wacc_floor = max(0.04, rf+0.01)` — would push UP, but strategist's `config.wacc_floor` of 9% is binding for utilities (CNC/LLY/UNH) and produces equal-or-lower values, not equal-or-higher
- MRP delta (`MARKET_RISK_PREMIUM = 0.0475` vs historical `get_equity_risk_premium`) — would scale with beta, not produce a uniform 1.50pp

Live re-run of AMD today produces `12.37%` for both paths, suggesting either the divergence-causing code has been changed, or one path's input (e.g. as_of_date MRP, beta cache) has shifted. The historical empirical fact remains: **at capture time, 32% of tickers had divergent WACC values between phase2 and strategist by exactly 1.50pp.**

**Migration scope:** strategist's `wacc`/`beta`/`cost_of_equity` outputs are downstream-redundant with phase2's `wacc`/`beta`/`risk_free_rate`. Strategist has unique outputs in `risk_factors.{liquidity, downside, leverage, wacc_schedule}` that are NOT in phase2 — these come from `CapitalStructureRiskEngine` ([strategist.py:11](aletheia/agents/strategist.py#L11)) and would need to be relocated. Estimated **2 days** if `risk_factors` migration is in scope, **0.5 day** if just deduplicating WACC.

### Fields in valuation_report not covered by phase2

| `valuation_report` field | Equivalent in phase2 / elsewhere? |
|---|---|
| `dcf_result` | phase2.dcf has same DCFResult.to_dict() |
| `calculated_upside` | phase2.three_scenario_dcf.base.margin_of_safety (same calc, different field name) |
| `assumptions_used` | phase2.dcf has assumption-level metadata; lead reads `terminal_growth_rate` from this for compliance check at lead.py:254 |
| `summary` | phase2.summary covers same content |
| `base_financials` | DB-derived; no equivalent in phase2 (but recomputable any time) |
| `dcf_model.equity_value` | phase2 has EV per scenario, not equity_value (EV-net_debt is in `intrinsic_per_share` calc) |

**No unique analytical fields** in `valuation_report` that aren't in phase2 or recomputable from DB.

### Fields in strategist_report not covered by calc_node

| `strategist_report` field | Equivalent? |
|---|---|
| `capital_stack.debt_long, debt_current, equity` | Recomputable from `company_records_latest` raw fields (Step 1 of the JSON-as-truth migration already does this in `/capital_structure` endpoint) |
| `capital_stack.wacc, beta, cost_of_equity` | phase2.wacc / phase2.dcf.beta / phase2.dcf.risk_free_rate (with the +1.50pp caveat above) |
| `risk_factors.liquidity` (maturities_next_2y, cash, liquidity_ratio, refinancing_risk_score, liquidity_alert) | **Not in phase2.** Computed by `CapitalStructureRiskEngine.analyze_maturity_wall()` |
| `risk_factors.downside` (tangible_book_value, crash_fcf, earnings_power_value, floor_value, floor_price_per_share) | EPV is partially in DB (`epv_*` columns) via the screening pipeline; full EPV-floor calc lives in `CapitalStructureRiskEngine.run_break_the_company_audit()` |
| `risk_factors.leverage` (operating_leverage_score, financial_leverage_score, double_leverage_flag) | `operating_leverage_score` is duplicated (calc_node has it); `financial_leverage_score` and `double_leverage_flag` are unique to strategist |
| `risk_factors.wacc_schedule[1..5]` | Year-by-year WACC under target D/E migration. Not in phase2. Used by `report_generator` for the WACC schedule table |
| `concentration_risk, concentration_details` | From forensic_report (mirrored into capital_structure section by lead.py:236-237) |

**Unique strategist outputs:** `liquidity` block, `downside` block (excluding tangible_book_value which is in DB), `wacc_schedule`, `financial_leverage_score`, `double_leverage_flag`. These would need to migrate to a calc-layer module if strategist were removed.

---

## 4. Output Quality Assessment

Sampled 9 tickers covering distinct lifecycles: hyper-growth (NVDA, AAPL, COST), mature (JPM, MSFT, BRK-B), embedded value (CNC), routing-required (TSLA), platform (META). For each, examined the LLM-authored fields in the saved JSON.

### forensic_report — quality: **specific, ticker-grounded**

- `business_description` — names actual product lines, segments, distribution. NVDA: "data center-scale AI infrastructure company that designs GPUs, CPUs, DPUs, and networking solutions." AAPL: "premium consumer electronics, including smartphones (iPhone), personal computers (Mac), and wearables." Not generic.
- `moat.evidence` — cites specific 10-K facts: COST 90-93% renewal rates, NVDA 7.5M CUDA developers, JPM $4.4T assets, BRK-B $160B insurance float. **Direct citation pattern is consistent across all 9 sampled tickers.**
- `revenue_segments`, `key_customers`, `competitive_landscape` — populated with ticker-specific entries.

### value_chain_report — quality: **ticker-specific suppliers, margin numbers from DB**

- `bottleneck_analysis` — names actual suppliers: TSMC for AAPL/NVDA, Panasonic/CATL for TSLA, Apple iOS for META, locomotive manufacturers for BNSF (BRK-B). 9/9 sampled were specific.
- `pricing_power_assessment` — cites actual margin percentages from the DB context (NVDA 71.1% GM, AAPL 46.9% GM). Combines DB facts with narrative judgment.
- `power_ratio` (LLM-estimated supplier-margin / target-margin) — flagged in schema as "ESTIMATE" since supplier margins aren't in DB. Intermediate-quality field.

### strategic_context_report — quality: **mostly cyclicality re-narrative**

- `summary` — references actual z-scores (`AAPL z=1.51`, `NVDA z=3.22`, `JPM z=2.33`). The narrative content beyond the z-score is thin; mostly re-states the deterministic flag (peak/non-peak).
- `deferred_revenue_trend` — sometimes specific (Apple "9.8% growth"), sometimes generic ("trends are aligned with growth"). Mixed quality.
- `intangible_risk_assessment` — frequently cites 10-K language ("the 10-K explicitly states no single intellectual property"). When 10-K text is in `raw_10k_text`, this field is grounded; when 10-K fetch fails, it falls back to model knowledge (lower quality).

### contrarian_report — quality: **structured math + specific bear narrative**

- `bias_detected` — labels recur across tickers ("Narrative Premium / FOMO" appears for NVDA/MSFT/META; "Growth Extrapolation" for AAPL/COST/TSLA; "Overconfidence" for JPM). The categorization is genuinely ticker-mapped, not random — high-multiple-premium tickers cluster under "Narrative Premium," lower-premium cluster under "Overconfidence."
- `bear_case_summary` — incorporates actual reverse-DCF math from phase2 (implied CAGR vs historical, multiple premium percentage, bear DCF IPS). Length consistent at ~1000 chars.
- `quant_challenge` — purely structured (no LLM) — built from phase2 numbers via Python f-string. Provides the math the LLM bear case argues against.
- **Web search contribution unclear from saved data.** The `raw_results` field isn't preserved in current reports; web-search content is folded into the LLM bear case but its incremental value beyond the quant challenge can't be assessed from the saved output alone.

### Agent-proposed scenarios — quality: **rich for hyper-growth tickers, empty for everyone else**

| Ticker | Scenarios proposed | Examples |
|---|---|---|
| NVDA | 5 | Export Control Escalation (forensic/bear), Hyperscaler ASIC Substitution (vc/bear), Agentic AI Expansion (vc/bull), AI Capex Digestion (context/bear), Sustained AI Supercycle (context/bull) |
| AAPL | 2 | Supply Chain Tariff Compression, Supply Chain Cost Squeeze (both forensic+vc bear) |
| JPM, COST, CNC, TSLA, BRK-B, META | 0 | none |

The catalog comment in `forensic.py` and `value_chain.py` explicitly says **empty list is preferred when no high-conviction alternate hypothesis exists**. The agents are following that guidance — only NVDA/AAPL produce scenarios because they have prominent narrative debates worth modeling. For 7 of 9 sampled tickers, the scenario_eval_node runs and produces an empty `scenario_results` list. The pipeline machinery is intact but underutilized.

(Note: NVDA's saved bear scenario shows `IPS=$834, upside=+301.59%` — a bear scenario with hugely positive upside. This is a stale-data artifact from the bull/base/bear inversion bug fixed during the JSON-as-truth migration; the current engine would compute correctly.)

---

## 5. Removal Feasibility

For each agent flagged as a removal candidate, exact migration scope.

### Candidate A — `fundamentalist`

- **Status:** already removed from production graph; only legacy entry points still call it.
- **Consumers to update:**
  - [aletheia/agents/lead.py:122](aletheia/agents/lead.py#L122) — `val = state.get("valuation_report", {})`. Used at lines 254 (`val.get("assumptions_used", {}).get("terminal_growth_rate")`), 322 (`dcf = val.get("dcf_result")`), 411 (`val.get("calculated_upside")`), and 415 (the entire `dcf_model` block in final_report).
  - [run_valuation.py:15-51](run_valuation.py#L15-L51) — legacy CLI entry point.
  - [scripts/archive/](scripts/archive/) — already archived; ignore.
- **Output fields lost:** all 4 are recomputable from phase2:
  - `dcf_result` → phase2.dcf
  - `calculated_upside` → phase2.three_scenario_dcf.base.margin_of_safety
  - `assumptions_used.terminal_growth_rate` → already in phase2 (read by compliance check)
  - `dcf_model.equity_value` → phase2 base scenario EV − net_debt
- **Effort:** 4 lead.py edits + delete fundamentalist.py + delete or update run_valuation.py. **0.5 day. High confidence.**

### Candidate B — `valuation_node` shim

- **Status:** explicitly self-documents as deprecated; just delegates to `calc_node`.
- **Consumers:** only `run_valuation.py:54` and archived scripts.
- **Output fields lost:** none (it returns calc_node's output).
- **Effort:** delete file + update run_valuation.py to import calc_node directly. **0.25 day. High confidence.**

### Candidate C — `strategist`

**More involved than the others.** Three categories of output:

1. **Redundant with phase2:** `capital_stack.{wacc, beta, cost_of_equity, debt_long, equity}` — verified divergent by 1.50pp on 8 tickers; conceptually equivalent. The `/capital_structure` API endpoint already recomputes these from DB+DCFEngine for pending tickers (Step 1 of JSON-as-truth migration).

2. **Unique to strategist (not in phase2 or DB):**
   - `risk_factors.liquidity` — `analyze_maturity_wall` from `CapitalStructureRiskEngine`
   - `risk_factors.downside` — `run_break_the_company_audit` (EPV floor, crash FCF, tangible book — partially in DB epv_* columns but not all)
   - `risk_factors.wacc_schedule[1..5]` — year-by-year WACC under target D/E
   - `risk_factors.leverage.{financial_leverage_score, double_leverage_flag}` — capital-stack ratios

3. **Mirrored from forensic:** `concentration_risk`, `concentration_details` — copied in lead.py:236-237.

- **Consumers to update:**
  - [aletheia/agents/lead.py:118](aletheia/agents/lead.py#L118) — assembles `3_capital_structure_risk` from strategist
  - [aletheia/tools/conviction_scorer.py:433](aletheia/tools/conviction_scorer.py#L433) — pillar 2 reads strategist
  - [aletheia/agents/fundamentalist.py:59](aletheia/agents/fundamentalist.py#L59) — dead code, ignore
- **Output fields lost without migration:** all of category 2 above. `wacc_schedule` is rendered into the report's WACC-schedule table; `risk_factors.liquidity` and `downside` are rendered into the capital-risk panel of Deep Dive.
- **Migration paths:**
  - Move `CapitalStructureRiskEngine` to `aletheia/tools/` and call it from calc_node
  - Or split: keep `risk_factors.*` blocks but compute them deterministically (no agent), source `wacc/beta` from phase2
- **Effort:** **2-3 days** if `risk_factors` is preserved; **0.5 day** if strategist is removed and risk_factors are accepted as lost (Step 5 of JSON-as-truth migration noted these would degrade to empty for pending tickers).

### Candidate D — `intake` (no-op)

- **Status:** `intake_agent` is essentially a no-op (`return {"serving_base": {}, ...}`). The 270 lines of class definitions are unused.
- **Consumers:** nothing reads `serving_base`.
- **Effort:** delete the file or strip to a 5-line passthrough. **0.25 day. High confidence.**

### Candidate E — `contrarian` (v1)

- **Status:** superseded by `contrarian_v2`. Not in graph.
- **Effort:** delete file. **0.1 day. High confidence.**

---

## 6. Observed Gaps

Things the architectural argument might miss:

1. **lead.py's "FATAL" raise on missing forensic/value_chain.** If forensic or value_chain agents fail in a way that doesn't write the state key (rare but possible — e.g. if the agent raises before `output = {...}` is constructed), lead crashes the entire pipeline at [lead.py:114-115](aletheia/agents/lead.py#L114-L115). Current narrative agents catch their own exceptions and always return at least an empty dict, so the raise is defensive but tightly coupled. Removing forensic or value_chain would require updating this guard.

2. **scenario_eval_node is empty for 78% of tickers.** The infrastructure works correctly — it correctly returns empty when no agents propose scenarios. But the actual analytical contribution is concentrated in 2-3 hyper-growth tickers per cohort. If forensic/value_chain/context were stripped, the scenario pipeline would lose its only inputs and produce empty results across the universe. This is not a hidden cost of those agents (the scenarios are visible in the report), but it is a coupling: removing the narrative agents removes the scenario pipeline's entire input source.

3. **conviction_scorer's pillar 1 reads `mf.score` from local variable inside calc_node.** The `moat_fingerprint` state key persists for audit but is not the actual data path. If anyone tries to "improve" pillar 1 by mutating the state field, it won't take effect. The actual coupling is `calc_node → ConvictionScorer._compute(...)` inline, not state-mediated.

4. **Strategist falls back to `raw_TotalEquity` when `get_market_cap` fails ([strategist.py:35-36](aletheia/agents/strategist.py#L35-L36)).** Book equity is not market cap; this silently produces wrong WACC weights for any ticker whose market cap fetch fails. Calc_node uses the same `get_market_cap` but doesn't fall back to book equity. This may explain part of the 1.50pp divergence pattern for tickers whose market_cap fetch is flaky in one path but not the other. Worth checking before removing strategist.

5. **`raw_10k_text` is consumed by 3 narrative agents.** If librarian fails (SEC API down, unknown ticker), `raw_10k_text` contains "SEC Fetch Failed" or empty. Forensic/value_chain/context all check `ten_k_available` and fall back to "use pipeline data and knowledge" prompt — they do not crash, but their output quality degrades materially. This is observable in the saved data: tickers with successful 10-K fetches have specific 10-K-citations in their narratives; tickers with failures fall back to model knowledge (verifiable but not directly evidence-grounded).

6. **Token cost is concentrated in narrative agents reading raw_10k_text.** Forensic/value_chain/context each ingest 50-60K characters of 10-K text. That's roughly 90% of the token cost per pipeline run. If any of these agents are kept, the 10-K text injection is the dominant cost driver — far more than the prompt template or DB-context strings.

7. **Lead's compliance checks at [lead.py:254-291](aletheia/agents/lead.py#L254-L291) read from `valuation_report.assumptions_used.terminal_growth_rate` and `phase2_valuation.implied_cagr / historical_cagr / ev_ebitda_premium_pct`.** With fundamentalist removed, the first read is always None, so the terminal-cap compliance check at lead.py:265 always passes vacuously. This is currently silent — the check appears in `constitution_checks` as "✅ PASS: Terminal Cap" regardless of whether the input was checked. **This is an actively misleading output.**

8. **Reports older than 2026-05-02 contain stale post-fundamentalist-removal artifacts.** 24 of 25 saved reports were generated before fundamentalist was removed. AAPL was re-run on 2026-05-02. The `dcf_model.intrinsic_value` field for the 24 older reports contains EV-shaped numbers; for AAPL it's null. The Step 1-3 JSON-as-truth migration partially mitigates this by recomputing live; legacy reports persist on disk for the export pipeline.

9. **Web search contribution to bear case is opaque.** Contrarian_v2's saved output collapses the LLM-synthesized bear case into a single ~1000-char string. The raw search results are written to `contrarian_report.raw_results` but get truncated or dropped in some report generations (varies by ticker). Without a controlled experiment (run with vs without search), the marginal value of the DuckDuckGo search vs the structured `quant_challenge` cannot be assessed.

10. **The `intake.py` file contains a fully-implemented `EconomicRealityTranslator` class (~120 lines) with intermediate calculations that overlap with cleaning_engine.** It's never called, but a future developer might confuse the dead code for active logic.

---

## 7. Recommendations

Confidence levels: **High** = supported by direct empirical evidence; **Medium** = supported by structural reading + sample data; **Low** = needs further investigation before action.

### Safe removals (high confidence)

| Agent | Confidence | Reason | Effort |
|---|---|---|---|
| `valuation_node` shim | High | Already documented as deprecated, delegates to calc_node, only run_valuation.py imports it | 0.25 day |
| `intake` | High | No-op in production; class defs never called; no consumers of `serving_base` | 0.25 day |
| `contrarian` v1 | High | Superseded by v2 in graph; v1 not imported anywhere current | 0.1 day |
| `fundamentalist` | High | Already removed from graph but file persists; lead.py reads produce null dcf_model fields and silently bypass compliance checks; **active misleading output** | 0.5 day |

### Keep but enrich

| Agent | Confidence | Reason |
|---|---|---|
| `forensic` | Medium | Output is ticker-specific and grounded; but consumers reduce to (a) lead's narrative assembly, (b) conviction's `operating_leverage_score` reads, (c) export rendering. The narrative IS the value. |
| `value_chain` | Medium | Same pattern — ticker-specific narrative; some unique scenarios for hyper-growth names |
| `context` | Medium | Mixed quality; the deterministic z-score is the value-add; the LLM narrative around it is thinner |
| `contrarian_v2` | Medium | The structured `quant_challenge` is unambiguously valuable (it's pure math from phase2). The web-search-augmented bear narrative requires controlled experiment to assess marginal value |

### Need empirical evaluation before deciding

| Agent | What to measure | Why |
|---|---|---|
| `strategist` | Run calc_node + `compute_wacc` direct comparison for the 8 divergent tickers; identify root cause of +1.50pp gap | Removal scope depends entirely on whether the divergent path is correct or buggy |
| `contrarian_v2` web search | A/B run: bear case generated with web search vs without | The DuckDuckGo dependency adds latency + flake; if value-add is small, replace with structured retrieval |
| Agent-proposed scenarios | Track scenario count over a larger sample (40+ tickers) | Currently 22% of sampled tickers produce scenarios; if the figure is similar over the full universe, the scenario pipeline justifies its cost only for hyper-growth names |

### Structural changes safe to do immediately

1. **Remove `fundamentalist`, `valuation_node`, `intake`, `contrarian` v1 dead files.** ~1.1 days total. None are in production graph; cleanup eliminates ~510 lines of misleading code.
2. **Update [lead.py:254-256](aletheia/agents/lead.py#L254-L256) terminal-cap compliance check** to read from `phase2_valuation.dcf.assumptions` rather than the dead `valuation_report.assumptions_used` path. Currently always vacuously PASS-es. This is a correctness bug, not a refactor.
3. **Clean up the orphan state keys** (`operating_leverage`, `moat_fingerprint`, `serving_base`). They persist in state for no reader. Either document them as audit-only outputs or stop writing them.

### Need broader architectural decisions

1. **Strategist's `risk_factors.{liquidity, downside, wacc_schedule}` migration path.** These are unique outputs (not redundant with phase2). If strategist is removed, they need a new home — likely as deterministic computers in `aletheia/tools/capital_structure.py`, mirroring the calc-layer pattern from the JSON-as-truth migration. The decision is whether to do this now or accept that strategist stays as the home for these computations. Both are defensible; the choice depends on appetite for further calc-layer expansion vs accepting one more LLM-free-but-still-an-agent module.

2. **Whether to retain narrative-agent scenarios.** They produce rich content for hyper-growth tickers and empty content for the rest. Either:
   - Accept this as correct ("quality > quantity," 22% utilization is fine), or
   - Replace with structured scenario library in code (analyst-authored, not LLM-proposed) and decommission the scenario fields of forensic/value_chain/context.

3. **10-K text consumption pattern.** Three agents each ingest 50-60K chars of 10-K text and produce overlapping narratives. Whether to consolidate into a single 10-K-ingestion node that produces a structured summary consumed by all three, or accept the redundant ingestion as the cost of having three independent perspectives. This is the largest token cost in the system.

---

## Appendix — Investigation methods

- **Agent inventory:** `ls aletheia/agents/` + line counts via `wc -l`
- **Graph orchestration:** read [aletheia/workflow/graph.py](aletheia/workflow/graph.py) end-to-end
- **Consumer mapping:** Explore subagent grep across [aletheia/](aletheia/), [api_main.py](api_main.py), [streamlit_app.py](streamlit_app.py); verified by direct grep on representative state keys
- **Quality assessment:** parsed 9 saved reports (`valuation_data/serving/latest/{AAPL, NVDA, JPM, COST, CNC, MSFT, TSLA, BRK-B, META}_report.json`)
- **Redundancy verification (WACC):** parsed all 25 saved reports and computed phase2.wacc − strategist.wacc
- **Redundancy verification (DCF):** examined `dcf_model.intrinsic_value` field across reports of varying generation dates relative to fundamentalist removal
- **Failure mode analysis:** grep for `except` patterns in each agent + read of error-handling blocks
- **Token cost estimation:** `re.findall` on prompt templates + 10-K truncation lengths

---

*This investigation took ~0.5 day of focused work and produces evidence sufficient to ground refactoring decisions. It does not propose a new architecture. Decisions on which agents to remove, retain, or migrate require strategic input on tolerance for narrative-agent token costs vs deterministic-computer expansion.*
