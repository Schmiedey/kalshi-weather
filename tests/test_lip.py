"""Liquidity-incentive tracker: thin-side share math, paging, report."""
from datetime import datetime, timezone

from kalshi_weather import lip

NOW = datetime(2026, 10, 4, 12, tzinfo=timezone.utc)

P = {"id": "p1", "market_ticker": "KXT-26OCT05-A", "incentive_type": "liquidity",
     "incentive_description": "series_lip", "target_size_fp": "1000.00", "period_reward": 1000000,
     "start_date": "2026-10-04T00:00:00Z", "end_date": "2026-10-05T00:00:00Z", "discount_factor_bps": 5000}


def test_empty_side_gets_half_the_pool_at_one_cent():
    r = lip.thin_side_estimate(P, yes=[], no=[(0.97, 3000)], now=NOW)
    assert r["thin"] == ["yes"] and r["share"] == 0.5 and r["at_risk"] == 10.0
    assert r["reward_day"] == 100.0 and abs(r["est_hour"] - 100 / 24 / 2) < 1e-3


def test_short_period_credits_only_what_is_left():
    p = dict(P, start_date="2026-10-04T11:00:00Z", end_date="2026-10-04T12:30:00Z")
    r = lip.thin_side_estimate(p, yes=[], no=[(0.97, 3000)], now=NOW)
    assert abs(r["est_hour"] - 100 * 0.5 * (30 / 90)) < 1e-3


def test_partly_filled_side_and_two_sided_book():
    r = lip.thin_side_estimate(P, yes=[(0.03, 250), (0.02, 250)], no=[(0.95, 2000)], now=NOW)
    assert r["share"] == round(1000 / 1500 / 2, 4) and r["at_risk"] == 30.0
    assert lip.thin_side_estimate(P, yes=[(0.03, 1000)], no=[(0.95, 1000)], now=NOW) is None


class FakeK:
    def _get(self, path, params=None, **kw):
        if path == "/incentive_programs":
            if params.get("cursor"):
                return {"incentive_programs": [dict(P, id="p2", market_ticker="KXT-26OCT05-B")]}
            return {"incentive_programs": [P, dict(P, id="old", end_date="2026-10-04T01:00:00Z")],
                    "next_cursor": "c"}
        if path == "/markets":
            return {"markets": [{"ticker": t, "status": "active"} for t in params["tickers"].split(",")]}
        assert path == "/markets/orderbooks"
        return {"orderbooks": [{"ticker": t, "orderbook_fp": {"no_dollars": [["0.9700", "3000.00"]]}}
                               for t in params["tickers"] if t.endswith("A")]}


def test_snapshot_pages_skips_ended_and_tolerates_a_missing_book(tmp_path):
    s = lip.snapshot(FakeK(), now=NOW, log=lambda *a: None)
    assert s["programs"] == 2 and s["failed"] == 0 and len(s["thin"]) == 1
    lip.write(s, str(tmp_path))
    lip.write(dict(s, at="2026-10-04T13:00:00Z"), str(tmp_path))
    rep = lip.report(str(tmp_path))
    assert rep["runs"] == 2 and rep["days"]["2026-10-04"]["est_usd"] == round(100 / 2, 2)


def test_side_share_reference_discount_and_target_cut():
    # alone on an empty side: full credit
    assert lip.side_share([], 1000, 0.5, (0.01, 1000)) == 1.0
    # 300 ahead at 5c sets the reference (>= 200); our 1c bid is 4 ticks under it
    s = lip.side_share([(0.05, 300)], 1000, 0.5, (0.01, 1000))
    assert abs(s - 1000 * 0.5 ** 4 / (300 + 1000 * 0.5 ** 4)) < 1e-9
    # Target already reached before our level: we do not qualify
    assert lip.side_share([(0.05, 1200)], 1000, 0.5, (0.01, 1000)) == 0.0
    # side that cannot reach Target excludes the snapshot
    assert lip.side_share([(0.05, 300)], 1000, 0.5, None) is None


def test_quotes_only_cheap_thin_sides():
    deep_yes = [(0.97, 3000)]
    assert lip.quotes("lip_1c", [P], deep_yes, []) == [("no", 0.01)]
    assert lip.quotes("lip_1c", [P], deep_yes, [(0.03, 100)]) == [("no", 0.03)]
    assert lip.quotes("lip_1c", [P], deep_yes, [(0.40, 100)]) == []  # not a cheap bid
    assert lip.quotes("lip_1c", [P], deep_yes, [(0.02, 1000)]) == []  # not thin


class PaperK:
    def __init__(self):
        self.status, self.trades = "active", []

    def _get(self, path, params=None, **kw):
        if path == "/incentive_programs":
            return {"incentive_programs": [P]}
        if path == "/markets/orderbooks":
            return {"orderbooks": [{"ticker": P["market_ticker"],
                                    "orderbook_fp": {"yes_dollars": [["0.9700", "3000.00"]]}}]}
        if path == "/markets":
            return {"markets": [{"ticker": P["market_ticker"], "event_ticker": "KXT-26OCT05",
                                 "status": self.status, "last_price_dollars": "0.0200"}]}
        if path == "/markets/trades":
            return {"trades": self.trades}
        raise AssertionError(path)


def test_paper_run_places_accrues_fills_and_closes(tmp_path):
    from datetime import timedelta
    from kalshi_weather.ledger import Ledger
    led, k = Ledger(str(tmp_path / "p.db")), PaperK()
    r = lip.run_lip_paper(k, led, now=NOW, log=lambda *a: None)
    assert r["placed"] == 1  # NO at 1c on the empty side
    # an hour later: half the pool for an hour ($100/day / 24 / 2); one trade reaches 1c
    k.trades = [{"taker_side": "yes", "yes_price_dollars": "0.9800", "count_fp": "70.00"},
                {"taker_side": "yes", "yes_price_dollars": "0.9900", "count_fp": "40.00"}]
    r = lip.run_lip_paper(k, led, now=NOW + timedelta(hours=1), log=lambda *a: None)
    assert abs(r["accrued"] - 100 / 24 / 2) < 1e-6
    assert r["fills"] == 1  # only the YES buy at 99c crosses a 1c NO bid
    t = led.db.execute("SELECT * FROM trades").fetchone()
    assert (t["strategy"], t["side"], t["contracts"], t["price"]) == ("lip_1c", "no", 40, 0.01)
    k.status, k.trades = "closed", []
    r = lip.run_lip_paper(k, led, now=NOW + timedelta(hours=2), log=lambda *a: None)
    assert r["closed"] == 1 and r["placed"] == 0
    s = lip.paper_summary(led.db, now=NOW + timedelta(hours=2))[0]
    assert s["fills"] == 1 and s["resting"] == 0 and abs(s["rewards"] - 100 / 48) < 0.01
