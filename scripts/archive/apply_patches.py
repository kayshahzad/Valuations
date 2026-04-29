"""
apply_patches.py
================
Applies three targeted patches to the existing Phase 1 files:

  Patch 1: cleaning_engine.py — integrate TagResolver into _load_and_pivot
  Patch 2: cleaning_engine.py — fix the net_re_adjustment typo (line ~196)
  Patch 3: database.py — fix duplicate rows in flagged_for_review view

Run from project root:
    PYTHONPATH=. python3 apply_patches.py
"""

from pathlib import Path
import re
import sys

def patch_file(path: str, old: str, new: str, description: str) -> bool:
    p = Path(path)
    if not p.exists():
        print(f"  ✗ File not found: {path}")
        return False
    content = p.read_text()
    if old not in content:
        print(f"  ⚠ Patch target not found in {path}: {description}")
        print(f"    Looking for: {repr(old[:80])}")
        return False
    patched = content.replace(old, new, 1)
    p.write_text(patched)
    print(f"  ✓ Patched: {description}")
    return True


print("=" * 60)
print("Applying Phase 1 patches")
print("=" * 60)


# ─────────────────────────────────────────────────────────────────────────────
# Patch 1: Add TagResolver import to cleaning_engine.py
# ─────────────────────────────────────────────────────────────────────────────
print("\n[1] Integrating TagResolver into cleaning_engine.py")

patch_file(
    "aletheia/data/cleaning_engine.py",
    old='import pandas as pd\nimport numpy as np\n\nwarnings.filterwarnings("ignore", category=FutureWarning)',
    new='import pandas as pd\nimport numpy as np\n\nwarnings.filterwarnings("ignore", category=FutureWarning)\n\n# Tag resolution layer — normalizes transformer output + enriches from raw XBRL\ntry:\n    from aletheia.data.tag_resolver import TagResolver\n    _tag_resolver = TagResolver()\nexcept ImportError:\n    _tag_resolver = None',
    description="Add TagResolver import to cleaning_engine.py"
)


# ─────────────────────────────────────────────────────────────────────────────
# Patch 2: Use TagResolver in _load_and_pivot
# ─────────────────────────────────────────────────────────────────────────────

patch_file(
    "aletheia/data/cleaning_engine.py",
    old='''        # Pivot: standard_tag → value
        wide = {}
        for _, row in fy_df.iterrows():
            tag = row.get("standard_tag")
            val = row.get("value")
            if tag and val is not None and not pd.isna(val):
                wide[tag] = float(val)

        # Best period_end_date for this year
        period_end_date = None
        if "period_end_date" in fy_df.columns:
            dates = fy_df["period_end_date"].dropna()
            if not dates.empty:
                period_end_date = str(dates.iloc[-1])

        return wide, period_end_date''',
    new='''        # Pivot: standard_tag → value
        wide = {}
        for _, row in fy_df.iterrows():
            tag = row.get("standard_tag")
            val = row.get("value")
            if tag and val is not None and not pd.isna(val):
                wide[tag] = float(val)

        # Best period_end_date for this year
        period_end_date = None
        if "period_end_date" in fy_df.columns:
            dates = fy_df["period_end_date"].dropna()
            if not dates.empty:
                period_end_date = str(dates.iloc[-1])

        # ── Enrich: normalize tag names + supplement from raw XBRL ──────────
        # This resolves the lowercase/PascalCase mismatch between
        # canonical_transformer output and cleaning engine expectations,
        # and fills in metrics the transformer did not capture (SBC, lease, etc.)
        if _tag_resolver is not None:
            wide = _tag_resolver.enrich(wide, ticker, fiscal_year)

        return wide, period_end_date''',
    description="Integrate TagResolver into _load_and_pivot"
)


# ─────────────────────────────────────────────────────────────────────────────
# Patch 3: Fix typo net_re_adjustment → net_adjustment
# ─────────────────────────────────────────────────────────────────────────────
print("\n[2] Fixing typo in cleaning_engine.py Domain 1")

patch_file(
    "aletheia/data/cleaning_engine.py",
    old="net_re_adjustment",
    new="net_adjustment",
    description="Fix net_re_adjustment typo → net_adjustment"
)


# ─────────────────────────────────────────────────────────────────────────────
# Patch 4: Fix duplicate rows in flagged_for_review view
# The view was joining screen_results without deduplication, causing
# one row per screen_results entry rather than one per company/year.
# ─────────────────────────────────────────────────────────────────────────────
print("\n[3] Fixing flagged_for_review view in database.py")

patch_file(
    "aletheia/data/database.py",
    old='''        # Flagged companies requiring review
        self._conn.execute("""
            CREATE OR REPLACE VIEW flagged_for_review AS
            SELECT
                cr.ticker,
                cr.fiscal_year,
                cr.overall_quality_score,
                cr.warning_count,
                sr.beneish_m_score,
                sr.beneish_flagged,
                sr.sloan_accrual_ratio,
                sr.sloan_flagged,
                sr.any_flagged,
                cr.cleaned_at
            FROM company_records_latest cr
            LEFT JOIN screen_results sr
                ON cr.ticker = sr.ticker
                AND cr.fiscal_year = sr.fiscal_year
            WHERE sr.any_flagged = TRUE
               OR cr.warning_count > 3
               OR cr.overall_quality_score < 0.70
            ORDER BY sr.beneish_m_score DESC NULLS LAST
        """)''',
    new='''        # Flagged companies requiring review
        # Uses latest screen_results per (ticker, fiscal_year) to avoid duplicates
        self._conn.execute("""
            CREATE OR REPLACE VIEW flagged_for_review AS
            WITH latest_screens AS (
                SELECT ticker, fiscal_year,
                    beneish_m_score, beneish_flagged,
                    sloan_accrual_ratio, sloan_flagged,
                    any_flagged,
                    ROW_NUMBER() OVER (
                        PARTITION BY ticker, fiscal_year
                        ORDER BY screened_at DESC
                    ) AS rn
                FROM screen_results
            )
            SELECT
                cr.ticker,
                cr.fiscal_year,
                cr.overall_quality_score,
                cr.warning_count,
                ls.beneish_m_score,
                ls.beneish_flagged,
                ls.sloan_accrual_ratio,
                ls.sloan_flagged,
                ls.any_flagged,
                cr.cleaned_at
            FROM company_records_latest cr
            LEFT JOIN latest_screens ls
                ON cr.ticker = ls.ticker
                AND cr.fiscal_year = ls.fiscal_year
                AND ls.rn = 1
            WHERE ls.any_flagged = TRUE
               OR cr.warning_count > 3
               OR cr.overall_quality_score < 0.70
            ORDER BY ls.beneish_m_score DESC NULLS LAST
        """)''',
    description="Fix flagged_for_review view — deduplicate screen_results with ROW_NUMBER()"
)


# ─────────────────────────────────────────────────────────────────────────────
# Verification
# ─────────────────────────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Patch verification")
print("=" * 60)

checks = [
    ("aletheia/data/cleaning_engine.py", "TagResolver", "TagResolver import"),
    ("aletheia/data/cleaning_engine.py", "_tag_resolver.enrich", "TagResolver.enrich call"),
    ("aletheia/data/cleaning_engine.py", "net_adjustment", "typo fix"),
    ("aletheia/data/cleaning_engine.py", "net_re_adjustment", None),  # should be ABSENT
    ("aletheia/data/database.py", "ROW_NUMBER()", "view dedup fix"),
    ("aletheia/data/tag_resolver.py", "TagResolver", "tag_resolver.py exists"),
    ("aletheia/config/ACCOUNTING_MAPS.md", "OperatingIncomeLoss", "ACCOUNTING_MAPS updated"),
    ("aletheia/config/ACCOUNTING_MAPS.md", "SalesRevenueNet", "SalesRevenueNet in maps"),
]

all_ok = True
for path, token, label in checks:
    p = Path(path)
    if not p.exists():
        if label is None:
            print(f"  ✓ {path} — absent as expected")
        else:
            print(f"  ✗ MISSING FILE: {path}")
            all_ok = False
        continue
    content = p.read_text()
    present = token in content
    if label is None:
        # Should be absent
        if present:
            print(f"  ✗ Still found '{token}' in {path} — patch may have failed")
            all_ok = False
        else:
            print(f"  ✓ '{token}' correctly absent from {path}")
    else:
        if present:
            print(f"  ✓ '{token}' found in {path} — {label}")
        else:
            print(f"  ✗ '{token}' NOT found in {path} — {label}")
            all_ok = False

print("\n" + ("✓ All patches applied successfully" if all_ok else "✗ Some patches failed — check above"))
print("=" * 60)

if all_ok:
    print("\nNext steps:")
    print("  1. Delete the existing database to start fresh:")
    print("     rm -f valuation_data/database/investment.duckdb")
    print("")
    print("  2. Re-run the full pipeline for AAPL:")
    print("     PYTHONPATH=. python3 aletheia/data/database.py AAPL")
    print("")
    print("  3. Verify results:")
    print("""     PYTHONPATH=. python3 -c "
from aletheia.data.database import InvestmentDatabase
db = InvestmentDatabase()
df = db.get_latest('AAPL')
print(df[['fiscal_year','clean_Revenue','clean_NormalizedEBIT','derived_ROIC','derived_FCF_Margin_Pct','overall_quality_score']].to_string())
db.close()
"
""")
    print("  4. Once AAPL looks correct, run the full universe:")
    print("     PYTHONPATH=. python3 aletheia/data/database.py MSFT GOOGL NVDA META AMZN TSLA CNC")
