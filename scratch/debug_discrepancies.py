from aletheia.tools.dcf_engine import DCFEngine
from aletheia.tools.multiple_decomposition import MultipleDecomposition
from aletheia.tools.reverse_dcf import ReverseDCF
from aletheia.data.database import InvestmentDatabase

ticker = "LLY"

# ROIC
dcf_result = DCFEngine(verbose=False).run(ticker)
md_result = MultipleDecomposition(verbose=False).run(ticker)

dcf_roic = dcf_result.base.assumptions.roic_terminal
md_roic = md_result.roic
print(f"{ticker} ROIC: DCF uses {dcf_roic:.4f}, MD uses {md_roic:.4f}")

# EV
rdcf_result = ReverseDCF(verbose=False).run(ticker)
current_ev_rdcf = rdcf_result.current_ev

db = InvestmentDatabase(verbose=False)
df = db.get_latest(ticker)
db.close()

fy = int(df["fiscal_year"].max())
row = df[df["fiscal_year"] == fy].iloc[0]
market_cap = float(row.get("current_market_cap", 0))
net_debt = float(row.get("clean_NetDebt", 0))
expected_ev = market_cap + net_debt

print(f"{ticker} EV: ReverseDCF uses {current_ev_rdcf}, DB has MC={market_cap} NetDebt={net_debt} -> {expected_ev}")
