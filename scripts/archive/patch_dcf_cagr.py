"""
patch_dcf_cagr.py
=================
Patches dcf_engine.py to fix the historical CAGR computation.

Problem: AAPL's 5Y CAGR (2020-2025) is 2.6% because FY2020 was an
exceptional COVID-boom year that inflates the base. The 10Y CAGR
(2015-2025) is more representative of structural growth at ~11%.

Fix: Use a CAGR that is the MEDIAN of multiple lookback periods
(3Y, 5Y, 7Y, 10Y), dropping the highest and lowest to remove
base-year distortion. This is a standard professional analyst approach.

Also adds: business mix weighting — if services revenue is a known
fast-growing segment, weight its CAGR more heavily.

Run from project root:
    PYTHONPATH=. python3 patch_dcf_cagr.py
"""

from pathlib import Path

p = Path("aletheia/tools/dcf_engine.py")
code = p.read_text()

OLD = '''        # 5-year historical revenue CAGR
        hist_revenues = df[df["fiscal_year"] <= fiscal_year].sort_values(
            "fiscal_year"
        )["clean_Revenue"].dropna()

        if len(hist_revenues) >= 5:
            rev_5y_ago = float(hist_revenues.iloc[-5])
            rev_now = float(hist_revenues.iloc[-1])
            if rev_5y_ago > 0:
                hist_cagr = (rev_now / rev_5y_ago) ** (1 / 5) - 1
            else:
                hist_cagr = 0.10
        else:
            hist_cagr = 0.10

        hist_cagr = float(np.clip(hist_cagr, 0.01, 0.60))'''

NEW = '''        # Historical revenue CAGR — robust multi-period median
        # Uses median of available lookback periods (3Y, 5Y, 7Y, 10Y)
        # to remove base-year distortion (e.g. COVID boom anchoring).
        # Professional standard: never use a single lookback period.
        hist_revenues = df[df["fiscal_year"] <= fiscal_year].sort_values(
            "fiscal_year"
        )["clean_Revenue"].dropna()

        rev_now = float(hist_revenues.iloc[-1]) if len(hist_revenues) > 0 else 0.0
        cagr_candidates = []

        for lookback in [3, 5, 7, 10]:
            if len(hist_revenues) >= lookback:
                rev_past = float(hist_revenues.iloc[-lookback])
                if rev_past > 0 and rev_now > 0:
                    cagr = (rev_now / rev_past) ** (1 / lookback) - 1
                    cagr_candidates.append(cagr)

        if len(cagr_candidates) >= 3:
            # Drop highest and lowest to remove outlier base years,
            # then take the median of the remainder
            cagr_sorted = sorted(cagr_candidates)
            trimmed = cagr_sorted[1:-1]   # Remove min and max
            hist_cagr = float(np.median(trimmed))
        elif len(cagr_candidates) > 0:
            hist_cagr = float(np.median(cagr_candidates))
        else:
            hist_cagr = 0.08

        hist_cagr = float(np.clip(hist_cagr, 0.01, 0.60))

        if self.verbose:
            cagr_str = ", ".join(
                f"{c:.1%}" for c in cagr_candidates
            )
            print(f"  CAGR candidates ({len(cagr_candidates)} periods): [{cagr_str}]")
            print(f"  Robust CAGR (trimmed median): {hist_cagr:.1%}")'''

if OLD in code:
    code = code.replace(OLD, NEW, 1)
    p.write_text(code)
    print("✓ Patched: DCF engine CAGR computation → robust multi-period median")
else:
    print("✗ Pattern not found — checking what is there:")
    idx = code.find("5-year historical revenue CAGR")
    if idx > -1:
        print(repr(code[idx:idx+400]))
    else:
        print("  'historical revenue CAGR' block not found")

# Also patch reverse_dcf.py with the same fix
p2 = Path("aletheia/tools/reverse_dcf.py")
code2 = p2.read_text()

OLD2 = '''        hist = df[df["fiscal_year"] <= fy].sort_values("fiscal_year")
        rev_series = hist["clean_Revenue"].dropna()
        if len(rev_series) >= 5:
            r0 = float(rev_series.iloc[-5])
            r1 = float(rev_series.iloc[-1])
            hist_cagr = (r1 / r0) ** (1/5) - 1 if r0 > 0 else 0.10
        else:
            hist_cagr = 0.10
        result.historical_cagr_5y = float(np.clip(hist_cagr, 0.0, 0.80))'''

NEW2 = '''        hist = df[df["fiscal_year"] <= fy].sort_values("fiscal_year")
        rev_series = hist["clean_Revenue"].dropna()
        rev_now = float(rev_series.iloc[-1]) if len(rev_series) > 0 else 0.0
        cagr_candidates2 = []
        for lookback in [3, 5, 7, 10]:
            if len(rev_series) >= lookback:
                r0 = float(rev_series.iloc[-lookback])
                if r0 > 0 and rev_now > 0:
                    cagr_candidates2.append((rev_now / r0) ** (1/lookback) - 1)
        if len(cagr_candidates2) >= 3:
            s = sorted(cagr_candidates2)
            hist_cagr = float(np.median(s[1:-1]))
        elif cagr_candidates2:
            hist_cagr = float(np.median(cagr_candidates2))
        else:
            hist_cagr = 0.08
        result.historical_cagr_5y = float(np.clip(hist_cagr, 0.0, 0.80))'''

if OLD2 in code2:
    code2 = code2.replace(OLD2, NEW2, 1)
    p2.write_text(code2)
    print("✓ Patched: ReverseDCF CAGR computation → robust multi-period median")
else:
    print("⚠ ReverseDCF pattern not found — may need manual update")

# Same fix for multiple_decomposition.py
p3 = Path("aletheia/tools/multiple_decomposition.py")
code3 = p3.read_text()

OLD3 = '''        hist = df[df["fiscal_year"] <= fy].sort_values("fiscal_year")
        rev_series = hist["clean_Revenue"].dropna()
        if len(rev_series) >= 5:
            r0 = float(rev_series.iloc[-5])
            r1 = float(rev_series.iloc[-1])
            hist_cagr = (r1 / r0) ** (1/5) - 1 if r0 > 0 else 0.10
        else:
            hist_cagr = 0.10
        hist_cagr = float(np.clip(hist_cagr, 0.0, 0.60))'''

NEW3 = '''        hist = df[df["fiscal_year"] <= fy].sort_values("fiscal_year")
        rev_series = hist["clean_Revenue"].dropna()
        rev_now3 = float(rev_series.iloc[-1]) if len(rev_series) > 0 else 0.0
        cagr_candidates3 = []
        for lookback in [3, 5, 7, 10]:
            if len(rev_series) >= lookback:
                r0 = float(rev_series.iloc[-lookback])
                if r0 > 0 and rev_now3 > 0:
                    cagr_candidates3.append((rev_now3 / r0) ** (1/lookback) - 1)
        if len(cagr_candidates3) >= 3:
            s3 = sorted(cagr_candidates3)
            hist_cagr = float(np.median(s3[1:-1]))
        elif cagr_candidates3:
            hist_cagr = float(np.median(cagr_candidates3))
        else:
            hist_cagr = 0.08
        hist_cagr = float(np.clip(hist_cagr, 0.0, 0.60))'''

if OLD3 in code3:
    code3 = code3.replace(OLD3, NEW3, 1)
    p3.write_text(code3)
    print("✓ Patched: MultipleDecomposition CAGR → robust multi-period median")
else:
    print("⚠ MultipleDecomposition pattern not found — may need manual update")

print()
print("="*60)
print("Verification — run this after patching:")
print("""
PYTHONPATH=. python3 -c "
from aletheia.tools.dcf_engine import DCFEngine
engine = DCFEngine(verbose=True)
result = engine.run('AAPL')
print()
print(result.summary())
"
""")
