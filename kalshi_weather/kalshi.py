"""Minimal Kalshi REST client (public market data only, no auth needed)."""
from __future__ import annotations

import hashlib
import json
import os
import re
import time
from dataclasses import dataclass
from datetime import date

import requests

from .config import KALSHI_PROD

MONTHS = {m: i for i, m in enumerate(
    ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}


def _price(d: dict, name: str) -> float | None:
    """Read a price in dollars (0-1) from either `<name>_dollars` or cents `<name>`."""
    v = d.get(f"{name}_dollars")
    if v not in (None, ""):
        p = float(v)
    elif d.get(name) is not None:
        p = float(d[name]) / 100.0
    else:
        return None
    return p if 0.0 < p < 1.0 else None


def _candle_price(d: dict | None, historical: bool) -> float | None:
    """Candle close in dollars. The historical endpoint gives dollars under plain `close`."""
    d = d or {}
    if historical and d.get("close_dollars") in (None, "") and d.get("close") not in (None, ""):
        p = float(d["close"])
        return p if 0.0 < p < 1.0 else None
    return _price(d, "close")


@dataclass
class Bracket:
    ticker: str
    event_ticker: str
    strike_type: str | None
    floor: float | None
    cap: float | None
    result: str | None
    close_time: str | None
    yes_bid: float | None
    yes_ask: float | None
    title: str = ""

    @property
    def no_ask(self) -> float | None:
        return None if self.yes_bid is None else round(1.0 - self.yes_bid, 4)


def parse_market(m: dict) -> Bracket:
    def num(k):
        v = m.get(k)
        return None if v is None else float(v)

    res = (m.get("result") or "").lower()
    return Bracket(
        ticker=m["ticker"],
        event_ticker=m.get("event_ticker", ""),
        strike_type=m.get("strike_type"),
        floor=num("floor_strike"),
        cap=num("cap_strike"),
        result=res if res in ("yes", "no") else None,
        close_time=m.get("close_time"),
        yes_bid=_price(m, "yes_bid"),
        yes_ask=_price(m, "yes_ask"),
        title=m.get("yes_sub_title") or m.get("subtitle") or m.get("title") or "",
    )


def event_date(event_ticker: str) -> date | None:
    """KXHIGHNY-25OCT03 -> 2025-10-03."""
    mt = re.search(r"-(\d{2})([A-Z]{3})(\d{2})$", event_ticker or "")
    if not mt or mt.group(2) not in MONTHS:
        return None
    yy, mon, dd = mt.groups()
    try:
        return date(2000 + int(yy), MONTHS[mon], int(dd))
    except ValueError:
        return None


def book_quotes(resp: dict) -> dict:
    """Turn an orderbook response into best YES bid/ask (dollars) and size.

    Kalshi books only list bids. A YES ask is the complement of the best NO bid.
    """
    ob = resp.get("orderbook_fp") or resp.get("orderbook") or {}

    def levels(side):
        for key, scale in ((f"{side}_dollars", 1.0), (side, 0.01)):
            lv = ob.get(key)
            if lv:
                return [(float(p) * scale, float(q)) for p, q in lv]
        return []

    yes, no = levels("yes"), levels("no")
    best_yes = max(yes, default=None)
    best_no = max(no, default=None)
    return {
        "yes_bid": round(best_yes[0], 4) if best_yes else None,
        "yes_bid_qty": best_yes[1] if best_yes else 0,
        "yes_ask": round(1.0 - best_no[0], 4) if best_no else None,
        "yes_ask_qty": best_no[1] if best_no else 0,
    }


class KalshiClient:
    def __init__(self, base_url: str = KALSHI_PROD, session=None,
                 cache_dir: str | None = None, min_interval: float = 0.12):
        self.base_url = base_url.rstrip("/")
        self.session = session or requests.Session()
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self._last = 0.0
        self._cutoff = None
        self.throttled = 0
        if cache_dir:
            os.makedirs(cache_dir, exist_ok=True)

    def _get(self, path: str, params: dict | None = None, cache: bool = False,
             allow_404: bool = False) -> dict | None:
        url = self.base_url + path
        cpath = None
        if cache and self.cache_dir:
            key = hashlib.sha1((url + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()
            cpath = os.path.join(self.cache_dir, key + ".json")
            if os.path.exists(cpath):
                with open(cpath) as f:
                    data = json.load(f)
                return None if data == {"_not_found": True} else data
        for attempt in range(10):
            wait = self.min_interval - (time.time() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.time()
            try:
                r = self.session.get(url, params=params, timeout=20)
            except (requests.ConnectionError, requests.Timeout):
                self.throttled += 1
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code == 429 or r.status_code >= 500:
                self.throttled += 1
                time.sleep(min(2 ** attempt, 30))
                continue
            if r.status_code == 404 and allow_404:
                data = {"_not_found": True}
                break
            r.raise_for_status()
            data = r.json()
            break
        else:
            raise RuntimeError(f"Kalshi API kept failing: {url}")
        if cpath:
            with open(cpath, "w") as f:
                json.dump(data, f)
        return None if data == {"_not_found": True} else data

    def markets(self, series_ticker: str, status: str | None = None,
                min_close_ts: int | None = None, max_close_ts: int | None = None,
                cache: bool = False, path: str = "/markets") -> list[Bracket]:
        out, cursor = [], None
        while True:
            params = {"series_ticker": series_ticker, "limit": 1000}
            if status:
                params["status"] = status
            if min_close_ts:
                params["min_close_ts"] = min_close_ts
            if max_close_ts:
                params["max_close_ts"] = max_close_ts
            if cursor:
                params["cursor"] = cursor
            data = self._get(path, params, cache=cache)
            out += [parse_market(m) for m in data.get("markets", [])]
            cursor = data.get("cursor")
            if not cursor or not data.get("markets"):
                return out

    def settled_markets(self, series_ticker: str, min_close_ts: int | None = None,
                        max_close_ts: int | None = None, cache: bool = False) -> list[Bracket]:
        """Settled markets from both the live and the historical endpoints.

        Kalshi moves markets settled before its historical cutoff (a few months
        back) to /historical/markets; /markets no longer returns them.
        """
        live = self.markets(series_ticker, status="settled", min_close_ts=min_close_ts,
                            max_close_ts=max_close_ts, cache=cache)
        old = self.markets(series_ticker, min_close_ts=min_close_ts, max_close_ts=max_close_ts,
                           cache=cache, path="/historical/markets")
        seen = {b.ticker for b in live}
        return live + [b for b in old if b.ticker not in seen]

    def market(self, ticker: str) -> Bracket:
        return parse_market(self._get(f"/markets/{ticker}")["market"])

    def orderbook(self, ticker: str) -> dict:
        return book_quotes(self._get(f"/markets/{ticker}/orderbook"))

    def historical_cutoff(self) -> str | None:
        """ISO time before which settled markets live only under /historical."""
        if self._cutoff is None:
            self._cutoff = (self._get("/historical/cutoff") or {}).get("market_settled_ts") or ""
        return self._cutoff or None

    def candles(self, series: str, ticker: str, start_ts: int, end_ts: int,
                period: int = 60, cache: bool = True, close_time: str | None = None) -> list[dict]:
        """Candles, from the historical endpoint when the market is past Kalshi's cutoff.

        Pass `close_time` to skip the live endpoint for markets known to be historical.
        """
        params = {"start_ts": start_ts, "end_ts": end_ts, "period_interval": period}
        cutoff = self.historical_cutoff() if close_time else None
        if cutoff and close_time < cutoff:
            data = None
        else:
            data = self._get(f"/series/{series}/markets/{ticker}/candlesticks", params,
                             cache=cache, allow_404=True)
        historical = data is None   # markets past Kalshi's historical cutoff live elsewhere
        if historical:
            data = self._get(f"/historical/markets/{ticker}/candlesticks", params,
                             cache=cache, allow_404=True) or {}
        out = []
        for c in data.get("candlesticks", []):
            out.append({
                "ts": int(c.get("end_period_ts", 0)),
                "yes_bid": _candle_price(c.get("yes_bid"), historical),
                "yes_ask": _candle_price(c.get("yes_ask"), historical),
            })
        return sorted(out, key=lambda x: x["ts"])

    def series_list(self, category: str | None = None) -> list[dict]:
        params = {"category": category} if category else None
        return self._get("/series", params).get("series", []) or []
