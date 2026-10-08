# Kalshi Weather Longshot (paper trading)

Paper-trades one rule on Kalshi "daily high temperature" markets: at 5pm local time
on the day before the event, **buy NO on every bracket whose best YES bid is 1-4c**,
paying 1 - YES bid, sized to what the book offers. It settles each trade against
Kalshi's real result.

No API keys or accounts are needed. Everything uses public data, and no real money
is involved.

Dashboard: https://schmiedey.github.io/kalshi-weather/

## Why this rule

Cheap brackets settle YES less often than their price implies: in 180 days on 7 cities,
brackets with a mid price under 5c won 1.1% of the time against 2.8% implied. This is the
favorite-longshot bias, which is documented across betting markets.

The same rule at different local hours on the day before (180 days, 7 cities,
10 contracts per trade, after fees and spread):

| Hour | Trades | Losses | c/contract | 90% CI | 1st / 2nd half |
|---|---|---|---|---|---|
| noon | 2,213 | 36 | +0.48 | +0.03 to +0.94 | +0.88 / +0.14 |
| 3pm | 2,110 | 26 | +0.83 | +0.37 to +1.27 | +0.92 / +0.76 |
| **5pm** | 2,114 | 19 | **+1.09** | +0.70 to +1.47 | +0.99 / +1.18 |
| 7pm | 2,157 | 20 | +1.02 | +0.63 to +1.38 | +1.18 / +0.87 |
| 9pm | 2,139 | 23 | +0.83 | +0.37 to +1.27 | +0.94 / +0.73 |
| 11pm | 2,133 | 29 | +0.55 | +0.07 to +1.01 | +0.45 / +0.65 |

The hours trade mostly the same brackets, so only 5pm, the strongest, runs.

**Limits:**
- Each win earns 1-4c per contract and each loss costs 96-99c, so a bad week can wipe out a month of gains.
- Many ideas and hours were tested, so the real edge is probably smaller than these numbers.
- About 1,500 settled trades are needed to tell a ~0.5c edge from zero.
- The backtest covers April to October only, so winter weather is untested.
- Order books are thin: the median bracket offers about 126 contracts at the best price, which caps how much the edge can earn.

Everything else that was tried failed or was dropped, and the code for it has been removed:
forecast and ML models, the same rule on Kalshi's 41 thinner temperature series,
Polymarket longshots, maker orders, Kalshi liquidity-incentive pools and an ETF trend
portfolio. `lab/experiments.jsonl` records each test and its result.

## Setup (once)

```bash
cd kalshi-weather
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q
```

## Commands

```bash
python -m kalshi_weather backtest --days 180            # 5pm by default; --hour 15 for 3pm
python -m kalshi_weather paper                          # paper-trade tomorrow's brackets now
python -m kalshi_weather paper --scheduled              # only cities where it is 5pm local now
python -m kalshi_weather settle                         # settle finished trades
python -m kalshi_weather report                         # results; "edge vs market" is the number to watch
python -m kalshi_weather brain                          # edge estimate, status and next size
python -m kalshi_weather dashboard                      # write site/index.html
```

## Running on GitHub Actions (laptop can be off)

`paper.yml` runs every hour at :05 on GitHub's servers, free on a public repo. It settles
finished trades and paper-trades the cities where it is currently 5pm local. It then
updates the brain, commits the ledger to `ledger/paper.db` and rebuilds the dashboard on
GitHub Pages.

GitHub can start scheduled runs late when it is busy. A run that slips past the hour
skips that city for that day.

## The brain

`brain.py` keeps a skeptical estimate of the edge: it starts at 0 ± 1c and moves only as
trades settle. It sizes trades from that estimate and stops the strategy automatically,
opening a GitHub issue, if P(edge > 0) falls below 10% after 200 trades or the drawdown
grows too large.

**What to watch:** the loss rate. The backtest's was about 1.2%. A loss rate well above
2-3% after many trades means the edge is gone.
