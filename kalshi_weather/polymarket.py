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


# Forward paper test (registered 2026-10-04 as lab experiment "poly_longshot"): the same rule,
# paid at the live NO ask from the public order book instead of an assumed slippage. A check of
# 216 open longshot brackets on 2026-10-04 found the real NO ask 0.30c worse than the backtest's
# assumed cost on average, so the backtest edge (+0.57c) is likely closer to +0.3c.
POLY_STRATEGY = "poly_longshot"


def _iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def _city(title: str) -> str:
    """'Highest temperature in NYC on October 5?' -> 'nyc high'."""
    t = title.lower()
    kind = "low" if t.startswith("lowest") else "high"
    return t.split(" in ", 1)[-1].split(" on ")[0].strip() + " " + kind


def due_events(gamma, now: datetime, hours_before: int = 24, early_min: int = 40,
               late_min: int = 65) -> list[dict]:
    """Open temperature events ending about `hours_before` hours from now. The window reaches
    `late_min` back so a scheduled run that GitHub starts up to an hour late still trades;
    has_ticker stops repeats. (All of them end at 12:00 UTC, so the hourly :05 run catches each once.)"""
    from datetime import timedelta
    mid = now + timedelta(hours=hours_before)
    lo, hi = mid - timedelta(minutes=late_min), mid + timedelta(minutes=early_min)
    out = []
    for off in range(0, 1000, 100):
        data = gamma._get("/events", {"closed": "false", "limit": 100, "offset": off, "tag_slug": "weather",
                                      "end_date_min": _iso(lo), "end_date_max": _iso(hi)})
        if not data:
            break
        out += [e for e in data if " temperature in " in (e.get("title") or "").lower()]
        if len(data) < 100:
            break
    return out


def best_ask(clob, token: str) -> tuple[float | None, float]:
    asks = sorted((float(a["price"]), float(a["size"]))
                  for a in (clob._get("/book", {"token_id": token}) or {}).get("asks", []))
    return asks[0] if asks else (None, 0.0)


def run_poly_paper(gamma, clob, ledger, contracts: int = 10, now: datetime | None = None,
                   lo: float = 0.005, hi: float = 0.04, log=print) -> int:
    """Paper-buy NO at the live NO ask on brackets whose YES price is lo..hi, 24h before the end."""
    from .strategy import Signal
    now = now or datetime.now(timezone.utc)
    n = 0
    for e in due_events(gamma, now):
        for m in e.get("markets", []):
            tick = f"PM-{m.get('id')}"
            if m.get("closed") or ledger.has_ticker(tick, POLY_STRATEGY):
                continue
            try:
                p = float(json.loads(m.get("outcomePrices") or "[]")[0])
                no_token = json.loads(m.get("clobTokenIds") or "[]")[1]
            except (ValueError, IndexError, TypeError):
                continue
            if not lo <= p <= hi:
                continue
            ask, size = best_ask(clob, no_token)
            qty = min(contracts, int(size))
            if ask is None or ask >= 1 or qty < 1:
                continue
            sig = Signal(tick, str(e.get("id")), "no", round(ask, 4), prob=round(1 - p, 4), edge=0.0,
                         contracts=qty, fee=0.0)
            ledger.record(_city(e.get("title") or ""), sig, strategy=POLY_STRATEGY)
            n += 1
            log(f"[poly] PAPER BUY {qty}x NO {m.get('groupItemTitle') or m.get('question')} "
                f"({e.get('title')}) @ ${ask:.3f} (YES price {p:.3f})")
    return n


def settle_poly(gamma, ledger, log=print) -> int:
    """Settle open Polymarket paper trades, one event lookup per event."""
    by_event: dict[str, list] = {}
    for t in ledger.open_trades():
        if t["strategy"] == POLY_STRATEGY:
            by_event.setdefault(t["event_ticker"], []).append(t)
    n = 0
    for ev, trades in by_event.items():
        markets = {f"PM-{m.get('id')}": m for m in (gamma._get(f"/events/{ev}") or {}).get("markets", [])}
        for t in trades:
            m = markets.get(t["ticker"])
            res = outcome(m) if m and m.get("closed") else None
            if res:
                pnl = ledger.settle(t["id"], res)
                n += 1
                log(f"settled {t['ticker']} {t['side']} -> {res}: ${pnl:+.2f}")
    return n
