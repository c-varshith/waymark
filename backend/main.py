"""WAYMARK API. Run from the project root:  uvicorn backend.main:app --reload   (docs at /docs)"""
from __future__ import annotations

import logging
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from sqlalchemy import select
from starlette.concurrency import run_in_threadpool

from .config import settings
from .config_loader import get_effects
from .db import DB_PATH, IS_SQLITE, SessionLocal, configure_sqlite, engine, validate_postgres_schema
from .localities import ensure_locality_indexes
from .ml_runtime import ACTIVE_INCIDENT_STATUSES, InferenceRuntime
from .realtime import Broadcaster
from .realtime import utc_iso
from .realtime_config import SWEEP_SECONDS
from .live_context import LiveContextManager
from .models_ops import IncidentReport, create_ops_tables
from .storage import validate_storage_configuration
from .traffic import ConditionHistoryWriter, TrafficManager
from .routes import (audit, auth, cells, chat, crashes, evidence, incidents, live_context as live_context_routes,
                     measures, places, realtime, regions, reports, system,
                     predict, traffic as traffic_routes, validation, whatif)

log = logging.getLogger("waymark")


async def _sweep_incident_overlays(app: FastAPI, broadcaster: Broadcaster, now=None) -> int:
    """Publish only cells whose decay changed enough (or reached zero)."""
    runtime = getattr(app.state, "ml_runtime", None)
    if runtime is None:
        return 0
    cell_ids = await run_in_threadpool(runtime.sweep_incidents, now=now)
    published = 0
    for cell_id in cell_ids:
        try:
            prediction = await run_in_threadpool(runtime.predict, cell_id, None, False, now=now)
            payload = {
                "cell_id": cell_id,
                "risk_score": prediction["risk_score"],
                "live_risk_score": prediction["live_risk_score"],
                "active_incident_count": prediction["active_incident_count"],
                "incident_overlay_points": prediction["incident_overlay_points"],
                "scored_at": utc_iso(),
            }
            if broadcaster.publish("prediction.updated", payload):
                runtime.mark_overlay_published(cell_id, prediction["incident_overlay_points"])
                published += 1
        except Exception:
            log.warning("Could not sweep incident overlay for cell %s", cell_id, exc_info=True)
    return published


async def _incident_overlay_sweeper(app: FastAPI, broadcaster: Broadcaster):
    """Periodic, cancellable task with no per-cell event storm."""
    try:
        while True:
            await asyncio.sleep(SWEEP_SECONDS)
            await _sweep_incident_overlays(app, broadcaster)
    except asyncio.CancelledError:
        raise


@asynccontextmanager
async def lifespan(app: FastAPI):
    broadcaster = getattr(app.state, "broadcaster", None)
    if broadcaster is None:
        broadcaster = Broadcaster()
        app.state.broadcaster = broadcaster
    get_effects()                                  # refuse to start on a malformed effects.json
    validate_storage_configuration()
    database_ready = False
    if IS_SQLITE and DB_PATH is not None and DB_PATH.exists():
        configure_sqlite()                         # configure concurrency before serving requests
        ensure_locality_indexes(engine)             # make locality autocomplete prefix searches index-backed
        create_ops_tables(engine)                  # local SQLite compatibility only
        database_ready = True
    elif not IS_SQLITE:
        validate_postgres_schema()                 # Postgres schema is managed by versioned migrations
        database_ready = True
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    app.state.ml_runtime = None
    try:
        runtime = InferenceRuntime.load(settings.ml_artifact_dir, settings.ml_feature_store)
        if database_ready:
            with SessionLocal() as db:
                active_reports = db.scalars(select(IncidentReport).where(
                    IncidentReport.status.in_(ACTIVE_INCIDENT_STATUSES))).all()
                runtime.hydrate_incidents(active_reports)
        await run_in_threadpool(runtime.warm_score_cache)
        app.state.ml_runtime = runtime
        log.info("Live inference ready: %d cells, %d features", len(runtime.features), len(runtime.feature_names))
    except Exception:
        # Keep non-inference APIs available; prediction routes return 503 while ML is unavailable.
        log.exception("Live inference is unavailable; check model artifacts and feature store")
    await broadcaster.start()
    sweeper_task = asyncio.create_task(_incident_overlay_sweeper(app, broadcaster), name="waymark-incident-sweeper")
    live_context = LiveContextManager(broadcaster=broadcaster)
    app.state.live_context = live_context
    context_task = asyncio.create_task(live_context.run(), name="waymark-live-context-poller")
    traffic_manager = TrafficManager(broadcaster=broadcaster)
    app.state.traffic_manager = traffic_manager
    if app.state.ml_runtime is not None:
        await run_in_threadpool(traffic_manager.configure_watchlist, app.state.ml_runtime)
    try:
        await traffic_manager.refresh_usage()
    except Exception:
        log.exception("Traffic counters could not be read; disabling provider polling")
        traffic_manager._budget_reached = True
    traffic_task = asyncio.create_task(traffic_manager.run(), name="waymark-traffic-poller")
    history_writer = ConditionHistoryWriter()
    history_task = asyncio.create_task(
        history_writer.run(lambda: getattr(app.state, "ml_runtime", None),
                           lambda: getattr(app.state, "live_context", None),
                           lambda: traffic_manager),
        name="waymark-condition-history-writer")
    try:
        yield
    finally:
        for task in (history_task, traffic_task):
            task.cancel()
        for task in (history_task, traffic_task):
            try:
                await task
            except asyncio.CancelledError:
                pass
        context_task.cancel()
        try:
            await context_task
        except asyncio.CancelledError:
            pass
        sweeper_task.cancel()
        try:
            await sweeper_task
        except asyncio.CancelledError:
            pass
        await broadcaster.stop()


app = FastAPI(title="WAYMARK API", version="2.0.0", lifespan=lifespan,
              description="Explainable road risk, cell by cell, plus the action loop: region reports, an assistant, "
                          "tracked measures with photo evidence and a verified-only adjusted risk estimate. "
                          "Decision support only; human experts decide.")
app.state.ml_runtime = None
app.state.broadcaster = Broadcaster()
app.state.live_context = None
app.state.traffic_manager = None

app.add_middleware(GZipMiddleware, minimum_size=1000)   # the full cell list is ~1.6 MB of JSON
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins,
                   allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
                   allow_headers=["Authorization", "Content-Type", "Last-Event-ID"])

for r in (system.router, cells.router, crashes.router, incidents.router, predict.router, places.router, whatif.router, validation.router, audit.router,
          auth.router, regions.router, reports.router, measures.router, evidence.router, chat.router, realtime.router,
          live_context_routes.router, traffic_routes.router):
    app.include_router(r)


@app.exception_handler(Exception)
async def unexpected(_: Request, exc: Exception):
    # Never leak internals; the frontend shows the message in an error banner.
    log.exception("unhandled error")
    return JSONResponse(status_code=500, content={"detail": "Unexpected server error. Please try again."})


class SPAStaticFiles(StaticFiles):
    """Serve the built frontend; unknown paths (such as /region/abc on a page reload) get index.html."""

    async def get_response(self, path: str, scope):
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as e:
            if e.status_code == 404 and not path.startswith("api/"):
                return await super().get_response("index.html", scope)
            raise


# Optional: serve the built frontend (frontend/dist) from the same server for one-command deployment.
DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"
if DIST.exists():
    app.mount("/", SPAStaticFiles(directory=DIST, html=True), name="frontend")
