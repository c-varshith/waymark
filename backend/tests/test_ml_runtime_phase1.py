"""Focused acceptance checks against the packaged Houston model."""
from __future__ import annotations

import random
import time

import pytest
import asyncio
import httpx
from fastapi import FastAPI
from backend.routes.predict import router as predict_router

from backend.config import settings
from backend.ml_runtime import InferenceRuntime
from backend.schemas import PredictionOverrides


@pytest.fixture(scope="module")
def packaged_runtime():
    return InferenceRuntime.load(settings.ml_artifact_dir, settings.ml_feature_store)


def test_300_cell_batch_matches_single_scoring_for_three_scenarios(packaged_runtime):
    rng = random.Random(1711)
    ids = rng.sample(list(packaged_runtime.features.index), 300)
    scenarios = [None, PredictionOverrides(weather_condition="rain"),
                 PredictionOverrides(lighting="night", low_visibility=True)]
    for overrides in scenarios:
        batch = packaged_runtime.predict_batch(ids, overrides)
        singles = [packaged_runtime.predict(cid, overrides, include_shap=False) for cid in ids]
        for actual, expected in zip(batch, singles):
            assert actual["risk_probability"] == pytest.approx(expected["risk_probability"], abs=1e-9)
            assert actual["risk_score"] == pytest.approx(expected["risk_score"], abs=1e-9)
            assert actual["confidence"] == expected["confidence"]
            assert actual["active_incident_count"] == expected["active_incident_count"]
            assert actual["incident_overlay_points"] == expected["incident_overlay_points"]
            assert actual["live_risk_score"] == expected["live_risk_score"]


def test_cached_full_city_batch_is_three_times_faster_and_incident_is_live(packaged_runtime):
    ids = list(packaged_runtime.features.index)
    overrides = PredictionOverrides(weather_condition="clear", lighting="day", low_visibility=True)
    with packaged_runtime._cache_lock:
        packaged_runtime._score_cache.pop(packaged_runtime._override_key(overrides), None)
    started = time.perf_counter()
    before = packaged_runtime.predict_batch(ids, overrides)
    first = time.perf_counter() - started
    cell_id = ids[0]
    packaged_runtime.activate_incident(987654321, cell_id, "crash")
    started = time.perf_counter()
    after = packaged_runtime.predict_batch(ids, overrides)
    second = time.perf_counter() - started
    packaged_runtime.reject_incident(987654321)
    assert first >= second * 3
    assert after[0]["risk_score"] == before[0]["risk_score"]
    assert after[0]["live_risk_score"] == min(100, before[0]["risk_score"] + 20)
    assert after[0]["active_incident_count"] == before[0]["active_incident_count"] + 1
    assert len(packaged_runtime._score_cache) <= 8


def test_full_city_batch_does_not_block_a_trivial_route(packaged_runtime):
    async def run():
        app = FastAPI()
        app.state.ml_runtime = packaged_runtime
        app.include_router(predict_router)

        @app.get("/trivial")
        async def trivial():
            return {"ok": True}

        ids = list(packaged_runtime.features.index)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            batch_task = asyncio.create_task(client.post("/api/predict/batch", json={"cell_ids": ids}))
            await asyncio.sleep(0.01)
            started = time.perf_counter()
            trivial_response = await client.get("/trivial")
            trivial_elapsed = time.perf_counter() - started
            batch_response = await batch_task
        assert batch_response.status_code == 200
        assert batch_response.json()["count"] == len(ids)
        assert trivial_response.status_code == 200 and trivial_response.json() == {"ok": True}
        assert trivial_elapsed < 0.1

    asyncio.run(run())
