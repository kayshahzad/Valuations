"""
tests/benchmark_public_consensus.py

Runs the Aletheia DCFEngine for a basket of tickers and compares the outputs
against publicly available data (from yfinance) to highlight variance.
No hard thresholds are enforced; this is purely an analytical script to 
reveal discrepancies between our contrarian framework and Wall Street consensus.
"""

import sys
import os
import yfinance as yf
from dataclasses import dataclass

# Ensure aletheia is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from aletheia.tools.dcf_engine import DCFEngine

TICKERS = ["AAPL", "MSFT", "NVDA", "CNC"]

def format_var(val1, val2):
    if val2 and val2 != 0:
        var = (val1 - val2) / abs(val2)
        return f"{var:+.1%}"
    return "N/A"

def format_currency(val):
    if val is None: return "N/A"
    if abs(val) >= 1e9:
        return f"${val/1e9:.2f}B"
    elif abs(val) >= 1e6:
        return f"${val/1e6:.2f}M"
    return f"${val:.2f}"

def main():
    print("================================================================================")
    print("  Public Consensus Benchmarking (Aletheia vs Wall St)")
    print("================================================================================\n")
    
    engine = DCFEngine(verbose=False)
    
    for ticker in TICKERS:
        print(f"Analyzing {ticker}...")
        
        # 1. Run Aletheia DCF
        result = engine.run(ticker)
        if result.errors:
            print(f"  [ERROR] {result.errors[0]}\n")
            continue
            
        aletheia_beta = result.beta
        aletheia_base_iv = result.intrinsic_per_share(result.base.enterprise_value, result.net_debt) if result.base else None
        aletheia_bull_iv = result.intrinsic_per_share(result.bull.enterprise_value, result.net_debt) if result.bull else None
        aletheia_bear_iv = result.intrinsic_per_share(result.bear.enterprise_value, result.net_debt) if result.bear else None
        aletheia_ev_ebitda = result.base.implied_ev_ebitda if result.base else None
        
        # Base Data Metrics
        a_price = result.current_price
        a_shares = result.shares_diluted
        a_mktcap = result.market_cap
        a_rev = result.revenue
        a_ebitda = result.ebitda
        a_ebit = result.ebit
        a_fcf = result.fcf
        a_net_debt = result.net_debt
        a_roic = result.roic
        
        # 2. Fetch Public Consensus
        try:
            yf_ticker = yf.Ticker(ticker)
            info = yf_ticker.info
            
            public_beta = info.get("beta")
            public_mean_target = info.get("targetMeanPrice")
            public_high_target = info.get("targetHighPrice")
            public_low_target = info.get("targetLowPrice")
            public_ev_ebitda = info.get("enterpriseToEbitda")
            
            p_price = info.get("currentPrice") or info.get("previousClose")
            p_shares = info.get("impliedSharesOutstanding") or info.get("sharesOutstanding")
            p_mktcap = info.get("marketCap")
            p_rev = info.get("totalRevenue")
            p_ebitda = info.get("ebitda")
            p_margin = info.get("operatingMargins")
            p_ebit = (p_rev * p_margin) if (p_rev and p_margin) else None
            p_fcf = info.get("freeCashflow")
            
            p_debt = info.get("totalDebt")
            p_cash = info.get("totalCash")
            p_net_debt = (p_debt - p_cash) if (p_debt is not None and p_cash is not None) else None
            p_roe = info.get("returnOnEquity")
            
        except Exception as e:
            print(f"  [ERROR] Failed to fetch yfinance data: {e}\n")
            continue
            
        # 3. Print Comparison
        print(f"\n[{ticker}] Comparison Report:")
        print("-" * 75)
        print(f"{'Metric':<20} | {'Aletheia (DB)':<20} | {'Public (yfinance)':<20} | {'Variance'}")
        print("-" * 75)
        
        # Helper to print rows
        def print_row(name, aval, pval, fmt_func):
            a_str = fmt_func(aval) if aval is not None else "N/A"
            p_str = fmt_func(pval) if pval is not None else "N/A"
            v_str = format_var(aval, pval) if aval is not None and pval is not None else "N/A"
            print(f"{name:<20} | {a_str:<20} | {p_str:<20} | {v_str}")
            
        print("--- BASELINE METRICS ---")
        print_row("Current Price", a_price, p_price, lambda x: f"${x:.2f}")
        print_row("Shares Out", a_shares, p_shares, lambda x: f"{x/1e9:.2f}B")
        print_row("Market Cap", a_mktcap, p_mktcap, format_currency)
        print_row("Beta (5Y)", aletheia_beta, public_beta, lambda x: f"{x:.2f}")
        print_row("Revenue", a_rev, p_rev, format_currency)
        print_row("EBITDA", a_ebitda, p_ebitda, format_currency)
        print_row("EBIT (Operating)", a_ebit, p_ebit, format_currency)
        print_row("Free Cash Flow", a_fcf, p_fcf, format_currency)
        print_row("Net Debt", a_net_debt, p_net_debt, format_currency)
        print_row("ROIC vs ROE", a_roic, p_roe, lambda x: f"{x:.1%}")
        
        print("\n--- VALUATION DIVERGENCE ---")
        print_row("Base IV / Mean Tgt", aletheia_base_iv, public_mean_target, lambda x: f"${x:.2f}")
        print_row("Bull IV / High Tgt", aletheia_bull_iv, public_high_target, lambda x: f"${x:.2f}")
        print_row("Bear IV / Low Tgt", aletheia_bear_iv, public_low_target, lambda x: f"${x:.2f}")
        print_row("EV/EBITDA", aletheia_ev_ebitda, public_ev_ebitda, lambda x: f"{x:.1f}x")
        
        print("\n")

if __name__ == "__main__":
    main()
