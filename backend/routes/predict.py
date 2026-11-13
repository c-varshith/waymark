"""Live single-cell and batch inference endpoints."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from time import perf_counter

from fastapi import APIRouter, HTTPException, Request, Response
from starlette.concurrency import run_in_threadpool

from ..ml_runtime import InferenceRuntime, MLRuntimeError
from ..schemas import (BatchPredictRequest, BatchPredictionResponse, PredictRequest,
                       PredictionResponse)
from ..realtime_config import STALE_AFTER_SECONDS

log = logging.getLogger("waymark.ml")
router = APIRouter(prefix="/api", tags=["inference"])


def _metadata(runtime: InferenceRuntime, overrides) -> dict[str, object]:
    values = overrides.model_dump() if overrides is not None else {}
    return {
        "model_version": runtime.model_version,
        "scored_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "context_source": "scenario" if any(value is not None for value in values.values()) else "baseline",
        "stale_after_seconds": STALE_AFTER_SECONDS,
        "overlay_params": runtime.overlay_params(),
    }


def _runtime(request: Request) -> InferenceRuntime:
    runtime = getattr(request.app.state, "ml_runtime", None)
    if runtime is None:
        raise HTTPException(503, "Live inference is unavailable. Check the model artifacts and feature store.")
    return runtime


@router.post("/predict", response_model=PredictionResponse)
async def predict(body: PredictRequest, request: Request, response: Response = None):
    has_coordinates = body.lat is not None and body.lng is not None
    if (body.lat is None) != (body.lng is None) or (body.cell_id is None) == (not has_coordinates):
        raise HTTPException(422, "Provide either cell_id or both lat and lng.")
    runtime = _runtime(request)
    try:
        cell_id = runtime.resolve_cell(body.cell_id, body.lat, body.lng)
        started = perf_counter()
        result = await run_in_threadpool(runtime.predict, cell_id, body.overrides)
        elapsed_ms = (perf_counter() - started) * 1000
        if response is not None:
            response.headers["Server-Timing"] = f"infer;dur={elapsed_ms:.3f}"
        result.update(_metadata(runtime, body.overrides))
        return result
    except KeyError:
        raise HTTPException(404, "No baseline features are available for this H3 cell.") from None
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    except MLRuntimeError as exc:
        log.exception("Live prediction failed: %s", exc)
        raise HTTPException(503, "Live inference failed. Try again after the model runtime is checked.") from None


@router.post("/predict/batch", response_model=BatchPredictionResponse)
async def predict_batch(body: BatchPredictRequest, request: Request, response: Response = None):
    runtime = _runtime(request)
    try:
        started = perf_counter()
        items = await run_in_threadpool(runtime.predict_batch, body.cell_ids, body.overrides)
        elapsed_ms = (perf_counter() - started) * 1000
        if response is not None:
            response.headers["Server-Timing"] = f"infer;dur={elapsed_ms:.3f}"
    except KeyError:
        raise HTTPException(404, "At least one H3 cell has no baseline feature row.") from None
    except MLRuntimeError as exc:
        log.exception("Batch prediction failed: %s", exc)
        raise HTTPException(503, "Live inference failed. Try again after the model runtime is checked.") from None
    metadata = _metadata(runtime, body.overrides)
    return BatchPredictionResponse(count=len(items), items=items,
                                   model_version=metadata["model_version"],
                                   schema_version=runtime.schema_version,
                                   scored_at=metadata["scored_at"],
                                   context_source=metadata["context_source"],
                                   stale_after_seconds=metadata["stale_after_seconds"],
                                   overlay_params=metadata["overlay_params"])
