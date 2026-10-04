"""Longshot strategy and multi-strategy ledger."""
import sqlite3
from datetime import date, datetime, timedelta, timezone

from kalshi_weather.config import CITIES
from kalshi_weather.ledger import Ledger
from kalshi_weather.longshot import (VARIANTS, LongshotConfig, backtest_longshot, due_cities,
                                     longshot_signal, quote_from_candles, run_longshot_paper,
                                     summarize)
from tests.fakes import FakeKalshi, World


def test_signal_thresholds_and_sizing():
    cfg = LongshotConfig(contracts=10)
    assert longshot_signal("T", "E", None, cfg) is None          # no YES bid -> no NO ask
    assert longshot_signal("T", "E", 0.05, cfg) is None          # not a longshot
    s = longshot_signal("T", "E", 0.03, cfg)
    assert (s.side, s.price, s.contracts, s.fee) == ("no", 0.97, 10, 0.03)
    assert longshot_signal("T", "E", 0.02, cfg, available=4).contracts == 4
    assert longshot_signal("T", "E", 0.02, cfg, available=0.5) is None


def test_backtest_pnl_and_summary():
    rows = [{"city": "nyc", "date": "2026-07-01", "event": "E1", "ticker": "A", "bid": 0.02, "result": "no"},
            {"city": "nyc", "date": "2026-07-01", "event": "E1", "ticker": "B", "bid": 0.30, "result": "no"},
            {"city": "nyc", "date": "2026-07-02", "event": "E2", "ticker": "C", "bid": 0.04, "result": "yes"}]
    t = backtest_longshot(rows, LongshotConfig(contracts=10))
    assert [x["ticker"] for x in t] == ["A", "C"]
    assert t[0]["pnl"] == round(10 * 0.02 - 0.02, 4)              # win: 2c each minus 2c fee
    assert t[1]["pnl"] == round(-10 * 0.96 - 0.03, 4)             # loss: lose the 96c stake
    s = summarize(t)
    assert s["trades"] == 2 and s["losses"] == 1 and s["days"] == 2
    assert s["ci90_cents"][0] <= s["cents_per_contract"] <= s["ci90_cents"][1]


def test_ledger_migrates_old_db_and_separates_strategies(tmp_path):
    p = str(tmp_path / "old.db")
    db = sqlite3.connect(p)            # ledger schema from before strategies existed
    db.execute("CREATE TABLE trades (id INTEGER PRIMARY KEY AUTOINCREMENT, opened_at TEXT NOT NULL,"
               " city TEXT NOT NULL, ticker TEXT NOT NULL, event_ticker TEXT NOT NULL, side TEXT NOT NULL,"
               " price REAL NOT NULL, contracts INTEGER NOT NULL, fee REAL NOT NULL, prob REAL NOT NULL,"
               " edge REAL NOT NULL, forecast REAL, status TEXT NOT NULL DEFAULT 'open', result TEXT,"
               " pnl REAL, settled_at TEXT)")
    db.execute("INSERT INTO trades (opened_at, city, ticker, event_ticker, side, price, contracts, fee,"
               " prob, edge) VALUES ('x','nyc','A','E','yes',0.5,10,0.18,0.6,0.1)")
    db.commit()
    db.close()
    led = Ledger(p)
    assert led.strategies() == ["model"] and led.has_event("E")
    led.record("nyc", longshot_signal("B", "E", 0.02, LongshotConfig()), strategy="longshot_3pm")
    assert led.has_ticker("B", "longshot_3pm") and not led.has_ticker("A", "longshot_3pm")
    assert led.summary("longshot_3pm")["total_trades"] == 1 and led.summary()["total_trades"] == 2


def test_paper_buys_no_on_longshots_once(tmp_path):
    city = CITIES["nyc"]
    start = date(2025, 1, 1)
    w = World(city, start, 10, true_sigma=2.0, bias=0.0, market_sigma=2.0, seed=1)
    d = date(2025, 10, 30)
    w.forecasts[d] = 70.0
    k = FakeKalshi(w, open_date=d)
    books = {}
    for b in k.markets(city.series, status="open"):
        books[b.ticker] = (0.02, 0.03) if not books else (0.40, 0.42)
    w.prices.update(books)
    led = Ledger(str(tmp_path / "p.db"))
    now = datetime(2025, 10, 29, 19, tzinfo=timezone.utc)
    placed = run_longshot_paper(k, led, [city], LongshotConfig(), now=now, log=lambda *a: None)
    assert len(placed) == 1 and placed[0].side == "no" and placed[0].price == 0.98
    assert run_longshot_paper(k, led, [city], LongshotConfig(), now=now, log=lambda *a: None) == []


def test_variants_use_own_ledger_names(tmp_path):
    city = CITIES["nyc"]
    w = World(city, date(2025, 1, 1), 10, true_sigma=2.0, bias=0.0, market_sigma=2.0, seed=1)
    d = date(2025, 10, 30)
    w.forecasts[d] = 70.0
    k = FakeKalshi(w, open_date=d)
    first = k.markets(city.series, status="open")[0].ticker
    w.prices[first] = (0.02, 0.03)
    led = Ledger(str(tmp_path / "p.db"))
    now = datetime(2025, 10, 29, 19, tzinfo=timezone.utc)
    for cfg in VARIANTS.values():          # same bracket, traded once per variant
        assert len(run_longshot_paper(k, led, [city], cfg, now=now, log=lambda *a: None)) == 1
    assert sorted(led.strategies()) == sorted(VARIANTS)


def test_due_cities_by_local_hour():
    cities = [CITIES["nyc"], CITIES["chi"], CITIES["lax"]]
    now = datetime(2026, 7, 1, 19, 7, tzinfo=timezone.utc)      # 3pm EDT, 2pm CDT, noon PDT
    assert [c.key for c in due_cities(cities, 15, now)] == ["nyc"]
    assert [c.key for c in due_cities(cities, 12, now)] == ["lax"]


def test_quote_from_candles_never_uses_later_data():
    cs = [{"ts": 100, "yes_bid": 0.02, "yes_ask": 0.03}, {"ts": 200, "yes_bid": 0.30, "yes_ask": 0.32}]
    assert quote_from_candles(cs, 150) == (0.02, 0.03)
    assert quote_from_candles(cs, 200) == (0.30, 0.32)
    assert quote_from_candles(cs, 50) == (None, None)


def test_ask_filter_only_applies_when_set():
    from kalshi_weather.longshot import FORWARD
    plain, fwd = LongshotConfig(), FORWARD["longshot41_5pm"]
    assert longshot_signal("T", "E", 0.02, plain, yes_ask=0.90) is not None   # original rule unchanged
    assert longshot_signal("T", "E", 0.02, fwd, yes_ask=0.90) is None         # stub bid, no real market
    assert longshot_signal("T", "E", 0.02, fwd, yes_ask=None) is None
    assert longshot_signal("T", "E", 0.02, fwd, yes_ask=0.05).price == 0.98
    rows = [{"city": "x", "date": "2026-09-01", "event": "E", "ticker": "A", "bid": 0.02, "ask": 0.95, "result": "yes"},
            {"city": "x", "date": "2026-09-01", "event": "E", "ticker": "B", "bid": 0.02, "ask": 0.04, "result": "no"}]
    assert [t["ticker"] for t in backtest_longshot(rows, fwd)] == ["B"]
    assert len(backtest_longshot(rows, plain)) == 2
