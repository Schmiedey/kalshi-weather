"""Command line: python -m kalshi_weather <command>"""
from __future__ import annotations

import argparse
import json
from datetime import date, timedelta

from .config import CITIES, KALSHI_PROD


def _cities(arg: str):
    keys = list(CITIES) if arg == "all" else [k.strip() for k in arg.split(",")]
    bad = [k for k in keys if k not in CITIES]
    if bad:
        raise SystemExit(f"unknown city {bad}; choose from {list(CITIES)}")
    return [CITIES[k] for k in keys]


def _table(rows: list[dict]):
    if not rows:
        return
    cols = list(rows[0].keys())
    widths = {c: max(len(c), *(len(str(r[c])) for r in rows)) for c in cols}
    print("  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows:
        print("  ".join(str(r[c]).ljust(widths[c]) for c in cols))


def _backtest(kalshi, cities, days, hour, data_dir):
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

    bt = sub.add_parser("backtest", help="longshot backtest on settled markets")
    bt.add_argument("--cities", default="all")
    bt.add_argument("--days", type=int, default=180)
    bt.add_argument("--hour", type=int, default=17, help="local hour, day before, to decide")

    pp = sub.add_parser("paper", help="paper-buy NO on tomorrow's longshot brackets")
    pp.add_argument("--cities", default="all")
    pp.add_argument("--contracts", type=int, default=None, help="override the brain's sizing")
    pp.add_argument("--scheduled", action="store_true",
                    help="only trade cities where it is currently the strategy's local hour")

    sub.add_parser("settle", help="settle finished paper trades")
    sub.add_parser("report", help="paper trading results")
    br = sub.add_parser("brain", help="review the strategy: edge estimate, status, sizing")
    br.add_argument("--alerts", default=None, help="write newly-stopped strategies to this file")
    br.add_argument("--revive", default=None, help="put a stopped strategy back into evaluation")
    db = sub.add_parser("dashboard", help="write an HTML dashboard of the paper ledger")
    db.add_argument("--out", default="site/index.html")
    db.add_argument("--repo-url", default="")
    a = ap.parse_args(argv)

    from .kalshi import KalshiClient
    kalshi = KalshiClient(KALSHI_PROD, cache_dir=f"{a.data}/cache")

    if a.cmd == "backtest":
        _backtest(kalshi, _cities(a.cities), a.days, a.hour, a.data)
        return

    if a.cmd == "dashboard":
        from .dashboard import build
        n = build(f"{a.data}/paper.db", a.out, a.repo_url)
        print(f"Wrote {a.out} ({n} trades)")
        return

    from .brain import Brain
    from .ledger import Ledger
    from .longshot import VARIANTS
    ledger = Ledger(f"{a.data}/paper.db")

    if a.cmd == "paper":
        from dataclasses import replace
        from .longshot import due_cities, run_longshot_paper
        brain = Brain(ledger)
        cities = _cities(a.cities)
        placed = []
        for n, cfg in VARIANTS.items():
            size = a.contracts or brain.sized(n, 0.97, list(VARIANTS))   # 97c: typical NO price
            if size == 0:
                print(f"{n}: stopped by the brain ({brain.status(n)}); skipping")
                continue
            todo = due_cities(cities, cfg.hour) if a.scheduled else cities
            if todo:
                placed += run_longshot_paper(kalshi, ledger, todo, replace(cfg, contracts=size))
            else:
                print(f"{n}: no city is at {cfg.hour}:00 local right now")
        print(f"\n{len(placed)} longshot paper trade(s) placed.")
    elif a.cmd == "settle":
        from .longshot import settle_open
        print(f"{settle_open(kalshi, ledger)} trade(s) settled.")
    elif a.cmd == "brain":
        brain = Brain(ledger)
        if a.revive:
            brain.revive(a.revive)
            print(f"{a.revive} revived")
        rows, alerts = [], []
        for n in sorted(set(VARIANTS) | set(ledger.strategies())):
            status, est, alert = brain.review(n)
            if alert:
                alerts.append(alert)
            rows.append({"strategy": n, "status": status, "settled": est.trades,
                         "c/contract": round(100 * est.mean, 3) if est.trades else None,
                         "post_c": round(100 * est.post_mean, 3), "post_sd_c": round(100 * est.post_sd, 3),
                         "P(edge>0)": round(est.p_positive, 3), "drawdown": round(est.drawdown, 2),
                         "size@97c": brain.contracts(n, 0.97)})
        _table(rows)
        if a.alerts:
            with open(a.alerts, "w") as f:
                f.write("\n".join(alerts))
        for x in alerts:
            print("ALERT:", x)
    elif a.cmd == "report":
        for strat in ledger.strategies() or list(VARIANTS):
            s = ledger.summary(strat)
            print(f"== {strat} ==")
            for k, v in s.items():
                print(f"{k:>18}: {round(v, 4) if isinstance(v, float) else v}")
            if s["settled"] and s["settled"] < 100:
                print(f"Only {s['settled']} settled trades - need 100+ before trusting the result.")
            if s["settled"]:
                print(f"{'edge vs market':>18}: {round(s['pnl'] - s['expected_pnl'], 2)} "
                      "(realized P&L minus the market-implied baseline)")
