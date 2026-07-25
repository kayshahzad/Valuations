import pandas as pd
import os
from aletheia.utils.config import load_config as load_base_config, ValuationConfig

def load_valuation_config(config_md_path="config/CONFIG.md", universe_path="config/universe.csv", fred_path="config/fred_series.csv") -> ValuationConfig:
    """
    Loads the comprehensive ValuationConfig object.
    1. Parses CONFIG.md for global assumptions.
    2. Loads target tickers from config/universe.csv.
    3. Loads macro series IDs from config/fred_series.csv.
    """
    # Load base assumptions from CONFIG.md
    config = load_base_config(config_md_path)
    
    # Load Universe
    config.tickers = []
    config.target_tickers = []
    
    # Fallback to root if path doesn't exist relative to current dir
    if not os.path.exists(universe_path) and os.path.exists(f"../{universe_path}"):
        universe_path = f"../{universe_path}"
        
    if os.path.exists(universe_path):
        try:
            df = pd.read_csv(universe_path)
            # Ensure strip whitespace from headers
            df.columns = df.columns.str.strip()
            config.tickers = df.to_dict(orient="records")
            if "ticker" in df.columns:
                config.target_tickers = df["ticker"].tolist()
        except Exception as e:
            print(f"Error loading universe.csv: {e}")
    else:
        print(f"⚠️ Warning: Universe file not found at {universe_path}")
            
    # Load FRED Series
    config.fred_series = []
    config.fred_ids = []
    
    if not os.path.exists(fred_path) and os.path.exists(f"../{fred_path}"):
        fred_path = f"../{fred_path}"
        
    if os.path.exists(fred_path):
        try:
            df = pd.read_csv(fred_path)
            df.columns = df.columns.str.strip()
            config.fred_series = df.to_dict(orient="records")
            if "series_id" in df.columns:
                config.fred_ids = df["series_id"].tolist()
        except Exception as e:
            print(f"Error loading fred_series.csv: {e}")
    else:
        print(f"⚠️ Warning: FRED Series file not found at {fred_path}")
            
    return config
