# Phase 4: live condition scoring checks

## Automated checks run in the implementation environment

- Python 3.12 unit tests: `backend/tests/test_condition_lift.py`, `backend/tests/test_live_context.py`, `backend/tests/test_live_context_routes.py`, and `backend/tests/test_validation_bootstrap.py` (**19 passed**). These exercise threshold boundaries, rain exit hysteresis, Open-Meteo hourly parsing, provider failures/staleness, cache-only context access, stale-context rejection, and paired bootstrap reproducibility without Starlette `TestClient`.
- Frontend `npm run build` (includes TypeScript project checks and Vite production build).
- Held-out validation with 1,000 paired cell-bootstrap replicates: `python scripts/validate_condition_lift.py --through-year 2021 --rain-mm 0.5 --rain-mm 1.0 --rain-mm 2.0 --bootstrap-replicates 1000 --bootstrap-seed 20261114`.

## Checks to run locally against the service

The full suite's TestClient fixtures hang in the implementation sandbox. Run these with the deployment Python (3.12) on the developer machine:

```bash
python -m pytest backend/tests/test_api.py backend/tests/test_crashes.py backend/tests/test_measures.py backend/tests/test_ops_foundation.py -q
```

The following new HTTP behavior has not been exercised through an HTTP client in this sandbox. Verify it locally using `/docs`, curl, or the browser network panel:

1. `GET /api/live/context` returns HTTP 200 and truthful `ok`, `stale`, or `unavailable` status, with `source`, `observed_at`, active condition flags, thresholds, and the one-point Houston-centre caveat. The endpoint must read cache only.
2. When context is `ok`, `POST /api/predict` with `{"cell_id":"<scored H3 id>","use_live_context":true}` returns `context_source: "live"`, `active_conditions`, additive `adjustments`, and baseline SHAP contributors. Rain's adjustment should identify the historical per-cell rain-hour evidence where the cell qualifies.
3. When weather is stale/unavailable, that same live request returns HTTP 503; do not interpret a baseline response as a live score. Sending `use_live_context` with any non-null what-if override returns HTTP 422.
4. `POST /api/predict/batch` with `use_live_context: true` returns the full map scores. Change weather context or wait for a provider condition transition and confirm one debounced full-city refresh. The context-change SSE event should contain no cell, report, position, or free-text data.
5. Open `/map`: Baseline is selected initially. Live shows the status chip and live score colours; stale/unavailable context remains labelled and does not silently claim fresh values. What-if toggles use scenario overrides and are mutually exclusive with live context.
6. Select a cell in Live. Its score card and adjustment list should agree with the live prediction response; each condition or incident component is separately labelled. SHAP is still described as baseline-model explanation.
7. With proximity alerts enabled, test a score above 80. It alerts once and rearms only after the score drops below 77. Test location can be simulated on the map; confirm the UI labels that location simulated.
8. Confirm that historical crash dots remain visible in Street and Satellite modes. New condition polygons must not replace the historical crash-point layer.

The validator's bootstrap interval is for the lifted-minus-baseline AUC and top-5% precision difference. It resamples the same cells for both score vectors. This is a cell-level interval and does not correct for spatial dependence between neighboring H3 cells.

Open-Meteo refresh is every 10 minutes; `observed_at` is the latest completed hourly reading. To test stale/circuit-breaker paths promptly, use a local fake provider or temporarily override the provider in a development test; do not wait for a real outage.

## Current limits to confirm before production

- The weather context is one Open-Meteo model point for Houston and applies to every cell. It is not a road-level sensor feed. The provider is only acceptable for non-commercial use under its Free terms; commercial deployment needs an appropriate plan and attribution.
- No traffic/congestion provider is connected. The current score has no traffic-exposure component.
- Historical mode is cell-level and the live weather cache is memory-only. This phase does not add a persisted condition-history log or a database retention/prune job.
