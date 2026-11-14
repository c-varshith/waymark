# Phase 7: live map pulse, ticker, and corroboration

## Two-window live check

For a quicker cooling demonstration, start the backend with the Phase 3 manual-check overrides:

```bash
WAYMARK_HALF_LIFE_MINUTES=2 WAYMARK_PENDING_MAX_AGE_MINUTES=10 \
DATABASE_URL= WAYMARK_DB=waymark.db \
.venv312/bin/uvicorn backend.main:app --host 127.0.0.1 --port 8000
```

Restart with the normal values after the demonstration; these are environment-only demo settings.

1. Start the backend and frontend. Open `/map` in two browser windows and select the same map area. Keep the stream status at **Live updates**.
2. In window A, report a crash in a visible cell. Within about two seconds, window B should show a brass ripple around that cell, animate the cell's fill to its updated score, and add a cell-level ticker line such as **New crash report near Midtown**. Click the ticker entry and confirm window B selects the cell.
3. Watch the affected cell after the report. As `prediction.updated` events arrive from the incident sweeper, its fill should transition with each reported score change. The ticker should show **Score cooling near …** when the event score drops below the previous live score.
4. Check `prefers-reduced-motion: reduce` in browser accessibility settings or DevTools rendering emulation. Event fills should change without an animated transition or ripple.
5. Verify the ticker contains at most eight entries, groups announcements during a burst, and never shows a note, reporter identity, reason, review note, or other report text. Its entries must be cell-selectable.

## Corroboration check

Use the backend unit tests for deterministic clocks and report identities. Three reports from one reporter get no independent-reporter bonus. A second distinct reporter within 60 minutes adds 25% of that report's decayed/status-weighted points; the bonus expires after 60 minutes. The single-cell and batch paths must agree. Reporter IDs are passed only into in-process overlay aggregation and are not included in prediction responses, SSE events, or condition snapshots.

## Test commands

```bash
.venv312/bin/python -m pytest backend/tests/test_incident_overlay.py backend/tests/test_realtime.py -q
cd frontend && npm run test:activity && npm run build
```

The two-window visual gate requires a manual browser run; automated tests do not claim that run occurred.
