# Roadmap

A self-running research and paper-trading system: it tests strategies, paper-trades
the ones that pass, sizes them by evidence, and shuts down the ones that stop working.

Constraints: free data only (no paid APIs, no API keys), paper trading only, everything
scheduled runs on GitHub Actions (the laptop is only for development), models are small
CPU models (scikit-learn) trained inside Actions and stored as files in the repo.

Status: [x] done · [~] in progress · [ ] not started

## Phase 1 - Brain: edge tracking, kill switch, sizing  [x]
- `brain.py`: per-strategy Bayesian estimate of edge per contract (day-clustered), P(edge > 0)
- Kill switch: after 200 settled trades, stop a strategy if P(edge > 0) < 10% or drawdown limit hit
- Sizing: quarter-Kelly from the conservative edge estimate, capped by book depth and risk limits
- State stored in the ledger; GitHub issue opened when a strategy is killed

## Phase 2 - Data engine  [x]
- `collector.py`: hourly snapshot of all ~350 open daily temperature markets (best bid/ask, sizes, volume, open interest) from 48 list calls
- Stored as compressed daily files on a separate `data` branch (main stays small)

## Phase 3 - Lab  [~]
- `lab.py`: run any strategy over any Kalshi daily series, walk-forward, fees included
- Experiment registry (`lab/experiments.jsonl`) so results are judged against how many ideas were tried
- Locked holdout: the most recent 30 days are only used for a strategy's final exam
- Longshot scan across every Kalshi daily temperature series; passing series join paper trading

## Phase 4 - Learned models  [ ]
- `ml.py`: features from prices, spread, timing, bracket position (+ forecasts where available)
- `calib` (logistic calibration) and `gbm` (gradient boosting) models, walk-forward evaluated
  against the market's own Brier score; retrained weekly in Actions; paper variants if they pass

## Phase 5 - Market making  [ ]
- Fill model from Kalshi's public trade history: would a resting order at price X have filled?
- Maker version of the longshot trade (post 1c better than the bid instead of hitting it)
- Paper: virtual resting orders, filled only if real trades cross them

## Phase 6 - Other markets  [ ]
- Kalshi vs Polymarket scanner for matching temperature markets (logs gaps; resolution sources differ)
- ETF trend-following (10-month average rule) on free Stooq prices, paper portfolio

## Phase 7 - Allocation and dashboard  [ ]
- Thompson-sampling allocator across strategies
- Dashboard: brain status per strategy, allocations, ETF equity curve, scanner, collector health

## Rules for every strategy
Rule fixed before testing · walk-forward only · fees and spread included · fills checked
against book depth · logged in the experiment registry · paper first · real money only by
explicit decision.
