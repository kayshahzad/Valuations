"""
aletheia/data/market_data.py

Lightweight wrapper around yfinance to aggressively cache market data 
and reduce HTTP calls during pipeline execution.
"""
import time
import yfinance as yf
import numpy as np
from typing import Dict, Any

class MarketDataCache:
    _cache: Dict[str, Dict[str, Any]] = {}
    
    @classmethod
    def get_info(cls, ticker: str) -> dict:
        ticker_upper = ticker.upper()
        if ticker_upper in cls._cache:
            return cls._cache[ticker_upper]
            
        max_retries = 3
        for attempt in range(max_retries):
            try:
                t = yf.Ticker(ticker_upper)
                info = t.fast_info
                
                data = {
                    "last_price": float(info.last_price) if info.last_price else 0.0,
                    "market_cap": float(info.market_cap) if info.market_cap else 0.0,
                    "shares": float(info.shares) if getattr(info, 'shares', None) else 0.0,
                }
                cls._cache[ticker_upper] = data
                return data
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"  ⚠ MarketData: Failed to fetch {ticker_upper} after {max_retries} attempts: {e}")
                    cls._cache[ticker_upper] = {"last_price": 0.0, "market_cap": 0.0, "shares": 0.0}
                    return cls._cache[ticker_upper]
                time.sleep(0.5)
        return {"last_price": 0.0, "market_cap": 0.0, "shares": 0.0}

def get_fx_to_usd(currency: str) -> float:
    """USD per 1 unit of ``currency`` (e.g. DKK -> ~0.145). USD -> 1.0.

    Fetched from yfinance (``{CUR}USD=X``) and cached for the process.
    Returns 1.0 on failure — callers treat a 1.0 rate on a non-USD filer as
    "conversion unavailable" and must surface it rather than silently
    mixing currencies.
    """
    cur = (currency or "USD").upper()
    if cur == "USD":
        return 1.0
    key = f"FX:{cur}"
    cached = MarketDataCache._cache.get(key)
    if cached is not None:
        return cached.get("rate", 1.0)
    rate = None
    for _ in range(3):
        try:
            info = yf.Ticker(f"{cur}USD=X").fast_info
            px = float(info.last_price) if info.last_price else None
            if px and px > 0:
                rate = px
                break
        except Exception:
            time.sleep(0.5)
    if rate is None:
        print(f"  ⚠ FX: could not fetch {cur}USD rate; using 1.0 (NO conversion)")
        rate = 1.0
    MarketDataCache._cache[key] = {"rate": rate}
    return rate


def get_current_price(ticker: str) -> float:
    return MarketDataCache.get_info(ticker).get("last_price", 0.0)

def get_market_cap(ticker: str) -> float:
    return MarketDataCache.get_info(ticker).get("market_cap", 0.0)
    
def get_shares_outstanding(ticker: str) -> float:
    return MarketDataCache.get_info(ticker).get("shares", 0.0)
    
_RISK_FREE_RATE = None

def get_risk_free_rate() -> float:
    global _RISK_FREE_RATE
    if _RISK_FREE_RATE is not None:
        return _RISK_FREE_RATE
        
    # 10-year Treasury (^TNX), not the 13-week bill (^IRX): the risk-free rate
    # should match the horizon of a perpetuity DCF, and it keeps CAPM consistent
    # with Damodaran's implied ERP (computed against the 10-year). The 4.5%
    # fallback was already 10-year-ish.
    try:
        t = yf.Ticker("^TNX")
        hist = t.history(period="5d")
        if not hist.empty:
            val = hist["Close"].iloc[-1] / 100.0
            _RISK_FREE_RATE = val
            return val
    except Exception:
        pass
    _RISK_FREE_RATE = 0.045
    return 0.045

_BETA_CACHE = {}

def get_beta(ticker: str, period="5y", interval="1wk") -> float:
    ticker_upper = ticker.upper()
    if ticker_upper in _BETA_CACHE:
        return _BETA_CACHE[ticker_upper]
        
    try:
        stock = yf.Ticker(ticker_upper)
        market = yf.Ticker("^GSPC")
        s_hist = stock.history(period=period, interval=interval)["Close"].pct_change().dropna()
        m_hist = market.history(period=period, interval=interval)["Close"].pct_change().dropna()
        
        common = s_hist.index.intersection(m_hist.index)
        if len(common) > 30:
            cov = np.cov(s_hist[common], m_hist[common])[0][1]
            var = np.var(m_hist[common])
            beta = cov / var
            _BETA_CACHE[ticker_upper] = float(beta)
            return float(beta)
    except Exception:
        pass
        
    _BETA_CACHE[ticker_upper] = 1.0
    return 1.0


# ── β-reference diagnostic (spec §7 / Build 1) ────────────────────────────────
# A firm's β against the broad domestic index can badly mis-state its true
# systematic risk when its marginal investor prices it against a sector/global
# benchmark (the Nokia / JSE-gold-mine lesson). We compute β AND R² vs both
# ^GSPC and a sector ETF so the discount-rate section can FLAG the discrepancy.
# This is a diagnostic only — headline WACC keeps using the ^GSPC β (decision #3).
_SECTOR_BENCHMARK_ETF = {
    "Technology": "XLK", "Information Technology": "XLK", "Software": "XLK",
    "Semiconductors": "XLK", "Real Estate": "VNQ",
    "Healthcare": "XLV", "Health Care": "XLV",
    "Financials": "XLF", "Financial Services": "XLF",
    "Energy": "XLE", "Consumer Discretionary": "XLY", "Consumer Cyclical": "XLY",
    "Consumer Staples": "XLP", "Consumer Defensive": "XLP",
    "Industrials": "XLI", "Utilities": "XLU",
    "Materials": "XLB", "Basic Materials": "XLB",
    "Communication Services": "XLC",
}
_GLOBAL_BENCHMARK = "ACWI"   # fallback for sectors without a clean ETF proxy
_BETA_DIAG_CACHE = {}


def _regress_beta(s_ret, m_ret):
    """OLS β and R² of stock returns on benchmark returns. Returns
    (beta, r2) or (None, None) when there isn't enough overlap."""
    common = s_ret.index.intersection(m_ret.index)
    if len(common) <= 30:
        return None, None
    s = s_ret[common]
    m = m_ret[common]
    cov = np.cov(s, m)[0][1]
    var = np.var(m)
    if var <= 0:
        return None, None
    beta = cov / var
    sd_s, sd_m = np.std(s), np.std(m)
    r2 = None
    if sd_s > 0 and sd_m > 0:
        r = cov / (sd_s * sd_m)
        r2 = float(r * r)
    return float(beta), r2


_DD_CACHE = {}


def get_drawdown_correlation(ticker: str, benchmark: str = "SPY",
                            period: str = "5y") -> dict:
    """Max drawdown + return-correlation vs a benchmark (SaaS overlay /
    drawdown-history gap). Cached per (ticker, benchmark). Never raises.

    Caveat the caller should surface: for a large-cap, SPY correlation is
    near 1 and tells you little — correlation vs the existing book (portfolio
    overlap) is the more useful cut, deferred to v2."""
    key = (ticker.upper(), benchmark.upper())
    if key in _DD_CACHE:
        return _DD_CACHE[key]
    out = {"max_drawdown": None, "corr_benchmark": None,
           "benchmark": benchmark, "period": period}
    try:
        s = yf.Ticker(ticker.upper()).history(period=period)["Close"].dropna()
        if len(s) >= 60:
            roll_max = s.cummax()
            out["max_drawdown"] = float((s / roll_max - 1.0).min())
            b = yf.Ticker(benchmark).history(period=period)["Close"].pct_change().dropna()
            sr = s.pct_change().dropna()
            common = sr.index.intersection(b.index)
            if len(common) > 30:
                out["corr_benchmark"] = float(np.corrcoef(sr[common], b[common])[0][1])
    except Exception:
        pass
    _DD_CACHE[key] = out
    return out


def get_beta_diagnostics(ticker: str, sector: str = None,
                         period="5y", interval="1wk") -> dict:
    """β + R² vs ^GSPC and vs the sector-benchmark ETF for the marginal
    investor. Cached per (ticker, sector). Never raises — degrades to Nones."""
    key = (ticker.upper(), (sector or "").strip())
    if key in _BETA_DIAG_CACHE:
        return _BETA_DIAG_CACHE[key]
    bench = _SECTOR_BENCHMARK_ETF.get((sector or "").strip(), _GLOBAL_BENCHMARK)
    out = {"beta_gspc": None, "r2_gspc": None, "beta_sector": None,
           "r2_sector": None, "sector_benchmark": bench}
    try:
        s_hist = yf.Ticker(ticker.upper()).history(
            period=period, interval=interval)["Close"].pct_change().dropna()
        gspc = yf.Ticker("^GSPC").history(
            period=period, interval=interval)["Close"].pct_change().dropna()
        sect = yf.Ticker(bench).history(
            period=period, interval=interval)["Close"].pct_change().dropna()
        bg, rg = _regress_beta(s_hist, gspc)
        bs, rs = _regress_beta(s_hist, sect)
        out.update({"beta_gspc": bg, "r2_gspc": rg,
                    "beta_sector": bs, "r2_sector": rs})
    except Exception:
        pass
    _BETA_DIAG_CACHE[key] = out
    return out
