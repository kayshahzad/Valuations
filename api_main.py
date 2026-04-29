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

    return {
        "ticker":              ticker,
        "generated_at":        report.get("generated_at"),
        # Conviction
        "conviction":          g(thesis, "conviction_score"),
        "moat":                g(er.get("moat", {}), "score"),
        # DCF scenarios
        "bear_iv":             g(dcf3.get("bear", {}), "intrinsic_per_share"),
        "base_iv":             g(dcf3.get("base", {}), "intrinsic_per_share"),
        "bull_iv":             g(dcf3.get("bull", {}), "intrinsic_per_share"),
        "bear_mos":            g(dcf3.get("bear", {}), "margin_of_safety"),
        "base_mos":            g(dcf3.get("base", {}), "margin_of_safety"),
        "bull_mos":            g(dcf3.get("bull", {}), "margin_of_safety"),
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


@app.get("/universe", response_model=UniverseResponse, tags=["Universe"])
def get_universe():
    """
    Full universe summary ranked by conviction → margin of safety.
    Returns all tickers that have a report in the serving directory.
    """
    summaries = []
    available = [
        p.stem.replace("_report", "")
        for p in REPORT_DIR.glob("*_report.json")
    ]
    for ticker in sorted(available):
        try:
            report = _load_report(ticker)
            summaries.append(_extract_summary(ticker, report))
        except HTTPException:
            pass   # skip missing tickers

    # Sort: conviction desc, base_mos desc
    summaries.sort(
        key=lambda x: (
            x.get("conviction") or -99,
            x.get("base_mos") or -99,
        ),
        reverse=True,
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
        engine = ScreeningEngine(verbose=False)
        card = engine.score(ticker.upper())
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
        engine = ScreeningEngine(verbose=False)
        available = [t for t in UNIVERSE if (REPORT_DIR / f"{t}_report.json").exists()]
        cards = engine.score_universe(available)

        result = {}
        for ticker, card in cards.items():
            result[ticker] = {
                "passes":   card.passes,
                "flags":    card.flags,
                "fails":    card.fails,
                "available":card.available,
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
