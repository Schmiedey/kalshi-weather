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


def test_brain_section():
    brain = [{"strategy": "longshot_5pm", "status": "active", "settled": 300, "post_c": 1.1, "post_sd_c": 0.3,
              "p_pos": 0.99, "p_best": 0.6, "size": 40}]
    page = render([], brain=brain)
    assert "<h2>Brain</h2>" in page and "+1.10 ± 0.30" in page


def test_pending_section_counts_only_open_trades():
    from kalshi_weather.dashboard import pending_stats
    rows = [{"strategy": "a", "status": "open", "price": 0.98, "contracts": 10, "fee": 0.02},
            {"strategy": "a", "status": "open", "price": 0.99, "contracts": 10, "fee": 0.01},
            {"strategy": "a", "status": "settled", "price": 0.98, "contracts": 10, "fee": 0.02},
            {"strategy": "b", "status": "resting", "price": 0.98, "contracts": 10, "fee": 0.02}]
    p = pending_stats(rows)
    assert set(p) == {"a"} and p["a"]["trades"] == 2
    assert round(p["a"]["at_risk"], 2) == round(9.82 + 9.91, 2)
    assert round(p["a"]["if_all_win"], 2) == round(0.18 + 0.09, 2)
    page = render([dict(r, ticker="T", city="nyc", opened_at="2026-10-04T16:00:00+00:00", side="no", result=None,
                        pnl=None, prob=0.98) for r in rows[:2]])
    assert "Open bets (not profit yet)" in page and "+$0.27" in page
