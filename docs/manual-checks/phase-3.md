# Phase 3: decaying incident overlay checks

Run the API against a local/demo database with the Houston model and feature store. For a short demo decay cycle, set these environment variables before starting the backend:

```bash
WAYMARK_HALF_LIFE_MINUTES=2 WAYMARK_PENDING_MAX_AGE_MINUTES=10 uvicorn backend.main:app --reload
```

Optional tuning variables and defaults:

| Environment variable | Default |
|---|---:|
| `WAYMARK_CRASH_POINTS` | 20.0 |
| `WAYMARK_NEAR_MISS_POINTS` | 10.0 |
| `WAYMARK_PENDING_WEIGHT` | 0.6 |
| `WAYMARK_CONFIRMED_WEIGHT` | 1.0 |
| `WAYMARK_REJECTED_WEIGHT` | 0.0 |
| `WAYMARK_HALF_LIFE_MINUTES` | 120 |
| `WAYMARK_MIN_EFFECTIVE_POINTS` | 1.0 |
| `WAYMARK_MAX_AGE_MINUTES` | 720 |
| `WAYMARK_PENDING_MAX_AGE_MINUTES` | 240 |
| `WAYMARK_OVERLAY_CAP` | 100.0 |
| `WAYMARK_SWEEP_SECONDS` | 30 |
| `WAYMARK_SWEEP_PUBLISH_DELTA` | 1.0 |

## Two-window demo

1. Open the same Houston cell in two browser windows and sign in as a user allowed to report incidents.
2. Report a crash with the current time as its occurrence time. The reporting response should show a pending incident overlay of 12 points at age zero (20 crash points × 0.6 pending weight). Both windows should receive the live score update.
3. Leave the windows open. With the demo half-life of two minutes, the overlay should reduce to about 6 points after two minutes. The map color continues to use the existing live-risk score scale.
4. As planner, confirm a pending report. The response should show the live score before and after; at age zero, the overlay changes from 12 to 20 points. The detail panel should describe the current overlay amount, report count, half-life, and pending weight from the API response.
5. Reject another pending report. It should immediately stop contributing to the score; the UI should state “Rejected reports no longer affect the score”.
6. For a pending report, after ten minutes it should expire even without review. Confirmed reports use their separate configured maximum age and also expire once their effective points fall below the minimum.
7. Stop and restart the backend. Only still-active, non-expired reports should hydrate into the process-local overlay. Watch the second window receive decay events after reconnect.

An incident saved through the fallback endpoint while inference is unavailable does not receive a fabricated score. The client displays “Report saved locally, score will update when the connection returns”.
