python3 -c "
from aletheia.tools.lifecycle_classifier import get_stage_adjusted_thresholds, LifecycleClassifier

for ticker in ['MSFT', 'NVDA', 'META', 'AMZN', 'GOOGL']:
    try:
        t = get_stage_adjusted_thresholds(ticker)
        print(f'{ticker}: {t.stage.value}')
    except Exception as e:
        print(f'{ticker}: ERROR — {e}')

# Also try direct classification
clf = LifecycleClassifier()
for ticker in ['MSFT', 'NVDA']:
    try:
        r = clf.classify_from_report(ticker)
        print(f'{ticker} direct: {r.stage.value}')
    except Exception as e:
        print(f'{ticker} direct ERROR: {e}')
" 2>&1
