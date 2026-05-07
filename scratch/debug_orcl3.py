from config.ticker_classification import UNIVERSE
from aletheia.data.database import InvestmentDatabase
from aletheia.tools.dcf_engine import DCFEngine
from aletheia.contracts.interfaces import CalculationInput, ValuationProfile
from config.valuation_defaults import LIFECYCLE_PROFILES
from config.known_issues import KNOWN_ISSUES

ticker = "ORCL"
db = InvestmentDatabase(verbose=False)
latest_df = db.get_latest(ticker)
classification = UNIVERSE[ticker]
lifecycle = classification.lifecycle
profile_cfg = LIFECYCLE_PROFILES[lifecycle]
vp = ValuationProfile(growth_rate=profile_cfg.growth_rate, terminal_growth=profile_cfg.terminal_growth, forecast_years=profile_cfg.forecast_years, terminal_margin_decay=profile_cfg.terminal_margin_decay)
calc_input = CalculationInput(df=latest_df, classification=classification, known_issues=KNOWN_ISSUES.get(ticker, []), valuation_profile=vp)
engine = DCFEngine(verbose=True)
result = engine.run(calc_input)
print("Base IV:", result.intrinsic_per_share(result.base.enterprise_value, result.net_debt))
print("Base TV used:", result.base.terminal.tv_used)
print("Gordon TV:", result.base.terminal.gordon_tv)
print("Liberti TV:", result.base.terminal.reinvestment_tv)
