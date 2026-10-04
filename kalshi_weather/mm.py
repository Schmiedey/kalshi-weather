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


def trades_after(kalshi, ticker: str, close_time: str | None, after_ts: int,
                 cache: bool = True) -> list[dict]:
    """All public trades on `ticker` after `after_ts` (cached; historical endpoint if archived)."""
    cutoff = kalshi.historical_cutoff() if close_time else None
    path = "/historical/trades" if (cutoff and close_time < cutoff) else "/markets/trades"
    out, cursor = [], None
    while True:
        params = {"ticker": ticker, "min_ts": after_ts, "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = kalshi._get(path, params, cache=cache, allow_404=True) or {}
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


MAKER_STRATEGY = "maker_5pm"
MAKER_HOUR = 17


def run_maker_paper(kalshi, ledger, markets, size: int = 10, now=None, log=print) -> list:
    """Post virtual resting NO orders (YES offers 1c under the ask) on tomorrow's longshots."""
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    from .kalshi import event_date
    from .strategy import Signal
    now = now or datetime.now(timezone.utc)
    posted = []
    for m in markets:
        target = now.astimezone(ZoneInfo(m.tz)).date() + timedelta(days=1)
        for b in kalshi.markets(m.series, status="open"):
            if event_date(b.event_ticker) != target or ledger.has_ticker(b.ticker, MAKER_STRATEGY):
                continue
            q = kalshi.orderbook(b.ticker)
            x = maker_order(q["yes_bid"], q["yes_ask"])
            if x is None:
                continue
            price = round(1 - x, 4)
            sig = Signal(b.ticker, b.event_ticker, "no", price, prob=price, edge=0.0,
                         contracts=size, fee=maker_fee(price, size))
            tid = ledger.record(m.key, sig, strategy=MAKER_STRATEGY)
            ledger.db.execute("UPDATE trades SET status='resting' WHERE id=?", (tid,))
            ledger.db.commit()
            posted.append(sig)
            log(f"[{m.key}] {MAKER_STRATEGY}: RESTING offer YES @ ${x:.2f} (= NO @ ${price:.2f}) "
                f"x{size} on {b.ticker} (book {q['yes_bid']}/{q['yes_ask']})")
    return posted


def check_fills(kalshi, ledger, now=None, log=print) -> dict:
    """Fill resting orders from real trades since they were posted; expire them at close."""
    now = now or datetime.now(timezone.utc)
    out = {"filled": 0, "expired": 0, "resting": 0}
    for t in ledger.db.execute("SELECT * FROM trades WHERE status='resting'").fetchall():
        mk = kalshi.market(t["ticker"])
        posted = int(datetime.fromisoformat(t["opened_at"]).timestamp())
        offer = round(1 - t["price"], 4)
        q = filled_qty(trades_after(kalshi, t["ticker"], None, posted, cache=False), offer, t["contracts"])
        closed = mk.close_time and _ts(mk.close_time) <= now.timestamp()
        if q >= 1 and (closed or q == t["contracts"]):
            ledger.db.execute("UPDATE trades SET status='open', contracts=?, fee=? WHERE id=?",
                              (q, maker_fee(t["price"], q), t["id"]))
            out["filled"] += 1
            log(f"filled {q}x {t['ticker']} NO @ ${t['price']:.2f}")
        elif closed:
            ledger.db.execute("UPDATE trades SET status='expired' WHERE id=?", (t["id"],))
            out["expired"] += 1
        else:
            out["resting"] += 1
    ledger.db.commit()
    return out
