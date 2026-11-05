"""String timeframe names used by agents, and the integer codes MT5 expects."""

from __future__ import annotations

TIMEFRAMES: dict[str, int] = {
    "M1": 1,
    "M2": 2,
    "M3": 3,
    "M4": 4,
    "M5": 5,
    "M6": 6,
    "M10": 10,
    "M12": 12,
    "M15": 15,
    "M20": 20,
    "M30": 30,
    "H1": 16385,
    "H2": 16386,
    "H3": 16387,
    "H4": 16388,
    "H6": 16390,
    "H8": 16392,
    "H12": 16396,
    "D1": 16408,
    "W1": 32769,
    "MN1": 49153,
}

# Seconds used only to advance a paging cursor past the last bar.
TIMEFRAME_SECONDS: dict[str, int] = {
    "M1": 60,
    "M2": 120,
    "M3": 180,
    "M4": 240,
    "M5": 300,
    "M6": 360,
    "M10": 600,
    "M12": 720,
    "M15": 900,
    "M20": 1200,
    "M30": 1800,
    "H1": 3600,
    "H2": 7200,
    "H3": 10800,
    "H4": 14400,
    "H6": 21600,
    "H8": 28800,
    "H12": 43200,
    "D1": 86400,
    "W1": 604800,
    "MN1": 2592000,
}


def parse_timeframe(name: str) -> int:
    key = name.strip().upper()
    if key not in TIMEFRAMES:
        known = ", ".join(TIMEFRAMES)
        raise ValueError(f"unknown timeframe {name!r}; expected one of {known}")
    return TIMEFRAMES[key]
