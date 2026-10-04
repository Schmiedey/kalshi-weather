"""ETF trend-following (Faber 2007): hold each asset class only while it is above its
10-month moving average, otherwise that slice sits in cash.

Universe: US stocks (SPY), international stocks (EFA), Treasuries (IEF), gold (GLD),
commodities (DBC), equal weight. Signals use month-end prices and apply to the next
month, so there is no lookahead. Cash earns 0% here (a conservative simplification).
Prices: Yahoo Finance's public chart endpoint (free, no key, unofficial).
"""
from __future__ import annotations

import json
import math
from datetime import datetime, timezone

import requests

UNIVERSE = ("SPY", "EFA", "IEF", "GLD", "DBC")
SMA_MONTHS = 10
NAV_SCHEMA = """
CREATE TABLE IF NOT EXISTS etf_nav (
  month TEXT PRIMARY KEY,      -- month the return was earned (YYYY-MM)
  nav REAL NOT NULL,           -- paper equity at the end of that month
  weights TEXT NOT NULL        -- weights held during that month (JSON)
);
"""


def monthly_closes(symbol: str, session=None) -> dict[str, float]:
    """{YYYY-MM: adjusted close} for completed months only."""
    s = session or requests.Session()
    r = s.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
              params={"range": "max", "interval": "1mo"},
              headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    this_month = datetime.now(timezone.utc).strftime("%Y-%m")
    out = {}
    for ts, px in zip(res["timestamp"], res["indicators"]["adjclose"][0]["adjclose"]):
        m = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m")
        if px is not None and m != this_month:
            out[m] = float(px)
    return out


def signals(closes: dict[str, dict[str, float]], month: str) -> dict[str, float]:
    """Weights to hold during the month AFTER `month`, from data through `month`'s close."""
    w = {}
    for sym, series in closes.items():
        months = [m for m in sorted(series) if m <= month][-SMA_MONTHS:]
        if len(months) == SMA_MONTHS and months[-1] == month:
            sma = sum(series[m] for m in months) / SMA_MONTHS
            w[sym] = (1 / len(closes)) if series[month] > sma else 0.0
        else:
            w[sym] = 0.0
    return w


def _next_month(m: str) -> str:
    y, mo = int(m[:4]), int(m[5:])
    return f"{y + mo // 12}-{mo % 12 + 1:02d}"


def backtest(closes: dict[str, dict[str, float]], start: str) -> list[dict]:
    """Monthly path from `start`: [{month, ret, ret_buyhold, weights}]."""
    months = sorted(set.intersection(*(set(s) for s in closes.values())))
    out = []
    for prev, cur in zip(months, months[1:]):
        if cur < start or _next_month(prev) != cur:
            continue
        w = signals(closes, prev)
        rets = {s: closes[s][cur] / closes[s][prev] - 1 for s in closes}
        out.append({"month": cur, "ret": sum(w[s] * rets[s] for s in closes),
                    "ret_buyhold": sum(rets.values()) / len(closes), "weights": w})
    return out


def stats(rets: list[float]) -> dict:
    if not rets:
        return {}
    nav, peak, dd = 1.0, 1.0, 0.0
    for r in rets:
        nav *= 1 + r
        peak = max(peak, nav)
        dd = max(dd, 1 - nav / peak)
    years = len(rets) / 12
    mean = sum(rets) / len(rets)
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)) if len(rets) > 1 else 0
    return {"months": len(rets), "cagr": round(nav ** (1 / years) - 1, 4),
            "vol": round(sd * math.sqrt(12), 4), "max_drawdown": round(dd, 4),
            "sharpe0": round(mean / sd * math.sqrt(12), 2) if sd else None}


def fetch_all(session=None) -> dict[str, dict[str, float]]:
    return {s: monthly_closes(s, session) for s in UNIVERSE}


def update_paper(db, closes: dict[str, dict[str, float]], start_nav: float = 10_000.0,
                 start_month: str | None = None) -> list[dict]:
    """Advance the paper NAV through every completed month not yet recorded."""
    db.executescript(NAV_SCHEMA)
    rows = db.execute("SELECT month, nav FROM etf_nav ORDER BY month").fetchall()
    common = sorted(set.intersection(*(set(s) for s in closes.values())))
    if not common:
        return []
    if not rows:                      # start: hold the signal from the latest month end
        first = start_month or common[-1]
        db.execute("INSERT INTO etf_nav VALUES (?,?,?)",
                   (first, start_nav, json.dumps({})))          # starting point; no return yet
        db.commit()
        return [{"month": first, "nav": start_nav}]
    last, nav = rows[-1][0], rows[-1][1]
    added = []
    for cur in [m for m in common if m > last]:
        prev = max(m for m in common if m < cur)
        w = signals(closes, prev)
        ret = sum(w[s] * (closes[s][cur] / closes[s][prev] - 1) for s in closes)
        nav *= 1 + ret
        db.execute("INSERT INTO etf_nav VALUES (?,?,?)", (cur, nav, json.dumps(w)))
        added.append({"month": cur, "nav": nav, "ret": ret})
    db.commit()
    return added
