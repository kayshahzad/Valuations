"""
aletheia/backtest/calibration.py

Aggregates raw signal-outcome pairs into calibration tables. Three flavors:

1. signal_calibration_table: bucket signals into quintiles, report mean
   forward return + hit rate per bucket. Tells you whether higher signal
   values predict higher returns.

2. fundamental_vs_momentum_comparison: top/bottom-quintile spread for the
   fundamental signal vs the same for the momentum baseline. The diagnostic
   that matters: if `excess_spread` is near zero, the fundamental signal is
   reproducing momentum factors at higher cost.

3. staleness_calibration: bucket by days_since_filing, see whether signals
   decay as data ages. If they don't, the engine isn't actually using the
   data (it's just reading current price).
"""

from __future__ import annotations

from typing import List, Optional

import numpy as np
import pandas as pd


def _bucket_quintile(s: pd.Series, n_buckets: int = 5) -> pd.Series:
    """qcut with duplicate-edge handling (signals can have ties)."""
    try:
        return pd.qcut(s, n_buckets, labels=False, duplicates="drop")
    except ValueError:
        # All values identical or too few unique values
        return pd.Series(np.nan, index=s.index)


def signal_calibration_table(
    results: pd.DataFrame,
    signal_column: str,
    return_column: str,
    n_buckets: int = 5,
) -> pd.DataFrame:
    """
    Bucket `signal_column` into quintiles. For each bucket, report:
      - bucket index, mean signal value, count
      - mean forward return, hit rate (return > 0)
      - mean days_since_filing (staleness sanity check)
    """
    df = results.dropna(subset=[signal_column, return_column]).copy()
    if df.empty:
        return pd.DataFrame()
    df["_bucket"] = _bucket_quintile(df[signal_column], n_buckets=n_buckets)
    df = df.dropna(subset=["_bucket"])
    if df.empty:
        return pd.DataFrame()
    out = (
        df.groupby("_bucket")
        .agg(
            mean_signal=(signal_column, "mean"),
            mean_return=(return_column, "mean"),
            hit_rate=(return_column, lambda s: float((s > 0).mean())),
            count=(return_column, "size"),
            mean_days_since_filing=("days_since_filing", "mean"),
        )
        .reset_index()
        .rename(columns={"_bucket": "bucket"})
    )
    out["bucket"] = out["bucket"].astype(int)
    return out.sort_values("bucket").reset_index(drop=True)


def fundamental_vs_momentum_comparison(
    results: pd.DataFrame,
    fundamental_column: str = "upside_pct",
    momentum_column: str = "price_momentum_180d",
    return_column: str = "return_12m",
    n_buckets: int = 5,
) -> pd.DataFrame:
    """
    Compute top-quintile vs bottom-quintile spread for fundamental and
    momentum signals separately, then report the excess spread.

    excess_spread > 0  → fundamental signal carries info beyond momentum
    excess_spread <= 0 → engine is reproducing momentum factors at higher cost
    """
    fund = signal_calibration_table(
        results, fundamental_column, return_column, n_buckets=n_buckets
    )
    mom = signal_calibration_table(
        results, momentum_column, return_column, n_buckets=n_buckets
    )
    if fund.empty or mom.empty:
        return pd.DataFrame()

    fund_top = float(fund.iloc[-1]["mean_return"])
    fund_bot = float(fund.iloc[0]["mean_return"])
    mom_top = float(mom.iloc[-1]["mean_return"])
    mom_bot = float(mom.iloc[0]["mean_return"])
    fund_spread = fund_top - fund_bot
    mom_spread = mom_top - mom_bot

    return pd.DataFrame([{
        "top_quintile_fundamental_return": fund_top,
        "bottom_quintile_fundamental_return": fund_bot,
        "fundamental_spread": fund_spread,
        "top_quintile_momentum_return": mom_top,
        "bottom_quintile_momentum_return": mom_bot,
        "momentum_spread": mom_spread,
        "excess_spread": fund_spread - mom_spread,
        "n_observations": int(min(fund["count"].sum(), mom["count"].sum())),
    }])


def calibrate_signal(
    results: pd.DataFrame,
    signal_column: str,
    return_column: str = "return_12m",
    n_buckets: int = 5,
    bucket_method: str = "quintile",
) -> pd.DataFrame:
    """
    Generic calibration table for any signal column. Same shape as
    `signal_calibration_table` but parameterized to support fixed-threshold
    bucketing (currently mirrors quintile; reserved for future binary
    signals like beneish_flagged).
    """
    if bucket_method == "quintile":
        return signal_calibration_table(results, signal_column, return_column, n_buckets)
    if bucket_method == "binary":
        df = results.dropna(subset=[signal_column, return_column]).copy()
        if df.empty:
            return pd.DataFrame()
        df["_bucket"] = df[signal_column].astype(bool).astype(int)
        out = (
            df.groupby("_bucket")
            .agg(
                mean_signal=(signal_column, "mean"),
                mean_return=(return_column, "mean"),
                hit_rate=(return_column, lambda s: float((s > 0).mean())),
                count=(return_column, "size"),
                mean_days_since_filing=("days_since_filing", "mean"),
            )
            .reset_index()
            .rename(columns={"_bucket": "bucket"})
        )
        return out.sort_values("bucket").reset_index(drop=True)
    raise ValueError(f"Unknown bucket_method: {bucket_method}")


def multi_signal_comparison(
    results: pd.DataFrame,
    signal_columns: List[str],
    return_column: str = "return_12m",
    momentum_column: str = "price_momentum_180d",
    n_buckets: int = 5,
) -> pd.DataFrame:
    """
    Side-by-side spread comparison across all signals. For each signal:
      - top_bucket_return, bottom_bucket_return, spread (top − bottom)
      - spread_vs_momentum: signal spread − momentum spread (the edge metric)
    Returns DataFrame with one row per signal, sorted by spread_vs_momentum.
    """
    # Reference: momentum baseline spread
    mom = signal_calibration_table(results, momentum_column, return_column, n_buckets)
    if mom.empty:
        momentum_spread = 0.0
    else:
        momentum_spread = float(mom.iloc[-1]["mean_return"] - mom.iloc[0]["mean_return"])

    rows = []
    for col in signal_columns:
        if col not in results.columns:
            continue
        cal = signal_calibration_table(results, col, return_column, n_buckets)
        if cal.empty:
            rows.append({
                "signal_name": col,
                "top_bucket_return": None,
                "bottom_bucket_return": None,
                "spread": None,
                "spread_vs_momentum": None,
                "n_observations": 0,
            })
            continue
        top = float(cal.iloc[-1]["mean_return"])
        bot = float(cal.iloc[0]["mean_return"])
        spread = top - bot
        rows.append({
            "signal_name": col,
            "top_bucket_return": top,
            "bottom_bucket_return": bot,
            "spread": spread,
            "spread_vs_momentum": spread - momentum_spread,
            "n_observations": int(cal["count"].sum()),
        })
    out = pd.DataFrame(rows)
    if not out.empty and "spread_vs_momentum" in out.columns:
        out = out.sort_values("spread_vs_momentum", ascending=False, na_position="last")
    return out.reset_index(drop=True)


def signal_correlation_matrix(
    results: pd.DataFrame,
    signal_columns: List[str],
) -> pd.DataFrame:
    """
    Pearson correlation matrix among the named signal columns. Drops columns
    that are entirely null. Pairs with |r| < 0.3 carry independent
    information and could be combined.
    """
    df = results.copy()
    cols = [c for c in signal_columns if c in df.columns and df[c].notna().any()]
    if len(cols) < 2:
        return pd.DataFrame()
    return df[cols].corr(method="pearson", numeric_only=True)


def staleness_calibration(
    results: pd.DataFrame,
    return_column: str = "return_12m",
    bucket_edges: Optional[list] = None,
) -> pd.DataFrame:
    """
    Bucket signals by days_since_filing. Default buckets: [0, 90, 180, 270, 365, 730].
    For each bucket, report mean return + hit rate + sample count.
    Tests whether signals decay as data ages.
    """
    if bucket_edges is None:
        bucket_edges = [0, 90, 180, 270, 365, 730]
    df = results.dropna(subset=[return_column, "days_since_filing"]).copy()
    if df.empty:
        return pd.DataFrame()
    df["_staleness_bucket"] = pd.cut(
        df["days_since_filing"], bins=bucket_edges, right=False, include_lowest=True,
    )
    out = (
        df.groupby("_staleness_bucket", observed=True)
        .agg(
            mean_return=(return_column, "mean"),
            hit_rate=(return_column, lambda s: float((s > 0).mean())),
            count=(return_column, "size"),
            mean_upside_pct=("upside_pct", "mean"),
        )
        .reset_index()
        .rename(columns={"_staleness_bucket": "days_since_filing"})
    )
    out["days_since_filing"] = out["days_since_filing"].astype(str)
    return out
