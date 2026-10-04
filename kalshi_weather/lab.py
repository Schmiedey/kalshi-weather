"""The lab: test strategies across markets, record every test, keep a locked holdout.

Every test is appended to lab/experiments.jsonl (committed to the repo), so a result is
always read next to how many ideas were tried. The most recent HOLDOUT_DAYS are reserved:
development runs end before them, and `final_exam` runs a strategy on them exactly once.
"""
from __future__ import annotations

import json
import math
import os
from datetime import date, datetime, timedelta, timezone

from .longshot import LongshotConfig, backtest_longshot, collect_quotes, quote_from_candles, summarize

REGISTRY = "lab/experiments.jsonl"
HOLDOUT_DAYS = 30


def windows(end: date, days: int = 180) -> dict[str, tuple[date, date]]:
    start = end - timedelta(days=days)
    split = end - timedelta(days=HOLDOUT_DAYS)
    return {"all": (start, end), "dev": (start, split - timedelta(days=1)), "holdout": (split, end)}


def load(path: str = REGISTRY) -> list[dict]:
    if not os.path.exists(path):
        return []
    with open(path) as f:
        return [json.loads(line) for line in f if line.strip()]


def register(entry: dict, path: str = REGISTRY) -> dict:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    entry = {"id": len(load(path)) + 1, "at": datetime.now(timezone.utc).isoformat(timespec="seconds")} | entry
    with open(path, "a") as f:
        f.write(json.dumps(entry) + "\n")
    return entry


def judge(trades: list[dict], ci_key: str = "ci95_cents") -> dict:
    """Pass = CI lower bound above zero and both halves of the period positive."""
    s = summarize(trades)
    if not s.get("trades"):
        return s | {"passed": False}
    dates = sorted({t["date"] for t in trades})
    mid = dates[len(dates) // 2]
    first = summarize([t for t in trades if t["date"] < mid])
    second = summarize([t for t in trades if t["date"] >= mid])
    halves = (first.get("cents_per_contract", 0), second.get("cents_per_contract", 0))
    return s | {"halves_cents": halves,
                "passed": s[ci_key][0] > 0 and halves[0] > 0 and halves[1] > 0}


def longshot_scan(kalshi, markets, start: date, end: date, hour: int, max_yes_bid: float = 0.05,
                  log=print) -> tuple[list[dict], dict[str, dict]]:
    """Longshot rule over many markets. Returns (all trades, per-market summary)."""
    cfg = LongshotConfig(hour=hour, max_yes_bid=max_yes_bid)
    trades, per = [], {}
    for m in markets:
        t = backtest_longshot(collect_quotes(kalshi, m, start, end, hour), cfg)
        for x in t:
            x["city"] = m.key
        trades += t
        per[m.key] = summarize(t)
        log(f"[{m.key}] {per[m.key].get('trades', 0)} trades, "
            f"{per[m.key].get('cents_per_contract')}c/contract")
    return trades, per


def final_exam(name: str, run, path: str = REGISTRY) -> dict:
    """Run `run()` (which must use the holdout window) once per strategy name, and record it."""
    if any(e.get("name") == name and e.get("period") == "holdout" for e in load(path)):
        raise RuntimeError(f"{name} already took its final exam; the holdout is spent for it")
    return register({"name": name, "period": "holdout"} | run(), path)


def sample_by_date(markets: list, cap: int) -> list:
    """Every k-th close date, k chosen from the market count alone, so at most about `cap` markets."""
    dates = sorted({(m.close_time or "")[:10] for m in markets})
    if len(markets) <= cap or not dates:
        return list(markets)
    per_day = len(markets) / len(dates)
    k = max(1, math.ceil(len(dates) * per_day / cap))
    keep = set(dates[::k])
    return [m for m in markets if (m.close_time or "")[:10] in keep]


def xmarket_quotes(kalshi, series: str, start: date, end: date, hours_before: int = 6,
                   cap: int = 400) -> list[dict]:
    """Quote (YES bid/ask) `hours_before` each market's close, for any Kalshi series.

    Rows match backtest_longshot's input: city (= series), date, event, ticker, bid, ask, result.
    """
    lo = int(datetime(start.year, start.month, start.day, tzinfo=timezone.utc).timestamp())
    hi = int(datetime(end.year, end.month, end.day, tzinfo=timezone.utc).timestamp()) + 86400
    ms = [m for m in kalshi.settled_markets(series, lo, hi, cache=True)
          if m.result in ("yes", "no") and m.close_time]
    rows = []
    for m in sample_by_date(ms, cap):
        close = int(datetime.fromisoformat(m.close_time.replace("Z", "+00:00")).timestamp())
        ts = close - hours_before * 3600
        q = quote_from_candles(kalshi.candles(series, m.ticker, ts - 4 * 3600, ts, close_time=m.close_time), ts)
        bid, ask = q
        rows.append({"city": series, "date": m.close_time[:10], "event": m.event_ticker,
                     "ticker": m.ticker, "bid": bid, "ask": ask, "result": m.result})
    return rows
