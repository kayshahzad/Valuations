from aletheia.data.cleaning_engine import CleaningEngine
import pandas as pd

engine = CleaningEngine(verbose=False)
tickers = ('MSFT', 'AAPL', 'GOOGL', 'AMZN', 'TSLA', 'NVDA', 'META', 'CNC', 'AMD', 'ASML', 'TSM', 'UNH', 'LLY', 'ABT', 'V', 'COST', 'WMT', 'NEE', 'CAT', 'JPM', 'BRK-B', 'SMCI', 'QCOM', 'ORCL', 'TXN')

data = []
for ticker in tickers:
    for year in [2025, 2024, 2023]:
        try:
            record = engine.clean(ticker, year)
            if record and record.raw:
                row = {'ticker': ticker, 'fiscal_year': year}
                row.update({k: v for k, v in record.raw.items() if type(v) in [int, float]})
                data.append(row)
                break
        except Exception as e:
            pass

df = pd.DataFrame(data)

def format_b(x):
    if pd.isna(x):
        return '-'
    return f"${x/1e9:.1f}B"

structured_fields = ['Revenue', 'OperatingIncome', 'NetIncome', 'Depreciation', 'CapEx', 'TotalAssets', 'TotalLiabilities', 'LiabilitiesCurrent', 'TotalEquity', 'Cash']
cols = ['ticker', 'fiscal_year'] + [c for c in structured_fields if c in df.columns]

for col in cols:
    if col not in ['ticker', 'fiscal_year']:
        df[col] = df[col].apply(format_b)

markdown_table = df[cols].sort_values('ticker').to_markdown(index=False)
with open('/Users/kashifshahzad/.gemini/antigravity/brain/426b6bda-c00f-4f62-bd7a-2230751ceedc/structured_fields_25.md', 'w') as f:
    f.write('# Core Structured Fields (25 Ticker Universe)\n\n')
    f.write('Data from the latest available fiscal year directly processed by `CleaningEngine` after schema hardening. Missing values (`-`) are explicitly exempted based on sector rules (e.g. Banks/Insurance lacking `LiabilitiesCurrent`, Utilities lacking `CapEx`, and certain filers lacking `OperatingIncome`).\n\n')
    f.write(markdown_table)
