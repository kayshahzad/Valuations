import os
import re

class ValuationConfig:
    def __init__(self):
        # Defaults
        self.tax_rate_global = 0.21
        self.wacc_floor = 0.09
        self.terminal_growth_cap = 0.03
        self.liquidity_ratio_safe = 1.5
        self.maturity_amortization_rate = 0.10
        self.double_leverage_threshold = 7.0
        self.stress_test_revenue_impact = 0.50
        self.stress_test_wacc_impact = 0.02

    def to_dict(self):
        return self.__dict__

def load_config(config_path="config/CONFIG.md"):
    """
    Parses CONFIG.md for key-value pairs in the markdown table.
    """
    config = ValuationConfig()
    
    if not os.path.exists(config_path):
        # Fallback to root
        if os.path.exists(f"../{config_path}"):
            config_path = f"../{config_path}"
        else:
            print("⚠️ CONFIG.md not found, using defaults.")
            return config

    try:
        with open(config_path, "r") as f:
            content = f.read()
            
        # Parse table rows: | `key` | value | desc |
        # Regex: \|\s*`(\w+)`\s*\|\s*([\d\.]+)\s*\|
        matches = re.findall(r"\|\s*`(\w+)`\s*\|\s*([\d\.]+)\s*\|", content)
        
        for key, value in matches:
            if hasattr(config, key):
                # Convert to float
                try:
                    setattr(config, key, float(value))
                except ValueError:
                    pass # Keep default
                    
    except Exception as e:
        print(f"Error parsing CONFIG.md: {e}")
        
    return config
