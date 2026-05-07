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
import math
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
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


def _load_report(ticker: str) -> dict:
    path = REPORT_DIR / f"{ticker.upper()}_report.json"
    if not path.exists():
        raise HTTPException(
            status_code=404,
            detail=f"No report found for {ticker}. Run: python main.py --ticker {ticker}"
        )
    return json.loads(path.read_text())


def _extract_summary(ticker: str, report: dict) -> dict:
    """Flatten a full report into a summary row for the universe table."""
    p2     = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {})
    ft     = report.get("2_financial_translation", {})
    cf     = ft.get("clean_financials", {}) or {}
    rat    = ft.get("ratios", {}) or {}
    er     = report.get("1_economic_reality", {})
    thesis = report.get("4_valuation_synthesis", {}).get("investment_thesis", {})
    dcf3   = p2.get("three_scenario_dcf", {})
    rdcf   = p2.get("reverse_dcf", {})
    md     = p2.get("multiple_decomposition", {})

    def g(d, *keys, fb=None):
        for k in keys:
            v = d.get(k)
            if v is not None and not (isinstance(v, float) and math.isnan(v)):
                return v
        return fb

    implied = g(rdcf, "implied_cagr_10y")
    hist    = g(rdcf, "historical_cagr")
    ratio   = implied / hist if implied is not None and hist and hist > 0 else None

    # The lead agent's `phase2_valuation.three_scenario_dcf` sometimes
    # serializes `ev` but leaves `intrinsic_per_share` and `margin_of_safety`
    # as None (NVDA latest run is the canonical example). When that happens
    # we reconstruct IPS from the EV using the accounting identity:
    #     IPS = (EV − net_debt) / shares_diluted
    # The required pieces (net_debt, shares_diluted, current_price) are
    # always serialized in `agent_scenarios[0]` even when the main scenarios
    # don't have IPS.
    agent_scenarios = report.get("4_valuation_synthesis", {}).get("agent_scenarios") or []
    fallback_calc = agent_scenarios[0].get("dcf", {}) if agent_scenarios else {}
    shares_diluted = fallback_calc.get("shares_diluted")
    net_debt       = fallback_calc.get("net_debt")
    current_price  = fallback_calc.get("current_price")

    def _ips_for(scenario_key: str) -> Optional[float]:
        """Return IPS from the scenario, or compute from EV if missing."""
        s = dcf3.get(scenario_key, {}) or {}
        ips = s.get("intrinsic_per_share")
        if ips is not None:
            return float(ips)
        ev = s.get("ev")
        if ev and shares_diluted:
            equity_value = ev - (net_debt or 0)
            return float(equity_value) / float(shares_diluted)
        return None

    def _mos_for(scenario_key: str) -> Optional[float]:
        s = dcf3.get(scenario_key, {}) or {}
        mos = s.get("margin_of_safety")
        if mos is not None:
            return float(mos)
        ips = _ips_for(scenario_key)
        if ips and current_price:
            return (ips - current_price) / current_price
        return None

    return {
        "ticker":              ticker,
        "generated_at":        report.get("generated_at"),
        # Conviction
        "conviction":          g(thesis, "conviction_score"),
        "moat":                g(er.get("moat", {}), "score"),
        # DCF scenarios — reconstructs IPS from EV when the agent serialized
        # it as None (see _ips_for / _mos_for above).
        "bear_iv":             _ips_for("bear"),
        "base_iv":             _ips_for("base"),
        "bull_iv":             _ips_for("bull"),
        "bear_mos":            _mos_for("bear"),
        "base_mos":            _mos_for("base"),
        "bull_mos":            _mos_for("bull"),
        # Reverse DCF
        "implied_cagr":        implied,
        "historical_cagr":     hist,
        "implied_hist_ratio":  ratio,
        "rdcf_signal":         g(rdcf, "signal"),
        "rdcf_reasons":        rdcf.get("reasons", []),
        # Multiple decomposition
        "ev_ebitda":           g(md, "market_ev_ebitda"),
        "justified_ev_ebitda": g(md, "justified_ev_ebitda"),
        "multiple_premium":    g(md, "premium_pct"),
        "multiple_signal":     g(md, "signal"),
        "roic":                g(rat, "roic"),
        "wacc":                g(p2, "wacc"),
        "beta":                g(p2, "beta"),
        "risk_free_rate":      g(p2, "risk_free_rate"),
        "roic_wacc_spread":    g(md, "roic_wacc_spread"),
        "value_creation":      g(md, "value_creation"),
        # Fundamentals
        "revenue_bn":          g(cf, "revenue_bn"),
        "ebitda_bn":           g(cf, "ebitda_bn"),
        "fcf_bn":              g(cf, "fcf_bn"),
        "fcf_margin":          g(rat, "fcf_margin_pct"),
        "gross_margin":        g(rat, "gross_margin_pct"),
        "ebit_margin":         g(rat, "ebit_margin_pct"),
        "data_quality":        g(cf, "data_quality"),
        # Value chain
        "strategic_leverage":  g(er.get("value_chain", {}), "strategic_leverage"),
        "power_ratio":         g(er.get("value_chain", {}), "power_ratio"),
        "upstream_leak":       er.get("value_chain", {}).get("upstream_leak"),
        # Industry
        "cyclicality_z_score": g(er.get("industry_structure", {}), "cyclicality_z_score"),
        "is_cyclical_peak":    er.get("industry_structure", {}).get("is_peak"),
    }


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
    Build a partial TickerSummary from the cleaned DB + DCFEngine output, for
    tickers that have been ingested but never had the agents (LangGraph) run.

    Populates whatever the calc layer can produce without the LLM:
      - Fundamentals (Revenue, EBITDA, FCF, ROIC) — always from DB
      - Historical revenue CAGR — always from DB (5y compounded)
      - DCF outputs (Base IV, MoS, EV/EBITDA, multiple decomp) — when
        DCFEngine succeeds
      - Implied CAGR via reverse-DCF math — when DCFEngine succeeds

    DCFEngine fails on filers it doesn't understand (e.g., AXP — no
    `OperatingIncome` tag because card networks file pretax income).
    Those tickers still get raw fundamentals from the DB so the row isn't
    entirely blank.

    Agent-driven fields (conviction, moat, pillar_total, narrative) stay
    None. Returns None only if the ticker has no DB rows at all.
    """
    try:
        from aletheia.utils.calc_input_builder import make_calc_input
        from aletheia.tools.dcf_engine import DCFEngine
        calc = make_calc_input(ticker)
        if calc.df.empty:
            return None
    except Exception:
        return None

    df = calc.df.sort_values("fiscal_year")

    # ── Always-available: raw fundamentals from the DB ──────────────────
    # DCFEngine sometimes fails on schema-mismatch filers (AXP). We pull
    # revenue/ebitda/fcf directly from the latest cleaned row as a baseline
    # so the table cell is informative even when DCF can't run.
    latest = df.iloc[-1]
    raw_revenue = (latest.get("clean_Revenue") or 0) or None
    raw_fcf     = (latest.get("derived_FCF") or latest.get("clean_FCF") or 0) or None

    # Reconstruct EBITDA: prefer derived; if not present try op_inc + D&A
    raw_ebitda = (latest.get("derived_EBITDA") or latest.get("clean_EBITDA") or 0) or None
    raw_roic   = (latest.get("derived_ROIC") or 0) or None

    # ── Historical CAGR — last 5 years revenue ──────────────────────────
    hist_cagr = None
    if len(df) >= 2:
        revs = df["clean_Revenue"].tolist()[-5:]
        revs = [float(r) for r in revs if r and r > 0]
        if len(revs) >= 2:
            n = len(revs) - 1
            hist_cagr = (revs[-1] / revs[0]) ** (1.0 / n) - 1.0

    # ── DCF-dependent fields ────────────────────────────────────────────
    try:
        result = DCFEngine(verbose=False).run(calc)
    except Exception:
        result = None

    base = getattr(result, "base", None) if result else None
    base_iv = None
    base_mos = None
    if base is not None and result is not None:
        base_iv = result.intrinsic_per_share(base.enterprise_value, result.net_debt)
        if base_iv and result.current_price:
            base_mos = (base_iv - result.current_price) / result.current_price

    revenue = getattr(result, "revenue", None) if result else raw_revenue
    ebitda  = getattr(result, "ebitda",  None) if result else raw_ebitda
    fcf     = getattr(result, "fcf",     None) if result else raw_fcf
    roic    = getattr(result, "roic",    None) if result else raw_roic
    wacc    = getattr(result, "wacc_base", None) if result else None

    # Multiple decomp: market vs justified EV/EBITDA from the base scenario
    market_ev_ebitda = float(base.implied_ev_ebitda) if base else None
    just_ev_ebitda   = float(base.justified_ev_ebitda) if base else None
    premium = (
        market_ev_ebitda / just_ev_ebitda - 1.0
        if (market_ev_ebitda and just_ev_ebitda) else None
    )
    if premium is None:
        signal = None
    elif premium < 0:
        signal = "undervalued"
    elif premium < 0.20:
        signal = "fairly_valued"
    elif premium < 0.50:
        signal = "premium"
    else:
        signal = "high_premium"

    # ── Implied CAGR via reverse DCF (when DCFEngine succeeded) ─────────
    implied_cagr = None
    if base is not None and result is not None and result.revenue and result.wacc_base:
        try:
            from aletheia.tools.testable import pure_reverse_dcf_math
            current_ev = (result.market_cap or 0) + (result.net_debt or 0)
            ebit_margin = (result.ebit / result.revenue) if (result.ebit and result.revenue) else 0.20
            implied_cagr = pure_reverse_dcf_math(
                current_ev=current_ev,
                base_revenue=result.revenue,
                ebit_margin=ebit_margin,
                wacc=result.wacc_base,
                tax_rate=0.21,
                capex_pct=0.05,
                da_pct=0.05,
                nwc_pct=0.02,
            )
        except Exception:
            implied_cagr = None

    impl_hist_ratio = (
        implied_cagr / hist_cagr
        if (implied_cagr is not None and hist_cagr and hist_cagr > 0)
        else None
    )

    return {
        "ticker":              ticker,
        "generated_at":        None,
        "conviction":          None,
        "moat":                None,
        "base_iv":             float(base_iv) if base_iv else None,
        "bear_iv":             None,
        "bull_iv":             None,
        "base_mos":            float(base_mos) if base_mos is not None else None,
        "implied_cagr":        float(implied_cagr) if implied_cagr is not None else None,
        "historical_cagr":     float(hist_cagr) if hist_cagr is not None else None,
        "implied_hist_ratio":  float(impl_hist_ratio) if impl_hist_ratio is not None else None,
        "rdcf_signal":         None,
        "ev_ebitda":           market_ev_ebitda,
        "justified_ev_ebitda": just_ev_ebitda,
        "multiple_premium":    premium,
        "multiple_signal":     signal,
        "roic":                float(roic) if roic else None,
        "wacc":                float(wacc) if wacc else None,
        "value_creation":      None,
        "revenue_bn":          float(revenue / 1e9) if revenue else None,
        "ebitda_bn":           float(ebitda / 1e9) if ebitda else None,
        "fcf_bn":              float(fcf / 1e9) if fcf else None,
        "fcf_margin":          None,
        "gross_margin":        None,
        "data_quality":        float(latest.get("overall_quality_score") or 0) or None,
        "agents_status":       "pending",
        "last_agent_run":      None,
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

    for ticker in universe:
        report_path = REPORT_DIR / f"{ticker}_report.json"
        if report_path.exists():
            try:
                report = _load_report(ticker)
                s = _extract_summary(ticker, report)
                s["agents_status"] = "ready"
                s["last_agent_run"] = datetime.fromtimestamp(
                    report_path.stat().st_mtime
                ).isoformat()
                summaries.append(s)
                continue
            except HTTPException:
                pass  # fall through to calc-only branch
        # No report — try calc-only summary
        s = _calc_only_summary(ticker)
        if s is not None:
            summaries.append(s)
            continue
        # Last resort — placeholder
        summaries.append(_placeholder_summary(ticker))

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


@app.get("/ticker/{ticker}", tags=["Ticker"])
def get_ticker(ticker: str) -> dict:
    """
    Full report for one ticker — all four sections of the investment memo.
    Returns the raw JSON report structure.
    """
    return _load_report(ticker.upper())


@app.get("/ticker/{ticker}/summary", response_model=TickerSummary, tags=["Ticker"])
def get_ticker_summary(ticker: str):
    """Flattened summary for one ticker — same schema as universe rows."""
    report = _load_report(ticker.upper())
    summary = _extract_summary(ticker.upper(), report)
    return TickerSummary(**summary)


@app.get("/ticker/{ticker}/dcf", response_model=DCFResponse, tags=["Ticker"])
def get_ticker_dcf(ticker: str):
    """DCF scenarios, reverse DCF, and multiple decomposition for one ticker."""
    report = _load_report(ticker.upper())
    p2 = report.get("4_valuation_synthesis", {}).get("phase2_valuation", {})
    dcf3 = p2.get("three_scenario_dcf", {})

    def scenario(key) -> Optional[DCFScenario]:
        d = dcf3.get(key, {})
        if not d:
            return None
        return DCFScenario(
            intrinsic_per_share=_safe(d.get("intrinsic_per_share")),
            margin_of_safety=_safe(d.get("margin_of_safety")),
            ev=_safe(d.get("ev")),
        )

    return DCFResponse(
        ticker=ticker.upper(),
        wacc=_safe(p2.get("wacc")),
        beta=_safe(p2.get("beta")),
        risk_free_rate=_safe(p2.get("risk_free_rate")),
        bear=scenario("bear"),
        base=scenario("base"),
        bull=scenario("bull"),
        reverse_dcf=p2.get("reverse_dcf"),
        multiple_decomposition=p2.get("multiple_decomposition"),
    )


@app.get("/ticker/{ticker}/fundamentals", response_model=FundamentalsResponse, tags=["Ticker"])
def get_ticker_fundamentals(ticker: str):
    """
    Phase 1 cleaned fundamentals for one ticker.
    Reads from the financial_translation section of the report.
    Falls back to DuckDB if report is missing that section.
    """
    report = _load_report(ticker.upper())
    ft  = report.get("2_financial_translation", {})
    cf  = ft.get("clean_financials", {}) or {}
    rat = ft.get("ratios", {}) or {}
    qf  = ft.get("quality_screens", {}) or {}
    clf = ft.get("cleaning_flags", {}) or {}

    # Try DB for any missing fields
    try:
        from aletheia.data.database import InvestmentDatabase
        import numpy as np
        db = InvestmentDatabase(verbose=False)
        df = db.get_latest(ticker.upper())
        db.close()
        if not df.empty:
            row = df[df["fiscal_year"] == df["fiscal_year"].max()].iloc[0]
            def gdb(col, fb=None):
                v = row.get(col)
                return float(v) if v is not None and not (isinstance(v, float) and np.isnan(v)) else fb
            fy = int(df["fiscal_year"].max())
            domain_scores = {
                k.replace("domain_score_", ""): gdb(k)
                for k in df.columns if k.startswith("domain_score_") and gdb(k) is not None
            }
        else:
            fy, domain_scores = 0, {}
    except Exception:
        fy, domain_scores = 0, {}

    return FundamentalsResponse(
        ticker=ticker.upper(),
        fiscal_year=fy or cf.get("fiscal_year"),
        revenue_bn=cf.get("revenue_bn"),
        ebitda_bn=cf.get("ebitda_bn"),
        fcf_bn=cf.get("fcf_bn"),
        fcf_margin=rat.get("fcf_margin_pct"),
        gross_margin=rat.get("gross_margin_pct"),
        ebit_margin=rat.get("ebit_margin_pct"),
        roic=rat.get("roic"),
        roe=rat.get("roe"),
        sbc_pct_fcf=rat.get("sbc_pct_fcf"),
        cash_tax_rate=rat.get("cash_tax_rate"),
        share_dilution_pct=rat.get("share_dilution_pct"),
        warning_count=clf.get("warning_count"),
        error_count=clf.get("error_count"),
        data_quality=cf.get("data_quality"),
        domain_scores=domain_scores,
        cleaning_flags=clf,
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
def get_ticker_narrative(ticker: str) -> dict:
    """
    Investment thesis narrative, conviction score, and constitution checks.
    Returns the lead agent output directly.
    """
    report = _load_report(ticker.upper())
    synthesis = report.get("4_valuation_synthesis", {})
    return {
        "ticker":    ticker.upper(),
        "conviction": synthesis.get("investment_thesis", {}).get("conviction_score"),
        "narrative":  synthesis.get("investment_thesis", {}).get("narrative"),
        "constitution_checks": synthesis.get("investment_thesis", {}).get("constitution_checks", []),
        "margin_of_safety":    synthesis.get("investment_thesis", {}).get("margin_of_safety"),
        "generated_at": report.get("generated_at"),
    }


@app.get("/ticker/{ticker}/economic_reality", tags=["Ticker"])
def get_economic_reality(ticker: str) -> dict:
    """Moat, value chain, industry structure from the agent layer."""
    report = _load_report(ticker.upper())
    return {
        "ticker": ticker.upper(),
        **report.get("1_economic_reality", {}),
    }


@app.get("/ticker/{ticker}/capital_structure", tags=["Ticker"])
def get_capital_structure(ticker: str) -> dict:
    """WACC, capital stack, and risk factors from the Strategist agent."""
    report = _load_report(ticker.upper())
    return {
        "ticker": ticker.upper(),
        **report.get("3_capital_structure_risk", {}),
    }


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
    """Save a new thesis version and generate PDF."""
    from aletheia.tools.thesis_builder import save_thesis_from_api, export_pdf
    try:
        thesis = save_thesis_from_api(ticker.upper(), data.model_dump())
        pdf_path = export_pdf(thesis)
        return {
            "status": "success",
            "version": thesis["version"],
            "pdf_path": str(pdf_path)
        }
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


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
