"""Cache-only traffic display endpoints. Segment polling happens only in TrafficManager.run;
street tiles are relayed (and cached) by TrafficTileProxy so the provider key never reaches the browser."""
import httpx
from fastapi import APIRouter, HTTPException, Request, Response

from ..schemas import LiveTraffic, TrafficWatchRequest
from ..traffic import TileBudgetExceeded, TrafficTileProxy

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


def _proxy(request: Request) -> TrafficTileProxy:
    proxy = getattr(request.app.state, "traffic_tiles", None)
    if proxy is None:
        proxy = TrafficTileProxy()
        request.app.state.traffic_tiles = proxy
    return proxy


@router.get("/tiles/{z}/{x}/{y}.png", responses={200: {"content": {"image/png": {}}}})
async def traffic_tile(z: int, x: int, y: int, request: Request):
    """Street-level congestion colours for one map tile (display only; never used for scoring)."""
    proxy = _proxy(request)
    if not proxy.enabled:
        raise HTTPException(404, "Street traffic tiles are not configured on this server.")
    if not proxy.valid(z, x, y):
        raise HTTPException(404, "Tile outside the supported zoom range.")
    try:
        body = await proxy.tile(z, x, y)
    except TileBudgetExceeded:
        raise HTTPException(429, "Daily traffic tile budget reached.") from None
    except (httpx.HTTPError, ValueError):
        raise HTTPException(502, "Traffic tile provider unavailable.") from None
    return Response(body, media_type="image/png",
                    headers={"Cache-Control": f"public, max-age={proxy.ttl}"})
