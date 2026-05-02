from dataclasses import dataclass
from typing import Literal, Dict
from datetime import date

@dataclass(frozen=True)
class TickerClassification:
    ticker: str
    sector: str
    industry: str
    lifecycle: Literal["secular_hyper_growth", "hyper_growth", "high_growth_compounder", 
                       "growth_compounder", "mature", "cyclical_industrial"]
    business_model: Literal["fcff_compatible", "ddm_required", "embedded_value_required", "routing_required"]
    notes: str
    last_reviewed: date

    # Filing taxonomy. True for 20-F filers using IFRS (TSM, ASML in current
    # universe). The TagResolver uses this to select IFRS priority lists in
    # FIELD_MAPPINGS instead of US-GAAP defaults. Defaults to False because
    # the universe is overwhelmingly US-GAAP; mark as True per-ticker only
    # when the filer's primary taxonomy is IFRS.
    is_ifrs_filer: bool = False

UNIVERSE: Dict[str, TickerClassification] = {
    # --- Technology / Software / Hardware ---
    "MSFT": TickerClassification("MSFT", "Technology", "Software", "growth_compounder", "fcff_compatible", "Mature compounder, AAA-rated, high ROIC", date.today()),
    "AAPL": TickerClassification("AAPL", "Technology", "Hardware", "growth_compounder", "fcff_compatible", "Hardware/service ecosystem", date.today()),
    "GOOGL": TickerClassification("GOOGL", "Technology", "Internet", "growth_compounder", "fcff_compatible", "Ads, cloud, GDP-linked", date.today()),
    "META": TickerClassification("META", "Technology", "Internet", "high_growth_compounder", "fcff_compatible", "Ads, slowing from hyper-growth to high-growth compounder", date.today()),
    "AMZN": TickerClassification("AMZN", "Technology", "E-Commerce/Cloud", "high_growth_compounder", "fcff_compatible", "AWS structural growth + retail GDP", date.today()),
    "ORCL": TickerClassification("ORCL", "Technology", "Software", "mature", "fcff_compatible", "Legacy enterprise software transitioning to cloud", date.today()),
    "SMCI": TickerClassification("SMCI", "Technology", "Hardware", "hyper_growth", "fcff_compatible", "Server hardware / AI capex. High growth but quality concerns", date.today()),

    # --- Semiconductors ---
    "NVDA": TickerClassification("NVDA", "Semiconductors", "Semiconductors", "secular_hyper_growth", "fcff_compatible", "Current-cycle AI capex linked", date.today()),
    "AMD": TickerClassification("AMD", "Semiconductors", "Semiconductors", "secular_hyper_growth", "fcff_compatible", "Current-cycle AI capex linked", date.today()),
    "ASML": TickerClassification("ASML", "Semiconductors", "Semiconductor Equipment", "high_growth_compounder", "fcff_compatible", "Semi capital equipment, AI structural growth", date.today(), is_ifrs_filer=True),
    "TSM": TickerClassification("TSM", "Semiconductors", "Semiconductors", "high_growth_compounder", "fcff_compatible", "Foundry, AI structural growth", date.today(), is_ifrs_filer=True),
    "QCOM": TickerClassification("QCOM", "Semiconductors", "Semiconductors", "mature", "fcff_compatible", "Mobile comms maturity", date.today()),
    "TXN": TickerClassification("TXN", "Semiconductors", "Semiconductors", "mature", "fcff_compatible", "Analog semi maturity", date.today()),

    # --- Auto ---
    "TSLA": TickerClassification("TSLA", "Auto Manufacturers", "Automotive", "high_growth_compounder", "fcff_compatible", "Auto manufacturing (rate sensitive). Slowing hyper-growth", date.today()),

    # --- Healthcare ---
    "UNH": TickerClassification("UNH", "Healthcare", "Managed Care", "mature", "ddm_required", "Float-based business; FCFF DCF inappropriate", date.today()),
    "CNC": TickerClassification("CNC", "Healthcare Plans", "Managed Care", "mature", "ddm_required", "Float-based business; FCFF DCF inappropriate", date.today()),
    "LLY": TickerClassification("LLY", "Healthcare", "Pharmaceuticals", "growth_compounder", "fcff_compatible", "Structural GLP-1 growth", date.today()),
    "ABT": TickerClassification("ABT", "Healthcare", "Medical Devices", "mature", "fcff_compatible", "Diversified healthcare mature", date.today()),

    # --- Financials ---
    "V": TickerClassification("V", "Financials", "Payments", "growth_compounder", "fcff_compatible", "Payments toll-bridge", date.today()),
    "JPM": TickerClassification("JPM", "Financials", "Banks", "mature", "routing_required", "Financial bank requiring specialized model", date.today()),
    "BRK-B": TickerClassification("BRK-B", "Financials", "Diversified", "mature", "routing_required", "Conglomerate/Insurer requiring specialized model", date.today()),

    # --- Consumer Defensive / Retail ---
    "COST": TickerClassification("COST", "Consumer Defensive", "Retail", "growth_compounder", "fcff_compatible", "Consumer defensive compounder", date.today()),
    "WMT": TickerClassification("WMT", "Consumer Defensive", "Retail", "mature", "fcff_compatible", "Consumer defensive mature", date.today()),

    # --- Industrials ---
    "CAT": TickerClassification("CAT", "Industrials", "Heavy Machinery", "cyclical_industrial", "fcff_compatible", "Heavy machinery, peak cycle distortion", date.today()),

    # --- Utilities ---
    "NEE": TickerClassification("NEE", "Utilities", "Utilities", "mature", "routing_required", "Regulated utility. CapEx non-standard mapping", date.today()),
}
