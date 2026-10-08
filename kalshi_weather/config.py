"""Cities and endpoints.

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
