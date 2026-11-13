"""Cached city-wide Open-Meteo context; providers are polled only by a background task."""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable
from zoneinfo import ZoneInfo

import httpx

from . import realtime_config as config
from .conditions import live_night_active, live_rain_active, rush_active

log = logging.getLogger("waymark.live_context")
PROVIDER_URL = "https://api.open-meteo.com/v1/forecast"
HOUSTON_LAT = 29.7604
HOUSTON_LNG = -95.3698
POLL_SECONDS = 600
FRESH_SECONDS = 1800
CIRCUIT_FAILURES = 5
CIRCUIT_RESET_SECONDS = 600
SOURCE = "Open-Meteo"
SCOPE = "city-wide, one point at the Houston centre"
CAVEAT = ("One city-centre weather model point is used for every scored cell; it is not a "
          "street-level observation or crash forecast. Current precipitation uses the provider's "
          "backward-looking current interval, while the historical lift was built from hourly archive data.")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def _parse_observed(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("Open-Meteo current.time is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(config.HOUSTON_TIMEZONE))
    return parsed.astimezone(timezone.utc)


class OpenMeteoProvider:
    """One bounded request for the Houston centre; no API key is used by this endpoint."""

    def __init__(self, url: str | None = None,
                 transport: httpx.AsyncBaseTransport | None = None):
        self.url = url or os.getenv("WAYMARK_OPEN_METEO_BASE_URL", PROVIDER_URL)
        self.transport = transport

    async def __call__(self) -> dict[str, Any]:
        params = {
            "latitude": HOUSTON_LAT,
            "longitude": HOUSTON_LNG,
            "hourly": "precipitation,weather_code,is_day",
            "timezone": config.HOUSTON_TIMEZONE,
        }
        async with httpx.AsyncClient(timeout=3.0, transport=self.transport) as client:
            response = await client.get(self.url, params=params)
            response.raise_for_status()
            payload = response.json()
        hourly = payload.get("hourly") if isinstance(payload, dict) else None
        if not isinstance(hourly, dict):
            raise ValueError("Open-Meteo response has no hourly object")
        times = hourly.get("time")
        precipitation_values = hourly.get("precipitation")
        weather_codes = hourly.get("weather_code")
        day_values = hourly.get("is_day")
        if not all(isinstance(value, list) for value in (times, precipitation_values, weather_codes, day_values)):
            raise ValueError("Open-Meteo hourly arrays are malformed")
        now = utc_now()
        candidates = []
        for index, raw_time in enumerate(times):
            if not all(index < len(values) for values in (precipitation_values, weather_codes, day_values)):
                continue
            observed = _parse_observed(raw_time)
            if observed <= now:
                candidates.append((observed, index))
        if not candidates:
            raise ValueError("Open-Meteo has no completed hourly reading")
        observed_at, index = max(candidates)
        precipitation = precipitation_values[index]
        weather_code = weather_codes[index]
        is_day = day_values[index]
        if (not isinstance(precipitation, (int, float)) or precipitation < 0
                or not isinstance(weather_code, int) or is_day not in (0, 1)):
            raise ValueError("Open-Meteo current readings are malformed")
        return {
            "precipitation_mm": float(precipitation),
            "weather_code": weather_code,
            "is_day": int(is_day),
            "observed_at": observed_at,
        }


class LiveContextManager:
    def __init__(self, provider: Callable[[], Awaitable[dict[str, Any]]] | None = None,
                 broadcaster: Any = None, clock: Callable[[], datetime] = utc_now,
                 poll_seconds: float = POLL_SECONDS, fresh_seconds: float = FRESH_SECONDS):
        self.provider = provider or OpenMeteoProvider()
        self.broadcaster = broadcaster
        self.clock = clock
        self.poll_seconds = poll_seconds
        self.fresh_seconds = fresh_seconds
        self.last_success: datetime | None = None
        self.raw: dict[str, Any] | None = None
        self.rain_active = False
        self._rain_exit_polls = 0
        self._failures = 0
        self._circuit_open_until: datetime | None = None
        self._last_published_signature: tuple[Any, ...] | None = None

    def _status(self, now: datetime) -> str:
        if self.last_success is None:
            return "unavailable"
        age = max(0.0, (now - self.last_success).total_seconds())
        return "ok" if age <= self.fresh_seconds else "stale"

    def snapshot(self, now: datetime | None = None) -> dict[str, Any]:
        current = now or self.clock()
        if current.tzinfo is None:
            current = current.replace(tzinfo=timezone.utc)
        status = self._status(current)
        observed = self.raw.get("observed_at") if self.raw else None
        age = max(0.0, (current - self.last_success).total_seconds()) if self.last_success else None
        conditions: list[str] = []
        if self.raw:
            if self.rain_active:
                conditions.append("rain")
            if live_night_active(self.raw["is_day"]):
                conditions.append("night")
        if rush_active(current.astimezone(ZoneInfo(config.HOUSTON_TIMEZONE))):
            conditions.append("rush")
        return {
            "status": status,
            "source": SOURCE,
            "scope": SCOPE,
            "observed_at": _iso(observed),
            "fetched_at": _iso(self.last_success),
            "age_seconds": age,
            "active_conditions": conditions,
            "raw_readings": ({"precipitation_mm": self.raw["precipitation_mm"],
                              "weather_code": self.raw["weather_code"],
                              "is_day": self.raw["is_day"]} if self.raw else {}),
            "thresholds": {"rain_mm": config.RAIN_MM,
                           "rain_exit_mm": config.RAIN_EXIT_MM,
                           "rain_exit_polls": 2,
                           "rush_windows": [[a, b] for a, b in config.RUSH_WINDOWS],
                           "timezone": config.HOUSTON_TIMEZONE},
            "caveat": CAVEAT,
        }

    async def poll_once(self) -> dict[str, Any]:
        now = self.clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        if self._circuit_open_until and now < self._circuit_open_until:
            return self.snapshot(now)
        if self._circuit_open_until and now >= self._circuit_open_until:
            self._circuit_open_until = None
            self._failures = 0
        try:
            reading = await self.provider()
            precip = float(reading["precipitation_mm"])
            day = int(reading["is_day"])
            if live_rain_active(precip):
                self.rain_active = True
                self._rain_exit_polls = 0
            elif precip < config.RAIN_EXIT_MM:
                self._rain_exit_polls += 1
                if self._rain_exit_polls >= 2:
                    self.rain_active = False
            else:
                self._rain_exit_polls = 0
            self.raw = {"precipitation_mm": precip, "weather_code": int(reading["weather_code"]),
                        "is_day": day, "observed_at": reading.get("observed_at") or now}
            if isinstance(self.raw["observed_at"], str):
                self.raw["observed_at"] = _parse_observed(self.raw["observed_at"])
            self.last_success = now
            self._failures = 0
        except Exception as exc:
            self._failures += 1
            log.warning("Open-Meteo refresh failed (%s consecutive): %s", self._failures, type(exc).__name__)
            if self._failures >= CIRCUIT_FAILURES:
                self._circuit_open_until = now + timedelta(seconds=CIRCUIT_RESET_SECONDS)

        after = self.snapshot(now)
        signature = (tuple(after["active_conditions"]), after["status"])
        if signature != self._last_published_signature and self.broadcaster is not None:
            try:
                self.broadcaster.publish("context.changed", {})
            except Exception:
                log.warning("Could not publish context.changed", exc_info=True)
        self._last_published_signature = signature
        return after

    async def run(self) -> None:
        while True:
            await self.poll_once()
            await asyncio.sleep(self.poll_seconds)
