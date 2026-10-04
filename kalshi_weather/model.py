"""Forecast -> probability for each temperature bracket.

Actual high ~ Normal(forecast + bias, sigma), with bias and sigma learned from
past forecast errors. Kalshi reports whole degrees, so brackets get a 0.5
continuity correction.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass

from .kalshi import Bracket


def norm_cdf(x: float, mu: float, sigma: float) -> float:
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def bracket_bounds(b: Bracket) -> tuple[float, float] | None:
    """Continuous bounds for a bracket on integer temperatures."""
    st = (b.strike_type or "").lower()
    lo, hi = -math.inf, math.inf
    if st == "between" and b.floor is not None and b.cap is not None:
        lo, hi = b.floor - 0.5, b.cap + 0.5
    elif st == "greater" and b.floor is not None:
        lo = b.floor + 0.5
    elif st == "greater_or_equal" and b.floor is not None:
        lo = b.floor - 0.5
    elif st == "less" and b.cap is not None:
        hi = b.cap - 0.5
    elif st == "less_or_equal" and b.cap is not None:
        hi = b.cap + 0.5
    else:
        return None
    return lo, hi


def bracket_prob(b: Bracket, mu: float, sigma: float) -> float | None:
    bounds = bracket_bounds(b)
    if bounds is None:
        return None
    lo, hi = bounds
    p_hi = 1.0 if hi == math.inf else norm_cdf(hi, mu, sigma)
    p_lo = 0.0 if lo == -math.inf else norm_cdf(lo, mu, sigma)
    return max(0.0, p_hi - p_lo)


@dataclass
class ErrorModel:
    bias: float = 0.0
    sigma: float = 2.5
    n: int = 0

    def mean(self, forecast: float) -> float:
        return forecast + self.bias


def _loglik(intervals, bias, sigma):
    ll = 0.0
    for lo, hi in intervals:
        p_hi = 1.0 if hi == math.inf else norm_cdf(hi, bias, sigma)
        p_lo = 0.0 if lo == -math.inf else norm_cdf(lo, bias, sigma)
        ll += math.log(max(p_hi - p_lo, 1e-12))
    return ll


def _grid_best(intervals, biases, sigmas):
    return max(((b, s) for b in biases for s in sigmas), key=lambda bs: _loglik(intervals, *bs))


def fit_error_model(errors: list[tuple[float, float]], min_n: int = 15, default_sigma: float = 2.5,
                    sigma_floor: float = 1.2, window: int = 120) -> ErrorModel:
    """Max-likelihood fit of forecast error ~ Normal(bias, sigma).

    Each error is an interval (lo, hi) = winning bracket bounds minus the
    forecast, so tail outcomes ("above 75") count too instead of being dropped.
    A plain number is treated as an exact error.
    """
    ivs = [(e - 1e-3, e + 1e-3) if isinstance(e, (int, float)) else tuple(e) for e in errors[-window:]]
    if len(ivs) < min_n:
        return ErrorModel(0.0, default_sigma, len(ivs))
    b, s = _grid_best(ivs, [x * 0.5 for x in range(-16, 17)], [0.8 + x * 0.4 for x in range(16)])
    b, s = _grid_best(ivs, [b + x * 0.1 for x in range(-5, 6)],
                      [max(0.5, s + x * 0.05) for x in range(-8, 9)])
    return ErrorModel(b, max(s, sigma_floor), len(ivs))


def outcome_interval(brackets: list[Bracket]) -> tuple[float, float] | None:
    """Bounds of the settled high, from whichever bracket resolved YES."""
    for b in brackets:
        if b.result == "yes":
            bounds = bracket_bounds(b)
            if bounds:
                return bounds
    return None


def actual_high_from_event(brackets: list[Bracket]) -> float | None:
    """Point estimate (winning 'between' bracket midpoint); tails return None."""
    for b in brackets:
        if b.result == "yes" and (b.strike_type or "").lower() == "between" \
                and b.floor is not None and b.cap is not None:
            return (b.floor + b.cap) / 2.0
    return None


def brier(pairs: list[tuple[float, int]]) -> float | None:
    return statistics.fmean((p - y) ** 2 for p, y in pairs) if pairs else None
