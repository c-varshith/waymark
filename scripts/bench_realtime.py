"""Measure in-process publish-to-subscriber latency for the Phase 2 event hub."""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.realtime import Broadcaster, utc_iso


async def main() -> None:
    hub = Broadcaster(heartbeat_seconds=3600)
    await hub.start()
    reporter = await hub.subscribe()
    observer = await hub.subscribe()
    durations: list[float] = []
    for index in range(5):
        payload = {"cell_id": f"bench-cell-{index}", "kind": "crash", "at": utc_iso()}
        start = time.perf_counter_ns()
        worker = threading.Thread(target=hub.publish, args=("incident.created", payload))
        worker.start()
        reporter_event, observer_event = await asyncio.wait_for(
            asyncio.gather(reporter.queue.get(), observer.queue.get()), timeout=2
        )
        worker.join(timeout=1)
        assert reporter_event.id == observer_event.id
        durations.append((time.perf_counter_ns() - start) / 1_000_000)

    snapshot = {
        "version": 123456,
        "scored_at": "2026-11-14T00:00:00Z",
        "model_version": "lgbm3-06fbe9a7",
        "context_source": "live",
        "overlays": [{"cell_id": "8928308280fffff", "risk_score": 50.0,
                       "live_risk_score": 70.0, "active_incident_count": 1,
                       "incident_overlay_points": 20.0}],
    }
    print(f"runs_ms={','.join(f'{v:.3f}' for v in durations)}")
    print(f"median_ms={statistics.median(durations):.3f}")
    print(f"worst_ms={max(durations):.3f}")
    print(f"snapshot_one_overlay_json_bytes={len(json.dumps(snapshot, separators=(',', ':')).encode())}")
    hub.unsubscribe(reporter)
    hub.unsubscribe(observer)
    await hub.stop()


if __name__ == "__main__":
    asyncio.run(main())
