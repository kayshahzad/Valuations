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


def _compute_specialized_live(ticker: str, calc) -> Dict[str, Any]:
    """Run the ValuationRouter for specialized-engine tickers (NEE rate-base,
    DDM filers, BRK-B embedded value) and reshape the result into the same
    dict ``_compute_dcf_live`` produces. Used as the fallback when
    ``DCFEngine.run()`` raises ``NotImplementedError`` for non-FCFF business
    models.

    The returned dict has the router's IV in the ``base`` scenario;
    ``bear`` / ``bull`` are None (these engines don't produce scenario
    spreads). ``engine`` + ``valuation_decomposition`` + ``source_citation``
    surface the engine identity and year-by-year breakdown for the Deep
    Dive UI.

    Raises HTTPException(422) only when the router itself can't produce an
    IV (CNC empty-state, V KNOWN_ISSUES bypass) so the client gets the
    same empty-state UX as before for those genuinely-unvaluable tickers.
    """
    from aletheia.tools.valuation_router import (
        UnknownBusinessModelError, ValuationRouter,
    )

    try:
        vresult = ValuationRouter().execute(calc)
    except NotImplementedError as e:
        raise HTTPException(
            status_code=422,
            detail=(
                f"ValuationRouter bypassed {ticker}: {e}. "
                f"Ticker is flagged in KNOWN_ISSUES (typically a data-gap "
                f"filer); no engine ran."
            ),
        )
    except UnknownBusinessModelError as e:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown business model for {ticker}: {e}",
        )

    if vresult.intrinsic_per_share is None:
        # CNC-style empty-state (no dividend → DDM undefined). Surface as
        # 422 with the engine's warning so the client can show a friendly
        # "specialized engine can't value this ticker" message.
        warning = (vresult.warnings or ["no IV produced"])[0]
        raise HTTPException(
            status_code=422,
            detail=(
                f"{vresult.engine.upper()} engine produced empty-state for "
                f"{ticker}: {warning}"
            ),
        )

    snap = vresult.inputs_snapshot or {}
    ips = float(vresult.intrinsic_per_share)
    price = float(vresult.current_price) if vresult.current_price else None
    mos = float(vresult.margin_of_safety) if vresult.margin_of_safety is not None else None
    shares = snap.get("shares_diluted")
    market_cap = (
        float(price * shares)
        if (price is not None and shares is not None) else None
    )
    ke = snap.get("cost_of_equity")
    decomposition = (vresult.engine_specific or {}).get("decomposition")

    # Value source decomposition (spec §3 / Build 4) for specialized engines —
    # REIT uses AFFO/share growth + growth-normalized P/AFFO. Feed the engine
    # inputs the REIT branch needs via a p2-shaped dict.
    _vsd_payload = None
    try:
        from aletheia.tools.value_source_decomposition import (
            build_value_source_decomposition as _bvsd,
        )
        _vsd_payload = _bvsd(calc, None, p2={
            "current_price": price,
            "market_cap": market_cap,
            "engine": vresult.engine,
            "specialized_inputs": snap,
        })
    except Exception:
        _vsd_payload = None

    # CADS + capital-structure flag are engine-agnostic (computed off the frame),
    # so they apply to specialized engines too — EQIX's capex-sinkhole is exactly
    # the case CADS exists for. valuation_methods stays FCFF-only (None here).
    _cads_payload = None
    _csf_payload = None
    try:
        from aletheia.tools.cads_coverage import build_cads_coverage as _bcc
        _cc = _bcc(calc)
        _cads_payload = _cc if _cc.get("available") else None
    except Exception:
        _cads_payload = None
    try:
        from aletheia.tools.capital_structure_flag import build_capital_structure_flag as _bcf
        _cf = _bcf(calc, market_cap=market_cap)
        _csf_payload = _cf if _cf.get("available") else None
    except Exception:
        _csf_payload = None

    # Bank convergent set (residual income / justified P/B / Gordon DDM) — the
    # financial-sector analog of the FCFF four-method convergence. Gated on the
    # business model; this is the path DDM/embedded-value filers take.
    _bvm_payload = None
    try:
        from aletheia.tools.bank_valuation_methods import build_bank_valuation_methods as _bbvm
        _bm = _bbvm(calc, vresult, p2={
            "engine": vresult.engine,
            "wacc": float(ke) if ke is not None else None,
            "dcf": {"base_intrinsic_per_share": ips},
        })
        _bvm_payload = _bm if _bm.get("available") else None
    except Exception:
        _bvm_payload = None

    # Method-appropriate headline (CF-R19): the DDM structurally understates a
    # low-payout bank and that wrong MoS poisons the report headline / conviction /
    # thesis. When the convergent set flags it, swap the PRESENTED IV/MoS to the
    # residual-income fair value (DDM retained as a convergent-set leg). Banks that
    # aren't understated and all non-banks pass through untouched.
    _headline_override = None
    _bank_band = None
    try:
        from aletheia.tools.bank_valuation_methods import (
            bank_headline_override, bank_scenario_band,
        )
        _headline_override = bank_headline_override(
            ddm_ips=ips, price=price, bvm=_bvm_payload)
        # Bank bear/bull (CF-R22): RI at flexed ROE (cyclical reversion / NIM
        # compression / Basel III) — a real downside vs the fake $0.00 bear.
        _bank_band = bank_scenario_band(bvm=_bvm_payload, price=price)
    except Exception:
        _headline_override = None
    if _headline_override:
        ips = _headline_override["intrinsic_per_share"]
        mos = _headline_override["margin_of_safety"]

    # Bottom-up business analysis (deterministic layer) — engine-agnostic, built
    # off the cleaned frame, so it applies to specialized-engine tickers too (the
    # FCFF path builds it; this path previously omitted it, leaving CNC/NEE/ET/etc.
    # with an empty "no bottom-up analysis available" tab despite having clean data).
    _ba_payload = None
    try:
        _ba_payload = _business_analysis_payload(ticker, calc)
    except Exception:
        _ba_payload = None

    # Bank operating metrics (NII / efficiency / NIM / provisions / tangible book),
    # read from SEC XBRL companyfacts — gated to bank/specialized-financial filers
    # (JPM/AXP/BRK-B/SOFI). CET1/RWA flagged gated, not faked.
    _bank_metrics_payload = None
    try:
        from aletheia.tools.bank_metrics import build_bank_metrics as _bbm
        _bm = _bbm(calc, shares=shares)
        _bank_metrics_payload = _bm if _bm.get("available") else None
    except Exception:
        _bank_metrics_payload = None

    # Bull/bear sensitivity band (single-driver flex) — specialized engines
    # attach this to engine_specific so the three-scenario panel shows a real
    # range instead of one bar. Drop the legs into the bear/bull slots.
    _band = (vresult.engine_specific or {}).get("scenario_band") or {}
    # Prefer the bank RI band (flexed ROE) over the engine's DDM-growth band so the
    # three-scenario panel is coherent with the RI base headline.
    _bear = (_bank_band or {}).get("bear") or _band.get("bear")
    _bull = (_bank_band or {}).get("bull") or _band.get("bull")

    def _scn_leg(leg):
        if not leg:
            return None
        return {"intrinsic_per_share": leg.get("intrinsic_per_share"),
                "margin_of_safety": leg.get("margin_of_safety"),
                "ev": None}

    return {
        "ticker":                 vresult.ticker.upper(),
        "wacc":                   float(ke) if ke is not None else None,
        "beta":                   (float(snap["beta"])
                                   if snap.get("beta") is not None else None),
        "risk_free_rate":         (float(snap["risk_free_rate"])
                                   if snap.get("risk_free_rate") is not None
                                   else None),
        "current_price":          price,
        "market_cap":             market_cap,
        "shares_diluted":         float(shares) if shares is not None else None,
        "run_date":               None,
        "base_period":            "FY",
        "base_period_end_date":   None,
        "fy_fiscal_year":         int(vresult.fiscal_year) or None,
        "bear":                   _scn_leg(_bear),
        "base":                   {
            "intrinsic_per_share": ips,
            "margin_of_safety":    mos,
            "ev":                  (float(vresult.equity_value)
                                    if vresult.equity_value is not None else None),
        },
        "bull":                   _scn_leg(_bull),
        "scenario_band":          (_band or None),
        "reverse_dcf":            None,
        "multiple_decomposition": None,
        # Specialized-engine extras (surface to DCFResponse for the Deep Dive).
        "engine":                 vresult.engine,
        "valuation_decomposition": decomposition,
        "value_source_decomposition": _vsd_payload,
        "business_analysis":      _ba_payload,
        "bank_metrics":           _bank_metrics_payload,
        "cads":                   _cads_payload,
        "capital_structure_flag": _csf_payload,
        "valuation_methods":      None,   # FCFF-only; REIT/DDM value ECF directly
        "bank_valuation_methods": _bvm_payload,
        "headline_override":      _headline_override,
        "bank_scenario_band":     _bank_band,
        "specialized_inputs":     snap,
        "source_citation":        snap.get("source"),
        "as_of_date":             snap.get("as_of_date"),
        "engine_warnings":        list(vresult.warnings or []),
        # _result kept as None — the specialized engines don't expose a
        # DCFResult-shaped object; `_calc_only_summary` already handles
        # this via its `result is None` fallback.
        "_result":                None,
    }


def _regulatory_exposure(ticker: str) -> Optional[Dict[str, Any]]:
    """Latest `regulatory_exposure` qualitative assessment (LLM-extracted from
    the 10-K), reused as policy/regulatory context. Cache/DB read only — no new
    LLM call. Returns None when not assessed."""
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            return db.get_latest_assessment(ticker, "regulatory_exposure")
        finally:
            db.close()
    except Exception:
        return None


def _downside_protection_payload(
    calc: Any, result: Any,
    multiple_decomposition: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Downside-protection block (memo §8) for the /dcf payload: asymmetry
    ratio, downside ladder, required-MoS-by-risk, position-sizing band. Reuses
    the already-computed DCF result + multiples — deterministic, no LLM. Never
    raises."""
    try:
        from aletheia.tools.downside_protection import build_downside_protection
        return build_downside_protection(
            calc, result, multiple_decomposition=multiple_decomposition)
    except Exception:
        return None


def _wacc_analysis_payload(ticker: str, result: Any) -> Optional[Dict[str, Any]]:
    """Discount-rate detail (memo §7) for the /dcf payload: component
    decomposition, build-up premia + adjusted WACC, sensitivity table, and
    implied WACC. Deterministic — re-discounts the base scenario; no LLM. The
    country premium uses the cached FMP profile. Never raises."""
    try:
        from aletheia.tools.wacc_analysis import build_wacc_analysis
        country = None
        try:
            from aletheia.data import fmp_client
            country = (fmp_client.fetch_profile(ticker) or {}).get("country")
        except Exception:
            country = None
        # Inject the classification industry (calc layer can't import config) so
        # the sector-β diagnostic can look up Damodaran's industry-average beta.
        industry = None
        try:
            from config.ticker_classification import get_extended_universe
            _cls = get_extended_universe().get(ticker.upper())
            industry = getattr(_cls, "industry", None)
        except Exception:
            industry = None
        return build_wacc_analysis(result, country=country, industry=industry)
    except Exception:
        return None


def _market_context_payload(ticker: str) -> Optional[Dict[str, Any]]:
    """Market context (memo §8): earnings surprises, sell-side ratings, ESG
    placeholder, recent news. Deterministic, FMP-backed; no LLM. Never raises."""
    try:
        from aletheia.tools.market_context import compose_market_context
        return compose_market_context(ticker)
    except Exception:
        return None


def _business_analysis_payload(ticker: str, calc: Any) -> Optional[Dict[str, Any]]:
    """Bottom-up business analysis (memo §4): growth decomposition (organic/M&A)
    + coverage map. Deterministic; no LLM. The /dcf path has no agent report, so
    business_model-sourced coverage shows pending here (the HTML report path,
    which has the report, fills those). Never raises."""
    try:
        from aletheia.tools.business_analysis import build_business_analysis
        return build_business_analysis(None, ticker, calc=calc)
    except Exception:
        return None


def _assumption_grounding_payload(
    calc: Any, result: Any, business_analysis: Optional[Dict[str, Any]],
    current_state: Optional[Dict[str, Any]],
    wacc_analysis: Optional[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    """Assumption grounding (memo keystone): engine assumptions vs business-
    grounded references (organic CAGR, lifecycle terminal growth, disruption/
    concentration WACC premium). Shown, not applied. Never raises."""
    try:
        from aletheia.tools.assumption_grounding import build_assumption_grounding
        gd = (business_analysis or {}).get("growth_decomposition")
        seg = (business_analysis or {}).get("segment_economics")
        return build_assumption_grounding(
            calc, result, growth_decomposition=gd,
            current_state=current_state, wacc_analysis=wacc_analysis,
            segment_economics=seg)
    except Exception:
        return None


def _current_state_payload(
    ticker: str, result: Any,
    multiple_decomposition: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """Current-State Awareness (Phase 1.5) for the /dcf payload: reconcile the
    engine's near-term growth vs forward analyst consensus + cached events, and
    surface reused signals (sector-relative valuation from the multiple tool;
    policy/regulatory context from cached events + the regulatory_exposure
    qualitative dimension). All read-path — no new LLM call. Never raises."""
    try:
        from aletheia.agents.current_state import build_current_state
        from aletheia.agents.current_state_events import cached_events
        base = getattr(result, "base", None)
        eng_y1 = getattr(getattr(base, "assumptions", None), "revenue_cagr_y1_5", None) if base else None
        fy = getattr(result, "fy_fiscal_year", None) or getattr(result, "fiscal_year", None)
        cs = build_current_state(
            ticker, engine_y1_growth=eng_y1,
            latest_fy=int(fy) if fy else None,
            events=cached_events(ticker),
            multiple_decomposition=multiple_decomposition,
            regulatory_exposure=_regulatory_exposure(ticker),
        ).to_dict()
        return _annotate_acks(ticker, cs)
    except Exception:
        return None


_SEV_RANK = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}


def _annotate_acks(ticker: str, cs: Dict[str, Any]) -> Dict[str, Any]:
    """Merge analyst flag acknowledgments into the current-state payload.

    Each flag gains ``acknowledged`` + ``ack`` (decision/rationale/decided_*).
    Adds ``unresolved_severity`` = max severity among UNacknowledged flags, and
    ``unresolved_high`` count. The gate uses ``unresolved_severity`` (not the
    raw ``max_severity``) so resolving every HIGH flag clears FLAGS-PENDING.
    """
    try:
        from aletheia.data.database import InvestmentDatabase
        db = InvestmentDatabase(verbose=False)
        try:
            acks = db.get_flag_acks(ticker)
        finally:
            db.close()
    except Exception:
        acks = {}
    unresolved_rank, unresolved_high = 0, 0
    for f in cs.get("flags", []):
        ack = acks.get(f.get("key", ""))
        # "needs_analysis" is parked, not resolved — it must NOT clear the gate.
        resolved = bool(ack) and ack["decision"] != "needs_analysis"
        f["acknowledged"] = resolved
        f["ack"] = {
            "decision": ack["decision"], "rationale": ack.get("rationale"),
            "decided_by": ack.get("decided_by"), "decided_at": ack.get("decided_at"),
        } if ack else None
        if not resolved:
            unresolved_rank = max(unresolved_rank, _SEV_RANK.get(f.get("severity"), 0))
            if f.get("severity") == "HIGH":
                unresolved_high += 1
    cs["unresolved_severity"] = next(
        (s for s, r in _SEV_RANK.items() if r == unresolved_rank), "NONE")
    cs["unresolved_high"] = unresolved_high
    return cs


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
    except NotImplementedError:
        # Specialized-engine ticker (NEE rate-base, JPM/AXP/UNH DDM,
        # BRK-B embedded-value, CNC empty-state, V KNOWN_ISSUES bypass).
        # Route through the ValuationRouter and reshape into the DCF
        # response so the Deep Dive renders the IV instead of a 422.
        return _compute_specialized_live(ticker, calc)
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

    # Multiple decomposition — run the SAME standalone MultipleDecomposition
    # tool the agent report uses (calc_node), so "justified EV/EBITDA",
    # premium, and the multiple signal are IDENTICAL across the report, the
    # deep dive, and the financials tab. Previously this reused the DCF base
    # scenario's own justified multiple (forecast-growth-based), which
    # diverged from the report's tool value (historical-growth-based) — e.g.
    # CHWY showed 8.2x here vs 5.7x in the report. Single source of truth.
    from aletheia.tools.multiple_decomposition import MultipleDecomposition
    multiple_decomposition = None
    try:
        _mdres = MultipleDecomposition(verbose=False).run(calc)
        multiple_decomposition = {
            "market_ev_ebitda":    _mdres.market_ev_ebitda,
            "justified_ev_ebitda": _mdres.justified_ev_ebitda,
            "premium_pct":         _mdres.ev_ebitda_premium_pct,
            "signal":              _mdres.signal,
            "roic_wacc_spread":    _mdres.roic_wacc_spread,
            "value_creation":      _mdres.value_creation,
            "roic":                _mdres.roic,
            "wacc":                _mdres.wacc,
            # Sector-relative valuation (Phase B reuse) — already computed by
            # the tool; surfaced here so the Current-State layer can interpret
            # market multiple vs sector median without recomputing anything.
            "sector":                 getattr(_mdres, "sector", None),
            "sector_median_ev_ebitda": _mdres.sector_median_ev_ebitda,
            "vs_sector_premium":       _mdres.vs_sector_premium,
        }
    except Exception:
        multiple_decomposition = None

    # Reverse DCF — same tool the agent path uses, AND anchored to the engine
    # BASE scenario's assumptions (WACC, terminal margin, terminal growth) the
    # SAME way calc_node does for the report. Without these overrides the live
    # reverse DCF ran against its own hardcoded constants and diverged from the
    # report (CHWY: live implied +16% 'caution' vs report -0.9% 'deep_value').
    reverse_dcf: Optional[Dict[str, Any]] = None
    try:
        _rkwargs: Dict[str, Any] = {}
        _bsc = getattr(result, "base", None)
        _basm = getattr(_bsc, "assumptions", None) if _bsc else None
        if _basm is not None:
            _rkwargs["wacc_override"] = float(_basm.wacc)
            _rkwargs["margin_override"] = float(_basm.ebit_margin_terminal)
            _rkwargs["terminal_growth_override"] = float(_basm.terminal_growth)
        rdcf_result = ReverseDCF(verbose=False).run(calc, **_rkwargs)
        reverse_dcf = rdcf_result.to_dict()
        # The agent-written field is `signal_reasons`; surface it as
        # `reasons` for UI compatibility with the JSON-shaped output.
        reverse_dcf["reasons"] = list(getattr(rdcf_result, "signal_reasons", []) or [])
    except Exception:
        reverse_dcf = None

    # Current-state, WACC, downside, business-analysis + assumption-grounding —
    # computed as locals so the grounding (Phase 1) can reuse them.
    _cs_payload = _current_state_payload(ticker, result, multiple_decomposition)
    _wa_payload = _wacc_analysis_payload(ticker, result)
    _ba_payload = _business_analysis_payload(ticker, calc)
    _ag_payload = _assumption_grounding_payload(
        calc, result, _ba_payload, _cs_payload, _wa_payload)

    # Value source decomposition (spec §3 / Build 4) — same tool the report
    # uses, so the live deep-dive shows the identical attribution.
    _vsd_payload = None
    try:
        from aletheia.tools.value_source_decomposition import (
            build_value_source_decomposition as _bvsd,
        )
        _vsd_payload = _bvsd(calc, result, p2=None)
    except Exception:
        _vsd_payload = None

    # SaaS analysis overlay (Build B) — gated to SaaS names; None otherwise.
    _saas_payload = None
    try:
        from aletheia.tools.saas_metrics import build_saas_metrics as _bsm
        _sm = _bsm(calc, market_cap=(float(result.market_cap) if result.market_cap else None))
        _saas_payload = _sm if _sm.get("available") else None
    except Exception:
        _saas_payload = None

    # CADS coverage (Phase 2) — credit floor / §9 trigger.
    _cads_payload = None
    try:
        from aletheia.tools.cads_coverage import build_cads_coverage as _bcc
        _cc = _bcc(calc)
        _cads_payload = _cc if _cc.get("available") else None
    except Exception:
        _cads_payload = None

    # Capital-structure flag + four-method convergence (Phase 0/1/3).
    _csf_payload = None
    _vm_payload = None
    try:
        from aletheia.tools.capital_structure_flag import build_capital_structure_flag as _bcf
        _cf = _bcf(calc, market_cap=(float(result.market_cap) if result.market_cap else None))
        _csf_payload = _cf if _cf.get("available") else None
    except Exception:
        _csf_payload = None
    try:
        from aletheia.tools.valuation_methods import build_valuation_methods as _bvm
        _vm = _bvm(calc, result, None)
        _vm_payload = _vm if _vm.get("available") else None
    except Exception:
        _vm_payload = None

    return {
        "ticker":                 ticker.upper(),
        "wacc":                   float(result.wacc_base) if result.wacc_base else None,
        "beta":                   float(result.beta) if result.beta else None,
        "risk_free_rate":         float(result.risk_free_rate) if result.risk_free_rate else None,
        # Price provenance — every scenario IPS/MoS on this response is computed against current_price.
        "current_price":          float(result.current_price) if result.current_price else None,
        "market_cap":             float(result.market_cap)    if result.market_cap    else None,
        "shares_diluted":         float(result.shares_diluted) if result.shares_diluted else None,
        "run_date":               result.run_date if hasattr(result, "run_date") else None,
        # Phase Q-5: which period drove the base year. UI shows the
        # FY-base IPS alongside for reconciliation when this is 'TTM'.
        "base_period":            getattr(result, "base_period", "FY"),
        "base_period_end_date":   getattr(result, "base_period_end_date", None),
        "fy_fiscal_year":         getattr(result, "fy_fiscal_year", None),
        "bear":                   scenario_dict(result.bear),
        "base":                   scenario_dict(result.base),
        "bull":                   scenario_dict(result.bull),
        "reverse_dcf":            reverse_dcf,
        "multiple_decomposition": multiple_decomposition,
        "downside_protection":    _downside_protection_payload(calc, result, multiple_decomposition),
        "wacc_analysis":          _wa_payload,
        "current_state":          _cs_payload,
        "business_analysis":      _ba_payload,
        "assumption_grounding":   _ag_payload,
        "market_context":         _market_context_payload(ticker),
        "value_source_decomposition": _vsd_payload,
        "saas_metrics":           _saas_payload,
        "cads":                   _cads_payload,
        "capital_structure_flag": _csf_payload,
        "valuation_methods":      _vm_payload,
        "bank_valuation_methods": None,   # financial-sector only; FCFF uses the four-method set
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
    # Price provenance — every scenario's IPS/MoS is computed against current_price.
    current_price: Optional[float] = None
    market_cap: Optional[float] = None
    shares_diluted: Optional[float] = None
    run_date: Optional[str] = None
    # Period provenance (Phase Q-5) — 'TTM' or 'FY'.
    base_period: Optional[str] = None
    base_period_end_date: Optional[str] = None
    fy_fiscal_year: Optional[int] = None
    bear: Optional[DCFScenario]
    base: Optional[DCFScenario]
    bull: Optional[DCFScenario]
    reverse_dcf: Optional[dict]
    multiple_decomposition: Optional[dict]
    # Single-driver sensitivity band for specialized engines (bull/bear flexed
    # off one key driver). Lets the three-scenario panel label the band.
    scenario_band: Optional[dict] = None
    # Specialized-engine identity + decomposition (NEE rate-base, JPM/AXP/UNH
    # DDM, BRK-B embedded value). For FCFF tickers these stay None — the
    # standard bull/base/bear + reverse_dcf carry the full picture.
    engine: Optional[str] = None
    valuation_decomposition: Optional[dict] = None
    source_citation: Optional[str] = None
    as_of_date: Optional[str] = None
    engine_warnings: Optional[List[str]] = None
    current_state: Optional[dict] = None
    downside_protection: Optional[dict] = None
    wacc_analysis: Optional[dict] = None
    business_analysis: Optional[dict] = None
    assumption_grounding: Optional[dict] = None
    market_context: Optional[dict] = None
    specialized_inputs: Optional[dict] = None


class DCFOverridesRequest(BaseModel):
    """Analyst edits to the DCF base assumptions. All optional — a field
    left None means 'use the model-derived value'. Bounds are enforced by
    ScenarioOverride when these are applied."""
    revenue_growth_y1_5:  Optional[float] = None
    revenue_growth_y6_10: Optional[float] = None
    terminal_ebit_margin: Optional[float] = None
    capex_pct_revenue:    Optional[float] = None
    tax_rate:             Optional[float] = None
    discount_rate:        Optional[float] = None   # WACC
    terminal_growth:      Optional[float] = None
    terminal_roic:        Optional[float] = None
    note:                 Optional[str] = None
    updated_by:           Optional[str] = None


class FlagAckRequest(BaseModel):
    """Analyst decision on a current-state flag (Phase 1.5 acknowledgment).

    ``decision`` is one of: override_applied (assumptions edited to reflect the
    flag), accepted_rationale (flag judged immaterial / already priced, with a
    written reason), rejected (flag disputed as inaccurate), needs_analysis
    (parked pending more work — does NOT clear the gate)."""
    flag_key:    str
    decision:    str
    rationale:   Optional[str] = None
    category:    Optional[str] = None
    severity:    Optional[str] = None
    decided_by:  Optional[str] = None


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

    # LLM proposer (Phase 1 + 2). None for legacy / non-proposed rows.
    # The UI renders a badge based on `provenance` + `review_state`,
    # pre-fills the review dialog from `llm_proposal`, and shows the
    # confidence chip from `confidence`.
    provenance: Optional[str] = None              # "llm_proposed" | "analyst_confirmed" | "analyst_adjusted" | "analyst_overridden"
    review_state: Optional[str] = None            # "unreviewed" | "reviewed_no_change" | "reviewed_adjusted"
    confidence: Optional[str] = None              # "high" | "medium" | "low"
    llm_proposal: Optional[Dict[str, Any]] = None  # Snapshot of LLM proposal at the time of review
    llm_proposal_latest: Optional[Dict[str, Any]] = None  # Latest LLM proposal — may differ from `llm_proposal` after Stage 4 re-run


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
            "value_source_decomposition": dcf_payload.get("value_source_decomposition"),
            "saas_metrics":           dcf_payload.get("saas_metrics"),
            "cads":                   dcf_payload.get("cads"),
            "capital_structure_flag": dcf_payload.get("capital_structure_flag"),
            "valuation_methods":      dcf_payload.get("valuation_methods"),
            "bank_valuation_methods": dcf_payload.get("bank_valuation_methods"),
            "bank_metrics":           dcf_payload.get("bank_metrics"),
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

    # Reconcile the persisted agent thesis's NUMERIC margin of safety / IV with the
    # LIVE deterministic recompute. The investment_thesis block is an agent_runs
    # snapshot — its MoS is frozen at synthesis time, so any later valuation change
    # (here: the CF-R19 bank headline override, DDM $118 → RI $315) leaves a stale
    # −64% on the report header while three_scenario_dcf.base already reads −5%.
    # Narrative stays from the agent; the headline number follows the live engine.
    _thesis = dict((llm or {}).get("investment_thesis") or
                   ((legacy or {}).get("4_valuation_synthesis") or {}).get("investment_thesis") or {})
    _live_base = ((phase2.get("three_scenario_dcf") or {}).get("base") or {})
    if _live_base.get("margin_of_safety") is not None:
        _thesis["margin_of_safety"] = _live_base.get("margin_of_safety")
    if _live_base.get("intrinsic_per_share") is not None:
        _thesis["intrinsic_per_share"] = _live_base.get("intrinsic_per_share")

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
            "investment_thesis":    _thesis,
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
        current_price=payload.get("current_price"),
        market_cap=payload.get("market_cap"),
        shares_diluted=payload.get("shares_diluted"),
        run_date=payload.get("run_date"),
        base_period=payload.get("base_period"),
        base_period_end_date=payload.get("base_period_end_date"),
        fy_fiscal_year=payload.get("fy_fiscal_year"),
        bear=DCFScenario(**payload["bear"]) if payload["bear"] else None,
        base=DCFScenario(**payload["base"]) if payload["base"] else None,
        bull=DCFScenario(**payload["bull"]) if payload["bull"] else None,
        reverse_dcf=payload["reverse_dcf"],
        multiple_decomposition=payload["multiple_decomposition"],
        scenario_band=payload.get("scenario_band"),
        engine=payload.get("engine"),
        valuation_decomposition=payload.get("valuation_decomposition"),
        specialized_inputs=payload.get("specialized_inputs"),
        source_citation=payload.get("source_citation"),
        as_of_date=payload.get("as_of_date"),
        engine_warnings=payload.get("engine_warnings"),
        current_state=payload.get("current_state"),
        downside_protection=payload.get("downside_protection"),
        wacc_analysis=payload.get("wacc_analysis"),
        business_analysis=payload.get("business_analysis"),
        assumption_grounding=payload.get("assumption_grounding"),
        market_context=payload.get("market_context"),
    )


# ─────────────────────────────────────────────────────────────────────────────
# DCF assumption overrides (analyst what-if, persisted per ticker)
# ─────────────────────────────────────────────────────────────────────────────

_DCF_OVERRIDE_FIELDS = (
    "revenue_growth_y1_5", "revenue_growth_y6_10", "terminal_ebit_margin",
    "capex_pct_revenue", "tax_rate", "discount_rate", "terminal_growth",
    "terminal_roic",
)


def _assumptions_dict_from_result(result: Any) -> Dict[str, Any]:
    """Extract the base-scenario assumptions into the bundle dict shape the
    UI + validator use (mirrors ui/financials.py)."""
    base = getattr(result, "base", None)
    a = getattr(base, "assumptions", None) if base else None
    if a is None:
        return {}
    def _f(v):
        try:
            return float(v) if v is not None else None
        except (TypeError, ValueError):
            return None
    return {
        "revenue_cagr_y1_5":  _f(a.revenue_cagr_y1_5),
        "revenue_cagr_y6_10": _f(a.revenue_cagr_y6_10),
        "ebit_margin_current": _f(a.ebit_margin_current),
        "ebit_margin_terminal": _f(a.ebit_margin_terminal),
        "capex_pct_revenue":  _f(a.capex_pct_revenue),
        "da_pct_revenue":     _f(a.da_pct_revenue),
        "nwc_pct_revenue":    _f(a.nwc_pct_revenue),
        "tax_rate":           _f(a.tax_rate),
        "wacc":               _f(a.wacc),
        "terminal_growth":    _f(a.terminal_growth),
        "terminal_roic":      _f(a.terminal_roic),
        "base_roic":          _f(a.base_roic),
    }


def _build_dcf_assumptions_payload(
    ticker: str,
    *,
    candidate_overrides: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute the effective + baseline DCF assumptions for a ticker and
    validate them. Used by the GET/PUT assumptions endpoints and the Stage 4
    gate.

    When ``candidate_overrides`` is provided, those are applied (NOT
    persisted) instead of the saved ones — used by PUT to validate before
    committing. Otherwise the persisted overrides are used.

    Raises HTTPException(404) for unknown ticker, (409) when the ticker
    routes to a specialized (non-FCFF) engine where these assumptions don't
    apply.
    """
    from aletheia.utils.calc_input_builder import make_calc_input
    from aletheia.contracts.interfaces import ScenarioOverride
    from aletheia.agents.scenario_eval_node import _clone_profile_with_overrides
    from aletheia.contracts.interfaces import CalculationInput
    from aletheia.tools.dcf_engine import DCFEngine
    from aletheia.calculations.dcf_assumption_validation import (
        validate_dcf_assumptions,
    )
    from aletheia.data.database import InvestmentDatabase

    # Baseline (model-derived, no overrides).
    try:
        baseline_calc = make_calc_input(ticker, apply_overrides=False)
    except Exception as e:
        raise HTTPException(status_code=404,
                            detail=f"No cleaned data for {ticker}: {e}")

    # Determine which overrides apply: candidate (unsaved) or persisted.
    if candidate_overrides is not None:
        applied = {k: candidate_overrides[k] for k in _DCF_OVERRIDE_FIELDS
                   if candidate_overrides.get(k) is not None}
        provenance = {}
    else:
        db = InvestmentDatabase(verbose=False)
        try:
            saved = db.get_dcf_overrides(ticker)
        finally:
            db.close()
        applied = {k: saved[k] for k in _DCF_OVERRIDE_FIELDS
                   if saved.get(k) is not None}
        provenance = {k: saved.get(k) for k in ("updated_at", "updated_by", "note")
                      if saved.get(k) is not None}

    # Build the effective calc input from the baseline profile + applied
    # overrides. ScenarioOverride enforces the per-field bounds (raises 422).
    effective_calc = baseline_calc
    if applied:
        try:
            override = ScenarioOverride(
                name="Analyst assumptions", scenario_type="base_alternative",
                proposed_by="analyst",
                rationale=(provenance.get("note") or "Analyst-edited base assumptions.")[:600],
                **applied,
            )
        except Exception as e:
            raise HTTPException(status_code=422, detail=f"Invalid override: {e}")
        eff_profile = _clone_profile_with_overrides(
            baseline_calc.valuation_profile, override)
        effective_calc = CalculationInput(
            df=baseline_calc.df, classification=baseline_calc.classification,
            known_issues=baseline_calc.known_issues, valuation_profile=eff_profile,
            lifecycle_thresholds=baseline_calc.lifecycle_thresholds,
        )

    engine = DCFEngine(verbose=False)
    try:
        eff_result = engine.run(effective_calc)
        base_result = engine.run(baseline_calc) if applied else eff_result
    except NotImplementedError:
        raise HTTPException(
            status_code=409,
            detail=(f"{ticker} routes to a specialized (non-FCFF) valuation "
                    "engine; editable DCF assumptions don't apply."),
        )
    except Exception as e:
        raise HTTPException(status_code=422,
                            detail=f"DCF compute failed for {ticker}: {e}")

    eff_assumptions = _assumptions_dict_from_result(eff_result)
    base_assumptions = _assumptions_dict_from_result(base_result)

    overridden_overrides = set(applied.keys())

    # Validate the analyst's RAW inputs for overridden fields, not the
    # engine's post-clamp values. The engine silently clamps e.g.
    # terminal_growth to MAX_TERMINAL_G, so reading it back would hide an
    # out-of-cap input. Overlay the raw override values onto the effective
    # assumptions (non-overridden fields stay model-derived) so validation
    # reflects what the analyst actually asked for.
    from aletheia.calculations.dcf_assumption_validation import (
        ASSUMPTION_TO_OVERRIDE,
    )
    _override_to_assumption = {v: k for k, v in ASSUMPTION_TO_OVERRIDE.items()}
    to_validate = dict(eff_assumptions)
    for ov_field, raw_val in applied.items():
        akey = _override_to_assumption.get(ov_field)
        if akey is not None:
            to_validate[akey] = raw_val

    tg_cap = getattr(baseline_calc.valuation_profile, "terminal_growth_cap", None)
    validation = validate_dcf_assumptions(
        to_validate, overridden_overrides,
        terminal_growth_cap=tg_cap, baseline=base_assumptions,
    )

    def _scn(s):
        if s is None:
            return None
        ev = float(s.enterprise_value)
        ips = eff_result.intrinsic_per_share(ev, eff_result.net_debt)
        return {
            "intrinsic_per_share": float(ips) if ips is not None else None,
            "margin_of_safety": float(eff_result.upside(ips)) if ips else None,
        }

    return {
        "ticker": ticker.upper(),
        "assumptions": eff_assumptions,
        "baseline_assumptions": base_assumptions,
        "overridden_fields": sorted(overridden_overrides),
        "provenance": provenance,
        "current_price": float(getattr(eff_result, "current_price", 0.0) or 0.0) or None,
        "scenarios": {
            "bear": _scn(eff_result.bear),
            "base": _scn(eff_result.base),
            "bull": _scn(eff_result.bull),
        },
        "validation": validation.to_dict(),
        "bounds": {
            "revenue_growth_y1_5": [0.0, 0.45],
            "revenue_growth_y6_10": [0.0, 0.25],
            "terminal_ebit_margin": [0.0, 0.65],
            "capex_pct_revenue": [0.0, 0.50],
            "tax_rate": [0.0, 0.40],
            "discount_rate": [0.04, 0.16],
            "terminal_growth": [-0.02, 0.06],
        },
    }


@app.get("/ticker/{ticker}/dcf/assumptions", tags=["Ticker"])
def get_dcf_assumptions(ticker: str):
    """Effective DCF base assumptions (model-derived + any persisted
    overrides), with validation, provenance, and editable-field bounds."""
    return _build_dcf_assumptions_payload(ticker.upper())


@app.get("/ticker/{ticker}/dcf/assumptions/validate", tags=["Ticker"])
def validate_dcf_assumptions_endpoint(ticker: str):
    """Run the 4-layer DCF-assumption validation against the ticker's
    current effective assumptions. The Stage 4 confirmation panel reads this."""
    return _build_dcf_assumptions_payload(ticker.upper())["validation"]


@app.put("/ticker/{ticker}/dcf/overrides", tags=["Ticker"])
def put_dcf_overrides(ticker: str, body: DCFOverridesRequest):
    """Validate candidate overrides, persist them, and return the recomputed
    DCF assumptions bundle. Rejects (422) when validation finds a hard error
    — the override is NOT persisted in that case."""
    ticker = ticker.upper()
    candidate = {k: getattr(body, k) for k in _DCF_OVERRIDE_FIELDS}
    # Compute + validate against the candidate (not yet persisted).
    payload = _build_dcf_assumptions_payload(ticker, candidate_overrides=candidate)
    if payload["validation"]["status"] == "error":
        raise HTTPException(
            status_code=422,
            detail={"message": "DCF assumptions failed validation; not saved.",
                    "errors": payload["validation"]["errors"]},
        )
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)
    try:
        db.upsert_dcf_overrides(
            ticker, candidate,
            updated_by=(body.updated_by or "analyst"), note=body.note,
        )
    finally:
        db.close()
    # Re-read from persisted state so provenance is populated in the response.
    return _build_dcf_assumptions_payload(ticker)


@app.delete("/ticker/{ticker}/dcf/overrides", tags=["Ticker"])
def delete_dcf_overrides(ticker: str):
    """Clear all persisted overrides for a ticker — revert to model defaults.
    Returns the recomputed (model-default) assumptions bundle."""
    ticker = ticker.upper()
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)
    try:
        db.clear_dcf_overrides(ticker)
    finally:
        db.close()
    return _build_dcf_assumptions_payload(ticker)


# ── Current-State flag acknowledgments (Phase 1.5) ──────────────────────────

_ACK_DECISIONS = {"override_applied", "accepted_rationale", "rejected", "needs_analysis"}


@app.get("/ticker/{ticker}/current_state/acknowledgments", tags=["Ticker"])
def get_flag_acks_endpoint(ticker: str):
    """All persisted flag acknowledgments for a ticker (the audit trail)."""
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)
    try:
        return {"ticker": ticker.upper(), "acknowledgments": db.get_flag_acks(ticker)}
    finally:
        db.close()


@app.put("/ticker/{ticker}/current_state/acknowledgments", tags=["Ticker"])
def put_flag_ack_endpoint(ticker: str, body: FlagAckRequest):
    """Record an analyst's decision on a current-state flag. Persisting an
    ack with a clearing decision (anything but ``needs_analysis``) resolves
    that flag in the gate. Returns the refreshed current-state payload."""
    ticker = ticker.upper()
    if body.decision not in _ACK_DECISIONS:
        raise HTTPException(
            status_code=422,
            detail=f"decision must be one of {sorted(_ACK_DECISIONS)}")
    if body.decision in ("accepted_rationale", "rejected") and not (body.rationale or "").strip():
        raise HTTPException(
            status_code=422,
            detail=f"a written rationale is required for decision '{body.decision}'")
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)
    try:
        db.upsert_flag_ack(
            ticker, body.flag_key, decision=body.decision,
            rationale=body.rationale, category=body.category,
            severity=body.severity, decided_by=(body.decided_by or "analyst"))
        acks = db.get_flag_acks(ticker)
    finally:
        db.close()
    return {"ticker": ticker, "acknowledgments": acks}


@app.delete("/ticker/{ticker}/current_state/acknowledgments", tags=["Ticker"])
def delete_flag_ack_endpoint(ticker: str, flag_key: Optional[str] = None):
    """Remove one ack (``?flag_key=...``) or all acks for a ticker (re-opens
    the flag in the gate)."""
    ticker = ticker.upper()
    from aletheia.data.database import InvestmentDatabase
    db = InvestmentDatabase(verbose=False)
    try:
        db.clear_flag_ack(ticker, flag_key)
        acks = db.get_flag_acks(ticker)
    finally:
        db.close()
    return {"ticker": ticker, "acknowledgments": acks}


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

    # "Current FY" fundamentals must be the latest *FY* row, NOT simply the
    # max fiscal_year — the TTM row carries a higher fiscal_year (e.g. 2026
    # TTM vs 2025 FY), so a plain max() silently returns TTM numbers under a
    # "current FY" label. That mislabel made ORCL's FY FCF margin (−0.7%)
    # render as the TTM figure (−38.6%), contradicting the ratios table.
    fy_df = df[df["period"] == "FY"] if "period" in df.columns else df
    if fy_df.empty:
        fy_df = df  # no FY rows on file — fall back to whatever exists
    row = fy_df[fy_df["fiscal_year"] == fy_df["fiscal_year"].max()].iloc[0]

    def gdb(col, fb=None):
        v = row.get(col)
        return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else fb

    fy = int(fy_df["fiscal_year"].max())
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
    Graham + Lynch + Malkiel + NorthWestern with pass/flag/fail signals.
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
        # LLM-proposer fields surfaced for the dashboard badge + review dialog.
        # All None for deterministic / pending / legacy rows.
        "provenance":      latest_record.get("provenance") if latest_record else None,
        "review_state":    latest_record.get("review_state") if latest_record else None,
        "confidence":      latest_record.get("confidence") if latest_record else None,
        "llm_proposal":    latest_record.get("llm_proposal") if latest_record else None,
        "llm_proposal_latest": (
            latest_record.get("llm_proposal_latest") if latest_record else None
        ),
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


def _classify_analyst_submission(
    *,
    sub_scores: Dict[str, float],
    narrative: Optional[str],
    prior_proposal: Dict[str, Any],
    prior_provenance: Optional[str],
) -> tuple[str, str]:
    """Compute (provenance, review_state) for a HITL submission.

    The badge on the dashboard distinguishes three review outcomes:
      - analyst_confirmed: submission matches the prior LLM proposal verbatim
      - analyst_adjusted:  prior LLM proposal exists but analyst changed
        at least one sub-score or the narrative
      - analyst_overridden: no LLM proposal existed, OR the prior was
        already analyst-owned (re-submission)

    Numeric sub-scores compared as ints; narrative compared after
    whitespace strip + lowercase for robustness against trivial edits.
    """
    if not prior_proposal or prior_provenance in (
        "analyst_overridden", "analyst_adjusted",
    ):
        return "analyst_overridden", "reviewed_adjusted"

    llm_sub = (prior_proposal or {}).get("sub_scores") or {}
    same_scores = (
        set(sub_scores.keys()) == set(llm_sub.keys())
        and all(int(sub_scores[k]) == int(llm_sub.get(k, -1))
                for k in sub_scores)
    )

    llm_narr = (prior_proposal or {}).get("narrative") or ""
    sub_narr = narrative or ""
    same_narr = llm_narr.strip().lower() == sub_narr.strip().lower()

    if same_scores and same_narr:
        return "analyst_confirmed", "reviewed_no_change"
    return "analyst_adjusted", "reviewed_adjusted"


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

    # Provenance computation — compare the submission to the prior
    # LLM proposal (if any) so the dashboard can distinguish
    # "analyst confirmed LLM's proposal verbatim" from
    # "analyst adjusted the LLM proposal" from "analyst wrote their
    # own from scratch with no prior proposal".
    db = InvestmentDatabase(verbose=False)
    try:
        prior = db.get_latest_assessment(ticker_u, dimension_id)
        prior_proposal = (prior or {}).get("llm_proposal") or {}
        provenance, review_state = _classify_analyst_submission(
            sub_scores=request.sub_scores,
            narrative=request.narrative,
            prior_proposal=prior_proposal,
            prior_provenance=(prior or {}).get("provenance"),
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
            provenance=provenance,
            review_state=review_state,
            confidence=None,   # confidence is LLM-self-rated; not relevant for analyst
            # Carry the original LLM proposal forward so future re-runs
            # can compute drift without losing it. None when there was
            # no prior LLM proposal.
            llm_proposal=(prior_proposal or None),
        )
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


@app.get("/ticker/{ticker}/report/detailed", tags=["Reports"])
def get_report_detailed_md(ticker: str):
    """Serve the Detailed Markdown report."""
    path = REPORT_DIR / f"{ticker.upper()}_Detailed_Report.md"
    if not path.exists():
        raise HTTPException(404, f"No detailed report for {ticker}")
    return FileResponse(path, media_type="text/markdown")


@app.post("/ticker/{ticker}/report/rebuild", tags=["Reports"])
def rebuild_report(ticker: str):
    """Regenerate the report's DETERMINISTIC sections + re-render HTML/MD
    WITHOUT a Stage 4 (no LLM cost).

    Refreshes ``current_state`` (consensus, market signal, analyst sentiment,
    sector valuation, policy/regulatory) and ``downside_protection`` (asymmetry,
    downside ladder, required-MoS, sizing) on the existing serving report, then
    re-renders the Executive HTML + Detailed MD. All LLM-authored sections
    (thesis, contrarian, economic reality) are preserved as-is; the contrarian's
    failure_modes + pre-mortem (if present from the last Stage 4) are carried
    into the downside block. Use a full Stage 4 run to refresh those LLM pieces."""
    ticker_u = ticker.upper()
    path_json = REPORT_DIR / f"{ticker_u}_report.json"
    if not path_json.exists():
        raise HTTPException(
            404, f"No serving report for {ticker_u}; run Stage 4 first.")
    try:
        report = json.loads(path_json.read_text())
    except Exception as e:
        raise HTTPException(422, f"Cannot read report JSON: {e}")

    refreshed = []
    # Current-state (deterministic / cache-backed).
    try:
        from aletheia.agents.current_state import compose_current_state
        cs = compose_current_state(ticker_u)
        if cs:
            report["current_state"] = cs
            refreshed.append("current_state")
    except Exception as e:
        report.setdefault("_rebuild_warnings", []).append(f"current_state: {e}")

    # Downside protection — carry forward the contrarian LLM extras if present.
    try:
        from aletheia.tools.downside_protection import compose_downside_protection
        _synth = report.get("4_valuation_synthesis") or {}
        _tier = ((_synth.get("investment_thesis") or {})
                 .get("pillar_scores") or {}).get("position_tier")
        _ca = _synth.get("contrarian_analysis") or {}
        dp = compose_downside_protection(
            ticker_u, conviction_tier=_tier,
            failure_modes=_ca.get("failure_modes") or [],
            premortem=_ca.get("premortem") or "")
        if dp:
            report["downside_protection"] = dp
            refreshed.append("downside_protection")
    except Exception as e:
        report.setdefault("_rebuild_warnings", []).append(f"downside_protection: {e}")

    # WACC analysis (deterministic).
    try:
        from aletheia.tools.wacc_analysis import compose_wacc_analysis
        wa = compose_wacc_analysis(ticker_u)
        if wa:
            report["wacc_analysis"] = wa
            refreshed.append("wacc_analysis")
    except Exception as e:
        report.setdefault("_rebuild_warnings", []).append(f"wacc_analysis: {e}")

    # Bottom-up business analysis + assumption grounding (deterministic). The
    # report context enriches the coverage map (business_model fields).
    try:
        from aletheia.tools.business_analysis import compose_business_analysis
        ba = compose_business_analysis(ticker_u, report=report)
        if ba:
            report["business_analysis"] = ba
            refreshed.append("business_analysis")
    except Exception as e:
        report.setdefault("_rebuild_warnings", []).append(f"business_analysis: {e}")
    try:
        from aletheia.tools.assumption_grounding import compose_assumption_grounding
        ag = compose_assumption_grounding(ticker_u)
        if ag:
            report["assumption_grounding"] = ag
            refreshed.append("assumption_grounding")
    except Exception as e:
        report.setdefault("_rebuild_warnings", []).append(f"assumption_grounding: {e}")
    try:
        from aletheia.tools.market_context import compose_market_context
        mc = compose_market_context(ticker_u)
        if mc:
            report["market_context"] = mc
            refreshed.append("market_context")
    except Exception as e:
        report.setdefault("_rebuild_warnings", []).append(f"market_context: {e}")

    # Refresh the HEADLINE three-scenario IVs from the SAME live engine the §7
    # discount detail uses, so the headline base IV and the WACC-sensitivity base
    # row don't diverge (the no-LLM rebuild previously left phase2_valuation
    # stale while recomputing §7 live). Only the IV/MoS/EV/wacc the headline
    # renders are touched; other phase2 fields are left intact.
    try:
        from aletheia.utils.calc_input_builder import make_calc_input as _mci
        from aletheia.tools.dcf_engine import DCFEngine as _Eng
        _calc = _mci(ticker_u)
        _res = None         # FCFF DCFResult
        _spec = None        # specialized router ValuationResult
        try:
            _res = _Eng(verbose=False).run(_calc)
        except NotImplementedError:
            # Specialized (REIT / rate-base / DDM / embedded-value) — route via
            # the ValuationRouter so the headline IV reflects the right engine.
            from aletheia.tools.valuation_router import ValuationRouter as _VR
            _spec = _VR().execute(_calc)
        _p2 = (report.get("4_valuation_synthesis") or {}).get("phase2_valuation")
        if _p2 is not None and _res is not None and getattr(_res, "base", None):
            _price = _res.current_price
            _tsd = _p2.setdefault("three_scenario_dcf", {})
            for _name in ("bear", "base", "bull"):
                _sc = getattr(_res, _name, None)
                if _sc is None:
                    continue
                _ev = float(_sc.enterprise_value)
                _ips = _res.intrinsic_per_share(_ev, _res.net_debt)
                _tsd[_name] = {
                    "intrinsic_per_share": float(_ips) if _ips is not None else None,
                    "margin_of_safety": ((_ips / _price - 1.0)
                                         if (_ips and _price) else None),
                    "ev": _ev,
                }
            if _res.wacc_base:
                _p2["wacc"] = float(_res.wacc_base)
            # Propagate the LIVE price so every price-dependent module agrees.
            # Without this, phase2.current_price stayed frozen at the last full
            # run while the scenario MoS used the fresh price — the torn-price
            # bug (e.g. ADBE $251.44 stale vs $204 live).
            if _price:
                _p2["current_price"] = float(_price)
            # Recompute the price-dependent deterministic modules off the live
            # price (reverse-DCF + multiple decomposition), matching the shapes
            # _compute_dcf_live produces.
            try:
                from aletheia.tools.multiple_decomposition import MultipleDecomposition as _MD
                _mdr = _MD(verbose=False).run(_calc)
                _p2["multiple_decomposition"] = {
                    "market_ev_ebitda": _mdr.market_ev_ebitda,
                    "justified_ev_ebitda": _mdr.justified_ev_ebitda,
                    "premium_pct": _mdr.ev_ebitda_premium_pct,
                    "signal": _mdr.signal,
                    "roic_wacc_spread": _mdr.roic_wacc_spread,
                    "value_creation": _mdr.value_creation,
                    "roic": _mdr.roic, "wacc": _mdr.wacc,
                    "sector": getattr(_mdr, "sector", None),
                    "sector_median_ev_ebitda": _mdr.sector_median_ev_ebitda,
                    "vs_sector_premium": _mdr.vs_sector_premium,
                }
                refreshed.append("multiple_decomposition")
            except Exception as e:
                report.setdefault("_rebuild_warnings", []).append(f"multiple_decomposition: {e}")
            try:
                from aletheia.tools.reverse_dcf import ReverseDCF as _RD
                _rk = {}
                _bsm = getattr(_res.base, "assumptions", None)
                if _bsm is not None:
                    _rk = {"wacc_override": float(_bsm.wacc),
                           "margin_override": float(_bsm.ebit_margin_terminal),
                           "terminal_growth_override": float(_bsm.terminal_growth)}
                _rdr = _RD(verbose=False).run(_calc, **_rk)
                _p2["reverse_dcf"] = _rdr.to_dict()
                _p2["reverse_dcf"]["reasons"] = list(getattr(_rdr, "signal_reasons", []) or [])
                refreshed.append("reverse_dcf")
            except Exception as e:
                report.setdefault("_rebuild_warnings", []).append(f"reverse_dcf: {e}")
            refreshed.append("phase2_headline_iv")
        elif _p2 is not None and _spec is not None and _spec.intrinsic_per_share is not None:
            # Specialized engine: a single IV (no bull/bear scenario triangle).
            _tsd = _p2.setdefault("three_scenario_dcf", {})
            _tsd["base"] = {
                "intrinsic_per_share": float(_spec.intrinsic_per_share),
                "margin_of_safety": _spec.margin_of_safety,
                "ev": _spec.equity_value,
            }
            _p2["engine"] = _spec.engine
            # Store the engine's valuation decomposition (e.g. the two-stage
            # AFFO table for REITs) + the inputs snapshot so the memo can render
            # the math.
            _es = _spec.engine_specific or {}
            _p2["valuation_decomposition"] = _es.get("decomposition")
            _p2["specialized_inputs"] = _spec.inputs_snapshot
            if _spec.current_price:
                _p2["current_price"] = float(_spec.current_price)
            refreshed.append("phase2_headline_iv")
        # Recompute the value-source decomposition (Build 4/6) so the engine
        # attribution + conviction durability read refresh with any overrides
        # (the EQIX stale-phase2 lesson). Works for FCFF (_res) and specialized
        # (_res None, p2 specialized_inputs) alike.
        try:
            from aletheia.tools.value_source_decomposition import (
                build_value_source_decomposition as _bvsd,
            )
            if _p2 is not None and (_res is not None or _p2.get("specialized_inputs")):
                _p2["value_source_decomposition"] = _bvsd(_calc, _res, p2=_p2)
                refreshed.append("value_source_decomposition")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(
                f"value_source_decomposition: {e}")
        # SaaS overlay (Build B) — gated; refresh so the panel reflects any edits.
        try:
            from aletheia.tools.saas_metrics import build_saas_metrics as _bsm
            if _p2 is not None:
                _mc = float(_res.market_cap) if (_res is not None and _res.market_cap) else None
                _sm = _bsm(_calc, market_cap=_mc)
                if _sm.get("available"):
                    _p2["saas_metrics"] = _sm
                    refreshed.append("saas_metrics")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(f"saas_metrics: {e}")
        # CADS coverage (Phase 2) — refresh the §9 credit floor.
        try:
            from aletheia.tools.cads_coverage import build_cads_coverage as _bcc
            if _p2 is not None:
                _cc = _bcc(_calc)
                if _cc.get("available"):
                    _p2["cads"] = _cc
                    refreshed.append("cads")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(f"cads: {e}")
        # Capital-structure flag + four-method convergence (Phase 0/1/3).
        try:
            from aletheia.tools.capital_structure_flag import build_capital_structure_flag as _bcf
            from aletheia.tools.valuation_methods import build_valuation_methods as _bvm
            if _p2 is not None:
                _mc = float(_res.market_cap) if (_res is not None and _res.market_cap) else None
                _cf = _bcf(_calc, market_cap=_mc)
                if _cf.get("available"):
                    _p2["capital_structure_flag"] = _cf
                    refreshed.append("capital_structure_flag")
                if _res is not None:
                    _vm = _bvm(_calc, _res, _p2)
                    if _vm.get("available"):
                        _p2["valuation_methods"] = _vm
                        refreshed.append("valuation_methods")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(f"valuation_methods: {e}")
        # Bank convergent set (residual income / justified P/B / Gordon DDM) —
        # financial-sector analog; reconciles vs the headline DDM. Uses the
        # specialized router result (_spec), since banks have no FCFF _res.
        try:
            from aletheia.tools.bank_valuation_methods import build_bank_valuation_methods as _bbvm
            if _p2 is not None and _spec is not None:
                _bm = _bbvm(_calc, _spec, _p2)
                if _bm.get("available"):
                    _p2["bank_valuation_methods"] = _bm
                    refreshed.append("bank_valuation_methods")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(f"bank_valuation_methods: {e}")
        # Bank operating metrics (NII / efficiency / NIM / deposits / tangible book)
        # from SEC XBRL — was omitted from rebuild, so a rebuilt report dropped the
        # panel the deep-dive shows.
        try:
            from aletheia.tools.bank_metrics import build_bank_metrics as _bbm
            _shares = ((_spec.inputs_snapshot or {}).get("shares_diluted")
                       if _spec is not None else None)
            if _p2 is not None and _shares:
                _bkm = _bbm(_calc, shares=_shares)
                if _bkm.get("available"):
                    _p2["bank_metrics"] = _bkm
                    refreshed.append("bank_metrics")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(f"bank_metrics: {e}")
        # Method-appropriate headline (CF-R19): swap the structurally-low DDM base
        # IV/MoS for the convergent-set residual income when understatement is
        # flagged, so the rebuilt three_scenario_dcf.base matches the LLM-run path
        # and the deep-dive (otherwise the rebuilt headline reverts to DDM $118).
        try:
            from aletheia.tools.bank_valuation_methods import (
                bank_headline_override, bank_scenario_band,
            )
            if _p2 is not None and _spec is not None:
                _price = float(_spec.current_price) if _spec.current_price else None
                _tsd_base = (_p2.get("three_scenario_dcf") or {}).get("base") or {}
                _ho = bank_headline_override(
                    ddm_ips=_tsd_base.get("intrinsic_per_share"),
                    price=_price, bvm=_p2.get("bank_valuation_methods"))
                if _ho:
                    _tsd_base["intrinsic_per_share"] = _ho["intrinsic_per_share"]
                    _tsd_base["margin_of_safety"] = _ho["margin_of_safety"]
                    _p2.setdefault("three_scenario_dcf", {})["base"] = _tsd_base
                    _p2["headline_override"] = _ho
                    refreshed.append("headline_override")
                # Bank bear/bull (CF-R22): real RI band vs the fake $0.00 bear.
                _bd = bank_scenario_band(bvm=_p2.get("bank_valuation_methods"),
                                         price=_price)
                if _bd:
                    _tsd = _p2.setdefault("three_scenario_dcf", {})
                    _tsd["bear"] = _bd["bear"]
                    _tsd["bull"] = _bd["bull"]
                    _p2["bank_scenario_band"] = _bd
                    refreshed.append("bank_scenario_band")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(f"headline_override: {e}")
        # Refresh the Comprehensive Ratios + WACC build (§ metrics) from the
        # same fresh result, so ROIC / EV-EBITDA / Net-Debt-EBITDA / capital
        # weights reconcile with the engine instead of showing a stale FY-only
        # snapshot.
        try:
            from aletheia.utils.financial_metrics import build_financial_metrics
            from aletheia.data.database import InvestmentDatabase as _DB
            _db = _DB(verbose=False)
            try:
                _fm = build_financial_metrics(ticker_u, _db, dcf_result=_res, p2=_p2 or {})
            finally:
                _db.close()
            if _fm:
                report["5_financial_metrics"] = _fm
                refreshed.append("financial_metrics")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(f"financial_metrics: {e}")
    except Exception as e:
        report.setdefault("_rebuild_warnings", []).append(f"phase2_headline_iv: {e}")

    try:
        path_json.write_text(json.dumps(report, indent=2))
    except Exception as e:
        raise HTTPException(422, f"Cannot write report JSON: {e}")

    # Re-render HTML + Detailed MD from the refreshed JSON.
    rendered = []
    try:
        from aletheia.utils.report_generator import ReportGenerator
        gen = ReportGenerator(str(REPORT_DIR))
        gen.generate_html(ticker_u, report)
        rendered.append("html")
        try:
            gen.generate_detailed_markdown(ticker_u, report)
            rendered.append("detailed_md")
        except Exception as e:
            report.setdefault("_rebuild_warnings", []).append(f"detailed_md: {e}")
    except Exception as e:
        raise HTTPException(422, f"Report render failed: {e}")

    return {
        "ticker": ticker_u,
        "refreshed_sections": refreshed,
        "rendered": rendered,
        "note": ("Deterministic sections refreshed without Stage 4. Failure "
                 "modes + pre-mortem reflect the last Stage 4 run."),
        "warnings": report.get("_rebuild_warnings", []),
    }


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
# Pipeline stage endpoints — per-stage execution + status surface.
#
# These are the typed-contract entry points the Stage Explorer +
# Status Matrix UIs consume. They do NOT supersede the legacy
# /pipeline/run/{ticker} (subprocess to main.py) — that stays until
# the workflow/graph deprecation window closes (decision #3 in
# docs/pipeline_contracts.md). The new endpoints are the canonical
# path for any new caller.
#
# Spec: docs/pipeline_ui_design.md
# ─────────────────────────────────────────────────────────────────────────────

class _PipelineIngestRequest(BaseModel):
    force_refresh: bool = False
    sources: Optional[List[str]] = None
    include_market_snapshot: bool = True


class _PipelineValidateRequest(BaseModel):
    input_bundle_fingerprint: Optional[str] = None
    fiscal_years: Optional[List[int]] = None


class _PipelineCalculateRequest(BaseModel):
    fiscal_year: Optional[int] = None


class _PipelineAgentsRequest(BaseModel):
    """Stage 4 incurs LLM cost. ``confirm_llm_cost`` must be True
    explicitly — the endpoint refuses without it. Deliberate friction
    so the UI never accidentally triggers a paid run.

    ``confirm_assumptions_validated`` is the second gate: the analyst
    confirms the DCF assumptions (the basis for the Stage 4 thesis) have
    been reviewed. The endpoint also runs a hard validation pass and
    refuses on any error regardless of this flag."""
    confirm_llm_cost: bool = False
    confirm_assumptions_validated: bool = False
    confirm_current_state_flags: bool = False


class _PipelineRunRequest(BaseModel):
    auto_agents: bool = False
    bust_cache: Optional[List[str]] = None
    force_refresh: bool = False
    include_market_snapshot: bool = True


class _PipelineBustCacheRequest(BaseModel):
    stages: List[str]


def _pipeline_version() -> str:
    """Resolve current git SHA for stamping pipeline outputs.
    Mirrors the CLI helpers in aletheia/cli/*.py."""
    try:
        sha = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            stderr=subprocess.DEVNULL, text=True,
        ).strip()
        return sha or "unversioned"
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unversioned"


@app.post("/pipeline/stages/{ticker}/ingest", tags=["Pipeline"])
def pipeline_stage_ingest(ticker: str, body: _PipelineIngestRequest):
    """Run Stage 1 (ingest) for a ticker. Returns the typed
    IngestedRawBundle as JSON. Status row written to pipeline_status."""
    from aletheia.pipeline.stage1_ingest import (
        Stage1IngestError, run_stage1,
    )
    ticker = ticker.upper()
    try:
        bundle = run_stage1(
            ticker,
            pipeline_version=_pipeline_version(),
            force_refresh=body.force_refresh,
            sources=body.sources,
            include_market_snapshot=body.include_market_snapshot,
        )
    except Stage1IngestError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return json.loads(bundle.model_dump_json())


@app.post("/pipeline/stages/{ticker}/validate", tags=["Pipeline"])
def pipeline_stage_validate(ticker: str, body: _PipelineValidateRequest):
    """Run Stage 2 (validate + clean) for a ticker. Returns the list
    of typed ValidatedCleanedRecord payloads."""
    from aletheia.pipeline.stage2_validate import (
        Stage2ValidateError, run_stage2,
    )
    ticker = ticker.upper()
    try:
        records = run_stage2(
            ticker=ticker,
            pipeline_version=_pipeline_version(),
            input_bundle_fingerprint=body.input_bundle_fingerprint,
            fiscal_years=body.fiscal_years,
        )
    except Stage2ValidateError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return [json.loads(r.model_dump_json()) for r in records]


@app.post("/pipeline/stages/{ticker}/calculate", tags=["Pipeline"])
def pipeline_stage_calculate(ticker: str, body: _PipelineCalculateRequest):
    """Run Stage 3 (calculate) for a ticker. Adapter reads cleaned
    records from DuckDB (until Stage 2 emits typed contracts natively
    via the Week 5 path). Returns the typed CalculationBundle."""
    from aletheia.cli.calc import load_records
    from aletheia.pipeline.stage3_calculate import (
        Stage3InputError, run_stage3,
    )
    ticker = ticker.upper()
    pv = _pipeline_version()
    try:
        records = load_records(ticker, pv)
    except RuntimeError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    try:
        bundle = run_stage3(
            records,
            pipeline_version=pv,
            fiscal_year=body.fiscal_year,
        )
    except Stage3InputError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return json.loads(bundle.model_dump_json())


@app.post("/pipeline/stages/{ticker}/agents", tags=["Pipeline"])
def pipeline_stage_agents(ticker: str, body: _PipelineAgentsRequest):
    """Run Stage 4 (agents). Requires ``confirm_llm_cost: true`` in
    the body. Without that flag the endpoint refuses — deliberate
    friction so the UI never accidentally triggers a paid run."""
    if not body.confirm_llm_cost:
        raise HTTPException(
            status_code=400,
            detail=(
                "Stage 4 incurs LLM cost. Set `confirm_llm_cost: true` "
                "in the request body to proceed. The UI surfaces the "
                "estimated cost before issuing this call."
            ),
        )
    ticker = ticker.upper()

    # ── DCF-assumptions validation gate ─────────────────────────────────
    # Stage 4's LLM thesis is built ON the DCF. Refuse to burn LLM calls
    # on a valuation whose assumptions are economically invalid (Gordon
    # divergence, out-of-cap terminal growth, broken bounds). Hard errors
    # block unconditionally; warnings require explicit analyst
    # confirmation via `confirm_assumptions_validated`.
    try:
        _assum = _build_dcf_assumptions_payload(ticker)
        _val = _assum["validation"]
    except HTTPException as exc:
        # 409 = specialized non-FCFF engine: editable DCF assumptions don't
        # apply, so there's nothing to validate — let Stage 4 proceed.
        _val = None if exc.status_code == 409 else None
        if exc.status_code not in (409,):
            raise
    if _val is not None:
        if _val["status"] == "error":
            raise HTTPException(
                status_code=422,
                detail={
                    "message": (f"Stage 4 blocked: {ticker} DCF assumptions "
                                "failed validation. Fix the assumptions (or "
                                "reset overrides) before running agents."),
                    "errors": _val["errors"],
                },
            )
        if _val["status"] == "warn" and not body.confirm_assumptions_validated:
            raise HTTPException(
                status_code=412,
                detail={
                    "message": (f"Stage 4 needs confirmation: {ticker} DCF "
                                "assumptions have warnings. Review them and "
                                "set `confirm_assumptions_validated: true`."),
                    "warnings": _val["warnings"],
                },
            )

    # ── Current-State flag gate (Phase 1.5) ─────────────────────────────
    # An unresolved HIGH current-state flag means the engine's assumptions
    # disconnect from current reality (the NVO case) or a material adverse
    # event is unaddressed. Refuse to finalize a memo on top of that until
    # the analyst resolves each HIGH flag (override / accept-with-rationale /
    # reject) — `needs_analysis` does NOT clear it. The analyst can still
    # force past with `confirm_current_state_flags: true` (logged downstream).
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        _cs = _current_state_payload(
            ticker, DCFEngine(verbose=False).run(make_calc_input(ticker)))
    except Exception:
        _cs = None
    if _cs and _cs.get("unresolved_high", 0) > 0 and not body.confirm_current_state_flags:
        _pending = [f for f in _cs.get("flags", [])
                    if f.get("severity") == "HIGH" and not f.get("acknowledged")]
        raise HTTPException(
            status_code=412,
            detail={
                "message": (f"Stage 4 blocked: {ticker} has "
                            f"{_cs['unresolved_high']} unresolved HIGH "
                            "current-state flag(s). Acknowledge each (apply an "
                            "override, accept with rationale, or reject) in the "
                            "Current-State gate, or set "
                            "`confirm_current_state_flags: true` to override."),
                "flags": [{"key": f.get("key"), "category": f.get("category"),
                           "message": f.get("message")} for f in _pending],
            },
        )

    from aletheia.cli.calc import load_records
    from aletheia.pipeline.stage3_calculate import (
        Stage3InputError, run_stage3,
    )
    from aletheia.pipeline.stage4_agents import (
        Stage4AgentError, run_stage4,
    )
    ticker = ticker.upper()
    pv = _pipeline_version()
    try:
        records = load_records(ticker, pv)
        calc_bundle = run_stage3(records, pipeline_version=pv)
    except (RuntimeError, Stage3InputError) as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    try:
        bundle = run_stage4(calc_bundle, pipeline_version=pv)
    except Stage4AgentError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return json.loads(bundle.model_dump_json())


@app.post("/pipeline/stages/{ticker}/run", tags=["Pipeline"])
def pipeline_stage_run(ticker: str, body: _PipelineRunRequest):
    """Run the full orchestrator chain for a ticker. Optional
    bust_cache + force_refresh flags propagate cascade-invalidation
    per docs/pipeline_contracts.md decision #4."""
    from aletheia.pipeline.orchestrator import Orchestrator
    ticker = ticker.upper()
    pv = _pipeline_version()
    with Orchestrator() as orch:
        result = orch.run(
            ticker,
            pipeline_version=pv,
            auto_agents=body.auto_agents,
            bust_cache=body.bust_cache,
            force_refresh=body.force_refresh,
            include_market_snapshot=body.include_market_snapshot,
        )
    return {
        "ticker": result.ticker,
        "pipeline_version": result.pipeline_version,
        "started_at": result.started_at.isoformat(),
        "finished_at": result.finished_at.isoformat(),
        "auto_agents": result.auto_agents,
        "all_ok": result.all_ok,
        "stages": {
            stage: {
                "status": outcome.status.value,
                "fingerprint": outcome.fingerprint,
                "duration_seconds": round(outcome.duration_seconds, 3),
                "error_message": outcome.error_message,
            }
            for stage, outcome in result.stages.items()
        },
    }


@app.get("/pipeline/status", tags=["Pipeline"])
def pipeline_status_matrix():
    """Universe-wide (ticker, stage) status matrix. One row per
    (ticker, stage) currently tracked in pipeline_status."""
    from aletheia.pipeline.status_store import PipelineStatusStore
    with PipelineStatusStore() as store:
        rows = store.matrix()
    return [
        {
            "ticker": r.ticker,
            "stage": r.stage,
            "status": r.status.value,
            "fingerprint": r.fingerprint,
            "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
            "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
            "error_message": r.error_message,
            "duration_seconds": r.duration_seconds,
            "rows_processed": r.rows_processed,
        }
        for r in rows
    ]


@app.get("/pipeline/status/{ticker}", tags=["Pipeline"])
def pipeline_status_for_ticker(ticker: str):
    """Per-ticker stage rows. Empty list when the ticker has never
    been run through the orchestrator (returns 200 with [] — not
    404, to match how the matrix endpoint handles absent state)."""
    from aletheia.pipeline.status_store import PipelineStatusStore
    ticker = ticker.upper()
    with PipelineStatusStore() as store:
        rows = store.get_for_ticker(ticker)
    return [
        {
            "ticker": r.ticker,
            "stage": r.stage,
            "status": r.status.value,
            "fingerprint": r.fingerprint,
            "last_run_at": r.last_run_at.isoformat() if r.last_run_at else None,
            "last_success_at": r.last_success_at.isoformat() if r.last_success_at else None,
            "error_message": r.error_message,
            "duration_seconds": r.duration_seconds,
            "rows_processed": r.rows_processed,
        }
        for r in rows
    ]


@app.get("/pipeline/fmp-compare/{ticker}", tags=["Pipeline"])
def pipeline_fmp_compare(ticker: str, n_years: int = 5):
    """Per-FY × per-field XBRL-vs-FMP side-by-side comparison.

    The XBRL side is the cleaned record's resolved fields (extracted
    by tag_resolver in Stage 2). The FMP side comes from the
    on-disk FMP cache files (income / balance / cashflow annual).
    Returns drift in dollars + percent + tier classification.

    Stage 2's typed contract will carry this comparison per-record
    natively in a future iteration (Week 5 follow-up); until then
    the Stage Explorer panel recomputes it from raw sources via this
    endpoint."""
    from aletheia.pipeline._fmp_compare import (
        compare_xbrl_to_fmp, comparison_to_jsonable,
    )
    ticker = ticker.upper()
    result = compare_xbrl_to_fmp(ticker, n_years=n_years)
    return comparison_to_jsonable(result)


@app.post("/pipeline/bust-cache/{ticker}", tags=["Pipeline"])
def pipeline_bust_cache(ticker: str, body: _PipelineBustCacheRequest):
    """Force cache invalidation for one or more stages of a ticker.
    Returns the updated status rows. The next pipeline run for those
    stages will be a real run, not a cache hit."""
    from aletheia.contracts.pipeline import StageStatus, cascade_invalidation_targets
    from aletheia.pipeline.status_store import PipelineStatusStore
    ticker = ticker.upper()
    if not body.stages:
        raise HTTPException(
            status_code=400,
            detail="Provide at least one stage id in `stages`.",
        )
    valid_stages = {
        "stage1_ingest", "stage2_validate",
        "stage3_calculate", "stage4_agents",
    }
    # Accept short forms as a convenience (matches CLI behaviour).
    short_to_full = {
        "stage1": "stage1_ingest", "stage2": "stage2_validate",
        "stage3": "stage3_calculate", "stage4": "stage4_agents",
    }
    resolved = [short_to_full.get(s, s) for s in body.stages]
    unknown = [s for s in resolved if s not in valid_stages]
    if unknown:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown stage ids: {unknown}. Valid: {sorted(valid_stages)}",
        )
    # Cascade-invalidate downstream of each busted stage.
    to_mark = set(resolved)
    for s in list(to_mark):
        to_mark.update(cascade_invalidation_targets(s))

    updated: List[Dict[str, Any]] = []
    with PipelineStatusStore() as store:
        for stage in sorted(to_mark):
            existing = store.get(ticker, stage)
            if existing is None:
                continue  # nothing to bust
            # Re-upsert with stale_due_to_override status so the
            # orchestrator's cache-hit check will miss next run.
            from aletheia.contracts.pipeline import PipelineStatusRow
            store.upsert(PipelineStatusRow(
                ticker=ticker, stage=stage,
                status=StageStatus.STALE_DUE_TO_OVERRIDE,
                fingerprint=existing.fingerprint,
                last_run_at=existing.last_run_at,
                last_success_at=existing.last_success_at,
                error_message=None,
                duration_seconds=existing.duration_seconds,
                rows_processed=existing.rows_processed,
            ))
            row = store.get(ticker, stage)
            updated.append({
                "ticker": row.ticker,
                "stage": row.stage,
                "status": row.status.value,
                "fingerprint": row.fingerprint,
                "last_run_at": row.last_run_at.isoformat() if row.last_run_at else None,
                "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
            })
    return {"updated": updated}


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
