"""Kalshi Liquidity Incentive Program: paper-track unclaimed reward pools.

Kalshi pays each incentive market's pool to resting orders, but a snapshot only counts
when BOTH sides of the book hold at least the Target Size. When one side is thin (often
a near-certain outcome, or a brand-new market), the pool pays nobody. Posting the Target
Size on the thin side at that side's best bid (1c if empty) makes the book two-sided and
earns that side's share: at most Target x price at risk, usually $10.

Each hourly run records every active program with a thin side and the share such an
order would get. `lip_report` sums those hourly estimates. Upper bound: it assumes the
book stays as seen until the next snapshot and that Kalshi scores the order as written
in the program terms (help.kalshi.com, Liquidity Incentive Program).
"""
from __future__ import annotations

import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timezone

from .kalshi import book_levels


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def active_programs(kalshi, now: datetime | None = None) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    out, cursor = [], None
    for _ in range(50):
        params = {"status": "active", "limit": 1000}
        if cursor:
            params["cursor"] = cursor
        data = kalshi._get("/incentive_programs", params)
        out += data.get("incentive_programs", [])
        cursor = data.get("next_cursor")
        if not cursor:
            break
    return [p for p in out if p.get("incentive_type") == "liquidity"
            and _ts(p["start_date"]) <= now < _ts(p["end_date"])]


def reward_per_day(p: dict) -> float:
    """period_reward is in centi-cents (1/100 of a cent)."""
    days = (_ts(p["end_date"]) - _ts(p["start_date"])).total_seconds() / 86400
    return p["period_reward"] / 10000 / max(days, 1 / 24)


def thin_side_estimate(p: dict, yes: list, no: list, now: datetime) -> dict | None:
    """Share of the pool from filling each side that is short of the Target Size.

    yes/no: [(price_dollars, size)] bids. Our order goes at the side's best bid (1c if
    empty), so it is never below the reference price; existing bids get full credit too,
    which makes T / (T + existing) a lower bound on our share of that side.
    """
    target = float(p["target_size_fp"])
    sides = {}
    for name, lv in (("yes", yes), ("no", no)):
        depth = sum(q for _, q in lv)
        if depth < target:
            price = max((px for px, _ in lv), default=0.01)
            sides[name] = {"depth": depth, "price": price, "share": target / (target + depth)}
    if not sides:
        return None
    share = sum(s["share"] for s in sides.values()) / 2  # each side is worth half the pool
    start, end = _ts(p["start_date"]), _ts(p["end_date"])
    # pool accrues evenly over the period; credit only the next hour (or what is left of it)
    left = min(3600.0, (end - now).total_seconds())
    est_hour = p["period_reward"] / 10000 * share * left / max((end - start).total_seconds(), 1)
    return {
        "ticker": p["market_ticker"], "program": p["id"], "kind": p.get("incentive_description"),
        "end": p["end_date"], "target": target, "reward_day": round(reward_per_day(p), 2),
        "yes_depth": sum(q for _, q in yes), "no_depth": sum(q for _, q in no),
        "thin": sorted(sides), "share": round(share, 4),
        "at_risk": round(sum(target * s["price"] for s in sides.values()), 2),
        "est_hour": round(est_hour, 4),
    }


def snapshot(kalshi, now: datetime | None = None, log=print) -> dict:
    now = now or datetime.now(timezone.utc)
    progs = active_programs(kalshi, now)
    tickers = sorted({p["market_ticker"] for p in progs})
    books, failed = {}, 0
    for i in range(0, len(tickers), 100):
        chunk = tickers[i:i + 100]
        try:
            for ob in kalshi._get("/markets/orderbooks", {"tickers": chunk}).get("orderbooks", []):
                books[ob["ticker"]] = book_levels(ob)
        except Exception as e:  # one bad batch must not sink the run
            failed += len(chunk)
            log(f"orderbooks {chunk[0]}..: {e}")
    rows = [r for p in progs if p["market_ticker"] in books
            for r in [thin_side_estimate(p, *books[p["market_ticker"]], now)] if r]
    # closed or halted markets have empty books but pay nothing: keep only open ones
    status = {}
    thin = sorted({r["ticker"] for r in rows})
    for i in range(0, len(thin), 100):
        data = kalshi._get("/markets", {"tickers": ",".join(thin[i:i + 100]), "limit": 100})
        status.update({m["ticker"]: m.get("status") for m in data.get("markets", [])})
    rows = [r for r in rows if status.get(r["ticker"]) == "active"]
    return {"at": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "programs": len(progs),
            "pool_day": round(sum(reward_per_day(p) for p in progs), 2), "failed": failed,
            "thin": rows}


def write(snap: dict, root: str) -> str:
    """One JSON line per run in lip/<date>.jsonl under `root`."""
    d = os.path.join(root, "lip")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, snap["at"][:10] + ".jsonl")
    with open(path, "a") as f:
        f.write(json.dumps(snap, separators=(",", ":")) + "\n")
    return path


def report(root: str) -> dict:
    """Sum the hourly estimates per UTC day; also how long thin books stay thin."""
    days = defaultdict(lambda: {"runs": 0, "est": 0.0, "markets": set()})
    seen = defaultdict(int)
    runs = 0
    for path in sorted(glob.glob(os.path.join(root, "lip", "*.jsonl"))):
        with open(path) as f:
            for line in f:
                s = json.loads(line)
                runs += 1
                d = days[s["at"][:10]]
                d["runs"] += 1
                for r in s["thin"]:
                    d["est"] += r["est_hour"]
                    d["markets"].add(r["ticker"])
                    seen[r["program"]] += 1
    out = {"runs": runs, "days": {}}
    for k, d in sorted(days.items()):
        # scale to a full day when some hourly runs were missed
        out["days"][k] = {"runs": d["runs"], "est_usd": round(d["est"] * 24 / max(d["runs"], 1), 2),
                          "markets": len(d["markets"])}
    if seen:
        hours = sorted(seen.values())
        out["thin_hours_median"] = hours[len(hours) // 2]
    return out


# ---------------------------------------------------------------- paper trading

LIP_SCHEMA = """
CREATE TABLE IF NOT EXISTS lip_orders (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  strategy TEXT NOT NULL, ticker TEXT NOT NULL, side TEXT NOT NULL,
  price REAL NOT NULL, size REAL NOT NULL,
  placed_at TEXT NOT NULL, checked_at TEXT NOT NULL,
  last_share TEXT NOT NULL DEFAULT '{}',
  filled REAL NOT NULL DEFAULT 0,
  status TEXT NOT NULL DEFAULT 'resting',
  closed_at TEXT
);
CREATE TABLE IF NOT EXISTS lip_rewards (
  order_id INTEGER NOT NULL, strategy TEXT NOT NULL, ticker TEXT NOT NULL, program TEXT NOT NULL,
  from_ts TEXT NOT NULL, to_ts TEXT NOT NULL, usd REAL NOT NULL
);
"""

# capital each paper strategy may tie up in resting orders plus filled positions
BUDGETS = {"lip_1c": 1000.0}
MIN_LEFT = 2 * 3600  # seconds a pool must still run when we post (runs are ~hourly)


def side_share(lv: list, target: float, disc: float, mine: tuple | None) -> float | None:
    """Our normalized score on one side of the book, per the help-page rules.

    Reference price: walking down from the best bid, the first level where cumulative
    size reaches Target/5. Bids are taken best-first (ours behind others at the same
    price) until Target is reached; each scores size x Discount^ticks below reference.
    Returns None when the side cannot reach Target (the snapshot is then excluded).
    """
    book = [(p, q, False) for p, q in lv] + ([(mine[0], mine[1], True)] if mine else [])
    book.sort(key=lambda x: -x[0])  # stable: ours stays behind existing bids at its price
    cum, ref = 0.0, None
    for p, q, _ in book:
        cum += q
        if cum >= target / 5:
            ref = p
            break
    if ref is None:
        return None
    cum, total, ours = 0.0, 0.0, 0.0
    for p, q, is_ours in book:
        if cum >= target:
            break
        cum += q
        s = q * disc ** max(0, round((ref - p) * 100))
        total += s
        ours += s if is_ours else 0.0
    if cum < target:
        return None
    return ours / total if total else 0.0


def pool_fraction(p: dict, yes: list, no: list, side: str, price: float, size: float) -> float:
    """Our fraction of the program's pool for one snapshot (0 if the snapshot is excluded)."""
    target, disc = float(p["target_size_fp"]), p["discount_factor_bps"] / 10000
    mine = (price, size)
    sy = side_share(yes, target, disc, mine if side == "yes" else None)
    sn = side_share(no, target, disc, mine if side == "no" else None)
    if sy is None or sn is None:
        return 0.0
    return (sy + sn) / 2


def quotes(strategy: str, progs: list[dict], yes: list, no: list) -> list[tuple]:
    """(side, price) bids a strategy would rest on this market's thin side(s).

    The thin side is almost always the near-certain loser (nobody bids for a worthless
    contract), so 1c is about its fair value: the side's best bid if it is 5c or less,
    or 1c if empty. A fill costs about 1c per contract, at most Target x price.
    """
    target = max(float(p["target_size_fp"]) for p in progs)
    out = []
    for side, lv in (("yes", yes), ("no", no)):
        if sum(q for _, q in lv) < target:
            px = max((x for x, _ in lv), default=0.01)
            if px <= 0.05:
                out.append((side, px))
    return out


def _fill_volume(trades: list[dict], side: str, price: float) -> float:
    """Contracts that traded through our bid: takers selling `side` at or below `price`."""
    vol = 0.0
    for t in trades:
        y = float(t.get("yes_price_dollars") or 0)
        n = float(t.get("count_fp") or t.get("count") or 0)
        if side == "yes" and t.get("taker_side") == "no" and 0 < y <= price + 1e-9:
            vol += n
        elif side == "no" and t.get("taker_side") == "yes" and y >= 1 - price - 1e-9:
            vol += n
    return vol


def _books(kalshi, tickers, log) -> dict:
    books = {}
    tickers = sorted(tickers)
    for i in range(0, len(tickers), 100):
        try:
            for ob in kalshi._get("/markets/orderbooks", {"tickers": tickers[i:i + 100]}).get("orderbooks", []):
                books[ob["ticker"]] = book_levels(ob)
        except Exception as e:
            log(f"orderbooks {tickers[i]}..: {e}")
    return books


def _markets(kalshi, tickers) -> dict:
    out = {}
    tickers = sorted(tickers)
    for i in range(0, len(tickers), 100):
        data = kalshi._get("/markets", {"tickers": ",".join(tickers[i:i + 100]), "limit": 100})
        out.update({m["ticker"]: m for m in data.get("markets", [])})
    return out


def run_lip_paper(kalshi, ledger, now: datetime | None = None, budgets: dict = BUDGETS,
                  log=print) -> dict:
    """One hourly step: accrue rewards and fills on resting paper orders, then post new ones.

    Rewards for the interval since the last check use the smaller of our pool fraction at
    its start and end, so an hour only pays if the book qualified at both ends.
    """
    from .mm import maker_fee, trades_after
    from .strategy import Signal
    now = now or datetime.now(timezone.utc)
    stamp = now.isoformat()
    db = ledger.db
    db.executescript(LIP_SCHEMA)
    by_ticker = defaultdict(list)
    for p in active_programs(kalshi, now):
        by_ticker[p["market_ticker"]].append(p)
    orders = [dict(r) for r in db.execute("SELECT * FROM lip_orders WHERE status='resting'")]
    books = _books(kalshi, set(by_ticker) | {o["ticker"] for o in orders}, log)
    thin = {t for t, ps in by_ticker.items() if t in books
            and min(sum(q for _, q in books[t][0]), sum(q for _, q in books[t][1]))
            < max(float(p["target_size_fp"]) for p in ps)}
    markets = _markets(kalshi, thin | {o["ticker"] for o in orders})
    out = {"accrued": 0.0, "fills": 0, "closed": 0, "placed": 0}

    for o in orders:
        t, m = o["ticker"], markets.get(o["ticker"], {})
        if t not in books and m.get("status") == "active":
            continue  # book fetch failed: leave the order alone until the next run
        since = _ts(o["checked_at"])
        prev = json.loads(o["last_share"])
        share = {}
        if t in books and m.get("status") == "active":
            for p in by_ticker.get(t, []):
                share[p["id"]] = pool_fraction(p, *books[t], o["side"], o["price"], o["size"])
        for p in by_ticker.get(t, []):
            lo, hi = max(since, _ts(p["start_date"])), min(now, _ts(p["end_date"]))
            frac = min(prev.get(p["id"], 0.0), share.get(p["id"], 0.0))
            if hi > lo and frac > 0:
                period = (_ts(p["end_date"]) - _ts(p["start_date"])).total_seconds()
                usd = p["period_reward"] / 10000 * frac * (hi - lo).total_seconds() / period
                db.execute("INSERT INTO lip_rewards VALUES (?,?,?,?,?,?,?)",
                           (o["id"], o["strategy"], t, p["id"], o["checked_at"], stamp, usd))
                out["accrued"] += usd
        try:
            vol = _fill_volume(trades_after(kalshi, t, None, int(since.timestamp()), cache=False),
                               o["side"], o["price"])
        except Exception as e:
            log(f"trades {t}: {e}")
            vol = 0.0
        q = int(min(vol, o["size"]))
        if q >= 1:  # the filled part becomes a position; the bot re-posts it to keep its size
            sig = Signal(t, m.get("event_ticker", t.rsplit("-", 1)[0]), o["side"], o["price"],
                         prob=o["price"], edge=0.0, contracts=q, fee=maker_fee(o["price"], q))
            ledger.record("lip", sig, strategy=o["strategy"])
            db.execute("UPDATE lip_orders SET filled=filled+? WHERE id=?", (q, o["id"]))
            out["fills"] += 1
            log(f"{o['strategy']}: FILLED {q}x {o['side'].upper()} {t} @ ${o['price']:.2f}")
        done = m.get("status") != "active" or not by_ticker.get(t) or not any(share.values())
        db.execute("UPDATE lip_orders SET checked_at=?, last_share=?, status=?, closed_at=? WHERE id=?",
                   (stamp, json.dumps(share), "closed" if done else "resting",
                    stamp if done else None, o["id"]))
        out["closed"] += done

    for strategy, budget in budgets.items():
        used = capital(db, strategy)
        have = {(o["ticker"], o["side"]) for o in orders if o["strategy"] == strategy}
        cands = []
        for t in thin:
            m = markets.get(t, {})
            if m.get("status") != "active":
                continue
            ps = by_ticker[t]
            for side, px in quotes(strategy, ps, *books[t]):
                if (t, side) in have:
                    continue
                size = max(float(p["target_size_fp"]) for p in ps)
                share = {p["id"]: pool_fraction(p, *books[t], side, px, size) for p in ps}
                # an hourly paper run can only measure pools that outlast the next check
                rate = sum(p["period_reward"] / 10000 * share[p["id"]]
                           / (_ts(p["end_date"]) - _ts(p["start_date"])).total_seconds()
                           for p in ps if (_ts(p["end_date"]) - now).total_seconds() >= MIN_LEFT)
                if rate > 0:
                    cands.append((rate / (px * size), t, side, px, size, share))
        for _, t, side, px, size, share in sorted(cands, reverse=True):
            if used + px * size > budget:
                continue
            db.execute("INSERT INTO lip_orders (strategy, ticker, side, price, size, placed_at, checked_at,"
                       " last_share) VALUES (?,?,?,?,?,?,?,?)",
                       (strategy, t, side, px, size, stamp, stamp, json.dumps(share)))
            used += px * size
            out["placed"] += 1
    db.commit()
    return out


def capital(db, strategy: str) -> float:
    """Cash tied up: resting paper orders plus filled positions not yet settled."""
    rest = db.execute("SELECT COALESCE(SUM(price*size),0) FROM lip_orders WHERE strategy=? AND status='resting'",
                      (strategy,)).fetchone()[0]
    pos = db.execute("SELECT COALESCE(SUM(price*contracts+fee),0) FROM trades WHERE strategy=? AND status='open'",
                     (strategy,)).fetchone()[0]
    return rest + pos


def paper_summary(db, now: datetime | None = None) -> list[dict]:
    """Per LIP strategy: rewards (total, last 24h), fill P&L, capital, open orders."""
    db.executescript(LIP_SCHEMA)
    now = now or datetime.now(timezone.utc)
    day_ago = datetime.fromtimestamp(now.timestamp() - 86400, timezone.utc).isoformat()
    out = []
    for strategy in BUDGETS:
        r = db.execute("SELECT COALESCE(SUM(usd),0), COALESCE(SUM(CASE WHEN to_ts>? THEN usd END),0),"
                       " MIN(from_ts) FROM lip_rewards WHERE strategy=?", (day_ago, strategy)).fetchone()
        f = db.execute("SELECT COUNT(*), COALESCE(SUM(contracts),0), COALESCE(SUM(pnl),0),"
                       " SUM(status='settled') FROM trades WHERE strategy=?", (strategy,)).fetchone()
        n = db.execute("SELECT COUNT(*) FROM lip_orders WHERE strategy=? AND status='resting'",
                       (strategy,)).fetchone()[0]
        out.append({"strategy": strategy, "rewards": round(r[0], 2), "rewards_24h": round(r[1], 2),
                    "since": r[2], "fills": f[0], "filled_contracts": f[1], "fill_pnl": round(f[2], 2),
                    "fills_settled": f[3] or 0, "capital": round(capital(db, strategy), 2), "resting": n})
    return out
