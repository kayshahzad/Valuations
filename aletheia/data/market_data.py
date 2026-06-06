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
        
    try:
        t = yf.Ticker("^IRX")
        hist = t.history(period="1d")
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
