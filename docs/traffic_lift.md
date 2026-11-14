# Learned traffic effect ("traffic lift")

An experimental traffic effect can raise or lower a cell's **Live** score by an amount learned from historical traffic matched to past crashes. It requires a held-out validation PASS and is **disabled by default**. Until a licensed historical archive is supplied and the operator explicitly enables it, traffic remains display-only/context-only.

## Steps
1. Get an hourly historical traffic file for the study area. It should cover at least two years that overlap the crash records.
2. Build and validate it:
   ```bash
   python scripts/build_traffic_lift.py --traffic data/external/houston_traffic_hourly.csv
   # if the crashes table isn't loaded: add --csv US_Accidents_March23.csv
   ```
   This writes `backend/ml_artifacts/traffic_lift.json` and prints the verdict.
3. Review the report and artifact. Restart the API. `GET /api/health/model` then shows `traffic_lift_verdict` and `traffic_lift_applied`.
4. Only after reviewing the validation result, explicitly opt in on the backend with `WAYMARK_TRAFFIC_LIFT=1`, then restart. The default is `false`; Baseline and What-if scores never use this adjustment.

## File format (CSV, one row per segment or cell per hour)
| column | meaning |
| --- | --- |
| `hour` or `timestamp` | ISO time. Values with `Z` or an offset are converted to Houston local time. |
| `cell_id` **or** `lat` + `lng` | H3 resolution-9 cell, or a point that is mapped to one |
| `congestion_ratio` **or** `current_speed` + `free_flow_speed` | 0 = free flow, 1 = stopped (`1 - speed / free-flow`). Any one speed unit. |

Several segments in the same cell and hour are averaged. Buckets use the live feed's thresholds: free < 0.15 ≤ moderate < 0.35 ≤ heavy.

## What is learned
- For each bucket, the effect is the crash rate in that bucket's hours relative to the average hour. It is measured citywide and per cell. Per-cell values are shrunk towards the citywide value, so cells with little history fall back to the citywide value.
- The multiplier is `1 + 0.5 × (ratio − 1)`, bounded to 0.67–1.5. Heavy traffic can raise a score, and free flow can lower it, if the history says so.

## When it is applied
- The model is fitted on earlier years, then scored on the latest year. The test is the log-likelihood gain per held-out crash, with a 95% bootstrap interval over cells.
- **PASS** requires all of these:
  - at least 200 held-out crashes fall in archived cell-hours;
  - the interval is above 0;
  - the citywide direction for free and heavy traffic is the same in both periods.
- Only a PASS can change live scores, and only in Live mode when `WAYMARK_TRAFFIC_LIFT=1` is explicitly set. The flag defaults to `false`; setting it to `0` switches it off.
- A FAIL or INSUFFICIENT_DATA verdict is shown in the cell panel as context only.

## Limits
- It applies only in **Live** mode, and only to cells with a fresh TomTom reading: the watched hotspots plus the cell you select. Cells with a learned congestion effect are prioritised for watching. Street-colour tiles are pictures, so they cannot score cells.
- Do not build this history from cached TomTom live readings. The live feed is memory-only and its bucket is not written to condition snapshots. Obtain a separate source with rights for historical storage and analysis, and review its license before processing. Potential sources to investigate include:
  - TomTom Traffic Stats / MOVE or HERE Traffic Analytics (paid);
  - FHWA NPMRDS via a state DOT or MPO partner;
  - Houston TranStar data on request.
