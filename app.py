from __future__ import annotations

from dataclasses import asdict, dataclass
import time
from typing import Any

import requests
import yfinance as yf
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

import os

API_URL = "https://quote-feed.zacks.com/index"
TICKER_PARAMETER = "t"
RANK_FIELD_NAMES = ("zacks_rank", "zacksRank", "rank", "zr_rank")

app = FastAPI(title="Stock Tool API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://amirdmgazzali.com",
        "https://www.amirdmgazzali.com",
        "http://amirdmgazzali.com",
        "http://www.amirdmgazzali.com",
        "http://localhost",
        "http://127.0.0.1",
    ],
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

@dataclass
class StockResult:
    ticker: str
    rank: int | None
    current_price: float | None
    average_target: float | None
    error: str | None = None

    @property
    def target_difference_percent(self) -> float | None:
        if self.current_price is None or self.average_target is None:
            return None
        if self.current_price == 0:
            return None
        return ((self.average_target - self.current_price) / self.current_price) * 100

class StockRequest(BaseModel):
    tickers: list[str] | str
    delay: float = 0.25

def normalize_tickers(values: list[str]) -> list[str]:
    normalized = []
    seen = set()
    for value in values:
        for piece in value.replace(",", " ").split():
            ticker = piece.strip().upper()
            if ticker and ticker not in seen:
                normalized.append(ticker)
                seen.add(ticker)
    return normalized

def find_rank(value: Any) -> int | None:
    if isinstance(value, dict):
        for field in RANK_FIELD_NAMES:
            if field in value:
                try:
                    rank = int(value[field])
                    if 1 <= rank <= 5:
                        return rank
                except (TypeError, ValueError):
                    pass
        for nested_value in value.values():
            rank = find_rank(nested_value)
            if rank is not None:
                return rank
    elif isinstance(value, list):
        for item in value:
            rank = find_rank(item)
            if rank is not None:
                return rank
    return None

def as_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None

def get_zacks_rank(ticker: str, session: requests.Session) -> tuple[int | None, str | None]:
    headers = {"Accept": "application/json", "User-Agent": "personal-zacks-rank-tool/1.0"}
    params = {TICKER_PARAMETER: ticker}
    try:
        response = session.get(API_URL, headers=headers, params=params, timeout=20)
        if response.status_code == 401:
            return None, "Invalid or expired API token"
        if response.status_code == 404:
            return None, "Ticker not found"
        if response.status_code == 429:
            return None, "Zacks rate limit reached"
        response.raise_for_status()
        data = response.json()
        rank = find_rank(data)
        if rank is None:
            return None, "No Zacks Rank field found in response"
        return rank, None
    except requests.Timeout:
        return None, "Zacks request timed out"
    except requests.RequestException as exc:
        return None, f"Zacks error: {exc}"
    except ValueError:
        return None, "Zacks returned invalid JSON"

def get_yahoo_prices(ticker: str) -> tuple[float | None, float | None, str | None]:
    stock = yf.Ticker(ticker)

    current_price = None
    average_target = None
    errors = []

    try:
        current_price = as_float(stock.fast_info["last_price"])
    except Exception as exc:
        errors.append(f"Current price error: {exc}")

    try:
        targets = stock.get_analyst_price_targets()

        if targets:
            average_target = as_float(targets.get("mean"))

            if current_price is None:
                current_price = as_float(targets.get("current"))

        if average_target is None:
            errors.append(f"No analyst target returned. Raw targets: {targets}")

    except Exception as exc:
        errors.append(f"Analyst target error: {exc}")

    if current_price is None and average_target is None:
        return None, None, "; ".join(errors)

    return (
        current_price,
        average_target,
        "; ".join(errors) if errors else None
    )
def get_stock_result(ticker: str, session: requests.Session) -> StockResult:
    rank, zacks_error = get_zacks_rank(ticker, session)
    current_price, average_target, yahoo_error = get_yahoo_prices(ticker)
    errors = [e for e in (zacks_error, yahoo_error) if e]
    return StockResult(
        ticker=ticker,
        rank=rank,
        current_price=current_price,
        average_target=average_target,
        error="; ".join(errors) if errors else None,
    )

def decide(result: StockResult) -> str:
    difference = result.target_difference_percent
    if difference is not None and difference <= -25:
        return "xx"
    if result.rank is None:
        return "N/A"
    if result.rank <= 2:
        if difference is not None and difference < -7.5:
            return "+?"
        return "+"
    if result.rank >= 4:
        return "x"
    if difference is None:
        return "-"
    if difference < -7.5:
        return "x?"
    if difference > 25:
        return "+"
    if difference > 7.5:
        return "-+"
    return "-"

def result_to_dict(result: StockResult) -> dict[str, Any]:
    data = asdict(result)
    data["target_difference_percent"] = result.target_difference_percent
    data["decision"] = (
        "(not enough market research available)"
        if result.rank is None and result.average_target is None
        else decide(result)
    )
    return data

@app.get("/")
def root():
    return {"status": "ok", "message": "Stock Tool API is running", "docs": "/docs"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/stocks")
def stocks(request: StockRequest):
    raw_values = [request.tickers] if isinstance(request.tickers, str) else request.tickers
    tickers = normalize_tickers(raw_values)
    if not tickers:
        raise HTTPException(status_code=400, detail="No tickers supplied.")
    if len(tickers) > 100:
        raise HTTPException(status_code=400, detail="Maximum 100 tickers per request.")

    delay = max(0.0, min(float(request.delay), 2.0))
    results = []
    with requests.Session() as session:
        for index, ticker in enumerate(tickers):
            results.append(get_stock_result(ticker, session))
            if index < len(tickers) - 1 and delay:
                time.sleep(delay)

    return {"count": len(results), "results": [result_to_dict(r) for r in results]}

@app.get("/fmp-test/{ticker}")
def fmp_test(ticker: str):
    api_key = os.getenv("FMP_API_KEY")

    if not api_key:
        raise HTTPException(status_code=500, detail="FMP_API_KEY is not set")

    symbol = ticker.upper()

    quote_response = requests.get(
        "https://financialmodelingprep.com/stable/quote",
        params={
            "symbol": symbol,
            "apikey": api_key
        },
        timeout=20
    )

    target_response = requests.get(
        "https://financialmodelingprep.com/stable/price-target-consensus",
        params={
            "symbol": symbol,
            "apikey": api_key
        },
        timeout=20
    )

    return {
        "ticker": symbol,
        "quote_status": quote_response.status_code,
        "quote": quote_response.json(),
        "target_status": target_response.status_code,
        "target": target_response.json()
    }
