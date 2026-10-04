"""Decide whether a bracket is worth buying, after fees."""
from __future__ import annotations

import math
from dataclasses import dataclass

from .config import StrategyConfig
from .kalshi import Bracket


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
    prob: float        # model probability this side wins
    edge: float        # prob - price - fee/contract
    contracts: int
    fee: float


def evaluate(b: Bracket, prob_yes: float, cfg: StrategyConfig) -> Signal | None:
    p = min(max(prob_yes, cfg.prob_clip), 1.0 - cfg.prob_clip)
    best = None
    for side, price, prob in (("yes", b.yes_ask, p), ("no", b.no_ask, 1.0 - p)):
        if price is None or not (cfg.min_price <= price <= cfg.max_price):
            continue
        fee = taker_fee(price, cfg.contracts_per_trade, cfg.fee_rate)
        edge = prob - price - fee / cfg.contracts_per_trade
        if edge >= cfg.min_edge and (best is None or edge > best.edge):
            best = Signal(b.ticker, b.event_ticker, side, price, prob, round(edge, 4),
                          cfg.contracts_per_trade, fee)
    return best


def pick_for_event(signals: list[Signal], cfg: StrategyConfig) -> list[Signal]:
    return sorted(signals, key=lambda s: s.edge, reverse=True)[:cfg.max_trades_per_event]


def settle_pnl(side: str, price: float, contracts: int, fee: float, result: str) -> float:
    won = side == result
    return round(contracts * ((1.0 - price) if won else -price) - fee, 4)
