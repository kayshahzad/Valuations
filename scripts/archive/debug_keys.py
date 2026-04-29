import yfinance as yf
import json

ticker = yf.Ticker("NVDA")
income_stmt = ticker.income_stmt
if income_stmt is not None and not income_stmt.empty:
    print("Keys found in yfinance income_stmt:")
    for idx in income_stmt.index:
        print(f" - {idx}")
else:
    print("No income statement found.")
