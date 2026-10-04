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
