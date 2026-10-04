"""Live paper trading: real prices and forecasts, fake money."""
from __future__ import annotations

import json
import os
from collections import defaultdict
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .config import City, StrategyConfig
from .kalshi import event_date
from .blend import blend_prob
from .model import ErrorModel, bracket_prob
from .strategy import evaluate, pick_for_event


def load_params(path: str) -> dict[str, ErrorModel]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {k: ErrorModel(v["bias"], v["sigma"], v.get("n", 0)) for k, v in raw.items()}


def load_blend_weights(path: str) -> dict[str, tuple]:
    if not os.path.exists(path):
        return {}
    with open(path) as f:
        raw = json.load(f)
    return {k: tuple(v["blend_w"]) for k, v in raw.items() if v.get("blend_w")}


def run_paper(kalshi, weather, ledger, cities: list[City], cfg: StrategyConfig,
              params: dict[str, ErrorModel], now: datetime | None = None, log=print,
              blend_w: dict[str, tuple] | None = None) -> list:
    now = now or datetime.now(timezone.utc)
    placed = []
    for city in cities:
        target = now.astimezone(ZoneInfo(city.tz)).date() + timedelta(days=1)
        em = params.get(city.key)
        if em is None:
            em = ErrorModel(0.0, cfg.default_sigma, 0)
            log(f"[{city.key}] no fitted params (run backtest first) - using sigma={em.sigma}")
        events = defaultdict(list)
        for b in kalshi.markets(city.series, status="open"):
            if event_date(b.event_ticker) == target:
                events[b.event_ticker].append(b)
        if not events:
            log(f"[{city.key}] no open markets for {target}")
            continue
        fc = weather.forecast_highs(city).get(target)
        if fc is None:
            log(f"[{city.key}] no forecast for {target}")
            continue
        mu = em.mean(fc)
        bw = (blend_w or {}).get(city.key)
        if cfg.prob_source == "blend" and bw is None:
            log(f"[{city.key}] no blend weights (run backtest first) - skipping")
            continue
        for ev, brackets in events.items():
            if ledger.has_event(ev):
                log(f"[{city.key}] already traded {ev}")
                continue
            signals = []
            for b in brackets:
                p = bracket_prob(b, mu, em.sigma)
                if p is None:
                    continue
                q = kalshi.orderbook(b.ticker)
                if cfg.prob_source == "blend":
                    bid, ask = q["yes_bid"], q["yes_ask"]
                    mkt = (bid + ask) / 2 if bid is not None and ask is not None else (ask or bid)
                    if mkt is None:
                        continue
                    p = blend_prob(bw, mkt, p)
                sig = evaluate(replace(b, yes_bid=q["yes_bid"], yes_ask=q["yes_ask"]), p, cfg)
                if sig:
                    signals.append(sig)
            picks = pick_for_event(signals, cfg)
            for s in picks:
                ledger.record(city.key, s, forecast=fc)
                placed.append(s)
                log(f"[{city.key}] PAPER BUY {s.contracts}x {s.side.upper()} {s.ticker} @ ${s.price:.2f} "
                    f"(model {s.prob:.0%}, edge {s.edge:+.3f}, forecast {fc:.1f}F)")
            if not picks:
                log(f"[{city.key}] {ev}: forecast {fc:.1f}F, no trade clears the edge threshold")
    return placed


def settle_open(kalshi, ledger, log=print) -> int:
    n = 0
    for t in ledger.open_trades():
        if (t["strategy"] or "").startswith("poly_"):
            continue                # Polymarket trades settle in polymarket.settle_poly
        m = kalshi.market(t["ticker"])
        if m.result in ("yes", "no"):
            pnl = ledger.settle(t["id"], m.result)
            n += 1
            log(f"settled {t['ticker']} {t['side']} -> {m.result}: ${pnl:+.2f}")
    return n
