"""Kalshi fees, trade signals and settlement P&L."""
from __future__ import annotations

import math
from dataclasses import dataclass


def taker_fee(price: float, contracts: int, rate: float = 0.07) -> float:
    """Kalshi taker fee: ceil(rate * C * P * (1-P)) to the next cent."""
    raw = rate * contracts * price * (1.0 - price)
    return math.ceil(round(raw * 100, 6)) / 100.0


@dataclass
class Signal:
    ticker: str
    event_ticker: str
    side: str          # "yes" or "no"
    price: float       # dollars per contract
    prob: float        # probability this side wins (market-implied for longshots)
    edge: float        # prob - price - fee/contract
    contracts: int
    fee: float


def settle_pnl(side: str, price: float, contracts: int, fee: float, result: str) -> float:
    won = side == result
    return round(contracts * ((1.0 - price) if won else -price) - fee, 4)
