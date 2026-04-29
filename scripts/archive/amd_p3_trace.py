from aletheia.tools.conviction_scorer import ConvictionScorer
scorer = ConvictionScorer()
sector = scorer._get_sector('AMD')
print(f'AMD sector: "{sector}"')

# Also check what thresholds are being applied
from aletheia.tools.lifecycle_classifier import get_stage_adjusted_thresholds
t = get_stage_adjusted_thresholds('AMD')
print(f'cagr_strong threshold: {t.cagr_strong:.2f}')
print(f'cagr_good threshold:   {t.cagr_good:.2f}')
print()

# Manually trace the P3 scoring
from aletheia.tools.conviction_scorer import _p3_score
score, reasons = _p3_score(
    rev_cagr=None,
    hist_cagr=0.197,
    sector=sector,
    cyclicality_z=1.7,
    is_peak=False,
    implied_cagr=0.630,
    cagr_strong=t.cagr_strong,
    cagr_good=t.cagr_good,
    cagr_moderate=t.cagr_moderate,
    cagr_slow=t.cagr_slow,
    stage='growth_compounder',
)
print(f'P3 manual trace: {score}/5')
for r in reasons:
    print(f'  → {r}')
