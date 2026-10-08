
import pytest

from kalshi_weather.kalshi import book_quotes, event_date, parse_market, _price
from kalshi_weather.strategy import settle_pnl, taker_fee


def test_fee_matches_kalshi_formula():
    assert taker_fee(0.50, 10) == 0.18   # 0.07*10*0.25 = 0.175 -> 0.18
    assert taker_fee(0.50, 100) == 1.75
    assert taker_fee(0.10, 1) == 0.01    # rounds up


def test_price_formats():
    assert _price({"yes_ask": 45}, "yes_ask") == 0.45
    assert _price({"yes_ask_dollars": "0.4500"}, "yes_ask") == 0.45
    assert _price({"yes_ask": 0}, "yes_ask") is None
    assert _price({"yes_ask": 100}, "yes_ask") is None


def test_event_date():
    assert str(event_date("KXHIGHNY-25OCT03")) == "2025-10-03"
    assert event_date("garbage") is None


def test_parse_market():
    b = parse_market({"ticker": "KXHIGHNY-25OCT03-B70.5", "event_ticker": "KXHIGHNY-25OCT03",
                      "strike_type": "between", "floor_strike": 70, "cap_strike": 71,
                      "result": "yes", "yes_bid": 40, "yes_ask": 44, "yes_sub_title": "70° to 71°"})
    assert (b.floor, b.cap, b.result, b.yes_ask, b.no_ask) == (70, 71, "yes", 0.44, 0.60)


def test_orderbook_cents_and_dollars():
    q = book_quotes({"orderbook": {"yes": [[30, 5], [38, 10]], "no": [[55, 7], [58, 3]]}})
    assert q == {"yes_bid": 0.38, "yes_bid_qty": 10, "yes_ask": 0.42, "yes_ask_qty": 3}
    q2 = book_quotes({"orderbook_fp": {"yes_dollars": [["0.38", "10"]], "no_dollars": [["0.58", "3"]]}})
    assert q2["yes_bid"] == 0.38 and q2["yes_ask"] == 0.42
    assert book_quotes({"orderbook": {"yes": None, "no": None}})["yes_ask"] is None


def test_settle_pnl():
    assert settle_pnl("yes", 0.30, 10, 0.15, "yes") == pytest.approx(6.85)
    assert settle_pnl("yes", 0.30, 10, 0.15, "no") == pytest.approx(-3.15)
    assert settle_pnl("no", 0.70, 10, 0.15, "no") == pytest.approx(2.85)
