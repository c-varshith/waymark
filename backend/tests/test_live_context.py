import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from starlette.requests import Request

from backend.live_context import LiveContextManager, OpenMeteoProvider
import backend.live_context as live_context_module
from backend.routes.live_context import get_live_context


NOW = datetime(2026, 11, 14, 1, 0, tzinfo=timezone.utc)


class SequenceProvider:
    def __init__(self, readings):
        self.readings = iter(readings)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        value = next(self.readings)
        if isinstance(value, Exception):
            raise value
        return value


def reading(precip=0.0, is_day=1, observed_at=NOW):
    return {"precipitation_mm": precip, "weather_code": 0, "is_day": is_day,
            "observed_at": observed_at}


def request_for(manager):
    app = FastAPI()
    app.state.live_context = manager
    return Request({"type": "http", "app": app, "headers": [], "query_string": b"",
                    "method": "GET", "path": "/api/live/context", "server": ("test", 80),
                    "client": ("test", 1234), "scheme": "http"})


@pytest.mark.parametrize("precip,expected", [(0.999, False), (1.0, True), (2.0, True)])
def test_rain_enters_at_configured_threshold(precip, expected):
    manager = LiveContextManager(SequenceProvider([reading(precip)]), clock=lambda: NOW)
    result = asyncio.run(manager.poll_once())
    assert ("rain" in result["active_conditions"]) is expected


def test_rain_exit_requires_two_polls_strictly_below_exit_threshold():
    manager = LiveContextManager(SequenceProvider([
        reading(1.0), reading(0.5), reading(0.499), reading(0.499),
    ]), clock=lambda: NOW)
    assert "rain" in asyncio.run(manager.poll_once())["active_conditions"]
    assert "rain" in asyncio.run(manager.poll_once())["active_conditions"]  # equality does not exit
    assert "rain" in asyncio.run(manager.poll_once())["active_conditions"]  # first low poll
    assert "rain" not in asyncio.run(manager.poll_once())["active_conditions"]


def test_night_uses_is_day_and_rush_uses_houston_weekday_clock():
    monday_rush = datetime(2026, 11, 16, 13, 0, tzinfo=timezone.utc)  # 07:00 Houston
    manager = LiveContextManager(SequenceProvider([reading(is_day=0)]), clock=lambda: monday_rush)
    result = asyncio.run(manager.poll_once())
    assert result["active_conditions"] == ["night", "rush"]


@pytest.mark.parametrize("provider_error", [TimeoutError("late"), ValueError("bad JSON shape")])
def test_provider_failures_leave_context_unavailable_and_read_route_stays_200(provider_error):
    manager = LiveContextManager(SequenceProvider([provider_error]), clock=lambda: NOW)
    asyncio.run(manager.poll_once())
    result = get_live_context(request_for(manager))
    assert result["status"] == "unavailable"
    assert result["active_conditions"] == []
    assert result["raw_readings"] == {}


def test_open_meteo_request_uses_documented_hourly_parameters_and_units(monkeypatch):
    monkeypatch.setattr(live_context_module, "utc_now", lambda: datetime(2026, 11, 14, 2, 0, tzinfo=timezone.utc))
    seen = {}

    async def handler(request):
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"hourly": {
            "time": ["2026-11-13T19:00"], "precipitation": [0.0],
            "weather_code": [0], "is_day": [1],
        }})

    provider = OpenMeteoProvider(transport=httpx.MockTransport(handler))
    result = asyncio.run(provider())
    assert seen["hourly"] == "precipitation,weather_code,is_day"
    assert seen["timezone"] == "America/Chicago"
    assert result["precipitation_mm"] == 0.0 and result["is_day"] == 1


def test_bad_json_from_provider_is_converted_to_unavailable_status():
    async def handler(_request):
        return httpx.Response(200, content=b"not-json")

    manager = LiveContextManager(OpenMeteoProvider(transport=httpx.MockTransport(handler)),
                                 clock=lambda: NOW)
    asyncio.run(manager.poll_once())
    assert get_live_context(request_for(manager))["status"] == "unavailable"


def test_context_changed_publishes_once_per_condition_or_status_change_with_no_report_data():
    class Hub:
        def __init__(self): self.events = []
        def publish(self, name, payload): self.events.append((name, payload)); return True

    hub = Hub()
    provider = SequenceProvider([reading(0.0), reading(0.0), reading(1.0)])
    manager = LiveContextManager(provider, broadcaster=hub, clock=lambda: NOW)
    asyncio.run(manager.poll_once())
    asyncio.run(manager.poll_once())
    asyncio.run(manager.poll_once())
    assert [name for name, _ in hub.events] == ["context.changed", "context.changed"]
    assert all(payload == {} for _, payload in hub.events)


def test_stale_status_is_derived_from_last_success_age():
    clock = [NOW]
    provider = SequenceProvider([reading(), TimeoutError("offline")])
    manager = LiveContextManager(provider, clock=lambda: clock[0])
    assert asyncio.run(manager.poll_once())["status"] == "ok"
    clock[0] += timedelta(seconds=1801)
    assert asyncio.run(manager.poll_once())["status"] == "stale"
