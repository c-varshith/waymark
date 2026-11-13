import asyncio
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import pytest
import h3
from fastapi import FastAPI, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.requests import Request

from backend.auth import Role, User
from backend.ml_runtime import InferenceRuntime
from backend import realtime_config
from backend.models import Base, Cell
from backend.routes.incidents import create_live_incident, review_incident
from backend.routes.predict import predict, predict_batch
from backend.routes.system import _model_health
from backend.schemas import (BatchPredictRequest, IncidentReportIn, IncidentReviewIn,
                             PredictRequest, PredictionOverrides)


FEATURES = [
    "n_acc", "n_recent", "mean_sev", "share_night", "share_rain", "mean_vis",
    "Junction", "Crossing", "Traffic_Signal", "Stop", "Give_Way", "Railway",
    "Roundabout", "Bump", "Traffic_Calming", "Station", "Amenity", "nb_acc",
]


class FakeModel:
    def predict_proba(self, frame):
        p = (0.05 + frame["share_rain"].to_numpy() * 0.15
             + frame["share_night"].to_numpy() * 0.10
             + (frame["mean_vis"].to_numpy() < 5) * 0.05)
        p = np.clip(p, 0, 1)
        return np.column_stack((1 - p, p))


class FakeExplainer:
    def shap_values(self, frame):
        return np.tile(np.arange(1, len(FEATURES) + 1, dtype=float), (len(frame), 1))


def runtime_fixture():
    cell_ids = [h3.latlng_to_cell(29.7, -95.4, 9), h3.latlng_to_cell(29.8, -95.3, 9)]
    rows = np.zeros((2, len(FEATURES)), dtype=float)
    rows[:, FEATURES.index("n_acc")] = [2, 6]
    rows[:, FEATURES.index("mean_sev")] = 2
    rows[:, FEATURES.index("mean_vis")] = 10
    frame = pd.DataFrame(rows, index=pd.Index(cell_ids, name="cell"), columns=FEATURES)
    bundle = {"models": [FakeModel(), FakeModel(), FakeModel()], "features": FEATURES,
              "probability_reference": np.linspace(0, 1, 101)}
    schema = {"feature_columns": FEATURES, "h3_resolution": 9}
    return InferenceRuntime(bundle, schema, FakeExplainer(), frame, disagreement_threshold=0.5), cell_ids


def request_for(runtime):
    app = FastAPI()
    app.state.ml_runtime = runtime
    return Request({"type": "http", "app": app, "headers": [], "query_string": b"",
                    "method": "POST", "path": "/api/predict", "server": ("test", 80),
                    "client": ("test", 1234), "scheme": "http"})


def test_predict_route_uses_server_features_overrides_and_shap():
    runtime, cell_ids = runtime_fixture()
    before = runtime.features.loc[cell_ids[0], "share_rain"]
    result = asyncio.run(predict(PredictRequest(cell_id=cell_ids[0], overrides=PredictionOverrides(
        weather_condition="rain", lighting="night", low_visibility=True)), request_for(runtime)))

    assert result["cell_id"] == cell_ids[0]
    assert result["risk_probability"] == pytest.approx(0.35)
    assert 0 <= result["risk_score"] <= 100
    assert result["confidence"] == "Low"
    assert len(result["shap_contributors"]) == 6
    assert result["shap_contributors"][0]["feature"] == "nb_acc"
    assert runtime.features.loc[cell_ids[0], "share_rain"] == before


def test_predict_accepts_coordinates_and_rejects_missing_or_unknown_target():
    runtime, cell_ids = runtime_fixture()
    lat, lng = h3.cell_to_latlng(cell_ids[0])
    result = asyncio.run(predict(PredictRequest(lat=lat, lng=lng), request_for(runtime)))
    assert result["cell_id"] == cell_ids[0]

    with pytest.raises(HTTPException) as missing:
        asyncio.run(predict(PredictRequest(), request_for(runtime)))
    assert missing.value.status_code == 422

    other = h3.latlng_to_cell(30.5, -96.5, 9)
    with pytest.raises(HTTPException) as unknown:
        asyncio.run(predict(PredictRequest(cell_id=other), request_for(runtime)))
    assert unknown.value.status_code == 404


def test_batch_prediction_returns_scores_without_shap_and_incident_overlay_is_separate():
    runtime, cell_ids = runtime_fixture()
    result = asyncio.run(predict_batch(BatchPredictRequest(cell_ids=cell_ids,
        overrides=PredictionOverrides(weather_condition="rain")), request_for(runtime)))
    assert result.count == 2
    assert all(not item.shap_contributors for item in result.items)
    assert all(item.risk_probability > 0.05 for item in result.items)

    base = runtime.predict(cell_ids[0])
    runtime.activate_incident(1, cell_ids[0], "crash")
    runtime.activate_incident(2, cell_ids[0], "near_miss")
    live = runtime.predict(cell_ids[0])
    assert live["risk_probability"] == base["risk_probability"]
    assert live["risk_score"] == base["risk_score"]
    assert live["live_risk_score"] == pytest.approx(min(100, base["risk_score"] + 18))
    runtime.reject_incident(1)
    assert runtime.predict(cell_ids[0])["incident_overlay_points"] == pytest.approx(6)


def test_condition_context_is_not_applied_to_scores_by_default(monkeypatch):
    runtime, cell_ids = runtime_fixture()
    monkeypatch.setattr(realtime_config, "CONDITION_LIFT_APPLIES_TO_RANKING", False)
    citywide = {name: {"p": 0.1, "h": 0.1, "L": 1.0} for name in ("rain", "night", "rush")}
    condition_lift = {
        "schema_version": 1,
        "citywide": citywide,
        "cells": {cell_ids[0]: {"n": 10, "conditions": {
            "rain": {"k": 8, "prone": True, "multiplier": 1.5},
            "night": {"k": 0, "prone": False, "multiplier": 1.0},
            "rush": {"k": 0, "prone": False, "multiplier": 1.0},
        }}},
    }
    runtime._load_condition_lift(condition_lift, "lift-test")
    health = _model_health(request_for(runtime))
    assert health["condition_lift_enabled"] is True
    assert health["condition_lift_applied"] is False

    baseline = runtime.predict(cell_ids[0])
    context = runtime.predict(cell_ids[0], active_conditions=("rain",))
    assert runtime.condition_lift_enabled is True
    assert context["condition_multiplier"] == 1.0
    assert context["risk_probability"] == baseline["risk_probability"]
    assert context["risk_score"] == baseline["risk_score"]
    assert context["adjustments"][0]["kind"] == "rain_context"
    assert "context only, not a validated improvement" in context["adjustments"][0]["reason"].lower()

    monkeypatch.setattr(realtime_config, "CONDITION_LIFT_APPLIES_TO_RANKING", True)
    applied = runtime.predict(cell_ids[0], active_conditions=("rain",))
    assert applied["condition_multiplier"] == pytest.approx(1.5)
    assert applied["risk_probability"] > baseline["risk_probability"]
    assert applied["adjustments"][0]["kind"] == "rain_lift"


def test_live_incident_endpoint_returns_score_and_rejection_removes_overlay(monkeypatch):
    from backend.routes import incidents

    async def inline_worker(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(incidents, "run_in_threadpool", inline_worker)
    runtime, cell_ids = runtime_fixture()
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    with session_factory() as db:
        lat, lng = h3.cell_to_latlng(cell_ids[0])
        db.add(Cell(cell_id=cell_ids[0], lat=lat, lng=lng, n_past_crashes=2, risk_score=20,
                    risk_vs_similar_history=20, history_percentile=20, confidence="Low",
                    top_factors="n_acc", recommendations="Audit", emerging_risk=0, baseline_prob=0.1))
        db.commit()
        police = User(id="police", name="Police", role=Role.community)
        planner = User(id="planner", name="Planner", role=Role.planner)
        body = IncidentReportIn(kind="crash", lat=lat, lng=lng,
                                occurred_at=datetime.now(timezone.utc).isoformat(), severity=3)

        created = asyncio.run(create_live_incident(body, request_for(runtime), db, police))
        assert created.incident.status == "pending"
        assert created.prediction.active_incident_count == 1
        assert created.prediction.incident_overlay_points == pytest.approx(12, abs=0.001)
        assert created.prediction.live_risk_score == pytest.approx(
            min(100, created.prediction.risk_score + 12))

        reviewed = review_incident(created.incident.id, IncidentReviewIn(decision="reject", note="Duplicate"),
                                   request_for(runtime), db, planner)
        assert reviewed.status == "rejected"
        assert reviewed.live_score_before == pytest.approx(created.prediction.live_risk_score)
        assert reviewed.live_score_after == pytest.approx(created.prediction.risk_score)
        after = runtime.predict(cell_ids[0])
        assert after["active_incident_count"] == 0
        assert after["live_risk_score"] == after["risk_score"]


def test_batch_parity_with_active_incidents():
    runtime, cell_ids = runtime_fixture()
    now = datetime.now(timezone.utc)
    runtime.activate_incident(101, cell_ids[0], "crash")
    runtime.activate_incident(102, cell_ids[0], "near_miss")
    batch = runtime.predict_batch(cell_ids, now=now)
    single = [runtime.predict(cid, include_shap=False, now=now) for cid in cell_ids]
    for actual, expected in zip(batch, single):
        for field in ("risk_probability", "risk_score"):
            assert actual[field] == pytest.approx(expected[field], abs=1e-9)
        for field in ("confidence", "active_incident_count", "incident_overlay_points", "live_risk_score"):
            assert actual[field] == expected[field]


def test_predict_routes_report_missing_runtime_as_503():
    request = request_for(None)
    with pytest.raises(HTTPException) as single:
        asyncio.run(predict(PredictRequest(cell_id="89446ca8d03ffff"), request))
    with pytest.raises(HTTPException) as batch:
        asyncio.run(predict_batch(BatchPredictRequest(cell_ids=["89446ca8d03ffff"]), request))
    assert single.value.status_code == batch.value.status_code == 503
    assert single.value.detail == "Live inference is unavailable. Check the model artifacts and feature store."
    assert batch.value.detail == single.value.detail




def test_live_batch_cache_is_model_only_and_incidents_are_applied_on_each_call():
    runtime, cell_ids = runtime_fixture()
    overrides = PredictionOverrides(weather_condition="rain", lighting="night", low_visibility=True)
    first = runtime.predict_batch(cell_ids, overrides)
    runtime.activate_incident(201, cell_ids[0], "crash")
    second = runtime.predict_batch(cell_ids, overrides)
    assert first[0]["risk_score"] == second[0]["risk_score"]
    assert second[0]["live_risk_score"] == pytest.approx(min(100, first[0]["risk_score"] + 12))
    assert second[0]["active_incident_count"] == 1
    assert len(runtime._score_cache) <= 8


def test_prediction_metadata_and_server_timing_are_additive():
    from fastapi import Response

    runtime, cell_ids = runtime_fixture()
    response = Response()
    result = asyncio.run(predict(PredictRequest(cell_id=cell_ids[0], overrides=PredictionOverrides(
        weather_condition="clear")), request_for(runtime), response))
    assert result["context_source"] == "scenario"
    assert result["scored_at"].endswith("Z")
    assert result["model_version"] is None  # fixture runtime has no packaged model file hash
    assert response.headers["Server-Timing"].startswith("infer;dur=")
