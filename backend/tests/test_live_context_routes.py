"""Direct route-helper checks. HTTP/TestClient runs are recorded for local verification."""
from fastapi import FastAPI, HTTPException
from starlette.requests import Request

from backend.routes.live_context import get_live_context
from backend.routes.predict import _live_context


class Manager:
    def __init__(self, status):
        self.status = status
        self.calls = 0

    def snapshot(self):
        self.calls += 1
        return {"status": self.status, "active_conditions": ["rain"], "source": "Open-Meteo"}


def request(manager):
    app = FastAPI()
    app.state.live_context = manager
    return Request({"type": "http", "app": app, "headers": [], "query_string": b"",
                    "method": "GET", "path": "/api/live/context", "server": ("test", 80),
                    "client": ("test", 1234), "scheme": "http"})


def test_context_route_reads_snapshot_once_without_polling_provider():
    manager = Manager("ok")
    result = get_live_context(request(manager))
    assert result["active_conditions"] == ["rain"]
    assert manager.calls == 1


def test_live_prediction_context_rejects_stale_and_unavailable_status():
    assert _live_context(request(Manager("ok")))["status"] == "ok"
    for status in ("stale", "unavailable"):
        try:
            _live_context(request(Manager(status)))
        except HTTPException as exc:
            assert exc.status_code == 503
        else:
            raise AssertionError(f"expected status {status} to be rejected")
