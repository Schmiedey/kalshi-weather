"""Every US daily temperature series on Kalshi (highs and lows), with local time zone.

The longshot strategy only needs prices and a time zone, so it can trade all of
these. The forecast model needs station coordinates and only covers `config.CITIES`.
Each series settles on the NWS climate report for the station in its rules.
"""
from dataclasses import dataclass

ET, CT, MT, PT, AZ = ("America/New_York", "America/Chicago", "America/Denver",
                      "America/Los_Angeles", "America/Phoenix")


@dataclass(frozen=True)
class Market:
    key: str          # e.g. "high_nyc"
    series: str
    tz: str
    kind: str         # "high" or "low"


_HIGH = {"nyc": ("KXHIGHNY", ET), "chi": ("KXHIGHCHI", CT), "mia": ("KXHIGHMIA", ET),
         "aus": ("KXHIGHAUS", CT), "den": ("KXHIGHDEN", MT), "lax": ("KXHIGHLAX", PT),
         "phl": ("KXHIGHPHIL", ET), "atl": ("KXHIGHTATL", ET), "bos": ("KXHIGHTBOS", ET),
         "dal": ("KXHIGHTDAL", CT), "dc": ("KXHIGHTDC", ET), "ewr": ("KXHIGHTEWR", ET),
         "hou": ("KXHIGHTHOU", CT), "lv": ("KXHIGHTLV", PT), "msp": ("KXHIGHTMIN", CT),
         "nola": ("KXHIGHTNOLA", CT), "okc": ("KXHIGHTOKC", CT), "phx": ("KXHIGHTPHX", AZ),
         "san": ("KXHIGHTSAN", PT), "satx": ("KXHIGHTSATX", CT), "sdf": ("KXHIGHTSDF", ET),
         "sea": ("KXHIGHTSEA", PT), "sfo": ("KXHIGHTSFO", PT), "ttn": ("KXHIGHTTTN", ET)}
_LOW = {"atl": ET, "aus": CT, "bos": ET, "chi": CT, "dal": CT, "dc": ET, "den": MT, "ewr": ET,
        "hou": CT, "lax": PT, "lv": PT, "mia": ET, "min": CT, "nola": CT, "nyc": ET, "okc": CT,
        "phil": ET, "phx": AZ, "san": PT, "satx": CT, "sdf": ET, "sea": PT, "sfo": PT, "ttn": ET}

MARKETS = {m.key: m for m in
           [Market(f"high_{k}", s, tz, "high") for k, (s, tz) in _HIGH.items()] +
           [Market(f"low_{k}", f"KXLOWT{k.upper()}", tz, "low") for k, tz in _LOW.items()]}

# The 7 series the longshot strategy was first developed and tested on.
ORIGINAL = ("high_nyc", "high_chi", "high_mia", "high_aus", "high_den", "high_lax", "high_phl")
