"""Polymarket daily temperature markets: the longshot test on a second venue.

Polymarket lists ~100 daily high/low temperature events a day across ~45 cities (US cities
settle on different stations than Kalshi, so cross-venue gaps are not arbitrage). Its price
history (clob prices-history) gives one price per hour, not bid/ask, so the backtest charges
an assumed slippage on top of that price. Polymarket charges no fee on these markets.

Rule (fixed before testing): 24h before the event ends, buy NO on every bracket whose YES
price is between 0.5c and 4c, paying 1 - price + slippage.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from .kalshi import KalshiClient

GAMMA = "https://gamma-api.polymarket.com"
CLOB = "https://clob.polymarket.com"


def clients(cache_dir: str | None = "data/cache"):
    return (KalshiClient(GAMMA, cache_dir=cache_dir, min_interval=0.1),
            KalshiClient(CLOB, cache_dir=cache_dir, min_interval=0.1))


def closed_temperature_events(gamma, start, end, cache: bool = True) -> list[dict]:
    """Closed daily temperature events ending between dates `start` and `end`, one day at a time
    (the events endpoint refuses large offsets)."""
    from datetime import timedelta
    out, d = [], start
    while d <= end:
        for off in range(0, 1000, 100):
            try:
                data = gamma._get("/events", {"closed": "true", "limit": 100, "offset": off,
                                              "tag_slug": "weather", "end_date_min": f"{d}T00:00:00Z",
                                              "end_date_max": f"{d}T23:59:59Z"}, cache=cache)
            except Exception:
                break
            if not data:
                break
            out += [e for e in data if " temperature in " in (e.get("title") or "").lower()]
            if len(data) < 100:
                break
        d += timedelta(days=1)
    return out


def price_at(clob, token: str, ts: int, lookback: int = 6 * 3600) -> float | None:
    data = clob._get("/prices-history", {"market": token, "interval": "max", "fidelity": 60}, cache=True)
    best = None
    for pt in (data or {}).get("history", []):
        if ts - lookback < pt["t"] <= ts:
            best = pt["p"]
    return best


def outcome(market: dict) -> str | None:
    try:
        yes, no = (float(x) for x in json.loads(market.get("outcomePrices") or "[]"))
    except (ValueError, TypeError):
        return None
    return "yes" if yes > 0.99 else "no" if no > 0.99 else None


def longshot_backtest(gamma, clob, events: list[dict], hours_before: int = 24,
                      lo: float = 0.005, hi: float = 0.04, slippage: float = 0.005,
                      contracts: int = 10) -> list[dict]:
    trades = []
    for e in events:
        end = datetime.fromisoformat(e["endDate"].replace("Z", "+00:00"))
        ts = int(end.timestamp()) - hours_before * 3600
        for m in e.get("markets", []):
            res = outcome(m)
            toks = json.loads(m.get("clobTokenIds") or "[]")
            if res is None or not toks:
                continue
            p = price_at(clob, toks[0], ts)
            if p is None or not lo <= p <= hi:
                continue
            price = round(1 - p + slippage, 4)
            won = res == "no"
            trades.append({"date": end.date().isoformat(), "city": e["title"], "ticker": m.get("question"),
                           "side": "no", "price": price, "contracts": contracts, "fee": 0.0, "won": won,
                           "pnl": round(contracts * ((1 - price) if won else -price), 4)})
    return trades
