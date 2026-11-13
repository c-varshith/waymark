# Phase 2: Live stream and snapshot checks

Run the API with the Houston model and feature store configured. The realtime hub is process-local; use one API worker for this demo. A multi-worker deployment needs a shared broker such as Redis before it can promise cross-worker fan-out.

## Endpoint smoke checks

```bash
curl -i http://127.0.0.1:8000/api/live/snapshot
curl -N -i http://127.0.0.1:8000/api/stream
```

The stream should return `text/event-stream`, `Cache-Control: no-cache`, and `X-Accel-Buffering: no`. It should send a heartbeat every 20 seconds. Snapshot reads are anonymous and contain only active cell score overlays, the model version, score timestamp, context source, and event version.

To verify replay, copy an `id:` value from the stream and reconnect:

```bash
curl -N -H 'Last-Event-ID: 1' http://127.0.0.1:8000/api/stream
```

Use an id older than the retained replay window to confirm the stream returns `event: resync`.

## Cross-window incident loop

1. Open the WAYMARK map in two browser windows. Keep the same Houston cell visible in both.
2. In the reporting window, submit a crash or near-miss report as a community user.
3. In the other window, check that the incident marker and cell overlay update without a reload. Record time from successful submit to visible map update; repeat at least five times for a median and worst-case reading.
4. Confirm a pending incident as a planner and verify that the incident marker changes status. Reject a separate pending report and verify its live overlay is removed.
5. Reload the second window. Its live snapshot should restore currently active incident overlays; rejected reports should not return.

## Recovery and privacy checks

1. Disable the API network request or stop the API briefly. The map should retain its last scores and show the offline timestamp.
2. Restore the API. The client should fetch a snapshot, reconnect, and return to the live status.
3. In browser developer tools, inspect `incident.created` and `incident.reviewed` frames. They may include only the documented cell id, kind/decision, and timestamp. They must not include report notes, reasons, reporter/reviewer identity, review text, or precise GPS coordinates.
4. Check the browser Network panel for the stream response. `Content-Encoding` should not be `gzip`.

Record browser/network details and the five submit-to-map timings when doing the live demo rehearsal; automated local benchmarks cover event-bus delivery, not the browser/network path.
