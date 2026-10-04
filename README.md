# Kalshi Weather Bot (paper trading)

Trades Kalshi "daily high temperature" markets with fake money. It turns free
weather-model forecasts (GFS, ECMWF, ICON via Open-Meteo) into a probability
for each temperature bracket. When Kalshi's price is far enough off after
fees, it logs a paper trade, then settles it against Kalshi's real result.

No API keys or accounts are needed. Everything uses public data.

## Setup (once)

```bash
cd kalshi-weather
python -m venv .venv
# Mac/Linux:
source .venv/bin/activate
# Windows:
.venv\Scripts\activate
pip install -r requirements.txt
python -m pytest -q          # should say 19 passed
```

## Step 1: Check the tickers

```bash
python -m kalshi_weather discover
```

This lists Kalshi's high-temperature series. If a ticker in
`kalshi_weather/config.py` (KXHIGHNY, KXHIGHCHI, and so on) is missing or
renamed, fix it there. Also check each market's rules page on kalshi.com to
confirm the settlement station matches the coordinates in `config.py`.

## Step 2: Backtest (does an edge exist?)

```bash
python -m kalshi_weather backtest --days 180
# or a single city first, which is faster:
python -m kalshi_weather backtest --cities nyc --days 120
```

The backtest is walk-forward: every decision uses only the forecast and prices
that existed at 3pm the day before, plus a model fitted on earlier days only.
The first run is slow because it caches Kalshi data in `data/cache`; reruns
are fast.

How to read the output:
- **model_brier < market_brier** means your probabilities are more accurate than
  the market's. This is the most important number.
- **roi** is the return after fees.
- **avg_prob_sum** should be close to 1.0. If it isn't, the bracket parsing is
  off; tell Claude.
- **blend_brier vs blend_mkt_brier**: a walk-forward blend of the market price
  and the model (`blend.py`), scored on the same brackets as the market. If the
  blend beats the market, the model adds information the price doesn't have.
  **model_weight** is how much the blend trusts the model (0 = not at all).
- **arb_events / arb_checked**: events where buying every bracket's YES (or NO)
  locked in a profit after fees at decision time.

To trade on the blended probability instead of the raw model:

```bash
python -m kalshi_weather backtest --days 180 --prob blend
python -m kalshi_weather paper --prob blend
```

The backtest also writes `data/model_params.json` (the fitted forecast bias
and error per city), which paper trading uses.

## Step 3: Paper trade daily

```bash
python -m kalshi_weather settle   # settle yesterday's trades
python -m kalshi_weather paper    # place trades for tomorrow
python -m kalshi_weather report   # running results
```

Run this once a day in the afternoon, around 3pm Eastern, to match the
backtest. To automate it:

- **Mac/Linux:** run `crontab -e` and add:
  `0 15 * * * cd ~/Documents/kalshi-weather && .venv/bin/python -m kalshi_weather settle && .venv/bin/python -m kalshi_weather paper`
- **Windows:** in Task Scheduler, create a daily 3pm task that runs
  `.venv\Scripts\python.exe -m kalshi_weather paper` with the project folder as "Start in". Add a second action for `settle`.

## Go / no-go rules

| Result | Action |
|---|---|
| Backtest: model_brier >= market_brier | No edge yet. Improve the model (ideas below) before paper trading. |
| 100+ settled paper trades, ROI > 0, results roughly match the backtest | Consider $50-100 real money. |
| Paper results far worse than the backtest | The backtest was optimistic. Find out why before going further. |

## Longshot strategy (the one with evidence of an edge)

Research over 180 days and 7 cities found that the forecast model does **not**
beat the market (model Brier 0.129 vs market 0.106). What does show up is the
favorite-longshot bias: brackets priced at a few cents settle YES less often
than their price implies (mid under 5c: 1.1% won vs 2.8% implied).

Rule: on the day before the event, **buy NO on every bracket whose best YES bid
is 1-4c**, paying 1 - YES bid, sized to what the book offers.

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

All 7 cities were positive. By 9am on the event day the edge is gone (+0.11c,
CI -0.41 to +0.61). Each win earns 1-4c; each loss costs 96-99c, so a bad week
can wipe out a month of gains. Because I tested many ideas and hours, the real
edge is probably smaller than these numbers; about 1,500 live trades are needed
to tell a ~0.5c edge from zero.

Ideas that did **not** hold up: the forecast model (alone, blended with the
market, or as a filter on longshots), buying favorites, wider longshots (5-10c),
bracket-sum arbitrage, and buying NO on brackets the station readings had already
ruled out (they had no bids left by then).

All six hours run as separate paper variants (`VARIANTS` in `longshot.py`), each
with its own ledger name. They mostly trade the same brackets, so they show which
timing works best; real money should use one of them, not all.

```bash
python -m kalshi_weather backtest --strategy longshot --days 180
python -m kalshi_weather backtest --strategy longshot --hour 17
python -m kalshi_weather paper --strategy longshot --variant longshot_5pm   # trade now
python -m kalshi_weather paper --strategy longshot --scheduled             # each variant at its hour
python -m kalshi_weather report      # per-variant results; "edge vs market" is the number to watch
```

## Running it for free on GitHub Actions (laptop can be off)

Two workflows run on GitHub's servers, free on a public repo:

- `paper.yml` (every hour at :07): settles finished trades, runs each longshot variant in
  the cities where it is currently that variant's local hour, posts and checks maker
  orders, updates the brain, updates the ETF paper portfolio once a day, commits the
  ledger to `ledger/paper.db`, and rebuilds the dashboard on GitHub Pages
  (https://schmiedey.github.io/kalshi-weather/).
- `collect.yml` (every hour at :37): saves prices for every open Kalshi daily temperature
  market to the `data` branch, building a private price history.

GitHub can start scheduled runs late when it is busy; a run that slips past the hour
skips that variant for that city that day.

## The full system

| Part | What it does |
|---|---|
| Strategies | `longshot_*`: buy NO on brackets with YES at 1-4c, the day before, at six times of day. `maker_5pm`: same idea, but posts an order 1c better and waits for a real trade to fill it. ETF trend: holds SPY/EFA/IEF/GLD/DBC only while above their 10-month average. |
| Brain (`brain.py`) | For each strategy: a skeptical estimate of edge per contract (starts at 0 +/- 1c), P(edge > 0), and a kill switch (stops a strategy after 200 trades if P(edge > 0) < 10%, or on a large drawdown, and opens a GitHub issue). Sizes trades by quarter-Kelly and shifts size toward the strategy most likely to be best (Thompson sampling). |
| Lab (`lab.py`) | Tests ideas walk-forward with fees. Every test is logged in `lab/experiments.jsonl`, and the last 30 days are locked for a one-time final exam. `python -m kalshi_weather lab list` shows them all. |
| Models (`ml.py`) | Small scikit-learn models (logistic calibration, gradient boosting) on market prices, judged against the market's own Brier score. |
| Fills (`mm.py`) | Uses Kalshi's public trade history to decide whether a resting order would have filled. |
| Other venues | `polymarket.py` (longshot test on Polymarket), `etf.py` (free Yahoo prices). |
| Data (`collector.py`) | The hourly market snapshots. |
| Dashboard (`dashboard.py`) | Static HTML page: P&L, brain, maker orders, ETF, research results, lab, collector health. |

## How it works

| File | Job |
|---|---|
| `kalshi.py` | Kalshi public API: markets, order books, price history |
| `weather.py` | Open-Meteo live forecasts and historical forecasts with no lookahead |
| `model.py` | Forecast to bracket probabilities. Fits bias and sigma by max-likelihood using each day's winning bracket, including tail brackets. |
| `strategy.py` | Kalshi taker fee, edge check, YES/NO choice |
| `longshot.py` | Longshot strategy: signal, backtest, paper trading, bootstrap stats |
| `blend.py` | Model + market blend and bracket-sum arbitrage checks |
| `backtest.py` | Walk-forward backtest |
| `paper.py` / `ledger.py` | Live paper trading and the SQLite ledger (`data/paper.db`) |

## Ideas once it runs (where real edge usually comes from)

1. **Per-season error**: forecast error is larger in spring and fall. Fit sigma by month.
2. **Weight models by skill**: ECMWF is usually best. Weight each model by its recent error instead of a plain average.
3. **Model disagreement**: when the models disagree, raise sigma for that day.
4. **NWS's own forecast**: add the api.weather.gov point forecast as another input.
5. **Limit orders**: post bids instead of paying the ask. This saves the spread but needs fill modeling.
6. **Bracket sums**: if YES asks across an event add up to well under $1.00, that is close to free money.

## Known limits

- Kalshi moves markets settled before its "historical cutoff" (a few months back)
  to `/historical/markets`; the client merges both endpoints and falls back to
  historical candlesticks automatically.
- The backtest only uses model runs that were public at the decision time, so for
  the afternoon peak it uses ~1.5-2 day old runs (Open-Meteo `previous_day2`).
  Live trading at 3pm uses fresher runs, so live forecasts are somewhat better
  than the backtest's.

- The simulated tests prove the logic is correct, not that a real edge exists. Only the real backtest and paper results can tell you that.
- Kalshi settles on the NWS climate report, which uses local *standard* time days. Open-Meteo uses local clock time, so the two differ by an hour in summer (minor).
- Paper fills assume you get the full order at the best ask. Real books can be thin, so check `yes_ask_qty` before trusting large sizes.
- This is a research tool, not financial advice.
