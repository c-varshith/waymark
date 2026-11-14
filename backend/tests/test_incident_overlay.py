from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from backend.incident_overlay import aggregate, effective_points
from backend.tests.test_ml_runtime import runtime_fixture
from backend.main import _sweep_incident_overlays
from backend.realtime import Broadcaster


NOW = datetime(2026, 11, 14, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize(("status", "expected"), [("pending", 12.0), ("confirmed", 20.0)])
def test_crash_overlay_uses_status_weight_and_decays(status, expected):
    assert effective_points("crash", status, NOW, NOW) == pytest.approx(expected)


def test_half_life_expiry_future_timestamp_and_naive_utc():
    assert effective_points("crash", "confirmed", NOW - timedelta(minutes=120), NOW) == pytest.approx(10.0)
    assert effective_points("crash", "confirmed", NOW - timedelta(minutes=720), NOW) == 0.0
    assert effective_points("crash", "confirmed", NOW + timedelta(days=1), NOW) == pytest.approx(20.0)
    naive_now = NOW.replace(tzinfo=None)
    assert effective_points("crash", "pending", naive_now, naive_now) == pytest.approx(12.0)
    assert effective_points("crash", "rejected", NOW, NOW) == 0.0


def test_pending_hard_expiry_and_minimum_effective_threshold():
    assert effective_points("crash", "pending", NOW - timedelta(minutes=241), NOW) == 0.0
    assert effective_points("near_miss", "confirmed", NOW - timedelta(minutes=240), NOW) == pytest.approx(2.5)
    assert effective_points("near_miss", "confirmed", NOW - timedelta(minutes=480), NOW) == 0.0


def test_aggregate_counts_only_effective_reports_and_caps_cell_points():
    items = [SimpleNamespace(cell_id="cell-a", kind="crash", status="confirmed", occurred_at=NOW)
             for _ in range(6)]
    items.append(SimpleNamespace(cell_id="cell-a", kind="crash", status="rejected", occurred_at=NOW))
    assert aggregate(items, NOW)["cell-a"] == (100.0, 6)


def test_same_reporter_repeats_do_not_receive_corroboration_bonus():
    runtime, ids = runtime_fixture()
    for report_id in range(1, 4):
        runtime.activate_incident(report_id, ids[0], "crash", status="confirmed", occurred_at=NOW,
                                  reporter_id="reporter-a")
    prediction = runtime.predict(ids[0], include_shap=False, now=NOW)
    assert prediction["incident_overlay_points"] == pytest.approx(60.0)
    assert prediction["corroboration"] == {"report_count": 3, "independent_reporters": 0, "bonus_points": 0.0}


def test_distinct_reporters_add_bounded_bonus_and_explanation_is_cell_only():
    runtime, ids = runtime_fixture()
    runtime.activate_incident(1, ids[0], "crash", status="confirmed", occurred_at=NOW,
                              reporter_id="reporter-a")
    runtime.activate_incident(2, ids[0], "crash", status="confirmed", occurred_at=NOW,
                              reporter_id="reporter-b")
    single = runtime.predict(ids[0], include_shap=False, now=NOW)
    batch = runtime.predict_batch(ids, now=NOW)[0]
    assert single["incident_overlay_points"] == pytest.approx(45.0)
    assert single["corroboration"] == {"report_count": 2, "independent_reporters": 1, "bonus_points": 5.0}
    assert single["incident_overlay_points"] == pytest.approx(batch["incident_overlay_points"])
    assert single["corroboration"] == batch["corroboration"]
    reason = next(item["reason"] for item in single["adjustments"] if item["kind"] == "incident_overlay")
    assert reason == "+5.0 points from 2 reports (1 independent); incident reports are status-weighted and decayed."
    assert "reporter-a" not in str(single) and "reporter-b" not in str(batch)


def test_corroboration_expires_after_window_without_removing_base_overlay():
    runtime, ids = runtime_fixture()
    runtime.activate_incident(1, ids[0], "crash", status="confirmed", occurred_at=NOW,
                              reporter_id="reporter-a")
    runtime.activate_incident(2, ids[0], "crash", status="confirmed", occurred_at=NOW,
                              reporter_id="reporter-b")
    later = NOW + timedelta(minutes=61)
    single = runtime.predict(ids[0], include_shap=False, now=later)
    batch = runtime.predict_batch(ids, now=later)[0]
    assert single["corroboration"]["independent_reporters"] == 0
    assert single["corroboration"]["bonus_points"] == 0
    assert single["incident_overlay_points"] == pytest.approx(40 * 0.5 ** (61 / 120))
    assert single["incident_overlay_points"] == pytest.approx(batch["incident_overlay_points"])


def test_runtime_single_batch_parity_uses_same_injected_clock():
    runtime, ids = runtime_fixture()
    runtime.activate_incident(1, ids[0], "crash", status="pending", occurred_at=NOW - timedelta(minutes=120))
    single = runtime.predict(ids[0], include_shap=False, now=NOW)
    batch = runtime.predict_batch(ids, now=NOW)[0]
    assert single["incident_overlay_points"] == pytest.approx(6.0)
    assert single["live_risk_score"] == pytest.approx(batch["live_risk_score"])
    assert single["incident_overlay_points"] == pytest.approx(batch["incident_overlay_points"])
    assert single["active_incident_count"] == batch["active_incident_count"] == 1


def test_status_change_and_reject_immediately_update_overlay():
    runtime, ids = runtime_fixture()
    runtime.activate_incident(1, ids[0], "crash", occurred_at=NOW)
    assert runtime.predict(ids[0], include_shap=False, now=NOW)["incident_overlay_points"] == pytest.approx(12)
    runtime.update_incident_status(1, "confirmed")
    assert runtime.predict(ids[0], include_shap=False, now=NOW)["incident_overlay_points"] == pytest.approx(20)
    runtime.update_incident_status(1, "rejected")
    assert runtime.predict(ids[0], include_shap=False, now=NOW)["active_incident_count"] == 0


def test_hydration_uses_occurred_at_and_skips_expired_rows():
    runtime, ids = runtime_fixture()
    rows = [
        SimpleNamespace(id=1, cell_id=ids[0], kind="crash", status="confirmed", occurred_at=NOW - timedelta(minutes=30)),
        SimpleNamespace(id=2, cell_id=ids[1], kind="crash", status="confirmed", occurred_at=NOW - timedelta(minutes=721)),
    ]
    runtime.hydrate_incidents(rows, now=NOW)
    assert runtime.predict(ids[0], include_shap=False, now=NOW)["incident_overlay_points"] == pytest.approx(20 * 0.5 ** 0.25)
    assert runtime.predict(ids[1], include_shap=False, now=NOW)["active_incident_count"] == 0


def test_sweeper_publishes_on_delta_and_expiry_once():
    runtime, ids = runtime_fixture()
    runtime.activate_incident(1, ids[0], "crash", status="confirmed", occurred_at=NOW)
    runtime.mark_overlay_published(ids[0], 20)
    assert runtime.sweep_incidents(NOW + timedelta(minutes=5)) == []
    changed = runtime.sweep_incidents(NOW + timedelta(minutes=30))
    assert changed == [ids[0]]
    runtime.mark_overlay_published(ids[0], runtime.predict(ids[0], include_shap=False, now=NOW + timedelta(minutes=30))["incident_overlay_points"])
    assert runtime.sweep_incidents(NOW + timedelta(minutes=31)) == []
    assert runtime.sweep_incidents(NOW + timedelta(minutes=720)) == [ids[0]]
    runtime.mark_overlay_published(ids[0], 0)
    assert runtime.sweep_incidents(NOW + timedelta(minutes=721)) == []


def test_sweeper_broadcasts_once_per_changed_cell_then_once_on_expiry(monkeypatch):
    from backend import main

    async def inline_worker(func, *args, **kwargs):
        return func(*args, **kwargs)

    monkeypatch.setattr(main, "run_in_threadpool", inline_worker)

    async def scenario():
        runtime, ids = runtime_fixture()
        cell_id = ids[0]
        runtime.activate_incident(1, cell_id, "crash", status="confirmed", occurred_at=NOW)
        runtime.mark_overlay_published(cell_id, 20)
        hub = Broadcaster(heartbeat_seconds=3600)
        await hub.start()
        subscriber = await hub.subscribe()
        app = SimpleNamespace(state=SimpleNamespace(ml_runtime=runtime))

        assert await _sweep_incident_overlays(app, hub, NOW + timedelta(minutes=5)) == 0
        assert subscriber.queue.empty()
        assert await _sweep_incident_overlays(app, hub, NOW + timedelta(minutes=30)) == 1
        first = await subscriber.queue.get()
        assert first.name == "prediction.updated" and first.data["active_incident_count"] == 1
        assert await _sweep_incident_overlays(app, hub, NOW + timedelta(minutes=31)) == 0
        assert subscriber.queue.empty()

        assert await _sweep_incident_overlays(app, hub, NOW + timedelta(minutes=720)) == 1
        expired = await subscriber.queue.get()
        assert expired.name == "prediction.updated" and expired.data["active_incident_count"] == 0
        assert await _sweep_incident_overlays(app, hub, NOW + timedelta(minutes=721)) == 0
        assert subscriber.queue.empty()
        hub.unsubscribe(subscriber)
        await hub.stop()

    import asyncio
    asyncio.run(scenario())
