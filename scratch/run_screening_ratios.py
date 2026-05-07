import sys
from aletheia.tools.screening_ratios import ScreeningEngine

tickers = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "LLY", "ABT", "UNH", "V", "COST", "WMT", "CAT", "SMCI", "QCOM", "ASML", "TXN", "AMD", "CNC"]
engine = ScreeningEngine(verbose=False)
cards = engine.score_universe(tickers)
table_text = engine.universe_table(cards)

# Save as artifact
with open("/Users/kashifshahzad/.gemini/antigravity/brain/426b6bda-c00f-4f62-bd7a-2230751ceedc/screening_results.md", "w") as f:
    f.write("# Universe Screening Table\n\n")
    f.write("This table shows the 34-metric screening scorecard evaluating the universe against Graham, Lynch, Malkiel, and Liberti thresholds.\n\n")
    f.write("```text\n")
    f.write(table_text)
    f.write("\n```\n")

print("Screening results saved.")
