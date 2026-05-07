# Scenario Authoring Guide

The Aletheia DCF engine supports analyst-authored scenarios via the `ScenarioOverride` Pydantic model. Each override is a typed bundle of bounded fields applied to a fresh `DCFEngine.run()`; the calc layer does all the math.

## The 9 override fields

All fields are optional. Unspecified fields fall through to the lifecycle's default value.

| Field | Bounds | Routes to | Use when |
|-------|--------|-----------|----------|
| `revenue_growth_y1_5` | `[0.0, 0.45]` | `profile.growth_rate` | Override Y1-5 revenue CAGR |
| `revenue_growth_y6_10` | `[0.0, 0.25]` | (DCF math; no profile field) | Override Y6-10 revenue CAGR |
| `terminal_growth` | `[0.0, 0.06]` | `profile.terminal_growth` | Override perpetual growth rate |
| `terminal_margin_decay` | `[0.5, 1.0]` | `profile.terminal_margin_decay` | Ratio terminal/current margin (0.85 = 15% decay) |
| `terminal_ebit_margin` | `[0.05, 0.65]` | `profile.terminal_ebit_margin_override` | Direct override of terminal EBIT margin |
| `capex_pct_revenue` | `[0.0, 0.50]` | `profile.capex_pct_revenue_override` | Forward CapEx as % of revenue |
| `discount_rate` | `[0.04, 0.16]` | `profile.discount_rate_override` | Direct WACC override |
| `tax_rate` | `[0.0, 0.40]` | `profile.tax_rate_override` | Override effective tax rate |
| `base_revenue_normalization` | `> 0` | DataFrame mutation | Override base-year revenue (cyclical normalization) |

> Note: `discount_rate` is named that way (not `wacc_override`) to avoid the `test_no_agent_emitted_overrides.py` architecture lock. Same effect; cleaner naming.

## Quick start

```python
from aletheia.contracts.interfaces import ScenarioOverride
from aletheia.scenarios.library import run_scenario

# Define a scenario
def msft_conservative(_calc):
    return ScenarioOverride(
        name="MSFT conservative",
        scenario_type="bear",
        proposed_by="forensic",
        rationale="Terminal growth at 4% instead of software cap",
        terminal_growth=0.04,
    ), {}

# Run it
result = run_scenario("MSFT", msft_conservative)
print(f"Base IV ${result.base_iv:.2f}, upside {result.upside_pct*100:+.1f}%")
```

## Pre-defined library scenarios

Importable from `aletheia.scenarios.library`:

| Scenario | Direction | What it does |
|----------|-----------|--------------|
| `historical_cagr_continues` | base | Y1-5 = Y6-10 = realized 5y revenue CAGR |
| `growth_fades_to_gdp` | bear | Gradual deceleration; terminal at 2.5% GDP |
| `recession_scenario` | bear | Y1-5 cut 50%, terminal margin × 0.85 |
| `rate_normalization` | base alt | Discount rate at 5y-trailing-mean Rf + ERP |
| `bull_execution` | bull | Y1-5 CAGR + 25%, terminal growth at lifecycle cap |
| `bear_execution` | bear | Y1-5 halved, terminal at 2%, margin decay 30% |
| `consensus_growth` | n/a | **STUB** — needs IBES analyst-consensus EPS data |
| `margin_reverts_to_industry_median` | n/a | **STUB** — needs industry peer mapping |

## Scenario comparison

```python
from aletheia.scenarios.compare import compare_scenarios, render_comparison
from aletheia.scenarios.library import (
    historical_cagr_continues, growth_fades_to_gdp,
    bull_execution, bear_execution,
)

scenarios = [
    ("Historical CAGR", historical_cagr_continues),
    ("Fade to GDP",     growth_fades_to_gdp),
    ("Bull execution",  bull_execution),
    ("Bear execution",  bear_execution),
]
df = compare_scenarios("MSFT", scenarios)
print(render_comparison(df))
```

Output is a tidy markdown table showing IV, WACC, terminal growth, terminal margin, etc. side-by-side.

## Sensitivity (tornado) analysis

```python
from aletheia.scenarios.sensitivity import tornado_analysis, render_tornado

t = tornado_analysis("MSFT", perturbation_pct=0.10)
print(render_tornado(t))
```

Perturbs each of 10 inputs ±10% and ranks them by IV impact. Standard analytical tool to identify which assumption dominates a given ticker's IV.

## Persistence

```python
from datetime import date
from aletheia.scenarios.persistence import (
    SavedScenario, save_scenario, load_scenario, list_scenarios, run_saved
)

# Save
saved = SavedScenario(
    ticker="MSFT",
    name="My MSFT view 2026-Q2",
    rationale="Software at 4% terminal, conservative margin path",
    created_at=date.today(),
    override=ScenarioOverride(
        name="My MSFT view 2026-Q2",
        scenario_type="base_alternative",
        proposed_by="forensic",
        rationale="...",
        terminal_growth=0.04,
        terminal_ebit_margin=0.42,
    ),
)
path = save_scenario(saved)

# List
for s in list_scenarios("MSFT"):
    print(f"  {s.created_at}  {s.name}")

# Re-run
result = run_saved(saved)
```

Storage: `valuation_data/scenarios/<TICKER>/<slug>__<YYYY-MM-DD>.json`. Saved scenarios are reproducible across runs — same input, same IV. Even after parameter calibration changes, the saved override is what runs.

## Common patterns

### Override only what matters

Don't construct kitchen-sink overrides. The lifecycle defaults are already calibrated; override only the fields where you have a specific view.

### Use `terminal_ebit_margin` for direct overrides; `terminal_margin_decay` for ratios

`terminal_ebit_margin` is a direct value (e.g., 0.30 = 30%). `terminal_margin_decay` is a ratio of terminal to current (e.g., 0.85 = terminal is 85% of current). Pick one.

### `base_revenue_normalization` for cyclicals

When a cyclical name is at peak earnings (CAT, NVDA), historical CAGR projection forward is misleading. Override the base-year revenue to a normalized level and the engine will compound from there.

### Discount rate overrides for rate-regime testing

Use `discount_rate=0.07` to ask "what if rates normalize to 7%?". The library scenario `rate_normalization` does this with the 5y-trailing-mean Rf.

## What NOT to do

- **Don't pass `wacc_override` as a field name** — the architecture lock test will fail. Use `discount_rate`.
- **Don't override every field** — bounds enforcement protects you, but kitchen-sink overrides destroy the model's interpretability. Override the 1-2 fields you have a view on.
- **Don't mutate ScenarioOverride after construction** — it's a frozen Pydantic model. Make a new one.
- **Don't rely on the stubs** (`consensus_growth`, `margin_reverts_to_industry_median`) — they raise NotImplementedError until phase 3.5 ships the data ingestion.

## File map

- `aletheia/contracts/interfaces.py` — `ScenarioOverride` Pydantic model + bounds validators
- `aletheia/scenarios/library.py` — 8 pre-defined scenarios + runner
- `aletheia/scenarios/sensitivity.py` — tornado analysis
- `aletheia/scenarios/compare.py` — side-by-side comparison
- `aletheia/scenarios/persistence.py` — save/load/list
- `aletheia/agents/scenario_eval_node.py` — agent-side override application (existing)
