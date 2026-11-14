"""Role-scoped crash and near-miss reports, kept separate from historical model inputs."""
from __future__ import annotations

from datetime import timezone
import logging
from typing import Optional

import h3
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from ..auth import Role, User
from ..db import get_db
from ..models import Cell
from ..models_ops import IncidentReport, utcnow
from ..ml_runtime import InferenceRuntime, MLRuntimeError
from ..realtime import utc_iso
from ..permissions import require_permission
from ..schemas import (IncidentReportIn, IncidentReportList, IncidentReportOut,
                       IncidentReviewIn, LiveIncidentResponse)
from ..ops_common import iso_req
from .cells import parse_bbox

router = APIRouter(prefix="/api", tags=["incidents"])
log = logging.getLogger("waymark.incidents")


def _out(row: IncidentReport) -> IncidentReportOut:
    return IncidentReportOut(
        id=row.id, kind=row.kind, cell_id=row.cell_id, lat=row.lat, lng=row.lng,
        occurred_at=iso_req(row.occurred_at), severity=row.severity, reasons=row.reasons, note=row.note,
        status=row.status, review_note=row.review_note,
        created_at=iso_req(row.created_at))


def _runtime(request: Request) -> InferenceRuntime | None:
    return getattr(request.app.state, "ml_runtime", None)


def _publish_best_effort(request: Request, runtime: InferenceRuntime, event: str, payload: dict,
                         cell_id: str) -> None:
    """Publish public, cell-level updates without making the report depend on SSE."""
    broadcaster = getattr(request.app.state, "broadcaster", None)
    if broadcaster is None:
        return
    traffic_manager = getattr(request.app.state, "traffic_manager", None)
    if traffic_manager is not None:
        try:
            # Incident cells enter the bounded traffic watch list immediately; review can
            # also remove a rejected report from the active-priority slots.
            traffic_manager.configure_watchlist(runtime)
            traffic_manager.notify_clients_changed()
        except Exception:
            log.warning("Could not refresh traffic watch list for cell %s", cell_id, exc_info=True)
    try:
        if event == "incident.created":
            broadcaster.publish(event, payload)
        elif event == "incident.reviewed":
            broadcaster.publish(event, payload)
    except Exception:
        log.warning("Could not publish %s for cell %s", event, cell_id, exc_info=True)
    try:
        prediction = runtime.predict(cell_id, include_shap=False)
        update = {
            "cell_id": cell_id,
            "risk_score": prediction["risk_score"],
            "live_risk_score": prediction["live_risk_score"],
            "active_incident_count": prediction["active_incident_count"],
            "incident_overlay_points": prediction["incident_overlay_points"],
            "scored_at": prediction.get("scored_at") or utc_iso(),
        }
    except Exception:
        log.warning("Could not score live incident update for cell %s", cell_id, exc_info=True)
        return
    try:
        if broadcaster.publish("prediction.updated", update):
            runtime.mark_overlay_published(cell_id, float(update["incident_overlay_points"]))
    except Exception:
        log.warning("Could not publish prediction update for cell %s", cell_id, exc_info=True)


def _cell_id_for_report(db: Session, body: IncidentReportIn) -> str:
    sample = db.scalar(select(Cell.cell_id).limit(1))
    if sample is None:
        raise HTTPException(503, "Scored cells are not loaded.")
    cell_id = h3.latlng_to_cell(body.lat, body.lng, h3.get_resolution(sample))
    if db.get(Cell, cell_id) is None:
        raise HTTPException(422, "The selected point is outside the scored-cell coverage.")
    return cell_id


def _save_report(db: Session, body: IncidentReportIn, cell_id: str, user: User) -> IncidentReport:
    occurred = body.occurred_at
    if occurred.tzinfo:
        occurred = occurred.astimezone(timezone.utc).replace(tzinfo=None)
    row = IncidentReport(kind=body.kind, cell_id=cell_id, lat=body.lat, lng=body.lng, occurred_at=occurred,
                         severity=body.severity if body.kind == "crash" else None,
                         reasons=[reason.strip() for reason in (body.reasons or []) if reason.strip()] or None,
                         note=(body.note or "").strip() or None, status="pending", reported_by=user.id,
                         created_at=utcnow(), updated_at=utcnow())
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.post("/incidents", response_model=IncidentReportOut, status_code=201)
def create_incident(body: IncidentReportIn, request: Request, db: Session = Depends(get_db),
                    user: User = Depends(require_permission("incident.create"))):
    cell_id = _cell_id_for_report(db, body)
    row = _save_report(db, body, cell_id, user)
    runtime = _runtime(request) if request is not None else None
    if runtime is not None:
        runtime.activate_incident(row.id, cell_id, row.kind, status=row.status, occurred_at=row.occurred_at)
        _publish_best_effort(request, runtime, "incident.created", {
            "cell_id": cell_id, "kind": row.kind, "at": iso_req(row.created_at),
        }, cell_id)
    return _out(row)


def _score_and_save_incident(db: Session, body: IncidentReportIn, user: User,
                             runtime: InferenceRuntime):
    """Keep the request-scoped SQLAlchemy session on one worker while doing its DB work."""
    cell_id = _cell_id_for_report(db, body)
    try:
        prediction = runtime.predict(cell_id)
    except KeyError as exc:
        raise _IncidentCellMissing from exc
    except MLRuntimeError as exc:
        raise _IncidentScoringFailed("model") from exc
    except Exception as exc:
        raise _IncidentScoringFailed("unexpected") from exc
    row = _save_report(db, body, cell_id, user)
    return cell_id, prediction, row


class _IncidentCellMissing(Exception):
    pass


class _IncidentScoringFailed(Exception):
    def __init__(self, kind: str):
        self.kind = kind


@router.post("/incident", response_model=LiveIncidentResponse, status_code=201)
async def create_live_incident(body: IncidentReportIn, request: Request, db: Session = Depends(get_db),
                               user: User = Depends(require_permission("incident.create"))):
    runtime = _runtime(request)
    if runtime is None:
        raise HTTPException(503, "Live inference is unavailable. The incident was not submitted.")
    try:
        cell_id, prediction, row = await run_in_threadpool(
            _score_and_save_incident, db, body, user, runtime)
    except _IncidentCellMissing:
        raise HTTPException(422, "The selected cell has no model feature row; the incident was not submitted.") from None
    except _IncidentScoringFailed as exc:
        if exc.kind == "model":
            log.exception("Incident live scoring failed")
        else:
            log.exception("Unexpected incident live scoring failure")
        raise HTTPException(503, "Live inference failed. The incident was not submitted.") from None
    runtime.activate_incident(row.id, cell_id, row.kind, status=row.status, occurred_at=row.occurred_at)
    _publish_best_effort(request, runtime, "incident.created", {
        "cell_id": cell_id, "kind": row.kind, "at": iso_req(row.created_at),
    }, cell_id)
    prediction = runtime.decorate_prediction(prediction)
    return LiveIncidentResponse(incident=_out(row), prediction=prediction)


@router.get("/incidents", response_model=IncidentReportList)
def list_incidents(bbox: Optional[str] = Query(None), cell_id: Optional[str] = Query(None),
                   status: Optional[str] = Query(None, pattern="^(pending|confirmed|rejected)$"),
                   limit: int = Query(3000, ge=1, le=10000), db: Session = Depends(get_db),
                   user: User = Depends(require_permission("incident.read"))):
    if not bbox and not cell_id:
        raise HTTPException(422, "Provide a map bbox or cell id.")
    where = []
    if bbox:
        south, west, north, east = parse_bbox(bbox)
        where += [IncidentReport.lat >= south, IncidentReport.lat <= north,
                  IncidentReport.lng >= west, IncidentReport.lng <= east]
    if cell_id:
        where.append(IncidentReport.cell_id == cell_id)
    if user.role is Role.engineer:
        where.append(IncidentReport.status == "confirmed")
    elif user.role is Role.community:
        where.append((IncidentReport.status == "confirmed") |
                     ((IncidentReport.reported_by == user.id) & (IncidentReport.status != "confirmed")))
    if status:
        where.append(IncidentReport.status == status)
    rows = db.scalars(select(IncidentReport).where(*where)
                      .order_by(IncidentReport.created_at.desc(), IncidentReport.id.desc()).limit(limit)).all()
    return IncidentReportList(count=len(rows), items=[_out(r) for r in rows])


@router.post("/incidents/{incident_id}/review", response_model=IncidentReportOut)
def review_incident(incident_id: int, body: IncidentReviewIn, request: Request,
                    db: Session = Depends(get_db),
                    user: User = Depends(require_permission("incident.review"))):
    row = db.get(IncidentReport, incident_id)
    if row is None:
        raise HTTPException(404, "Incident report not found.")
    if row.status != "pending":
        raise HTTPException(409, "Only pending incident reports can be reviewed.")
    if row.reported_by == user.id:
        raise HTTPException(403, "You cannot review your own incident report.")
    if body.decision == "reject" and not (body.note or "").strip():
        raise HTTPException(422, "Add a reason when rejecting a report.")
    runtime = _runtime(request) if request is not None else None
    score_before = None
    if runtime is not None:
        try:
            score_before = runtime.predict(row.cell_id, include_shap=False)["live_risk_score"]
        except Exception:
            log.warning("Could not score incident cell before review", exc_info=True)
    row.status = "confirmed" if body.decision == "confirm" else "rejected"
    row.reviewed_by = user.id
    row.review_note = (body.note or "").strip() or None
    row.updated_at = utcnow()
    db.commit()
    db.refresh(row)
    score_after = None
    if runtime is not None:
        runtime.update_incident_status(row.id, row.status)
        try:
            score_after = runtime.predict(row.cell_id, include_shap=False)["live_risk_score"]
        except Exception:
            log.warning("Could not score incident cell after review", exc_info=True)
        _publish_best_effort(request, runtime, "incident.reviewed", {
            "cell_id": row.cell_id, "decision": body.decision, "at": iso_req(row.updated_at),
        }, row.cell_id)
    output = _out(row)
    output.live_score_before = score_before
    output.live_score_after = score_after
    return output

