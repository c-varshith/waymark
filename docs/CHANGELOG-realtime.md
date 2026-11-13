# WAYMARK realtime inference changes

## Phase 4: condition-aware live scoring and rain threshold

- Set `RAIN_MM=1.0` and `RAIN_EXIT_MM=0.5` in `backend/realtime_config.py`. The station comparison supplied for Houston Hobby and IAH (2017–2022) found the archive rainy-hour share was 7.8% at 0.5 mm versus about 3.5% at the gauges, while the 1.0 mm archive share was 5.2% versus about 5% at the gauges' smallest step. At 1.0 mm the archive's crash rain share (7.0%) is also close to the dataset rain flag (7.4%). This is a calibration choice from the supplied station check, not evidence that rain causes crashes.
- Historical weather tags, lift construction, runtime context and the validator share the condition definitions in `backend/realtime_config.py` and `backend/conditions.py`. Rain enters at 1.0 mm; live rain clears only after two polls strictly below 0.5 mm. Night comes from Open-Meteo `is_day`; rush is weekday local time in `America/Chicago` during 07:00–10:00 or 16:00–19:00.
- Rebuilt `backend/ml_artifacts/condition_lift.json` using complete weather years and crashes through 2022 at 1.0 mm. Version: `lift-615a78a5`; 151,601 training crash rows; 1,716 cells have at least one condition-qualified lift. Citywide rain crash/hour ratio `L=1.298`; night `L=0.531`; rush `L=2.253`. The night ratio below one is retained as a lower multiplier; it is not described as a citywide hazard increase.
- Archive-to-dataset rain-flag threshold sweep (169,428 crashes, 59,485 joined hours):

| Rain threshold | Archive rainy-hour share | Archive crash rain share | Dataset rain-flag share | Precision | Recall | Cohen's κ |
|---:|---:|---:|---:|---:|---:|---:|
| 0.1 mm | 22.3% | 27.9% | 7.4% | 21.5% | 81.1% | 0.252 |
| 0.3 mm | 11.6% | 14.9% | 7.4% | 30.9% | 62.4% | 0.349 |
| 0.5 mm | 8.5% | 10.9% | 7.4% | 36.1% | 53.5% | 0.376 |
| 1.0 mm | 5.4% | 7.0% | 7.4% | 41.5% | 39.6% | 0.359 |
| 2.0 mm | 3.0% | 3.9% | 7.4% | 48.1% | 25.6% | 0.298 |

  Precision/recall/kappa compare archive tags with the accident dataset's flag; station shares are a separate gauge comparison. Agreement alone is not a calibration target because the positive rates differ.
- Re-ran the 2022 held-out rain robustness check with `--rain-mm 0.5 --rain-mm 1.0 --rain-mm 2.0` (125,784 training crash rows; 25,817 held-out crashes; cell score evaluated over the packaged runtime's 8,275 cells). Per threshold, the 2022 archive has 8,760 hours, with respectively 531 / 329 / 209 rain hours, 1,967 / 1,170 / 769 rain-tagged crash rows, and 1,256 / 844 / 596 rain-positive cells among the 8,275 scored cells. The changing AUC and top-5% precision are therefore due to changing per-cell rain-occurrence labels; the evaluated cell count stays fixed. The 2.0 mm result has the smallest positive set and its top-5% precision is less stable.
- The per-condition lift is **OPT_IN**, not default-on: rain-cell Spearman rho was 0.077 / 0.041 / 0.048 at 0.5 / 1.0 / 2.0 mm. Rain AUC changed 0.789→0.784 / 0.785→0.778 / 0.785→0.777; top-5% precision changed 0.659→0.517 / 0.481→0.333 / 0.367→0.225. None of the thresholds improved held-out rain ranking, so Live is an explicit user-selected mode and Baseline remains the map default.
- Night and rush held-out performance is unchanged across the rain sweep. The 2022 archive has 4,322 night hours / 1,560 rush hours; these label 3,323 / 3,269 positive cells among the runtime's 8,275 cells. Night rho is 0.153, AUC 0.7602→0.7602, and top-5% precision 0.923→0.896. Rush rho is 0.179, AUC 0.7977→0.7970, and top-5% precision 0.952→0.915. Both condition lifts slightly reduce held-out ranking metrics.
- The production artifact `lift-615a78a5` was built with `--through-year 2022`, so it includes 2017–2022. It is not the artifact used for the held-out lift evaluation. For each validation threshold, `validate_condition_lift.py` calls `build_lift(..., through_year=2021, ...)`, then compares with 2022; its temporary lift fit uses only 2017–2021. The packaged LightGBM training code separately uses features through 2020 to train against 2021 labels, then evaluates features through 2021 against 2022 labels.
- Added paired cell-bootstrap 95% percentile intervals for lifted-minus-baseline AUC and top-5% precision. The command was run with 1,000 resamples and seed `20261114`; each replicate uses the same resampled cell indices for baseline and lifted scores. Results below use all 8,275 cells and the 2022 per-cell condition labels described above:

  The complete machine-readable result, including point metrics, sample counts, and intervals, is [condition_lift_2022_bootstrap.json](validation/condition_lift_2022_bootstrap.json).

| Condition | Rain threshold | ΔAUC [95% CI] | Δ top-5% precision [95% CI] |
|---|---:|---:|---:|
| Rain | 0.5 mm | -0.0048 [-0.0065, -0.0029] | -14.3 [-19.6, -8.7] pp |
| Rain | 1.0 mm | -0.0073 [-0.0096, -0.0047] | -14.7 [-20.5, -9.2] pp |
| Rain | 2.0 mm | -0.0079 [-0.0112, -0.0046] | -14.3 [-19.1, -8.5] pp |
| Night | 0.5 mm | -0.0001 [-0.0008, 0.0007] | -2.7 [-5.6, 0.2] pp |
| Night | 1.0 mm | -0.0001 [-0.0008, 0.0007] | -2.7 [-5.8, 0.2] pp |
| Night | 2.0 mm | -0.0001 [-0.0008, 0.0007] | -2.7 [-5.8, 0.2] pp |
| Rush | 0.5 mm | -0.0007 [-0.0013, -0.0001] | -3.6 [-6.0, -1.2] pp |
| Rush | 1.0 mm | -0.0007 [-0.0013, -0.0001] | -3.6 [-6.3, -1.2] pp |
| Rush | 2.0 mm | -0.0007 [-0.0013, -0.0001] | -3.6 [-6.0, -1.2] pp |

  Rain intervals exclude zero in the negative direction at all three thresholds. Night intervals span zero; rush intervals are small but negative. These are cell-resampling intervals and do not account for spatial dependence between neighboring cells.
- At the configured 1.0 mm threshold, the rain component lifts 790 cells, shifts the mean citywide score by 0.835 points, and moves 133 cells above the 80-point alert threshold. Combined rain/night/rush shifts 1,402 cells with a 1.412-point mean and moves 209 cells above 80. These are model-score impacts, not observed injury reductions.
- Added cached, server-side Open-Meteo hourly polling at ten-minute intervals, a three-second timeout, a five-failure circuit breaker, `ok`/`stale`/`unavailable` status, and an in-memory cache only. Live predictions reject stale/unavailable weather instead of inventing readings. The context endpoint returns HTTP 200 with its true availability status; no provider call runs inside a request handler. The context is one Houston-centre point shared by all cells, not street-level weather.
- Added a Baseline / Live / What-if map selector. Baseline remains selected initially; Live fetches a full city batch and refreshes on context-change events after a two-second debounce. The map retains the last successful live batch only with a visible stale/unavailable status. Cell detail displays each additive condition/incident adjustment and keeps SHAP labelled as an explanation of baseline model inputs. Alerts re-arm after the score falls below 77 (threshold 80, hysteresis 3).
- New plain unit tests: `backend/tests/test_condition_lift.py`, `backend/tests/test_live_context.py`, `backend/tests/test_live_context_routes.py`, and `backend/tests/test_validation_bootstrap.py` (**19 passed** on Python 3.12). Frontend production build succeeds. Full-suite baseline remains the user-run Step A.1 result below; HTTP-level live-context/prediction checks listed in [phase-4.md](manual-checks/phase-4.md) still need the user's local run because TestClient hangs in this sandbox.
- Open-Meteo's free API quota is 10,000 requests/day; ten-minute polling is 144/day per running process. The Free API is for non-commercial use and data is CC BY 4.0 with attribution. A commercial deployment needs a suitable Open-Meteo plan and attribution. No traffic provider is connected in this phase, so congestion/exposure is not represented. See [Open-Meteo API docs](https://open-meteo.com/en/docs), [terms](https://open-meteo.com/en/terms), and [license](https://open-meteo.com/en/license).

## Phase 4 Step A.1: deployment Python and full-suite baseline

- Pinned the project Python version to 3.12 in `.python-version` (validated by the user with Python 3.12 outside the Codex sandbox).
- User-run full backend suite: **228 passed, 16 failed, 8 warnings, 183.18 seconds** (244 tests total).
- Compared with the previous documented baseline of **211 passed / 15 failed**: 17 more tests pass and one more test fails. The added failure is `backend/tests/test_ml_runtime.py::test_batch_prediction_returns_scores_without_shap_and_incident_overlay_is_separate` (float `5.9999984` versus exact `6`); the other reported failures remain in audit CSV, chat fallback/card behavior, evidence, and measures.
- The Codex sandbox still stalls in Starlette/AnyIO `TestClient`, including on a minimal FastAPI app; this result is therefore the accepted full-suite baseline. New phase logic will use non-`TestClient` unit tests here. Record the listed API/integration cases for user-run verification.

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
