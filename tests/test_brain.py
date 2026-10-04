"""Brain: edge estimate, kill switch, sizing."""
import random

from kalshi_weather.brain import Brain, BrainConfig, estimate, kelly_contracts
from kalshi_weather.ledger import Ledger
from kalshi_weather.longshot import LongshotConfig, longshot_signal


def trades(n, loss_rate, seed=0, price=0.97, per_day=10):
    rng = random.Random(seed)
    out = []
    for i in range(n):
        lose = rng.random() < loss_rate
        fee = 0.03
        pnl = 10 * (-price if lose else 1 - price) - fee
        out.append({"pnl": pnl, "contracts": 10, "settled_at": f"2026-{1 + i // (per_day * 28):02d}-"
                    f"{1 + (i // per_day) % 28:02d}T12:00:00"})
    return out


def test_no_data_is_the_prior():
    e = estimate([])
    assert e.p_positive == 0.5 and e.post_mean == 0.0


def test_real_edge_is_detected_and_sized_up():
    e = estimate(trades(3000, 0.005))          # 0.5% losses at 97c: ~+2.2c/contract
    assert e.p_positive > 0.99 and e.post_mean > 0.01
    assert kelly_contracts(e, 0.97) > 10


def test_no_edge_is_not_trusted():
    e = estimate(trades(3000, 0.04))           # 4% losses at 97c: clearly negative
    assert e.p_positive < 0.05
    assert kelly_contracts(e, 0.97) == BrainConfig().base_contracts


def test_kill_switch_stops_losing_strategy_and_alerts_once(tmp_path):
    led = Ledger(str(tmp_path / "p.db"))
    rng = random.Random(1)
    for i in range(300):
        t = led.record("nyc", longshot_signal(f"T{i}", f"E{i}", 0.03, LongshotConfig()), strategy="bad")
        led.settle(t, "yes" if rng.random() < 0.06 else "no")
        led.db.execute("UPDATE trades SET settled_at=? WHERE id=?",
                       (f"2026-{1 + i // 280:02d}-{1 + (i // 10) % 28:02d}T12:00:00", t))
    led.db.commit()
    b = Brain(led, BrainConfig(max_drawdown=1e9))
    status, est, alert = b.review("bad")
    assert status == "killed" and alert and "bad stopped" in alert
    assert b.contracts("bad", 0.97) == 0
    assert b.review("bad")[2] is None            # no repeat alert
    b.revive("bad")
    assert b.status("bad") == "evaluating"


def test_drawdown_kill(tmp_path):
    led = Ledger(str(tmp_path / "p.db"))
    for i in range(12):                          # 12 straight losses ~ -$116
        t = led.record("nyc", longshot_signal(f"T{i}", f"E{i}", 0.03, LongshotConfig()), strategy="dd")
        led.settle(t, "yes")
    assert Brain(led).review("dd")[0] == "killed"


def test_thompson_allocation_prefers_the_stronger_strategy():
    from kalshi_weather.brain import allocation_multiplier, p_best
    strong, weak = estimate(trades(3000, 0.005, seed=1)), estimate(trades(3000, 0.02, seed=2))
    pb = p_best({"strong": strong, "weak": weak})
    assert pb["strong"] > 0.95 and abs(sum(pb.values()) - 1) < 1e-9
    assert allocation_multiplier(pb["strong"], 2) == 1.9 or allocation_multiplier(pb["strong"], 2) > 1.8
    assert allocation_multiplier(pb["weak"], 2) == 0.5
    prior = p_best({"a": estimate([]), "b": estimate([])})
    assert abs(prior["a"] - 0.5) < 0.05             # no data: equal odds -> multiplier ~1
