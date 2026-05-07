"""
aletheia/backtest/signal_generator.py

Wraps the calc layer (CleaningEngine + DCFEngine + ConvictionScorer) for
historical signal generation. The DCF engine internally calls
market_data.get_current_price / get_market_cap; for backtesting we need
to inject the price at as_of_date instead of today's price. We do this by
monkey-patching those functions for the duration of the engine call.

Acknowledged lookahead-bias gaps (documented limitations, not fixed here):
  - WACC uses current beta and current risk-free rate, not historical values
    at as_of_date. Beta especially is a 5-year-trailing window from today,
    not from as_of_date. This biases WACC and therefore IV estimates.
  - The known_issues registry uses the current state, not the state as it
    was understood at as_of_date.
"""

from __future__ import annotations

import os
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import date
from typing import Dict, List, Optional

import pandas as pd

from aletheia.backtest.outcome_tracker import compute_momentum, get_price_at
from aletheia.backtest.point_in_time import load_point_in_time
from aletheia.contracts.interfaces import CalculationInput, ValuationProfile
from aletheia.data.database import InvestmentDatabase
from aletheia.data.cleaning_engine import CleanedRecord
from config.known_issues import KNOWN_ISSUES
from config.lifecycle_thresholds import STAGE_THRESHOLDS, Stage
from config.ticker_classification import UNIVERSE
from config.valuation_defaults import LIFECYCLE_PROFILES

# Signal-set selection — ordering doesn't matter, only membership.
ALL_SIGNALS = (
    "upside_pct", "conviction_score", "roic_wacc_spread", "ev_ebitda_gap_pct",
    "moat_fingerprint_score", "rdcf_growth_gap", "beneish_m_score",
)


@dataclass(frozen=True)
class BacktestSignal:
    """Snapshot of the engine's output at a historical point in time."""
    ticker: str
    as_of_date: date

    # Data lineage — critical for staleness analysis
    fiscal_year_used: int
    filing_date: date
    days_since_filing: int

    # DCF outputs (per-share intrinsic values)
    base_iv: float
    bull_iv: float
    bear_iv: float
    current_price: float            # historical price at as_of_date (NOT today)
    upside_pct: float               # (base_iv - current_price) / current_price

    # Other fundamental signals already produced by the calc layer
    conviction_score: float
    roic_wacc_spread: float
    implied_ev_ebitda: float
    justified_ev_ebitda: float
    ev_ebitda_gap_pct: float        # (implied - justified) / justified — positive = expensive

    # New signals (extended backtest)
    moat_fingerprint_score: Optional[float] = None     # 1-5 weighted score
    moat_components: Optional[Dict[str, float]] = None # roic_persistence/gm_stability/capex_intensity
    rdcf_implied_growth: Optional[float] = None        # market-implied 10y revenue CAGR
    rdcf_growth_gap: Optional[float] = None            # implied - historical 5y CAGR
    beneish_m_score: Optional[float] = None
    beneish_flagged: Optional[bool] = None

    # Momentum baseline (control variable)
    price_momentum_180d: float = 0.0

    # Metadata
    data_quality_warnings: List[str] = field(default_factory=list)
    cagr_window_used: int = 0
    cagr_data_density: float = 0.0


def _records_to_dataframe(records: List[CleanedRecord]) -> pd.DataFrame:
    """
    Convert a list of CleanedRecords into a DataFrame with the column shape
    the calc layer expects (`raw_*`, `clean_*`, `derived_*`, `fiscal_year`).

    We use a temp DuckDB to leverage the existing upsert_record schema —
    this is the most robust way to ensure downstream columns match.
    """
    tmp = tempfile.NamedTemporaryFile(suffix=".duckdb", delete=False)
    tmp.close()
    # DuckDB rejects empty files; remove it so connect() creates a fresh DB
    try:
        os.unlink(tmp.name)
    except FileNotFoundError:
        pass
    try:
        db = InvestmentDatabase(db_path=tmp.name, verbose=False)
        for r in records:
            db.upsert_record(r)
        ticker = records[0].ticker if records else None
        if ticker is None:
            db.close()
            return pd.DataFrame()
        df = db.get_latest(ticker)
        db.close()
        return df
    finally:
        try:
            os.unlink(tmp.name)
        except FileNotFoundError:
            pass


def _build_calc_input(
    ticker: str, df: pd.DataFrame
) -> Optional[CalculationInput]:
    """Construct a CalculationInput for backtesting (no live DB)."""
    classification = UNIVERSE.get(ticker)
    if classification is None:
        return None
    issues = KNOWN_ISSUES.get(ticker, [])
    lifecycle = classification.lifecycle or "mature"
    profile_cfg = LIFECYCLE_PROFILES.get(
        lifecycle, LIFECYCLE_PROFILES["mature"]
    )
    vp = ValuationProfile(
        growth_rate=profile_cfg.growth_rate,
        terminal_growth=profile_cfg.terminal_growth,
        forecast_years=profile_cfg.forecast_years,
        terminal_margin_decay=profile_cfg.terminal_margin_decay,
    )
    try:
        stage = Stage(lifecycle)
        thresholds = STAGE_THRESHOLDS[stage]
    except (ValueError, KeyError):
        thresholds = STAGE_THRESHOLDS[Stage.GROWTH_COMPOUNDER]
    return CalculationInput(
        df=df,
        classification=classification,
        known_issues=issues,
        valuation_profile=vp,
        lifecycle_thresholds=thresholds,
    )


@contextmanager
def _patch_market_data(historical_price: float, shares_diluted: float):
    """
    Inject the historical price and a back-computed market cap into the
    market_data module so DCFEngine sees historical values instead of
    today's. WACC inputs (beta, rf-rate) are NOT patched; this is an
    acknowledged limitation per the spec.
    """
    from aletheia.data import market_data

    historical_market_cap = (
        historical_price * shares_diluted
        if historical_price and shares_diluted else 0.0
    )

    # Save originals
    orig_price = market_data.get_current_price
    orig_mcap = market_data.get_market_cap
    try:
        market_data.get_current_price = lambda ticker_arg: historical_price
        market_data.get_market_cap = lambda ticker_arg: historical_market_cap
        # The DCFEngine imports inside its run() method — also patch the
        # bound names there. The engine does `from aletheia.data.market_data
        # import get_current_price, get_market_cap` inside the function body,
        # so the patch above propagates correctly through fresh imports.
        yield
    finally:
        market_data.get_current_price = orig_price
        market_data.get_market_cap = orig_mcap


def generate_signal_at_date(
    ticker: str,
    as_of_date: date,
    verbose: bool = False,
) -> Optional[BacktestSignal]:
    """
    Run the calc layer using only data available at as_of_date.
    Returns None if insufficient data (no fiscal year filing yet, or fewer
    than 3 years of history).
    """
    # 1. Point-in-time cleaning
    pit = load_point_in_time(ticker, as_of_date, verbose=verbose)
    if pit is None or len(pit.records) < 3:
        return None

    # 2. Build DataFrame and CalculationInput
    df = _records_to_dataframe(pit.records)
    if df.empty:
        return None
    calc_input = _build_calc_input(ticker, df)
    if calc_input is None:
        return None

    # 3. Historical price at as_of_date
    historical_price = get_price_at(ticker, as_of_date)
    if historical_price is None or historical_price <= 0:
        return None

    # 4. Run DCF (with market data patched to historical price)
    from aletheia.tools.dcf_engine import DCFEngine
    from aletheia.data.exceptions import MissingFieldError

    # We need shares_diluted from the latest record to compute market cap
    latest_record = max(pit.records, key=lambda r: r.fiscal_year)
    shares_diluted = (
        latest_record.clean.get("SharesDiluted")
        or latest_record.raw.get("SharesDiluted")
        or 0.0
    )
    if not shares_diluted:
        return None

    # Construct DCFEngine with as_of_date so it pulls historical rf/beta/erp
    # via aletheia.data.historical_macro instead of live yfinance values.
    # Diagnostic flag: ALETHEIA_RF_NORMALIZED=1 substitutes 5y-trailing-mean
    # ^IRX for spot ^IRX. Tests whether macro volatility (not methodology)
    # is driving the conviction-edge collapse.
    rf_override = None
    if os.environ.get("ALETHEIA_RF_NORMALIZED") == "1":
        from aletheia.data.historical_macro import get_risk_free_rate_normalized
        rf_override = get_risk_free_rate_normalized(as_of_date)
    dcf_engine = DCFEngine(verbose=False, as_of_date=as_of_date,
                            risk_free_rate=rf_override)
    try:
        with _patch_market_data(historical_price, shares_diluted):
            dcf_result = dcf_engine.run(calc_input, fiscal_year=pit.fiscal_year_used)
    except (MissingFieldError, NotImplementedError, ValueError, ZeroDivisionError) as e:
        if verbose:
            print(f"  [skip] DCF failed for {ticker} @ {as_of_date}: {e}")
        return None
    except Exception as e:
        if verbose:
            print(f"  [skip] DCF unexpected error {ticker} @ {as_of_date}: {e}")
        return None

    if not dcf_result.base or not dcf_result.bull or not dcf_result.bear:
        return None

    base_iv = dcf_result.intrinsic_per_share(
        dcf_result.base.enterprise_value, dcf_result.net_debt
    ) or 0.0
    bull_iv = dcf_result.intrinsic_per_share(
        dcf_result.bull.enterprise_value, dcf_result.net_debt
    ) or 0.0
    bear_iv = dcf_result.intrinsic_per_share(
        dcf_result.bear.enterprise_value, dcf_result.net_debt
    ) or 0.0
    upside_pct = (
        (base_iv - historical_price) / historical_price
        if historical_price > 0 else 0.0
    )

    # 5. Momentum baseline (control variable)
    momentum_180d = compute_momentum(ticker, as_of_date, lookback_days=180) or 0.0

    # 6. Days since filing (signal staleness)
    days_since_filing = (as_of_date - pit.filing_date_used).days

    # 7. EV/EBITDA gap = (implied - justified) / justified
    impl = float(dcf_result.base.implied_ev_ebitda)
    just = float(dcf_result.base.justified_ev_ebitda)
    ev_gap = (impl - just) / just if just > 0 else 0.0

    # 8. Moat fingerprint (point-in-time history only)
    moat_score, moat_components = _safe_moat_fingerprint(calc_input)

    # 9. Reverse DCF — implied growth & gap vs historical CAGR
    rdcf_implied, rdcf_gap, rdcf_hist_cagr = _safe_reverse_dcf(
        calc_input, pit.fiscal_year_used,
        historical_price=historical_price,
        market_cap=historical_price * shares_diluted,
    )

    # 10. Beneish M-score (needs prior-year record)
    beneish_score, beneish_flag = _safe_beneish(pit.records, latest_record)

    # 11. Conviction score — synthesized state dict from deterministic signals.
    #     Must run AFTER moat + RDCF since it consumes their outputs.
    conviction = _safe_conviction_score(
        ticker, calc_input,
        moat_score=moat_score,
        upside_pct=upside_pct,
        ev_ebitda_gap_pct=ev_gap,
        roic=getattr(dcf_result, "roic", None),
        wacc_base=getattr(dcf_result, "wacc_base", None),
        rdcf_implied_growth=rdcf_implied,
        rdcf_historical_cagr=rdcf_hist_cagr,
    )

    # Data quality warnings — pull from the latest record's blocking_errors / warnings
    quality_warnings: List[str] = []
    for w in (latest_record.cleaning_warnings or []):
        quality_warnings.append(str(w))

    return BacktestSignal(
        ticker=ticker,
        as_of_date=as_of_date,
        fiscal_year_used=pit.fiscal_year_used,
        filing_date=pit.filing_date_used,
        days_since_filing=days_since_filing,
        base_iv=float(base_iv),
        bull_iv=float(bull_iv),
        bear_iv=float(bear_iv),
        current_price=float(historical_price),
        upside_pct=float(upside_pct),
        conviction_score=float(conviction),
        roic_wacc_spread=float(dcf_result.base.roic_wacc_spread),
        implied_ev_ebitda=impl,
        justified_ev_ebitda=just,
        ev_ebitda_gap_pct=float(ev_gap),
        moat_fingerprint_score=moat_score,
        moat_components=moat_components,
        rdcf_implied_growth=rdcf_implied,
        rdcf_growth_gap=rdcf_gap,
        beneish_m_score=beneish_score,
        beneish_flagged=beneish_flag,
        price_momentum_180d=float(momentum_180d),
        data_quality_warnings=quality_warnings,
        cagr_window_used=len(pit.records),
        cagr_data_density=float(len(pit.records)) / 5.0
        if pit.records else 0.0,
    )


def _safe_conviction_score(
    ticker: str,
    calc_input,
    moat_score: Optional[float],
    upside_pct: float,
    ev_ebitda_gap_pct: float,
    roic: Optional[float],
    wacc_base: Optional[float],
    rdcf_implied_growth: Optional[float],
    rdcf_historical_cagr: Optional[float],
) -> float:
    """
    Compute conviction score by building a synthetic agent-state dict from
    deterministic signals. The full ConvictionScorer.score_from_state()
    expects fields populated by upstream agents; in a deterministic backtest
    we mirror the same fields with the values the calc layer already
    produced — same numerical inputs, just delivered via a different
    transport. Pillars whose inputs are LLM-only (operating_leverage_score,
    upstream_leak, strategic_leverage_score) fall through to neutral
    scoring.

    Returns the capped_total (range 5-25, or 0 on failure).
    """
    try:
        from aletheia.tools.conviction_scorer import ConvictionScorer

        # Build the synthetic state dict using the same key paths the
        # production scorer reads. See aletheia/tools/conviction_scorer.py
        # `score_from_state` for the source-of-truth schema.
        synthetic_state = {
            "forensic_report": {
                "moat_score": moat_score,
                # operating_leverage_score is LLM-only; leave None → P5 neutral
            },
            "value_chain_report": {
                # upstream_leak / strategic_leverage_score are LLM-only
            },
            "strategic_context_report": {
                # cyclicality_z and is_cyclical_peak require context agent;
                # for non-cyclical compounders this defaults to no penalty
                "applies_cyclical_haircut": False,
                "is_cyclical_peak": False,
            },
            "phase2_valuation": {
                "three_scenario_dcf": {
                    "base": {"margin_of_safety": float(upside_pct)},
                },
                "multiple_decomposition": {
                    "premium_pct": float(ev_ebitda_gap_pct),
                    "roic": float(roic) if roic is not None else None,
                    "value_creation": "creating" if (roic and wacc_base
                                                     and roic > wacc_base) else "neutral",
                },
                "wacc": float(wacc_base) if wacc_base is not None else None,
                "reverse_dcf": {
                    "implied_cagr_10y": float(rdcf_implied_growth)
                    if rdcf_implied_growth is not None else None,
                    "historical_cagr": float(rdcf_historical_cagr)
                    if rdcf_historical_cagr is not None else None,
                },
            },
            "strategist_report": {},
        }

        scorer = ConvictionScorer()
        result = scorer.score_from_state(
            ticker, state=synthetic_state, calc_input=calc_input
        )
        if result is None:
            return 0.0
        if hasattr(result, "capped_total") and result.capped_total:
            return float(result.capped_total)
        if hasattr(result, "raw_total") and result.raw_total:
            return float(result.raw_total)
        return 0.0
    except Exception:
        return 0.0


def _safe_moat_fingerprint(calc_input) -> tuple:
    """
    Returns (score, components_dict) — both Optional. None if insufficient
    history (< 3 years of clean data per the moat methodology).

    Components: roic_persistence (1-5), gm_stability (1-5), capex_intensity (1-5).
    """
    try:
        from aletheia.tools.moat_fingerprint import compute_moat_fingerprint
        mf = compute_moat_fingerprint(calc_input)
        if mf is None or mf.is_null_due_to_history:
            return None, None
        components = {
            "roic_persistence": float(mf.roic_persistence_score)
            if mf.roic_persistence_score is not None else None,
            "gm_stability": float(mf.gm_stability_score)
            if mf.gm_stability_score is not None else None,
            "capex_intensity": float(mf.capex_intensity_score)
            if mf.capex_intensity_score is not None else None,
        }
        return (float(mf.score) if mf.score is not None else None), components
    except Exception:
        return None, None


def _safe_reverse_dcf(
    calc_input,
    fiscal_year: int,
    historical_price: float,
    market_cap: float,
) -> tuple:
    """
    Returns (implied_cagr_10y, gap_vs_hist, historical_cagr_5y) — all
    Optional[float]. Reverse DCF gets passed historical price/market cap so
    it doesn't fall back to live yfinance values (would be lookahead bias).
    """
    try:
        from aletheia.tools.reverse_dcf import ReverseDCF
        engine = ReverseDCF(verbose=False)
        result = engine.run(
            calc_input,
            fiscal_year=fiscal_year,
            current_price=historical_price,
            market_cap=market_cap,
        )
        if result is None or not result.implied_revenue_cagr_10y:
            return None, None, None
        implied = float(result.implied_revenue_cagr_10y)
        hist = float(result.historical_cagr_5y) if result.historical_cagr_5y else None
        gap = (implied - hist) if hist is not None else None
        return implied, gap, hist
    except Exception:
        return None, None, None


def _safe_beneish(records, latest_record) -> tuple:
    """
    Returns (m_score, flagged_bool) — both Optional. Beneish needs a prior-year
    record; returns (None, None) if only one year of history available or if
    the screen errors out (often due to missing AccountsReceivable / DSRI input).
    """
    try:
        if len(records) < 2:
            return None, None
        prior = sorted(
            [r for r in records if r.fiscal_year < latest_record.fiscal_year],
            key=lambda r: r.fiscal_year,
        )
        if not prior:
            return None, None
        prior_record = prior[-1]
        from aletheia.data.quantitative_screens import QuantitativeScreens
        screens = QuantitativeScreens(verbose=False)
        result = screens.run_all(latest_record, prior_record=prior_record)
        if result is None or result.beneish is None:
            return None, None
        m = result.beneish.m_score
        flagged = result.beneish.is_flagged
        return (float(m) if m is not None else None,
                bool(flagged) if flagged is not None else None)
    except Exception:
        return None, None
