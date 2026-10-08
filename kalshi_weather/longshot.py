"""Longshot strategy: buy NO on brackets whose YES trades at a few cents.

Evidence (180 days, 7 cities, 3pm the day before): brackets with mid price under
5c settled YES 1.1% of the time vs 2.8% implied - the favorite-longshot bias.
Buying NO at (1 - best YES bid) when the YES bid is under 5c earned about
+0.8c per contract after fees and spread, with the rule chosen on the first 90
days and confirmed on the last 90. The edge per trade is small; it only adds up
over many trades, and one loss costs ~97c per contract.
"""
from __future__ import annotations

import random
from collections import defaultdict
from dataclasses import dataclass

from .strategy import Signal, settle_pnl, taker_fee

@dataclass
class LongshotConfig:
    name: str = "longshot_3pm"    # ledger strategy name
    hour: int = 15                # local hour on the day before the event to trade
    max_yes_bid: float = 0.05     # buy NO when the best YES bid is below this
    min_yes_bid: float = 0.01     # need a YES bid to sell into (no bid = no NO ask)
    contracts: int = 10
    fee_rate: float = 0.07


# Backtest (180 days, 7 cities, 10 contracts, after fees), c/contract and 90% CI, by hour:
#   noon +0.48 [+0.03, +0.94]   3pm +0.83 [+0.37, +1.27]   5pm +1.09 [+0.70, +1.47]
#   7pm  +1.02 [+0.63, +1.38]   9pm +0.83 [+0.37, +1.27]  11pm +0.55 [+0.07, +1.01]
# All passed the bar set before testing (CI above zero, both halves positive). They trade
# mostly the same brackets, so only the strongest, 5pm, is kept (2026-10-08).
VARIANTS = {"longshot_5pm": LongshotConfig(name="longshot_5pm", hour=17)}


def due_cities(cities, hour: int, now=None) -> list:
    """Cities where the local time is currently `hour` o'clock."""
    from datetime import datetime, timezone
    from zoneinfo import ZoneInfo
    now = now or datetime.now(timezone.utc)
    return [c for c in cities if now.astimezone(ZoneInfo(c.tz)).hour == hour]


def longshot_signal(ticker: str, event_ticker: str, yes_bid: float | None, cfg: LongshotConfig,
                    available: float | None = None) -> Signal | None:
    """NO order at 1 - yes_bid, sized to what the book can fill.

    `prob` is the market-implied NO probability (1 - yes_bid), so the ledger's
    expected P&L is the no-edge baseline and realized minus expected is the edge.
    """
    if yes_bid is None or not (cfg.min_yes_bid <= yes_bid < cfg.max_yes_bid):
        return None
    n = cfg.contracts if available is None else min(cfg.contracts, int(available))
    if n < 1:
        return None
    price = round(1.0 - yes_bid, 4)
    fee = taker_fee(price, n, cfg.fee_rate)
    return Signal(ticker, event_ticker, "no", price, prob=price, edge=round(-fee / n, 4),
                  contracts=n, fee=fee)


def summarize(trades: list[dict], n_boot: int = 2000, seed: int = 0) -> dict:
    """P&L stats with a 90% CI that resamples whole days (trades on one day are linked)."""
    if not trades:
        return {"trades": 0}
    pnl = sum(t["pnl"] for t in trades)
    contracts = sum(t["contracts"] for t in trades)
    cost = sum(t["price"] * t["contracts"] + t["fee"] for t in trades)
    by_day = defaultdict(list)
    for t in trades:
        by_day[t["date"]].append(t)
    days = sorted(by_day)
    rng = random.Random(seed)
    per_contract = []
    for _ in range(n_boot):
        sample = [t for d in (rng.choice(days) for _ in days) for t in by_day[d]]
        per_contract.append(sum(t["pnl"] for t in sample) / sum(t["contracts"] for t in sample))
    per_contract.sort()
    p_le_0 = sum(1 for x in per_contract if x <= 0) / n_boot
    daily = [sum(t["pnl"] for t in by_day[d]) for d in days]
    worst = min(daily)
    return {
        "trades": len(trades), "losses": sum(1 for t in trades if not t["won"]),
        "pnl": round(pnl, 2), "cost": round(cost, 2), "roi": round(pnl / cost, 4) if cost else None,
        "cents_per_contract": round(100 * pnl / contracts, 3),
        "ci90_cents": (round(100 * per_contract[int(0.05 * n_boot)], 3),
                       round(100 * per_contract[int(0.95 * n_boot)], 3)),
        "ci95_cents": (round(100 * per_contract[int(0.025 * n_boot)], 3),
                       round(100 * per_contract[int(0.975 * n_boot)], 3)),
        "p_value": round(p_le_0, 4),          # one-sided: share of resamples with edge <= 0
        "days": len(days), "worst_day": round(worst, 2),
        "losing_days": sum(1 for x in daily if x < 0),
    }


def backtest_longshot(rows: list[dict], cfg: LongshotConfig) -> list[dict]:
    """rows: dicts with city, date, event, ticker, bid (YES bid at decision), result ('yes'/'no')."""
    trades = []
    for r in rows:
        sig = longshot_signal(r["ticker"], r["event"], r["bid"], cfg)
        if sig is None:
            continue
        pnl = settle_pnl("no", sig.price, sig.contracts, sig.fee, r["result"])
        trades.append({"city": r["city"], "date": r["date"], "ticker": r["ticker"],
                       "price": sig.price, "contracts": sig.contracts, "fee": sig.fee,
                       "result": r["result"], "won": r["result"] == "no", "pnl": pnl})
    return trades


DAY_BEFORE_HOURS = (11, 23)   # one cached candle window per bracket covers all decision hours


def decision_ts(d, city, hour: int) -> int:
    """Unix time of `hour` local on the day before event day `d`."""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo
    return int(datetime.combine(d - timedelta(days=1), time(hour), ZoneInfo(city.tz)).timestamp())


def day_before_candles(kalshi, city, bracket, d):
    """Hourly candles for 11am-11pm local on the day before event day `d`."""
    from datetime import datetime, time, timedelta
    from zoneinfo import ZoneInfo
    tz = ZoneInfo(city.tz)
    prev = d - timedelta(days=1)
    s = int(datetime.combine(prev, time(DAY_BEFORE_HOURS[0]), tz).timestamp())
    e = int(datetime.combine(prev, time(DAY_BEFORE_HOURS[1]), tz).timestamp())
    return kalshi.candles(city.series, bracket.ticker, s, e, period=60, close_time=bracket.close_time)


def quote_from_candles(candles: list[dict], ts: int, lookback: int = 4 * 3600):
    """Latest YES bid/ask at or before `ts` (no later data), from the last `lookback` seconds."""
    for c in reversed(candles):
        if ts - lookback < c["ts"] <= ts and (c["yes_bid"] is not None or c["yes_ask"] is not None):
            return c["yes_bid"], c["yes_ask"]
    return None, None


def collect_quotes(kalshi, city, start, end, decision_hour: int = 15) -> list[dict]:
    """YES bid/ask for every settled bracket at `decision_hour` local on the day before."""
    from datetime import datetime, time, timedelta, timezone
    from .kalshi import event_date
    if not DAY_BEFORE_HOURS[0] <= decision_hour <= DAY_BEFORE_HOURS[1]:
        raise ValueError(f"decision_hour must be within {DAY_BEFORE_HOURS}")
    min_ts = int(datetime.combine(start, time(0), timezone.utc).timestamp())
    max_ts = int(datetime.combine(end + timedelta(days=2), time(0), timezone.utc).timestamp())
    rows = []
    for b in kalshi.settled_markets(city.series, min_close_ts=min_ts, max_close_ts=max_ts, cache=True):
        d = event_date(b.event_ticker)
        if not d or not (start <= d <= end) or b.result is None:
            continue
        candles = day_before_candles(kalshi, city, b, d)
        bid, ask = quote_from_candles(candles, decision_ts(d, city, decision_hour))
        rows.append({"city": city.key, "date": d.isoformat(), "event": b.event_ticker,
                     "ticker": b.ticker, "bid": bid, "ask": ask, "result": b.result})
    return rows


def run_longshot_paper(kalshi, ledger, cities, cfg: LongshotConfig, now=None, log=print) -> list:
    """Paper-buy NO on tomorrow's longshot brackets at the live order book."""
    from datetime import datetime, timedelta, timezone
    from zoneinfo import ZoneInfo
    from .kalshi import event_date
    now = now or datetime.now(timezone.utc)
    placed = []
    for city in cities:
        target = now.astimezone(ZoneInfo(city.tz)).date() + timedelta(days=1)
        brackets = [b for b in kalshi.markets(city.series, status="open")
                    if event_date(b.event_ticker) == target]
        if not brackets:
            log(f"[{city.key}] no open markets for {target}")
            continue
        n = 0
        for b in brackets:
            if ledger.has_ticker(b.ticker, cfg.name):
                continue
            q = kalshi.orderbook(b.ticker)
            sig = longshot_signal(b.ticker, b.event_ticker, q["yes_bid"], cfg, q["yes_bid_qty"])
            if sig is None:
                continue
            ledger.record(city.key, sig, strategy=cfg.name)
            placed.append(sig)
            n += 1
            log(f"[{city.key}] {cfg.name}: PAPER BUY {sig.contracts}x NO {sig.ticker} @ ${sig.price:.2f} "
                f"(YES bid {q['yes_bid']:.2f}, {q['yes_bid_qty']:.0f} available)")
        if not n:
            log(f"[{city.key}] {cfg.name} {target}: no longshot brackets (YES bid < {cfg.max_yes_bid:.2f})")
    return placed


def settle_open(kalshi, ledger, log=print) -> int:
    """Settle open paper trades whose market has a result."""
    n = 0
    for t in ledger.open_trades():
        m = kalshi.market(t["ticker"])
        if m.result in ("yes", "no"):
            pnl = ledger.settle(t["id"], m.result)
            n += 1
            log(f"settled {t['ticker']} {t['side']} -> {m.result}: ${pnl:+.2f}")
    return n
