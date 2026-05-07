# Calculation Dependencies

This document provides a dependency graph showing how calculation tools and agents interact within the Aletheia pipeline.

## Dependency Graph

```mermaid
graph TD;
    %% Tools Calling Other Tools
    equity_bridge.py --> dcf_engine.py
    reverse_dcf.py --> dcf_engine.py
    multiple_decomposition.py --> dcf_engine.py
    three_stage_dcf.py --> math_utils.py
    conviction_scorer.py --> lifecycle_classifier.py
    universe_portfolio.py --> screening_ratios.py
    
    %% Agents Calling Tools
    fundamentalist.py --> finance.py
    fundamentalist.py --> pro_forma.py
    strategist.py --> dcf_engine.py
    valuation_node.py --> equity_bridge.py
    valuation_node.py --> reverse_dcf.py
    valuation_node.py --> multiple_decomposition.py
    valuation_node.py --> dcf_engine.py
    lead.py --> conviction_scorer.py
    
    %% Database Interaction
    equity_bridge.py --> database.py
    screening_ratios.py --> database.py
    reverse_dcf.py --> database.py
    multiple_decomposition.py --> database.py
    lifecycle_classifier.py --> database.py
    dcf_engine.py --> database.py
    conviction_scorer.py --> database.py
    universe_portfolio.py --> database.py
    forensic.py --> database.py
    value_chain.py --> database.py
    lead.py --> database.py
    context.py --> database.py
    fundamentalist.py --> database.py
    strategist.py --> database.py
```

## Description of Interactions

*   **`dcf_engine.py`** is the core valuation engine. It is heavily relied upon by `reverse_dcf.py`, `multiple_decomposition.py`, and `equity_bridge.py` to extract intrinsic value baselines and reverse-engineer implied market expectations.
*   **`valuation_node.py`** orchestrates the entire Phase 2 valuation synthesis by combining outputs from `dcf_engine`, `reverse_dcf`, `multiple_decomposition`, and `equity_bridge`.
*   **`database.py`** acts as the central state and cache manager for almost all analytical tools.
*   **`screening_ratios.py`** serves as the provider for universe-level portfolio metrics used by `universe_portfolio.py`.
*   **`conviction_scorer.py`** requires context from `lifecycle_classifier.py` to appropriately gate its thresholds based on the company's maturity.
