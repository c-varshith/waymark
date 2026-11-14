"""Cache-only traffic display endpoints. Provider calls occur only in TrafficManager.run."""
from fastapi import APIRouter, HTTPException, Request

from ..schemas import LiveTraffic, TrafficWatchRequest

router = APIRouter(prefix="/api/live/traffic", tags=["live traffic"])


@router.get("", response_model=LiveTraffic)
def get_traffic(request: Request):
    manager = getattr(request.app.state, "traffic_manager", None)
    if manager is None:
        raise HTTPException(503, "Traffic display is unavailable.")
    return manager.snapshot()


@router.post("/watch")
async def watch_cell(body: TrafficWatchRequest, request: Request):
    manager = getattr(request.app.state, "traffic_manager", None)
    if manager is None:
        raise HTTPException(503, "Traffic display is unavailable.")
    return {"queued": manager.enqueue_cell(body.cell_id), "cell_id": body.cell_id,
            "score_effect": False}
