"""HTTP clients against fake responses (no network)."""
from datetime import date

from kalshi_weather.config import CITIES
from kalshi_weather.kalshi import KalshiClient
from kalshi_weather.weather import WeatherClient


class Resp:
    def __init__(self, data, status=200):
        self._d, self.status_code = data, status

    def json(self):
        return self._d

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class Session:
    def __init__(self, handler):
        self.handler, self.calls = handler, []

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.handler(url, params or {})


def test_markets_pagination_and_retry(tmp_path):
    state = {"n": 0}

    def h(url, p):
        state["n"] += 1
        if state["n"] == 1:
            return Resp({}, 429)       # rate limited once
        if "cursor" not in p:
            return Resp({"markets": [{"ticker": "A", "event_ticker": "X-25OCT03"}], "cursor": "c1"})
        return Resp({"markets": [{"ticker": "B", "event_ticker": "X-25OCT03"}], "cursor": ""})

    k = KalshiClient(session=Session(h), cache_dir=str(tmp_path), min_interval=0)
    import kalshi_weather.kalshi as km
    km.time.sleep = lambda s: None
    assert [m.ticker for m in k.markets("X", status="settled")] == ["A", "B"]


def test_candles_and_cache(tmp_path):
    s = Session(lambda u, p: Resp({"candlesticks": [
        {"end_period_ts": 200, "yes_bid": {"close": 40}, "yes_ask": {"close_dollars": "0.44"}},
        {"end_period_ts": 100, "yes_bid": {"close": 39}, "yes_ask": {"close": 43}}]}))
    k = KalshiClient(session=s, cache_dir=str(tmp_path), min_interval=0)
    c = k.candles("S", "T", 0, 300)
    assert [x["ts"] for x in c] == [100, 200] and c[1]["yes_ask"] == 0.44
    k.candles("S", "T", 0, 300)
    assert len(s.calls) == 1   # second call served from cache


def test_forecast_multi_model_mean():
    s = Session(lambda u, p: Resp({"daily": {
        "time": ["2025-10-03", "2025-10-04"],
        "temperature_2m_max_gfs_seamless": [70.0, 60.0],
        "temperature_2m_max_ecmwf_ifs025": [72.0, None]}}))
    out = WeatherClient(session=s).forecast_highs(CITIES["nyc"])
    assert out == {date(2025, 10, 3): 71.0, date(2025, 10, 4): 60.0}


def test_settled_markets_merges_historical_endpoint():
    def h(url, p):
        if url.endswith("/historical/markets"):
            assert "status" not in p
            return Resp({"markets": [{"ticker": "OLD", "event_ticker": "X-25JUL03"},
                                     {"ticker": "B", "event_ticker": "X-25OCT03"}], "cursor": ""})
        return Resp({"markets": [{"ticker": "B", "event_ticker": "X-25OCT03"}], "cursor": ""})
    k = KalshiClient("https://x", session=Session(h), min_interval=0)
    assert sorted(m.ticker for m in k.settled_markets("X")) == ["B", "OLD"]


def test_historical_highs_ignore_runs_after_decision():
    # NYC day Oct 3: decision is 3pm Oct 2. previous_day1 is only usable through
    # 7am Oct 3 (run ~7am Oct 2 + 8h latency); later hours must use previous_day2.
    times = [f"2025-10-03T{h:02d}:00" for h in range(24)]
    late_spike = [60] * 15 + [99] + [60] * 8          # day1 run sees a 3pm spike: future info
    s = Session(lambda u, p: Resp({"hourly": {
        "time": times,
        "temperature_2m_previous_day1_gfs_seamless": late_spike,
        "temperature_2m_previous_day2_gfs_seamless": [50 + h for h in range(24)],
        "temperature_2m_previous_day1_ecmwf_ifs025": late_spike,
        "temperature_2m_previous_day2_ecmwf_ifs025": [55 + h for h in range(24)]}}))
    out = WeatherClient(session=s).historical_highs(CITIES["nyc"], date(2025, 10, 3), date(2025, 10, 3))
    assert out == {date(2025, 10, 3): (73 + 78) / 2}


def test_historical_highs_use_day1_for_early_hours():
    times = [f"2025-10-03T{h:02d}:00" for h in range(24)]
    s = Session(lambda u, p: Resp({"hourly": {
        "time": times,
        "temperature_2m_previous_day1_gfs_seamless": [90] * 8 + [0] * 16,
        "temperature_2m_previous_day2_gfs_seamless": [50] * 24}}))
    out = WeatherClient(session=s).historical_highs(CITIES["nyc"], date(2025, 10, 3), date(2025, 10, 3))
    assert out == {date(2025, 10, 3): 90}


def test_candles_fall_back_to_historical_endpoint(tmp_path):
    def h(url, p):
        if "/series/" in url:
            return Resp({}, 404)
        return Resp({"candlesticks": [{"end_period_ts": 100, "yes_bid": {"close": "0.4100"},
                                       "yes_ask": {"close": "0.4500"}}]})
    s = Session(h)
    k = KalshiClient("https://x", session=s, cache_dir=str(tmp_path), min_interval=0)
    assert k.candles("S", "T", 0, 100) == [{"ts": 100, "yes_bid": 0.41, "yes_ask": 0.45}]
    n = len(s.calls)
    k.candles("S", "T", 0, 100)          # both the 404 and the fallback are cached
    assert len(s.calls) == n


def test_live_candles_dollars():
    s = Session(lambda u, p: Resp({"candlesticks": [
        {"end_period_ts": 200, "yes_bid": {"close_dollars": "0.0000"}, "yes_ask": {"close_dollars": "0.0300"}}]}))
    k = KalshiClient("https://x", session=s, min_interval=0)
    assert k.candles("S", "T", 0, 200, cache=False) == [{"ts": 200, "yes_bid": None, "yes_ask": 0.03}]


def test_candles_skip_live_endpoint_for_known_historical_markets():
    def h(url, p):
        if url.endswith("/historical/cutoff"):
            return Resp({"market_settled_ts": "2026-08-04T00:00:00Z"})
        assert "/series/" not in url, "should not try the live endpoint"
        return Resp({"candlesticks": [{"end_period_ts": 5, "yes_bid": {"close": "0.0200"},
                                       "yes_ask": {"close": "0.0300"}}]})
    k = KalshiClient("https://x", session=Session(h), min_interval=0)
    out = k.candles("S", "T", 0, 5, cache=False, close_time="2026-06-01T04:00:00Z")
    assert out == [{"ts": 5, "yes_bid": 0.02, "yes_ask": 0.03}]


def test_network_errors_are_retried(tmp_path, monkeypatch):
    import requests
    import kalshi_weather.kalshi as km
    monkeypatch.setattr(km.time, "sleep", lambda s: None)
    state = {"n": 0}

    def h(url, p):
        state["n"] += 1
        if state["n"] == 1:
            raise requests.ReadTimeout("slow")
        if state["n"] == 2:
            raise requests.ConnectionError("reset")
        return Resp({"markets": [{"ticker": "A", "event_ticker": "X-25OCT03"}], "cursor": ""})

    k = KalshiClient(session=Session(h), cache_dir=str(tmp_path), min_interval=0)
    assert [m.ticker for m in k.markets("X", status="settled")] == ["A"]
    assert k.throttled == 2
