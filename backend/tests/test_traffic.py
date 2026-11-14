from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import create_engine, inspect, select
from sqlalchemy.orm import sessionmaker

from backend import realtime_config as config
from backend.models_ops import (ConditionSnapshot, TrafficRequestCounter,
                                ensure_condition_snapshot_weather_nullable)
from backend.routes.traffic import get_traffic, watch_cell
from backend.schemas import TrafficWatchRequest
from backend.traffic import (ConditionHistoryWriter, TrafficManager, TomTomTrafficProvider,
                             _reserve_request_sync, congestion_bucket, congestion_ratio,
                             densest_crash_points, prune_condition_history, quota_limits)
from scripts.export_condition_log import write_csv


def test_traffic_ratio_buckets_and_missing_speed_edges():
    assert congestion_ratio(100, 100) == 0
    assert congestion_bucket(congestion_ratio(90, 100)) == "free"
    assert congestion_bucket(congestion_ratio(80, 100)) == "moderate"
    assert congestion_bucket(congestion_ratio(50, 100)) == "heavy"
    assert congestion_bucket(0.15) == "moderate"
    assert congestion_bucket(0.35) == "heavy"
    assert congestion_bucket(congestion_ratio(120, 100)) == "free"
    assert congestion_ratio(10, 0) is None
    assert congestion_bucket(None) is None


def test_quota_math_for_eight_cells_and_attended_window():
    assert quota_limits(20_000, 0.8, 530) == (16_000, 530)
    assert 8 * 4 == 32  # requests per attended hour at 15-minute polling
    assert 530 / 32 == pytest.approx(16.5625)
    assert 8 * 4 * 24 == 768  # continuous polling would exceed the daily cap


def test_densest_crash_bin_is_used_instead_of_cell_center():
    points = densest_crash_points(["cell-a"], [
        ("cell-a", 29.7600, -95.3700), ("cell-a", 29.7601, -95.3701),
        ("cell-a", 29.7700, -95.3800),
    ])
    assert points["cell-a"] == pytest.approx((29.76005, -95.37005))


def test_tomtom_provider_explicitly_requests_mph_without_logging_key():
    seen: dict[str, object] = {}

    def respond(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"flowSegmentData": {
            "frc": "FRC3", "currentSpeed": 20, "freeFlowSpeed": 40, "confidence": 0.8,
            "roadClosure": False,
            "coordinates": {"coordinate": [{"latitude": 29.7, "longitude": -95.4}]},
        }})

    async def check():
        provider = TomTomTrafficProvider("secret-test-key", httpx.MockTransport(respond))
        return await provider.flow(29.7, -95.4)

    reading = asyncio.run(check())
    assert seen["unit"] == "mph"
    assert seen["key"] == "secret-test-key"
    assert reading["frc"] == "FRC3"
    assert reading["current_speed"] == 20


def test_provider_failure_and_unconfigured_key_are_unavailable_without_calls():
    class Broadcaster:
        subscriber_count = 1

    manager = TrafficManager(provider=TomTomTrafficProvider(api_key=""), broadcaster=Broadcaster())
    manager.candidates = [("cell-a", 90, 29.7, -95.4)]
    manager.watchlist = ["cell-a"]
    async def no_usage_read():
        return None
    manager.refresh_usage = no_usage_read
    body = asyncio.run(manager.poll_once())
    assert body["status"] == "unavailable"
    assert body["cells"] == []
    assert body["score_effect"] is False
    assert body["error"] == "Traffic provider key is not configured"


def test_traffic_routes_read_cache_and_enqueue_only():
    manager = TrafficManager(provider=TomTomTrafficProvider(api_key=""))
    manager.candidates = [("cell-a", 90, 29.7, -95.4)]
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(traffic_manager=manager)))

    snapshot = get_traffic(request)
    assert snapshot["status"] == "unavailable" and snapshot["cells"] == []
    assert snapshot["score_effect"] is False
    queued = asyncio.run(watch_cell(TrafficWatchRequest(cell_id="cell-a"), request))
    assert queued == {"queued": True, "cell_id": "cell-a", "score_effect": False}
    assert manager._queue == ["cell-a"]

    missing = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(traffic_manager=None)))
    with pytest.raises(Exception) as exc:
        get_traffic(missing)
    assert getattr(exc.value, "status_code", None) == 503


def test_background_poll_publishes_cell_only_event_on_bucket_changes(monkeypatch):
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    class Broadcaster:
        subscriber_count = 1

        def __init__(self):
            self.events = []

        def publish(self, event, data):
            self.events.append((event, data))
            return True

    class Provider:
        api_key = "test-only"
        speed = 30

        async def flow(self, _lat, _lng):
            return {"current_speed": self.speed, "free_flow_speed": 50, "confidence": 0.9,
                    "frc": "FRC3", "road_closure": False, "segment_key": "segment-a"}

    async def scenario():
        broadcaster = Broadcaster()
        provider = Provider()
        manager = TrafficManager(provider=provider, broadcaster=broadcaster, clock=lambda: now)
        manager.candidates = [("cell-a", 90, 29.7, -95.4)]
        manager.watchlist = ["cell-a"]

        async def reserve():
            return True

        async def usage():
            return None

        monkeypatch.setattr(manager, "_reserve", reserve)
        monkeypatch.setattr(manager, "refresh_usage", usage)
        await manager.poll_once()
        await manager.poll_once()  # no event when the bucket is unchanged
        assert len(broadcaster.events) == 1
        provider.speed = 45
        next_time = now.replace(minute=2)
        manager.clock = lambda: next_time
        await manager.poll_once()
        assert len(broadcaster.events) == 2
        name, payload = broadcaster.events[-1]
        assert name == "traffic.updated"
        assert set(payload) == {"cell_id", "bucket", "observed_at"}
        assert payload["cell_id"] == "cell-a" and payload["bucket"] == "free"
        assert set(manager.readings["cell-a"]) == {"bucket", "observed_at", "_observed_dt", "road_closure", "geometry"}

    asyncio.run(scenario())


def test_traffic_cache_exposes_stale_buckets_but_not_as_fresh_after_budget():
    now = datetime(2026, 11, 14, 12, 30, tzinfo=timezone.utc)
    manager = TrafficManager(clock=lambda: now)
    manager.readings["cell-a"] = {"bucket": "heavy", "observed_at": "2026-11-14T12:00:00Z",
                                   "_observed_dt": now.replace(minute=0), "road_closure": False}
    assert manager.snapshot()["status"] == "stale"
    assert manager.snapshot()["cells"][0]["status"] == "stale"
    manager._budget_reached = True
    snapshot = manager.snapshot()
    assert snapshot["status"] == "budget_reached"
    assert snapshot["cells"] == [{"cell_id": "cell-a", "bucket": "heavy",
                                  "observed_at": "2026-11-14T12:00:00Z", "road_closure": False,
                                  "geometry": [], "status": "stale"}]


def test_background_poll_waits_for_sse_client_and_circuit_breaker_recovers(monkeypatch):
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)

    class Broadcaster:
        subscriber_count = 0

        def publish(self, _event, _data):
            return True

    class Provider:
        api_key = "test-only"

        def __init__(self):
            self.calls = 0
            self.fail = True

        async def flow(self, _lat, _lng):
            self.calls += 1
            if self.fail:
                raise TimeoutError("simulated provider timeout")
            return {"current_speed": 15, "free_flow_speed": 50, "confidence": 0.9,
                    "frc": "FRC2", "road_closure": False, "segment_key": f"seg-{self.calls}"}

    async def scenario():
        broadcaster = Broadcaster()
        provider = Provider()
        manager = TrafficManager(provider=provider, broadcaster=broadcaster, clock=lambda: now)
        manager.candidates = [(f"cell-{i}", 90 - i, 29.7 + i / 100, -95.4) for i in range(8)]
        manager.watchlist = [row[0] for row in manager.candidates[:config.TRAFFIC_WATCH_SIZE]]

        async def reserve():
            return True

        async def usage():
            return None

        monkeypatch.setattr(manager, "_reserve", reserve)
        monkeypatch.setattr(manager, "refresh_usage", usage)
        await manager.poll_once()
        assert provider.calls == 0  # disconnected SSE means no provider polling

        broadcaster.subscriber_count = 1
        await manager.poll_once()
        assert provider.calls == config.TRAFFIC_CIRCUIT_FAILURES
        assert manager._circuit_until == now.replace(minute=now.minute + config.TRAFFIC_CIRCUIT_RESET_MINUTES)
        await manager.poll_once()
        assert provider.calls == config.TRAFFIC_CIRCUIT_FAILURES

        provider.fail = False
        next_time = now.replace(minute=now.minute + config.TRAFFIC_CIRCUIT_RESET_MINUTES)
        manager.clock = lambda: next_time
        await manager.poll_once()
        assert provider.calls == config.TRAFFIC_CIRCUIT_FAILURES + config.TRAFFIC_WATCH_SIZE
        assert manager._circuit_until is None and manager._failures == 0

    asyncio.run(scenario())


def test_request_budget_is_persisted_and_stops_at_caps(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'traffic-budget.db'}")
    TrafficRequestCounter.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("backend.traffic.SessionLocal", factory)
    monkeypatch.setattr(config, "TRAFFIC_DAILY_CAP", 1)
    monkeypatch.setattr(config, "TRAFFIC_MONTHLY_QUOTA", 1)
    monkeypatch.setattr(config, "TRAFFIC_BUDGET_FRACTION", 1.0)
    now = datetime(2026, 11, 14, tzinfo=timezone.utc)

    assert _reserve_request_sync(now) == (True, 1, 1)
    assert _reserve_request_sync(now) == (False, 1, 1)
    with factory() as db:
        rows = db.scalars(select(TrafficRequestCounter).order_by(TrafficRequestCounter.period_key)).all()
        assert [(row.period_key, row.request_count) for row in rows] == [
            ("day:20261114", 1), ("month:202611", 1),
        ]


def test_on_demand_watch_requests_are_throttled_by_cell():
    now = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)
    manager = TrafficManager(clock=lambda: now)
    manager.candidates = [("cell-a", 90, 29.7, -95.4)]
    assert manager.enqueue_cell("cell-a") is True
    assert manager.enqueue_cell("cell-a") is False
    later = now.replace(minute=now.minute + config.TRAFFIC_ON_DEMAND_COOLDOWN_MINUTES)
    manager.clock = lambda: later
    assert manager.enqueue_cell("cell-a") is True


def test_condition_history_writes_cell_only_rows_and_prunes_retention(monkeypatch, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'condition-history.db'}")
    ConditionSnapshot.__table__.create(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr("backend.traffic.SessionLocal", factory)

    class Runtime:
        model_version = "model-test"
        condition_lift_version = None

        def predict_batch(self, ids):
            return [{"cell_id": ids[0], "risk_score": 43.0, "live_risk_score": 43.0,
                     "active_incident_count": 0}]

    class Context:
        def snapshot(self, now):
            return {"status": "ok", "active_conditions": ["rain", "rush"]}

    class Watch:
        watchlist = ["cell-a"]

    now = datetime(2026, 11, 14, tzinfo=timezone.utc)
    writer = ConditionHistoryWriter(clock=lambda: now)
    assert writer.write_once(Runtime(), Context(), Watch()) == 1
    class WeatherUnavailable:
        def snapshot(self, now):
            return {"status": "unavailable", "active_conditions": ["rush"]}

    assert writer.write_once(Runtime(), WeatherUnavailable(), Watch()) == 1
    with factory() as db:
        rows = db.scalars(select(ConditionSnapshot).order_by(ConditionSnapshot.id)).all()
        row = rows[0]
        assert row.cell_id == "cell-a" and row.rain and row.rush and not row.night
        assert row.base_score == 43 and row.live_score == 43
        assert row.congestion_bucket is None
        assert not hasattr(row, "reporter") and not hasattr(row, "note") and not hasattr(row, "ip")
        assert rows[1].rain is None and rows[1].night is None and rows[1].rush is True
        db.add(ConditionSnapshot(cell_id="old-cell", at=datetime(2026, 1, 1), rain=False, night=False, rush=False,
                                 congestion_bucket=None, base_score=10, live_score=10, active_incident_count=0))
        db.commit()
        assert prune_condition_history(db, now) == 1


def test_legacy_sqlite_condition_history_is_upgraded_for_missing_weather(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-condition-history.db'}")
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            CREATE TABLE condition_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                cell_id VARCHAR NOT NULL,
                at DATETIME NOT NULL,
                rain BOOLEAN NOT NULL,
                night BOOLEAN NOT NULL,
                rush BOOLEAN NOT NULL,
                congestion_bucket VARCHAR NULL,
                base_score FLOAT NOT NULL,
                live_score FLOAT NOT NULL,
                active_incident_count INTEGER NOT NULL,
                model_version VARCHAR NULL,
                condition_lift_version VARCHAR NULL
            )
        """)
        connection.exec_driver_sql("""
            INSERT INTO condition_snapshots
                (cell_id, at, rain, night, rush, congestion_bucket, base_score,
                 live_score, active_incident_count, model_version, condition_lift_version)
            VALUES ('cell-a', '2026-11-14 01:00:00', 0, 1, 1, NULL, 20, 20, 0, 'model', NULL)
        """)

    ensure_condition_snapshot_weather_nullable(engine)
    columns = {column["name"]: column for column in inspect(engine).get_columns("condition_snapshots")}
    assert columns["rain"]["nullable"] is True
    assert columns["night"]["nullable"] is True
    with engine.begin() as connection:
        connection.exec_driver_sql("""
            INSERT INTO condition_snapshots
                (cell_id, at, rain, night, rush, congestion_bucket, base_score,
                 live_score, active_incident_count, model_version, condition_lift_version)
            VALUES ('cell-b', '2026-11-14 01:15:00', NULL, NULL, 1, NULL, 21, 21, 0, 'model', NULL)
        """)
        assert connection.exec_driver_sql("SELECT COUNT(*) FROM condition_snapshots").scalar_one() == 2
    engine.dispose()


def test_condition_history_export_contains_only_declared_cell_fields(tmp_path):
    row = SimpleNamespace(cell_id="cell-a", at="2026-11-14T12:00:00", rain=True, night=False, rush=True,
                          congestion_bucket=None, base_score=25.0, live_score=25.0, active_incident_count=0,
                          model_version="model", condition_lift_version=None)
    path = Path(tmp_path) / "history.csv"
    assert write_csv([row], path) == 1
    body = path.read_text()
    assert body.splitlines()[0] == "cell_id,at,rain,night,rush,congestion_bucket,base_score,live_score,active_incident_count,model_version,condition_lift_version"
    assert "reporter" not in body and "note" not in body and "ip" not in body


# ---------------------------------------------------------------- street geometry and flow tiles
from backend.traffic import TileBudgetExceeded, TrafficTileProxy, segment_geometry, tile_proxy_info  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 32


def test_segment_geometry_keeps_the_road_shape_and_drops_junk():
    coords = [{"latitude": 29.7 + i * 1e-4, "longitude": -95.4} for i in range(500)]
    coords.insert(3, {"latitude": "x", "longitude": 1})
    coords.insert(5, {"latitude": float("nan"), "longitude": -95.4})
    line = segment_geometry(coords)
    assert 2 <= len(line) <= 120
    assert line[0] == (29.7, -95.4) and line[-1] == (round(29.7 + 499e-4, 6), -95.4)
    assert segment_geometry([{"latitude": 29.7, "longitude": -95.4}]) == []      # one point is not a line
    assert segment_geometry(None) == []


def test_provider_returns_segment_geometry_for_the_map():
    def respond(request):
        return httpx.Response(200, json={"flowSegmentData": {
            "frc": "FRC2", "currentSpeed": 20, "freeFlowSpeed": 40, "confidence": 0.9, "roadClosure": False,
            "coordinates": {"coordinate": [{"latitude": 29.70, "longitude": -95.40},
                                           {"latitude": 29.71, "longitude": -95.41}]},
        }})
    reading = asyncio.run(TomTomTrafficProvider("k", httpx.MockTransport(respond)).flow(29.7, -95.4))
    assert reading["geometry"] == [(29.7, -95.4), (29.71, -95.41)] and reading["segment_key"]


def test_snapshot_exposes_geometry_and_tile_info():
    manager = TrafficManager(provider=TomTomTrafficProvider(api_key="k"), broadcaster=None)
    now = datetime.now(timezone.utc)
    manager.clock = lambda: now
    manager.readings["cell-a"] = {"bucket": "heavy", "observed_at": "x", "_observed_dt": now,
                                  "road_closure": False, "geometry": [(29.7, -95.4), (29.71, -95.41)]}
    snap = manager.snapshot()
    assert snap["status"] == "ok"
    assert snap["cells"][0]["geometry"] == [[29.7, -95.4], [29.71, -95.41]]
    assert snap["tiles"]["available"] is True and snap["tiles"]["url"].endswith("/{z}/{x}/{y}.png")
    assert tile_proxy_info(TomTomTrafficProvider(api_key=""))["available"] is False


def test_tile_proxy_caches_counts_and_hides_the_key():
    calls = []

    def respond(request):
        calls.append(request.url)
        return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})

    proxy = TrafficTileProxy(api_key="tile-secret", transport=httpx.MockTransport(respond))

    async def run():
        a = await proxy.tile(12, 960, 1700)
        b = await proxy.tile(12, 960, 1700)               # served from cache
        return a, b

    a, b = asyncio.run(run())
    assert a == b == PNG and len(calls) == 1
    assert "/flow/relative0/12/960/1700.png" in str(calls[0]) and calls[0].params["key"] == "tile-secret"
    assert proxy.valid(12, 960, 1700) and not proxy.valid(3, 0, 0) and not proxy.valid(12, 5000, 0)


def test_tile_proxy_stops_at_the_daily_cap(monkeypatch):
    monkeypatch.setattr(config, "TRAFFIC_TILE_DAILY_CAP", 1)
    proxy = TrafficTileProxy(api_key="k", transport=httpx.MockTransport(lambda r: httpx.Response(200, content=PNG)))

    async def run():
        await proxy.tile(12, 1, 1)
        with pytest.raises(TileBudgetExceeded):
            await proxy.tile(12, 1, 2)
        assert await proxy.tile(12, 1, 1) == PNG          # cached tiles are still served
    asyncio.run(run())


def test_tile_route_is_404_without_a_key(monkeypatch):
    from fastapi.testclient import TestClient
    from backend.main import app
    monkeypatch.delenv("WAYMARK_TRAFFIC_API_KEY", raising=False)
    app.state.traffic_tiles = TrafficTileProxy(api_key="")
    try:
        r = TestClient(app).get("/api/live/traffic/tiles/12/960/1700.png")
        assert r.status_code == 404
    finally:
        app.state.traffic_tiles = None
