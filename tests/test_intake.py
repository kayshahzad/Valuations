from aletheia.agents.intake import intake_agent
from pprint import pprint

def test_intake():
    print("Testing Intake Agent...")
    state = {"ticker": "AAPL"}
    
    result = intake_agent(state)
    
    print("\n--- Result ---")
    pprint(result.get("messages"))
    
    if "serving_base" in result:
        base = result["serving_base"]
        print("\n--- Generated Base Truth ---")
        print(f"Revenue: {base['financials']['income_statement']['revenue']:,.0f}")
        print(f"Gross Margin: {base['ratios']['gross_margin']:.1%}")
        print(f"Meta: {base['meta']}")
    else:
        print("❌ Failed to generate serving base.")

if __name__ == "__main__":
    test_intake()
