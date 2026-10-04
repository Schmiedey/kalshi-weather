import math
from dataclasses import replace

import pytest

from kalshi_weather.config import StrategyConfig
from kalshi_weather.kalshi import Bracket, book_quotes, event_date, parse_market, _price
from kalshi_weather.model import (actual_high_from_event, bracket_prob, fit_error_model)
from kalshi_weather.strategy import evaluate, settle_pnl, taker_fee
from tests.fakes import make_brackets


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


def test_brackets_form_a_partition():
    from datetime import date
    br = make_brackets("KXHIGHNY", date(2025, 10, 3), 70.3)
    for mu, sigma in [(70.3, 2.0), (61, 3), (80, 1.5)]:
        assert sum(bracket_prob(b, mu, sigma) for b in br) == pytest.approx(1.0, abs=1e-9)


def test_continuity_correction():
    b = Bracket("t", "e", "between", 70, 71, None, None, None, None)
    # P(69.5 < X < 71.5) with X centered at 70.5 -> symmetric
    p = bracket_prob(b, 70.5, 2.0)
    assert p == pytest.approx(math.erf(1 / (2 * math.sqrt(2))), rel=1e-9)
    assert bracket_prob(Bracket("t", "e", "weird", 1, 2, None, None, None, None), 70, 2) is None


def test_fit_error_model():
    assert fit_error_model([1.0] * 5).sigma == 2.5          # not enough data -> default
    em = fit_error_model([1.0, -1.0, 3.0, -1.0] * 5)
    assert em.bias == pytest.approx(0.5, abs=0.1) and em.n == 20
    assert fit_error_model([0.0] * 30).sigma == 1.2         # floor


def test_fit_uses_censored_tails():
    import random
    rng = random.Random(0)
    errs = [rng.gauss(2.0, 2.0) for _ in range(120)]
    # anything above +3 only known as "above +3" (a tail bracket won)
    ivs = [(3.0, math.inf) if e > 3 else (e - 0.5, e + 0.5) for e in errs]
    em = fit_error_model(ivs)
    assert abs(em.bias - 2.0) < 0.5 and abs(em.sigma - 2.0) < 0.5


def test_actual_from_winning_bracket():
    br = [Bracket("a", "e", "between", 70, 71, "yes", None, None, None),
          Bracket("b", "e", "greater", 71, None, "no", None, None, None)]
    assert actual_high_from_event(br) == 70.5


def test_evaluate_picks_yes_no_or_nothing():
    cfg = StrategyConfig(min_edge=0.05)
    b = Bracket("t", "e", "between", 70, 71, None, None, yes_bid=0.30, yes_ask=0.32)
    s = evaluate(b, 0.50, cfg)
    assert s.side == "yes" and s.edge == pytest.approx(0.50 - 0.32 - 0.16 / 10, abs=1e-4)
    s = evaluate(b, 0.10, cfg)
    assert s.side == "no" and s.price == 0.70
    assert evaluate(b, 0.33, cfg) is None
    assert evaluate(replace(b, yes_bid=None, yes_ask=None), 0.9, cfg) is None


def test_settle_pnl():
    assert settle_pnl("yes", 0.30, 10, 0.15, "yes") == pytest.approx(6.85)
    assert settle_pnl("yes", 0.30, 10, 0.15, "no") == pytest.approx(-3.15)
    assert settle_pnl("no", 0.70, 10, 0.15, "no") == pytest.approx(2.85)


def test_known_errors_lag_two_days():
    from datetime import date
    from kalshi_weather.backtest import known_errors
    errs = [(date(2025, 10, d), (d, d)) for d in range(1, 6)]
    # deciding Oct 5 at 3pm Oct 4: Oct 4 is not final yet, Oct 3 is
    assert known_errors(errs, date(2025, 10, 5)) == [(1, 1), (2, 2), (3, 3)]


def test_blend_recovers_informative_model():
    import random
    from kalshi_weather.blend import fit_blend, logit, sigmoid
    rng = random.Random(1)
    rows = []
    for _ in range(3000):
        mkt, mdl = rng.uniform(0.05, 0.95), rng.uniform(0.05, 0.95)
        y = 1 if rng.random() < sigmoid(0.5 * logit(mkt) + 1.0 * logit(mdl)) else 0
        rows.append((logit(mkt), logit(mdl), y))
    a, b, c = fit_blend(rows)
    assert abs(a - 0.5) < 0.15 and abs(b - 1.0) < 0.15 and abs(c) < 0.15


def test_blend_ignores_useless_model():
    import random
    from kalshi_weather.blend import fit_blend, logit
    rng = random.Random(2)
    rows = []
    for _ in range(3000):
        mkt = rng.uniform(0.05, 0.95)
        rows.append((logit(mkt), logit(rng.uniform(0.05, 0.95)), 1 if rng.random() < mkt else 0))
    a, b, _ = fit_blend(rows)
    assert abs(a - 1.0) < 0.15 and abs(b) < 0.1


def test_arb_profit():
    from kalshi_weather.blend import arb_profit
    # YES asks sum to 0.80: buy one of each for 0.80 + fees, collect $1
    assert arb_profit([(None, 0.20)] * 4) == round(1 - 4 * (0.20 + 0.012), 4)
    # fair book: no arb either way
    assert arb_profit([(0.24, 0.26)] * 4) < 0
    # missing ask on one bracket: YES side impossible, NO side still checked
    assert arb_profit([(0.30, None), (0.30, 0.32), (0.30, 0.32), (0.30, 0.32)]) == \
        round(3 - 4 * (0.70 + 0.015), 4)
    assert arb_profit([(None, None)]) is None
