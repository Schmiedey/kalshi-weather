"""Walk-forward backtest on settled Kalshi markets.

For each event (one city, one day), in date order:
  1. take the forecast the models issued the day before (no lookahead)
  2. fit bias/sigma using ONLY earlier events
  3. read the market's real bid/ask at the decision time (default 3pm local, day before)
  4. trade if edge > threshold after fees, settle with Kalshi's actual result
"""
from __future__ import annotations

import csv
import json
import os
from collections import defaultdict
from dataclasses import asdict, dataclass, field, replace
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from .blend import arb_profit, blend_prob, fit_blend, logit
from .config import City, StrategyConfig
from .kalshi import event_date
from .model import brier, bracket_prob, fit_error_model, outcome_interval
from .strategy import evaluate, pick_for_event, settle_pnl


@dataclass
class CityResult:
    city: str
    events: int = 0
    events_priced: int = 0
    trades: list = field(default_factory=list)
    model_pairs: list = field(default_factory=list)
    market_pairs: list = field(default_factory=list)
    partition_sums: list = field(default_factory=list)
    blend_pairs: list = field(default_factory=list)
    blend_market_pairs: list = field(default_factory=list)   # market on the same brackets
    blend_w: tuple = (1.0, 0.0, 0.0)
    arbs: list = field(default_factory=list)
    rows: list = field(default_factory=list)      # per-bracket record for research
    final_bias: float = 0.0
    final_sigma: float = 0.0
    fit_n: int = 0

    def stats(self) -> dict:
        n = len(self.trades)
        pnl = sum(t["pnl"] for t in self.trades)
        cost = sum(t["price"] * t["contracts"] + t["fee"] for t in self.trades)
        wins = sum(1 for t in self.trades if t["won"])
        return {
            "city": self.city, "events": self.events, "events_priced": self.events_priced,
            "trades": n, "wins": wins, "win_rate": round(wins / n, 3) if n else None,
            "pnl": round(pnl, 2), "cost": round(cost, 2),
            "roi": round(pnl / cost, 4) if cost else None,
            "model_brier": _r(brier(self.model_pairs)), "market_brier": _r(brier(self.market_pairs)),
            "brackets_scored": len(self.model_pairs),
            "avg_prob_sum": _r(sum(self.partition_sums) / len(self.partition_sums))
            if self.partition_sums else None,
            "blend_brier": _r(brier(self.blend_pairs)),
            "blend_mkt_brier": _r(brier(self.blend_market_pairs)),
            "model_weight": round(self.blend_w[1], 2),
            "arb_events": sum(1 for a in self.arbs if a > 0),
            "arb_checked": len(self.arbs),
            "bias": round(self.final_bias, 2), "sigma": round(self.final_sigma, 2), "fit_n": self.fit_n,
        }


def _r(x, n=4):
    return None if x is None else round(x, n)


def decision_ts(d: date, city: City, hour: int) -> int:
    local = datetime.combine(d - timedelta(days=1), time(hour), ZoneInfo(city.tz))
    return int(local.timestamp())


def quote_at(kalshi, city: City, ticker: str, ts: int) -> tuple[float | None, float | None]:
    candles = kalshi.candles(city.series, ticker, ts - 4 * 3600, ts, period=60)
    for c in reversed(candles):
        if c["ts"] <= ts and (c["yes_ask"] is not None or c["yes_bid"] is not None):
            return c["yes_bid"], c["yes_ask"]
    return None, None


def known_errors(errors: list[tuple[date, tuple[float, float]]], d: date) -> list:
    """Errors whose outcome was public when deciding on day `d` (3pm on d-1).

    Day d-1's high is not final until its climate report the next morning, so
    only events up to d-2 count.
    """
    return [e for ed, e in errors if ed <= d - timedelta(days=2)]


def backtest_city(kalshi, weather, city: City, start: date, end: date,
                  cfg: StrategyConfig, decision_hour: int = 15, log=print,
                  refit_every: int = 7, min_blend_events: int = 30) -> CityResult:
    res = CityResult(city.key)
    min_ts = int(datetime.combine(start, time(0), timezone.utc).timestamp())
    max_ts = int(datetime.combine(end + timedelta(days=2), time(0), timezone.utc).timestamp())
    markets = kalshi.settled_markets(city.series, min_close_ts=min_ts, max_close_ts=max_ts, cache=True)
    events = defaultdict(list)
    for b in markets:
        d = event_date(b.event_ticker)
        if d and start <= d <= end:
            events[(d, b.event_ticker)].append(b)
    log(f"[{city.key}] {len(markets)} settled markets, {len(events)} events in range")
    if not events:
        return res

    forecasts = weather.historical_highs(city, start, end, decision_hour=decision_hour)
    errors: list[tuple[date, tuple[float, float]]] = []
    em, since_fit = fit_error_model([], cfg.min_fit_events, cfg.default_sigma), 999
    blend_rows: list[tuple[date, float, float, int]] = []   # (date, logit mkt, logit model, y)
    bw = None
    for (d, ev), brackets in sorted(events.items()):
        res.events += 1
        fc = forecasts.get(d)
        outcome = outcome_interval(brackets)
        if fc is None:
            continue
        if since_fit >= refit_every:          # walk-forward: refit on past events only
            em, since_fit = fit_error_model(known_errors(errors, d), cfg.min_fit_events,
                                            cfg.default_sigma), 0
            train = [(a, b, y) for ed, a, b, y in blend_rows if ed <= d - timedelta(days=2)]
            bw = fit_blend(train) if len({ed for ed, *_ in blend_rows
                                          if ed <= d - timedelta(days=2)}) >= min_blend_events else None
        since_fit += 1
        mu = em.mean(fc)
        ts = decision_ts(d, city, decision_hour)

        signals, probs, priced, quotes = [], [], False, []
        for b in brackets:
            p = bracket_prob(b, mu, em.sigma)
            if p is None or b.result is None:
                continue
            probs.append(p)
            bid, ask = quote_at(kalshi, city, b.ticker, ts)
            quotes.append((bid, ask))
            res.rows.append({"city": city.key, "date": d.isoformat(), "event": ev, "ticker": b.ticker,
                             "bid": bid, "ask": ask, "model_p": p, "fitted": em.n >= cfg.min_fit_events,
                             "result": b.result})
            if bid is None and ask is None:
                continue
            priced = True
            y = 1 if b.result == "yes" else 0
            if em.n >= cfg.min_fit_events:   # only score once the model is fitted
                res.model_pairs.append((p, y))
                mkt = (bid + ask) / 2 if bid is not None and ask is not None else (ask or bid)
                res.market_pairs.append((mkt, y))
                blend_rows.append((d, logit(mkt), logit(p), y))
                if bw is not None:
                    res.blend_pairs.append((blend_prob(bw, mkt, p), y))
                    res.blend_market_pairs.append((mkt, y))
                if cfg.prob_source == "blend":
                    p_trade = blend_prob(bw, mkt, p) if bw is not None else None
                else:
                    p_trade = p
                sig = evaluate(replace(b, yes_bid=bid, yes_ask=ask), p_trade, cfg) \
                    if p_trade is not None else None
                if sig:
                    signals.append((sig, b))
        if probs:
            res.partition_sums.append(sum(probs))
        arb = arb_profit(quotes, cfg.contracts_per_trade, cfg.fee_rate) if quotes else None
        if arb is not None:
            res.arbs.append(arb)
        res.events_priced += priced

        picks = pick_for_event([x for x, _ in signals], cfg)
        for sig, b in [(s, b) for s, b in signals if s in picks]:
            pnl = settle_pnl(sig.side, sig.price, sig.contracts, sig.fee, b.result)
            res.trades.append(asdict(sig) | {"city": city.key, "date": d.isoformat(),
                                             "bracket": b.title, "forecast": round(fc, 1),
                                             "mu": round(mu, 1), "sigma": round(em.sigma, 2),
                                             "result": b.result, "won": sig.side == b.result,
                                             "pnl": pnl})
        if outcome is not None:
            errors.append((d, (outcome[0] - fc, outcome[1] - fc)))

    final = fit_error_model([e for _, e in errors], cfg.min_fit_events, cfg.default_sigma)
    res.final_bias, res.final_sigma, res.fit_n = final.bias, final.sigma, final.n
    train = [(a, b, y) for _, a, b, y in blend_rows]
    res.blend_w = fit_blend(train) if len({ed for ed, *_ in blend_rows}) >= min_blend_events \
        else (1.0, 0.0, 0.0)
    return res


def run_backtest(kalshi, weather, cities: list[City], start: date, end: date,
                 cfg: StrategyConfig, out_dir: str = "data", decision_hour: int = 15,
                 log=print) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    results = [backtest_city(kalshi, weather, c, start, end, cfg, decision_hour, log) for c in cities]
    trades = [t for r in results for t in r.trades]
    if trades:
        with open(os.path.join(out_dir, "backtest_trades.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0].keys()))
            w.writeheader()
            w.writerows(trades)
    params = {r.city: {"bias": r.final_bias, "sigma": r.final_sigma, "n": r.fit_n,
                       "blend_w": list(r.blend_w) if r.blend_pairs else None}
              for r in results if r.fit_n >= cfg.min_fit_events}
    with open(os.path.join(out_dir, "model_params.json"), "w") as f:
        json.dump(params, f, indent=2)
    per_city = [r.stats() for r in results]
    all_model = [p for r in results for p in r.model_pairs]
    all_mkt = [p for r in results for p in r.market_pairs]
    all_blend = [p for r in results for p in r.blend_pairs]
    all_blend_mkt = [p for r in results for p in r.blend_market_pairs]
    pnl = sum(t["pnl"] for t in trades)
    cost = sum(t["price"] * t["contracts"] + t["fee"] for t in trades)
    total = {"trades": len(trades), "wins": sum(t["won"] for t in trades),
             "pnl": round(pnl, 2), "cost": round(cost, 2),
             "roi": round(pnl / cost, 4) if cost else None,
             "model_brier": _r(brier(all_model)), "market_brier": _r(brier(all_mkt)),
             "blend_brier": _r(brier(all_blend)), "blend_mkt_brier": _r(brier(all_blend_mkt)),
             "arb_events": sum(1 for r in results for a in r.arbs if a > 0),
             "arb_checked": sum(len(r.arbs) for r in results)}
    return {"per_city": per_city, "total": total, "params": params}
