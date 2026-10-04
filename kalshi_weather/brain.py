"""The brain: how much to trust each strategy, how big to trade it, and when to stop it.

Edge is measured as net P&L per contract (after fees). The estimate is Bayesian with a
skeptical prior centred on zero, and its uncertainty is computed from daily totals,
because trades settled on the same day are not independent. A strategy is:

  evaluating - fewer than `min_trades` settled; trades at base size
  active     - judged; sized by quarter-Kelly on a conservative edge estimate
  killed     - P(edge > 0) fell below `kill_prob`, or drawdown passed `max_drawdown`;
               stays stopped until a person revives it (`brain --revive NAME`)
"""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone

STATE_SCHEMA = """
CREATE TABLE IF NOT EXISTS brain_state (
  strategy TEXT PRIMARY KEY,
  status TEXT NOT NULL,
  reason TEXT,
  changed_at TEXT NOT NULL
);
"""


@dataclass
class BrainConfig:
    prior_sd: float = 0.01          # prior on edge per contract: Normal(0, 1c)
    min_trades: int = 200           # settled trades before a strategy is judged
    kill_prob: float = 0.10         # stop when P(edge > 0) drops below this
    max_drawdown: float = 100.0     # stop when drawdown, in 10-contract units, passes $100
    bankroll: float = 1000.0        # paper bankroll per strategy, for Kelly sizing
    kelly_fraction: float = 0.25
    base_contracts: int = 10
    max_contracts: int = 100


@dataclass
class Estimate:
    trades: int
    contracts: int
    mean: float                     # observed net P&L per contract ($)
    se: float                       # day-clustered standard error ($)
    post_mean: float
    post_sd: float
    p_positive: float
    drawdown: float                 # max drawdown in 10-contract units ($)


def _norm_cdf(z: float) -> float:
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2.0)))


def estimate(trades: list[dict], cfg: BrainConfig = BrainConfig()) -> Estimate:
    """Posterior for edge per contract from settled trades (dicts with pnl, contracts, settled_at)."""
    settled = sorted((t for t in trades if t.get("pnl") is not None),
                     key=lambda t: t.get("settled_at") or "")
    n_contracts = sum(t["contracts"] for t in settled)
    if not settled or not n_contracts:
        return Estimate(0, 0, 0.0, math.inf, 0.0, cfg.prior_sd, 0.5, 0.0)
    pnl = sum(t["pnl"] for t in settled)
    mean = pnl / n_contracts
    days = defaultdict(lambda: [0.0, 0])
    for t in settled:
        d = days[(t.get("settled_at") or "")[:10]]
        d[0] += t["pnl"]
        d[1] += t["contracts"]
    n = len(days)
    if n >= 2:      # ratio-estimator variance over daily clusters
        resid = sum((p - mean * c) ** 2 for p, c in days.values())
        se = math.sqrt(n / (n - 1) * resid) / n_contracts
    else:
        se = math.inf
    prior_var = cfg.prior_sd ** 2
    if se == math.inf or se == 0:
        post_mean, post_var = 0.0, prior_var
    else:
        post_var = 1.0 / (1.0 / prior_var + 1.0 / se ** 2)
        post_mean = post_var * mean / se ** 2
    post_sd = math.sqrt(post_var)
    peak = run = dd = 0.0
    for t in settled:                       # drawdown normalized to 10-contract trades
        run += t["pnl"] * 10.0 / t["contracts"]
        peak = max(peak, run)
        dd = max(dd, peak - run)
    return Estimate(len(settled), n_contracts, mean, se, post_mean, post_sd,
                    _norm_cdf(post_mean / post_sd), dd)


def kelly_contracts(est: Estimate, price: float, cfg: BrainConfig = BrainConfig()) -> int:
    """Quarter-Kelly size for a contract costing `price`, using edge = post_mean - post_sd.

    For a contract paying $1, Kelly stakes (q - p) / (1 - p) of bankroll, with q = p + edge.
    Until the conservative edge is positive the strategy trades base size.
    """
    edge = est.post_mean - est.post_sd
    if edge <= 0 or not 0 < price < 1:
        return cfg.base_contracts
    q = min(price + edge, 0.999)
    stake = cfg.kelly_fraction * (q - price) / (1 - price) * cfg.bankroll
    return int(max(cfg.base_contracts, min(cfg.max_contracts, stake / price)))


class Brain:
    def __init__(self, ledger, cfg: BrainConfig = BrainConfig()):
        self.ledger, self.cfg = ledger, cfg
        ledger.db.executescript(STATE_SCHEMA)

    def _trades(self, strategy: str) -> list[dict]:
        return [dict(r) for r in self.ledger.db.execute(
            "SELECT pnl, contracts, settled_at FROM trades WHERE strategy=? AND status='settled'",
            (strategy,))]

    def status(self, strategy: str) -> str:
        r = self.ledger.db.execute("SELECT status FROM brain_state WHERE strategy=?",
                                   (strategy,)).fetchone()
        return r[0] if r else "evaluating"

    def _set(self, strategy: str, status: str, reason: str = "") -> None:
        self.ledger.db.execute(
            "INSERT INTO brain_state (strategy, status, reason, changed_at) VALUES (?,?,?,?) "
            "ON CONFLICT(strategy) DO UPDATE SET status=excluded.status, reason=excluded.reason, "
            "changed_at=excluded.changed_at",
            (strategy, status, reason, datetime.now(timezone.utc).isoformat()))
        self.ledger.db.commit()

    def review(self, strategy: str) -> tuple[str, Estimate, str | None]:
        """Update a strategy's status. Returns (status, estimate, alert message if just killed)."""
        est = estimate(self._trades(strategy), self.cfg)
        status = self.status(strategy)
        if status == "killed":
            return status, est, None
        reason = None
        if est.drawdown > self.cfg.max_drawdown:
            reason = f"drawdown ${est.drawdown:.2f} (10-contract units) passed ${self.cfg.max_drawdown:.0f}"
        elif est.trades >= self.cfg.min_trades and est.p_positive < self.cfg.kill_prob:
            reason = (f"P(edge > 0) = {est.p_positive:.1%} after {est.trades} trades "
                      f"(below {self.cfg.kill_prob:.0%})")
        if reason:
            self._set(strategy, "killed", reason)
            return "killed", est, f"{strategy} stopped: {reason}"
        new = "active" if est.trades >= self.cfg.min_trades else "evaluating"
        if new != status:
            self._set(strategy, new)
        return new, est, None

    def contracts(self, strategy: str, price: float) -> int:
        """0 if killed, otherwise the Kelly size for this price."""
        status, est, _ = self.review(strategy)
        return 0 if status == "killed" else kelly_contracts(est, price, self.cfg)

    def revive(self, strategy: str) -> None:
        self._set(strategy, "evaluating", "revived by hand")
