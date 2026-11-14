import asyncio
import threading
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from starlette.middleware.gzip import DEFAULT_EXCLUDED_CONTENT_TYPES, GZipMiddleware

from backend.realtime import Broadcaster, EVENT_KEYS
from backend.routes import realtime
from backend.routes.incidents import _publish_best_effort


def _created(cell="cell-a"):
    return {"cell_id": cell, "kind": "crash", "at": "2026-11-14T00:00:00Z"}


def test_broadcaster_fanout_thread_publish_replay_overflow_and_cleanup():
    async def scenario():
        hub = Broadcaster(heartbeat_seconds=3600, max_subscribers=3, replay_buffer=2, queue_size=2)
        await hub.start()
        first = await hub.subscribe()
        second = await hub.subscribe()
        hub.publish("incident.created", _created())
        event_one, event_two = await asyncio.gather(first.queue.get(), second.queue.get())
        assert event_one == event_two
        assert event_one.name == "incident.created"
        assert set(event_one.data) == EVENT_KEYS[event_one.name]

        worker = threading.Thread(target=hub.publish, args=("incident.created", _created("cell-b")))
        worker.start()
        worker.join(timeout=1)
        assert not worker.is_alive()
        event = await asyncio.wait_for(first.queue.get(), 1)
        assert event.data["cell_id"] == "cell-b"

        replay = await hub.subscribe(last_event_id=event_one.id)
        assert (await replay.queue.get()).id == event.id
        hub.unsubscribe(replay)
        assert hub.subscriber_count == 2

        hub.publish("incident.created", _created("cell-c"))
        hub.publish("incident.created", _created("cell-d"))
        await asyncio.sleep(0)
        overflow_event = await first.queue.get()
        assert overflow_event.name == "resync"
        assert first.queue.empty()

        too_old = await hub.subscribe(last_event_id=0)
        assert (await too_old.queue.get()).name == "resync"
        hub.unsubscribe(first)
        assert hub.subscriber_count == 2
        await hub.stop()

    asyncio.run(scenario())


def test_subscriber_limit_is_enforced():
    async def scenario():
        hub = Broadcaster(heartbeat_seconds=3600, max_subscribers=1)
        await hub.start()
        await hub.subscribe()
        with pytest.raises(Exception, match="capacity"):
            await hub.subscribe()
        await hub.stop()
    asyncio.run(scenario())


def test_live_snapshot_is_public_and_contains_only_active_cell_scores():
    class Runtime:
        model_version = "test-model"
        active = ["cell-a"]

        def active_incident_cell_ids(self):
            return self.active

        def predict_batch(self, cell_ids):
            return [{"cell_id": cid, "risk_score": 50.0, "live_risk_score": 70.0,
                     "active_incident_count": 1, "incident_overlay_points": 20.0} for cid in cell_ids]

    async def scenario():
        app = FastAPI()
        app.include_router(realtime.router)
        wrapped_app = GZipMiddleware(app, minimum_size=0)
        app.state.ml_runtime = Runtime()
        app.state.broadcaster = Broadcaster(heartbeat_seconds=3600)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://waymark.test") as client:
            response = await client.get("/api/live/snapshot")
            app.state.ml_runtime.active = []  # equivalent to rejecting the last active report
            after_rejection = await client.get("/api/live/snapshot")
        assert response.status_code == 200
        body = response.json()
        assert body["overlays"] == [{"cell_id": "cell-a", "risk_score": 50.0,
            "live_risk_score": 70.0, "active_incident_count": 1, "incident_overlay_points": 20.0}]
        assert set(body) == {"version", "scored_at", "model_version", "context_source", "overlays"}
        assert after_rejection.status_code == 200 and after_rejection.json()["overlays"] == []
        assert app.state.broadcaster.subscriber_count == 0
        await app.state.broadcaster.stop()
    asyncio.run(scenario())


def test_snapshot_requires_live_model():
    async def scenario():
        app = FastAPI()
        app.include_router(realtime.router)
        app.state.ml_runtime = None
        app.state.broadcaster = Broadcaster(heartbeat_seconds=3600)
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://waymark.test") as client:
            response = await client.get("/api/live/snapshot")
        assert response.status_code == 503
        await app.state.broadcaster.stop()
    asyncio.run(scenario())


def test_sse_stream_emits_configurable_heartbeat_then_cleans_up():
    async def scenario():
        app = FastAPI()
        app.include_router(realtime.router)
        wrapped_app = GZipMiddleware(app, minimum_size=0)
        hub = Broadcaster(heartbeat_seconds=0.05)
        app.state.broadcaster = hub
        received = 0

        async def receive():
            nonlocal received
            received += 1
            if received == 1:
                return {"type": "http.request", "body": b"", "more_body": False}
            await asyncio.sleep(0.12)
            return {"type": "http.disconnect"}

        sent = []

        async def send(message):
            sent.append(message)

        scope = {"type": "http", "method": "GET", "path": "/api/stream", "raw_path": b"/api/stream",
                 "query_string": b"", "headers": [(b"accept-encoding", b"gzip")], "server": ("test", 80), "client": ("test", 1),
                 "scheme": "http", "http_version": "1.1", "root_path": "", "app": app,
                 "asgi": {"version": "3.0", "spec_version": "2.3"}}
        await asyncio.wait_for(wrapped_app(scope, receive, send), 1)
        body = b"".join(message.get("body", b"") for message in sent)
        headers = dict(sent[0]["headers"])
        assert headers[b"content-type"].startswith(b"text/event-stream")
        assert headers[b"cache-control"] == b"no-cache"
        assert headers[b"x-accel-buffering"] == b"no"
        assert b"content-encoding" not in headers
        assert b"event: heartbeat" in body and b'"at"' in body
        assert hub.subscriber_count == 0
        await hub.stop()
    asyncio.run(scenario())


def test_event_payload_contract_excludes_report_details():
    assert EVENT_KEYS["incident.created"] == {"cell_id", "kind", "at"}
    assert EVENT_KEYS["incident.reviewed"] == {"cell_id", "decision", "at"}
    assert not ({"note", "reasons", "reporter", "reviewer", "review_note", "lat", "lng"} & EVENT_KEYS["incident.created"])
    assert all(set(data) == EVENT_KEYS[name] for name, data in (("incident.created", _created()),))


def test_starlette_gzip_excludes_event_stream_content_type():
    assert "text/event-stream" in DEFAULT_EXCLUDED_CONTENT_TYPES


def test_incident_publish_is_best_effort_and_payload_is_cell_only():
    recorded = []

    class Hub:
        def publish(self, name, data):
            recorded.append((name, data))
            return True

    class Runtime:
        def predict(self, cell_id, include_shap):
            assert include_shap is False
            return {"risk_score": 40.0, "live_risk_score": 60.0, "active_incident_count": 1,
                    "incident_overlay_points": 20.0, "scored_at": "2026-11-14T00:00:00Z"}

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(broadcaster=Hub())))
    _publish_best_effort(request, Runtime(), "incident.created", _created(), "cell-a")
    assert [name for name, _ in recorded] == ["incident.created", "prediction.updated"]
    assert all(set(data) == EVENT_KEYS[name] for name, data in recorded)
    assert all(not ({"note", "reasons", "reporter", "lat", "lng"} & data.keys()) for _, data in recorded)

    class FailedHub:
        def publish(self, _name, _data):
            raise RuntimeError("stream unavailable")

    request.app.state.broadcaster = FailedHub()
    _publish_best_effort(request, Runtime(), "incident.created", _created(), "cell-a")
