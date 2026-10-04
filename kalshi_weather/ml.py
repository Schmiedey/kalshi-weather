"""Learned models for bracket outcomes, evaluated walk-forward against the market.

Features are taken at the decision hour on the day before the event (default 5pm local)
from the cached day-before candles, so nothing after the decision is used:

  logit_mid, spread, bid_missing   price and liquidity of this bracket
  mid_change                       move in mid since the reference hour (noon)
  rank, dist_fav, n_brackets       where the bracket sits in its event
  overround                        sum of mids across the event
  is_tail, is_low, month           bracket type, market type, season

Two models: `calib` (logistic regression on price and spread - a learned calibration
curve) and `gbm` (gradient boosting on everything). Each is refit weekly on events at
least two days older than the week it predicts, and must beat the market's own Brier
score and make money after fees before it is trusted.
"""
from __future__ import annotations

import math
from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone

from .model import bracket_bounds
from .strategy import taker_fee

FEATURES = {
    "calib": ["logit_mid", "spread"],
    "gbm": ["logit_mid", "spread", "bid_missing", "mid_change", "rank", "dist_fav", "n_brackets",
            "overround", "is_tail", "is_low", "month"],
}


def _logit(p: float) -> float:
    p = min(max(p, 0.005), 0.995)
    return math.log(p / (1 - p))


def _mid(bid, ask):
    if bid is None and ask is None:
        return None
    return ((bid or 0.0) + (ask if ask is not None else 1.0)) / 2


def event_features(brackets: list[dict], month: int, is_low: bool) -> list[dict]:
    """brackets: dicts with ticker, lo, hi, bid, ask, ref_bid, ref_ask, result. Adds feature keys."""
    rows = [b for b in brackets if _mid(b["bid"], b["ask"]) is not None]
    if len(rows) < 2:
        return []
    rows.sort(key=lambda b: b["lo"])
    for b in rows:
        b["mid"] = _mid(b["bid"], b["ask"])
    order = sorted(range(len(rows)), key=lambda i: -rows[i]["mid"])
    fav = order[0]
    over = sum(b["mid"] for b in rows)
    for r, i in enumerate(order):
        b = rows[i]
        ref = _mid(b["ref_bid"], b["ref_ask"])
        b.update({
            "logit_mid": _logit(b["mid"]),
            "spread": (b["ask"] if b["ask"] is not None else 1.0) - (b["bid"] or 0.0),
            "bid_missing": float(b["bid"] is None),
            "mid_change": (b["mid"] - ref) if ref is not None else 0.0,
            "rank": float(r), "dist_fav": float(abs(i - fav)), "n_brackets": float(len(rows)),
            "overround": over, "is_tail": float(b["lo"] == -math.inf or b["hi"] == math.inf),
            "is_low": float(is_low), "month": float(month),
        })
    return rows


def build_rows(kalshi, markets, start: date, end: date, hour: int = 17, ref_hour: int = 12,
               log=print) -> list[dict]:
    """One row per settled bracket with features at `hour` local on the day before."""
    from zoneinfo import ZoneInfo
    from .kalshi import event_date
    from .longshot import day_before_candles, quote_from_candles
    out = []
    for m in markets:
        tz = ZoneInfo(m.tz)
        min_ts = int(datetime.combine(start, time(0), timezone.utc).timestamp())
        max_ts = int(datetime.combine(end + timedelta(days=2), time(0), timezone.utc).timestamp())
        events = defaultdict(list)
        for b in kalshi.settled_markets(m.series, min_close_ts=min_ts, max_close_ts=max_ts, cache=True):
            d = event_date(b.event_ticker)
            if d and start <= d <= end and b.result and bracket_bounds(b):
                events[(d, b.event_ticker)].append(b)
        n = 0
        for (d, ev), bs in events.items():
            prev = d - timedelta(days=1)
            ts = int(datetime.combine(prev, time(hour), tz).timestamp())
            ts_ref = int(datetime.combine(prev, time(ref_hour), tz).timestamp())
            items = []
            for b in bs:
                cs = day_before_candles(kalshi, m, b, d)
                bid, ask = quote_from_candles(cs, ts)
                rb, ra = quote_from_candles(cs, ts_ref)
                lo, hi = bracket_bounds(b)
                items.append({"date": d.isoformat(), "market": m.key, "event": ev, "ticker": b.ticker,
                              "lo": lo, "hi": hi, "bid": bid, "ask": ask, "ref_bid": rb, "ref_ask": ra,
                              "y": int(b.result == "yes")})
            rows = event_features(items, d.month, m.kind == "low")
            out += rows
            n += len(rows)
        log(f"[{m.key}] {n} bracket rows")
    return out


def make_model(kind: str):
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.linear_model import LogisticRegression
    if kind == "calib":
        return LogisticRegression(C=1.0, max_iter=1000)
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200,
                                          l2_regularization=1.0, min_samples_leaf=50)


def trade_for(row: dict, p: float, min_edge: float = 0.02, contracts: int = 10) -> dict | None:
    """Best of buying YES at the ask or NO at 1 - bid, if the model's edge clears `min_edge` after fees."""
    best = None
    for side, price, prob in (("yes", row["ask"], p),
                              ("no", None if row["bid"] is None else round(1 - row["bid"], 4), 1 - p)):
        if price is None or not 0.01 <= price <= 0.99:
            continue
        fee = taker_fee(price, contracts)
        edge = prob - price - fee / contracts
        if edge >= min_edge and (best is None or edge > best[3]):
            best = (side, price, fee, edge)
    if not best:
        return None
    side, price, fee, edge = best
    won = (side == "yes") == bool(row["y"])
    pnl = round(contracts * ((1 - price) if won else -price) - fee, 4)
    return {"date": row["date"], "city": row["market"], "ticker": row["ticker"], "side": side,
            "price": price, "contracts": contracts, "fee": fee, "won": won, "pnl": pnl,
            "model_p": round(p, 4), "edge": round(edge, 4)}


def walk_forward(rows: list[dict], kind: str, warmup_days: int = 60, step_days: int = 7,
                 gap_days: int = 2, min_edge: float = 0.02) -> dict:
    """Weekly refits, each trained only on events >= gap_days older than the week predicted."""
    import numpy as np
    feats = FEATURES[kind]
    rows = sorted(rows, key=lambda r: r["date"])
    dates = sorted({r["date"] for r in rows})
    if len(dates) <= warmup_days:
        return {"kind": kind, "error": "not enough history"}
    first = date.fromisoformat(dates[0])
    fold = first + timedelta(days=warmup_days)
    last = date.fromisoformat(dates[-1])
    preds, trades = [], []
    while fold <= last:
        nxt = fold + timedelta(days=step_days)
        cut = (fold - timedelta(days=gap_days)).isoformat()
        train = [r for r in rows if r["date"] <= cut]
        test = [r for r in rows if fold.isoformat() <= r["date"] < nxt.isoformat()]
        if test and len({r["y"] for r in train}) == 2:
            model = make_model(kind)
            model.fit(np.array([[r[f] for f in feats] for r in train]), np.array([r["y"] for r in train]))
            ps = model.predict_proba(np.array([[r[f] for f in feats] for r in test]))[:, 1]
            for r, p in zip(test, ps):
                preds.append((float(p), r["mid"], r["y"]))
                t = trade_for(r, float(p), min_edge)
                if t:
                    trades.append(t)
        fold = nxt
    n = len(preds)
    model_brier = sum((p - y) ** 2 for p, _, y in preds) / n if n else None
    market_brier = sum((m - y) ** 2 for _, m, y in preds) / n if n else None
    return {"kind": kind, "scored": n, "model_brier": model_brier, "market_brier": market_brier,
            "trades": trades}


def fit_final(rows: list[dict], kind: str):
    """Fit on all rows (for live use) and return the model."""
    import numpy as np
    feats = FEATURES[kind]
    model = make_model(kind)
    model.fit(np.array([[r[f] for f in feats] for r in rows]), np.array([r["y"] for r in rows]))
    return model
