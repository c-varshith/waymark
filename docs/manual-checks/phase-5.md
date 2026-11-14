# Phase 5: traffic display and condition history

## Preconditions

- Apply `supabase/migrations/20261114000000_realtime_condition_history_and_traffic_budget.sql` to Supabase before starting the API there. Local SQLite creates these operational tables at startup.
- Set `WAYMARK_TRAFFIC_API_KEY` in the backend environment only. Do not paste it into a shell transcript, browser, frontend environment, or issue.
- TomTom flow tiles are enabled by default and relayed through a bounded memory cache (256 entries, 16 MiB). On a 512 MiB backend instance, set `WAYMARK_TRAFFIC_TILES=false` to disable the raster tile layer if memory pressure occurs; watched-cell readings are a separate feature.
- The local `waymark.db` must have a `crashes` table loaded from the supplied dataset for a road-cluster watch list. If absent, the traffic watch list is empty; use `python pipeline/load_crashes.py --csv US_Accidents_March23.csv --state TX --city Houston --db waymark.db` after confirming the target DB is the local demo database. This loader replaces the local `crashes` table.
- Traffic remains a separate display layer by default. No traffic bucket, provider status, or traffic-derived value enters a score or ranking unless the separate experimental historical lift passes its held-out gate and `WAYMARK_TRAFFIC_LIFT=1` is explicitly set. The traffic display API continues to report `score_effect: false`; see [traffic_lift.md](../traffic_lift.md).
- TomTom's reviewed terms prohibit historical collection/analysis of its live traffic feed. Until written terms clarify this, `condition_snapshots.congestion_bucket` is deliberately NULL; do not enable traffic-bucket logging.

## API and UI checks

1. Start the backend and frontend using the normal local commands, then open `/map` and wait for the live-update stream.
2. Read the cache-only API response: `curl -sS http://127.0.0.1:8000/api/live/traffic | python -m json.tool`. It should expose only cell IDs, congestion buckets, a separate road-closure flag, timestamps, status, attribution, and quota counts. No speeds, segment geometry, provider key, or reporter fields should appear.
3. Select a cell. The browser queues it for on-demand retrieval; this handler itself makes no provider call. At most one request per cell can be queued in five minutes, within the shared daily/monthly budget.
4. Turn on **Traffic · display only**. Free, moderate, and heavy readings appear as distinct labelled dots. Baseline and Live hexagon colors/scores must remain unchanged by traffic. The map shows the source attribution and stale/unavailable/budget status.
5. Verify the stream event with `curl -N http://127.0.0.1:8000/api/stream`. A traffic bucket change emits `traffic.updated` with exactly `cell_id`, `bucket`, and `observed_at`; an unchanged bucket emits no event.
6. Restart with the traffic key unset to verify the cache-only route reports unavailable and the layer has no dots. No handler call should trigger a TomTom request.
7. Wait through two 15-minute intervals, then query `SELECT cell_id, at, rain, night, rush, congestion_bucket FROM condition_snapshots ORDER BY at DESC LIMIT 16;`. Rows should be cell-level; `rain` and `night` are NULL when fresh weather is unavailable, `rush` is still derived from Houston local time, and `congestion_bucket` remains NULL pending license clarification.
8. Run `python scripts/export_condition_log.py --output /tmp/waymark-condition-snapshots.csv`; confirm its columns contain only the declared cell-level fields. Rows older than 90 days are pruned daily.

## Budget check

- Monthly quota from the supplied account information: 20,000; application hard cap at 80%: 16,000.
- Daily cap: 530. Eight cells at four polls/hour use 32 requests per attended hour; the daily cap covers 16.56 attended hours. Continuous operation would use 768 requests/day, so polling is gated on at least one connected stream client.
- Counts are persisted in `traffic_request_counters` for both calendar day and month. Provider calls stop when either cap is reached.

## Current environment limitations

- The local loader populated `waymark.db` with 168,740 Houston crash points (688 of 169,428 Houston records fell outside the 8,275 scored cells).
- Nine TomTom verification calls produced eight valid watch cells after rejecting/replacing a candidate. The local persisted budget counters now read 9 today / 9 this month. The account dashboard may also include calls made outside this local counter.
- Supabase migration and browser/API endpoint checks remain unverified because the configured Supabase hostname was not resolvable from this environment. The backend and frontend builds/tests passed, but do not interpret a missing live layer as free-flowing traffic.
