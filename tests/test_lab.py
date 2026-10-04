"""Lab: judging, registry, single-use holdout."""
from datetime import date, datetime, timezone

import pytest

from kalshi_weather.lab import final_exam, judge, load, register, windows


def mk(n_days, lose_every):
    """5 trades a day at 97c; on every `lose_every`-th day one of them loses."""
    out = []
    for d in range(n_days):
        for i in range(5):
            pnl = -9.73 if (d % lose_every == 0 and i == 0) else 0.27
            out.append({"date": f"2026-{1 + d // 28:02d}-{1 + d % 28:02d}", "pnl": pnl, "contracts": 10,
                        "price": 0.97, "fee": 0.03, "won": pnl > 0})
    return out


def test_judge_pass_and_fail():
    assert judge(mk(120, 20))["passed"]        # 6 losses: +1.37 a day, clearly positive
    assert not judge(mk(120, 5))["passed"]     # 24 losses: losing
    assert not judge([])["passed"]


def test_windows_reserve_holdout():
    w = windows(date(2026, 10, 2))
    assert w["dev"][1] < w["holdout"][0] and w["holdout"][1] == date(2026, 10, 2)


def test_registry_and_single_final_exam(tmp_path):
    path = str(tmp_path / "exp.jsonl")
    register({"name": "a", "passed": False}, path)
    e = final_exam("b", lambda: {"passed": True}, path)
    assert e["id"] == 2 and [x["name"] for x in load(path)] == ["a", "b"]
    with pytest.raises(RuntimeError):
        final_exam("b", lambda: {"passed": True}, path)


def test_xmarket_quotes_and_sampling():
    from kalshi_weather.kalshi import Bracket
    from kalshi_weather.lab import sample_by_date, xmarket_quotes
    ms = [Bracket(f"T{d}-{i}", f"E{d}", None, None, None, "no", f"2026-09-{d:02d}T20:00:00Z", None, None)
          for d in range(1, 31) for i in range(10)]
    s = sample_by_date(ms, 100)
    assert len(s) <= 100 and len({m.close_time for m in s}) == len(s) // 10    # whole days kept
    assert sample_by_date(ms[:50], 100) == ms[:50]

    class K:
        def settled_markets(self, series, lo, hi, cache=False):
            return [Bracket("A", "E", None, None, None, "no", "2026-09-02T20:00:00Z", None, None),
                    Bracket("B", "E", None, None, None, None, "2026-09-02T20:00:00Z", None, None)]

        def candles(self, series, ticker, a, b, close_time=None):
            self.window = (a, b)
            return [{"ts": b - 3600, "yes_bid": 0.02, "yes_ask": 0.05}]
    k = K()
    rows = xmarket_quotes(k, "S", date(2026, 9, 1), date(2026, 9, 30), hours_before=6)
    assert rows == [{"city": "S", "date": "2026-09-02", "event": "E", "ticker": "A", "bid": 0.02,
                     "ask": 0.05, "result": "no"}]                                   # unsettled B skipped
    assert k.window[1] == int(datetime(2026, 9, 2, 14, tzinfo=timezone.utc).timestamp())
