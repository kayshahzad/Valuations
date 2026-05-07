"""
aletheia/backtest/harness.py

Backtest orchestrator. For each (ticker, signal_date) pair on a quarterly
cadence, generate a point-in-time signal and pair it with forward returns
at the requested horizons. Returns a tidy DataFrame; writes to parquet.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import date
from pathlib import Path
from typing import List

import pandas as pd

from aletheia.backtest.outcome_tracker import compute_forward_return, warm_cache
from aletheia.backtest.signal_generator import BacktestSignal, generate_signal_at_date


def quarterly_dates(start: date, end: date) -> List[date]:
    """Generate the first business day of each calendar quarter in [start, end]."""
    out: List[date] = []
    current_month_starts = [1, 4, 7, 10]
    cursor = date(start.year, 1, 1)
    while cursor <= end:
        for m in current_month_starts:
            d = date(cursor.year, m, 1)
            # advance to next weekday if d falls on a weekend
            while d.weekday() >= 5:
                d = date(d.year, d.month, d.day + 1)
            if start <= d <= end:
                out.append(d)
        cursor = date(cursor.year + 1, 1, 1)
    return sorted(out)


def _signal_row(
    signal: BacktestSignal,
    horizons_months: List[int],
) -> dict:
    """Pack a BacktestSignal + forward returns into a flat dict row."""
    row = asdict(signal)
    # Replace list with a string (parquet-safe) and drop nested types
    row["data_quality_warnings"] = "; ".join(signal.data_quality_warnings) \
        if signal.data_quality_warnings else ""
    for h in horizons_months:
        ret = compute_forward_return(signal.ticker, signal.as_of_date, h)
        row[f"return_{h}m"] = ret  # may be None if horizon is in the future
    return row


def run_backtest(
    tickers: List[str],
    start_date: date,
    end_date: date,
    horizons_months: List[int] = (6, 12, 24),
    signals_to_compute: List[str] = None,
    output_path: str = "valuation_data/backtest/results.parquet",
    verbose: bool = True,
) -> pd.DataFrame:
    """
    For each (ticker, signal_date) pair on quarterly cadence:
      1. Generate signal as of that date (point-in-time cleaning)
      2. Compute forward returns at each horizon
      3. Append to results DataFrame
    Skip dates with no available filing or with insufficient history.
    Returns the assembled DataFrame; also writes to `output_path`.

    `signals_to_compute` is a hint for partial runs during development —
    currently the signal_generator always computes all signals (cheap
    relative to the point-in-time cleaning cost). The parameter is kept
    in the signature for forward compatibility.
    """
    del signals_to_compute  # forward-compat parameter, not yet wired
    # Pre-warm price cache so we hit yfinance once per ticker
    warm_cache(list(tickers))

    sig_dates = quarterly_dates(start_date, end_date)
    if verbose:
        print(f"\nBacktest plan: {len(tickers)} tickers × {len(sig_dates)} dates "
              f"= {len(tickers) * len(sig_dates)} max observations")

    rows = []
    skipped = 0
    for ticker in tickers:
        for d in sig_dates:
            if verbose:
                print(f"  [{ticker} @ {d}] generating signal...", end=" ")
            try:
                signal = generate_signal_at_date(ticker, d, verbose=False)
            except Exception as e:
                if verbose:
                    print(f"ERROR: {e}")
                skipped += 1
                continue
            if signal is None:
                if verbose:
                    print("skip (no data)")
                skipped += 1
                continue
            row = _signal_row(signal, list(horizons_months))
            rows.append(row)
            if verbose:
                up = signal.upside_pct * 100
                print(
                    f"FY{signal.fiscal_year_used}, "
                    f"price=${signal.current_price:.2f}, "
                    f"base IV=${signal.base_iv:.2f}, "
                    f"upside={up:+.0f}%, "
                    f"days_since_filing={signal.days_since_filing}"
                )

    if not rows:
        if verbose:
            print("No signals generated; nothing to write.")
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    if verbose:
        print(f"\nWrote {len(df)} rows ({skipped} skipped) → {out}")
    return df
