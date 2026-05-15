"""Stage 3 wiring for the seven accounting identities.

Reuses the battle-tested checkers in
``tools.verification.identity_checks`` (built during the Days 1-7 audit
and the A11 effective-tax-rate stabilisation). This module adapts the
Stage 3 input contract (``List[ValidatedCleanedRecord]``) to the
record-dict shape the checkers expect, then runs all seven identities.

The cross-layer import (``aletheia.calculations`` → ``tools.verification``)
is a deliberate stopgap to avoid duplicating the 600+ LoC checker
implementation. Follow-up: promote the checkers down into this module
and convert ``tools/verification/identity_checks.py`` to a thin CLI
wrapper that imports from here.

The seven identities:
  1. Balance Sheet Equation         A = L + E (tolerance 0.5%)
  2. Retained Earnings Roll-forward RE_end ≈ RE_beg + NI − Div
                                    (extended formula, 2% tolerance)
  3. Cash Roll-forward              Cash_end = Cash_beg + OCF + ICF
                                    + FCF + FX (tolerance 0.5%)
  4. PP&E Roll-forward              PPE_end ≈ PPE_beg + CapEx − D&A
                                    (tolerance 5%)
  5. Debt Roll-forward              Debt_end ≈ Debt_beg + Issued − Repaid
                                    (tolerance 3%)
  6. Working Capital Reconciliation CF Δ matches BS Δ for AR / Inv / AP
                                    (tolerance 10% each)
  7. FCF Pathway Reconciliation     FCF_direct ≈ FCF_from_NOPAT
                                    (tolerance 10%)
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Dict, List

from aletheia.contracts.pipeline import ValidatedCleanedRecord


# ─────────────────────────────────────────────────────────────────────
# Record adapter
# ─────────────────────────────────────────────────────────────────────

def _records_to_check_dicts(
    records: List[ValidatedCleanedRecord],
) -> List[Dict[str, Any]]:
    """Convert ``ValidatedCleanedRecord`` to the dict shape the
    existing checkers consume: ``{ticker, fiscal_year, period, clean,
    raw}``. ``clean`` and ``raw`` here are the full materialised dicts
    (``r.clean`` and ``r.raw``); the checkers' ``_field`` helper
    searches both namespaces in order.
    """
    out: List[Dict[str, Any]] = []
    for r in records:
        out.append({
            "ticker": r.ticker,
            "fiscal_year": int(r.fiscal_year),
            "period": r.period,
            "clean": dict(r.clean or {}),
            "raw": dict(r.raw or {}),
        })
    # FY ASC then TTM after FY of the same year — same ordering as the
    # standalone audit so downstream pair-walking matches.
    out.sort(key=lambda d: (d["fiscal_year"], 0 if d["period"] == "FY" else 1))
    return out


# ─────────────────────────────────────────────────────────────────────
# Public entry point
# ─────────────────────────────────────────────────────────────────────

def run_identity_checks(
    records: List[ValidatedCleanedRecord],
) -> Dict[str, Any]:
    """Run the seven accounting identities for one ticker's records.

    Returns a JSON-serialisable dict:
      {
        "ticker": "META",
        "results": [ {identity_name, fiscal_year, passed, ...}, ... ],
        "summary": {
          "n_checks": int,
          "n_passed": int,
          "n_failed": int,
          "n_skipped": int,
          "failed_identities": ["..."]
        }
      }

    The checkers themselves live in ``tools.verification.identity_checks``
    — we delegate per-identity calls and just adapt the records on the
    input side. The standalone CLI tool at that path stays usable; this
    module is the Stage 3-native entry point.
    """
    # Imported lazily so a Stage 3 unit test that doesn't exercise this
    # path doesn't pay the cost of importing the audit infra (which
    # pulls in DuckDB + SEC client).
    from tools.verification.identity_checks import (
        IdentityCheckResult,
        RecordLoader,
        check_balance_sheet_equation,
        check_cash_rollforward,
        check_debt_rollforward,
        check_fcf_pathway_reconciliation,
        check_ppe_rollforward,
        check_retained_earnings_rollforward,
        check_working_capital_reconciliation,
    )

    if not records:
        return {
            "ticker": None,
            "results": [],
            "summary": {
                "n_checks": 0, "n_passed": 0,
                "n_failed": 0, "n_skipped": 0,
                "failed_identities": [],
            },
        }

    ticker = records[0].ticker
    check_dicts = _records_to_check_dicts(records)
    fy_records = [r for r in check_dicts if r["period"] == "FY"]
    by_fy = {r["fiscal_year"]: r for r in fy_records}
    fys_sorted = sorted(by_fy.keys())

    # ``RecordLoader`` is used for xbrl_fact() lookups against the SEC
    # raw companyfacts JSON (Retained Earnings, FX effect, debt
    # issuance/repayment, working-capital CF tags). It's safe to
    # instantiate here — it caches per-ticker and only does I/O when
    # the lookups actually fire.
    loader = RecordLoader()

    results: List[IdentityCheckResult] = []

    # Identity 1 — Balance Sheet Equation: every period (FY + TTM)
    for r in check_dicts:
        results.append(check_balance_sheet_equation(r))

    # Roll-forwards + FCF pathway: walk consecutive FY pairs
    for i, fy in enumerate(fys_sorted):
        current = by_fy[fy]
        if i == 0:
            # First FY has no prior — FCF pathway can still run on the
            # single period (history is the full FY list).
            results.append(check_fcf_pathway_reconciliation(
                None, current, history=fy_records,
            ))
            continue
        prior = by_fy[fys_sorted[i - 1]]
        results.append(check_retained_earnings_rollforward(prior, current, loader))
        results.append(check_cash_rollforward(prior, current, loader))
        results.append(check_ppe_rollforward(prior, current))
        results.append(check_debt_rollforward(prior, current, loader))
        results.extend(check_working_capital_reconciliation(prior, current, loader))
        results.append(check_fcf_pathway_reconciliation(
            prior, current, history=fy_records,
        ))

    # Summarise. Phase 3 splits non-passing into TWO buckets:
    #   - expected_exception: failure carries a documented C-category
    #     (hyperscaler CIP, ASC 842 transition, M&A WC distortion, etc.)
    #   - failed: truly unflagged, an unresolved diagnostic gap
    skipped = [r for r in results if r.notes and r.notes.startswith("skipped:")]
    non_skipped = [r for r in results if r not in skipped]
    passed = [r for r in non_skipped if r.passed]
    expected_exception = [
        r for r in non_skipped
        if not r.passed and r.exception_category is not None
    ]
    failed = [
        r for r in non_skipped
        if not r.passed and r.exception_category is None
    ]
    failed_identities = sorted({r.identity_name for r in failed})
    exception_categories = sorted(
        {r.exception_category for r in expected_exception if r.exception_category}
    )

    return {
        "ticker": ticker,
        "results": [asdict(r) for r in results],
        "summary": {
            "n_checks": len(results),
            "n_passed": len(passed),
            "n_expected_exception": len(expected_exception),
            "n_failed": len(failed),
            "n_skipped": len(skipped),
            "failed_identities": failed_identities,
            "exception_categories": exception_categories,
        },
    }


__all__ = ["run_identity_checks"]
