"""Does the model add information beyond the market price?

Blend: P(yes) = sigmoid(a * logit(market) + b * logit(model) + c), fitted by
logistic regression on past brackets only. If the out-of-sample blend scores
better than the market alone, the model carries real information; if b ~ 0,
it doesn't.

Also: bracket-sum arbitrage. An event's brackets are a partition, so one YES of
each pays exactly $1. If buying all YES costs less than $1 after fees (or
buying all NO costs less than $(n-1)), the profit is locked in.
"""
from __future__ import annotations

import math

from .strategy import taker_fee


def logit(p: float, eps: float = 0.01) -> float:
    p = min(max(p, eps), 1.0 - eps)
    return math.log(p / (1.0 - p))


def sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


def _solve3(m, v):
    """Solve a 3x3 linear system by Gaussian elimination."""
    a = [row[:] + [v[i]] for i, row in enumerate(m)]
    for c in range(3):
        piv = max(range(c, 3), key=lambda r: abs(a[r][c]))
        a[c], a[piv] = a[piv], a[c]
        for r in range(3):
            if r != c:
                f = a[r][c] / a[c][c]
                a[r] = [x - f * y for x, y in zip(a[r], a[c])]
    return [a[i][3] / a[i][i] for i in range(3)]


def fit_blend(rows: list[tuple[float, float, int]], ridge: float = 1.0,
              iters: int = 25) -> tuple[float, float, float]:
    """Logistic regression of y on (logit market, logit model, 1) by Newton's method.

    Ridge pulls (a, b, c) toward (1, 0, 0): "trust the market" until data says otherwise.
    """
    prior = [1.0, 0.0, 0.0]
    w = prior[:]
    for _ in range(iters):
        g = [ridge * (wi - pi) for wi, pi in zip(w, prior)]
        h = [[ridge if i == j else 0.0 for j in range(3)] for i in range(3)]
        for xm, xd, y in rows:
            x = (xm, xd, 1.0)
            p = sigmoid(sum(wi * xi for wi, xi in zip(w, x)))
            for i in range(3):
                g[i] += (p - y) * x[i]
                for j in range(3):
                    h[i][j] += p * (1 - p) * x[i] * x[j]
        step = _solve3(h, g)
        w = [wi - si for wi, si in zip(w, step)]
        if max(abs(s) for s in step) < 1e-6:
            break
    return tuple(w)


def blend_prob(w: tuple[float, float, float], market: float, model: float) -> float:
    return sigmoid(w[0] * logit(market) + w[1] * logit(model) + w[2])


def arb_profit(quotes: list[tuple[float | None, float | None]], contracts: int = 10,
               fee_rate: float = 0.07) -> float | None:
    """Best locked-in profit per set (dollars) from buying every YES or every NO.

    `quotes` must cover every bracket of the event. Returns None if a side can't be
    filled on every bracket; a negative number means no arbitrage.
    """
    def per_contract_fee(price):
        return taker_fee(price, contracts, fee_rate) / contracts

    best = None
    asks = [a for _, a in quotes]
    if asks and all(a is not None for a in asks):
        best = 1.0 - sum(a + per_contract_fee(a) for a in asks)
    bids = [b for b, _ in quotes]
    if bids and all(b is not None for b in bids):
        no_costs = [1.0 - b for b in bids]
        profit = (len(bids) - 1) - sum(c + per_contract_fee(c) for c in no_costs)
        best = profit if best is None else max(best, profit)
    return None if best is None else round(best, 4)
