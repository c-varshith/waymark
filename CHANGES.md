# WAYMARK fix pass (14 Nov 2026)

## Live traffic now follows the streets
- **Street network:** new `GET /api/live/traffic/tiles/{z}/{x}/{y}.png` relays TomTom flow tiles so every road in view is coloured free / slow / heavy. The TomTom key stays on the server. Tiles are cached for 90 s, same-tile requests share one upstream fetch, and fresh fetches stop at a daily cap (`WAYMARK_TRAFFIC_TILE_DAILY_CAP`, default 40,000; TomTom's free tier allows 50,000).
- **Watched hotspot roads:** the segment poller now keeps the road geometry TomTom returns, so each watched segment is drawn as a line along the road with a white outline and a hover tooltip. Before, it was a dot at the hexagon centre. Raw speeds are still never stored.
- **Without crash points:** the watch list falls back to cell centres when `pipeline/load_crashes.py` hasn't been run. Before, traffic was empty in that case.
- The Traffic toggle shows a colour key. When it's unavailable it's disabled, and its tooltip gives the reason (for example, no key configured).
- Configuration is in `.env.example` (`WAYMARK_TRAFFIC_API_KEY` and the tile settings in `backend/realtime_config.py`).

## Map layout: nothing overlaps
- The map overlays use three columns that can't collide: filters on the left, the toolbar and status in the centre, cell details on the right. Toolbar items wrap instead of covering the Baseline/Live/What-if switch or the details panel.
- Below 1280 px, opening one side panel closes the other. A "Cell details" button brings the details back, and the panel has a new "hide" button.
- On phones, filters open as a full drawer and details as a bottom sheet. Leaflet's zoom buttons and attribution move out of the way of both.
- The live-weather status sits under the Live mode switch, and the region report card is in the filters column until a cell is selected.

## Logic fixes
- Audit list returned HTTP 500 when crash points weren't imported. The API now creates an empty `crashes` table at startup, and "not imported" is detected correctly (`/api/crashes` and region reports).
- Region reports showed "Past crashes: 0" when crash records weren't loaded. They now show n/a with the reason.
- The cell panel showed incident overlay points ×100 ("+588.94 pts points"). It now shows "+5.9 score points".
- In Live mode, the cell panel labelled the baseline value as "Live condition-adjusted likelihood" even when live scoring had failed. It now says so honestly.
- Measure status changes by Traffic Police now return `forbidden` (role) instead of `wrong_measure_owner`.
- The evidence-upload permission table now matches the route and the UI: Traffic Police comment, they don't upload evidence.
- Projected and adjusted scores round half-up (47.775 → 47.78, not 47.77).
- Assistant fallback:
  - It falls back to rule-based cards when every card the model proposed fails validation.
  - It keeps text the model had already written before a mid-stream failure.
  - It says plainly that the suggestions are rule-based.
- The assistant button on a region page attaches the region's latest ready report automatically.

## Smaller UI fixes
- Evidence chart axis labels no longer overlap on phones.
- The region page leaves room for the assistant button.
- Measure drawer: "Start work" and "Cancel this measure…" are no longer jammed together, and the meta line has proper spacing.
- The cell panel shows scores with 1 decimal place instead of 3.

## Tests
- Backend: 287 passed, including new tests for segment geometry, tile proxy caching, the tile cap and hiding the key. Updated tests whose expectations were stale.
- Frontend: `tsc -b`, `vite build`, and the activity-ticker test pass.
