"""Free forecast data from Open-Meteo (no API key).

- forecast_highs(): live multi-model forecast of tomorrow's high
- historical_highs(): what the models forecast N days ahead, for backtesting
  (Open-Meteo "previous runs" API), so the backtest only uses information that
  existed at decision time.
"""
from __future__ import annotations

import time
from collections import defaultdict
from datetime import date, datetime, timedelta
from datetime import time as dtime
from zoneinfo import ZoneInfo

import requests

from .config import City

FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"
DEFAULT_MODELS = ("gfs_seamless", "ecmwf_ifs025", "icon_seamless")


def _series(block: dict, var: str) -> list[list]:
    """All model columns for `var` (plain key, or `var_<model>` when several models)."""
    return [v for k, v in block.items() if k == var or k.startswith(var + "_")]


class WeatherClient:
    def __init__(self, session=None, models=DEFAULT_MODELS):
        self.session = session or requests.Session()
        self.models = tuple(models)

    def _get(self, url: str, params: dict) -> dict:
        for attempt in range(5):
            r = self.session.get(url, params=params, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        raise RuntimeError(f"Open-Meteo kept failing: {url}")

    def _base(self, city: City) -> dict:
        return {"latitude": city.lat, "longitude": city.lon, "timezone": city.tz,
                "temperature_unit": "fahrenheit", "models": ",".join(self.models)}

    def forecast_highs(self, city: City, days: int = 3) -> dict[date, float]:
        p = self._base(city) | {"daily": "temperature_2m_max", "forecast_days": days}
        daily = self._get(FORECAST_URL, p).get("daily", {})
        cols = _series(daily, "temperature_2m_max")
        out = {}
        for i, t in enumerate(daily.get("time", [])):
            vals = [c[i] for c in cols if i < len(c) and c[i] is not None]
            if vals:
                out[date.fromisoformat(t)] = sum(vals) / len(vals)
        return out

    def historical_highs(self, city: City, start: date, end: date, decision_hour: int = 15,
                         latency_hours: int = 8) -> dict[date, float]:
        """Forecast daily high per day, using only runs published by the decision time.

        The decision for day D is made at `decision_hour` local on D-1. Open-Meteo's
        `_previous_dayN` value at hour V comes from a run started N days before V
        (or earlier), which is public about `latency_hours` later. So `previous_day1`
        is only usable for early hours of D; later hours (the afternoon peak) must
        use `previous_day2`, otherwise the backtest sees runs from after the decision.
        """
        v1, v2 = "temperature_2m_previous_day1", "temperature_2m_previous_day2"
        p = self._base(city) | {"hourly": f"{v1},{v2}", "start_date": start.isoformat(),
                                "end_date": end.isoformat()}
        hourly = self._get(PREVIOUS_RUNS_URL, p).get("hourly", {})
        times = hourly.get("time", [])
        tz = ZoneInfo(city.tz)
        lag = timedelta(hours=latency_hours)
        use_day1 = []
        for t in times:
            v = datetime.fromisoformat(t).replace(tzinfo=tz)
            decision = datetime.combine(v.date() - timedelta(days=1), dtime(decision_hour), tz)
            use_day1.append(v - timedelta(days=1) + lag <= decision)
        cols1 = {k[len(v1):]: c for k, c in hourly.items() if k == v1 or k.startswith(v1 + "_")}
        cols2 = {k[len(v2):]: c for k, c in hourly.items() if k == v2 or k.startswith(v2 + "_")}
        per_model_day = []
        for model, c2 in cols2.items():
            c1 = cols1.get(model, [None] * len(times))
            days = defaultdict(list)
            for t, early, a, b in zip(times, use_day1, c1, c2):
                v = a if early else b
                if v is not None:
                    days[t[:10]].append(v)
            per_model_day.append({d: max(v) for d, v in days.items() if len(v) >= 20})
        out = {}
        for d in sorted({d for m in per_model_day for d in m}):
            vals = [m[d] for m in per_model_day if d in m]
            out[date.fromisoformat(d)] = sum(vals) / len(vals)
        return out
