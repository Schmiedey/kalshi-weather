"""Polymarket forward paper test against fake gamma/clob clients (no network)."""
import json
from datetime import datetime, timezone

from kalshi_weather.ledger import Ledger
from kalshi_weather.paper import settle_open
from kalshi_weather.polymarket import POLY_STRATEGY, _city, run_poly_paper, settle_poly


class Fake:
    def __init__(self, routes):
        self.routes, self.calls = routes, []

    def _get(self, path, params=None, cache=False):
        self.calls.append((path, params))
        r = self.routes(path, params or {})
        return r


def _market(mid, yes_price, closed=False, outcome=None):
    prices = outcome or [str(yes_price), str(round(1 - yes_price, 3))]
    return {"id": mid, "question": f"q{mid}", "closed": closed, "outcomePrices": json.dumps(prices),
            "clobTokenIds": json.dumps([f"yes{mid}", f"no{mid}"])}


EVENT = {"id": 9, "title": "Highest temperature in NYC on October 5?",
         "markets": [_market("1", 0.02), _market("2", 0.30), _market("3", 0.002), _market("4", 0.03)]}


def test_buys_no_at_live_ask_and_settles(tmp_path):
    led = Ledger(str(tmp_path / "paper.db"))
    books = {"no1": {"asks": [{"price": "0.999", "size": "5"}, {"price": "0.985", "size": "40"}]},
             "no4": {"asks": []}}
    gamma = Fake(lambda p, q: [EVENT] if p == "/events" and q.get("offset") == 0 else [])
    clob = Fake(lambda p, q: books.get(q["token_id"], {}))
    now = datetime(2026, 10, 4, 12, 7, tzinfo=timezone.utc)
    assert run_poly_paper(gamma, clob, led, 10, now=now, log=lambda *a: None) == 1   # only market 1
    t = led.db.execute("SELECT * FROM trades").fetchone()
    assert (t["ticker"], t["side"], t["price"], t["contracts"], t["strategy"]) == ("PM-1", "no", 0.985, 10, POLY_STRATEGY)
    assert t["city"] == "nyc high" and _city("Lowest temperature in London on October 5?") == "london low"
    assert run_poly_paper(gamma, clob, led, 10, now=now, log=lambda *a: None) == 0   # no duplicates
    lo, hi = gamma.calls[0][1]["end_date_min"], gamma.calls[0][1]["end_date_max"]
    assert (lo, hi) == ("2026-10-05T11:02:00Z", "2026-10-05T12:47:00Z")

    class NoKalshi:                       # Kalshi settle must skip Polymarket trades
        def market(self, ticker):
            raise AssertionError("Kalshi asked about a Polymarket trade")
    assert settle_open(NoKalshi(), led, log=lambda *a: None) == 0

    open_g = Fake(lambda p, q: {"markets": [_market("1", 0.01)]})
    assert settle_poly(open_g, led, log=lambda *a: None) == 0                         # not closed yet
    done_g = Fake(lambda p, q: {"markets": [_market("1", 0.0, closed=True, outcome=["0", "1"])]})
    assert settle_poly(done_g, led, log=lambda *a: None) == 1
    assert [c[0] for c in done_g.calls] == ["/events/9"]
    t = led.db.execute("SELECT * FROM trades").fetchone()
    assert t["status"] == "settled" and t["result"] == "no" and round(t["pnl"], 2) == 0.15
