"""Shared, source-specific definitions for rain, darkness and weekday rush."""
from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

from . import realtime_config as config


def houston_local(value: datetime) -> datetime:
    """Convert aware datetimes to Houston time; naive dataset values already mean Houston local."""
    if value.tzinfo is None:
        return value
    return value.astimezone(ZoneInfo(config.HOUSTON_TIMEZONE)).replace(tzinfo=None)


def rush_active(value: datetime) -> bool:
    local = houston_local(value)
    if local.weekday() >= 5:
        return False
    hour = local.hour + local.minute / 60 + local.second / 3600
    return any(start <= hour < end for start, end in config.RUSH_WINDOWS)


def live_rain_active(precipitation_mm: float) -> bool:
    return precipitation_mm >= config.RAIN_MM


def live_night_active(is_day: int | bool) -> bool:
    return int(is_day) == 0


def historical_flags(precipitation_mm: float, is_day: int | bool, occurred_at: datetime) -> dict[str, bool]:
    """Tag archive-linked crashes using the shared definitions (never US Accidents flags)."""
    return {
        "rain": precipitation_mm >= config.RAIN_MM,
        "night": int(is_day) == 0,
        "rush": rush_active(occurred_at),
    }
