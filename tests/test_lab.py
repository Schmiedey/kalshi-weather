"""Lab: judging, registry, single-use holdout."""
from datetime import date

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
