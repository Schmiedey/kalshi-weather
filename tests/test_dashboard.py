"""Dashboard renders from a ledger, including the empty state."""
from datetime import datetime, timezone

from kalshi_weather.dashboard import build, render, variant_stats
from kalshi_weather.ledger import Ledger
from kalshi_weather.longshot import VARIANTS, LongshotConfig, longshot_signal


def test_empty_ledger_renders(tmp_path):
    out = tmp_path / "site" / "index.html"
    assert build(str(tmp_path / "paper.db"), str(out)) == 0
    page = out.read_text()
    assert "No settled trades yet" in page and "No trades yet" in page
    assert all(v in page for v in VARIANTS)


def test_stats_and_chart_from_settled_trades(tmp_path):
    led = Ledger(str(tmp_path / "paper.db"))
    win = led.record("nyc", longshot_signal("A", "E", 0.02, LongshotConfig()), strategy="longshot_5pm")
    lose = led.record("chi", longshot_signal("B", "F", 0.04, LongshotConfig()), strategy="longshot_5pm")
    led.settle(win, "no")
    led.settle(lose, "yes")
    rows = [dict(r) for r in led.db.execute("SELECT * FROM trades")]
    s = variant_stats(rows)["longshot_5pm"]
    assert s["settled"] == 2 and s["losses"] == 1
    assert round(s["pnl"], 2) == round(0.18 + (-9.63), 2)
    assert round(s["edge"], 2) == round(s["pnl"] + 0.02 + 0.03, 2)   # baseline = fees only
    page = render(rows, now=datetime(2026, 10, 5, tzinfo=timezone.utc))
    assert "<svg" in page and "<path" in page and "longshot_5pm" in page


def test_brain_etf_and_lab_sections():
    brain = [{"strategy": "longshot_5pm", "status": "active", "settled": 300, "post_c": 1.1, "post_sd_c": 0.3,
              "p_pos": 0.99, "p_best": 0.6, "size": 40}]
    etf = [{"month": "2026-10", "nav": 10000.0, "weights": "{}"},
           {"month": "2026-11", "nav": 10100.0, "weights": '{"SPY": 0.2, "GLD": 0.2, "DBC": 0.0}'}]
    lab = [{"id": 1, "name": "x", "period": "dev", "passed": True}, {"id": 2, "name": "y", "passed": False}]
    page = render([], brain=brain, etf=etf, lab=lab)
    assert "<h2>Brain</h2>" in page and "+1.10 ± 0.30" in page
    assert "$10,100.00" in page and "SPY, GLD" in page
    assert "2 strategy tests on record, 1" in page


def test_maker_collector_and_research_sections(tmp_path):
    from kalshi_weather.collector import write
    from kalshi_weather.dashboard import collector_health, maker_stats
    rows = [{"strategy": "maker_5pm", "status": s} for s in ("resting", "open", "settled", "expired", "expired")]
    m = maker_stats(rows)
    assert (m["resting"], m["filled"], m["expired"]) == (1, 2, 2) and m["fill_rate"] == 0.5

    assert collector_health(str(tmp_path)) is None
    snap = [{"ticker": "T1", "event_ticker": "KXHIGHNY-26OCT05", "yes_bid_dollars": "0.0300"},
            {"ticker": "T2", "event_ticker": "KXLOWTCHI-26OCT05", "yes_bid_dollars": "0.0000"}]
    write(snap, str(tmp_path), datetime(2026, 10, 4, 22, 37, tzinfo=timezone.utc))
    write(snap, str(tmp_path), datetime(2026, 10, 5, 1, 37, tzinfo=timezone.utc))
    c = collector_health(str(tmp_path), now=datetime(2026, 10, 5, 2, 7, tzinfo=timezone.utc))
    assert (c["snapshots"], c["days"], c["markets"], c["with_bid"], c["series"]) == (2, 2, 2, 1, 2)
    assert round(c["age_h"], 1) == 0.5

    page = render([], collector=c, research=[("Idea A", "rejected", "no edge")], repo_url="https://x")
    assert "Maker orders" in page and "Data collector" in page and "2026-10-05 01:37" in page
    assert "Idea A" in page and "https://x/tree/data" in page
