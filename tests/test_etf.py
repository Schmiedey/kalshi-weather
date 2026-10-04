"""ETF trend rule: no lookahead, backtest and paper NAV."""
import sqlite3

from kalshi_weather.etf import backtest, signals, update_paper


def series(vals, start=(2020, 1)):
    y, m = start
    out = {}
    for v in vals:
        out[f"{y}-{m:02d}"] = v
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def test_signal_uses_only_data_through_the_month():
    up = series([100 + i for i in range(12)])
    crash_after = dict(up) | {"2021-01": 1.0}           # a later crash must not change the signal
    assert signals({"A": up}, "2020-12") == signals({"A": crash_after}, "2020-12") == {"A": 1.0}
    down = series([100 - i for i in range(12)])
    assert signals({"A": down}, "2020-12") == {"A": 0.0}
    assert signals({"A": series([100] * 5)}, "2020-05") == {"A": 0.0}   # not enough history


def test_backtest_holds_cash_in_downtrends():
    a = series([100 + i for i in range(14)])
    b = series([100 - i for i in range(14)])
    path = backtest({"A": a, "B": b}, "2020-01")
    last = path[-1]
    assert last["weights"] == {"A": 0.5, "B": 0.0}
    assert abs(last["ret"] - 0.5 * (a["2021-02"] / a["2021-01"] - 1)) < 1e-12


def test_paper_nav_advances_month_by_month():
    db = sqlite3.connect(":memory:")
    a = series([100 + i for i in range(12)])
    assert update_paper(db, {"A": a})[0]["nav"] == 10_000.0
    a2 = dict(a) | {"2021-01": 112.0}                     # one new month, +1.8%
    row = update_paper(db, {"A": a2})[0]
    assert row["month"] == "2021-01" and abs(row["nav"] - 10_000 * 112 / 111) < 1e-6
    assert update_paper(db, {"A": a2}) == []              # idempotent
