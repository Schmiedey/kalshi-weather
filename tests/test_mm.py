"""Maker longshot: order placement, fills from the trade tape, paper lifecycle."""
from datetime import datetime, timezone

from kalshi_weather.kalshi import Bracket
from kalshi_weather.ledger import Ledger
from kalshi_weather.mm import MAKER_STRATEGY, check_fills, filled_qty, maker_order
from kalshi_weather.polymarket import outcome
from kalshi_weather.strategy import Signal


def test_maker_order_rules():
    assert maker_order(0.01, 0.04) == 0.03          # 1c under the ask, above the bid
    assert maker_order(0.02, 0.03) is None           # 1c spread: no room
    assert maker_order(None, 0.03) == 0.02           # no bid is fine for a maker
    assert maker_order(0.01, 0.08) is None           # not a longshot
    assert maker_order(None, 0.01) is None


def test_fills_count_only_yes_takers_at_or_above_offer():
    tape = [{"taker_side": "yes", "yes_price_dollars": "0.0400", "count_fp": "6.00"},
            {"taker_side": "yes", "yes_price_dollars": "0.0200", "count_fp": "50.00"},   # below offer
            {"taker_side": "no", "yes_price_dollars": "0.0500", "count_fp": "50.00"},    # sellers
            {"taker_side": "yes", "yes_price_dollars": "0.0300", "count_fp": "2.50"}]
    assert filled_qty(tape, 0.03, 10) == 8
    assert filled_qty(tape, 0.03, 5) == 5


class FakeK:
    def __init__(self, tape, close):
        self.tape, self.close = tape, close

    def market(self, t):
        return Bracket(t, "E", "between", 70, 71, None, self.close, None, None)

    def _get(self, path, params, **kw):
        return {"trades": self.tape, "cursor": ""}

    def historical_cutoff(self):
        return None


def _rest(led, ticker):
    tid = led.record("high_nyc", Signal(ticker, "E", "no", 0.97, 0.97, 0.0, 10, 0.01), strategy=MAKER_STRATEGY)
    led.db.execute("UPDATE trades SET status='resting', opened_at=? WHERE id=?",
                   ("2026-10-04T22:00:00+00:00", tid))
    led.db.commit()
    return tid


def test_resting_order_lifecycle(tmp_path):
    led = Ledger(str(tmp_path / "p.db"))
    now = datetime(2026, 10, 6, 6, tzinfo=timezone.utc)
    a, b = _rest(led, "A"), _rest(led, "B")
    full = [{"taker_side": "yes", "yes_price_dollars": "0.0300", "count_fp": "12"}]
    assert check_fills(FakeK(full, "2026-10-07T04:59:00Z"), led, now=now, log=lambda *x: None)["filled"] == 2
    assert led.summary(MAKER_STRATEGY)["total_trades"] == 2
    led2 = Ledger(str(tmp_path / "q.db"))
    _rest(led2, "C")
    out = check_fills(FakeK([], "2026-10-05T04:59:00Z"), led2, now=now, log=lambda *x: None)
    assert out == {"filled": 0, "expired": 1, "resting": 0}
    assert led2.summary(MAKER_STRATEGY)["total_trades"] == 0     # expired orders never traded


def test_polymarket_outcome():
    assert outcome({"outcomePrices": '["0", "1"]'}) == "no"
    assert outcome({"outcomePrices": '["1", "0"]'}) == "yes"
    assert outcome({"outcomePrices": '["0.4", "0.6"]'}) is None
