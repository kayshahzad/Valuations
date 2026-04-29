"""
fix_tag_lookups.py
==================
Fixes tag name mismatches between tag_resolver.py output (short names)
and cleaning engine domain lookups (long XBRL names).

The tag resolver stores:
  "SBC"                 ← but domains look up "ShareBasedCompensation"
  "AccountsReceivable"  ← but D8 looks up "AccountsReceivableNetCurrent"
  "DeferredRevenue"     ← but D8 looks up "DeferredRevenueCurrent"
  "OperatingCF"         ← but D9/derived looks up "NetCashProvidedByUsedInOperatingActivities"
  "InvestingCF"         ← but Sloan looks up "NetCashProvidedByUsedInInvestingActivities"
  "NetIncome"           ← correctly named (works already)
  "CapEx"               ← but D9 looks up "PaymentsToAcquirePropertyPlantAndEquipment"
  "Buybacks"            ← but D7 looks up "PaymentsForRepurchaseOfCommonStock"
  "SharesDiluted"       ← but D7 looks up "WeightedAverageNumberOfDilutedSharesOutstanding"
  "SharesBasic"         ← but D7 looks up "CommonStockSharesOutstanding"
  "CurrentAssets"       ← but D9 looks up "AssetsCurrent"
  "CurrentLiabilities"  ← but D9/D10 look up "LiabilitiesCurrent"
  "CashTaxesPaid"       ← but D10 looks up "IncomeTaxesPaidNet"

Strategy: add alias lookups in each domain — try short name first, fall back to long XBRL name.
This makes the cleaning engine robust to both naming conventions without breaking anything.

Run from project root:
    PYTHONPATH=. python3 fix_tag_lookups.py
"""

from pathlib import Path

def patch(path: str, old: str, new: str, label: str) -> bool:
    p = Path(path)
    content = p.read_text()
    if old not in content:
        print(f"  ⚠ Not found: {label}")
        return False
    p.write_text(content.replace(old, new, 1))
    print(f"  ✓ Fixed: {label}")
    return True


TARGET = "aletheia/data/cleaning_engine.py"
print(f"Patching {TARGET}")
print("=" * 60)


# ── Fix 1: Domain 7 — SBC tag lookup ─────────────────────────────────────────
patch(TARGET,
    old='        sbc = record.raw.get("ShareBasedCompensation") or 0.0',
    new='        sbc = record.raw.get("SBC") or record.raw.get("ShareBasedCompensation") or 0.0',
    label="D7 SBC tag lookup"
)

# ── Fix 2: Domain 7 — Buybacks tag lookup ────────────────────────────────────
patch(TARGET,
    old='        buybacks = record.raw.get("PaymentsForRepurchaseOfCommonStock") or 0.0',
    new='        buybacks = record.raw.get("Buybacks") or record.raw.get("PaymentsForRepurchaseOfCommonStock") or 0.0',
    label="D7 Buybacks tag lookup"
)

# ── Fix 3: Domain 7 — Shares diluted tag lookup ──────────────────────────────
patch(TARGET,
    old='        shares_basic = record.raw.get("CommonStockSharesOutstanding")\n        shares_diluted = record.raw.get("WeightedAverageNumberOfDilutedSharesOutstanding")',
    new='        shares_basic = record.raw.get("SharesOutstanding") or record.raw.get("CommonStockSharesOutstanding")\n        shares_diluted = record.raw.get("SharesDiluted") or record.raw.get("WeightedAverageNumberOfDilutedSharesOutstanding")',
    label="D7 Shares tag lookups"
)

# ── Fix 4: Domain 8 — AccountsReceivable tag lookup ──────────────────────────
patch(TARGET,
    old='        ar = record.raw.get("AccountsReceivableNetCurrent") or 0.0',
    new='        ar = record.raw.get("AccountsReceivable") or record.raw.get("AccountsReceivableNetCurrent") or 0.0',
    label="D8 AccountsReceivable tag lookup"
)

# ── Fix 5: Domain 8 — DeferredRevenue tag lookup ─────────────────────────────
patch(TARGET,
    old='        deferred_rev = record.raw.get("DeferredRevenueCurrent") or 0.0',
    new='        deferred_rev = record.raw.get("DeferredRevenue") or record.raw.get("DeferredRevenueCurrent") or 0.0',
    label="D8 DeferredRevenue tag lookup"
)

# ── Fix 6: Domain 8 — OperatingCF tag lookup ─────────────────────────────────
patch(TARGET,
    old='        cash_ops = record.raw.get("NetCashProvidedByUsedInOperatingActivities") or 0.0\n\n        record.clean["AccountsReceivable"] = ar',
    new='        cash_ops = record.raw.get("OperatingCF") or record.raw.get("NetCashProvidedByUsedInOperatingActivities") or 0.0\n\n        record.clean["AccountsReceivable"] = ar',
    label="D8 OperatingCF tag lookup"
)

# ── Fix 7: Domain 8 prior year AR lookup ─────────────────────────────────────
patch(TARGET,
    old='            prior_ar = prior.raw.get("AccountsReceivableNetCurrent") or 0.0\n            prior_deferred = prior.raw.get("DeferredRevenueCurrent") or 0.0',
    new='            prior_ar = prior.raw.get("AccountsReceivable") or prior.raw.get("AccountsReceivableNetCurrent") or 0.0\n            prior_deferred = prior.raw.get("DeferredRevenue") or prior.raw.get("DeferredRevenueCurrent") or 0.0',
    label="D8 prior year AR and DeferredRevenue lookups"
)

# ── Fix 8: Domain 9 — CurrentAssets tag lookup ───────────────────────────────
patch(TARGET,
    old='        current_assets = record.raw.get("AssetsCurrent") or 0.0\n        current_liab = record.raw.get("LiabilitiesCurrent") or 0.0',
    new='        current_assets = record.raw.get("CurrentAssets") or record.raw.get("AssetsCurrent") or 0.0\n        current_liab = record.raw.get("CurrentLiabilities") or record.raw.get("LiabilitiesCurrent") or 0.0',
    label="D9 CurrentAssets and CurrentLiabilities tag lookups"
)

# ── Fix 9: Domain 9 — CapEx tag lookup ───────────────────────────────────────
patch(TARGET,
    old='        capex = record.raw.get("capex") or record.raw.get("PaymentsToAcquirePropertyPlantAndEquipment") or 0.0',
    new='        capex = record.raw.get("CapEx") or record.raw.get("capex") or record.raw.get("PaymentsToAcquirePropertyPlantAndEquipment") or 0.0',
    label="D9 CapEx tag lookup"
)

# ── Fix 10: Domain 9 — OperatingCF tag lookup ────────────────────────────────
patch(TARGET,
    old='        cash_ops = record.raw.get("NetCashProvidedByUsedInOperatingActivities") or 0.0\n        record.clean["NWC"] = nwc',
    new='        cash_ops = record.raw.get("OperatingCF") or record.raw.get("NetCashProvidedByUsedInOperatingActivities") or 0.0\n        record.clean["NWC"] = nwc',
    label="D9 OperatingCF tag lookup (NWC section)"
)

# ── Fix 11: Domain 10 — CurrentLiabilities tag lookup ────────────────────────
patch(TARGET,
    old='            current_liab = r.raw.get("LiabilitiesCurrent") or 0.0',
    new='            current_liab = r.raw.get("CurrentLiabilities") or r.raw.get("LiabilitiesCurrent") or 0.0',
    label="Derived InvestedCapital CurrentLiabilities lookup"
)

# ── Fix 12: Domain 10 — CashTaxesPaid tag lookup ─────────────────────────────
patch(TARGET,
    old='        cash_taxes = record.raw.get("IncomeTaxesPaid") or record.raw.get("IncomeTaxesPaidNet") or 0.0',
    new='        cash_taxes = record.raw.get("CashTaxesPaid") or record.raw.get("IncomeTaxesPaid") or record.raw.get("IncomeTaxesPaidNet") or 0.0',
    label="D10 CashTaxesPaid tag lookup"
)

# ── Fix 13: Derived metrics — OperatingCF for FCF ────────────────────────────
patch(TARGET,
    old='        cash_ops = record.raw.get("NetCashProvidedByUsedInOperatingActivities") or 0.0\n\n        # FCF = Operating CF - CapEx',
    new='        cash_ops = record.raw.get("OperatingCF") or record.raw.get("NetCashProvidedByUsedInOperatingActivities") or 0.0\n\n        # FCF = Operating CF - CapEx',
    label="Derived FCF OperatingCF lookup"
)

# ── Fix 14: Derived metrics — CapEx ──────────────────────────────────────────
patch(TARGET,
    old='        capex = r.clean.get("CapEx_Total") or 0.0',
    new='        capex = r.clean.get("CapEx_Total") or r.raw.get("CapEx") or 0.0',
    label="Derived FCF CapEx fallback"
)

# ── Fix 15: Quantitative screens — Sloan InvestingCF ─────────────────────────
# The Sloan screen in quantitative_screens.py also needs this fix
TARGET2 = "aletheia/data/quantitative_screens.py"
print(f"\nPatching {TARGET2}")
print("=" * 60)

patch(TARGET2,
    old='        cash_inv = record.raw.get("NetCashProvidedByUsedInInvestingActivities")',
    new='        cash_inv = record.raw.get("InvestingCF") or record.raw.get("NetCashProvidedByUsedInInvestingActivities")',
    label="Sloan InvestingCF tag lookup"
)

patch(TARGET2,
    old='        cash_ops = record.raw.get("NetCashProvidedByUsedInOperatingActivities")',
    new='        cash_ops = record.raw.get("OperatingCF") or record.raw.get("NetCashProvidedByUsedInOperatingActivities")',
    label="Sloan OperatingCF tag lookup"
)

patch(TARGET2,
    old='        net_income = record.raw.get("NetIncome")',
    new='        net_income = record.raw.get("NetIncome") or record.raw.get("NetIncomeLoss")',
    label="Sloan NetIncome tag lookup"
)

# Beneish screen tag lookups
patch(TARGET2,
    old='''        # Current year values
        revenue = get(record, "Revenue")
        ar = get(record, "AccountsReceivableNetCurrent")
        cogs = get(record, "COGS", "CostOfRevenue", "CostOfGoodsAndServicesSold")''',
    new='''        # Current year values
        revenue = get(record, "Revenue")
        ar = get(record, "AccountsReceivable", "AccountsReceivableNetCurrent")
        cogs = get(record, "COGS", "CostOfRevenue", "CostOfGoodsAndServicesSold")''',
    label="Beneish AR tag lookup"
)

patch(TARGET2,
    old='''        p_revenue = get(prior, "Revenue")
        p_ar = get(prior, "AccountsReceivableNetCurrent")''',
    new='''        p_revenue = get(prior, "Revenue")
        p_ar = get(prior, "AccountsReceivable", "AccountsReceivableNetCurrent")''',
    label="Beneish prior AR tag lookup"
)

patch(TARGET2,
    old='        cash_ops = get(record, "NetCashProvidedByUsedInOperatingActivities")',
    new='        cash_ops = get(record, "OperatingCF", "NetCashProvidedByUsedInOperatingActivities")',
    label="Beneish OperatingCF tag lookup"
)

patch(TARGET2,
    old='        net_income = get(record, "NetIncome")',
    new='        net_income = get(record, "NetIncome", "NetIncomeLoss")',
    label="Beneish NetIncome tag lookup"
)

patch(TARGET2,
    old='        pp_and_e = get(record, "PropertyPlantAndEquipmentNet")',
    new='        pp_and_e = get(record, "PPE", "PropertyPlantAndEquipmentNet")',
    label="Beneish PPE tag lookup"
)

patch(TARGET2,
    old='        p_pp_and_e = get(prior, "PropertyPlantAndEquipmentNet")',
    new='        p_pp_and_e = get(prior, "PPE", "PropertyPlantAndEquipmentNet")',
    label="Beneish prior PPE tag lookup"
)

patch(TARGET2,
    old='        long_term_debt = get(record, "LongTermDebt")',
    new='        long_term_debt = get(record, "LongTermDebt", "LongTermDebtNoncurrent")',
    label="Beneish LongTermDebt tag lookup"
)

patch(TARGET2,
    old='        p_long_term_debt = get(prior, "LongTermDebt")',
    new='        p_long_term_debt = get(prior, "LongTermDebt", "LongTermDebtNoncurrent")',
    label="Beneish prior LongTermDebt tag lookup"
)

patch(TARGET2,
    old='        current_assets = get(record, "AssetsCurrent")',
    new='        current_assets = get(record, "CurrentAssets", "AssetsCurrent")',
    label="Beneish CurrentAssets tag lookup"
)

patch(TARGET2,
    old='        p_current_assets = get(prior, "AssetsCurrent")',
    new='        p_current_assets = get(prior, "CurrentAssets", "AssetsCurrent")',
    label="Beneish prior CurrentAssets tag lookup"
)

patch(TARGET2,
    old='        current_liab = get(record, "LiabilitiesCurrent")',
    new='        current_liab = get(record, "CurrentLiabilities", "LiabilitiesCurrent")',
    label="Beneish CurrentLiabilities tag lookup"
)

patch(TARGET2,
    old='        p_current_liab = get(prior, "LiabilitiesCurrent")',
    new='        p_current_liab = get(prior, "CurrentLiabilities", "LiabilitiesCurrent")',
    label="Beneish prior CurrentLiabilities tag lookup"
)

patch(TARGET2,
    old='        total_assets = get(record, "TotalAssets")',
    new='        total_assets = get(record, "TotalAssets", "Assets")',
    label="Beneish TotalAssets tag lookup"
)

patch(TARGET2,
    old='        p_total_assets = get(prior, "TotalAssets")',
    new='        p_total_assets = get(prior, "TotalAssets", "Assets")',
    label="Beneish prior TotalAssets tag lookup"
)

print("\n" + "=" * 60)
print("All patches applied.")
print()
print("Next steps:")
print("  1. Delete database and re-run:")
print("     rm -f valuation_data/database/investment.duckdb")
print("     PYTHONPATH=. python3 aletheia/data/database.py AAPL CNC")
print()
print("  2. Verify FCF, SBC, and screens now populate:")
print("""     PYTHONPATH=. python3 -c "
from aletheia.data.database import InvestmentDatabase
import json
db = InvestmentDatabase()

# Check FCF and SBC
df = db.get_latest('AAPL')
cols = ['fiscal_year','clean_Revenue','clean_NormalizedEBIT',
        'derived_ROIC','derived_FCF','derived_FCF_Margin_Pct',
        'clean_SBC','overall_quality_score']
print(df[cols].tail(8).to_string())

# Check screens
screens = db.query(\\\"\\\"\\\"
    SELECT ticker, fiscal_year, beneish_m_score, beneish_flagged,
           sloan_accrual_ratio, sloan_signal, epv_per_share
    FROM screen_results
    WHERE ticker='AAPL'
    ORDER BY fiscal_year DESC LIMIT 5
\\\"\\\"\\\")
print(screens.to_string())
db.close()
"
""")
print("  3. Once clean, run full universe:")
print("     PYTHONPATH=. python3 aletheia/data/database.py MSFT GOOGL NVDA META AMZN TSLA")
