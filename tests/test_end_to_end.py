"""Full pipeline on simulated markets.

Proves the bot (a) finds money when the market is miscalibrated, and
(b) mostly stays out / doesn't fake profits when the market is efficient.
"""
from datetime import date, datetime, timezone, timedelta

from kalshi_weather.backtest import run_backtest
from kalshi_weather.config import CITIES, StrategyConfig
from kalshi_weather.ledger import Ledger
from kalshi_weather.paper import load_params, run_paper, settle_open
from tests.fakes import FakeKalshi, FakeWeather, World

CITY = CITIES["nyc"]
START = date(2025, 1, 1)


def bt(world, tmp_path):
    return run_backtest(FakeKalshi(world), FakeWeather(world), [CITY], START,
                        START + timedelta(days=299), StrategyConfig(), out_dir=str(tmp_path),
                        log=lambda *a: None)


def test_finds_edge_when_market_is_miscalibrated(tmp_path):
    w = World(CITY, START, 300, true_sigma=1.8, bias=1.5, market_sigma=3.5, seed=7)
    t = bt(w, tmp_path)["total"]
    assert t["trades"] > 50
    assert t["model_brier"] < t["market_brier"]
    assert t["pnl"] > 0
    p = load_params(str(tmp_path / "model_params.json"))["nyc"]
    assert abs(p.bias - 1.5) < 0.6 and abs(p.sigma - 1.8) < 0.6   # learned the truth


def test_no_fake_profits_on_efficient_market(tmp_path):
    pnls = []
    for seed in range(5):
        w = World(CITY, START, 300, true_sigma=2.0, bias=0.0, market_sigma=2.0, seed=seed)
        pnls.append(bt(w, tmp_path)["total"]["pnl"])
    # with fees + spread there should be no systematic profit
    assert sum(pnls) / len(pnls) < 15


def test_paper_trade_and_settle(tmp_path):
    w = World(CITY, START, 40, true_sigma=1.8, bias=1.5, market_sigma=3.5, seed=3)
    target = START + timedelta(days=20)
    now = datetime(2025, 1, 20, 20, 0, tzinfo=timezone.utc)   # 3pm NY, day before target
    ledger = Ledger(str(tmp_path / "paper.db"))
    k = FakeKalshi(w, open_date=target)
    from kalshi_weather.model import ErrorModel
    placed = run_paper(k, FakeWeather(w), ledger, [CITY], StrategyConfig(),
                       {"nyc": ErrorModel(1.5, 1.8, 50)}, now=now, log=lambda *a: None)
    assert len(placed) == 1
    # running again must not double-trade the same event
    assert run_paper(k, FakeWeather(w), ledger, [CITY], StrategyConfig(),
                     {"nyc": ErrorModel(1.5, 1.8, 50)}, now=now, log=lambda *a: None) == []
    assert settle_open(k, ledger, log=lambda *a: None) == 1
    s = ledger.summary()
    assert s["settled"] == 1 and s["open"] == 0


def test_blend_mode_finds_edge_and_paper_uses_weights(tmp_path):
    w = World(CITY, START, 300, true_sigma=1.8, bias=1.5, market_sigma=3.5, seed=7)
    out = run_backtest(FakeKalshi(w), FakeWeather(w), [CITY], START, START + timedelta(days=299),
                       StrategyConfig(prob_source="blend"), out_dir=str(tmp_path), log=lambda *a: None)
    t = out["total"]
    assert t["blend_brier"] < t["blend_mkt_brier"]       # model adds info -> blend beats market
    assert t["trades"] > 20 and t["pnl"] > 0
    from kalshi_weather.paper import load_blend_weights
    bw = load_blend_weights(str(tmp_path / "model_params.json"))["nyc"]
    assert bw[1] > 0.3                                     # real weight on the model
    d = START + timedelta(days=302)          # tomorrow relative to `now` below
    w.forecasts[d] = 70.0
    placed = run_paper(FakeKalshi(w, open_date=d), FakeWeather(w), Ledger(str(tmp_path / "p.db")),
                       [CITY], StrategyConfig(prob_source="blend"),
                       load_params(str(tmp_path / "model_params.json")),
                       now=datetime(2025, 10, 29, 19, tzinfo=timezone.utc), log=lambda *a: None,
                       blend_w={"nyc": bw})
    assert all(s.event_ticker.endswith("25OCT30") for s in placed)


def test_blend_mode_no_fake_profits_on_efficient_market(tmp_path):
    pnls = []
    for seed in range(3):
        w = World(CITY, START, 300, true_sigma=2.0, bias=0.0, market_sigma=2.0, seed=seed)
        pnls.append(run_backtest(FakeKalshi(w), FakeWeather(w), [CITY], START,
                                 START + timedelta(days=299), StrategyConfig(prob_source="blend"),
                                 out_dir=str(tmp_path), log=lambda *a: None)["total"]["pnl"])
    assert sum(pnls) / len(pnls) < 15
