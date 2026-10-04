"""Command line: python -m kalshi_weather <command>"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from .config import CITIES, KALSHI_PROD, StrategyConfig


def _cities(arg: str):
    keys = list(CITIES) if arg == "all" else [k.strip() for k in arg.split(",")]
    bad = [k for k in keys if k not in CITIES]
    if bad:
        raise SystemExit(f"unknown city {bad}; choose from {list(CITIES)}")
    return [CITIES[k] for k in keys]


def _local_hour(city) -> int:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.now(ZoneInfo(city.tz)).hour


def _table(rows: list[dict]):
    if not rows:
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def _backtest_longshot(kalshi, cities, days, hour, data_dir):
    import csv
    import os
    from .longshot import LongshotConfig, backtest_longshot, collect_quotes, summarize
    cfg = LongshotConfig(hour=hour)
    end = date.today() - timedelta(days=2)
    start = end - timedelta(days=days)
    trades = []
    rows = []
    for c in cities:
        t = backtest_longshot(collect_quotes(kalshi, c, start, end, hour), cfg)
        trades += t
        rows.append({"city": c.key} | summarize(t))
    print()
    _table(rows)
    dates = sorted({t["date"] for t in trades})
    if dates:
        mid = dates[len(dates) // 2]
        print("\nFIRST HALF ", json.dumps(summarize([t for t in trades if t["date"] < mid])))
        print("SECOND HALF", json.dumps(summarize([t for t in trades if t["date"] >= mid])))
    print("TOTAL      ", json.dumps(summarize(trades)))
    if trades:
        os.makedirs(data_dir, exist_ok=True)
        with open(os.path.join(data_dir, "longshot_trades.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(trades[0]))
            w.writeheader()
            w.writerows(trades)
        print(f"\nSaved {data_dir}/longshot_trades.csv")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kalshi_weather")
    ap.add_argument("--data", default="data", help="folder for db, cache, outputs")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("discover", help="list Kalshi weather series to verify tickers")

    bt = sub.add_parser("backtest", help="walk-forward backtest on settled markets")
    bt.add_argument("--cities", default="all")
    bt.add_argument("--days", type=int, default=180)
    bt.add_argument("--min-edge", type=float, default=StrategyConfig.min_edge)
    bt.add_argument("--hour", type=int, default=15, help="local hour, day before, to decide")
    bt.add_argument("--strategy", choices=["model", "longshot"], default="model")
    bt.add_argument("--prob", choices=["model", "blend"], default="model",
                    help="probability to trade on: forecast model, or model blended with market")

    pp = sub.add_parser("paper", help="place paper trades for tomorrow's markets")
    pp.add_argument("--cities", default="all")
    pp.add_argument("--min-edge", type=float, default=StrategyConfig.min_edge)
    pp.add_argument("--prob", choices=["model", "blend"], default="model")
    pp.add_argument("--strategy", choices=["model", "longshot"], default="model")
    pp.add_argument("--contracts", type=int, default=10)
    pp.add_argument("--variant", default="all", help="longshot variant name, or 'all'")
    pp.add_argument("--scheduled", action="store_true",
                    help="longshot: each variant trades only cities currently at its local hour")
    pp.add_argument("--at-local-hour", type=int, default=None,
                    help="only trade cities where it is currently this hour locally (for an hourly cron)")

    sub.add_parser("settle", help="settle finished paper trades")
    sub.add_parser("report", help="paper trading results")
    a = ap.parse_args(argv)

    from .kalshi import KalshiClient
    from .weather import WeatherClient
    kalshi = KalshiClient(KALSHI_PROD, cache_dir=f"{a.data}/cache")

    if a.cmd == "discover":
        for s in KalshiClient(KALSHI_PROD).series_list("Climate and Weather"):
            if "high" in (s.get("title") or "").lower() or "HIGH" in s.get("ticker", ""):
                print(f"{s.get('ticker'):<16} {s.get('title')}")
        return

    if a.cmd == "backtest" and a.strategy == "longshot":
        _backtest_longshot(kalshi, _cities(a.cities), a.days, a.hour, a.data)
        return

    if a.cmd == "backtest":
        from .backtest import run_backtest
        cfg = StrategyConfig(min_edge=a.min_edge, prob_source=a.prob)
        end = date.today() - timedelta(days=2)
        out = run_backtest(kalshi, WeatherClient(), _cities(a.cities), end - timedelta(days=a.days),
                           end, cfg, out_dir=a.data, decision_hour=a.hour)
        print()
        _table(out["per_city"])
        print("\nTOTAL", json.dumps(out["total"]))
        t = out["total"]
        if t["blend_brier"] is not None:
            print(f"Blend (model + market) Brier {t['blend_brier']} vs market {t['blend_mkt_brier']} "
                  f"on the same brackets; bracket-sum arbitrage in {t['arb_events']}/{t['arb_checked']} events.")
        print(f"\nSaved {a.data}/backtest_trades.csv and {a.data}/model_params.json")
        t = out["total"]
        if t["model_brier"] is not None and t["market_brier"] is not None:
            verdict = "BEATS" if t["model_brier"] < t["market_brier"] else "does NOT beat"
            print(f"Model {verdict} the market on accuracy (lower Brier is better).")
        return

    from .ledger import Ledger
    ledger = Ledger(f"{a.data}/paper.db")
    cities = _cities(a.cities) if a.cmd == "paper" else []
    if a.cmd == "paper" and a.at_local_hour is not None:
        cities = [c for c in cities if _local_hour(c) == a.at_local_hour]
        if not cities:
            print(f"no city is at local hour {a.at_local_hour} right now")
            return
    if a.cmd == "paper" and a.strategy == "longshot":
        from dataclasses import replace as _replace
        from .longshot import VARIANTS, due_cities, run_longshot_paper
        names = list(VARIANTS) if a.variant == "all" else [a.variant]
        if any(n not in VARIANTS for n in names):
            raise SystemExit(f"unknown variant; choose from {list(VARIANTS)} or all")
        placed = []
        for n in names:
            cfg = _replace(VARIANTS[n], contracts=a.contracts)
            todo = due_cities(cities, cfg.hour) if a.scheduled else cities
            if todo:
                placed += run_longshot_paper(kalshi, ledger, todo, cfg)
            else:
                print(f"{n}: no city is at {cfg.hour}:00 local right now")
        print(f"\n{len(placed)} longshot paper trade(s) placed.")
    elif a.cmd == "paper":
        from .paper import load_blend_weights, load_params, run_paper
        cfg = StrategyConfig(min_edge=a.min_edge, prob_source=a.prob)
        placed = run_paper(kalshi, WeatherClient(), ledger, cities, cfg,
                           load_params(f"{a.data}/model_params.json"),
                           blend_w=load_blend_weights(f"{a.data}/model_params.json"))
        print(f"\n{len(placed)} paper trade(s) placed.")
    elif a.cmd == "settle":
        from .paper import settle_open
        print(f"{settle_open(kalshi, ledger)} trade(s) settled.")
    elif a.cmd == "report":
        for strat in ledger.strategies() or ["model"]:
            s = ledger.summary(strat)
            print(f"== {strat} ==")
            for k, v in s.items():
                print(f"{k:>18}: {round(v, 4) if isinstance(v, float) else v}")
            if s["settled"] and s["settled"] < 100:
                print(f"Only {s['settled']} settled trades - need 100+ before trusting the result.")
            if strat.startswith("longshot") and s["settled"]:
                print(f"{'edge vs market':>18}: {round(s['pnl'] - s['expected_pnl'], 2)} "
                      "(realized P&L minus the market-implied baseline)")
