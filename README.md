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

Rule: at 3pm local the day before, **buy NO on every bracket whose best YES bid is
1-4c**, paying 1 - YES bid, sized to what the book offers.

| Test (10 contracts per trade, after fees and spread) | Trades | Losses | c/contract | 90% CI |
|---|---|---|---|---|
| First 90 days | 946 | 12 | +0.92 | +0.23 to +1.54 |
| Last 90 days | 1,164 | 14 | +0.76 | +0.16 to +1.28 |
| Same rule at 11am / 1pm the day before | ~2,200 | ~35 | +0.48 / +0.56 | both above 0 |
| All 180 days | 2,110 | 26 | +0.83 | +0.37 to +1.27 |

All 7 cities were positive. Expect roughly +0.5-0.8c per contract, about 12
trades a day across 7 cities. Each win earns 1-4c; each loss costs 96-99c, so
a bad week can wipe out a month of gains. Ideas that did **not** hold up: the
forecast model (alone or as a filter on longshots), buying favorites, bracket-sum
arbitrage, and buying NO on brackets the station readings had already ruled
out (they had no bids left by then).

```bash
python -m kalshi_weather backtest --strategy longshot --days 180
python -m kalshi_weather paper --strategy longshot --contracts 10
python -m kalshi_weather report      # per-strategy results; "edge vs market" is the number to watch
```

To match the backtest (3pm in each city's own time zone), run it hourly and let
it pick the cities where it is 3pm:

`5 * * * * cd ~/Documents/kalshi-weather && .venv/bin/python -m kalshi_weather settle && .venv/bin/python -m kalshi_weather paper --strategy longshot --at-local-hour 15`

## Running it for free on GitHub Actions (laptop can be off)

`.github/workflows/paper.yml` runs on GitHub's servers every hour from 19:07 to
23:07 UTC. Each run settles finished trades, paper-trades longshots in the cities
where it is 3pm local, and commits the ledger to `ledger/paper.db`. To see results,
open the latest run's log in the Actions tab (the `report` step), or pull the
repo and run `python -m kalshi_weather --data ledger report`.

Cost: free on a public repo. On a private repo it uses about 150 of the monthly
free Actions minutes (5 short runs a day). GitHub can start scheduled runs late
when it is busy; a run that starts after 4pm local skips that city for the day.

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
