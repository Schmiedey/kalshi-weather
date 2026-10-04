"""Maker version of the longshot trade, with fills taken from Kalshi's real trade tape.

At the decision hour on the day before, for a longshot bracket whose YES ask is at most
`max_ask` and whose spread is at least 2c, post a YES offer 1c under the ask (the same as
a resting NO bid 1c better than a taker would pay). Our offer would have been the best
ask, so any later taker buying YES at our price or higher would have hit us first.
Filled size = min(order size, YES-taker volume at >= our price before the market closed).

Only filled orders count, settled on the real outcome - so if fills mostly come right
before a longshot hits (informed buyers), the backtest pays for it.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import datetime, time, timedelta, timezone

MAKER_FEE_RATE = 0.0175          # assumed maker fee multiplier (conservative)


def maker_fee(price: float, contracts: int, rate: float = MAKER_FEE_RATE) -> float:
    return math.ceil(round(rate * contracts * price * (1 - price) * 100, 6)) / 100.0


def _ts(s: str) -> float:
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def trades_after(kalshi, ticker: str, close_time: str | None, after_ts: int) -> list[dict]:
    """All public trades on `ticker` after `after_ts` (cached; historical endpoint if archived)."""
    cutoff = kalshi.historical_cutoff() if close_time else None
    path = "/historical/trades" if (cutoff and close_time < cutoff) else "/markets/trades"
    out, cursor = [], None
    while True:
        params = {"ticker": ticker, "min_ts": after_ts, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = kalshi._get(path, params, cache=True, allow_404=True) or {}
        out += data.get("trades", [])
        cursor = data.get("cursor")
        if not cursor or not data.get("trades"):
            return out


def maker_order(bid: float | None, ask: float | None, max_ask: float = 0.06):
    """YES offer price (1c under the ask) for a longshot with room in the spread, else None."""
    if ask is None or ask > max_ask:
        return None
    x = round(ask - 0.01, 4)
    if x < 0.01 or x <= (bid or 0.0):
        return None
    return x


def filled_qty(trades: list[dict], offer: float, size: int) -> int:
    vol = sum(float(t.get("count_fp") or t.get("count") or 0) for t in trades
              if t.get("taker_side") == "yes" and float(t.get("yes_price_dollars") or 0) >= offer - 1e-9)
    return int(min(size, math.floor(vol)))


def maker_backtest(kalshi, markets, start, end, hour: int = 17, size: int = 10,
                   max_ask: float = 0.06, log=print) -> tuple[list[dict], dict]:
    from zoneinfo import ZoneInfo
    from .backtest import decision_ts
    from .kalshi import event_date
    from .longshot import day_before_candles, quote_from_candles
    trades, stats = [], defaultdict(int)
    for m in markets:
        min_ts = int(datetime.combine(start, time(0), timezone.utc).timestamp())
        max_ts = int(datetime.combine(end + timedelta(days=2), time(0), timezone.utc).timestamp())
        for b in kalshi.settled_markets(m.series, min_close_ts=min_ts, max_close_ts=max_ts, cache=True):
            d = event_date(b.event_ticker)
            if not d or not (start <= d <= end) or b.result is None:
                continue
            ts = decision_ts(d, m, hour)
            bid, ask = quote_from_candles(day_before_candles(kalshi, m, b, d), ts)
            x = maker_order(bid, ask, max_ask)
            if x is None:
                continue
            stats["orders"] += 1
            q = filled_qty(trades_after(kalshi, b.ticker, b.close_time, ts), x, size)
            if q < 1:
                continue
            stats["filled"] += 1
            no_price = round(1 - x, 4)
            fee = maker_fee(no_price, q)
            won = b.result == "no"
            trades.append({"date": d.isoformat(), "city": m.key, "ticker": b.ticker, "side": "no",
                           "price": no_price, "contracts": q, "fee": fee, "won": won,
                           "pnl": round(q * ((1 - no_price) if won else -no_price) - fee, 4)})
        log(f"[{m.key}] orders {stats['orders']} filled {stats['filled']}")
    return trades, dict(stats)
