import yfinance as yf
import pandas as pd
from typing import Dict, Any

def get_financial_metrics(ticker: str) -> Dict[str, Any]:
    """
    Fetches key financial metrics for a given ticker using yfinance.
    """
    stock = yf.Ticker(ticker)
    info = stock.info
    
    # Fundamental data
    financials = stock.financials
    balance_sheet = stock.balance_sheet
    cashflow = stock.cashflow
    
    # Calculate TTM (Trailing Twelve Months) metrics roughly if needed or rely on 'info'
    
    data = {
        "ticker": ticker,
        "company_name": info.get("longName"),
        "sector": info.get("sector"),
        "industry": info.get("industry"),
        "market_cap": info.get("marketCap"),
        "total_revenue": info.get("totalRevenue"), # Added for 3-Stage DCF
        "pe_ratio": info.get("trailingPE"),
        "forward_pe": info.get("forwardPE"),
        "beta": info.get("beta"),
        "price_to_book": info.get("priceToBook"),
        "earnings_growth": info.get("earningsGrowth"),
        "revenue_growth": info.get("revenueGrowth"),
        "profit_margins": info.get("profitMargins"),
        "free_cashflow": info.get("freeCashflow"),
        "target_price": info.get("targetMeanPrice"),
        "current_price": info.get("currentPrice"),
        "debt_to_equity": info.get("debtToEquity"),
        "return_on_equity": info.get("returnOnEquity"),
    }
    return data

def get_financial_statements(ticker: str) -> Dict[str, str]:
    """
    Returns the core financial statements as JSON/Dict strings.
    """
    stock = yf.Ticker(ticker)
    return {
        "income_statement": stock.financials.to_json(),
        "balance_sheet": stock.balance_sheet.to_json(),
        "cash_flow": stock.cashflow.to_json()
    }

def get_market_rates() -> Dict[str, float]:
    """
    Fetches market risk-free rate (^TNX) and defines ERP.
    """
    try:
        # 10-Year Treasury Yield
        tnx = yf.Ticker("^TNX")
        # 'regularMarketPrice' or 'previousClose' often works.
        # fast_info might be faster
        rf_percent = tnx.fast_info.last_price
        if not rf_percent:
            hist = tnx.history(period="1d")
            rf_percent = hist["Close"].iloc[-1]
            
        risk_free_rate = rf_percent / 100.0
    except Exception as e:
        print(f"Error fetching ^TNX: {e}")
        risk_free_rate = 0.04 # Fallback 4.0%
        
    return {
        "risk_free_rate": risk_free_rate,
        "equity_risk_premium": 0.05 # Standard 5% Assumption
    }
