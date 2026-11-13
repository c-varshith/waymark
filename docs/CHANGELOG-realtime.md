# WAYMARK realtime inference changes

## Phase 2: incident stream and live snapshot

- Added anonymous `GET /api/stream` server-sent events with sequenced IDs, 200-event replay, heartbeat, a 200-subscriber limit, bounded per-client queues, and a single `resync` marker after queue overflow.
- Added anonymous `GET /api/live/snapshot` for active incident score overlays and the latest event version. Snapshot overlays include active reports only and do not expose report GPS or free-text fields.
- `/api/incidents`, `/api/incident`, and incident review now publish cell-only events and re-score the affected cell without SHAP. Stream publication failures are logged and do not fail the incident operation.
- The map hydrates overlays from the snapshot, consumes per-cell updates idempotently, refreshes incident markers/detail state, and keeps the last scores visible while offline. Reconnect uses replay IDs, snapshot recovery, a 45-second watchdog, exponential backoff with jitter, and a 30-second offline snapshot poll. Hidden tabs close the stream and visible tabs snapshot before reconnecting.
- Full-city what-if scoring no longer refetches because an incident changed: the map applies the latest incident overlay on top of the scenario score. The driver watch ring rechecks immediately for matching cell events with a 20-second throttle and retains its 30-second polling fallback.
- The broadcaster and overlay state are in-process. Run one API worker for the demo. A multi-worker or multi-instance deployment requires shared event/overlay state (for example Redis pub/sub) to guarantee cross-worker delivery.
- GZip verification: the active local environment uses Starlette 1.10.0; its `GZipMiddleware` excludes `text/event-stream`, so SSE responses are not gzip-buffered. `backend/requirements.txt` does not pin Starlette, so verify this behavior again if dependency versions change.
- Five-run in-process event-hub benchmark: publish from a worker thread to two subscribers had 0.148 ms median and 0.606 ms worst delivery time. A representative compact snapshot containing one overlay was 251 bytes. This measures the server-side fan-out boundary, not HTTP/browser time.
- Manual two-window timing steps and restart/offline checks are in [phase-2.md](manual-checks/phase-2.md). No browser-to-browser latency was measured in this implementation environment.

## Phase 3: decaying, status-aware incident overlay

- Incident points now decay from `occurred_at`, pending reports use a lower weight and shorter hard-age limit, confirmed reports use full weight, and rejected reports are removed immediately. All timestamps are normalized as UTC (naive database timestamps mean UTC); future event times are clamped to age zero.
- These are chosen operational defaults, not fitted or validated coefficients: crash 20 points, near-miss 10, pending weight 0.6, confirmed 1.0, rejected 0.0, 120-minute half-life, 1-point minimum, 720-minute confirmed maximum age, 240-minute pending maximum age, and 100-point overlay cap. They live in `backend/realtime_config.py` and are overridable with `WAYMARK_CRASH_POINTS`, `WAYMARK_NEAR_MISS_POINTS`, `WAYMARK_PENDING_WEIGHT`, `WAYMARK_CONFIRMED_WEIGHT`, `WAYMARK_REJECTED_WEIGHT`, `WAYMARK_HALF_LIFE_MINUTES`, `WAYMARK_MIN_EFFECTIVE_POINTS`, `WAYMARK_MAX_AGE_MINUTES`, `WAYMARK_PENDING_MAX_AGE_MINUTES`, `WAYMARK_OVERLAY_CAP`, `WAYMARK_SWEEP_SECONDS`, and `WAYMARK_SWEEP_PUBLISH_DELTA`.
- This is a bounded adjustment to the LightGBM score, not model retraining and not evidence that the report signal predicts future crashes.
- The periodic sweeper removes expired in-memory signals and publishes only when points change by at least the configured threshold or reach zero. It is cancelled during application shutdown.
- Fixed-clock formula check for one crash (before minimum-point or hard-age expiry):

| Age (minutes) | Pending (0.6 weight) | Confirmed (1.0 weight) |
|---:|---:|---:|
| 0 | 12.00 | 20.00 |
| 30 | 10.09 | 16.82 |
| 60 | 8.49 | 14.14 |
| 120 | 6.00 | 10.00 |
| 240 | 3.00 | 5.00 |

- Automated test summary: Phase 2 realtime tests 8 passed; Phase 3 decay/runtime/sweeper tests 10 passed. Existing end-to-end backend `TestClient` suite remains blocked in this Python 3.15.2 environment; see the manual checks for target-runtime verification.

## Phase 1 before

Measured with `backend/.venv/bin/python scripts/bench_inference.py --runs 15` against the packaged Houston model (8,275 cells, 18 features). Runtime timings are in-process on the local development machine and include Python response serialization; they are not HTTP network timings. p95 uses NumPy's default linear percentile interpolation.

| Case | p50 | p95 |
|---|---:|---:|
| Single cell, SHAP | 4.641 ms | 6.449 ms |
| Single cell, no SHAP | 3.357 ms | 4.524 ms |
| 7-cell batch | 5.956 ms | 6.365 ms |
| Full city, baseline | 2,437.014 ms | 2,580.339 ms |
| Full city, rain + night + low visibility | 2,452.660 ms | 2,576.870 ms |
| Three LightGBM `predict_proba` calls, full city | 217.296 ms | 217.296 ms (one isolated sample) |
| Legacy per-cell DataFrame/serialization loop, full city | 3,039.487 ms | 3,463.924 ms |

Single prediction payload: 1,094 bytes raw / 470 bytes gzip. Full-city batch payload: 1,946,908 bytes raw / 198,562 bytes gzip. This baseline predates response metadata fields.

The baseline misses the requested 800 ms full-city target. The isolated per-cell row/overlay/serialization path costs substantially more than the model calls and is the optimization target.

## Phase 1 after

Same packaged model, host, and 15 measured runs per case. Cold cases clear the per-override array entry before each timed run; cache-hit cases are warmed before timing. Metadata is carried once in the batch envelope (`model_version`, `schema_version`, `scored_at`, `context_source`, and `stale_after_seconds`), avoiding per-cell metadata repetition.

| Case | p50 | p95 |
|---|---:|---:|
| Single cell, SHAP | 6.462 ms | 7.613 ms |
| Single cell, no SHAP | 4.349 ms | 5.428 ms |
| 7-cell batch, cold override cache | 230.720 ms | 238.480 ms |
| 7-cell batch, warm cache | 0.007 ms | 0.013 ms |
| Full city, baseline, cold cache | 229.955 ms | 240.820 ms |
| Full city, rain + night + low visibility, cold cache | 203.442 ms | 262.001 ms |
| Full city, baseline, warm cache | 6.013 ms | 41.559 ms |
| Full city, rain + night + low visibility, warm cache | 6.189 ms | 45.969 ms |
| Three LightGBM `predict_proba` calls, full city | 229.524 ms | one isolated sample |
| Legacy per-cell DataFrame/serialization loop, full city | 3,672.864 ms | 4,016.265 ms |

| Payload | Before raw / gzip | After raw / gzip |
|---|---:|---:|
| Single prediction | 1,094 / 470 bytes | 1,216 / 546 bytes |
| 7-cell batch | 1,666 / 391 bytes | 1,807 / 483 bytes |
| Full-city batch | 1,946,908 / 198,562 bytes | 1,947,049 / 198,734 bytes |

Batch metadata is envelope-only; this was chosen to avoid repeating it 8,275 times. Payload sizes are compact JSON serialized locally and gzip-compressed for comparison. HTTP smoke testing confirmed successful gzip content encoding, `Server-Timing` headers, current model/schema metadata, and 8,275 returned cells.

Model version: `lgbm3-06fbe9a7`; feature schema version: `1`. The cold full-city path is roughly 10–12x faster than the old path and remains below the requested 300 ms p95 in these runs. The vectorized cold path already meets the stated target, so the requested cache remains in place and reduces repeated full-city p50 to about 6 ms. A first request for a not-yet-cached override combination scores the city once; entries are model-only, LRU-bounded to eight combinations, and incident overlays are read fresh per response.

The complete backend suite reports 211 passed and 15 failed. The same 15 failures were present in the pre-Phase-1 baseline: audit CSV schema, chat fallback/card expectations, evidence storage/rate-limit behavior, and measure projection/permission expectations. None is a Phase 1 acceptance test. Frontend lint exits successfully with the existing warnings; TypeScript still reports the existing missing `gsap`/`lenis` landing-page dependencies and downstream implicit-any errors, with no errors in Phase 1 files.
