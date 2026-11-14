"""Read-only endpoint for the latest cached city-wide live context."""
from fastapi import APIRouter, Request

from ..live_context import LiveContextManager
from ..schemas import LiveContext

router = APIRouter(prefix="/api", tags=["live context"])


@router.get("/live/context", response_model=LiveContext)
def get_live_context(request: Request):
    manager = getattr(request.app.state, "live_context", None)
    if manager is None:
        manager = LiveContextManager()
    return manager.snapshot()
