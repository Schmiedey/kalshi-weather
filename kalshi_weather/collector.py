"""Hourly market snapshots for every US daily temperature series.

One /markets call per series returns best bid/ask, the size at each, volume and open
interest for every open bracket - things Kalshi's candle history does not keep (sizes).
Each run writes one gzipped JSON-lines file; the workflow commits it to the `data` branch.
"""
from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timezone

from .markets import MARKETS

FIELDS = ("ticker", "event_ticker", "close_time", "status", "yes_bid_dollars", "yes_ask_dollars",
          "yes_bid_size_fp", "yes_ask_size_fp", "no_bid_dollars", "no_ask_dollars",
          "last_price_dollars", "volume_fp", "volume_24h_fp", "open_interest_fp",
          "strike_type", "floor_strike", "cap_strike")


def snapshot(kalshi, markets=None, now: datetime | None = None, log=print) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    rows = []
    for m in (markets or MARKETS.values()):
        try:
            data = kalshi._get("/markets", {"series_ticker": m.series, "status": "open", "limit": 1000})
        except Exception as e:                     # one bad series must not lose the hour
            log(f"[{m.key}] snapshot failed: {e!r}")
            continue
        for mk in (data or {}).get("markets", []):
            rows.append({"ts": now.isoformat(timespec="seconds"), "market": m.key, "series": m.series}
                        | {f: mk.get(f) for f in FIELDS})
    return rows


def write(rows: list[dict], root: str, now: datetime | None = None) -> str:
    """archive/<date>/<HHMM>.jsonl.gz under `root`."""
    now = now or datetime.now(timezone.utc)
    d = os.path.join(root, "archive", now.strftime("%Y-%m-%d"))
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, now.strftime("%H%M") + ".jsonl.gz")
    with gzip.open(path, "wt") as f:
        for r in rows:
            f.write(json.dumps(r, separators=(",", ":")) + "\n")
    return path


def read_archive(root: str):
    """Yield every snapshot row stored under `root`/archive, oldest first."""
    base = os.path.join(root, "archive")
    for day in sorted(os.listdir(base)) if os.path.isdir(base) else []:
        for name in sorted(os.listdir(os.path.join(base, day))):
            with gzip.open(os.path.join(base, day, name), "rt") as f:
                for line in f:
                    yield json.loads(line)
