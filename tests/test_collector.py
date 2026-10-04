"""Collector writes and reads gzipped hourly snapshots."""
from datetime import datetime, timezone

from kalshi_weather.collector import read_archive, snapshot, write
from kalshi_weather.markets import MARKETS


class FakeK:
    def _get(self, path, params, **kw):
        if params["series_ticker"] == "BAD":
            raise RuntimeError("down")
        return {"markets": [{"ticker": params["series_ticker"] + "-26OCT05-B70.5", "yes_bid_dollars": "0.0200",
                             "yes_ask_dollars": "0.0300", "yes_bid_size_fp": "120.00", "extra": 1}]}


def test_snapshot_roundtrip_and_survives_a_failing_series(tmp_path):
    from kalshi_weather.markets import Market
    ms = [MARKETS["high_nyc"], Market("bad", "BAD", "America/New_York", "high"), MARKETS["low_sea"]]
    now = datetime(2026, 10, 4, 19, 37, tzinfo=timezone.utc)
    rows = snapshot(FakeK(), ms, now=now, log=lambda *a: None)
    assert [r["market"] for r in rows] == ["high_nyc", "low_sea"]
    assert rows[0]["yes_bid_size_fp"] == "120.00" and "extra" not in rows[0]
    path = write(rows, str(tmp_path), now=now)
    assert path.endswith("archive/2026-10-04/1937.jsonl.gz")
    assert list(read_archive(str(tmp_path))) == rows
