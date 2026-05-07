from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional
from datetime import date

@dataclass(frozen=True)
class TickerClassification:
    ticker: str
    sector: str
    industry: str
    lifecycle: Literal["secular_hyper_growth", "hyper_growth", "high_growth_compounder",
                       "growth_compounder",
                       "growth_compounder_software",
                       "growth_compounder_consumer",
                       "growth_compounder_pharma",
                       "mature", "cyclical_industrial"]
    business_model: Literal["fcff_compatible", "ddm_required", "embedded_value_required", "routing_required"]
    notes: str
    last_reviewed: date

    # Filing taxonomy. True for 20-F filers using IFRS (TSM, ASML in current
    # universe). The TagResolver uses this to select IFRS priority lists in
    # FIELD_MAPPINGS instead of US-GAAP defaults. Defaults to False because
    # the universe is overwhelmingly US-GAAP; mark as True per-ticker only
    # when the filer's primary taxonomy is IFRS.
    is_ifrs_filer: bool = False

UNIVERSE: Dict[str, TickerClassification] = {
    # --- Technology / Software / Hardware ---
    "MSFT": TickerClassification("MSFT", "Technology", "Software", "growth_compounder_software", "fcff_compatible", "Cloud/AI tailwind, services-mix expanding margins", date.today()),
    "AAPL": TickerClassification("AAPL", "Technology", "Hardware", "growth_compounder_consumer", "fcff_compatible", "Hardware-led; services-mix software-like but hardware dominates revenue", date.today()),
    "GOOGL": TickerClassification("GOOGL", "Technology", "Internet", "growth_compounder_software", "fcff_compatible", "Ads + cloud, software economics", date.today()),
    "META": TickerClassification("META", "Technology", "Internet", "high_growth_compounder", "fcff_compatible", "Ads, slowing from hyper-growth to high-growth compounder", date.today()),
    "AMZN": TickerClassification("AMZN", "Technology", "E-Commerce/Cloud", "high_growth_compounder", "fcff_compatible", "AWS structural growth + retail GDP", date.today()),
    "ORCL": TickerClassification("ORCL", "Technology", "Software", "mature", "fcff_compatible", "Legacy enterprise software transitioning to cloud", date.today()),
    "SMCI": TickerClassification("SMCI", "Technology", "Hardware", "hyper_growth", "fcff_compatible", "Server hardware / AI capex. High growth but quality concerns", date.today()),

    # --- Semiconductors ---
    "NVDA": TickerClassification("NVDA", "Semiconductors", "Semiconductors", "secular_hyper_growth", "fcff_compatible", "Current-cycle AI capex linked", date.today()),
    "AMD": TickerClassification("AMD", "Semiconductors", "Semiconductors", "secular_hyper_growth", "fcff_compatible", "Current-cycle AI capex linked", date.today()),
    "ASML": TickerClassification("ASML", "Semiconductors", "Semiconductor Equipment", "high_growth_compounder", "fcff_compatible", "Semi capital equipment, AI structural growth", date.today(), is_ifrs_filer=True),
    "TSM": TickerClassification("TSM", "Semiconductors", "Semiconductors", "high_growth_compounder", "fcff_compatible", "Foundry, AI structural growth", date.today(), is_ifrs_filer=True),
    "QCOM": TickerClassification("QCOM", "Semiconductors", "Semiconductors", "mature", "fcff_compatible", "Mobile comms maturity", date.today()),
    "TXN": TickerClassification("TXN", "Semiconductors", "Semiconductors", "mature", "fcff_compatible", "Analog semi maturity", date.today()),

    # --- Auto ---
    "TSLA": TickerClassification("TSLA", "Auto Manufacturers", "Automotive", "high_growth_compounder", "fcff_compatible", "Auto manufacturing (rate sensitive). Slowing hyper-growth", date.today()),

    # --- Healthcare ---
    "UNH": TickerClassification("UNH", "Healthcare", "Managed Care", "mature", "ddm_required", "Float-based business; FCFF DCF inappropriate", date.today()),
    "CNC": TickerClassification("CNC", "Healthcare Plans", "Managed Care", "mature", "ddm_required", "Float-based business; FCFF DCF inappropriate", date.today()),
    "LLY": TickerClassification("LLY", "Healthcare", "Pharmaceuticals", "growth_compounder_pharma", "fcff_compatible", "Structural GLP-1 growth, sticky margins", date.today()),
    "ABT": TickerClassification("ABT", "Healthcare", "Medical Devices", "growth_compounder_pharma", "fcff_compatible", "Diversified healthcare; pharma-style margins", date.today()),

    # --- Financials ---
    "V": TickerClassification("V", "Financials", "Payments", "growth_compounder", "fcff_compatible", "Payments toll-bridge", date.today()),
    "JPM": TickerClassification("JPM", "Financials", "Banks", "mature", "routing_required", "Financial bank requiring specialized model", date.today()),
    "BRK-B": TickerClassification("BRK-B", "Financials", "Diversified", "mature", "routing_required", "Conglomerate/Insurer requiring specialized model", date.today()),

    # --- Consumer Defensive / Retail ---
    "COST": TickerClassification("COST", "Consumer Defensive", "Retail", "growth_compounder_consumer", "fcff_compatible", "Consumer-defensive compounder, modest margin improvement", date.today()),
    "WMT": TickerClassification("WMT", "Consumer Defensive", "Retail", "growth_compounder_consumer", "fcff_compatible", "Consumer defensive at scale, retail steady-state", date.today()),

    # --- Industrials ---
    "CAT": TickerClassification("CAT", "Industrials", "Heavy Machinery", "cyclical_industrial", "fcff_compatible", "Heavy machinery, peak cycle distortion", date.today()),
    "UNP": TickerClassification("UNP", "Industrials", "Railroads", "mature", "fcff_compatible", "Class I freight rail; mature, capital-intensive", date.today()),
    "NSC": TickerClassification("NSC", "Industrials", "Railroads", "mature", "fcff_compatible", "Class I freight rail; mature, capital-intensive", date.today()),
    "ITW": TickerClassification("ITW", "Industrials", "Diversified Industrial", "mature", "fcff_compatible", "Diversified industrial; high-quality compounder", date.today()),
    "EMR": TickerClassification("EMR", "Industrials", "Automation & Process", "mature", "fcff_compatible", "Industrial automation; process-control franchise", date.today()),

    # --- Consumer Defensive (Staples) ---
    "KO": TickerClassification("KO", "Consumer Defensive", "Beverages", "mature", "fcff_compatible", "Beverage staple; brand moat, mature growth", date.today()),
    "PEP": TickerClassification("PEP", "Consumer Defensive", "Beverages & Snacks", "mature", "fcff_compatible", "Beverage + snack staple; brand-driven mature compounder", date.today()),
    "PG": TickerClassification("PG", "Consumer Defensive", "Household Products", "mature", "fcff_compatible", "Household-products staple; brand portfolio", date.today()),

    # --- Consumer Discretionary ---
    "HD": TickerClassification("HD", "Consumer Discretionary", "Home Improvement Retail", "growth_compounder_consumer", "fcff_compatible", "Home-improvement retail; pro + DIY exposure", date.today()),
    "LOW": TickerClassification("LOW", "Consumer Discretionary", "Home Improvement Retail", "growth_compounder_consumer", "fcff_compatible", "Home-improvement retail; #2 share", date.today()),

    # --- Healthcare additions ---
    "JNJ": TickerClassification("JNJ", "Healthcare", "Pharmaceuticals & Med Devices", "mature", "fcff_compatible", "Diversified pharma + med-device; post-Kenvue spin", date.today()),
    "MRK": TickerClassification("MRK", "Healthcare", "Pharmaceuticals", "growth_compounder_pharma", "fcff_compatible", "Pharma; Keytruda concentration", date.today()),
    "MDT": TickerClassification("MDT", "Healthcare", "Medical Devices", "mature", "fcff_compatible", "Med-device; cardio + diabetes franchises", date.today()),

    # --- Financials (FCFF-compatible) ---
    "MCO": TickerClassification("MCO", "Financials", "Credit Ratings", "growth_compounder", "fcff_compatible", "Credit-rating duopoly + analytics; toll-bridge economics", date.today()),
    "AXP": TickerClassification("AXP", "Financials", "Payments & Cards", "growth_compounder", "routing_required", "Closed-loop card *issuer* — has consumer-lending book, files bank-style income statement (no OperatingIncome line). FCFF DCF can't run; needs schema-specific framework.", date.today()),

    # --- Technology Services ---
    "ACN": TickerClassification("ACN", "Technology", "IT Services & Consulting", "mature", "fcff_compatible", "IT services + consulting; mature growth", date.today()),

    # --- Utilities ---
    "NEE": TickerClassification("NEE", "Utilities", "Utilities", "mature", "routing_required", "Regulated utility. CapEx non-standard mapping", date.today()),
}


# ────────────────────────────────────────────────────────────────────────────
# Runtime classifications
#
# Tickers added through the Streamlit UI ("Add ticker" on the Universe tab)
# don't get curated entries above — they get sane defaults written to a
# sidecar JSON file. This keeps the Python source canonical/curated while
# letting the UI add tickers without code changes.
#
# `get_extended_universe()` returns the merged view: curated UNIVERSE +
# anything in valuation_data/runtime/ticker_classifications.json.
# ────────────────────────────────────────────────────────────────────────────

import json as _json
from pathlib import Path as _Path

_RUNTIME_CLASSIFICATIONS_PATH = _Path("valuation_data/runtime/ticker_classifications.json")


def _load_runtime_classifications() -> Dict[str, TickerClassification]:
    """Read sidecar JSON. Returns {} if file missing or malformed (fail-soft)."""
    if not _RUNTIME_CLASSIFICATIONS_PATH.exists():
        return {}
    try:
        raw = _json.loads(_RUNTIME_CLASSIFICATIONS_PATH.read_text())
    except Exception:
        return {}
    out: Dict[str, TickerClassification] = {}
    for ticker, entry in raw.items():
        try:
            out[ticker] = TickerClassification(
                ticker=ticker,
                sector=entry.get("sector", "Unknown"),
                industry=entry.get("industry", "Unknown"),
                lifecycle=entry.get("lifecycle", "growth_compounder"),
                business_model=entry.get("business_model", "fcff_compatible"),
                notes=entry.get("notes", "Auto-added via UI; classification needs review"),
                last_reviewed=date.fromisoformat(entry["last_reviewed"])
                              if entry.get("last_reviewed") else date.today(),
                is_ifrs_filer=bool(entry.get("is_ifrs_filer", False)),
            )
        except Exception:
            continue
    return out


def get_extended_universe() -> Dict[str, TickerClassification]:
    """Curated UNIVERSE + runtime-added entries. Curated wins on ticker collision."""
    merged = dict(_load_runtime_classifications())
    merged.update(UNIVERSE)
    return merged


def classify_from_profile(profile: Dict[str, Any]) -> Dict[str, Any]:
    """
    Map an FMP /profile response → classification fields.

    Rules (most-specific first):
      - industry contains "Banks" or "Capital Markets" → routing_required
        (asset/liability mismatch; standard FCFF DCF doesn't apply)
      - industry contains "Insurance - Diversified" → routing_required
        (BRK-B-style conglomerate insurance — not pure float)
      - industry contains "Insurance" or "Healthcare Plans" or "Managed
        Care" → ddm_required (float-based, dividend-discount appropriate)
      - sector == "Utilities" or industry contains "Regulated" →
        routing_required (rate-base + allowed-ROE model needed)
      - everything else → fcff_compatible (standard DCF)

    Foreign filer detection: country != "US" → is_ifrs_filer = True.
    Non-USD currency on a non-US filer reinforces this.

    Returns a dict ready to merge into the runtime sidecar.
    """
    sector   = (profile.get("sector")   or "").strip()
    industry = (profile.get("industry") or "").strip()
    country  = (profile.get("country")  or "").strip().upper()
    currency = (profile.get("currency") or "").strip().upper()
    desc     = (profile.get("description") or "").lower()

    s_lower = sector.lower()
    i_lower = industry.lower()

    # Business model — order matters; check most-specific patterns first.
    if "banks" in i_lower or "capital markets" in i_lower:
        business_model = "routing_required"
    elif "insurance - diversified" in i_lower or "insurance—diversified" in i_lower:
        business_model = "routing_required"
    elif "insurance" in i_lower or "healthcare plans" in i_lower or "managed care" in i_lower:
        business_model = "ddm_required"
    elif s_lower == "utilities" or "regulated" in i_lower:
        business_model = "routing_required"
    elif "credit services" in i_lower:
        # FMP groups card issuers (AXP, DFS, COF, SYF) and pure payment
        # networks (V, MA, PYPL) under the same "Credit Services" label.
        # Issuers carry a consumer-lending book and file bank-style income
        # statements — DCFEngine can't run on them. Networks earn pure
        # transaction fees and have standard operating-income statements.
        # Distinguish via the description: issuers describe themselves
        # using "card products" / "credit card issuer" / "lending"; networks
        # describe themselves as "payments technology" / "transaction
        # processing" / "facilitates payments".
        # Lending-specific signals — what makes a "Credit Services" filer
        # actually carry a consumer-lending book on the balance sheet (and
        # therefore file a bank-style income statement that FCFF DCF can't
        # handle). Pure payment networks (V, MA, PYPL) won't match any of
        # these; card issuers (AXP, COF, DFS, SYF) will match at least one.
        lending_signals = (
            "consumer lending",
            "consumer banking",
            "consumer financial services",
            "digital banking",
            "deposit product",
            "savings account",
            "installment loan",
            "personal loan",
            "private student loan",
            "credit cards to individuals",
            "credit cards to consumers",
            "card issuer",
            "issues credit",
            "charge and credit payment card",   # AXP-specific phrasing
        )
        if any(sig in desc for sig in lending_signals):
            business_model = "routing_required"
        else:
            # Default for "Credit Services" without lending signals
            # (V, MA, PYPL pure networks) — standard FCFF DCF works.
            business_model = "fcff_compatible"
    else:
        business_model = "fcff_compatible"

    # Foreign filer flag — IFRS taxonomy lookup in the tag resolver.
    # US filers are virtually always US-GAAP; non-US listings on US exchanges
    # (20-F filers like ASML, TSM) report under IFRS.
    is_ifrs_filer = bool(country) and country not in ("US", "USA")

    return {
        "sector":         sector or "Unknown",
        "industry":       industry or "Unknown",
        "lifecycle":      "growth_compounder",   # default — analyst can refine
        "business_model": business_model,
        "is_ifrs_filer":  is_ifrs_filer,
        "country":        country or None,
        "currency":       currency or None,
    }


def add_runtime_classification(
    ticker: str,
    sector: Optional[str] = None,
    industry: Optional[str] = None,
    lifecycle: Optional[str] = None,
    business_model: Optional[str] = None,
    notes: Optional[str] = None,
    is_ifrs_filer: Optional[bool] = None,
) -> bool:
    """
    Append a ticker to the runtime sidecar.

    If any field is omitted, the helper queries FMP `/profile` and derives
    sane defaults (sector/industry/business_model/is_ifrs_filer) from the
    response. Falls back to "Unknown" + fcff_compatible only if FMP is
    unavailable.

    No-op if the ticker is already in the curated UNIVERSE.
    Returns True if a new entry was written, False otherwise.
    """
    ticker = ticker.upper()
    if ticker in UNIVERSE:
        return False

    # Auto-classify from FMP profile when caller didn't provide explicit values.
    classification = {
        "sector":         "Unknown",
        "industry":       "Unknown",
        "lifecycle":      "growth_compounder",
        "business_model": "fcff_compatible",
        "is_ifrs_filer":  False,
        "country":        None,
        "currency":       None,
    }
    auto_note = "Auto-added via UI; classification needs review"
    try:
        from aletheia.data import fmp_client
        profile = fmp_client.fetch_profile(ticker)
        if profile:
            classification.update(classify_from_profile(profile))
            auto_note = (
                f"Auto-classified from FMP profile {date.today().isoformat()} "
                f"(sector='{classification['sector']}', "
                f"industry='{classification['industry']}', "
                f"country='{classification.get('country') or 'unknown'}'). "
                "Lifecycle defaulted to growth_compounder — refine if needed."
            )
    except Exception:
        # FMP unavailable; sticks with the conservative defaults above.
        pass

    # Caller-provided values override anything FMP gave us.
    if sector is not None:         classification["sector"] = sector
    if industry is not None:       classification["industry"] = industry
    if lifecycle is not None:      classification["lifecycle"] = lifecycle
    if business_model is not None: classification["business_model"] = business_model
    if is_ifrs_filer is not None:  classification["is_ifrs_filer"] = is_ifrs_filer
    final_notes = notes if notes is not None else auto_note

    _RUNTIME_CLASSIFICATIONS_PATH.parent.mkdir(parents=True, exist_ok=True)
    existing: Dict[str, Any] = {}
    if _RUNTIME_CLASSIFICATIONS_PATH.exists():
        try:
            existing = _json.loads(_RUNTIME_CLASSIFICATIONS_PATH.read_text())
        except Exception:
            existing = {}
    existing[ticker] = {
        "sector":         classification["sector"],
        "industry":       classification["industry"],
        "lifecycle":      classification["lifecycle"],
        "business_model": classification["business_model"],
        "notes":          final_notes,
        "is_ifrs_filer":  classification["is_ifrs_filer"],
        "country":        classification.get("country"),
        "currency":       classification.get("currency"),
        "last_reviewed":  date.today().isoformat(),
    }
    _RUNTIME_CLASSIFICATIONS_PATH.write_text(_json.dumps(existing, indent=2, sort_keys=True))
    return True
