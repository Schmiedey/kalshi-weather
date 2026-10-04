"""Synthetic Kalshi + weather data for offline tests.

Simulates daily-high events with 6 brackets. The true high = forecast + noise.
`market_sigma` controls how the simulated market prices brackets: equal to the
true sigma = efficient market (no edge); larger = market is miscalibrated.
"""
import math
import random
from dataclasses import replace
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from kalshi_weather.kalshi import Bracket
from kalshi_weather.model import bracket_prob

MON = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]


def ev_ticker(series, d):
    return f"{series}-{d:%y}{MON[d.month - 1]}{d:%d}"


def make_brackets(series, d, center):
    ev = ev_ticker(series, d)
    c = 2 * round(center / 2)
    out = [Bracket(f"{ev}-T{c-4}", ev, "less", None, c - 4, None, None, None, None, f"<{c-4}")]
    for lo in range(c - 4, c + 4, 2):
        out.append(Bracket(f"{ev}-B{lo}.5", ev, "between", lo, lo + 1, None, None, None, None, f"{lo}-{lo+1}"))
    out.append(Bracket(f"{ev}-T{c+3}", ev, "greater", c + 3, None, None, None, None, None, f">{c+3}"))
    return out


class World:
    def __init__(self, city, start, days, true_sigma=2.0, bias=0.0, market_sigma=2.0,
                 spread=0.02, price_noise=0.0, seed=1):
        rng = random.Random(seed)
        self.city = city
        self.forecasts, self.events, self.prices = {}, {}, {}
        for i in range(days):
            d = start + timedelta(days=i)
            fc = 65 + 15 * math.sin(i / 20) + rng.gauss(0, 3)
            actual = round(fc + bias + rng.gauss(0, true_sigma))
            br = make_brackets(city.series, d, fc)
            settled = []
            for b in br:
                lo = b.floor if b.floor is not None else -999
                hi = b.cap if b.cap is not None else 999
                if b.strike_type == "between":
                    win = lo <= actual <= hi
                elif b.strike_type == "greater":
                    win = actual > lo
                else:
                    win = actual < hi
                settled.append(replace(b, result="yes" if win else "no"))
                fair = bracket_prob(b, fc + bias, market_sigma)
                mid = min(max(fair + rng.gauss(0, price_noise), 0.02), 0.98)
                self.prices[b.ticker] = (round(mid - spread / 2, 2), round(mid + spread / 2, 2))
            self.forecasts[d] = fc
            self.events[d] = settled


class FakeKalshi:
    def __init__(self, world, open_date=None):
        self.w = world
        self.open_date = open_date

    def markets(self, series_ticker, status=None, **kw):
        if status == "open":
            br = make_brackets(series_ticker, self.open_date, self.w.forecasts.get(self.open_date, 65))
            return br
        return [b for bs in self.w.events.values() for b in bs]

    def settled_markets(self, series_ticker, **kw):
        return self.markets(series_ticker, status="settled")

    def candles(self, series, ticker, start_ts, end_ts, period=60, cache=True):
        bid, ask = self.w.prices[ticker]
        return [{"ts": end_ts - 3600, "yes_bid": bid, "yes_ask": ask}]

    def orderbook(self, ticker):
        bid, ask = self.w.prices.get(ticker, (0.10, 0.12))
        return {"yes_bid": bid, "yes_ask": ask, "yes_bid_qty": 50, "yes_ask_qty": 50}

    def market(self, ticker):
        for bs in self.w.events.values():
            for b in bs:
                if b.ticker == ticker:
                    return b
        raise KeyError(ticker)


class FakeWeather:
    def __init__(self, world):
        self.w = world

    def historical_highs(self, city, start, end, **kw):
        return {d: v for d, v in self.w.forecasts.items() if start <= d <= end}

    def forecast_highs(self, city, days=3):
        return dict(self.w.forecasts)
