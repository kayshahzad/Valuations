"""Identity-audit verification across the universe.

Reads cleaned records from ``company_records`` (DuckDB) for materialised
fields, and falls through to the raw SEC XBRL ``companyfacts`` JSON for
fields the cleaning engine doesn't currently surface (RetainedEarnings,
CF-statement working-capital changes, debt issuance/repayment, FX effect
on cash). Computes the seven foundational accounting identities listed
in the Phase-1 prompt, emits findings to CSV / JSON / Markdown.

This is investigative tooling — the Stage 2 integration of these checks
happens in subsequent work. The script is intentionally standalone
under ``tools/verification/`` so it can be re-run without coupling to
the validation framework's runtime path.

Run:
    python -m tools.verification.identity_checks
    python -m tools.verification.identity_checks --tickers NVDA AAPL
    python -m tools.verification.identity_checks --output-dir audits/

Outputs:
    audits/identity_audit_<DATE>.csv      — flat tabular, one row per check
    audits/identity_audit_<DATE>.json     — structured (metadata + results)
    docs/identity_audit_findings_<DATE>.md — human-readable findings
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import math
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────

TOLERANCE_THRESHOLDS: Dict[str, float] = {
    "balance_sheet_equation":         0.005,   # 0.5% of total assets
    "retained_earnings_rollforward":  0.02,    # 2% of beginning RE
    "cash_rollforward":               0.001,   # 0.1% — exact identity
    "ppe_rollforward":                0.05,    # 5% — M&A/impair widen
    "debt_rollforward":               0.03,    # 3% — ASC 842 widens
    "working_capital_AR":             0.10,    # 10%
    "working_capital_inventory":      0.10,    # 10%
    "working_capital_AP":             0.10,    # 10%
    "fcf_pathway_reconciliation":     0.10,    # 10%
}

# Absolute-magnitude floor so 5% on tiny denominators doesn't fire
# spurious findings. Materiality consideration from the prompt.
ABS_MAGNITUDE_FLOOR_USD = 10_000_000  # $10M — below this, treat as passing

SEC_RAW_DIR = Path("valuation_data/raw/sec/companyfacts")

OUTPUT_DIR_DEFAULT = Path("audits")
DOCS_DIR = Path("docs")


# ─────────────────────────────────────────────────────────────────────
# Result dataclass
# ─────────────────────────────────────────────────────────────────────

@dataclass
class IdentityCheckResult:
    ticker: str
    fiscal_year: int
    period: str  # "FY" | "TTM"
    identity_name: str
    passed: bool
    discrepancy_abs: Optional[float]
    discrepancy_pct: Optional[float]
    tolerance_pct: float
    components: Dict[str, Any] = field(default_factory=dict)
    notes: Optional[str] = None

    @classmethod
    def skipped(
        cls, *, ticker: str, fiscal_year: int, period: str,
        identity_name: str, reason: str,
    ) -> "IdentityCheckResult":
        return cls(
            ticker=ticker, fiscal_year=fiscal_year, period=period,
            identity_name=identity_name,
            passed=True,                # skipped checks don't count as failures
            discrepancy_abs=None, discrepancy_pct=None,
            tolerance_pct=TOLERANCE_THRESHOLDS.get(identity_name, 0.0),
            components={}, notes=f"skipped: {reason}",
        )


# ─────────────────────────────────────────────────────────────────────
# Data loaders
# ─────────────────────────────────────────────────────────────────────

class RecordLoader:
    """Loads cleaned records from DuckDB and raw SEC XBRL facts on
    demand. Caches per-ticker so a universe sweep doesn't re-open the
    DB connection or re-parse the XBRL JSON 25× per ticker.
    """

    def __init__(self) -> None:
        from aletheia.data import edgar_client
        self._sec = edgar_client.SecEdgar()
        self._cik_cache: Dict[str, Optional[str]] = {}
        self._xbrl_cache: Dict[str, Optional[Dict[str, Any]]] = {}
        self._records_cache: Dict[str, List[Dict[str, Any]]] = {}

    def cik(self, ticker: str) -> Optional[str]:
        if ticker not in self._cik_cache:
            self._cik_cache[ticker] = self._sec.resolve_cik(ticker)
        return self._cik_cache[ticker]

    def xbrl_us_gaap(self, ticker: str) -> Dict[str, Any]:
        if ticker not in self._xbrl_cache:
            cik = self.cik(ticker)
            if not cik:
                self._xbrl_cache[ticker] = {}
                return {}
            path = SEC_RAW_DIR / f"CIK{cik}.json"
            if not path.exists():
                self._xbrl_cache[ticker] = {}
                return {}
            try:
                facts = json.loads(path.read_text())
                self._xbrl_cache[ticker] = (
                    facts.get("facts", {}).get("us-gaap", {})
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("XBRL load failed for %s: %s", ticker, exc)
                self._xbrl_cache[ticker] = {}
        return self._xbrl_cache[ticker] or {}

    def records(self, ticker: str) -> List[Dict[str, Any]]:
        """Cleaned records for ticker, sorted by fiscal_year ASC.
        Each record is a dict with raw_*/clean_*/derived_* fields PLUS
        the parsed contents of raw_json + clean_json blobs flattened
        into ``raw`` and ``clean`` sub-dicts."""
        if ticker not in self._records_cache:
            from aletheia.data.database import InvestmentDatabase
            db = InvestmentDatabase(verbose=False)
            try:
                df = db.get_latest(ticker)
            finally:
                db.close()
            out: List[Dict[str, Any]] = []
            for _, row in df.iterrows():
                raw_blob = row.get("raw_json")
                clean_blob = row.get("clean_json")
                rec: Dict[str, Any] = {
                    "ticker": ticker,
                    "fiscal_year": int(row["fiscal_year"]),
                    "period": row.get("period") or "FY",
                    "raw": json.loads(raw_blob) if isinstance(raw_blob, str) else {},
                    "clean": json.loads(clean_blob) if isinstance(clean_blob, str) else {},
                }
                out.append(rec)
            out.sort(key=lambda r: (r["fiscal_year"], 0 if r["period"] == "FY" else 1))
            self._records_cache[ticker] = out
        return self._records_cache[ticker]

    def xbrl_fact(
        self, ticker: str, tag: str, fiscal_year: int,
        *, form: str = "10-K",
    ) -> Optional[float]:
        """Latest USD value of an XBRL tag for the given fiscal year.
        Returns None when the tag is absent, when no entry matches the
        FY, or when the value isn't a finite number.
        """
        us = self.xbrl_us_gaap(ticker)
        entry = us.get(tag)
        if not entry:
            return None
        units = entry.get("units", {}).get("USD", [])
        candidates = [
            u for u in units
            if u.get("form") == form and u.get("fy") == fiscal_year
        ]
        if not candidates:
            return None
        # Pick the latest filing for this FY (handles amended 10-Ks).
        latest = max(
            candidates,
            key=lambda u: u.get("end") or u.get("filed") or "",
        )
        val = latest.get("val")
        try:
            f = float(val)
        except (TypeError, ValueError):
            return None
        return f if math.isfinite(f) else None


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────

def _coerce(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _field(record: Dict[str, Any], *keys: str) -> Optional[float]:
    """First non-None value among the candidate keys.

    Searches both ``clean`` and ``raw`` sub-dicts. Useful where the
    cleaner exposes a field under a different name than the raw
    XBRL tag — caller passes both as fallbacks.
    """
    for ns in ("clean", "raw"):
        d = record.get(ns, {})
        for k in keys:
            if k in d:
                v = _coerce(d[k])
                if v is not None:
                    return v
    return None


def _passes(
    discrepancy_abs: Optional[float],
    discrepancy_pct: Optional[float],
    tolerance: float,
) -> bool:
    """Pass when (a) % is within tolerance OR (b) absolute magnitude
    is below the materiality floor. Both checks coexist so a tiny
    denominator doesn't generate a noise finding on a meaningless gap.
    """
    if discrepancy_pct is None or discrepancy_abs is None:
        return False
    if abs(discrepancy_abs) < ABS_MAGNITUDE_FLOOR_USD:
        return True
    return abs(discrepancy_pct) <= tolerance * 100.0


# ─────────────────────────────────────────────────────────────────────
# Identity 1 — Balance Sheet Equation
# ─────────────────────────────────────────────────────────────────────

def check_balance_sheet_equation(
    record: Dict[str, Any],
) -> IdentityCheckResult:
    name = "balance_sheet_equation"
    ticker = record["ticker"]; fy = record["fiscal_year"]; period = record["period"]
    tol = TOLERANCE_THRESHOLDS[name]

    assets = _field(record, "TotalAssets")
    liab = _field(record, "TotalLiabilities")
    equity = _field(record, "TotalEquity", "EquityParentOnly")
    if any(v is None for v in (assets, liab, equity)):
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name,
            reason=f"missing fields A={assets!r} L={liab!r} E={equity!r}",
        )
    if not assets:
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name, reason="TotalAssets is 0",
        )
    disc_abs = assets - (liab + equity)
    disc_pct = disc_abs / assets * 100.0
    passed = _passes(disc_abs, disc_pct, tol)
    return IdentityCheckResult(
        ticker=ticker, fiscal_year=fy, period=period,
        identity_name=name, passed=passed,
        discrepancy_abs=disc_abs, discrepancy_pct=disc_pct,
        tolerance_pct=tol,
        components={
            "TotalAssets": assets,
            "TotalLiabilities": liab,
            "TotalEquity": equity,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Identity 2 — Retained Earnings Roll-Forward
# ─────────────────────────────────────────────────────────────────────

def check_retained_earnings_rollforward(
    prior: Dict[str, Any], current: Dict[str, Any], loader: RecordLoader,
) -> IdentityCheckResult:
    name = "retained_earnings_rollforward"
    ticker = current["ticker"]; fy = current["fiscal_year"]; period = current["period"]
    tol = TOLERANCE_THRESHOLDS[name]

    # RetainedEarnings isn't currently materialised in clean/raw blobs;
    # fall through to XBRL directly. Use prior FY's filing as the
    # beginning balance.
    beg = loader.xbrl_fact(ticker, "RetainedEarningsAccumulatedDeficit",
                          prior["fiscal_year"])
    end = loader.xbrl_fact(ticker, "RetainedEarningsAccumulatedDeficit",
                          fy)
    if beg is None or end is None:
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name,
            reason=f"RetainedEarnings unavailable (beg={beg!r}, end={end!r})",
        )
    ni = _field(current, "NetIncome")
    div = _field(current, "DividendsPaid") or 0.0
    if ni is None:
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name, reason="NetIncome unavailable",
        )

    implied = beg + ni - div
    disc_abs = end - implied
    # Denominator: |beg|, but if near-zero use TotalEquity to keep ratio meaningful
    denom = abs(beg) if abs(beg) > 1e6 else (
        _field(current, "TotalEquity", "EquityParentOnly") or 1.0
    )
    disc_pct = disc_abs / denom * 100.0
    passed = _passes(disc_abs, disc_pct, tol)
    return IdentityCheckResult(
        ticker=ticker, fiscal_year=fy, period=period,
        identity_name=name, passed=passed,
        discrepancy_abs=disc_abs, discrepancy_pct=disc_pct,
        tolerance_pct=tol,
        components={
            "RE_beginning": beg, "RE_ending_reported": end,
            "RE_ending_implied": implied,
            "NetIncome": ni, "DividendsPaid": div,
        },
        notes=(
            "discrepancy expected from SBC, treasury stock, OCI, "
            "buybacks — investigate before treating as bug"
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Identity 3 — Cash Roll-Forward
# ─────────────────────────────────────────────────────────────────────

def check_cash_rollforward(
    prior: Dict[str, Any], current: Dict[str, Any], loader: RecordLoader,
) -> IdentityCheckResult:
    name = "cash_rollforward"
    ticker = current["ticker"]; fy = current["fiscal_year"]; period = current["period"]
    tol = TOLERANCE_THRESHOLDS[name]

    beg = _field(prior, "Cash")
    end = _field(current, "Cash")
    ocf = _field(current, "OperatingCF")
    icf = _field(current, "InvestingCF")
    fcf = _field(current, "FinancingCF")
    # FX effect: prefer XBRL fact, default 0 for non-foreign filers
    fx = loader.xbrl_fact(
        ticker, "EffectOfExchangeRateOnCashAndCashEquivalents", fy,
    )
    if fx is None:
        fx = loader.xbrl_fact(
            ticker, "EffectOfExchangeRateOnCash", fy,
        )
    if fx is None:
        fx = 0.0  # treat absence as zero — most US filers have no FX

    if any(v is None for v in (beg, end, ocf, icf, fcf)):
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name,
            reason=(
                f"missing cash/CF fields beg={beg!r} end={end!r} "
                f"OCF={ocf!r} ICF={icf!r} FCF={fcf!r}"
            ),
        )
    implied = beg + ocf + icf + fcf + fx
    if not end:
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name, reason="ending Cash is 0",
        )
    disc_abs = end - implied
    disc_pct = disc_abs / end * 100.0
    passed = _passes(disc_abs, disc_pct, tol)
    return IdentityCheckResult(
        ticker=ticker, fiscal_year=fy, period=period,
        identity_name=name, passed=passed,
        discrepancy_abs=disc_abs, discrepancy_pct=disc_pct,
        tolerance_pct=tol,
        components={
            "Cash_beginning": beg, "Cash_ending_reported": end,
            "Cash_ending_implied": implied,
            "OCF": ocf, "ICF": icf, "FCF": fcf, "FX_effect": fx,
        },
    )


# ─────────────────────────────────────────────────────────────────────
# Identity 4 — PP&E Roll-Forward
# ─────────────────────────────────────────────────────────────────────

def check_ppe_rollforward(
    prior: Dict[str, Any], current: Dict[str, Any],
) -> IdentityCheckResult:
    name = "ppe_rollforward"
    ticker = current["ticker"]; fy = current["fiscal_year"]; period = current["period"]
    tol = TOLERANCE_THRESHOLDS[name]

    beg = _field(prior, "PPE")
    end = _field(current, "PPE")
    capex = _field(current, "CapEx_Total", "CapEx")
    da = _field(current, "Depreciation_Total")
    if any(v is None for v in (beg, end, capex, da)):
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name,
            reason=(
                f"missing fields beg={beg!r} end={end!r} "
                f"capex={capex!r} da={da!r}"
            ),
        )
    # CapEx is positive-magnitude per A6 sign convention; subtract D&A.
    implied = beg + capex - da
    if not beg:
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name, reason="beginning PPE is 0",
        )
    disc_abs = end - implied
    disc_pct = disc_abs / beg * 100.0
    passed = _passes(disc_abs, disc_pct, tol)
    return IdentityCheckResult(
        ticker=ticker, fiscal_year=fy, period=period,
        identity_name=name, passed=passed,
        discrepancy_abs=disc_abs, discrepancy_pct=disc_pct,
        tolerance_pct=tol,
        components={
            "PPE_beginning": beg, "PPE_ending_reported": end,
            "PPE_ending_implied": implied,
            "CapEx_Total": capex, "Depreciation_Total": da,
        },
        notes="acquisitions/divestitures/impairments expand gap",
    )


# ─────────────────────────────────────────────────────────────────────
# Identity 5 — Debt Roll-Forward
# ─────────────────────────────────────────────────────────────────────

def check_debt_rollforward(
    prior: Dict[str, Any], current: Dict[str, Any], loader: RecordLoader,
) -> IdentityCheckResult:
    name = "debt_rollforward"
    ticker = current["ticker"]; fy = current["fiscal_year"]; period = current["period"]
    tol = TOLERANCE_THRESHOLDS[name]

    beg_std = _field(prior, "ShortTermDebt") or 0.0
    beg_ltd = _field(prior, "LongTermDebt") or 0.0
    end_std = _field(current, "ShortTermDebt") or 0.0
    end_ltd = _field(current, "LongTermDebt") or 0.0
    beg_total = beg_std + beg_ltd
    end_total = end_std + end_ltd

    issued = (
        loader.xbrl_fact(ticker, "ProceedsFromIssuanceOfLongTermDebt", fy)
        or loader.xbrl_fact(ticker, "ProceedsFromIssuanceOfDebt", fy)
        or 0.0
    )
    repaid = (
        loader.xbrl_fact(ticker, "RepaymentsOfLongTermDebt", fy)
        or loader.xbrl_fact(ticker, "RepaymentsOfDebt", fy)
        or 0.0
    )

    if beg_total <= 0:
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name,
            reason=f"beginning total debt is 0 (std={beg_std}, ltd={beg_ltd})",
        )
    net_activity = issued - repaid
    implied = beg_total + net_activity
    disc_abs = end_total - implied
    disc_pct = disc_abs / beg_total * 100.0
    passed = _passes(disc_abs, disc_pct, tol)
    return IdentityCheckResult(
        ticker=ticker, fiscal_year=fy, period=period,
        identity_name=name, passed=passed,
        discrepancy_abs=disc_abs, discrepancy_pct=disc_pct,
        tolerance_pct=tol,
        components={
            "TotalDebt_beginning": beg_total,
            "TotalDebt_ending_reported": end_total,
            "TotalDebt_ending_implied": implied,
            "Issued": issued, "Repaid": repaid,
            "ShortTermDebt_beg": beg_std, "ShortTermDebt_end": end_std,
            "LongTermDebt_beg": beg_ltd, "LongTermDebt_end": end_ltd,
        },
        notes="2019 ASC 842 transition adds operating-lease liability",
    )


# ─────────────────────────────────────────────────────────────────────
# Identity 6 — Working Capital Reconciliation (AR, Inventory, AP)
# ─────────────────────────────────────────────────────────────────────

def check_working_capital_reconciliation(
    prior: Dict[str, Any], current: Dict[str, Any], loader: RecordLoader,
) -> List[IdentityCheckResult]:
    ticker = current["ticker"]; fy = current["fiscal_year"]; period = current["period"]
    out: List[IdentityCheckResult] = []

    for label, balance_keys, cf_tag, sign, tol_name in [
        ("AR", ("AccountsReceivable",),
         "IncreaseDecreaseInAccountsReceivable", -1.0, "working_capital_AR"),
        ("inventory", ("Inventory",),
         "IncreaseDecreaseInInventories", -1.0, "working_capital_inventory"),
        ("AP", ("AccountsPayable",),
         "IncreaseDecreaseInAccountsPayable", +1.0, "working_capital_AP"),
    ]:
        name = tol_name
        tol = TOLERANCE_THRESHOLDS[name]
        beg = _field(prior, *balance_keys)
        end = _field(current, *balance_keys)
        cf_reported = loader.xbrl_fact(ticker, cf_tag, fy)
        if beg is None or end is None or cf_reported is None:
            out.append(IdentityCheckResult.skipped(
                ticker=ticker, fiscal_year=fy, period=period,
                identity_name=name,
                reason=(
                    f"missing fields beg={beg!r} end={end!r} "
                    f"cf_change={cf_reported!r}"
                ),
            ))
            continue
        bs_change = end - beg
        # XBRL convention: IncreaseDecreaseIn* reports the *increase*
        # (positive = increase in asset/liability). The cash-flow
        # effect of that change has the opposite sign for assets
        # (AR/inventory) and same sign for liabilities (AP). We
        # compare CF-reported delta against balance-sheet delta directly.
        disc_abs = cf_reported - bs_change
        if abs(bs_change) < 1e6:
            out.append(IdentityCheckResult.skipped(
                ticker=ticker, fiscal_year=fy, period=period,
                identity_name=name,
                reason=f"|BS change|={abs(bs_change):.0f} below $1M floor",
            ))
            continue
        disc_pct = disc_abs / bs_change * 100.0
        passed = _passes(disc_abs, disc_pct, tol)
        out.append(IdentityCheckResult(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name, passed=passed,
            discrepancy_abs=disc_abs, discrepancy_pct=disc_pct,
            tolerance_pct=tol,
            components={
                "field": label,
                "BS_beginning": beg, "BS_ending": end,
                "BS_change": bs_change,
                "CF_reported_change": cf_reported,
                "sign_convention": sign,
            },
            notes="acquisitions/divestitures add WC outside CF changes",
        ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Identity 7 — FCF Pathway Reconciliation
# ─────────────────────────────────────────────────────────────────────

def check_fcf_pathway_reconciliation(
    prior: Optional[Dict[str, Any]], current: Dict[str, Any],
    *, history: Optional[List[Dict[str, Any]]] = None,
) -> IdentityCheckResult:
    name = "fcf_pathway_reconciliation"
    ticker = current["ticker"]; fy = current["fiscal_year"]; period = current["period"]
    tol = TOLERANCE_THRESHOLDS[name]

    ocf = _field(current, "OperatingCF")
    capex = _field(current, "CapEx_Total", "CapEx")
    ebit = _field(current, "NormalizedEBIT", "OperatingIncome")
    cleaned_cash_rate = _field(current, "CashTaxRate")
    cleaned_gaap_rate = _field(current, "GAAP_TaxRate")
    da = _field(current, "Depreciation_Total")

    if any(v is None for v in (ocf, capex, ebit, da)):
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name,
            reason=(
                f"missing fields OCF={ocf!r} capex={capex!r} "
                f"ebit={ebit!r} DA={da!r}"
            ),
        )

    # Tax-rate resolution via the A11 canonical chain. The cleaned
    # CashTaxRate / GAAP_TaxRate columns are NaN for several tickers
    # (cleaning_engine domain 10 doesn't always resolve the pre-tax
    # income tag). Falling through to the resolver lets the FCF
    # identity actually run, with the source captured in notes so
    # the analyst can weight findings accordingly.
    tax_source = "cleaned_field"
    if cleaned_cash_rate is not None:
        tax_rate = cleaned_cash_rate
    elif cleaned_gaap_rate is not None:
        tax_rate = cleaned_gaap_rate
        tax_source = "cleaned_gaap"
    else:
        try:
            import pandas as pd
            from aletheia.calculations import resolve_tax_rate
            hist_df = pd.DataFrame([
                {
                    "fiscal_year": h["fiscal_year"],
                    "clean_CashTaxRate": h["clean"].get("CashTaxRate"),
                    "clean_GAAP_TaxRate": h["clean"].get("GAAP_TaxRate"),
                }
                for h in (history or [])
                if h["period"] == "FY"
            ])
            tax_rate, tax_source = resolve_tax_rate(
                ticker=ticker, fn="identity_audit.fcf_pathway",
                df=hist_df, fy=fy,
                cash_tax_rate=None, gaap_tax_rate=None,
            )
        except Exception as exc:  # noqa: BLE001
            return IdentityCheckResult.skipped(
                ticker=ticker, fiscal_year=fy, period=period,
                identity_name=name,
                reason=f"tax-rate resolver failed: {exc}",
            )

    fcf_a = ocf - abs(capex)
    nopat = ebit * (1.0 - tax_rate)

    # ΔNWC: use balance-sheet deltas where prior year is available.
    # When prior is None (oldest year), treat ΔNWC as 0 — this widens
    # the gap deliberately and exposes the tooling limitation, NOT a
    # bug in the data.
    if prior is None:
        delta_nwc = 0.0
        nwc_note = "ΔNWC=0 (no prior year)"
    else:
        ar_b = _field(prior, "AccountsReceivable") or 0.0
        ar_e = _field(current, "AccountsReceivable") or 0.0
        inv_b = _field(prior, "Inventory") or 0.0
        inv_e = _field(current, "Inventory") or 0.0
        ap_b = _field(prior, "AccountsPayable") or 0.0
        ap_e = _field(current, "AccountsPayable") or 0.0
        delta_nwc = (ar_e - ar_b) + (inv_e - inv_b) - (ap_e - ap_b)
        nwc_note = (
            f"ΔAR={ar_e-ar_b:.0f} ΔInv={inv_e-inv_b:.0f} ΔAP={ap_e-ap_b:.0f}"
        )

    fcf_b = nopat + da - abs(capex) - delta_nwc
    disc_abs = fcf_a - fcf_b
    if not fcf_a:
        return IdentityCheckResult.skipped(
            ticker=ticker, fiscal_year=fy, period=period,
            identity_name=name, reason="FCF_A is 0",
        )
    disc_pct = disc_abs / abs(fcf_a) * 100.0
    passed = _passes(disc_abs, disc_pct, tol)
    return IdentityCheckResult(
        ticker=ticker, fiscal_year=fy, period=period,
        identity_name=name, passed=passed,
        discrepancy_abs=disc_abs, discrepancy_pct=disc_pct,
        tolerance_pct=tol,
        components={
            "FCF_A_ocf_minus_capex": fcf_a,
            "FCF_B_nopat_plus_da_minus_capex_minus_nwc": fcf_b,
            "OCF": ocf, "CapEx_abs": abs(capex),
            "EBIT": ebit,
            "tax_rate": tax_rate,
            "tax_rate_source": tax_source,
            "NOPAT": nopat, "DA": da, "delta_NWC": delta_nwc,
            "nwc_breakdown": nwc_note,
        },
        notes=(
            f"tax_rate source={tax_source}; SBC + deferred taxes "
            "drive systematic divergence"
        ),
    )


# ─────────────────────────────────────────────────────────────────────
# Per-ticker driver
# ─────────────────────────────────────────────────────────────────────

def run_all_checks_for_ticker(
    ticker: str, loader: Optional[RecordLoader] = None,
) -> List[IdentityCheckResult]:
    loader = loader or RecordLoader()
    records = loader.records(ticker)
    if not records:
        return []

    # Split into FY-only for roll-forward checks; FY+TTM for the
    # two single-period identities (balance sheet equation,
    # FCF-pathway).
    fy_records = [r for r in records if r["period"] == "FY"]
    by_fy = {r["fiscal_year"]: r for r in fy_records}
    fys_sorted = sorted(by_fy.keys())

    out: List[IdentityCheckResult] = []

    # Single-period identities — every (FY, TTM) row.
    for r in records:
        out.append(check_balance_sheet_equation(r))

    # Roll-forward identities — pairs of consecutive FY rows.
    for i, fy in enumerate(fys_sorted):
        current = by_fy[fy]
        if i == 0:
            # FCF pathway is single-period in (FCF_A) but ΔNWC needs
            # prior; record as skipped where appropriate. The function
            # itself handles prior=None.
            out.append(check_fcf_pathway_reconciliation(
                None, current, history=fy_records,
            ))
            continue
        prior = by_fy[fys_sorted[i - 1]]
        out.append(check_retained_earnings_rollforward(prior, current, loader))
        out.append(check_cash_rollforward(prior, current, loader))
        out.append(check_ppe_rollforward(prior, current))
        out.append(check_debt_rollforward(prior, current, loader))
        out.extend(check_working_capital_reconciliation(prior, current, loader))
        out.append(check_fcf_pathway_reconciliation(
            prior, current, history=fy_records,
        ))

    return out


# ─────────────────────────────────────────────────────────────────────
# Universe driver
# ─────────────────────────────────────────────────────────────────────

def run_universe_audit(
    tickers: Optional[List[str]] = None,
) -> List[IdentityCheckResult]:
    if tickers is None:
        from config.ticker_classification import UNIVERSE
        tickers = sorted(UNIVERSE.keys())
    loader = RecordLoader()
    all_results: List[IdentityCheckResult] = []
    for ticker in tickers:
        logger.info("auditing %s", ticker)
        try:
            results = run_all_checks_for_ticker(ticker, loader)
        except Exception as exc:  # noqa: BLE001
            logger.warning("audit raised for %s: %s", ticker, exc)
            continue
        all_results.extend(results)
    return all_results


# ─────────────────────────────────────────────────────────────────────
# Output emitters
# ─────────────────────────────────────────────────────────────────────

def _emit_csv(results: List[IdentityCheckResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "ticker", "fiscal_year", "period", "identity",
            "passed", "discrepancy_abs", "discrepancy_pct",
            "tolerance_pct", "notes", "components_json",
        ])
        for r in results:
            w.writerow([
                r.ticker, r.fiscal_year, r.period, r.identity_name,
                r.passed,
                f"{r.discrepancy_abs:.2f}" if r.discrepancy_abs is not None else "",
                f"{r.discrepancy_pct:.4f}" if r.discrepancy_pct is not None else "",
                r.tolerance_pct,
                r.notes or "",
                json.dumps(r.components, default=str),
            ])


def _emit_json(results: List[IdentityCheckResult], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "run_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha(),
        "universe_size": len({r.ticker for r in results}),
        "total_checks": len(results),
        "passed": sum(1 for r in results if r.passed),
        "failed": sum(1 for r in results if not r.passed),
        "tolerance_thresholds": TOLERANCE_THRESHOLDS,
        "abs_magnitude_floor_usd": ABS_MAGNITUDE_FLOOR_USD,
    }

    # Per-identity and per-ticker exception summaries.
    by_identity: Dict[str, Dict[str, int]] = {}
    by_ticker: Dict[str, Dict[str, int]] = {}
    for r in results:
        i_bucket = by_identity.setdefault(r.identity_name, {"failed": 0, "total": 0, "skipped": 0})
        t_bucket = by_ticker.setdefault(r.ticker, {"failed": 0, "total": 0, "skipped": 0})
        i_bucket["total"] += 1
        t_bucket["total"] += 1
        if r.notes and r.notes.startswith("skipped:"):
            i_bucket["skipped"] += 1
            t_bucket["skipped"] += 1
        elif not r.passed:
            i_bucket["failed"] += 1
            t_bucket["failed"] += 1

    payload = {
        "metadata": metadata,
        "exceptions_by_identity": by_identity,
        "exceptions_by_ticker": by_ticker,
        "results": [asdict(r) for r in results],
    }
    path.write_text(json.dumps(payload, indent=2, default=str))


def _git_sha() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unversioned"


def _emit_markdown(results: List[IdentityCheckResult], path: Path) -> None:
    """Human-readable findings report. Sections per the prompt."""
    path.parent.mkdir(parents=True, exist_ok=True)

    total = len(results)
    failed = [r for r in results if (not r.passed) and not (r.notes or "").startswith("skipped:")]
    skipped = [r for r in results if (r.notes or "").startswith("skipped:")]
    passed = total - len(failed) - len(skipped)

    # Per-identity rollup.
    identities = sorted({r.identity_name for r in results})
    rows_per_identity = []
    for ident in identities:
        sub = [r for r in results if r.identity_name == ident]
        s_total = len(sub)
        s_fail = sum(
            1 for r in sub
            if (not r.passed) and not (r.notes or "").startswith("skipped:")
        )
        s_skip = sum(1 for r in sub if (r.notes or "").startswith("skipped:"))
        s_pass = s_total - s_fail - s_skip
        rate = (s_pass / max(1, s_total - s_skip)) * 100.0 if s_total > s_skip else 0.0
        rows_per_identity.append((ident, s_total, s_pass, s_fail, s_skip, rate))

    # Per-ticker rollup.
    by_ticker: Dict[str, List[IdentityCheckResult]] = {}
    for r in results:
        by_ticker.setdefault(r.ticker, []).append(r)

    out: List[str] = []
    out.append(f"# Identity Audit Findings — {datetime.now(timezone.utc).strftime('%Y-%m-%d')}")
    out.append("")
    out.append("Phase-1 baseline run of the seven foundational accounting "
               "identities across the production universe. This is the "
               "findings report only — no fixes have been applied. "
               "See [tools/verification/identity_checks.py](../tools/verification/identity_checks.py) "
               "for the verification logic.")
    out.append("")

    out.append("## Executive summary")
    out.append("")
    out.append(f"- Total checks: **{total}**")
    out.append(f"- Passed: **{passed}** ({passed/total*100:.1f}%)")
    out.append(f"- Failed: **{len(failed)}** ({len(failed)/total*100:.1f}%)")
    out.append(f"- Skipped (no data): **{len(skipped)}** ({len(skipped)/total*100:.1f}%)")
    out.append(f"- Universe size: **{len(by_ticker)}** tickers")
    out.append(f"- Git SHA: `{_git_sha()[:12]}`")
    out.append("")
    out.append("Pass-rate excludes skipped checks (skipped is a tooling/"
               "coverage gap, not a data-quality finding).")
    out.append("")
    out.append("| Identity | Total | Pass | Fail | Skip | Pass-rate (ex-skip) |")
    out.append("|---|---|---|---|---|---|")
    for ident, t, p, f, s, rate in rows_per_identity:
        out.append(f"| `{ident}` | {t} | {p} | {f} | {s} | {rate:.1f}% |")
    out.append("")

    out.append("## Findings by identity")
    out.append("")
    for ident, t, p, f, s, rate in rows_per_identity:
        out.append(f"### `{ident}` — {f} failure(s), {s} skipped")
        out.append("")
        # Sort failures by absolute discrepancy (largest first) to
        # surface the most-material issues at the top of each section.
        ident_fails = [
            r for r in results
            if r.identity_name == ident
            and not r.passed
            and not (r.notes or "").startswith("skipped:")
        ]
        ident_fails.sort(
            key=lambda r: abs(r.discrepancy_abs or 0.0), reverse=True,
        )
        if not ident_fails:
            out.append("_No failures._")
            out.append("")
            continue
        out.append("Top 10 by absolute discrepancy:")
        out.append("")
        out.append("| Ticker | FY | Period | Discrepancy ($M) | % | Suggested category |")
        out.append("|---|---|---|---|---|---|")
        for r in ident_fails[:10]:
            cat = _suggest_category(r)
            disc_m = (r.discrepancy_abs or 0.0) / 1e6
            pct = r.discrepancy_pct or 0.0
            out.append(
                f"| {r.ticker} | {r.fiscal_year} | {r.period} | "
                f"{disc_m:+.1f} | {pct:+.2f}% | {cat} |"
            )
        out.append("")

    out.append("## Findings by ticker")
    out.append("")
    out.append("Tickers with at least one failure, ordered by failure count.")
    out.append("")
    ticker_fail_counts = [
        (
            t,
            sum(
                1 for r in rs
                if not r.passed and not (r.notes or "").startswith("skipped:")
            ),
            len(rs),
        )
        for t, rs in by_ticker.items()
    ]
    ticker_fail_counts.sort(key=lambda x: x[1], reverse=True)
    out.append("| Ticker | Failures | Total checks | Failing identities |")
    out.append("|---|---|---|---|")
    for t, fcount, tcount in ticker_fail_counts:
        if fcount == 0:
            continue
        idents = sorted({
            r.identity_name for r in by_ticker[t]
            if not r.passed and not (r.notes or "").startswith("skipped:")
        })
        out.append(f"| {t} | {fcount} | {tcount} | {', '.join(idents)} |")
    out.append("")

    out.append("## Recommended actions")
    out.append("")
    out.append(
        "Each finding above carries a *suggested* category — these are "
        "heuristic, not authoritative. The next step is analyst "
        "classification per the Category A/B/C/D scheme:"
    )
    out.append("")
    out.append("- **A — Source data quality**: SEC/FMP source is wrong. Fix at source or via override registry.")
    out.append("- **B — Cleaning engine gap**: Source is correct, cleaner doesn't handle this case. Extend cleaner.")
    out.append("- **C — Legitimate complexity**: M&A / FX / accounting change. Document as expected exception.")
    out.append("- **D — Methodology divergence**: Team decision required on correct approach.")
    out.append("")
    out.append("Next steps:")
    out.append("")
    out.append("1. Walk each failing identity's top-10 list and assign a category to each finding.")
    out.append("2. For each Category B finding, open a follow-up to extend the cleaning engine.")
    out.append("3. For each Category C finding, add an entry to `docs/data_quality_exceptions.md` (also new).")
    out.append("4. For Category A findings, evaluate whether the override registry or source-correction is the right path.")
    out.append("5. Re-run this audit after each batch of fixes; expect skip-rate to drop as Category B coverage extends.")

    path.write_text("\n".join(out))


def _suggest_category(r: IdentityCheckResult) -> str:
    """Heuristic category suggestion based on identity + ticker
    patterns documented in the prompt. Not authoritative — analyst
    classification overrides."""
    ident = r.identity_name
    ticker = r.ticker
    fy = r.fiscal_year
    # Known patterns from the prompt.
    if ident == "balance_sheet_equation" and ticker == "NEE":
        return "C (utility taxonomy — see A19/A15)"
    if ident == "ppe_rollforward" and ticker in {"ABT", "ADBE", "JPM", "NVDA"}:
        return "C (active acquirer — M&A year?)"
    if ident == "debt_rollforward" and fy == 2019:
        return "C (ASC 842 transition)"
    if ident in ("working_capital_AR", "working_capital_inventory", "working_capital_AP"):
        return "C (likely M&A / WC reclassification)"
    if ident == "fcf_pathway_reconciliation":
        return "B/C (SBC + deferred-tax non-cash items)"
    if ident == "cash_rollforward" and ticker in {"ASML", "TSM"}:
        return "B (FX effect not captured by cleaner)"
    return "?"


# ─────────────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="identity_checks",
        description="Run the seven-identity audit across the universe.",
    )
    p.add_argument(
        "--tickers", nargs="+", default=None,
        help="Specific tickers to audit (default: full UNIVERSE)",
    )
    p.add_argument(
        "--output-dir", type=Path, default=OUTPUT_DIR_DEFAULT,
        help="Directory for CSV + JSON outputs",
    )
    p.add_argument(
        "--docs-dir", type=Path, default=DOCS_DIR,
        help="Directory for the Markdown findings report",
    )
    p.add_argument("--verbose", action="store_true")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(message)s",
    )

    results = run_universe_audit(args.tickers)
    if not results:
        print("No results — empty universe or DB unavailable.", file=sys.stderr)
        return 1

    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    csv_path = args.output_dir / f"identity_audit_{date_str}.csv"
    json_path = args.output_dir / f"identity_audit_{date_str}.json"
    md_path = args.docs_dir / f"identity_audit_findings_{date_str}.md"

    _emit_csv(results, csv_path)
    _emit_json(results, json_path)
    _emit_markdown(results, md_path)

    print(f"audited {len({r.ticker for r in results})} tickers, "
          f"{len(results)} total checks")
    print(f"  CSV  → {csv_path}")
    print(f"  JSON → {json_path}")
    print(f"  MD   → {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
