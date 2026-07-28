"""Altman distress screen (Phase 1a).

A distress SCREEN for the levered subset of standard (FCFF) corporates — NOT a
universe-ranked metric and NOT a valuation input. It answers one question: is
this company at risk of financial distress? Zones are compared, never raw
scores, and never across archetypes.

Design (converged over review; see project memory):

  * Model — Z'' is ALWAYS authoritative for the zone:
        Z'' = 6.56·WC/TA + 3.26·RE/TA + 6.72·EBIT/TA + 1.05·BookEq/TL
        zones: >2.6 safe · 1.1-2.6 grey · <1.1 distress
    Original Z (adds market-equity + sales terms) is computed for manufacturers
    as an ADVISORY cross-read only — it never sets the zone.

  * Exclusions — financials / float-based / specialized-model tickers are
    not_applicable(non_fcff): Altman is undefined for their balance sheets.

  * Buyback artifacts — a company that returned more capital than it retained
    carries negative RE and can carry negative book equity from a pure policy
    choice, not distress. We DON'T reconstruct a counterfactual balance sheet
    (15y of buybacks overshoots wildly and is untested); we ABSTAIN:
        capital_return + book equity < 0            -> not_applicable(capital_structure)
        capital_return + |RE|/TA >= 10%             -> not_applicable(capital_structure)
        capital_return + |RE|/TA <  10% (noise)     -> score raw
    |RE|/TA rationale: healthy non-financials run RE/TA ~10-40%; a negative RE
    under 10% of assets is within noise, larger materially distorts the term.

  * accumulated_deficit (real losses) -> score raw; the negatives are the signal.

  * Classification (capital_return vs accumulated_deficit): sum of net income
    over available fiscal years (>= 8y). SigmaNI > 3·|RE| -> capital_return.
    Under 8y coverage -> unknown: any negative term abstains
    (insufficient_history); all-positive scores with confidence=low.

  * Leverage gate — a distress read is only ACTIONABLE when the name is levered
    (net_debt/EBITDA > 1 or gross_debt/TA > 15%). Net-cash names are silent.

  * Corroboration guard — distress is only confirmed when a NON-capital-structure
    term agrees: EBIT/TA < 0 (universal), or interest coverage < 2x, or
    (current_ratio < 0.8 and short-term borrowings rising) for NON-retail only
    (negative working capital is structural strength in retail, so WC/current
    ratio can't corroborate distress there).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Z'' zones (authoritative)
_Z2_SAFE, _Z2_DISTRESS = 2.6, 1.1
# original Z zones (advisory, manufacturers)
_Z_SAFE, _Z_DISTRESS = 2.99, 1.81

_RE_NOISE_FRAC = 0.10          # |RE|/TA below this is noise -> score raw
_MIN_NI_YEARS = 8             # coverage floor to CONFIRM capital_return
_MIN_DEFICIT_YEARS = 3        # light floor for accumulated_deficit (losses are unambiguous)
_LEV_NETDEBT_EBITDA = 1.0
_LEV_DEBT_TA = 0.15
_COVERAGE_DISTRESS = 2.0      # interest coverage below this corroborates
_CURRENT_RATIO_DISTRESS = 0.8

# Genuinely Altman-undefined: financial INTERMEDIARIES where leverage IS the
# business and there is no operating working-capital cycle — banks, lenders,
# card issuers, insurers, brokers, diversified financials. This is INDEPENDENT
# of which valuation engine a ticker routes to. Utilities (rate_base), REITs,
# MLPs, payment networks, rating agencies, and managed care all have definable
# Altman inputs and route to a specialized VALUATION model for reasons unrelated
# to distress applicability, so they are NOT excluded. (business_model is
# deliberately NOT used here — conflating valuation-routing with Altman
# applicability was the original over-exclusion bug.)
_UNDEFINED_INDUSTRY_KEYS = (
    "bank", "credit service", "cards", "insurance", "reinsurance",
    "capital market", "asset manage", "broker", "mortgage", "thrift",
)
# Managed care reads as Healthcare but has a definable claims-payable working-
# capital cycle → includable despite carrying float.
_MANAGED_CARE_KEYS = ("managed care", "health plan")
# Capital-intensive, continuously-refinancing archetypes — utilities, REITs,
# MLPs/midstream. They run structurally thin current ratios and roll short-term
# debt by design, so liquidity signals are NOT distress for them (same class as
# retail WC / managed-care float).
_CAPITAL_INTENSIVE_KEYS = ("utilit", "reit", "real estate", "midstream", "pipeline", "mlp")
_RETAIL_KEYS = ("retail", "restaurant", "grocery", "apparel", "store",
                "home improvement", "consumer defensive")
_MANUFACTURER_KEYS = ("industrial", "material", "machinery", "auto",
                      "manufactur", "semiconductor", "hardware", "chemical")


@dataclass
class AltmanScreen:
    ticker: str
    fiscal_year: int
    scoreable: bool
    zone: Optional[str] = None                 # safe | grey | distress
    reason: Optional[str] = None               # set when not scoreable
    model_authoritative: str = "Z''"
    z_double_prime: Optional[float] = None
    z_original: Optional[float] = None          # advisory (manufacturers only)
    classification: Optional[str] = None        # none|capital_return|accumulated_deficit|unknown
    re_reason: Optional[str] = None
    leverage_gated: bool = False                # True = levered enough to act on
    corroborated: Optional[bool] = None
    actionable_distress: bool = False           # zone==distress AND levered AND corroborated
    float_distorted: bool = False               # float archetype: raw zone understated (claims-payable)
    confidence: str = "normal"                  # normal | low
    facts: Dict[str, Any] = field(default_factory=dict)
    inputs: Dict[str, Any] = field(default_factory=dict)


def _num(x: Any) -> Optional[float]:
    try:
        if x is None or x == "":
            return None
        v = float(x)
        return v if v == v else None       # NaN -> None (NaN is silently truthy)
    except (TypeError, ValueError):
        return None


def _zone_from_z2(z: float) -> str:
    if z > _Z2_SAFE:
        return "safe"
    if z < _Z2_DISTRESS:
        return "distress"
    return "grey"


def _is_financial(cls: Dict[str, Any]) -> bool:
    """True only for genuine financial intermediaries (Altman-undefined) —
    banks, lenders, card issuers, insurers, brokers, diversified financials.
    Utilities / REITs / MLPs / payment networks / rating agencies / managed care
    are NOT financial for this purpose even though they route to specialized
    valuation engines."""
    sector = (cls.get("sector") or "").lower()
    industry = (cls.get("industry") or "").lower()
    text = f"{sector} {industry}"
    if any(k in text for k in _MANAGED_CARE_KEYS):
        return False                                      # includable despite float
    if any(k in text for k in _UNDEFINED_INDUSTRY_KEYS):
        return True
    # Diversified financials (e.g. BRK-B: Financials / Diversified).
    return sector == "financials" and "diversified" in industry


def _archetype(cls: Dict[str, Any],
               inventory_ta: Optional[float] = None,
               ppe_ta: Optional[float] = None) -> str:
    """Single source of truth for archetype-specific screen behavior. Returns
    'float' | 'retail' | 'manufacturer' | 'default'. Corroboration validity and
    the advisory model key off this, so a new archetype is added HERE once rather
    than as scattered inline exceptions (which don't scale past a couple).

    Float/retail are industry-driven (checked first, so a retailer never reaches
    the manufacturer test). Manufacturer is STRUCTURAL — inventory- or PP&E-heavy
    (Inventory/TA > 5% or PP&E/TA > 20%) — with the industry keyword as a fallback
    when the ratios aren't available. It only drives the advisory Z, never the zone.
    """
    text = f"{cls.get('sector','')} {cls.get('industry','')}".lower()
    if any(k in text for k in _MANAGED_CARE_KEYS):
        return "float"          # claims-payable inflates current liabilities
    if any(k in text for k in _RETAIL_KEYS):
        return "retail"         # supplier financing -> negative WC is structural
    if any(k in text for k in _CAPITAL_INTENSIVE_KEYS):
        return "capital_intensive"   # utilities/REITs/MLPs: structural thin liquidity + refinancing
    if (inventory_ta is not None and inventory_ta > 0.05) or (ppe_ta is not None and ppe_ta > 0.20):
        return "manufacturer"
    if inventory_ta is None and ppe_ta is None and any(k in text for k in _MANUFACTURER_KEYS):
        return "manufacturer"   # keyword fallback when ratios unavailable
    return "default"


# Archetypes where negative working capital / a low current ratio is STRUCTURAL,
# not distress — so liquidity-based signals may NOT corroborate. EBIT/TA and
# interest coverage (which mean the same in every sector) remain the only paths
# to actionable. A new WC-inverted archetype joins this set; the corroboration
# logic itself never grows. This makes the managed-care veto STRUCTURAL rather
# than incidental on the leverage gate.
_WC_STRUCTURAL_ARCHETYPES = {"retail", "float", "capital_intensive"}


def screen_distress(
    latest: Dict[str, Any],
    ni_history: List[float],
    cls: Dict[str, Any],
    *,
    price: Optional[float] = None,
    shares: Optional[float] = None,
    interest_expense: Optional[float] = None,
    prior_short_term_debt: Optional[float] = None,
) -> AltmanScreen:
    """Score one ticker's latest fiscal year. ``latest`` is a cleaned-record
    dict (promoted columns); ``ni_history`` is net income over available FYs;
    ``cls`` carries sector / industry / business_model."""
    tkr = str(latest.get("ticker") or cls.get("ticker") or "?")
    fy = int(_num(latest.get("fiscal_year")) or 0)
    out = AltmanScreen(ticker=tkr, fiscal_year=fy, scoreable=False)

    # 0. Exclude genuine financial intermediaries (Altman-undefined). NOT
    #    utilities / REITs / MLPs / payment networks / rating agencies / managed
    #    care — those route to specialized valuation but have definable inputs.
    if _is_financial(cls):
        out.reason = "not_applicable(financial): Altman undefined for bank/insurer/lender balance sheets"
        return out

    # 1. Inputs (from promoted, validated columns).
    ta = _num(latest.get("raw_TotalAssets"))
    tl = _num(latest.get("raw_TotalLiabilities"))
    re = _num(latest.get("raw_RetainedEarnings"))
    eq = _num(latest.get("raw_TotalEquity"))
    ca = _num(latest.get("raw_CurrentAssets"))
    cl = _num(latest.get("raw_LiabilitiesCurrent"))
    # Operating earning power for the distress read should EXCLUDE one-time
    # charges — a goodwill impairment is an accounting event, not operating
    # distress, and would otherwise false-corroborate EBIT/TA in the writedown
    # year. Prefer the ex-unusual figure when the cleaning engine has stripped
    # it; fall back to normalized/reported. (Ex-unusual is currently NaN for
    # several names — a cleaning-engine gap to close before the S&P-500
    # expansion, where impairment years are common.)
    ebit = (_num(latest.get("clean_OperatingIncome_ExUnusual"))
            or _num(latest.get("clean_NormalizedEBIT"))
            or _num(latest.get("derived_OperatingIncome")))
    sales = _num(latest.get("clean_Revenue")) or _num(latest.get("raw_Revenue"))
    ebitda = _num(latest.get("derived_EBITDA"))
    net_debt = _num(latest.get("derived_NetDebt"))
    ltd = _num(latest.get("raw_LongTermDebt")) or 0.0
    cpltd = _num(latest.get("raw_CurrentPortionLongTermDebt")) or 0.0
    gross_debt = ltd + cpltd
    inventory = _num(latest.get("raw_Inventory"))
    ppe = _num(latest.get("raw_PPE"))
    short_term_debt = _num(latest.get("raw_ShortTermDebt"))
    # interest_expense arg wins (callers may pass it); else the promoted column.
    if interest_expense is None:
        interest_expense = _num(latest.get("raw_InterestExpense"))

    if not ta or ta <= 0 or re is None or eq is None or ebit is None or tl is None:
        out.reason = "not_applicable(insufficient_data): missing a required balance-sheet input"
        return out
    wc = (ca - cl) if (ca is not None and cl is not None) else None

    out.inputs = {"TA": ta, "TL": tl, "RE": re, "BookEq": eq, "EBIT": ebit,
                  "WC": wc, "Sales": sales, "net_debt": net_debt, "gross_debt": gross_debt}

    # 2. Leverage gate — is a distress read even actionable for this name?
    levered = ((net_debt is not None and ebitda and ebitda > 0 and net_debt / ebitda > _LEV_NETDEBT_EBITDA)
               or (gross_debt / ta > _LEV_DEBT_TA))
    out.leverage_gated = bool(levered)

    # 3. Classify a negative RE before scoring. The >=8y floor gates ONLY the
    #    capital_return determination — distinguishing "returned more than
    #    retained" from real losses needs a confirmed profitable history. Net
    #    cumulative LOSSES are unambiguous distress even at short history (a
    #    young, money-losing company is exactly the target profile), so
    #    accumulated_deficit needs only a light sanity floor.
    re_ta = re / ta
    if re >= 0:
        out.classification = "none"
        out.re_reason = "re_positive"
    else:
        vals = [x for x in ni_history if x is not None]
        n = len(vals)
        sigma_ni = sum(vals)
        if sigma_ni < 0:
            # Net cumulative losses -> accumulated_deficit (unambiguous).
            if n < _MIN_DEFICIT_YEARS:
                out.classification = "unknown"
                out.reason = (f"not_applicable(insufficient_history): {n}y coverage "
                              f"below the {_MIN_DEFICIT_YEARS}y floor")
                return out
            out.classification = "accumulated_deficit"
        elif n < _MIN_NI_YEARS:
            # Positive cumulative income but negative RE, too little history to
            # confirm capital_return -> abstain if any other term is negative.
            out.classification = "unknown"
            neg_term = (ebit < 0) or (eq < 0) or (wc is not None and wc < 0)
            if neg_term:
                out.reason = f"not_applicable(insufficient_history): {n}y net-income coverage, cannot classify negative RE"
                return out
            out.confidence = "low"
            out.re_reason = "unknown_shorthistory_allpositive"
        elif sigma_ni > 3 * abs(re):
            out.classification = "capital_return"
        else:
            out.classification = "accumulated_deficit"

    # 4. Buyback-artifact handling — abstain rather than reconstruct.
    facts = {
        "ebit_positive": ebit > 0,
        "re_ta": round(re_ta, 3),
        "book_equity_negative": eq < 0,
        "levered": bool(levered),
        "net_debt_ebitda": (round(net_debt / ebitda, 2) if (net_debt is not None and ebitda and ebitda > 0) else None),
    }
    if out.classification == "capital_return":
        if eq < 0:
            out.reason = "not_applicable(capital_structure): negative book equity from capital return; reconstruction implausible"
            out.facts = facts
            return out
        if abs(re_ta) >= _RE_NOISE_FRAC:
            out.reason = (f"not_applicable(capital_structure): RE {re_ta:+.0%} of assets from capital return "
                          "materially distorts the RE term")
            out.facts = facts
            return out
        out.re_reason = "capital_return_noise"   # |RE|/TA < 10% -> score raw

    # 5. Compute Z'' (authoritative). WC may be None for odd balance sheets.
    wc_ta = (wc / ta) if wc is not None else 0.0
    z2 = 6.56 * wc_ta + 3.26 * re_ta + 6.72 * (ebit / ta) + 1.05 * (eq / tl)
    out.z_double_prime = round(z2, 3)
    out.zone = _zone_from_z2(z2)
    out.scoreable = True
    archetype = _archetype(cls,
                           inventory_ta=(inventory / ta) if inventory is not None else None,
                           ppe_ta=(ppe / ta) if ppe is not None else None)
    # For float archetypes (managed care), claims-payable inflates current
    # liabilities and drags WC/TA negative, understating Z''. Flag it so the raw
    # zone reads honestly until the claims-payable source fix (net it out of CL)
    # lands. The corroboration guard below already refuses to act on this.
    out.float_distorted = archetype == "float"

    # Advisory original Z for manufacturers (never sets the zone).
    if archetype == "manufacturer" and sales and price and shares:
        mkt_eq = price * shares
        z = (1.2 * wc_ta + 1.4 * re_ta + 3.3 * (ebit / ta)
             + 0.6 * (mkt_eq / tl) + 1.0 * (sales / ta))
        out.z_original = round(z, 3)

    # 6. Corroboration guard — confirm distress only with a non-capital-structure
    #    term. Liquidity/WC signals corroborate ONLY where negative WC is not
    #    structural (archetype-parameterized) — so managed care and retail can
    #    reach actionable only via EBIT/TA or interest coverage, which mean the
    #    same in every sector. This makes the veto structural, not a side effect
    #    of the leverage gate happening to be closed.
    coverage = (ebit / abs(interest_expense)) if (interest_expense not in (None, 0)) else None
    current_ratio = (ca / cl) if (ca and cl and cl != 0) else None
    # Rising short-term borrowings: prefer the real ShortTermDebt line; fall back
    # to current-portion-of-LTD only when ShortTermDebt is absent.
    _std_now = short_term_debt if short_term_debt is not None else cpltd
    st_rising = (prior_short_term_debt is not None and _std_now > prior_short_term_debt)
    liquidity_can_corroborate = archetype not in _WC_STRUCTURAL_ARCHETYPES
    corr_ebit = ebit / ta < 0
    corr_cov = coverage is not None and coverage < _COVERAGE_DISTRESS
    corr_liq = (liquidity_can_corroborate and current_ratio is not None
                and current_ratio < _CURRENT_RATIO_DISTRESS and st_rising)
    out.corroborated = bool(corr_ebit or corr_cov or corr_liq)
    facts["interest_coverage"] = round(coverage, 2) if coverage is not None else None
    facts["current_ratio"] = round(current_ratio, 2) if current_ratio is not None else None
    out.facts = facts

    # Actionable distress = distress zone AND levered AND corroborated.
    out.actionable_distress = bool(
        out.zone == "distress" and out.leverage_gated and out.corroborated
    )
    return out


def screen_ticker(db, ticker: str) -> AltmanScreen:
    """Convenience wrapper: pull the latest record + NI history + classification
    from the DB and run the screen."""
    import json
    from config.ticker_classification import UNIVERSE

    df = db.get_latest(ticker)
    if df is None or df.empty:
        return AltmanScreen(ticker=ticker, fiscal_year=0, scoreable=False,
                            reason="not_applicable(no_data)")
    df = df.sort_values("fiscal_year")
    latest = df.iloc[-1].to_dict()
    ni_history = [(_num(v)) for v in df.get("raw_NetIncome", [])]
    ni_history = [x for x in ni_history if x is not None]

    c = UNIVERSE.get(ticker)
    cls = {"ticker": ticker,
           "sector": getattr(c, "sector", None),
           "industry": getattr(c, "industry", None),
           "business_model": getattr(c, "business_model", None)}

    # Interest expense from the promoted column (falls back to the blob only for
    # rows not yet backfilled). Price is still blob/quote-sourced.
    interest_expense = _num(latest.get("raw_InterestExpense"))
    if interest_expense is None:
        try:
            interest_expense = _num(json.loads(latest.get("raw_json") or "{}").get("InterestExpense"))
        except Exception:
            interest_expense = None
    price = _num(latest.get("current_price"))
    if price is None:
        try:
            price = _num(json.loads(latest.get("raw_json") or "{}").get("Price"))
        except Exception:
            price = None
    shares = _num(latest.get("raw_SharesDiluted")) or _num(latest.get("clean_SharesDiluted"))
    prior_std = None
    if len(df) >= 2:
        prev = df.iloc[-2].to_dict()
        prior_std = (_num(prev.get("raw_ShortTermDebt"))
                     or _num(prev.get("raw_CurrentPortionLongTermDebt")))

    return screen_distress(latest, ni_history, cls, price=price, shares=shares,
                           interest_expense=interest_expense, prior_short_term_debt=prior_std)


__all__ = ["AltmanScreen", "screen_distress", "screen_ticker"]
