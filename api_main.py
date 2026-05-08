"""
api/main.py

Aletheia FastAPI Backend
========================
Serves cleaned investment data from DuckDB + serving reports.
Streamlit (and any other client) consumes this API.

Endpoints:
    GET  /health                    — health check + data freshness
    GET  /universe                  — all tickers summary ranked by conviction
    GET  /ticker/{ticker}           — full report for one ticker
    GET  /ticker/{ticker}/dcf       — DCF scenarios only
    GET  /ticker/{ticker}/screening — 34-metric screening scorecard
    GET  /ticker/{ticker}/fundamentals — Phase 1 cleaned financials
    GET  /screens/universe          — screening comparison across all tickers
    POST /pipeline/run/{ticker}     — trigger pipeline run (optional)

Run:
    uvicorn api.main:app --reload --port 8000

Or from project root:
    PYTHONPATH=. uvicorn api.main:app --reload --port 8000
"""

import json
import logging
import math
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, List, Dict, Any

# Load .env at module import so GOOGLE_API_KEY (and other secrets) are
# available to thesis_synthesizer when uvicorn starts the API. Without
# this, the synthesizer silently falls back to mock — and the
# thesis_synthesis/refresh endpoint correctly 503s, but the analyst has
# to figure out why.
try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ─────────────────────────────────────────────────────────────────────────────
# Config
# ─────────────────────────────────────────────────────────────────────────────

REPORT_DIR  = Path("valuation_data/serving/latest")
UNIVERSE    = ["AAPL", "MSFT", "NVDA", "GOOGL", "META", "AMZN", "TSLA", "CNC"]
API_VERSION = "1.0.0"

app = FastAPI(
    title="Aletheia Investment Intelligence API",
    description="Multi-agent investment analysis system — Phase 3",
    version=API_VERSION,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # restrict in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─────────────────────────────────────────────────────────────────────────────
# Observability — request timing
# ─────────────────────────────────────────────────────────────────────────────
# Logs every /ticker/* request with endpoint, ticker, latency_ms, source. The
# `source` tag tells us at a glance whether a response came from a live DB
# compute path or a JSON-file read — useful when comparing pre/post latency
# during the JSON→DB-as-truth migration. p95/p99 can be derived offline by
# parsing the log; no external metrics dep.

_perf_logger = logging.getLogger("aletheia.api.perf")
if not _perf_logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s perf %(message)s"))
    _perf_logger.addHandler(_h)
    _perf_logger.setLevel(logging.INFO)
    _perf_logger.propagate = False


@app.middleware("http")
async def _timing_middleware(request: Request, call_next):
    t0 = time.perf_counter()
    response = await call_next(request)
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    path = request.url.path
    if path.startswith("/ticker/") or path == "/universe":
        # Source defaults to "unknown"; handlers may set X-Aletheia-Source
        # (e.g. "db_compute" or "json_read") to disambiguate during the
        # migration.
        source = response.headers.get("x-aletheia-source", "unknown")
        # Pull ticker token from /ticker/{T}/...
        parts = path.split("/")
        ticker = parts[2] if len(parts) >= 3 and parts[1] == "ticker" else "-"
        endpoint = "/" + "/".join(["ticker", "{T}"] + parts[3:]) if parts[1] == "ticker" else path
        _perf_logger.info(
            "endpoint=%s ticker=%s status=%d latency_ms=%.1f source=%s",
            endpoint, ticker, response.status_code, elapsed_ms, source,
        )
    return response


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe(val, fallback=None):
    if val is None:
        return fallback
    try:
        f = float(val)
        return fallback if math.isnan(f) else f
    except (TypeError, ValueError):
        return fallback


# `_load_report` was removed in Step 3 of the JSON→DB-as-truth migration.
# Reading the entire report.json file is no longer the right path:
# deterministic blocks are recomputed live from `company_records_latest`
# via `_compute_dcf_live`, and LLM-authored blocks come from
# `agent_runs_latest` via `_load_llm_payload`. The only remaining
# legitimate JSON read is the section-3 narrative merge in
# `/capital_structure` (which inlines its own file read).


# ─────────────────────────────────────────────────────────────────────────────
# Deterministic recompute helpers (DB-as-truth)
# ─────────────────────────────────────────────────────────────────────────────

def _load_llm_payload(ticker: str) -> Optional[Dict[str, Any]]:
    """Return the four LLM-authored sections for a ticker, sourced from
    `agent_runs_latest` when available, falling back to the legacy
    `_report.json` during the JSON→DB migration window.

    Return shape (all keys always present, values may be empty):
        {
            "ticker":              str,
            "generated_at":        ISO8601 str | None,
            "source":              "db" | "json" | None,
            "git_sha":             str | None,
            "version":             int | None,
            "economic_reality":    dict,
            "contrarian_analysis": dict,
            "investment_thesis":   dict,
            "agent_scenarios":     list,
        }

    Returns None only when neither the DB nor the JSON has data — i.e.
    the ticker is pending (no agent run yet).

    The JSON fallback exists because Step 2 only dual-writes new runs;
    pre-existing reports (the original 25 tickers) live as files only
    until they are re-run. Once every ticker has been re-run after Step
    2, the JSON branch becomes dead code and can be removed.
    """
    ticker_u = ticker.upper()

    # 1. DB first
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        row = db.get_latest_agent_run(ticker_u)
        db.close()
        if row is not None:
            return {
                "ticker":              ticker_u,
                "generated_at":        row.get("generated_at"),
                "source":              "db",
                "git_sha":             row.get("git_sha"),
                "version":             row.get("version"),
                "economic_reality":    row.get("economic_reality")    or {},
                "contrarian_analysis": row.get("contrarian_analysis") or {},
                "investment_thesis":   row.get("investment_thesis")   or {},
                "agent_scenarios":     row.get("agent_scenarios")     or [],
            }
    except Exception:
        # Fall through to JSON — DB is best-effort, never blocking.
        pass

    # 2. Legacy JSON fallback
    path = REPORT_DIR / f"{ticker_u}_report.json"
    if path.exists():
        try:
            report = json.loads(path.read_text())
        except Exception:
            return None
        synthesis = report.get("4_valuation_synthesis", {}) or {}
        return {
            "ticker":              ticker_u,
            "generated_at":        report.get("generated_at"),
            "source":              "json",
            "git_sha":             None,
            "version":             None,
            "economic_reality":    report.get("1_economic_reality")    or {},
            "contrarian_analysis": synthesis.get("contrarian_analysis") or {},
            "investment_thesis":   synthesis.get("investment_thesis")   or {},
            "agent_scenarios":     synthesis.get("agent_scenarios")     or [],
        }

    return None


def _compute_dcf_live(ticker: str) -> Dict[str, Any]:
    """Run DCFEngine + ReverseDCF live against the cleaned DB and return a
    dict shaped like `DCFResponse`.

    Raises HTTPException(404) if there are no DB rows for the ticker (never
    ingested), and HTTPException(422) if the engine fails on a schema
    mismatch (e.g. AXP — card-network filer with no `OperatingIncome`).

    Used by `/ticker/{T}/dcf` (always) and by `_calc_only_summary` (which
    flattens this into the universe row schema). Keep the two consumers in
    sync via this single helper.
    """
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.tools.dcf_engine import DCFEngine
    from aletheia.tools.reverse_dcf import ReverseDCF

    try:
        calc = make_calc_input(ticker)
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail=f"No cleaned data for {ticker}: {type(e).__name__}: {e}",
        )
    if calc.df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No cleaned data for {ticker} (empty DB rows).",
        )

    try:
        result = DCFEngine(verbose=False).run(calc)
    except Exception as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"DCFEngine cannot value {ticker}: {type(e).__name__}: {e}. "
                f"This typically indicates a schema-mismatch filer "
                f"(e.g. card networks, banks, REITs) requiring a non-FCFF "
                f"valuation framework."
            ),
        )

    def scenario_dict(scenario) -> Optional[Dict[str, Any]]:
        if scenario is None:
            return None
        ev = float(scenario.enterprise_value)
        ips = result.intrinsic_per_share(ev, result.net_debt)
        mos = result.upside(ips) if ips else None
        return {
            "intrinsic_per_share": float(ips) if ips is not None else None,
            "margin_of_safety":    float(mos) if mos is not None else None,
            "ev":                  ev,
        }

    base = result.base
    market_ev_ebitda    = float(base.implied_ev_ebitda)   if base else None
    justified_ev_ebitda = float(base.justified_ev_ebitda) if base else None
    premium = (
        market_ev_ebitda / justified_ev_ebitda - 1.0
        if (market_ev_ebitda and justified_ev_ebitda) else None
    )
    if premium is None:
        mult_signal = None
    elif premium < 0:
        mult_signal = "undervalued"
    elif premium < 0.20:
        mult_signal = "fairly_valued"
    elif premium < 0.50:
        mult_signal = "premium"
    elif premium < 1.00:
        mult_signal = "high_premium"
    else:
        mult_signal = "speculative_premium"

    roic_wacc_spread = float(base.roic_wacc_spread) if base else None
    value_creation = (
        "creating" if (roic_wacc_spread is not None and roic_wacc_spread > 0)
        else "destroying" if (roic_wacc_spread is not None and roic_wacc_spread < 0)
        else None
    )

    multiple_decomposition = {
        "market_ev_ebitda":    market_ev_ebitda,
        "justified_ev_ebitda": justified_ev_ebitda,
        "premium_pct":         premium,
        "signal":              mult_signal,
        "roic_wacc_spread":    roic_wacc_spread,
        "value_creation":      value_creation,
        "roic":                float(result.roic) if result.roic else None,
        "wacc":                float(result.wacc_base) if result.wacc_base else None,
    }

    # Reverse DCF — same tool the agent path uses, so signal classification
    # (`deep_value` / `fair_value` / `priced_for_growth` / `caution` / `flag`)
    # matches what reports show for ready tickers.
    reverse_dcf: Optional[Dict[str, Any]] = None
    try:
        rdcf_result = ReverseDCF(verbose=False).run(calc)
        reverse_dcf = rdcf_result.to_dict()
        # The agent-written field is `signal_reasons`; surface it as
        # `reasons` for UI compatibility with the JSON-shaped output.
        reverse_dcf["reasons"] = list(getattr(rdcf_result, "signal_reasons", []) or [])
    except Exception:
        reverse_dcf = None

    return {
        "ticker":                 ticker.upper(),
        "wacc":                   float(result.wacc_base) if result.wacc_base else None,
        "beta":                   float(result.beta) if result.beta else None,
        "risk_free_rate":         float(result.risk_free_rate) if result.risk_free_rate else None,
        "bear":                   scenario_dict(result.bear),
        "base":                   scenario_dict(result.base),
        "bull":                   scenario_dict(result.bull),
        "reverse_dcf":            reverse_dcf,
        "multiple_decomposition": multiple_decomposition,
        # Carry-along fields for `_calc_only_summary` consumers (not in
        # DCFResponse schema). Kept on the dict so we don't run the engine
        # twice.
        "_result":                result,
    }


# `_extract_summary` was removed in Step 3. The universe loop now calls
# `_calc_only_summary` for every ticker, which recomputes deterministic
# blocks from the DB and overlays LLM data via `_load_llm_payload`.


# ─────────────────────────────────────────────────────────────────────────────
# Pydantic response models
# ─────────────────────────────────────────────────────────────────────────────

class HealthResponse(BaseModel):
    status: str
    version: str
    tickers_available: List[str]
    tickers_missing: List[str]
    report_dir: str


class TickerSummary(BaseModel):
    ticker: str
    generated_at: Optional[str]
    conviction: Optional[int]
    moat: Optional[float]
    base_iv: Optional[float]
    bear_iv: Optional[float]
    bull_iv: Optional[float]
    base_mos: Optional[float]
    implied_cagr: Optional[float]
    historical_cagr: Optional[float]
    implied_hist_ratio: Optional[float]
    rdcf_signal: Optional[str]
    ev_ebitda: Optional[float]
    justified_ev_ebitda: Optional[float]
    multiple_premium: Optional[float]
    multiple_signal: Optional[str]
    roic: Optional[float]
    wacc: Optional[float]
    value_creation: Optional[str]
    revenue_bn: Optional[float]
    ebitda_bn: Optional[float]
    fcf_bn: Optional[float]
    fcf_margin: Optional[float]
    gross_margin: Optional[float]
    data_quality: Optional[float]
    # Lifecycle status: indicates whether the LLM agent run has produced a
    # *_report.json or whether the ticker is calc-only / not yet ingested.
    agents_status: Optional[str] = None        # "ready" | "pending" | "not_ingested"
    last_agent_run: Optional[str] = None       # ISO timestamp of the report file mtime


class UniverseResponse(BaseModel):
    count: int
    tickers: List[str]
    ranked: List[TickerSummary]       # sorted by conviction desc, mos desc
    generated_at: str


class DCFScenario(BaseModel):
    intrinsic_per_share: Optional[float]
    margin_of_safety: Optional[float]
    ev: Optional[float]


class DCFResponse(BaseModel):
    ticker: str
    wacc: Optional[float]
    beta: Optional[float]
    risk_free_rate: Optional[float]
    bear: Optional[DCFScenario]
    base: Optional[DCFScenario]
    bull: Optional[DCFScenario]
    reverse_dcf: Optional[dict]
    multiple_decomposition: Optional[dict]


class FundamentalsResponse(BaseModel):
    ticker: str
    fiscal_year: Optional[int]
    revenue_bn: Optional[float]
    ebitda_bn: Optional[float]
    fcf_bn: Optional[float]
    fcf_margin: Optional[float]
    gross_margin: Optional[float]
    ebit_margin: Optional[float]
    roic: Optional[float]
    roe: Optional[float]
    sbc_pct_fcf: Optional[float]
    cash_tax_rate: Optional[float]
    share_dilution_pct: Optional[float]
    warning_count: Optional[int]
    error_count: Optional[int]
    data_quality: Optional[float]
    domain_scores: Optional[Dict[str, float]]
    cleaning_flags: Optional[dict]


class ScreeningMetric(BaseModel):
    name: str
    category: str
    authority: str
    value: Optional[float]
    display_value: str
    threshold: str
    signal: str          # ✓ / ⚠ / ✗ / —
    note: str = ""


class ThesisFormData(BaseModel):
    one_sentence: str
    assumption_1: str
    assumption_2: str
    assumption_3: str
    confirmation_12m: str
    falsification: str
    moat_powers: str
    unit_economics: str


class ScreeningResponse(BaseModel):
    ticker: str
    fiscal_year: int
    current_price: Optional[float]
    market_cap: Optional[float]
    passes: int
    flags: int
    fails: int
    available: int
    metrics: List[ScreeningMetric]


# ─────────────────────────────────────────────────────────────────────────────
# Qualitative-framework response/request models
# ─────────────────────────────────────────────────────────────────────────────

class QualitativeSubQuestion(BaseModel):
    """One question in a HITL dimension's structured prompt — surfaced for
    the UI to render the assessment dialog."""
    id: str
    text: str
    weight: float
    score_anchors: Dict[int, str] = {}


class QualitativeDimensionState(BaseModel):
    """One dimension's full state for a ticker. Used by the list endpoint
    (without questions) and the detail endpoint (with questions)."""
    dimension_id: str
    title: str
    category: str
    source_category: str               # "deterministic" | "hitl" | "llm_augmented" | "pending_data"
    description: str
    staleness_days: int
    code_version: int
    catalog_hash: str                  # for localStorage draft keying

    # Assessment state — populated when an assessment exists
    status: str                        # "not_assessed" | "pending_data" | "assessed" | "stale"
    score: Optional[float] = None
    sub_scores: Optional[Dict[str, float]] = None
    narrative: Optional[str] = None
    last_updated: Optional[str] = None
    assessed_by: Optional[str] = None
    code_git_sha: Optional[str] = None
    source_payload: Optional[Dict[str, Any]] = None

    # Catalog questions — only populated by the detail endpoint
    questions: Optional[List[QualitativeSubQuestion]] = None
    formula_citation: Optional[str] = None


class QualitativeListResponse(BaseModel):
    ticker: str
    dimensions: List[QualitativeDimensionState]
    generated_at: str


class QualitativeSubmitRequest(BaseModel):
    """HITL submission payload. The composite score is server-computed
    from sub_scores + catalog weights — clients can't override it."""
    sub_scores: Dict[str, float]       # {sub_question_id: 1-7}
    narrative: Optional[str] = None    # ≤ 500 chars
    analyst_id: Optional[str] = None   # defaults to env ALETHEIA_ANALYST_ID


class QualitativeSubmitResponse(BaseModel):
    ticker: str
    dimension_id: str
    score: float
    assessment_id: str
    assessed_at: str


class QualitativeRecomputeEntry(BaseModel):
    dimension_id: str
    score: Optional[float]
    status: str                        # "written" | "unchanged" | "no_data" | "error"
    reason: Optional[str] = None


class QualitativeRecomputeResponse(BaseModel):
    ticker: str
    results: List[QualitativeRecomputeEntry]
    written_count: int
    unchanged_count: int
    no_data_count: int


class QualitativeCategoryComposite(BaseModel):
    category_id: str
    title: str
    composite_score: Optional[float]   # None when no assessable members assessed
    n_assessed: int
    n_total: int                       # excludes pending_data
    contributing: List[Dict[str, Any]] # [{dim_id, score, weight, contribution}]


class QualitativeCategoriesResponse(BaseModel):
    ticker: str
    categories: List[QualitativeCategoryComposite]


# ─────────────────────────────────────────────────────────────────────────────
# Routes
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/health", response_model=HealthResponse, tags=["System"])
def health():
    """Health check — shows which tickers have current reports."""
    available = [
        p.stem.replace("_report", "")
        for p in REPORT_DIR.glob("*_report.json")
    ]
    missing = []  # No concept of "missing" when universe is open
    return HealthResponse(
        status="ok",
        version=API_VERSION,
        tickers_available=available,
        tickers_missing=missing,
        report_dir=str(REPORT_DIR.resolve()),
    )


def _calc_only_summary(ticker: str) -> Optional[Dict[str, Any]]:
    """
    Flatten the live DB+DCF compute into a TickerSummary row for the
    universe table. Used for tickers that are ingested but have no agent
    run yet.

    Shares the engine call with `_compute_dcf_live` via that helper — same
    DCF result, same multiple decomp, same reverse-DCF signal. When the
    engine fails on a schema-mismatch filer (AXP / banks / REITs), we fall
    back to raw DB fields so the universe row still shows revenue/ebitda/
    fcf rather than appearing entirely blank.

    Agent-driven fields (conviction, moat, pillar_total, narrative) stay
    None. Returns None only if the ticker has no DB rows at all.
    """
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        calc = make_calc_input(ticker)
        if calc.df.empty:
            return None
    except Exception:
        return None

    df = calc.df.sort_values("fiscal_year")
    latest = df.iloc[-1]

    # ── Historical revenue CAGR (always available from DB) ──────────────
    hist_cagr = None
    if len(df) >= 2:
        revs = df["clean_Revenue"].tolist()[-5:]
        revs = [float(r) for r in revs if r and r > 0]
        if len(revs) >= 2:
            n = len(revs) - 1
            hist_cagr = (revs[-1] / revs[0]) ** (1.0 / n) - 1.0

    # ── Live DCF compute (shared with /ticker/{T}/dcf) ──────────────────
    # On engine failure (schema mismatch), this raises HTTPException(422).
    # We catch it here because the universe row should still render with
    # raw DB fundamentals — only the per-ticker `/dcf` endpoint surfaces
    # 422 to the client.
    payload: Dict[str, Any] = {}
    result = None
    try:
        payload = _compute_dcf_live(ticker)
        result = payload.get("_result")
    except HTTPException:
        result = None

    base_iv  = payload.get("base", {}).get("intrinsic_per_share")  if payload.get("base")  else None
    base_mos = payload.get("base", {}).get("margin_of_safety")     if payload.get("base")  else None
    bear_iv  = payload.get("bear", {}).get("intrinsic_per_share")  if payload.get("bear")  else None
    bull_iv  = payload.get("bull", {}).get("intrinsic_per_share")  if payload.get("bull")  else None
    md       = payload.get("multiple_decomposition") or {}
    rdcf     = payload.get("reverse_dcf") or {}

    # Engine-derived totals fall back to the raw DB row when DCF couldn't
    # run for this filer.
    revenue = getattr(result, "revenue", None) if result else (latest.get("clean_Revenue") or None)
    ebitda  = getattr(result, "ebitda",  None) if result else (latest.get("derived_EBITDA") or latest.get("clean_EBITDA") or None)
    fcf     = getattr(result, "fcf",     None) if result else (latest.get("derived_FCF") or latest.get("clean_FCF") or None)
    roic    = md.get("roic") if md.get("roic") is not None else (latest.get("derived_ROIC") or None)
    wacc    = md.get("wacc")

    implied_cagr = rdcf.get("implied_cagr_10y")
    impl_hist_ratio = (
        implied_cagr / hist_cagr
        if (implied_cagr is not None and hist_cagr and hist_cagr > 0)
        else None
    )

    # ── LLM overlay (Step 3 read flip) ──────────────────────────────────
    # Conviction, moat, narrative come from agent_runs_latest with legacy
    # JSON fallback. When neither source has data, the row stays "pending".
    llm = _load_llm_payload(ticker)
    conviction = None
    moat_score = None
    if llm:
        thesis = llm.get("investment_thesis") or {}
        er     = llm.get("economic_reality") or {}
        conviction = thesis.get("conviction_score")
        moat_score = (er.get("moat") or {}).get("score")

    return {
        "ticker":              ticker,
        "generated_at":        (llm or {}).get("generated_at"),
        "conviction":          int(conviction) if conviction is not None else None,
        "moat":                float(moat_score) if moat_score is not None else None,
        "base_iv":             float(base_iv) if base_iv else None,
        "bear_iv":             float(bear_iv) if bear_iv else None,
        "bull_iv":             float(bull_iv) if bull_iv else None,
        "base_mos":            float(base_mos) if base_mos is not None else None,
        "implied_cagr":        float(implied_cagr) if implied_cagr is not None else None,
        "historical_cagr":     float(hist_cagr) if hist_cagr is not None else None,
        "implied_hist_ratio":  float(impl_hist_ratio) if impl_hist_ratio is not None else None,
        "rdcf_signal":         rdcf.get("signal"),
        "ev_ebitda":           md.get("market_ev_ebitda"),
        "justified_ev_ebitda": md.get("justified_ev_ebitda"),
        "multiple_premium":    md.get("premium_pct"),
        "multiple_signal":     md.get("signal"),
        "roic":                float(roic) if roic else None,
        "wacc":                float(wacc) if wacc else None,
        "value_creation":      md.get("value_creation"),
        "revenue_bn":          float(revenue / 1e9) if revenue else None,
        "ebitda_bn":           float(ebitda / 1e9) if ebitda else None,
        "fcf_bn":              float(fcf / 1e9) if fcf else None,
        "fcf_margin":          None,
        "gross_margin":        None,
        "data_quality":        float(latest.get("overall_quality_score") or 0) or None,
        "agents_status":       "ready" if llm else "pending",
        "last_agent_run":      (llm or {}).get("generated_at"),
    }


def _placeholder_summary(ticker: str) -> Dict[str, Any]:
    """Skeleton for tickers that are in the universe classification but have
    no DB rows yet (e.g., bad SEC fetch or never ingested)."""
    return {
        "ticker":              ticker,
        "generated_at":        None,
        "conviction":          None,
        "moat":                None,
        "base_iv":             None,
        "bear_iv":             None,
        "bull_iv":             None,
        "base_mos":            None,
        "implied_cagr":        None,
        "historical_cagr":     None,
        "implied_hist_ratio":  None,
        "rdcf_signal":         None,
        "ev_ebitda":           None,
        "justified_ev_ebitda": None,
        "multiple_premium":    None,
        "multiple_signal":     None,
        "roic":                None,
        "wacc":                None,
        "value_creation":      None,
        "revenue_bn":          None,
        "ebitda_bn":           None,
        "fcf_bn":              None,
        "fcf_margin":          None,
        "gross_margin":        None,
        "data_quality":        None,
        "agents_status":       "not_ingested",
        "last_agent_run":      None,
    }


def _ticker_universe_union() -> List[str]:
    """Union of curated universe + runtime-added tickers + tickers with DB
    rows. This is the source of truth for what the Universe tab displays."""
    out = set()
    # 1. Curated + runtime classifications
    try:
        from config.ticker_classification import get_extended_universe
        out.update(get_extended_universe().keys())
    except Exception:
        pass
    # 2. Anything with a cleaned DB row
    try:
        import duckdb
        con = duckdb.connect("valuation_data/database/investment.duckdb", read_only=True)
        rows = con.execute("SELECT DISTINCT ticker FROM company_records").fetchall()
        out.update(r[0] for r in rows)
        con.close()
    except Exception:
        pass
    # 3. Anything with a serving report (defensive — handles drift between
    # the DB and report files)
    out.update(p.stem.replace("_report", "") for p in REPORT_DIR.glob("*_report.json"))
    return sorted(out)


@app.get("/universe", response_model=UniverseResponse, tags=["Universe"])
def get_universe():
    """
    Full universe summary ranked by conviction → margin of safety.
    Returns the union of curated/runtime-classified tickers and tickers with
    DB rows. Each ticker carries an `agents_status`:
      - "ready"        — has *_report.json (full LLM analysis available)
      - "pending"      — has DB rows but no agent run yet (calc-layer only)
      - "not_ingested" — classified but no DB rows (placeholder)
    """
    summaries: List[Dict[str, Any]] = []
    universe = _ticker_universe_union()

    # Single read path: `_calc_only_summary` recomputes deterministic
    # blocks from the DB and overlays LLM blocks from agent_runs_latest
    # (with JSON fallback). The output's `agents_status` reflects whether
    # an LLM payload was found ("ready") or not ("pending"). When the
    # ticker isn't even in the DB yet we drop down to a placeholder row.
    #
    # Parallelized because each ticker's first DCFEngine.run() pays a
    # cold yfinance fetch for beta (~400-1700ms). Serial across 40
    # tickers blew the Streamlit 15s timeout on the first /universe call
    # after server startup; threaded does it in <5s. Calls are
    # independent — no shared mutable state — and yfinance is I/O-bound,
    # so threads (not processes) are the right tool.
    from concurrent.futures import ThreadPoolExecutor

    def _summarize_one(ticker: str) -> Dict[str, Any]:
        s = _calc_only_summary(ticker)
        return s if s is not None else _placeholder_summary(ticker)

    with ThreadPoolExecutor(max_workers=8) as ex:
        summaries = list(ex.map(_summarize_one, universe))

    # Sort: ready first by conviction desc / mos desc; then pending alpha;
    # then not_ingested alpha. Stable sort on multiple keys.
    status_rank = {"ready": 0, "pending": 1, "not_ingested": 2}
    summaries.sort(
        key=lambda x: (
            status_rank.get(x.get("agents_status"), 9),
            -(x.get("conviction") or -99) if x.get("agents_status") == "ready" else 0,
            -(x.get("base_mos") or -99) if x.get("agents_status") == "ready" else 0,
            x.get("ticker", ""),
        ),
    )

    return UniverseResponse(
        count=len(summaries),
        tickers=[s["ticker"] for s in summaries],
        ranked=[TickerSummary(**s) for s in summaries],
        generated_at=datetime.utcnow().isoformat(),
    )


def _build_full_report(ticker: str) -> Optional[Dict[str, Any]]:
    """Assemble the four-section report shape on demand from current state.

    LLM blocks (Sections 1, 4.contrarian, 4.investment_thesis, 4.agent_scenarios)
    come from `agent_runs_latest`; deterministic blocks (Sections 2, 3.capital_stack,
    4.phase2_valuation) are recomputed live from `company_records_latest`. The
    LLM-authored sub-keys of Section 3 (risk_factors narratives,
    concentration_*) are still served from the legacy JSON when present —
    those are the one outstanding piece of Step 2's whitelist that we
    deferred. They quietly disappear for pending tickers, which the Deep
    Dive UI already handles by collapsing the capital-risk section.

    Returns None only if the ticker has no DB rows AND no legacy JSON —
    i.e. genuinely never-ingested.
    """
    ticker_u = ticker.upper()
    llm = _load_llm_payload(ticker_u)            # may be None
    legacy = None
    legacy_path = REPORT_DIR / f"{ticker_u}_report.json"
    if legacy_path.exists():
        try:
            legacy = json.loads(legacy_path.read_text())
        except Exception:
            legacy = None

    # ── Section 2: financial_translation (DB) ───────────────────────────
    section2: Dict[str, Any] = {}
    try:
        from aletheia.data.database import InvestmentDatabase
        import numpy as np
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker_u)
        db.close()
        if not df.empty:
            row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]
            def gdb(col, fb=None):
                v = row.get(col)
                return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else fb
            section2 = {
                "clean_financials": {
                    "revenue_bn":          (gdb("clean_Revenue") or 0) / 1e9,
                    "ebitda_bn":           (gdb("derived_EBITDA") or 0) / 1e9,
                    "fcf_bn":              (gdb("derived_FCF") or 0) / 1e9,
                    "nopat_bn":            (gdb("clean_NOPAT") or 0) / 1e9,
                    "invested_capital_bn": (gdb("derived_InvestedCapital") or 0) / 1e9,
                    "net_debt_bn":         (gdb("derived_NetDebt") or 0) / 1e9,
                    "sbc_bn":              (gdb("clean_SBC") or 0) / 1e9,
                    "fiscal_year":         int(df["fiscal_year"].max()),
                    "data_quality":        gdb("overall_quality_score"),
                },
                "ratios": {
                    "gross_margin_pct":    gdb("derived_GrossMargin_Pct"),
                    "ebit_margin_pct":     gdb("derived_EBIT_Margin_Pct"),
                    "ebitda_margin_pct":   gdb("derived_EBITDA_Margin_Pct"),
                    "fcf_margin_pct":      gdb("derived_FCF_Margin_Pct"),
                    "roic":                gdb("derived_ROIC"),
                    "roe":                 gdb("derived_ROE"),
                    "sbc_pct_fcf":         gdb("clean_SBC_PctFCF"),
                    "share_dilution_pct":  gdb("clean_ShareDilution_Pct"),
                    "cash_tax_rate":       gdb("clean_CashTaxRate"),
                },
                "quality_screens": {
                    "beneish_m_score": gdb("beneish_m_score"),
                    "domain_scores": {
                        k.replace("domain_score_", ""): gdb(k)
                        for k in df.columns if k.startswith("domain_score_") and gdb(k) is not None
                    },
                },
                "cleaning_flags": {
                    "warning_count": int(row.get("warning_count") or 0),
                    "error_count":   int(row.get("error_count") or 0),
                },
            }
    except Exception:
        section2 = {}

    # ── Section 4.phase2_valuation: live DCF (Step 1) ───────────────────
    phase2: Dict[str, Any] = {}
    try:
        dcf_payload = _compute_dcf_live(ticker_u)
        phase2 = {
            "three_scenario_dcf": {
                "bear": dcf_payload.get("bear"),
                "base": dcf_payload.get("base"),
                "bull": dcf_payload.get("bull"),
            },
            "reverse_dcf":            dcf_payload.get("reverse_dcf"),
            "multiple_decomposition": dcf_payload.get("multiple_decomposition"),
            "wacc":                   dcf_payload.get("wacc"),
            "beta":                   dcf_payload.get("beta"),
            "risk_free_rate":         dcf_payload.get("risk_free_rate"),
        }
    except HTTPException:
        # Engine refused or no data — section stays empty. UI tolerates.
        phase2 = {}

    # ── Empty-state guard ───────────────────────────────────────────────
    # If we have nothing — no LLM, no DB row, no JSON — there's no
    # meaningful "full report" to assemble. Caller surfaces 404.
    if not llm and not section2 and not legacy:
        return None

    # ── Assemble four-section shape ─────────────────────────────────────
    section1 = (llm or {}).get("economic_reality") or (legacy or {}).get("1_economic_reality") or {}

    section3_legacy = (legacy or {}).get("3_capital_structure_risk") or {}
    # Rebuild the deterministic sub-keys of section 3 even when the JSON
    # is missing, so the UI's capital-stack readout works for pending
    # tickers. Risk-factor narratives only present if legacy JSON exists.
    section3: Dict[str, Any] = dict(section3_legacy)  # shallow copy preserving narratives
    if phase2.get("wacc") is not None:
        section3.setdefault("capital_stack", {}).update({
            "wacc":           phase2.get("wacc"),
            "beta":           phase2.get("beta"),
            "risk_free_rate": phase2.get("risk_free_rate"),
        })

    return {
        "ticker":       ticker_u,
        "generated_at": (llm or {}).get("generated_at") or (legacy or {}).get("generated_at"),
        "agent_run": {
            "source":   (llm or {}).get("source"),
            "version":  (llm or {}).get("version"),
            "git_sha":  (llm or {}).get("git_sha"),
        } if llm else None,
        "1_economic_reality":       section1,
        "2_financial_translation":  section2,
        "3_capital_structure_risk": section3,
        "4_valuation_synthesis": {
            "phase2_valuation":     phase2,
            "contrarian_analysis":  (llm or {}).get("contrarian_analysis") or
                                    ((legacy or {}).get("4_valuation_synthesis") or {}).get("contrarian_analysis") or {},
            "investment_thesis":    (llm or {}).get("investment_thesis") or
                                    ((legacy or {}).get("4_valuation_synthesis") or {}).get("investment_thesis") or {},
            # Structured thesis from thesis_synthesizer (added in week-5
            # consolidation). Sourced from the legacy JSON for now since
            # agent_runs persists only the four canonical LLM blocks; a
            # follow-up will surface thesis_synthesis directly from
            # agent_runs once the schema gains a fifth column.
            "thesis_synthesis":     ((legacy or {}).get("4_valuation_synthesis") or {}).get("thesis_synthesis") or {},
            "agent_scenarios":      (llm or {}).get("agent_scenarios") or
                                    ((legacy or {}).get("4_valuation_synthesis") or {}).get("agent_scenarios") or [],
        },
    }


@app.get("/ticker/{ticker}", tags=["Ticker"])
def get_ticker(ticker: str, response: Response) -> dict:
    """
    Full report for one ticker — all four sections of the investment memo.

    Assembled live: deterministic blocks come from `company_records_latest`
    via DCFEngine, LLM blocks from `agent_runs_latest` (with legacy
    `_report.json` fallback during the migration window). For pending
    tickers (no agent run yet), the deterministic blocks populate while
    LLM blocks remain empty — the UI degrades gracefully. 404 only when
    the ticker has no DB rows AND no legacy JSON.
    """
    payload = _build_full_report(ticker.upper())
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data for {ticker}. Run ingestion first.",
        )
    response.headers["X-Aletheia-Source"] = "db_compute+agent_runs"
    return payload


@app.get("/ticker/{ticker}/summary", response_model=TickerSummary, tags=["Ticker"])
def get_ticker_summary(ticker: str):
    """Flattened summary for one ticker — same schema as universe rows.

    Shares `_calc_only_summary` with the `/universe` endpoint, so the
    ranking-table row and the per-ticker fetch can never drift.
    """
    summary = _calc_only_summary(ticker.upper())
    if summary is None:
        raise HTTPException(
            status_code=404,
            detail=f"No data for {ticker}. Run ingestion first.",
        )
    return TickerSummary(**summary)


@app.get("/ticker/{ticker}/dcf", response_model=DCFResponse, tags=["Ticker"])
def get_ticker_dcf(ticker: str, response: Response):
    """DCF scenarios, reverse DCF, and multiple decomposition for one ticker.

    Recomputes live from the cleaned DuckDB rows via DCFEngine — the report
    JSON is no longer consulted. This means:
      - Pending tickers (no agent run) still get DCF output, since the calc
        layer doesn't depend on the LLM.
      - Engine-logic fixes (e.g. terminal-growth cap pattern) propagate to
        every ticker on the next request, no LLM re-run needed.
      - Schema-mismatch filers (AXP / banks / REITs) return 422 instead of
        404 — the failure is now categorical (engine doesn't apply) rather
        than artifactual (no JSON on disk).
    """
    payload = _compute_dcf_live(ticker.upper())
    response.headers["X-Aletheia-Source"] = "db_compute"
    return DCFResponse(
        ticker=payload["ticker"],
        wacc=payload["wacc"],
        beta=payload["beta"],
        risk_free_rate=payload["risk_free_rate"],
        bear=DCFScenario(**payload["bear"]) if payload["bear"] else None,
        base=DCFScenario(**payload["base"]) if payload["base"] else None,
        bull=DCFScenario(**payload["bull"]) if payload["bull"] else None,
        reverse_dcf=payload["reverse_dcf"],
        multiple_decomposition=payload["multiple_decomposition"],
    )


@app.get("/ticker/{ticker}/fundamentals", response_model=FundamentalsResponse, tags=["Ticker"])
def get_ticker_fundamentals(ticker: str, response: Response):
    """
    Phase 1 cleaned fundamentals for one ticker.

    DB is the source of truth — every field below is materialized in
    `company_records_latest`. The previous version read from the report
    JSON first and treated DB as fallback, which surfaced stale numbers
    when re-cleans hadn't been followed by an agent re-run. Returns 404
    only if the ticker has no DB rows at all.
    """
    from aletheia.data.database import InvestmentDatabase
    import numpy as np

    try:
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker.upper())
        db.close()
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"DB error: {type(e).__name__}: {e}")

    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No cleaned data for {ticker}. Run ingestion first.",
        )

    row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]

    def gdb(col, fb=None):
        v = row.get(col)
        return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else fb

    fy = int(df["fiscal_year"].max())
    revenue = gdb("clean_Revenue")
    ebitda  = gdb("derived_EBITDA") or gdb("clean_EBITDA")
    fcf     = gdb("derived_FCF") or gdb("clean_FCF")
    sbc     = gdb("clean_SBC")

    fcf_margin    = (fcf / revenue * 100) if (fcf and revenue) else None
    gross_margin  = gdb("derived_GrossMargin_Pct")
    ebit_margin   = gdb("derived_EBIT_Margin_Pct")
    sbc_pct_fcf   = (sbc / fcf * 100) if (sbc and fcf and fcf > 0) else None

    domain_scores = {
        k.replace("domain_score_", ""): gdb(k)
        for k in df.columns
        if k.startswith("domain_score_") and gdb(k) is not None
    }

    cleaning_flags = {
        "warning_count":      int(row.get("warning_count") or 0),
        "error_count":        int(row.get("error_count") or 0),
        "pension_deficit_bn": (gdb("clean_PensionDeficit_ForEquityBridge") or 0) / 1e9,
        "lease_debt_bn":      (gdb("clean_LeaseDebt_ForEquityBridge") or 0) / 1e9,
        "jva_income_bn":      (gdb("clean_JVA_Income_Isolated") or 0) / 1e9,
    }

    response.headers["X-Aletheia-Source"] = "db_compute"
    return FundamentalsResponse(
        ticker=ticker.upper(),
        fiscal_year=fy,
        revenue_bn=(revenue / 1e9) if revenue else None,
        ebitda_bn=(ebitda / 1e9) if ebitda else None,
        fcf_bn=(fcf / 1e9) if fcf else None,
        fcf_margin=fcf_margin,
        gross_margin=gross_margin,
        ebit_margin=ebit_margin,
        roic=gdb("derived_ROIC"),
        roe=gdb("derived_ROE"),
        sbc_pct_fcf=sbc_pct_fcf,
        cash_tax_rate=gdb("clean_CashTaxRate"),
        share_dilution_pct=gdb("clean_ShareDilution_Pct"),
        warning_count=cleaning_flags["warning_count"],
        error_count=cleaning_flags["error_count"],
        data_quality=gdb("overall_quality_score"),
        domain_scores=domain_scores,
        cleaning_flags=cleaning_flags,
    )


@app.get("/ticker/{ticker}/screening", response_model=ScreeningResponse, tags=["Ticker"])
def get_ticker_screening(ticker: str):
    """
    Full 34-metric unified screening scorecard for one ticker.
    Graham + Lynch + Malkiel + Liberti with pass/flag/fail signals.
    """
    try:
        from aletheia.tools.screening_ratios import ScreeningEngine
        from aletheia.utils.calc_input_builder import make_calc_input
        engine = ScreeningEngine(verbose=False)
        # Phase B: calc tools take CalculationInput, not a ticker string.
        calc_input = make_calc_input(ticker.upper())
        card = engine.score(calc_input)
        return ScreeningResponse(
            ticker=card.ticker,
            fiscal_year=card.fiscal_year,
            current_price=card.current_price,
            market_cap=card.market_cap,
            passes=card.passes,
            flags=card.flags,
            fails=card.fails,
            available=card.available,
            metrics=[
                ScreeningMetric(
                    name=m.name,
                    category=m.category,
                    authority=m.authority,
                    value=m.value,
                    display_value=m.display_value(),
                    threshold=m.threshold,
                    signal=m.signal,
                    note=m.note,
                )
                for m in card.metrics
            ],
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Screening failed: {e}")


@app.get("/screens/universe", tags=["Universe"])
def get_universe_screening():
    """
    Screening scorecard for all available tickers — compact comparison.
    Returns pass/flag/fail counts and key metric values per ticker.
    """
    try:
        from aletheia.tools.screening_ratios import ScreeningEngine
        from aletheia.utils.calc_input_builder import make_calc_input
        engine = ScreeningEngine(verbose=False)
        available = [t for t in UNIVERSE if (REPORT_DIR / f"{t}_report.json").exists()]

        # ScreeningEngine has no score_universe — iterate manually. Each
        # call gets its own CalculationInput (Phase B contract).
        result = {}
        for ticker in available:
            try:
                card = engine.score(make_calc_input(ticker))
            except Exception as inner:
                # Skip but don't fail the whole universe response
                result[ticker] = {"error": f"{type(inner).__name__}: {inner}"}
                continue
            result[ticker] = {
                "passes":   card.passes,
                "flags":    card.flags,
                "fails":    card.fails,
                "available": card.available,
                "metrics":  {m.name: {"value": m.value, "signal": m.signal}
                             for m in card.metrics},
            }
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Universe screening failed: {e}")


@app.get("/ticker/{ticker}/narrative", tags=["Ticker"])
def get_ticker_narrative(ticker: str, response: Response) -> dict:
    """
    Investment thesis narrative, conviction score, and constitution checks.

    Sourced from `agent_runs_latest` (Step 3 read flip). Falls back to the
    legacy report.json during the migration window so pre-Step-2 runs
    still resolve. 404 only if neither source has data — narrative is
    LLM-authored and there's no calc-layer fallback.
    """
    payload = _load_llm_payload(ticker.upper())
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"No agent run found for {ticker}. Run agents first.",
        )
    response.headers["X-Aletheia-Source"] = payload.get("source") or "unknown"
    thesis = payload["investment_thesis"] or {}
    return {
        "ticker":              payload["ticker"],
        "conviction":          thesis.get("conviction_score"),
        "narrative":           thesis.get("narrative"),
        "constitution_checks": thesis.get("constitution_checks", []),
        "margin_of_safety":    thesis.get("margin_of_safety"),
        "generated_at":        payload.get("generated_at"),
    }


@app.get("/ticker/{ticker}/economic_reality", tags=["Ticker"])
def get_economic_reality(ticker: str, response: Response) -> dict:
    """Moat, value chain, industry structure from the agent layer.

    LLM-authored. 404 if neither agent_runs nor JSON fallback has data."""
    payload = _load_llm_payload(ticker.upper())
    if payload is None:
        raise HTTPException(
            status_code=404,
            detail=f"No agent run found for {ticker}. Run agents first.",
        )
    response.headers["X-Aletheia-Source"] = payload.get("source") or "unknown"
    return {
        "ticker": payload["ticker"],
        **(payload["economic_reality"] or {}),
    }


@app.get("/ticker/{ticker}/capital_structure", tags=["Ticker"])
def get_capital_structure(ticker: str, response: Response) -> dict:
    """WACC, capital stack, and risk factors.

    Deterministic blocks (`capital_stack`, leverage ratios) are recomputed
    live via DCFEngine. LLM-authored blocks (`risk_factors.liquidity`
    narrative, `concentration_details`) are merged from the report JSON
    when one exists; pending tickers get the deterministic blocks only.
    """
    ticker_u = ticker.upper()

    # ── Deterministic: from DCFEngine + DB raw row ──────────────────────
    deterministic: Dict[str, Any] = {}
    try:
        payload = _compute_dcf_live(ticker_u)
        result = payload["_result"]

        # Raw debt/cash come from the DB row, not the DCFResult.
        from aletheia.data.database import InvestmentDatabase
        import numpy as np
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker_u)
        db.close()
        debt_long = debt_current = cash = None
        if not df.empty:
            row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]
            def gdb(col):
                v = row.get(col)
                return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else None
            debt_long    = gdb("raw_LongTermDebt")
            debt_current = gdb("raw_CurrentPortionLongTermDebt")
            cash         = gdb("raw_Cash")

        deterministic = {
            "capital_stack": {
                "debt_long":      debt_long,
                "debt_current":   debt_current,
                "cash":           cash,
                "equity":         float(result.market_cap) if result.market_cap else None,
                "wacc":           payload["wacc"],
                "beta":           payload["beta"],
            },
            "wacc":           payload["wacc"],
            "beta":           payload["beta"],
            "risk_free_rate": payload["risk_free_rate"],
        }
    except HTTPException:
        # Engine couldn't run for this filer; fall through to JSON-only.
        deterministic = {}

    # ── LLM-authored merge from legacy JSON ─────────────────────────────
    # Step 2's `agent_runs` schema captures the four canonical LLM blocks
    # but does NOT include the section-3 narratives (`risk_factors`,
    # `concentration_*`). They live on the JSON path until agent_runs is
    # expanded to capture them in a follow-up. This is the one remaining
    # `_load_report`-style read; flagged as deferred.
    llm_block: Dict[str, Any] = {}
    legacy_path = REPORT_DIR / f"{ticker_u}_report.json"
    source_tag = "db_compute"
    if legacy_path.exists():
        try:
            section3 = (json.loads(legacy_path.read_text())
                          .get("3_capital_structure_risk") or {})
            for k in ("risk_factors", "concentration_risk", "concentration_details"):
                if k in section3:
                    llm_block[k] = section3[k]
            if llm_block:
                source_tag = "db_compute+json_read"
        except Exception:
            pass
    response.headers["X-Aletheia-Source"] = source_tag

    return {"ticker": ticker_u, **deterministic, **llm_block}


# ─────────────────────────────────────────────────────────────────────────────
# Qualitative-framework endpoints
# ─────────────────────────────────────────────────────────────────────────────

def _assessment_status(catalog_entry, latest_record: Optional[Dict[str, Any]]) -> str:
    """Resolve the empty-state-or-staleness state for a (catalog, record)
    pair. Returns one of:
        "pending_data" — slot exists, data infrastructure not wired (e.g.
                         proxy filings for management dimensions)
        "not_assessed" — no assessment row exists yet
        "stale"        — assessment exists but exceeds staleness_days OR
                         (deterministic only) was produced under a
                         different code_git_sha than the running process
        "assessed"     — fresh assessment present
    """
    from aletheia.qualitative.types import SourceCategory

    if catalog_entry.source_category == SourceCategory.PENDING_DATA:
        return "pending_data"
    if latest_record is None:
        return "not_assessed"

    # Time-based staleness
    try:
        assessed_at = datetime.fromisoformat(latest_record["assessed_at"])
        # Some sources may not include tz; treat as UTC if naive
        if assessed_at.tzinfo is None:
            assessed_at = assessed_at.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - assessed_at).days
        if age_days > catalog_entry.staleness_days:
            return "stale"
    except (TypeError, ValueError, KeyError):
        pass

    # Code-version staleness — only meaningful for deterministic dims,
    # where the runner stamps `code_git_sha` at compute time. HITL
    # submissions also carry a git_sha, but a code change to api_main.py
    # that doesn't affect the catalog or computer logic shouldn't flag
    # an HITL assessment stale. For week 1 we apply the check uniformly;
    # if it produces too many false-positive stales for HITL, the
    # follow-up is to compare `catalog_hash` instead of git_sha for HITL.
    if catalog_entry.source_category == SourceCategory.DETERMINISTIC:
        from aletheia.qualitative.runner import _GIT_SHA as RUNTIME_GIT_SHA
        stored_sha = latest_record.get("code_git_sha")
        if stored_sha and RUNTIME_GIT_SHA and stored_sha != RUNTIME_GIT_SHA:
            return "stale"

    return "assessed"


def _compute_composite_score(catalog_entry, sub_scores: Dict[str, float]) -> float:
    """Weighted average of HITL sub-question scores using catalog weights.

    Validates that every catalog question is answered. Missing questions
    raise ValueError (clients must answer all questions; partial drafts
    live in localStorage, not on the wire).
    """
    catalog_q_ids = {q.id for q in catalog_entry.questions}
    submitted_q_ids = set(sub_scores.keys())

    missing = catalog_q_ids - submitted_q_ids
    if missing:
        raise ValueError(
            f"Submission missing answers for questions: {sorted(missing)}. "
            f"All catalog questions must be answered."
        )
    extra = submitted_q_ids - catalog_q_ids
    if extra:
        raise ValueError(
            f"Submission has unknown question ids: {sorted(extra)}. "
            f"Catalog questions: {sorted(catalog_q_ids)}."
        )

    for qid, score in sub_scores.items():
        if not (1.0 <= float(score) <= 7.0):
            raise ValueError(
                f"Sub-score for {qid!r}={score} out of [1, 7] range."
            )

    total = sum(q.weight * float(sub_scores[q.id]) for q in catalog_entry.questions)
    return round(total, 2)


def _build_dimension_state(catalog_entry, latest_record: Optional[Dict[str, Any]],
                           include_questions: bool = False) -> Dict[str, Any]:
    """Build the response shape for one dimension. The list endpoint omits
    questions; the detail endpoint includes them for UI rendering."""
    status = _assessment_status(catalog_entry, latest_record)

    out: Dict[str, Any] = {
        "dimension_id":    catalog_entry.id,
        "title":           catalog_entry.title,
        "category":        catalog_entry.category,
        "source_category": catalog_entry.source_category.value,
        "description":     catalog_entry.description,
        "staleness_days":  catalog_entry.staleness_days,
        "code_version":    catalog_entry.code_version,
        "catalog_hash":    catalog_entry.catalog_hash(),
        "status":          status,
        "score":           latest_record.get("score") if latest_record else None,
        "sub_scores":      latest_record.get("sub_scores") if latest_record else None,
        "narrative":       latest_record.get("narrative") if latest_record else None,
        "last_updated":    latest_record.get("assessed_at") if latest_record else None,
        "assessed_by":     latest_record.get("analyst_id") if latest_record else None,
        "code_git_sha":    latest_record.get("code_git_sha") if latest_record else None,
        "source_payload":  latest_record.get("source_payload") if latest_record else None,
    }

    if include_questions:
        out["questions"] = [
            {
                "id":             q.id,
                "text":           q.text,
                "weight":         q.weight,
                "score_anchors":  q.score_anchors,
            }
            for q in (catalog_entry.questions or ())
        ]
        out["formula_citation"] = catalog_entry.formula_citation or None

    return out


@app.get("/ticker/{ticker}/qualitative", response_model=QualitativeListResponse,
         tags=["Qualitative"])
def get_qualitative_list(ticker: str):
    """All catalog dimensions for a ticker, with current assessment state.

    Returns every dimension in the catalog (not just assessed ones) so
    the UI can render the full grid including empty states. Questions
    are omitted from list responses; the detail endpoint includes them.
    """
    from config.qualitative_dimensions import DIMENSIONS
    from aletheia.data.database import InvestmentDatabase

    ticker_u = ticker.upper()
    db = InvestmentDatabase(verbose=False)
    try:
        records = db.get_all_assessments_for_ticker(ticker_u)
    finally:
        db.close()

    states = [
        _build_dimension_state(
            catalog_entry,
            records.get(dim_id),
            include_questions=False,
        )
        for dim_id, catalog_entry in DIMENSIONS.items()
    ]

    return QualitativeListResponse(
        ticker=ticker_u,
        dimensions=[QualitativeDimensionState(**s) for s in states],
        generated_at=datetime.now(timezone.utc).isoformat(),
    )


@app.get("/ticker/{ticker}/qualitative/{dimension_id}",
         response_model=QualitativeDimensionState, tags=["Qualitative"])
def get_qualitative_dimension(ticker: str, dimension_id: str):
    """Full state for one dimension, including catalog questions.

    Used by the assessment dialog to render structured prompts. Returns
    404 only if the dimension_id is not in the catalog — never for
    "ticker has no assessment yet" (that's a valid empty state).
    """
    from config.qualitative_dimensions import DIMENSIONS
    from aletheia.data.database import InvestmentDatabase

    ticker_u = ticker.upper()
    catalog_entry = DIMENSIONS.get(dimension_id)
    if catalog_entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown dimension_id {dimension_id!r}. "
                   f"Catalog: {sorted(DIMENSIONS.keys())}"
        )

    db = InvestmentDatabase(verbose=False)
    try:
        latest = db.get_latest_assessment(ticker_u, dimension_id)
    finally:
        db.close()

    return QualitativeDimensionState(**_build_dimension_state(
        catalog_entry, latest, include_questions=True
    ))


@app.post("/ticker/{ticker}/qualitative/{dimension_id}",
          response_model=QualitativeSubmitResponse, tags=["Qualitative"])
def submit_qualitative_assessment(
    ticker: str,
    dimension_id: str,
    request: QualitativeSubmitRequest,
):
    """Submit a HITL assessment.

    Composite score is server-computed from `sub_scores` + catalog
    weights — the client doesn't send a score directly. This prevents
    UI bugs from drifting the displayed score from the math.

    Rejects:
      - Unknown dimension_id (404)
      - Non-HITL dimensions — deterministic ones go through
        /qualitative/recompute/{T}; PENDING_DATA / LLM_AUGMENTED can't
        be analyst-submitted (422)
      - Missing answers, unknown question ids, out-of-range sub-scores,
        narrative > 500 chars (422)
    """
    import os
    import uuid
    from config.qualitative_dimensions import DIMENSIONS
    from aletheia.data.database import InvestmentDatabase
    from aletheia.qualitative.types import AssessmentRecord, SourceCategory
    from aletheia.qualitative.runner import _GIT_SHA

    ticker_u = ticker.upper()
    catalog_entry = DIMENSIONS.get(dimension_id)
    if catalog_entry is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown dimension_id {dimension_id!r}",
        )
    if catalog_entry.source_category != SourceCategory.HITL:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Dimension {dimension_id!r} is "
                f"{catalog_entry.source_category.value}, not HITL. "
                f"Deterministic dimensions are computed via "
                f"/qualitative/recompute/{{ticker}}; PENDING_DATA and "
                f"LLM_AUGMENTED cannot be analyst-submitted."
            ),
        )
    if request.narrative is not None and len(request.narrative) > 500:
        raise HTTPException(status_code=422, detail="narrative > 500 chars")

    try:
        composite = _compute_composite_score(catalog_entry, request.sub_scores)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))

    analyst_id = (
        request.analyst_id
        or os.environ.get("ALETHEIA_ANALYST_ID")
        or "primary"
    )

    record = AssessmentRecord(
        assessment_id=str(uuid.uuid4()),
        ticker=ticker_u,
        dimension_id=dimension_id,
        score=float(composite),
        sub_scores={k: float(v) for k, v in request.sub_scores.items()},
        narrative=request.narrative,
        source_category=SourceCategory.HITL,
        source_payload={
            "prompts_version":     catalog_entry.code_version,
            "catalog_hash":        catalog_entry.catalog_hash(),
            "questions_answered":  len(request.sub_scores),
        },
        assessed_at=datetime.now(timezone.utc).isoformat(),
        analyst_id=analyst_id,
        code_git_sha=_GIT_SHA,
        input_fingerprint=None,   # HITL has no deterministic inputs
    )

    db = InvestmentDatabase(verbose=False)
    try:
        db.upsert_qualitative_assessment(record)
    finally:
        db.close()

    return QualitativeSubmitResponse(
        ticker=ticker_u,
        dimension_id=dimension_id,
        score=composite,
        assessment_id=record.assessment_id,
        assessed_at=record.assessed_at,
    )


@app.post("/qualitative/recompute/{ticker}", response_model=QualitativeRecomputeResponse,
          tags=["Qualitative"])
def recompute_deterministic_qualitative(ticker: str):
    """Run all deterministic computers for the ticker and persist new
    rows where inputs or formula version changed.

    Returns one entry per computer with status:
      - "written"   — new row written (score changed or first run)
      - "unchanged" — fingerprint + git_sha matched latest; no write
      - "no_data"   — computer returned None (insufficient history etc.)
      - "error"     — computer raised; payload includes the exception
    """
    from aletheia.qualitative.runner import recompute_deterministic

    ticker_u = ticker.upper()
    results = recompute_deterministic(ticker_u)

    # FastAPI's response_model strips fields not declared on the model;
    # we project to the entry shape and count statuses.
    entries: List[QualitativeRecomputeEntry] = []
    counts = {"written": 0, "unchanged": 0, "no_data": 0, "error": 0}
    for r in results:
        status = r.get("status", "no_data")
        counts[status] = counts.get(status, 0) + 1
        entries.append(QualitativeRecomputeEntry(
            dimension_id=r["dimension_id"],
            score=r.get("score"),
            status=status,
            reason=r.get("reason"),
        ))

    return QualitativeRecomputeResponse(
        ticker=ticker_u,
        results=entries,
        written_count=counts.get("written", 0),
        unchanged_count=counts.get("unchanged", 0),
        no_data_count=counts.get("no_data", 0) + counts.get("error", 0),
    )


@app.get("/qualitative/categories/{ticker}", response_model=QualitativeCategoriesResponse,
         tags=["Qualitative"])
def get_qualitative_categories(ticker: str):
    """Category-level composite scores.

    Computed as renormalized weighted averages over assessed dimensions
    only. PENDING_DATA dimensions are excluded entirely. If no member
    of a category is assessed, `composite_score` is None — the UI
    renders this as "no composite available."

    Renormalization: when only some dimensions are assessed, weights
    among the assessed ones are scaled to sum to 1. This means a partial
    composite is meaningful (3 of 5 dimensions assessed → composite
    reflects those 3 weighted appropriately) but is honestly flagged via
    `n_assessed` < `n_total`.
    """
    from config.qualitative_dimensions import DIMENSIONS, CATEGORIES, category_composite_weights
    from aletheia.qualitative.types import SourceCategory
    from aletheia.data.database import InvestmentDatabase

    ticker_u = ticker.upper()
    db = InvestmentDatabase(verbose=False)
    try:
        records = db.get_all_assessments_for_ticker(ticker_u)
    finally:
        db.close()

    out: List[QualitativeCategoryComposite] = []
    for cat_id, cat_label in CATEGORIES:
        weights = category_composite_weights(cat_id)
        # n_total is the count of catalog members eligible to compose
        # (i.e. excluding PENDING_DATA which the helper already skips)
        n_total = len(weights)

        # Resolve each member's current score; None means not yet
        # assessed (or score=None for some other reason — we treat both
        # as "not contributing")
        contributing: List[Dict[str, Any]] = []
        for dim_id, weight in weights.items():
            rec = records.get(dim_id)
            score = rec.get("score") if rec else None
            if score is None:
                continue
            contributing.append({
                "dimension_id": dim_id,
                "score":        float(score),
                "weight":       weight,
            })

        if contributing and n_total > 0:
            # Renormalize over assessed dimensions only
            assessed_weight_total = sum(c["weight"] for c in contributing)
            if assessed_weight_total > 0:
                composite = sum(
                    c["score"] * (c["weight"] / assessed_weight_total)
                    for c in contributing
                )
                # Add per-row contribution after renormalization
                for c in contributing:
                    c["renormalized_weight"] = round(c["weight"] / assessed_weight_total, 4)
                    c["contribution"] = round(c["score"] * c["renormalized_weight"], 4)
                composite_score = round(composite, 2)
            else:
                composite_score = None
        else:
            composite_score = None

        out.append(QualitativeCategoryComposite(
            category_id=cat_id,
            title=cat_label,
            composite_score=composite_score,
            n_assessed=len(contributing),
            n_total=n_total,
            contributing=contributing,
        ))

    return QualitativeCategoriesResponse(ticker=ticker_u, categories=out)


@app.post("/pipeline/run/{ticker}", tags=["System"])
async def run_pipeline(ticker: str, background_tasks: BackgroundTasks):
    """
    Trigger a full pipeline run for a ticker in the background.
    Returns immediately — poll /health to see when the report appears.
    """
    ticker = ticker.upper()

    def _run():
        subprocess.run(
            ["python3", "main.py", "--ticker", ticker],
            capture_output=True,
            cwd=Path(__file__).parent.parent,
        )

    background_tasks.add_task(_run)
    return {
        "status":  "queued",
        "ticker":  ticker,
        "message": f"Pipeline running for {ticker}. Poll GET /ticker/{ticker}/summary for results.",
    }


# ─────────────────────────────────────────────────────────────────────────────
# Thesis-only refresh + staleness (Phase 7) — partial rerun powered by
# serving-JSON hydration. Avoids the full 3-LLM-call pipeline when only
# qualitative-dashboard inputs changed.
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ticker/{ticker}/thesis_synthesis/staleness", tags=["Thesis"])
def get_thesis_staleness(ticker: str):
    """Return whether the latest thesis_synthesis output reflects the
    current dashboard state.

    Compares `_metadata.dashboard_state_fingerprint` stamped on the last
    thesis run against the live dashboard fingerprint computed now. The
    fingerprint hashes assessed/stale dim content (score, narrative,
    assessed_at, catalog_hash); any change → fingerprint shift → stale.

    Response shape:
      {is_stale, current_fp, thesis_fp, last_thesis_at, reason,
       thesis_present}
    """
    from aletheia.agents.state_hydration import load_serving_json
    from aletheia.agents.dashboard_fetch import dashboard_fetch_node

    ticker_u = ticker.upper()
    report = load_serving_json(ticker_u)

    if report is None:
        return {
            "ticker":          ticker_u,
            "is_stale":        False,
            "current_fp":      "",
            "thesis_fp":       "",
            "last_thesis_at":  None,
            "thesis_present":  False,
            "reason":          "no_report_yet",
        }

    thesis_synth = (report.get("4_valuation_synthesis") or {}).get("thesis_synthesis") or {}
    md = thesis_synth.get("_metadata") or {}
    thesis_fp = md.get("dashboard_state_fingerprint", "")
    last_at = md.get("generated_at")

    if not thesis_synth.get("thesis_statement"):
        return {
            "ticker":          ticker_u,
            "is_stale":        False,
            "current_fp":      "",
            "thesis_fp":       "",
            "last_thesis_at":  last_at,
            "thesis_present":  False,
            "reason":          "no_thesis_synthesis_yet",
        }

    qd_result = dashboard_fetch_node({"ticker": ticker_u, "messages": []})
    qd = qd_result.get("qualitative_dashboard") or {}
    current_fp = qd.get("state_fingerprint", "")

    is_stale = bool(current_fp) and bool(thesis_fp) and current_fp != thesis_fp
    if is_stale:
        reason = "dashboard_state_changed_since_thesis_generation"
    elif not thesis_fp:
        reason = "thesis_predates_fingerprint_stamping"
        is_stale = True
    else:
        reason = "thesis_matches_current_dashboard"

    return {
        "ticker":          ticker_u,
        "is_stale":        is_stale,
        "current_fp":      current_fp,
        "thesis_fp":       thesis_fp,
        "last_thesis_at":  last_at,
        "thesis_present":  True,
        "reason":          reason,
        "coverage": qd.get("coverage", {}),
    }


@app.post("/ticker/{ticker}/thesis_synthesis/refresh", tags=["Thesis"])
def refresh_thesis_synthesis(ticker: str):
    """Re-run thesis_synthesizer using cached upstream agent outputs from
    the serving JSON, with a fresh dashboard projection. One LLM call.

    Steps:
      1. Load serving JSON (404 if absent — caller must run full pipeline first)
      2. Hydrate LangGraph state from JSON (calc_node + agent outputs)
      3. Run dashboard_fetch_node — picks up the latest assessments
      4. Run thesis_synthesizer_agent — emits new ThesisSynthesis
      5. Patch serving JSON's `4_valuation_synthesis.thesis_synthesis`
      6. Write a new agent_runs row carrying unchanged columns forward

    Out of scope: full pipeline rerun (use POST /pipeline/run/{T}).
    """
    import json as _json

    from aletheia.agents.dashboard_fetch import dashboard_fetch_node
    from aletheia.agents.state_hydration import (
        hydrate_state_from_report,
        load_serving_json,
        serving_json_path,
    )
    from aletheia.agents.thesis_synthesizer import thesis_synthesizer_agent

    ticker_u = ticker.upper()

    # Pre-check: synthesizer falls back to mock when GOOGLE_API_KEY is
    # absent. A mock would silently overwrite the real thesis on disk —
    # refuse upfront with a clear 503 instead of running the synthesizer
    # and discovering the mock after the fact. Lesson learned the hard
    # way: do NOT trust truthiness of `thesis_statement` to detect mock.
    import os as _os
    if not _os.environ.get("GOOGLE_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail=(
                "GOOGLE_API_KEY is not set in the API process environment. "
                "thesis_synthesizer cannot run without it (it would emit a "
                "mock that would overwrite the real thesis). Set the key and "
                "restart the API server, then retry refresh."
            ),
        )

    report = load_serving_json(ticker_u)
    if report is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No serving JSON for {ticker_u}. Run the full pipeline first "
                f"via POST /pipeline/run/{ticker_u} — partial refresh requires "
                f"cached upstream outputs."
            ),
        )

    state = hydrate_state_from_report(ticker_u, report)
    state["messages"] = []

    qd_result = dashboard_fetch_node(state)
    state.update(qd_result)

    thesis_result = thesis_synthesizer_agent(state)
    new_thesis = thesis_result.get("thesis_synthesis") or {}

    # Post-check: even with a key set, the synthesizer can fall back to
    # mock on schema-validation failures across both retry attempts. The
    # mock has truthy `thesis_statement` ("Mock thesis (no API key or call
    # failure)") so we MUST inspect _quality_flags to detect it. NEVER
    # write a mock to disk — refuse the refresh, leave the prior thesis
    # intact. Surface the actual validation error in the response so the
    # caller can see what the LLM cited wrong.
    quality_flags = new_thesis.get("_quality_flags") or []
    if "mock_fallback" in quality_flags:
        mock_error = new_thesis.get("_mock_error", "") or "(no error captured)"
        raise HTTPException(
            status_code=503,
            detail=(
                "thesis_synthesizer fell back to mock — schema validation "
                "failed twice. The prior thesis on disk is unchanged. "
                f"Last validation error: {mock_error[:1200]}"
            ),
        )

    if not new_thesis.get("thesis_statement"):
        raise HTTPException(
            status_code=500,
            detail="thesis_synthesizer returned empty output; refresh aborted",
        )

    # Patch the serving JSON in place — only the thesis_synthesis field
    # changes. Everything else (phase2, contrarian, scenarios, conviction)
    # carries forward from the prior full run.
    if "4_valuation_synthesis" not in report:
        report["4_valuation_synthesis"] = {}
    report["4_valuation_synthesis"]["thesis_synthesis"] = new_thesis

    path = serving_json_path(ticker_u)
    try:
        with open(path, "w") as f:
            _json.dump(report, f, indent=2)
    except OSError as exc:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to write serving JSON: {exc}",
        )

    # Write a new agent_runs row preserving the prior LLM-authored columns.
    # The new investment_thesis column reflects the refresh; the others
    # carry forward unchanged. This keeps agent_runs_latest coherent —
    # readers always get a consistent snapshot.
    try:
        from aletheia.data.database import InvestmentDatabase
        synthesis = report.get("4_valuation_synthesis", {}) or {}
        ft_clean = (report.get("2_financial_translation", {}) or {}).get("clean_financials", {}) or {}
        llm_payload = {
            "economic_reality":    report.get("1_economic_reality") or {},
            "contrarian_analysis": synthesis.get("contrarian_analysis") or {},
            "investment_thesis":   {
                **(synthesis.get("investment_thesis") or {}),
                "thesis_synthesis": new_thesis,
            },
            "agent_scenarios":     synthesis.get("agent_scenarios") or [],
        }
        fy = ft_clean.get("fiscal_year") or 0
        from aletheia.agents.thesis_synthesizer import _GIT_SHA
        db = InvestmentDatabase(verbose=False)
        try:
            version = db.upsert_agent_run(
                ticker=ticker_u,
                fiscal_year=int(fy) if fy else 0,
                llm_payload=llm_payload,
                git_sha=_GIT_SHA,
            )
        finally:
            db.close()
    except Exception as exc:
        # agent_runs failure shouldn't block the refresh — the serving
        # JSON is already updated. Surface as a soft warning in the
        # response so callers know the audit row may be missing.
        version = None
        agent_runs_warning = str(exc)
    else:
        agent_runs_warning = None

    return {
        "status":           "refreshed",
        "ticker":           ticker_u,
        "agent_runs_version": version,
        "agent_runs_warning": agent_runs_warning,
        "thesis_synthesis": new_thesis,
    }


@app.get("/ticker/{ticker}/report/html", tags=["Reports"])
def get_report_html(ticker: str):
    """Serve the Executive HTML report for browser rendering."""
    path = REPORT_DIR / f"{ticker.upper()}_Executive_Report.html"
    if not path.exists():
        raise HTTPException(404, f"No HTML report for {ticker}")
    return FileResponse(path, media_type="text/html")


@app.get("/ticker/{ticker}/report/executive", tags=["Reports"])
def get_report_executive_md(ticker: str):
    """Serve the Executive Markdown report."""
    path = REPORT_DIR / f"{ticker.upper()}_Executive_Report.md"
    if not path.exists():
        raise HTTPException(404, f"No executive report for {ticker}")
    return FileResponse(path, media_type="text/markdown")


@app.get("/ticker/{ticker}/report/detailed", tags=["Reports"])
def get_report_detailed_md(ticker: str):
    """Serve the Detailed Markdown report."""
    path = REPORT_DIR / f"{ticker.upper()}_Detailed_Report.md"
    if not path.exists():
        raise HTTPException(404, f"No detailed report for {ticker}")
    return FileResponse(path, media_type="text/markdown")


@app.get("/ticker/{ticker}/report/dcf_excel", tags=["Reports"])
def get_report_dcf_excel(ticker: str):
    """Serve the DCF Excel model."""
    path = REPORT_DIR / f"{ticker.upper()}_DCF_Model.xlsx"
    if not path.exists():
        raise HTTPException(404, f"No DCF Excel model for {ticker}")
    return FileResponse(
        path, 
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=f"{ticker.upper()}_DCF_Model.xlsx"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Thesis Builder
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/ticker/{ticker}/thesis", tags=["Thesis"])
def get_thesis(ticker: str):
    """Get the latest thesis draft for a ticker."""
    from aletheia.tools.thesis_builder import ThesisDB
    db = ThesisDB()
    thesis = db.get_latest(ticker.upper())
    db.close()
    return thesis or {}


@app.get("/ticker/{ticker}/thesis/history", tags=["Thesis"])
def get_thesis_history(ticker: str):
    """Get all historical thesis versions for a ticker."""
    from aletheia.tools.thesis_builder import ThesisDB
    db = ThesisDB()
    history = db.get_history(ticker.upper())
    db.close()
    return history or []


@app.post("/ticker/{ticker}/thesis", tags=["Thesis"])
def save_thesis(ticker: str, data: ThesisFormData):
    """Retired — free-text Thesis Builder writes are no longer accepted.

    Per the dashboard-wiring change, structured analyst judgment now lives
    in the Qualitative Dashboard (per-dimension assessments). The
    thesis_synthesizer agent integrates dashboard state into its
    structured ThesisSynthesis output, surfaced via
    `GET /ticker/{T}/summary` → `4_valuation_synthesis.thesis_synthesis`.

    Historical memos remain accessible via the GET endpoints
    (`/ticker/{T}/thesis`, `/ticker/{T}/thesis/history`,
    `/ticker/{T}/thesis/pdf`).
    """
    raise HTTPException(
        status_code=410,
        detail=(
            "Free-text Thesis Builder is retired. Structured judgment now "
            "lives in the Qualitative Dashboard "
            "(POST /ticker/{T}/qualitative/{dimension_id}). The integrated "
            "thesis is produced by the thesis_synthesizer agent and "
            "surfaced via GET /ticker/{T}/summary -> "
            "4_valuation_synthesis.thesis_synthesis. Historical memos "
            "remain readable via GET /ticker/{T}/thesis."
        ),
    )


@app.get("/ticker/{ticker}/thesis/pdf", tags=["Thesis"])
def get_thesis_pdf(ticker: str):
    """Download the generated PDF brief."""
    from aletheia.tools.thesis_builder import ThesisDB
    db = ThesisDB()
    thesis = db.get_latest(ticker.upper())
    db.close()
    
    if not thesis:
        raise HTTPException(status_code=404, detail="Thesis not found in DB")

    pdf_path = Path("valuation_data/theses") / f"{ticker.upper()}_Thesis_v{thesis['version']}.pdf"
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF file not found at {pdf_path}")
    return FileResponse(
        pdf_path, 
        media_type="application/pdf", 
        filename=f"{ticker.upper()}_Thesis_v{thesis['version']}.pdf"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Root
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", tags=["System"])
def root():
    return {
        "name":    "Aletheia Investment Intelligence API",
        "version": API_VERSION,
        "docs":    "/docs",
        "health":  "/health",
        "universe":"/universe",
    }
