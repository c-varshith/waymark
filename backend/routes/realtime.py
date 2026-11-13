"""Public, cell-only event stream and current live incident snapshot."""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

from ..ml_runtime import InferenceRuntime, MLRuntimeError
from ..realtime import Broadcaster, SubscriberLimitError, utc_iso

router = APIRouter(prefix="/api", tags=["realtime"])


def _broadcaster(request: Request) -> Broadcaster:
    broadcaster = getattr(request.app.state, "broadcaster", None)
    if broadcaster is None:
        raise HTTPException(503, "Live updates are unavailable.")
    return broadcaster


def _runtime(request: Request) -> InferenceRuntime:
    runtime = getattr(request.app.state, "ml_runtime", None)
    if runtime is None:
        raise HTTPException(503, "Live inference is unavailable. Check the model artifacts and feature store.")
    return runtime


def _frame(event_id: int, name: str, data: dict) -> bytes:
    body = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    return f"id: {event_id}\nevent: {name}\ndata: {body}\n\n".encode("utf-8")


def _parse_last_event_id(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return -1


@router.get("/stream")
async def stream(request: Request, last_event_id: str | None = Header(default=None, alias="Last-Event-ID")):
    broadcaster = _broadcaster(request)
    await broadcaster.start()
    try:
        subscriber = await broadcaster.subscribe(_parse_last_event_id(last_event_id))
    except SubscriberLimitError as exc:
        raise HTTPException(503, str(exc)) from None

    async def events():
        try:
            while True:
                try:
                    event = await asyncio.wait_for(subscriber.queue.get(), timeout=5.0)
                except TimeoutError:
                    if await request.is_disconnected():
                        return
                    continue
                yield _frame(event.id, event.name, event.data)
                if await request.is_disconnected():
                    return
        finally:
            broadcaster.unsubscribe(subscriber)

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
        "Connection": "keep-alive",
    })


@router.get("/live/snapshot")
async def live_snapshot(request: Request):
    broadcaster = _broadcaster(request)
    await broadcaster.start()
    # Read the version before overlay state: a concurrent change is then either in
    # this snapshot or replayed after this version, possibly both (idempotently).
    version = broadcaster.version
    runtime = _runtime(request)
    cell_ids = runtime.active_incident_cell_ids()
    try:
        # Snapshot hydration normally hits the warmed baseline score cache. Keep
        # this bounded work on the request loop instead of handing each reconnect
        # to a new worker; the model batch path itself is vectorized.
        predictions = runtime.predict_batch(cell_ids) if cell_ids else []
    except (MLRuntimeError, KeyError) as exc:
        raise HTTPException(503, "The live incident snapshot could not be scored.") from exc
    overlays = [{
        "cell_id": item["cell_id"],
        "risk_score": item["risk_score"],
        "live_risk_score": item["live_risk_score"],
        "active_incident_count": item["active_incident_count"],
        "incident_overlay_points": item["incident_overlay_points"],
    } for item in predictions if item["active_incident_count"] > 0]
    return {
        "version": version,
        "scored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "model_version": runtime.model_version,
        "context_source": "live",
        "overlays": overlays,
    }
