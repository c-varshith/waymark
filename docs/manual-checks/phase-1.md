# Phase 1 live inference checks

Run the API from the WAYMARK project root (`backend/.venv/bin/uvicorn backend.main:app --reload`). These requests use Houston coordinates and the API resolves them to a scored H3 cell.

```bash
curl -sS http://127.0.0.1:8000/api/health/model
```

```bash
curl -sS -D /tmp/waymark-predict-headers.txt \
  -H 'Content-Type: application/json' \
  -d '{"lat":29.76,"lng":-95.37,"overrides":{"weather_condition":"rain","lighting":"night","low_visibility":true}}' \
  http://127.0.0.1:8000/api/predict
cat /tmp/waymark-predict-headers.txt | grep -i 'server-timing\|content-encoding'
```

Copy the returned `cell_id` into a seven-cell request (or use neighboring cell IDs from `/api/cells`):

```bash
curl --compressed -sS -w '\nrequest_seconds=%{time_total}\n' \
  -H 'Accept-Encoding: gzip' -H 'Content-Type: application/json' \
  -d '{"cell_ids":["89446c0200bffff","89446c0200fffff","89446c02017ffff","89446c0201bffff","89446c02023ffff","89446c0202bffff","89446c02033ffff"],"overrides":{}}' \
  http://127.0.0.1:8000/api/predict/batch
```

To capture transfer size as well as elapsed time, save the compressed response body and inspect the response headers:

```bash
curl --compressed -sS -D /tmp/waymark-batch-headers.txt -o /tmp/waymark-batch.json \
  -w 'request_seconds=%{time_total} downloaded_bytes=%{size_download}\n' \
  -H 'Accept-Encoding: gzip' -H 'Content-Type: application/json' \
  -d '{"cell_ids":["89446c0200bffff","89446c0200fffff","89446c02017ffff","89446c0201bffff","89446c02023ffff","89446c0202bffff","89446c02033ffff"]}' \
  http://127.0.0.1:8000/api/predict/batch
cat /tmp/waymark-batch-headers.txt | grep -i 'server-timing\|content-encoding\|content-length'
```

`Server-Timing: infer;dur=...` measures runtime inference in milliseconds. `scored_at` is the response scoring time. Batch metadata is carried once on the response envelope to avoid repeating it for every cell.
