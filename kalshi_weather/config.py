"""Cities, endpoints and strategy settings.

Station coordinates are the official NWS stations Kalshi settles on (per each
market's rules). Verify them against the rules on kalshi.com before going live.
"""
from dataclasses import dataclass

KALSHI_PROD = "https://api.elections.kalshi.com/trade-api/v2"
KALSHI_DEMO = "https://demo-api.kalshi.co/trade-api/v2"


@dataclass(frozen=True)
class City:
    key: str
    series: str
    name: str
    lat: float
    lon: float
    tz: str
    station: str


CITIES = {c.key: c for c in [
    City("nyc", "KXHIGHNY", "New York (Central Park)", 40.7789, -73.9692, "America/New_York", "KNYC"),
    City("chi", "KXHIGHCHI", "Chicago (Midway)", 41.7868, -87.7522, "America/Chicago", "KMDW"),
    City("mia", "KXHIGHMIA", "Miami", 25.7959, -80.2870, "America/New_York", "KMIA"),
    City("aus", "KXHIGHAUS", "Austin", 30.1945, -97.6699, "America/Chicago", "KAUS"),
    City("den", "KXHIGHDEN", "Denver", 39.8466, -104.6562, "America/Denver", "KDEN"),
    City("lax", "KXHIGHLAX", "Los Angeles (LAX)", 33.9382, -118.3866, "America/Los_Angeles", "KLAX"),
    City("phl", "KXHIGHPHIL", "Philadelphia", 39.8721, -75.2411, "America/New_York", "KPHL"),
]}


@dataclass
class StrategyConfig:
    min_edge: float = 0.06        # required edge per contract AFTER fees (probability units)
    min_price: float = 0.04       # skip lottery tickets
    max_price: float = 0.94       # skip near-certain contracts (tiny upside)
    contracts_per_trade: int = 10
    max_trades_per_event: int = 1
    fee_rate: float = 0.07        # Kalshi taker fee multiplier
    prob_clip: float = 0.02       # never trust the model beyond [clip, 1-clip]
    default_sigma: float = 2.5    # deg F, used until enough history is fitted
    min_fit_events: int = 15
    prob_source: str = "model"    # "model": forecast only; "blend": model + market price (see blend.py)
